"""
Book-Distiller 对话导出/导入模块
- 导出为 .vdc (ZIP) 归档，包含 session 数据 + 关联文件 + 图片
- 导出书籍包 (.bdc)：包含完整 book_dir（排除 pages/cache）+ 所有 sessions
- 导入时自动重定向路径，在新机器上可直接使用
"""

import copy
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

from src.chat import _SESSIONS_DIR, load_folders, save_folders, _load_meta, _save_meta, _get_meta

_EXPORT_VERSION = 1
_BOOK_EXPORT_VERSION = 3  # v3: book.json 路径也转为 BOOK_DIR/ 便携格式

# 匹配 ![alt](src) 中的 src
_MD_IMG_SRC_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
# 匹配 file:/// 开头的路径
_FILE_URL_RE = re.compile(r'file:///(/[^\s\)]+)')

# ──────────────────────────────────────────────
#  旧版导出/导入（单 session 级别，兼容 .vdc）
# ──────────────────────────────────────────────

def export_sessions(session_ids: list[str], dest_path: str) -> bool:
    """将选中 sessions 打包为 .vdc ZIP 文件"""
    meta_sessions = []
    sess_meta = _load_meta()

    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for sid in session_ids:
            session_dir = _SESSIONS_DIR / sid
            hfile = session_dir / "chat_history.json"
            if not hfile.is_file():
                continue

            data = json.loads(hfile.read_text(encoding="utf-8"))
            base_dir = _derive_base_dir(data)

            # 复制关联文件到 ZIP
            embedded = _embed_data_files(data, zf, sid)

            # 扫描消息中的图片，复制到 ZIP 的 images/ 目录
            img_map = _collect_images(data.get("messages", []), base_dir)
            for img_name, img_abs in img_map.items():
                if os.path.isfile(img_abs):
                    zf.write(img_abs, f"sessions/{sid}/images/{img_name}")

            # 重写 chat_history.json 中的文件路径为相对
            _rewrite_paths_export(data, embedded)

            sid_meta = _get_meta(sess_meta, sid)
            folder_name = _get_folder_name(sid_meta.get("folder_id", ""))
            meta_sessions.append({
                "session_id": sid,
                "folder_name": folder_name,
                "name": data.get("name", sid),
            })

            zf.writestr(
                f"sessions/{sid}/chat_history.json",
                json.dumps(data, ensure_ascii=False, indent=2),
            )

        meta = {
            "version": _EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(),
            "sessions": meta_sessions,
        }
        zf.writestr("export_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

    return len(meta_sessions) > 0


def import_sessions(vdc_path: str, output_dir: str = "") -> list[str]:
    """从 .vdc/.bdc 文件导入 sessions，返回新 session_id 列表。

    Args:
        vdc_path: 导出包路径
        output_dir: 书籍数据的输出目录（书籍包导入时使用）
    """
    with zipfile.ZipFile(vdc_path, "r") as zf:
        meta = json.loads(zf.read("export_meta.json"))
        is_book_export = "book_dir" in meta or any(
            n.startswith("book_dir/") for n in zf.namelist()
        )

        if is_book_export:
            return _import_book_package(zf, meta, output_dir)
        else:
            return _import_simple_sessions(zf, meta)


# ──────────────────────────────────────────────
#  书籍完整导出
# ──────────────────────────────────────────────

def export_book(folder_id: str, dest_path: str) -> bool:
    """将整本书（book_dir + 所有 sessions）打包为 .bdc ZIP。

    导出内容：
    - 完整 book_dir（book.json、chapters、notes、index、cache、pages）
    - 所有属于该文件夹的 session 对话
    - 路径全部转为相对路径，导入时再还原
    """
    if not folder_id:
        return False

    sess_meta = _load_meta()

    # 1. 找到该文件夹下所有 session
    folder_sessions = _get_folder_sessions(folder_id, sess_meta)
    if not folder_sessions:
        return False

    # 2. 从第一个 session 获取 book_dir
    first_data = _read_session_data(folder_sessions[0])
    if not first_data:
        return False

    book_dir = first_data.get("book_dir", "")
    book_dir_path = Path(book_dir) if book_dir else None
    if not book_dir_path or not book_dir_path.is_dir():
        return False

    book_dir_abs = str(book_dir_path.resolve())
    book_title = first_data.get("book_title", book_dir_path.name)

    meta_sessions = []

    # 打包时排除 pages/ 和 cache/ 目录（太大，chapters 已有完整文本）
    _SKIP_DIRS = {"pages", "cache"}

    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 3. 打包 book_dir（排除 pages/ 和 cache/）
        for file_path in book_dir_path.rglob("*"):
            if file_path.is_file():
                # 跳过 pages/ 和 cache/ 下的文件
                parts = file_path.relative_to(book_dir_path).parts
                if parts[0] in _SKIP_DIRS:
                    continue
                # book.json 单独处理（需要重写路径）
                if file_path.name == "book.json" and file_path.parent == book_dir_path:
                    continue
                rel = file_path.relative_to(book_dir_path)
                arc_name = f"book_dir/{rel}"
                zf.write(str(file_path), arc_name)

        # 3b. 重写 book.json 中的绝对路径为 BOOK_DIR/ 便携格式后写入 ZIP
        book_json_local = book_dir_path / "book.json"
        if book_json_local.is_file():
            book_raw = json.loads(book_json_local.read_text(encoding="utf-8"))
            book_portable = _rewrite_book_json_for_export(book_raw, book_dir_abs)
            zf.writestr(
                "book_dir/book.json",
                json.dumps(book_portable, ensure_ascii=False, indent=2),
            )

        # 4. 打包所有 sessions
        for sid in folder_sessions:
            data = _read_session_data(sid)
            if not data:
                continue

            # 复制关联文件（笔记等）
            embedded = _embed_data_files(data, zf, sid)

            # 扫描消息中的图片
            img_map = _collect_images(data.get("messages", []), book_dir_abs)
            for img_name, img_abs in img_map.items():
                if os.path.isfile(img_abs):
                    zf.write(img_abs, f"sessions/{sid}/images/{img_name}")

            # 重写 chat_history.json 中的绝对路径为相对
            _rewrite_book_paths_export(data, book_dir_abs, embedded)

            sid_meta = _get_meta(sess_meta, sid)
            meta_sessions.append({
                "session_id": sid,
                "folder_name": _get_folder_name(sid_meta.get("folder_id", "")),
                "name": data.get("name", sid),
            })

            zf.writestr(
                f"sessions/{sid}/chat_history.json",
                json.dumps(data, ensure_ascii=False, indent=2),
            )

        # 5. 写导出元数据
        meta = {
            "version": _BOOK_EXPORT_VERSION,
            "type": "book",
            "exported_at": datetime.now().isoformat(),
            "book_title": book_title,
            "book_dir_name": book_dir_path.name,
            "sessions": meta_sessions,
        }
        zf.writestr("export_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

    return len(meta_sessions) > 0


# ──────────────────────────────────────────────
#  书籍完整导入
# ──────────────────────────────────────────────

def _import_book_package(zf: zipfile.ZipFile, meta: dict,
                         output_dir: str) -> list[str]:
    """导入书籍包：解压 book_dir + sessions，重写所有路径。"""
    new_ids = []
    all_meta = _load_meta()

    # 1. 确定输出目录
    if not output_dir:
        output_dir = str(Path.home() / ".Book-Distiller" / "output")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 2. 解压 book_dir 到输出目录
    book_dir_name = meta.get("book_dir_name", "imported_book")
    target_book_dir = output_path / book_dir_name
    target_book_dir.mkdir(parents=True, exist_ok=True)

    for name in zf.namelist():
        if name.startswith("book_dir/"):
            rel = name[len("book_dir/"):]
            if not rel or rel.endswith("/"):
                continue
            target = target_book_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(str(target), "wb") as dst:
                dst.write(src.read())

    new_book_dir_abs = str(target_book_dir.resolve())

    # 3. 重写 book.json 中的路径
    book_json = target_book_dir / "book.json"
    if book_json.is_file():
        _rewrite_book_json_paths(book_json, new_book_dir_abs)

    # 4. 解压 sessions 并重写路径
    meta_sessions = meta.get("sessions", [])

    base_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for idx, entry in enumerate(meta_sessions, 1):
        old_sid = entry["session_id"]
        prefix = f"sessions/{old_sid}/"

        names = [n for n in zf.namelist() if n.startswith(prefix)]
        if not names:
            continue

        new_sid = _unique_sid(f"{base_ts}_{idx:03d}")
        new_dir = _SESSIONS_DIR / new_sid
        new_dir.mkdir(parents=True, exist_ok=True)

        for name in names:
            rel = name[len(prefix):]
            if not rel:
                continue
            target = new_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(str(target), "wb") as dst:
                dst.write(src.read())

        # 重写 session 路径为新绝对路径
        hfile = new_dir / "chat_history.json"
        if not hfile.is_file():
            continue

        data = json.loads(hfile.read_text(encoding="utf-8"))
        _rewrite_book_paths_import(data, new_book_dir_abs, str(new_dir))

        # 恢复文件夹分组
        folder_name = entry.get("folder_name", meta.get("book_title", ""))
        if folder_name:
            fid = _ensure_folder(folder_name)
            _get_meta(all_meta, new_sid)["folder_id"] = fid

        hfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        new_ids.append(new_sid)

    if new_ids:
        _save_meta(all_meta)

    return new_ids


# ──────────────────────────────────────────────
#  旧版简单导入
# ──────────────────────────────────────────────

def _import_simple_sessions(zf: zipfile.ZipFile, meta: dict) -> list[str]:
    """导入旧版 .vdc 格式（仅 sessions，无 book_dir）。"""
    new_ids = []
    all_meta = _load_meta()

    base_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for idx, entry in enumerate(meta.get("sessions", []), 1):
        old_sid = entry["session_id"]
        prefix = f"sessions/{old_sid}/"
        names = [n for n in zf.namelist() if n.startswith(prefix)]
        if not names:
            continue

        new_sid = _unique_sid(f"{base_ts}_{idx:03d}")
        new_dir = _SESSIONS_DIR / new_sid
        new_dir.mkdir(parents=True, exist_ok=True)

        for name in names:
            rel = name[len(prefix):]
            if not rel:
                continue
            target = new_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(str(target), "wb") as dst:
                dst.write(src.read())

        hfile = new_dir / "chat_history.json"
        if not hfile.is_file():
            continue

        data = json.loads(hfile.read_text(encoding="utf-8"))
        _rewrite_paths_import(data, str(new_dir))

        folder_name = entry.get("folder_name", "")
        if folder_name:
            fid = _ensure_folder(folder_name)
            _get_meta(all_meta, new_sid)["folder_id"] = fid

        hfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        new_ids.append(new_sid)

    if new_ids:
        _save_meta(all_meta)

    return new_ids


# ──────────────────────────────────────────────
#  路径重写：导出
# ──────────────────────────────────────────────

# session 中包含 BOOK_DIR/ 路径的字段白名单
_SESSION_PATH_KEYS = [
    "book_dir", "book_json_path", "notes_path", "slides_path",
    "transcript_path",
]


def _rewrite_book_json_for_export(book_data: dict, book_dir_abs: str) -> dict:
    """返回 book_data 的深拷贝，将绝对路径转为 BOOK_DIR/ 便携格式。

    处理的字段：
    - paths.book_dir, paths.pages_path
    - index.chunks_path, index.stats_path
    - chapters[].text_path, chapters[].note_path
    - memory.overview_path, memory.knowledge_map_path
    - source_pdf（清空，新机器无此文件）
    """
    data = copy.deepcopy(book_data)

    # paths
    paths = data.get("paths", {})
    for key in ("book_dir", "pages_path"):
        _abs_to_book_dir(paths, key, book_dir_abs)

    # index
    index = data.get("index", {})
    for key in ("chunks_path", "stats_path"):
        _abs_to_book_dir(index, key, book_dir_abs)

    # chapters
    for chapter in data.get("chapters", []):
        for key in ("text_path", "note_path"):
            _abs_to_book_dir(chapter, key, book_dir_abs)

    # memory
    memory = data.get("memory", {})
    for key in ("overview_path", "knowledge_map_path"):
        _abs_to_book_dir(memory, key, book_dir_abs)

    # source_pdf 清空
    data["source_pdf"] = ""

    return data


def _abs_to_book_dir(container: dict, key: str, book_dir_abs: str):
    """将容器中单个绝对路径转为 BOOK_DIR/ 前缀的相对路径。"""
    val = container.get(key, "")
    if val and os.path.isabs(val):
        rel = _to_book_rel(val, book_dir_abs)
        if rel == "." or rel == "":
            # 路径就是 book_dir 本身
            container[key] = "BOOK_DIR/"
        elif rel:
            container[key] = f"BOOK_DIR/{rel}"


def _rewrite_book_paths_export(data: dict, book_dir_abs: str, embedded: dict):
    """书籍导出时将 chat_history.json 中的绝对路径改为 BOOK_DIR/ 相对路径。

    注意：不使用 embedded 覆盖 notes_path/slides_path，因为这些路径应指向
    book_dir 内的文件（如 notes/ch01.md），而非 session 内嵌的副本。
    """
    # 重写 book 相关绝对路径为 BOOK_DIR/ 前缀
    book_keys = [
        "book_dir", "book_json_path", "notes_path", "slides_path",
        "transcript_path",
    ]
    for key in book_keys:
        val = data.get(key, "")
        if val and os.path.isabs(val):
            rel = _to_book_rel(val, book_dir_abs)
            if rel == "." or rel == "":
                data[key] = "BOOK_DIR/"
            elif rel:
                data[key] = f"BOOK_DIR/{rel}"

    # 重写 chapter_text_paths 数组
    ct_paths = data.get("chapter_text_paths", [])
    if isinstance(ct_paths, list):
        rewritten = []
        for p in ct_paths:
            rel = _to_book_rel(p, book_dir_abs) if isinstance(p, str) else ""
            if rel == "." or rel == "":
                rewritten.append("BOOK_DIR/")
            elif rel:
                rewritten.append(f"BOOK_DIR/{rel}")
            else:
                rewritten.append(p)
        data["chapter_text_paths"] = rewritten


def _rewrite_book_json_paths(book_json_path: Path, new_book_dir_abs: str):
    """导入后重写 book.json 中的 BOOK_DIR/ 路径字段为新绝对路径。"""
    data = json.loads(book_json_path.read_text(encoding="utf-8"))
    _rewrite_book_json_paths_targeted(data, new_book_dir_abs)
    book_json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rewrite_book_json_paths_targeted(data: dict, new_book_dir_abs: str):
    """定向重写 book.json 中已知路径字段（不递归处理消息内容）。

    支持 v3 (BOOK_DIR/ 前缀) 和 v2 (旧绝对路径) 两种格式。
    """
    # v2 兼容：从 paths.book_dir 推导旧 book_dir 前缀
    old_book_dir = data.get("paths", {}).get("book_dir", "")
    if old_book_dir and not old_book_dir.startswith("BOOK_DIR/"):
        old_book_dir = str(Path(old_book_dir).resolve())
    else:
        old_book_dir = ""

    # paths 字典
    paths = data.get("paths", {})
    for key in ("book_dir", "pages_path"):
        _convert_path_field(paths, key, new_book_dir_abs, old_book_dir)

    # index 字典
    index = data.get("index", {})
    for key in ("chunks_path", "stats_path"):
        _convert_path_field(index, key, new_book_dir_abs, old_book_dir)

    # chapters 列表
    for chapter in data.get("chapters", []):
        for key in ("text_path", "note_path"):
            _convert_path_field(chapter, key, new_book_dir_abs, old_book_dir)

    # memory 字典
    memory = data.get("memory", {})
    for key in ("overview_path", "knowledge_map_path"):
        _convert_path_field(memory, key, new_book_dir_abs, old_book_dir)

    # source_pdf: 新机器上无此文件，清空避免指向无效路径
    source_pdf = data.get("source_pdf", "")
    if source_pdf and not Path(source_pdf).is_file():
        data["source_pdf"] = ""


def _convert_path_field(container: dict, key: str,
                        new_book_dir_abs: str, old_book_dir: str = ""):
    """将容器中单个路径字段转为新绝对路径。

    支持两种格式：
    - v3: BOOK_DIR/ 前缀的相对路径
    - v2: 旧机器的绝对路径（通过 old_book_dir 推导相对部分）
    """
    val = container.get(key, "")
    if not val or not isinstance(val, str):
        return
    if val == "BOOK_DIR/":
        # 路径就是 book_dir 本身
        container[key] = new_book_dir_abs
    elif val.startswith("BOOK_DIR/"):
        # v3 格式
        rel = val[len("BOOK_DIR/"):]
        container[key] = os.path.join(new_book_dir_abs, rel.replace("/", os.sep))
    elif old_book_dir and os.path.isabs(val):
        # v2 兼容：用旧 book_dir 前缀推导相对路径
        try:
            rel = str(Path(val).resolve().relative_to(Path(old_book_dir)))
            if rel == ".":
                container[key] = new_book_dir_abs
            else:
                container[key] = os.path.join(new_book_dir_abs, str(rel))
        except (ValueError, OSError):
            pass  # 路径不在旧 book_dir 下，保留原值


# ──────────────────────────────────────────────
#  路径重写：导入
# ──────────────────────────────────────────────

def _rewrite_book_paths_import(data: dict, new_book_dir_abs: str,
                               new_session_dir: str):
    """书籍导入时将 BOOK_DIR/ 路径重写为新机器的绝对路径。

    只处理已知路径字段（白名单），不递归处理消息内容。
    同时兼容 v2 导出：notes_path/slides_path 可能是裸文件名。
    """
    for key in _SESSION_PATH_KEYS:
        val = data.get(key, "")
        if not val or not isinstance(val, str):
            continue
        if val == "BOOK_DIR/":
            # 路径就是 book_dir 本身
            data[key] = new_book_dir_abs
        elif val.startswith("BOOK_DIR/"):
            # v3 格式：直接替换前缀
            rel = val[len("BOOK_DIR/"):]
            data[key] = os.path.join(new_book_dir_abs, rel.replace("/", os.sep))
        elif not os.path.isabs(val):
            # v2 兼容：裸文件名（如 notes.md, data.json）解析到 session 目录
            data[key] = os.path.join(new_session_dir, val)

    # 重写 chapter_text_paths 数组
    ct_paths = data.get("chapter_text_paths", [])
    if isinstance(ct_paths, list):
        data["chapter_text_paths"] = [
            os.path.join(new_book_dir_abs, p[len("BOOK_DIR/"):].replace("/", os.sep))
            if isinstance(p, str) and p.startswith("BOOK_DIR/") else p
            for p in ct_paths
        ]


def _rewrite_paths_import(data: dict, new_session_dir: str):
    """旧版导入时将相对路径改为新绝对路径"""
    for key in ("notes_path", "slides_path", "transcript_path"):
        rel = data.get(key, "")
        if not rel:
            continue
        if not os.path.isabs(rel):
            data[key] = os.path.join(new_session_dir, rel)


# ──────────────────────────────────────────────
#  通用辅助函数
# ──────────────────────────────────────────────

def _to_book_rel(abs_path: str, book_dir_abs: str) -> str:
    """将绝对路径转为相对于 book_dir 的相对路径（使用 / 分隔）。"""
    try:
        rel = Path(abs_path).resolve().relative_to(Path(book_dir_abs).resolve())
        return str(rel).replace(os.sep, "/")
    except (ValueError, OSError):
        return ""


def _read_session_data(sid: str) -> dict | None:
    """读取 session 的 chat_history.json。"""
    hfile = _SESSIONS_DIR / sid / "chat_history.json"
    if not hfile.is_file():
        return None
    try:
        return json.loads(hfile.read_text(encoding="utf-8"))
    except Exception:
        return None


def _get_folder_sessions(folder_id: str, sess_meta: dict) -> list[str]:
    """获取属于指定文件夹的所有 session ID。"""
    result = []
    for sid, info in sess_meta.items():
        if info.get("folder_id") == folder_id:
            result.append(sid)
    return result


def _derive_base_dir(data: dict) -> str:
    """从 notes_path / slides_path 推导项目根目录"""
    for key in ("notes_path", "slides_path", "transcript_path"):
        p = data.get(key, "")
        if p and os.path.exists(p):
            parent = str(Path(p).parent)
            if Path(p).suffix == ".md":
                return str(Path(parent).parent)
            return parent
    return ""


def _embed_data_files(data: dict, zf: zipfile.ZipFile, sid: str) -> dict:
    """将关联文件复制到 ZIP，返回 {字段: 相对路径}"""
    embedded = {}

    notes_path = data.get("notes_path", "")
    if notes_path and os.path.isfile(notes_path):
        zf.write(notes_path, f"sessions/{sid}/notes.md")
        embedded["notes_path"] = "notes.md"

    slides_path = data.get("slides_path", "")
    if slides_path and os.path.isfile(slides_path):
        zf.write(slides_path, f"sessions/{sid}/data.json")
        embedded["slides_path"] = "data.json"

    transcript_path = data.get("transcript_path", "")
    if transcript_path and os.path.isfile(transcript_path):
        if transcript_path != slides_path:
            zf.write(transcript_path, f"sessions/{sid}/transcript.json")
            embedded["transcript_path"] = "transcript.json"
        else:
            embedded["transcript_path"] = "data.json"

    return embedded


def _collect_images(messages: list[dict], base_dir: str) -> dict:
    """扫描消息文本中的图片引用，返回 {文件名: 绝对路径}"""
    result = {}
    if not base_dir:
        return result

    for msg in messages:
        content = msg.get("content", "")
        if not content:
            continue

        for _, src in _MD_IMG_SRC_RE.findall(content):
            if src.startswith(("http://", "https://", "data:", "_formula_")):
                continue
            abs_path = _find_image_in_dir(src, base_dir)
            if abs_path:
                result[os.path.basename(abs_path)] = abs_path

        for m in _FILE_URL_RE.finditer(content):
            local = m.group(1)
            if os.path.isfile(local):
                result[os.path.basename(local)] = local

    return result


def _find_image_in_dir(src: str, base_dir: str) -> str:
    """在 base_dir 的 frames/、key_frames/、根目录中搜索图片"""
    candidates = [src]
    if ":" in src:
        candidates.append(src.replace(":", "_", 1))

    for name in candidates:
        for subdir in ("frames", "key_frames", ""):
            full = os.path.join(base_dir, subdir, name) if subdir else os.path.join(base_dir, name)
            if os.path.isfile(full):
                return full
    return ""


def _rewrite_paths_export(data: dict, embedded: dict):
    """导出时将绝对路径改为相对"""
    for key, rel in embedded.items():
        data[key] = rel


def _get_folder_name(folder_id: str) -> str:
    """根据 folder_id 查找文件夹名称"""
    if not folder_id:
        return ""
    folders = load_folders()
    for f in folders:
        if f["id"] == folder_id:
            return f["name"]
    return ""


def _ensure_folder(folder_name: str) -> str:
    """确保目标机器上存在同名文件夹，不存在则创建，返回 folder_id"""
    if not folder_name:
        return ""
    folders = load_folders()
    for f in folders:
        if f["name"] == folder_name:
            return f["id"]
    fid = f"f{len(folders) + 1}_{int(datetime.now().timestamp())}"
    folders.append({"id": fid, "name": folder_name, "order": len(folders)})
    save_folders(folders)
    return fid


def _unique_sid(base: str = "") -> str:
    """生成一个不与现有 session 冲突的 session ID。

    base 为空时使用时间戳；冲突时追加递增序号而非无限 _1。
    """
    sid = base or datetime.now().strftime("%Y%m%d_%H%M%S")
    d = _SESSIONS_DIR / sid
    if not d.exists():
        return sid
    i = 1
    while True:
        sid = f"{base or datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}"
        d = _SESSIONS_DIR / sid
        if not d.exists():
            return sid
        i += 1
