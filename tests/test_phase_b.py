"""
Phase B smoke test for the local PDF -> chapters -> chunks -> retrieval loop.

Run:
    python tests/test_phase_b.py
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
from src.context_builder import build_context


def main() -> int:
    pdf = ROOT / "book" / "introductiontocomputermusic.pdf"
    if not pdf.is_file():
        print(f"FAIL: missing test PDF: {pdf}")
        return 1

    out = ROOT / "tmp_phase_b"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    result = run_book_pipeline(pdf, out)
    book_json = Path(result["book_json_path"])
    book = json.loads(book_json.read_text(encoding="utf-8"))
    ctx = build_context(book_json, "computer music sound synthesis", top_k=5)

    checks = [
        ("book.json exists", book_json.is_file()),
        ("pages extracted", result["text_page_count"] > 0),
        ("chapters generated", result["chapter_count"] > 0),
        ("chunks generated", result["chunk_count"] > 0),
        ("retrieval hits", len(ctx["hits"]) > 0),
        ("embedding follows aggregation marker", book["index"].get("embedding_strategy") == "same-as-book-aggregation"),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")

    print(f"book_json={book_json}")
    print(f"pages={result['page_count']} text_pages={result['text_page_count']} chapters={result['chapter_count']} chunks={result['chunk_count']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
