"""
一次性迁移脚本（Step 1）：把已蒸馏的 3 本书从「F: 输出目录 + 扁平 sessions」
迁入统一仓库 ~/.Book-Distiller/sessions/book_<id>/ ，并把已迁移资源的路径改写为
相对路径（相对每个 JSON 文件自身的目录）。

范围（只迁这些）：
  - book.json
  - chapters/   （含 chapters/chapters.json + chXXX/text.md）
  - notes/      （含 chXXX.md + book_overview.md）
  - index/      （chunks.jsonl / stats.json / terms.json / retrieval_smoke.json）
  - 该书的 session 目录（<book_id>_chXXX / <book_id>_overview）

不迁、保留在 F: 原位（路径保持绝对）：
  - pages/      （渲染图，大体积可重建）
  - cache/      （视觉缓存，大体积可重建）
  - source_pdf  （原始 PDF，外部文件）

约定：每个 JSON 里存储的路径都是「相对该 JSON 自身目录」的相对路径；
      未迁移资源 / 外部路径保持绝对，解析时按绝对处理。

非破坏性：只复制 + 校验，不删除任何原文件。确认无误后另行清理。
可重入：已相对化的路径会被跳过，目录复制使用 dirs_exist_ok=True。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

# ── 配置 ──────────────────────────────────────────────────────────────────────
USER_DATA_DIR = Path.home() / ".Book-Distiller"
REPO_ROOT = USER_DATA_DIR / "sessions"          # 仓库根 = 现有 sessions 目录
F_OUTPUT_ROOT = Path("F:/书籍蒸馏")             # 旧蒸馏输出根目录

# (book_id, 旧输出目录) —— folder_id 自动取 book_<book_id>
BOOKS = [
    "sql必知必会",
    "fundamentals-of-computer-graphics-5th",
    "gpu精粹1-实时图形编程的技术-技巧和技艺",
]

MIGRATED_SUBDIRS = ("chapters", "notes", "index")   # 连同 book.json 一起迁
KEPT_ON_F = ("pages", "cache")                       # 留在 F:，路径保持绝对


# ── 路径改写核心 ──────────────────────────────────────────────────────────────

def migrate_path(value: str, old_root: Path, new_root: Path, json_base_dir: Path):
    """把一个绝对路径（位于 old_root 之下、属于已迁移资源）改写为相对路径，
    指向它在 new_root 下的新位置；相对基准为 json_base_dir（该 JSON 文件所在目录）。
    返回 (新值, 是否改动)。未迁移/外部/已相对化的路径原样返回。"""
    if not value:
        return value, False
    p = Path(value)
    if not p.is_absolute():
        return value, False  # 已是相对（重入时跳过）
    try:
        rel = p.relative_to(old_root)
    except ValueError:
        return value, False  # 不在旧书目录下（source_pdf 等）→ 保持绝对
    if rel.parts and rel.parts[0] in KEPT_ON_F:
        return value, False  # pages/cache 留在 F: → 保持绝对
    new_abs = new_root / rel
    stored = Path(os.path.relpath(new_abs, json_base_dir)).as_posix()
    return stored, True


def rewrite_book_json(book_json: Path, old_root: Path, new_root: Path, audit: list):
    data = json.loads(book_json.read_text(encoding="utf-8"))
    base = book_json.parent
    changed = 0

    def rw(value):
        nonlocal changed
        nv, ok = migrate_path(value, old_root, new_root, base)
        if ok:
            changed += 1
            audit.append((str(book_json), "abs→rel", value, nv, str(base / nv)))
        return nv

    # paths.chapters_path 迁移；paths.book_dir / paths.pages_path 保持绝对（pages/cache 在 F:）
    paths = data.get("paths") or {}
    if paths.get("chapters_path"):
        paths["chapters_path"] = rw(paths["chapters_path"])

    for ch in data.get("chapters") or []:
        if ch.get("text_path"):
            ch["text_path"] = rw(ch["text_path"])
        if ch.get("note_path"):
            ch["note_path"] = rw(ch["note_path"])

    idx = data.get("index") or {}
    for k in ("chunks_path", "stats_path", "terms_path"):
        if idx.get(k):
            idx[k] = rw(idx[k])

    mem = data.get("memory") or {}
    if mem.get("overview_path"):
        mem["overview_path"] = rw(mem["overview_path"])

    book_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def rewrite_chapters_json(chapters_json: Path, old_root: Path, new_root: Path, audit: list):
    if not chapters_json.is_file():
        return 0
    data = json.loads(chapters_json.read_text(encoding="utf-8"))
    base = chapters_json.parent  # …/book_<id>/chapters
    changed = 0
    for ch in data:
        v = ch.get("text_path")
        nv, ok = migrate_path(v, old_root, new_root, base)
        if ok:
            ch["text_path"] = nv
            changed += 1
            audit.append((str(chapters_json), "abs→rel", v, nv, str(base / nv)))
    chapters_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def rewrite_session(history_json: Path, old_root: Path, new_root: Path, audit: list):
    data = json.loads(history_json.read_text(encoding="utf-8"))
    base = history_json.parent  # …/book_<id>/<sid>
    changed = 0

    def rw(value):
        nonlocal changed
        nv, ok = migrate_path(value, old_root, new_root, base)
        if ok:
            changed += 1
            audit.append((str(history_json), "abs→rel", value, nv, str(base / nv)))
        return nv

    for key in ("slides_path", "notes_path", "book_dir", "book_json_path"):
        if data.get(key):
            data[key] = rw(data[key])
    ctp = data.get("chapter_text_paths") or []
    if ctp:
        data["chapter_text_paths"] = [rw(p) for p in ctp]

    history_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def rewrite_session_meta(sids_by_book: dict, old_roots: dict, new_roots: dict, audit: list):
    meta_file = USER_DATA_DIR / "session_meta.json"
    if not meta_file.is_file():
        return 0
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    base = meta_file.parent  # ~/.Book-Distiller
    changed = 0
    for book_id, sids in sids_by_book.items():
        old_root = old_roots[book_id]
        new_root = new_roots[book_id]
        for sid in sids:
            m = meta.get(sid) or {}
            v = m.get("book_json_path")
            nv, ok = migrate_path(v, old_root, new_root, base)
            if ok:
                m["book_json_path"] = nv
                changed += 1
                audit.append((str(meta_file), "abs→rel", v, nv, str(base / nv)))
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


# ── 校验 ──────────────────────────────────────────────────────────────────────

def verify_audit(audit: list):
    """audit 条目: (json_path, kind, old_val, new_val, resolved_abs)"""
    ok = bad = 0
    broken = []
    for entry in audit:
        _, _, _, _, resolved = entry
        if Path(resolved).exists():
            ok += 1
        else:
            bad += 1
            broken.append(entry)
    return ok, bad, broken


# ── 主流程 ────────────────────────────────────────────────────────────────────

def migrate_book(book_id: str, audit: list) -> dict:
    folder_id = f"book_{book_id}"
    old_root = F_OUTPUT_ROOT / book_id
    new_root = REPO_ROOT / folder_id
    if not old_root.is_dir():
        return {"book_id": book_id, "skipped": f"旧目录不存在: {old_root}"}

    new_root.mkdir(parents=True, exist_ok=True)

    # 1) 复制 F: 上的 book.json + chapters/notes/index
    copied_files = 0
    src_book_json = old_root / "book.json"
    if src_book_json.is_file():
        shutil.copy2(src_book_json, new_root / "book.json")
        copied_files += 1
    for sub in MIGRATED_SUBDIRS:
        s = old_root / sub
        if s.is_dir():
            shutil.copytree(s, new_root / sub, dirs_exist_ok=True)
            copied_files += sum(1 for _ in s.rglob("*") if _.is_file())

    # 2) 复制该书扁平 session 目录 → book_<id>/<sid>/
    session_sids = []
    for d in sorted(REPO_ROOT.iterdir()):
        if d.is_dir() and d.name.startswith(f"{book_id}_"):
            dst = new_root / d.name
            shutil.copytree(d, dst, dirs_exist_ok=True)
            session_sids.append(d.name)
        elif d.is_dir() and d.name == folder_id:
            continue  # 跳过自己

    # 3) 改写路径
    bj_changed = rewrite_book_json(new_root / "book.json", old_root, new_root, audit)
    cj_changed = rewrite_chapters_json(new_root / "chapters" / "chapters.json", old_root, new_root, audit)
    sess_changed = 0
    for sid in session_sids:
        hj = new_root / sid / "chat_history.json"
        if hj.is_file():
            sess_changed += rewrite_session(hj, old_root, new_root, audit)

    return {
        "book_id": book_id,
        "folder_id": folder_id,
        "new_root": str(new_root),
        "copied_files": copied_files,
        "sessions": len(session_sids),
        "rewrites": {
            "book.json": bj_changed,
            "chapters.json": cj_changed,
            "sessions_total": sess_changed,
        },
        "_sids": session_sids,
        "_old_root": old_root,
        "_new_root": new_root,
    }


def main():
    audit: list[tuple[str, str, str, str, str]] = []
    sids_by_book: dict[str, list[str]] = {}
    old_roots: dict[str, Path] = {}
    new_roots: dict[str, Path] = {}

    print(f"仓库根: {REPO_ROOT}")
    print(f"旧输出根: {F_OUTPUT_ROOT}")
    print("=" * 70)

    for book_id in BOOKS:
        r = migrate_book(book_id, audit)
        if r.get("skipped"):
            print(f"[跳过] {book_id}: {r['skipped']}")
            continue
        sids_by_book[book_id] = r["_sids"]
        old_roots[book_id] = r["_old_root"]
        new_roots[book_id] = r["_new_root"]
        print(f"[完成] {book_id}")
        print(f"   新目录     : {r['new_root']}")
        print(f"   复制文件数 : {r['copied_files']}")
        print(f"   session 数 : {r['sessions']}")
        print(f"   路径改写   : book.json={r['rewrites']['book.json']}, "
              f"chapters.json={r['rewrites']['chapters.json']}, "
              f"sessions 合计={r['rewrites']['sessions_total']}")
        del r["_sids"], r["_old_root"], r["_new_root"]

    # 4) 改写 session_meta.json 中各 session 的 book_json_path
    meta_changed = rewrite_session_meta(sids_by_book, old_roots, new_roots, audit)
    print("-" * 70)
    print(f"session_meta.json 改写: {meta_changed}")

    # 5) 校验：每个被改写的相对路径都能解析到真实文件
    ok, bad, broken = verify_audit(audit)
    print("-" * 70)
    print(f"校验: 解析成功 {ok} 条, 失败 {bad} 条")
    if broken:
        print("!! 以下相对路径解析失败（原文件可能不存在）:")
        for json_path, kind, old_val, new_val, resolved in broken[:20]:
            print(f"   - {json_path}\n       {new_val!r} → {resolved}  (原: {old_val})")

    print("=" * 70)
    print("迁移完成（仅复制+改写+校验，未删除任何原文件）。")


if __name__ == "__main__":
    main()
