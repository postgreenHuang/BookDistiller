"""
Phase C smoke test: rerunning the same PDF reuses local artifacts.

Run:
    python tests/test_phase_c.py
"""

from __future__ import annotations

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

    out = ROOT / "tmp_phase_c"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    first = run_book_pipeline(pdf, out)
    book_dir = Path(first["book_dir"])
    pages_path = book_dir / "pages" / "pages.jsonl"
    chunks_path = book_dir / "index" / "chunks.jsonl"
    pages_mtime = pages_path.stat().st_mtime_ns
    chunks_mtime = chunks_path.stat().st_mtime_ns

    second = run_book_pipeline(pdf, out)
    checks = [
        ("pdf cache hit", "pdf" in second["cache_hits"]),
        ("chapters cache hit", "chapters" in second["cache_hits"]),
        ("index cache hit", "index" in second["cache_hits"]),
        ("pages not rewritten", pages_path.stat().st_mtime_ns == pages_mtime),
        ("chunks not rewritten", chunks_path.stat().st_mtime_ns == chunks_mtime),
        ("second run still retrievable", second["smoke_hits"] > 0),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"cache_hits={second['cache_hits']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
