import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import src.chat as chat
from src.gui.chat_widget import ChatWidget


class SessionFileWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_external_history_change_reloads_current_session(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "sessions"
            session_dir = repo / "external_session"
            session_dir.mkdir(parents=True)
            (repo / "folder.json").write_text(
                json.dumps({"folders": []}), encoding="utf-8",
            )
            (repo / "session_meta.json").write_text("{}", encoding="utf-8")
            history = session_dir / "chat_history.json"
            history.write_text(
                json.dumps({
                    "name": "External",
                    "created_at": "2026-07-30 10:00:00",
                    "messages": [{"role": "assistant", "content": "before"}],
                }),
                encoding="utf-8",
            )

            with patch.object(chat, "get_sessions_dir", return_value=repo):
                widget = ChatWidget()
                widget.refresh_session_list({})
                widget._select_session_in_tree(str(session_dir))
                self.assertEqual(widget.session.messages[0]["content"], "before")

                history.write_text(
                    json.dumps({
                        "name": "External",
                        "created_at": "2026-07-30 10:00:00",
                        "messages": [
                            {"role": "assistant", "content": "after"},
                        ],
                    }),
                    encoding="utf-8",
                )
                widget._on_repository_path_changed(str(history))
                widget._apply_repository_changes()

                self.assertEqual(widget.session.messages[0]["content"], "after")
                watched = (
                    set(widget._repository_watcher.files())
                    | set(widget._repository_watcher.directories())
                )
                self.assertIn(str(repo), watched)
                self.assertIn(str(history), watched)
                widget.close()


if __name__ == "__main__":
    unittest.main()
