"""
Lightweight keyword/BM25 retriever for Phase B.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from src.indexer import load_chunks, tokenize
from src.paths import load_book


def _bm25_score(query_terms: list[str], chunk: dict[str, Any], stats: dict[str, Any]) -> float:
    counts = chunk.get("term_counts") or {}
    doc_len = float(chunk.get("term_count") or 0)
    avg_len = float(stats.get("avg_doc_len") or 1.0)
    total_docs = max(1, int(stats.get("chunk_count") or 1))
    doc_freq = stats.get("doc_freq") or {}
    k1 = 1.5
    b = 0.75
    score = 0.0
    for term in query_terms:
        tf = float(counts.get(term) or 0)
        if tf <= 0:
            continue
        df = float(doc_freq.get(term) or 0)
        idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
        denom = tf + k1 * (1 - b + b * doc_len / avg_len)
        score += idf * (tf * (k1 + 1)) / denom
    return score


def retrieve(book_json_path: str | Path, query: str, top_k: int = 8) -> list[dict[str, Any]]:
    book = load_book(book_json_path)
    index = book.get("index") or {}
    chunks_path = index.get("chunks_path")
    stats_path = index.get("stats_path")
    if not chunks_path or not stats_path:
        raise RuntimeError("索引不存在，请先运行 build_index")

    query_terms = tokenize(query)
    if not query_terms:
        return []
    stats = json.loads(Path(stats_path).read_text(encoding="utf-8"))
    chunks = load_chunks(chunks_path)
    hits = []
    for chunk in chunks:
        score = _bm25_score(query_terms, chunk, stats)
        if score <= 0:
            continue
        item = {k: v for k, v in chunk.items() if k not in {"term_counts"}}
        item["score"] = round(score, 6)
        hits.append(item)
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:top_k]
