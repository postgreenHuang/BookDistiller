"""
相对/绝对路径解析（仓库化重构）。

设计：
- 仓库（book_repo_dir，默认 ~/.Book-Distiller/sessions）只放可移植、可同步的内容：
  book.json、chapters/、notes/、index/、session 对话。
- 大体积可重建的中间产物 pages/、cache/ 放到独立的 workspace
  （book_workspace_dir，默认 ~/.Book-Distiller/.workspace），不进仓库。
- book.json 里：
    * 仓库内路径（paths.book_dir / paths.chapters_path /
      chapters[].text_path,note_path / index.{chunks,stats,terms}_path /
      memory.overview_path）按 book.json 自身目录存成相对路径。
    * 外部/机器本地路径（source_pdf / paths.pages_path / paths.workspace_dir）
      保持绝对，不做转换。
- chat_history.json 里所有路径（notes_path/book_json_path/chapter_text_paths/...）
  按 session 目录存成相对路径。

加载（load_book / resolve_session_paths）时把相对路径 resolve 成绝对，内存里永远是
绝对路径，和历史上游代码一致；保存（save_book / relativize_session_paths）时把绝对
路径转成相对写盘。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

# book.json 中按 book.json 目录做相对↔绝对的「仓库内」字段
_REPO_REL_PATH_KEYS = ("book_dir", "chapters_path")
_REPO_REL_SECTIONS = (
    ("index", ("chunks_path", "stats_path", "terms_path")),
    ("memory", ("overview_path", "knowledge_map_path")),
)
_CHAPTER_PATH_KEYS = ("text_path", "note_path")

# session chat_history.json 中按 session 目录做相对↔绝对的字段
_SESSION_REL_KEYS = ("slides_path", "transcript_path", "notes_path", "book_dir", "book_json_path")
_SESSION_REL_LIST_KEYS = ("chapter_text_paths",)


def resolve(base, p) -> Path:
    """p 绝对或空 → 原样返回 Path；否则返回 base/p。"""
    if not p:
        return Path()
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return Path(base) / pp


def to_rel(target, base) -> str:
    """把 target 转成相对 base 的 POSIX 字符串；跨盘/无法相对时返回绝对字符串。"""
    if not target:
        return ""
    t = Path(target)
    if not t.is_absolute():
        return t.as_posix()
    try:
        return t.relative_to(Path(base)).as_posix()
    except ValueError:
        return str(t)


def workspace_dir(book) -> Path:
    """大体积 pages/cache 的根目录（不在仓库里）。

    优先 paths.workspace_dir；缺省回退 paths.book_dir（兼容旧数据）。
    返回的是 book 里存储的值（通常为绝对路径）。
    """
    paths = book.get("paths") or {}
    ws = paths.get("workspace_dir")
    if ws:
        return Path(ws)
    return Path(paths.get("book_dir") or ".")


# ─── book.json 加载/保存 ──────────────────────────────────────────────────────

def _transform_book_paths(book: dict, base: Path, fn):
    """对 book dict 的「仓库内」路径字段逐个做字符串转换 fn(value, base)->str。"""
    paths = book.get("paths")
    if isinstance(paths, dict):
        for k in _REPO_REL_PATH_KEYS:
            if paths.get(k):
                paths[k] = fn(paths[k], base)
    for sec, keys in _REPO_REL_SECTIONS:
        s = book.get(sec)
        if isinstance(s, dict):
            for k in keys:
                if s.get(k):
                    s[k] = fn(s[k], base)
    for ch in book.get("chapters") or []:
        if isinstance(ch, dict):
            for k in _CHAPTER_PATH_KEYS:
                if ch.get(k):
                    ch[k] = fn(ch[k], base)
    return book


def load_book(book_json_path) -> dict:
    """读 book.json，把仓库内相对路径 resolve 成绝对（in-place on fresh dict）。

    source_pdf / pages_path / workspace_dir 保持原样（通常已绝对）。
    """
    p = Path(book_json_path)
    book = json.loads(p.read_text(encoding="utf-8"))
    base = p.parent
    _transform_book_paths(book, base, lambda v, b: str(resolve(b, v)))
    return book


def save_book(book_json_path, book: dict) -> None:
    """写 book.json，把仓库内绝对路径转成相对（相对 book.json 目录）。

    源 dict 不被修改（deepcopy 后再转换）。
    """
    p = Path(book_json_path)
    base = p.parent
    rel = copy.deepcopy(book)
    _transform_book_paths(rel, base, to_rel)
    p.write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── chapters.json 加载/保存 ──────────────────────────────────────────────────

def load_chapters(chapters_json_path):
    p = Path(chapters_json_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    base = p.parent
    for ch in data:
        if isinstance(ch, dict) and ch.get("text_path"):
            ch["text_path"] = str(resolve(base, ch["text_path"]))
    return data


def save_chapters(chapters_json_path, chapters) -> None:
    p = Path(chapters_json_path)
    base = p.parent
    rel = copy.deepcopy(chapters)
    for ch in rel:
        if isinstance(ch, dict) and ch.get("text_path"):
            ch["text_path"] = to_rel(ch["text_path"], base)
    p.write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── session chat_history.json ────────────────────────────────────────────────

def resolve_session_paths(history_data: dict, session_dir) -> dict:
    """chat_history.json 加载后，把相对路径 resolve 成绝对（in-place）。"""
    base = Path(session_dir)
    for k in _SESSION_REL_KEYS:
        if history_data.get(k):
            history_data[k] = str(resolve(base, history_data[k]))
    for k in _SESSION_REL_LIST_KEYS:
        lst = history_data.get(k) or []
        if lst:
            history_data[k] = [str(resolve(base, x)) for x in lst]
    return history_data


def relativize_session_paths(history_data: dict, session_dir) -> dict:
    """写盘前把 session 绝对路径转成相对（返回新 dict，不改原）。"""
    base = Path(session_dir)
    out = copy.deepcopy(history_data)
    for k in _SESSION_REL_KEYS:
        if out.get(k):
            out[k] = to_rel(out[k], base)
    for k in _SESSION_REL_LIST_KEYS:
        lst = out.get(k) or []
        if lst:
            out[k] = [to_rel(x, base) for x in lst]
    return out
