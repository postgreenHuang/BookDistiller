"""
仓库 / workspace 迁移：用户在设置里更改 book_repo_dir / book_workspace_dir 后，
把现有数据搬到新位置，并刷新 book.json 里的 workspace 绝对路径。

- 仓库（repo）内的路径全是相对路径（相对各自 JSON 所在目录），移动书文件夹后无需改写。
- workspace 的 pages_path / workspace_dir 是机器本地绝对路径，移动后必须改写。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from src.paths import load_book, save_book

LogCallback = Callable[[str], None]


def _same(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except Exception:
        return str(a) == str(b)


def relocate_library(old_repo, new_repo, old_ws, new_ws,
                     log_cb: LogCallback | None = None) -> dict:
    """把现有书数据从 old_repo/old_ws 搬到 new_repo/new_ws，并改写 workspace 绝对路径。

    返回 {moved_books, moved_ws, rewritten, repo, workspace}。
    """
    def log(m: str):
        if log_cb:
            log_cb(m)

    old_repo, new_repo = Path(old_repo), Path(new_repo)
    old_ws, new_ws = Path(old_ws), Path(new_ws)

    repo_moved = not _same(old_repo, new_repo)
    ws_moved = not _same(old_ws, new_ws)

    moved_books = 0
    if repo_moved and old_repo.is_dir():
        new_repo.mkdir(parents=True, exist_ok=True)
        for d in list(old_repo.iterdir()):
            if d.is_dir() and d.name.startswith("book_"):
                dst = new_repo / d.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(d), str(dst))
                moved_books += 1
        log(f"仓库迁移：{moved_books} 个书文件夹 → {new_repo}")

    # 以（可能的新）仓库为基准枚举 book_id
    repo_root = new_repo if repo_moved else old_repo
    book_ids = [
        d.name[len("book_"):]
        for d in (repo_root.iterdir() if repo_root.is_dir() else [])
        if d.is_dir() and d.name.startswith("book_")
    ]

    moved_ws = 0
    if ws_moved and old_ws.is_dir():
        new_ws.mkdir(parents=True, exist_ok=True)
        for bid in book_ids:
            sd = old_ws / bid
            if sd.is_dir():
                dst = new_ws / bid
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(sd), str(dst))
                moved_ws += 1
        # 没有对应 repo 记录的孤立 workspace 目录也一并搬走
        for d in list(old_ws.iterdir()):
            if d.is_dir() and d.name not in book_ids:
                dst = new_ws / d.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(d), str(dst))
                moved_ws += 1
        log(f"workspace 迁移：{moved_ws} 个书目录 → {new_ws}")

    # 改写 book.json 的 workspace 绝对路径
    rewritten = 0
    if ws_moved:
        for bid in book_ids:
            bj = repo_root / f"book_{bid}" / "book.json"
            if not bj.is_file():
                continue
            try:
                book = load_book(bj)
                ws_dir = new_ws / bid
                paths = book.setdefault("paths", {})
                paths["workspace_dir"] = str(ws_dir)
                paths["pages_path"] = str(ws_dir / "pages" / "pages.jsonl")
                save_book(bj, book)
                rewritten += 1
            except Exception as exc:
                log(f"  改写 {bid} 失败：{exc}")
        log(f"book.json workspace 路径改写：{rewritten}")

    return {
        "moved_books": moved_books,
        "moved_ws": moved_ws,
        "rewritten": rewritten,
        "repo": str(new_repo),
        "workspace": str(new_ws),
    }
