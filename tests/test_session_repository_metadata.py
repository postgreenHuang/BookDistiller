import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.chat as chat


class SessionRepositoryMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sessions = self.root / "sessions"
        replacements = {
            "_SESSIONS_DIR": self.sessions,
            "_FOLDERS_FILE": self.sessions / "folder.json",
            "_META_FILE": self.sessions / "session_meta.json",
            "_LEGACY_FOLDERS_FILES": (
                self.root / "folders.json",
                self.sessions / "folders.json",
            ),
            "_LEGACY_META_FILES": (self.root / "session_meta.json",),
        }
        self.patchers = [
            patch.object(chat, name, value) for name, value in replacements.items()
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_metadata_is_saved_inside_sessions_repository(self):
        chat.save_folders([{"id": "book_1", "name": "Book"}])
        chat._save_meta({"s1": {"folder_id": "book_1", "favorite": True}})

        folders = json.loads(
            (self.sessions / "folder.json").read_text(encoding="utf-8")
        )
        meta = json.loads(
            (self.sessions / "session_meta.json").read_text(encoding="utf-8")
        )
        self.assertEqual(folders["folders"][0]["id"], "book_1")
        self.assertTrue(meta["s1"]["favorite"])
        self.assertFalse((self.root / "folders.json").exists())
        self.assertFalse((self.root / "session_meta.json").exists())

    def test_legacy_metadata_is_loaded_and_migrated(self):
        (self.root / "folders.json").write_text(
            json.dumps({"folders": [{"id": "legacy", "name": "Legacy"}]}),
            encoding="utf-8",
        )
        (self.root / "session_meta.json").write_text(
            json.dumps({"old": {"folder_id": "legacy", "order": 2}}),
            encoding="utf-8",
        )

        self.assertEqual(chat.load_folders()[0]["id"], "legacy")
        self.assertEqual(chat._load_meta()["old"]["order"], 2)
        self.assertTrue((self.sessions / "folder.json").is_file())
        self.assertTrue((self.sessions / "session_meta.json").is_file())

    def test_new_metadata_takes_precedence_over_legacy(self):
        self.sessions.mkdir(parents=True)
        (self.sessions / "folder.json").write_text(
            json.dumps({"folders": [{"id": "new", "name": "New"}]}),
            encoding="utf-8",
        )
        (self.root / "folders.json").write_text(
            json.dumps({"folders": [{"id": "old", "name": "Old"}]}),
            encoding="utf-8",
        )

        self.assertEqual(chat.load_folders()[0]["id"], "new")


if __name__ == "__main__":
    unittest.main()
