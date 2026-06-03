"""
Smoke test for RAG-backed chat message construction.

Run:
    python tests/test_rag_chat.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.book_pipeline import run_book_pipeline
from src.chat import ChatSession


class FakeSession(ChatSession):
    def __init__(self, session_dir: str):
        super().__init__(session_dir, {
            "base_url": "https://example.test/v1",
            "api_key": "test",
            "model": "fake",
        })
        self.last_api_messages = []

    def _call_provider(self, messages: list[dict]) -> str:
        self.last_api_messages = messages
        return "fake answer"


def main() -> int:
    pdf = ROOT / "book" / "introductiontocomputermusic.pdf"
    out = ROOT / "tmp_rag_chat"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    result = run_book_pipeline(pdf, out)

    session_dir = out / "session"
    session = FakeSession(str(session_dir))
    session.name = "RAG test"
    session.system_prompt = "你是书籍导师。"
    session.book_json_path = result["book_json_path"]
    reply = session.chat("What is computer music synthesis?")

    system = session.last_api_messages[0]["content"]
    user = session.messages[-2]
    checks = [
        ("reply returned", reply == "fake answer"),
        ("system contains retrieved evidence", "本轮检索到的原文证据" in system),
        ("system contains chunk marker", "_p" in system),
        ("user message saved hits", bool(user.get("retrieval_hits"))),
        ("history saved", (session_dir / "chat_history.json").is_file()),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"hits={user.get('retrieval_hits')[:2]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
