"""
Text chunking and lightweight local index for Phase B.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from src.paths import load_book, save_book, to_rel


INDEX_VERSION = "keyword-bm25-v1"


TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
PAGE_MARK_RE = re.compile(r"<!--\s*page:(\d+)\s*-->")


def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(text)]


def _split_text(text: str, max_chars: int = 1400, overlap: int = 180) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}".strip()
            continue
        if buf:
            chunks.append(buf)
        while len(para) > max_chars:
            chunks.append(para[:max_chars].strip())
            para = para[max_chars - overlap:].strip()
        buf = para
    if buf:
        chunks.append(buf)

    with_overlap: list[str] = []
    prev_tail = ""
    for chunk in chunks:
        merged = f"{prev_tail}\n{chunk}".strip() if prev_tail else chunk
        with_overlap.append(merged)
        prev_tail = chunk[-overlap:] if len(chunk) > overlap else chunk
    return with_overlap


def _extract_page(text: str, fallback: int) -> int:
    matches = PAGE_MARK_RE.findall(text)
    if matches:
        return int(matches[0])
    return fallback


def _strip_page_marks(text: str) -> str:
    return PAGE_MARK_RE.sub("", text).strip()


def build_index(book_json_path: str | Path, max_chars: int = 1400,
                embedding_provider: dict | None = None,
                log_cb: Callable[[str], None] | None = None) -> dict[str, Any]:
    book_path = Path(book_json_path)
    book = load_book(book_path)
    index_dir = Path(book["paths"]["book_dir"]) / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = index_dir / "chunks.jsonl"
    stats_path = index_dir / "stats.json"

    t0 = time.time()
    chunks: list[dict[str, Any]] = []
    doc_freq: dict[str, int] = defaultdict(int)
    total_terms = 0

    chapters = book.get("chapters", [])
    if log_cb:
        log_cb(f"开始构建索引: {len(chapters)} 章，切块大小 {max_chars} 字符")

    for ci, chapter in enumerate(chapters):
        text_path = Path(chapter["text_path"])
        text = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
        parts = _split_text(text, max_chars=max_chars)
        ch_chunks = 0
        for idx, part in enumerate(parts, 1):
            clean = _strip_page_marks(part)
            terms = tokenize(clean)
            if not terms:
                continue
            page = _extract_page(part, int(chapter["page_start"]))
            chunk_id = f"{chapter['chapter_id']}_p{page:04d}_{idx:03d}"
            term_counts = Counter(terms)
            for term in term_counts:
                doc_freq[term] += 1
            total_terms += len(terms)
            chunks.append({
                "chunk_id": chunk_id,
                "book_id": book["book_id"],
                "chapter_id": chapter["chapter_id"],
                "chapter_title": chapter["title"],
                "page": page,
                "type": "text",
                "text": clean,
                "source_path": to_rel(chapter["text_path"], book_path.parent),
                "tokens_estimate": max(1, math.ceil(len(clean) / 4)),
                "term_counts": dict(term_counts),
                "term_count": len(terms),
            })
            ch_chunks += 1
        if log_cb and ch_chunks > 0:
            log_cb(f"  索引 [{ci + 1}/{len(chapters)}] {chapter['chapter_id']}: {ch_chunks} 段")

    with chunks_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    stats = {
        "index_version": INDEX_VERSION,
        "chunk_count": len(chunks),
        "avg_doc_len": (total_terms / len(chunks)) if chunks else 0,
        "doc_freq": dict(doc_freq),
    }
    stats_path.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")

    provider = embedding_provider or {}
    embedding_model = provider.get("model") or "same-as-book-aggregation"
    book["index"] = {
        "version": INDEX_VERSION,
        "chunks_path": str(chunks_path),
        "stats_path": str(stats_path),
        "chunk_count": len(chunks),
        "embedding_provider": provider.get("name", ""),
        "embedding_model": embedding_model,
        "embedding_strategy": "same-as-book-aggregation",
    }
    save_book(book_path, book)

    if log_cb:
        elapsed = time.time() - t0
        avg_tokens = sum(c["tokens_estimate"] for c in chunks) // max(1, len(chunks))
        log_cb(f"索引构建完成: {len(chunks)} 段，平均 {avg_tokens} tokens/段，耗时 {elapsed:.1f}s")

    return book["index"]


def load_chunks(chunks_path: str | Path) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    with Path(chunks_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks
