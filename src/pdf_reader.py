"""
PDF text-layer reader for the Book-Distiller pipeline.
Supports page type classification for visual analysis routing.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.paths import save_book


PARSER_VERSION = "pypdf-text-v2"


def slugify(value: str, fallback: str = "book") -> str:
    text = re.sub(r"[^\w一-鿿]+", "-", value.strip().lower(), flags=re.UNICODE)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or fallback


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify_page(page_data: dict) -> str:
    """Classify a page by its content type.

    Returns one of: "text_ok", "needs_ocr", "is_cover", "is_blank".
    Pages classified as "needs_ocr" or "is_blank" will be routed to
    the visual analysis pipeline for OCR / image understanding.
    """
    page_num = int(page_data.get("page", 0))
    char_count = int(page_data.get("char_count", 0))
    has_text = bool(page_data.get("has_text", False))

    # Blank page: no text at all
    if not has_text and char_count == 0:
        if page_num == 1:
            return "is_cover"
        return "is_blank"

    # Scanned page: PDF has no text layer for this page
    if not has_text:
        if page_num == 1:
            return "is_cover"
        return "needs_ocr"

    # Cover: first page with minimal text
    if page_num == 1 and char_count < 200:
        return "is_cover"

    return "text_ok"


def _read_with_pypdf(pdf_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 PDF 文本解析依赖 pypdf，请先安装 pypdf") from exc

    reader = PdfReader(str(pdf_path))
    meta_raw = reader.metadata or {}
    metadata = {
        str(k).lstrip("/"): str(v)
        for k, v in dict(meta_raw).items()
        if v is not None
    }
    pages: list[dict[str, Any]] = []
    for idx, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = _clean_text(text)
        page_data = {
            "page": idx,
            "text": text,
            "char_count": len(text),
            "has_text": bool(text.strip()),
        }
        page_data["page_type"] = classify_page(page_data)
        pages.append(page_data)

    toc: list[dict[str, Any]] = []
    try:
        outline = reader.outline
        toc = _flatten_outline(reader, outline)
    except Exception:
        toc = []

    return metadata, pages, toc


def _flatten_outline(reader: Any, outline: list[Any], level: int = 1) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in outline:
        if isinstance(entry, list):
            items.extend(_flatten_outline(reader, entry, level + 1))
            continue
        try:
            title = str(getattr(entry, "title", "") or "")
            page_index = reader.get_destination_page_number(entry)
        except Exception:
            continue
        if title:
            items.append({
                "title": title.strip(),
                "page": int(page_index) + 1,
                "level": level,
            })
    return items


def read_pdf(pdf_path: str | Path, output_root: str | Path,
             workspace_root: str | Path | None = None,
             pdf_sha256: str | None = None,
             log_cb: Callable[[str], None] | None = None) -> dict[str, Any]:
    pdf = Path(pdf_path).resolve()
    if not pdf.is_file():
        raise FileNotFoundError(str(pdf))
    if pdf.suffix.lower() != ".pdf":
        raise ValueError("第一版仅支持 PDF")

    t0 = time.time()
    metadata, pages, toc = _read_with_pypdf(pdf)
    parse_elapsed = time.time() - t0

    # 直接用 PDF 文件名作为书名（最可靠）
    title = pdf.stem.strip() or pdf.stem
    author = (metadata.get("Author") or "").strip()
    book_id = slugify(pdf.stem)

    if log_cb:
        text_pages = sum(1 for p in pages if p.get("has_text"))
        log_cb(f"PDF 解析完成: {title}" + (f" / {author}" if author else ""))
        log_cb(f"  共 {len(pages)} 页，文本页 {text_pages}，扫描/空白页 {len(pages) - text_pages}")
        if toc:
            log_cb(f"  PDF 内置目录: {len(toc)} 个条目")
        else:
            log_cb("  PDF 无内置目录，将自动检测章节")
        log_cb(f"  解析耗时 {parse_elapsed:.1f}s")

    # 仓库（可移植）：book.json + chapters/notes/index
    repo_book_dir = Path(output_root).resolve() / f"book_{book_id}"
    repo_book_dir.mkdir(parents=True, exist_ok=True)
    (repo_book_dir / "chapters").mkdir(exist_ok=True)
    (repo_book_dir / "index").mkdir(exist_ok=True)
    (repo_book_dir / "notes").mkdir(exist_ok=True)

    # workspace（大体积可重建，不进仓库）：pages/cache
    if workspace_root is None:
        from src.config import get_book_workspace_dir
        workspace_root = get_book_workspace_dir()
    ws_book_dir = Path(workspace_root).resolve() / book_id
    ws_book_dir.mkdir(parents=True, exist_ok=True)
    (ws_book_dir / "pages").mkdir(exist_ok=True)
    (ws_book_dir / "cache").mkdir(exist_ok=True)

    pages_path = ws_book_dir / "pages" / "pages.jsonl"
    with pages_path.open("w", encoding="utf-8") as f:
        for page in pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")

    # Page type summary
    page_type_counts = dict(Counter(p.get("page_type", "unknown") for p in pages))

    book = {
        "book_id": book_id,
        "title": title,
        "author": author,
        "source_pdf": str(pdf),
        "pdf_sha256": pdf_sha256 or sha256_file(pdf),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "parser": PARSER_VERSION,
        "page_count": len(pages),
        "text_page_count": sum(1 for p in pages if p.get("has_text")),
        "page_types": page_type_counts,
        "metadata": metadata,
        "toc": toc,
        "paths": {
            "book_dir": str(repo_book_dir),     # 仓库内（save_book 会转成相对 "."）
            "pages_path": str(pages_path),       # workspace，保持绝对
            "workspace_dir": str(ws_book_dir),   # pages/cache 根，保持绝对
        },
        "chapters": [],
        "index": {},
    }
    save_book(repo_book_dir / "book.json", book)
    return book


def load_pages(pages_path: str | Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with Path(pages_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pages.append(json.loads(line))
    return pages


def save_pages(pages_path: str | Path, pages: list[dict[str, Any]]):
    """Write pages list back to pages.jsonl."""
    with Path(pages_path).open("w", encoding="utf-8") as f:
        for page in pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")
