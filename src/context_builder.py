"""
Build bounded prompt context from retrieval hits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.retriever import retrieve


def build_context(book_json_path: str | Path, query: str, top_k: int = 8,
                  max_chars: int = 9000) -> dict[str, Any]:
    book = json.loads(Path(book_json_path).read_text(encoding="utf-8"))
    hits = retrieve(book_json_path, query, top_k=top_k)
    parts = [
        f"书名: {book.get('title', '')}",
        f"作者: {book.get('author', '') or '未知'}",
        "",
        "检索证据:",
    ]
    used = 0
    packed_hits = []
    for hit in hits:
        header = f"[{hit['chunk_id']}] {hit.get('chapter_title', '')} | p.{hit.get('page')} | score={hit.get('score')}"
        text = str(hit.get("text", "")).strip()
        block = f"{header}\n{text}\n"
        if used + len(block) > max_chars and packed_hits:
            break
        parts.append(block)
        used += len(block)
        packed_hits.append({
            "chunk_id": hit["chunk_id"],
            "chapter_id": hit.get("chapter_id", ""),
            "page": hit.get("page"),
            "score": hit.get("score"),
        })
    return {
        "book_id": book.get("book_id", ""),
        "query": query,
        "context": "\n".join(parts).strip(),
        "hits": packed_hits,
    }
