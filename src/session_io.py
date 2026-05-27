"""
Video-Distiller 对话导出/导入模块
- 导出为 .vdc (ZIP) 归档，包含 session 数据 + 关联文件 + 图片
- 导入时自动重定向路径，在新机器上可直接使用
- 导入后 session 自包含：图片在 session_dir/images/ 下，
  MessageBubble._find_image 搜索 images/ 子目录即可找到
"""

import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

from src.chat import _SESSIONS_DIR, load_folders, save_folders, _load_meta, _save_meta, _get_meta

_EXPORT_VERSION = 1

# 匹配消息中的时间戳图片文件名 (XX_XX_*.jpg/png)
_TS_IMG_RE = re.compile(
    r'(?<!!\[)(?:\b|\()'
    r'(\d{1,2}[:_]\d{2}(?:_\w+)?\.(?:jpg|jpeg|png))'
    r'(?:\b|\))(?!\))'
)
# 匹配 ![alt](src) 中的 src
_MD_IMG_SRC_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
# 匹配 file:/// 开头的路径
_FILE_URL_RE = re.compile(r'file:///(/[^\s\)]+)')


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

            # 消息文本保持原样，不重写图片路径
            # 导入后 _find_image 会搜索 session_dir/images/ 即可找到

            # 重写 chat_history.json 中的文件路径为相对
            _rewrite_paths_export(data, embedded)

            # folder_id 从统一的 meta 读取
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


def import_sessions(vdc_path: str) -> list[str]:
    """从 .vdc 文件导入 sessions，返回新 session_id 列表"""
    new_ids = []
    all_meta = _load_meta()

    with zipfile.ZipFile(vdc_path, "r") as zf:
        meta = json.loads(zf.read("export_meta.json"))
        meta_sessions = meta.get("sessions", [])

        for entry in meta_sessions:
            old_sid = entry["session_id"]
            prefix = f"sessions/{old_sid}/"

            names = [n for n in zf.namelist() if n.startswith(prefix)]
            if not names:
                continue

            # 创建新 session 目录（避免 ID 冲突）
            new_sid = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_dir = _SESSIONS_DIR / new_sid
            while new_dir.exists():
                new_sid += "_1"
                new_dir = _SESSIONS_DIR / new_sid

            new_dir.mkdir(parents=True, exist_ok=True)

            # 解压所有文件
            for name in names:
                rel = name[len(prefix):]
                if not rel:
                    continue
                target = new_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())

            # 重写文件路径为新绝对路径
            hfile = new_dir / "chat_history.json"
            if not hfile.is_file():
                continue

            data = json.loads(hfile.read_text(encoding="utf-8"))
            _rewrite_paths_import(data, str(new_dir))

            # 恢复文件夹分组到统一的 session_meta.json
            folder_name = entry.get("folder_name", "")
            if folder_name:
                fid = _ensure_folder(folder_name)
                _get_meta(all_meta, new_sid)["folder_id"] = fid

            hfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            new_ids.append(new_sid)

    if new_ids:
        _save_meta(all_meta)

    return new_ids


# ─── 导出辅助 ───

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

        for m in _TS_IMG_RE.finditer(content):
            fname = m.group(1).replace(":", "_", 1)
            if fname in result:
                continue
            abs_path = _find_image_in_dir(fname, base_dir)
            if abs_path:
                result[os.path.basename(abs_path)] = abs_path

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


# ─── 导入辅助 ───

def _rewrite_paths_import(data: dict, new_session_dir: str):
    """导入时将相对路径改为新绝对路径"""
    for key in ("notes_path", "slides_path", "transcript_path"):
        rel = data.get(key, "")
        if not rel:
            continue
        if not os.path.isabs(rel):
            data[key] = os.path.join(new_session_dir, rel)


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
