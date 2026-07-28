"""
Cache validation helpers for resumable book pipelines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.indexer import INDEX_VERSION
from src.paths import load_book, load_chapters, save_book
from src.pdf_reader import PARSER_VERSION, sha256_file, slugify


def expected_book_dir(pdf_path: str | Path, output_dir: str | Path) -> Path:
    # output_dir 现在是仓库根；书文件夹 = repo/book_<id>（与 folder_id 一致）
    return Path(output_dir).resolve() / f"book_{slugify(Path(pdf_path).stem)}"


def expected_book_json_path(pdf_path: str | Path, output_dir: str | Path) -> Path:
    return expected_book_dir(pdf_path, output_dir) / "book.json"


def load_json(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_cached_book(pdf_path: str | Path, output_dir: str | Path) -> tuple[dict[str, Any] | None, str]:
    pdf = Path(pdf_path).resolve()
    book_path = expected_book_json_path(pdf, output_dir)
    if not book_path.is_file():
        return None, sha256_file(pdf)
    book = load_book(book_path)

    current_hash = sha256_file(pdf)
    pages_path = Path((book.get("paths") or {}).get("pages_path", ""))
    if (
        book.get("source_pdf") == str(pdf)
        and book.get("pdf_sha256") == current_hash
        and book.get("parser") == PARSER_VERSION
        and pages_path.is_file()
    ):
        # 规整 book_dir 到实际发现位置（仓库），避免历史绝对路径误导后续写入
        book.setdefault("paths", {})["book_dir"] = str(expected_book_dir(pdf, output_dir))
        return book, current_hash
    return None, current_hash


def has_valid_chapters(book: dict[str, Any], book_json_path: str | Path | None = None) -> bool:
    """Check if chapters were already detected and saved.

    Checks book["chapters"] first, then falls back to chapters/chapters.json
    on disk. When restoring from disk, also writes back to book.json so
    downstream functions that read book.json from disk see the chapters.
    """
    chapters = book.get("chapters") or []
    if len(chapters) > 0:
        return True
    # Fallback: check chapters.json on disk
    book_dir = (book.get("paths") or {}).get("book_dir", "")
    if book_dir:
        chapters_path = Path(book_dir) / "chapters" / "chapters.json"
        if chapters_path.is_file():
            try:
                disk_chapters = load_chapters(chapters_path)
                if disk_chapters and len(disk_chapters) > 0:
                    # Restore chapters to book dict
                    book["chapters"] = disk_chapters
                    # Also write back to book.json so downstream disk reads see chapters
                    json_path = Path(book_json_path) if book_json_path else Path(book_dir) / "book.json"
                    if json_path.is_file():
                        try:
                            save_book(json_path, book)
                        except Exception:
                            pass  # Non-fatal: in-memory restore is still valid
                    return True
            except Exception:
                pass
    return False


def has_valid_index(book: dict[str, Any]) -> bool:
    index = book.get("index") or {}
    if index.get("version") != INDEX_VERSION:
        return False
    chunks_path = Path(index.get("chunks_path", ""))
    stats_path = Path(index.get("stats_path", ""))
    if not chunks_path.is_file() or not stats_path.is_file():
        return False
    try:
        return int(index.get("chunk_count") or 0) > 0
    except Exception:
        return False


# ─── Visual analysis cache ───

def _visual_cache_key(page_num: int, vision_model: str, prompt_version: str) -> str:
    """Deterministic cache key for a visual analysis result."""
    import hashlib
    raw = f"{page_num}_{vision_model}_{prompt_version}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"page_{page_num:04d}_{h}"


def visual_cache_dir(book_dir: str | Path, page_num: int,
                     vision_model: str, prompt_version: str) -> Path:
    """Return (and create) the cache directory for a visual analysis result."""
    key = _visual_cache_key(page_num, vision_model, prompt_version)
    d = Path(book_dir) / "cache" / "visual" / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cached_visual(book_dir: str | Path, page_num: int,
                      vision_model: str, prompt_version: str) -> dict[str, Any] | None:
    """Return cached visual analysis result, or None if not cached."""
    key = _visual_cache_key(page_num, vision_model, prompt_version)
    result_path = Path(book_dir) / "cache" / "visual" / key / "result.json"
    if result_path.is_file():
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_visual_cache(book_dir: str | Path, page_num: int,
                      vision_model: str, prompt_version: str,
                      result: dict, image_path: str | Path | None = None) -> Path:
    """Save visual analysis result (and optionally the rendered image) to cache."""
    import shutil
    d = visual_cache_dir(book_dir, page_num, vision_model, prompt_version)
    (d / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if image_path and Path(image_path).is_file():
        shutil.copy2(str(image_path), str(d / "page.png"))
    return d


def has_visual_cache(book_dir: str | Path, page_num: int,
                     vision_model: str, prompt_version: str) -> bool:
    """Check if a cached visual analysis result exists."""
    key = _visual_cache_key(page_num, vision_model, prompt_version)
    return (Path(book_dir) / "cache" / "visual" / key / "result.json").is_file()
