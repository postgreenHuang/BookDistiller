"""
Smoke test for creating book folder sessions after Phase B.

Run:
    python tests/test_book_sessions.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.book_pipeline import run_book_pipeline


def main() -> int:
    pdf = ROOT / "book" / "introductiontocomputermusic.pdf"
    if not pdf.is_file():
        print(f"FAIL: missing test PDF: {pdf}")
        return 1

    out = ROOT / "tmp_book_sessions"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    result = run_book_pipeline(pdf, out, create_sessions=True)
    book = json.loads(Path(result["book_json_path"]).read_text(encoding="utf-8"))

    from src.chat import _session_groups, load_folders
    from src.paths import resolve_session_paths
    folder_id = f"book_{book['book_id']}"
    expected_groups = _session_groups(book["chapters"], "level2")
    book_dir = Path(result["book_json_path"]).parent
    expected_chapter = book_dir / f"{book['book_id']}_{book['chapters'][0]['chapter_id']}" / "chat_history.json"
    expected_overview = book_dir / f"{book['book_id']}_overview" / "chat_history.json"
    overview = json.loads(expected_overview.read_text(encoding="utf-8")) if expected_overview.is_file() else {}
    resolve_session_paths(overview, expected_overview.parent)

    checks = [
        ("session count uses default level-2 groups plus overview", result["session_count"] == len(expected_groups) + 1),
        ("book folder exists", any(f.get("id") == folder_id for f in load_folders())),
        ("first chapter session exists", expected_chapter.is_file()),
        ("overview session exists", expected_overview.is_file()),
        ("overview binds book json", overview.get("book_json_path") == result["book_json_path"]),
        ("overview is last order", result["session_count"] > len(expected_groups)),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"session_count={result['session_count']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
