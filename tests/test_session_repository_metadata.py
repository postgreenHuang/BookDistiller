import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.chat as chat
from src.library import relocate_library


class SessionRepositoryMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sessions = self.root / "sessions"
        self.patchers = [
            patch.object(chat, "USER_DATA_DIR", self.root),
            patch.object(chat, "get_sessions_dir", return_value=self.sessions),
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

    def test_metadata_path_updates_when_repository_changes(self):
        second_repo = self.root / "cloud-sessions"
        self.patchers[-1].stop()
        active_repo = {"path": self.sessions}
        dynamic = patch.object(
            chat, "get_sessions_dir", side_effect=lambda: active_repo["path"],
        )
        self.patchers[-1] = dynamic
        dynamic.start()

        chat.save_folders([{"id": "first"}])
        active_repo["path"] = second_repo
        chat.save_folders([{"id": "second"}])

        self.assertEqual(
            json.loads((self.sessions / "folder.json").read_text())["folders"][0]["id"],
            "first",
        )
        self.assertEqual(
            json.loads((second_repo / "folder.json").read_text())["folders"][0]["id"],
            "second",
        )


class LibraryRelocationTests(unittest.TestCase):
    def test_relocation_moves_metadata_and_flat_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_repo = root / "old"
            new_repo = root / "new"
            workspace = root / "workspace"
            (old_repo / "book_1").mkdir(parents=True)
            (old_repo / "manual_session").mkdir()
            (old_repo / "manual_session" / "chat_history.json").write_text("{}")
            (old_repo / "folder.json").write_text('{"folders": []}')
            (old_repo / "session_meta.json").write_text("{}")

            result = relocate_library(
                old_repo, new_repo, workspace, workspace,
            )

            self.assertEqual(result["moved_books"], 1)
            self.assertEqual(result["moved_sessions"], 1)
            self.assertEqual(result["moved_metadata"], 2)
            self.assertTrue((new_repo / "book_1").is_dir())
            self.assertTrue((new_repo / "manual_session").is_dir())
            self.assertTrue((new_repo / "folder.json").is_file())
            self.assertTrue((new_repo / "session_meta.json").is_file())


if __name__ == "__main__":
    unittest.main()
