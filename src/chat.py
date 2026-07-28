"""
Book-Distiller AI 对话模块
- 每个对话是独立 session（按时间戳命名）
- session 持久化到 ~/.Book-Distiller/sessions/{session_id}/
- 后续绑定 book.json / chapter notes / RAG index
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import (
    RICH_TEXT_FORMATTING_PROMPT,
    USER_DATA_DIR,
    get_book_repo_dir,
)
from src.paths import (
    load_book,
    relativize_session_paths,
    resolve_session_paths,
    save_book,
    workspace_dir,
)

_SESSIONS_DIR = get_book_repo_dir()
# 可同步的全局索引也放在 sessions 仓库内。旧版本把它们放在
# USER_DATA_DIR 根目录；加载时仍会识别并自动迁移。
_FOLDERS_FILE = _SESSIONS_DIR / "folder.json"
_META_FILE = _SESSIONS_DIR / "session_meta.json"
_LEGACY_FOLDERS_FILES = (
    USER_DATA_DIR / "folders.json",
    _SESSIONS_DIR / "folders.json",
)
_LEGACY_META_FILES = (USER_DATA_DIR / "session_meta.json",)


def _load_json_with_migration(current: Path, legacy_files: tuple[Path, ...],
                              default):
    """Read the repository metadata, falling back to old locations once.

    A successfully read legacy file is copied to the new location.  The old
    file is deliberately retained so downgrading the application is safe.
    """
    candidates = (current, *legacy_files)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if path != current:
            current.parent.mkdir(parents=True, exist_ok=True)
            current.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return data
    return default


def _load_meta() -> dict:
    """加载统一的 session 元数据 {sid: {folder_id, order, hidden}}"""
    data = _load_json_with_migration(_META_FILE, _LEGACY_META_FILES, {})
    return data if isinstance(data, dict) else {}


def _save_meta(meta: dict):
    _META_FILE.parent.mkdir(parents=True, exist_ok=True)
    _META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_meta(meta: dict, sid: str) -> dict:
    """获取单个 session 的元数据，带默认值"""
    m = meta.setdefault(sid, {})
    m.setdefault("folder_id", "")
    m.setdefault("order", 0)
    m.setdefault("hidden", False)
    m.setdefault("favorite", False)
    return m


def load_folders() -> list[dict]:
    data = _load_json_with_migration(
        _FOLDERS_FILE, _LEGACY_FOLDERS_FILES, {"folders": []},
    )
    if isinstance(data, dict) and isinstance(data.get("folders"), list):
        return data["folders"]
    return []


def save_folders(folders: list[dict]):
    _FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FOLDERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"folders": folders}, f, ensure_ascii=False, indent=2)


CHAT_SYSTEM_PROMPT = (
    "你是一位读完整本书、并且擅长教学的学习导师。\n"
    "以下是书籍的学习笔记和结构化资料，作为你的知识基础：\n\n"
    "--- 学习笔记 ---\n{notes}\n\n"
    "--- 结构化资料 ---\n{slides}\n\n"
    "你的任务是：\n"
    "1. 回答学生关于书籍、本章和相关概念的问题\n"
    "2. 用通俗但不浅薄的语言解释复杂内容\n"
    "3. 帮助学生建立章节、概念和原文证据之间的联系\n"
    "4. 指出容易忽略的重要细节\n"
    "5. 默认给出章节/页码或资料来源，证据不足时不要编造"
)


def _now_message_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _message(role: str, content: str, **extra) -> dict:
    data = {
        "role": role,
        "content": content,
        "created_at": _now_message_time(),
    }
    data.update(extra)
    return data


class ChatSession:
    """管理单个对话 session"""

    def __init__(self, session_dir: str, provider_config: dict):
        self.session_dir = session_dir
        self.history_path = os.path.join(session_dir, "chat_history.json")
        self.provider = provider_config
        self.base_url = provider_config.get("base_url", "").rstrip("/")
        self.api_key = provider_config.get("api_key", "")
        self.model = provider_config.get("model", "")
        self.system_prompt = ""
        self.messages: list[dict] = []
        # 元数据
        self.name = ""
        self.created_at = ""
        self.folder_id = ""
        self.slides_path = ""
        self.transcript_path = ""
        self.notes_path = ""
        self.book_id = ""
        self.book_title = ""
        self.chapter_id = ""
        self.chapter_title = ""
        self.book_dir = ""
        self.book_json_path = ""
        self.index_version = ""
        self.chapter_text_paths: list[str] = []  # 章节原文 md 路径列表

    def initialize(self, notes_path: str = "", data_path: str = "") -> bool:
        """加蒸馏结果构建 system prompt，返回是否成功"""
        notes = self._read_file(notes_path)
        slides = self._summarize_slides(data_path)

        if not notes and not slides:
            return False

        self.notes_path = notes_path
        self.slides_path = data_path
        self.system_prompt = CHAT_SYSTEM_PROMPT.format(
            notes=notes or "(未找到蒸馏笔记)",
            slides=slides or "(未找到幻灯片描述)",
        )
        self._load_history()
        return True

    def update_files(self, notes_path: str = "", data_path: str = ""):
        """更新关联文件并重建 system prompt"""
        self.notes_path = notes_path
        self.slides_path = data_path

        notes = self._read_file(notes_path)
        slides = self._summarize_slides(data_path)

        if notes or slides:
            self.system_prompt = CHAT_SYSTEM_PROMPT.format(
                notes=notes or "(未找到蒸馏笔记)",
                slides=slides or "(未找到幻灯片描述)",
            )

        # 如果有笔记且还没有首条消息，注入笔记作为第一条
        if notes and not self.messages:
            self.messages.append(_message("assistant", notes))

        # 更新名称
        if notes_path:
            stem = Path(notes_path).stem
            self.name = stem

        self._save_history()

    def chat(self, user_message: str) -> str:
        if not self.system_prompt:
            return "请先配置学习资料（点击齿轮按钮），然后再开始对话。"

        user_entry = _message("user", user_message)
        api_messages, hits = self._build_api_messages(user_message, self.messages + [user_entry])
        if hits:
            user_entry["retrieval_hits"] = hits
        self.messages.append(user_entry)

        reply = self._call_provider(api_messages)
        self.messages.append(_message("assistant", reply))
        self._save_history()
        return reply

    def add_user_message(self, user_message: str) -> dict:
        """Append and persist a user message before the assistant reply starts."""
        user_entry = _message("user", user_message)
        self.messages.append(user_entry)
        self._save_history()
        return user_entry

    def reply_to_last_user(self) -> str:
        """Generate an assistant reply for an already persisted user message."""
        if not self.system_prompt:
            return "请先配置学习资料（点击齿轮按钮），然后再开始对话。"
        last_user = next((m for m in reversed(self.messages) if m.get("role") == "user"), {})
        user_message = last_user.get("content", "")
        api_messages, hits = self._build_api_messages(user_message, self.messages)
        if hits and last_user:
            last_user["retrieval_hits"] = hits
        reply = self._call_provider(api_messages)
        self.messages.append(_message("assistant", reply))
        self._save_history()
        return reply

    def regenerate(self) -> str:
        """重新生成最后一条 AI 回复"""
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages.pop()
        last_user = next((m for m in reversed(self.messages) if m.get("role") == "user"), {})
        api_messages, hits = self._build_api_messages(last_user.get("content", ""), self.messages)
        if hits and last_user:
            last_user["retrieval_hits"] = hits
        reply = self._call_provider(api_messages)
        self.messages.append(_message("assistant", reply))
        self._save_history()
        return reply

    def edit_and_regenerate(self, msg_index: int, new_text: str) -> str:
        """编辑用户消息，丢弃后续，重新生成"""
        self.messages[msg_index]["content"] = new_text
        self.messages[msg_index]["created_at"] = _now_message_time()
        self.messages = self.messages[:msg_index + 1]
        api_messages, hits = self._build_api_messages(new_text, self.messages)
        if hits:
            self.messages[msg_index]["retrieval_hits"] = hits
        reply = self._call_provider(api_messages)
        self.messages.append(_message("assistant", reply))
        self._save_history()
        return reply

    def delete_message(self, msg_index: int):
        """删除消息；user 消息会连带删除下一条 assistant"""
        removed = 0
        if 0 <= msg_index < len(self.messages):
            self.messages.pop(msg_index)
            removed += 1
            if (msg_index < len(self.messages)
                    and self.messages[msg_index]["role"] == "assistant"):
                self.messages.pop(msg_index)
                removed += 1
        self._save_history()
        return removed

    def clear_history(self):
        self.messages.clear()
        self._save_history()

    # ─── Provider ───

    def _build_api_messages(self, query: str, history: list[dict]) -> tuple[list[dict], list[dict]]:
        system_prompt = self.system_prompt
        if "富文本排版规范" not in system_prompt or "Markdown 表格" not in system_prompt:
            system_prompt = f"{system_prompt}\n{RICH_TEXT_FORMATTING_PROMPT}"
        hits: list[dict] = []
        if self.book_json_path and os.path.exists(self.book_json_path) and query:
            try:
                from src.context_builder import build_context
                context = build_context(self.book_json_path, query, top_k=8, max_chars=9000)
                hits = context.get("hits", [])
                if hits:
                    system_prompt = (
                        f"{system_prompt}\n\n"
                        "## 本轮检索到的原文证据\n"
                        "请优先依据下面证据回答；回答中尽量标注章节、页码或 chunk id。"
                        "如果证据不足，请明确说明。\n\n"
                        f"{context.get('context', '')}"
                    )
            except Exception as exc:
                system_prompt = (
                    f"{system_prompt}\n\n"
                    f"## 检索状态\n本轮检索失败：{exc}。请说明证据不足，不要编造出处。"
                )
        api_messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-40:]:
            if msg.get("role") in {"user", "assistant"}:
                api_messages.append({
                    "role": msg.get("role", ""),
                    "content": msg.get("content", ""),
                })
        return api_messages, hits

    def _call_provider(self, messages: list[dict]) -> str:
        import requests

        if not self.base_url or not self.api_key:
            raise ValueError("请先在 Settings 中配置 AI Provider 的 URL 和 API Key")
        api_key = self.api_key.strip()
        if "\n" in api_key or "\r" in api_key or '"' in api_key or "model" in api_key.lower():
            raise ValueError("API Key 配置不正确：请只填写单行 Key，不要粘贴 JSON 配置片段")

        url = self.base_url + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 8192,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    # ─── 持久化 ───

    def _save_history(self):
        os.makedirs(self.session_dir, exist_ok=True)
        data = {
            "name": self.name,
            "created_at": self.created_at,
            "folder_id": self.folder_id,
            "slides_path": self.slides_path,
            "transcript_path": self.transcript_path,
            "notes_path": self.notes_path,
            "book_id": self.book_id,
            "book_title": self.book_title,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "book_dir": self.book_dir,
            "book_json_path": self.book_json_path,
            "index_version": self.index_version,
            "chapter_text_paths": self.chapter_text_paths,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
        }
        # 写盘前把 session 内的绝对路径转成相对（相对 session 目录）
        out = relativize_session_paths(data, Path(self.session_dir))
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    def _load_history(self):
        if os.path.exists(self.history_path):
            try:
                data = json.loads(Path(self.history_path).read_text(encoding="utf-8"))
                # 加载时把相对路径 resolve 成绝对（内存里保持绝对）
                resolve_session_paths(data, Path(self.history_path).parent)
                self.messages = data.get("messages", [])
                self.name = data.get("name", self.name)
                self.created_at = data.get("created_at", "")
                self.folder_id = data.get("folder_id", "")
                self.slides_path = data.get("slides_path", "")
                self.transcript_path = data.get("transcript_path", "")
                self.notes_path = data.get("notes_path", "")
                self.book_id = data.get("book_id", "")
                self.book_title = data.get("book_title", "")
                self.chapter_id = data.get("chapter_id", "")
                self.chapter_title = data.get("chapter_title", "")
                self.book_dir = data.get("book_dir", "")
                self.book_json_path = data.get("book_json_path", "")
                self.index_version = data.get("index_version", "")
                self.chapter_text_paths = data.get("chapter_text_paths", [])
                chapter_ids = data.get("chapter_ids", [])
                if data.get("system_prompt"):
                    self.system_prompt = data["system_prompt"]
                if chapter_ids:
                    self._repair_group_first_message(chapter_ids, data.get("chapter_title", ""))
            except Exception:
                self.messages = []

        # 有笔记但消息为空时，注入笔记作为首条助手消息
        if self.notes_path and not self.messages:
            notes = self._read_file(self.notes_path)
            if notes:
                self.messages.append(_message("assistant", notes))

    # ─── 工具 ───

    @staticmethod
    def _read_file(path: str) -> str:
        if path and os.path.exists(path):
            try:
                return Path(path).read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return ""

    def _repair_group_first_message(self, chapter_ids: list[str], group_title: str = ""):
        """Refresh old grouped-session first messages that duplicated one shared note."""
        if not self.messages or self.messages[0].get("role") != "assistant":
            return
        if len(chapter_ids) <= 1:
            return
        if not self.book_json_path or not Path(self.book_json_path).is_file():
            return
        try:
            book = load_book(self.book_json_path)
            chapters = book.get("chapters") or []
            id_set = set(chapter_ids)
            group_chapters = [c for c in chapters if c.get("chapter_id", "") in id_set]
            if len(group_chapters) <= 1:
                return
            note_paths = {
                c.get("note_path", "")
                for c in group_chapters
                if c.get("note_path", "")
            }
            if len(note_paths) != 1:
                return
            note = _read_note(next(iter(note_paths)))
            current = self.messages[0].get("content", "")
            if not note or current.count(note) <= 1:
                return
            group = {
                "title": group_title or self.chapter_title or group_chapters[0].get("title", ""),
                "chapters": group_chapters,
            }
            rebuilt = _group_first_message(
                group,
                book.get("title", "") or self.book_title,
                book.get("index") or {},
                book.get("title", "") or self.book_title,
            )
            if rebuilt and rebuilt != current:
                self.messages[0]["content"] = rebuilt
                self._save_history()
        except Exception:
            return

    @staticmethod
    def _summarize_slides(path: str) -> str:
        if not path or not os.path.exists(path):
            return ""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            # 兼容统一 JSON（data 包含 slides 键）和旧 slides.json
            slides = data.get("slides", [])
            lines = []
            for s in slides:
                ts = s.get("timestamp", "")
                title = s.get("title", "")
                text = s.get("text", "")[:200]
                diagrams = s.get("diagrams", "")
                line = f"[{ts}] {title}"
                if text:
                    line += f" — {text}"
                if diagrams and diagrams != "无":
                    line += f" | 图表: {diagrams[:100]}"
                lines.append(line)

            # 如果统一 JSON 中还有 segments，追加转录摘要
            segments = data.get("segments", [])
            if segments and not slides:
                lines.append("\n## 语音转录摘要")
                for seg in segments[:10]:
                    start_mmss = seg.get("start_mmss", "")
                    text = seg.get("text", "")[:100]
                    if start_mmss and text:
                        lines.append(f"[{start_mmss}] {text}")

            return "\n".join(lines)
        except Exception:
            return ""


# ─── Session 管理 ───

def create_session(project_dir: str, video_name: str = "",
                   notes_path: str = "", provider_config: Optional[dict] = None) -> ChatSession:
    """创建新的对话 session，返回 ChatSession"""
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    display = now.strftime("%m-%d %H:%M")

    sessions_dir = str(_SESSIONS_DIR)
    session_dir = os.path.join(sessions_dir, ts)
    os.makedirs(session_dir, exist_ok=True)

    cfg = provider_config or {}
    session = ChatSession(session_dir, cfg)
    session.created_at = now.strftime("%Y-%m-%d %H:%M:%S")

    # 自动查找关联文件
    if not notes_path:
        notes_dir = os.path.join(project_dir, "notes")
        if os.path.isdir(notes_dir):
            for f in sorted(os.listdir(notes_dir), reverse=True):
                if f.endswith(".md"):
                    notes_path = os.path.join(notes_dir, f)
                    break

    # 查找统一 JSON（包含 slides 或 segments 的 JSON 文件）
    data_path = ""
    unified_candidates = [f for f in os.listdir(project_dir) if f.endswith(".json") and not f.startswith(".")]
    for uc in unified_candidates:
        p = os.path.join(project_dir, uc)
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if "segments" in data or "slides" in data:
                data_path = p
                break
        except Exception:
            continue

    # 回退旧格式 slides.json
    if not data_path:
        legacy = os.path.join(project_dir, "slides.json")
        if os.path.exists(legacy):
            data_path = legacy

    session.initialize(notes_path, data_path)

    # 名称：有笔记用笔记名，有视频名用视频名，否则用时间
    if session.notes_path:
        session.name = Path(session.notes_path).stem
    elif video_name:
        session.name = f"{video_name} {display}"
    else:
        session.name = display

    session._save_history()
    return session


def create_empty_session(output_dir: str, provider_config: Optional[dict] = None) -> ChatSession:
    """创建空白对话 session，不自动查找文件"""
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    display = now.strftime("%m-%d %H:%M")

    sessions_dir = str(_SESSIONS_DIR)
    session_dir = os.path.join(sessions_dir, ts)
    os.makedirs(session_dir, exist_ok=True)

    cfg = provider_config or {}
    session = ChatSession(session_dir, cfg)
    session.name = display
    session.created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    session._save_history()
    return session


def _session_id_for_book(book_id: str, suffix: str) -> str:
    raw = f"{book_id}_{suffix}"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)
    if len(safe) <= 120:
        return safe
    # book_id 太长时，用哈希缩短前缀，保留后缀可读性
    import hashlib
    short = hashlib.sha256(book_id.encode("utf-8", errors="replace")).hexdigest()[:16]
    safe_suffix = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in suffix)
    return f"b_{short}_{safe_suffix}"[:120]


def _ensure_book_folder(book_id: str, title: str) -> str:
    folder_id = f"book_{book_id}"
    folders = load_folders()
    found = False
    for folder in folders:
        if folder.get("id") == folder_id:
            folder["name"] = title or book_id
            found = True
            break
    if not found:
        folders.append({"id": folder_id, "name": title or book_id, "order": len(folders)})
    save_folders(folders)
    return folder_id


def _chapter_preview(text_path: str, limit: int = 1800) -> str:
    try:
        text = Path(text_path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    text = text.replace("<!-- page:", "\n<!-- page:")
    return text[:limit].strip()


def _read_note(path: str) -> str:
    try:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _chapter_level(chapter: dict) -> int:
    try:
        return max(1, int(chapter.get("level") or 1))
    except Exception:
        return 1


def _chapter_span_end(chapters: list[dict], start: int) -> int:
    level = _chapter_level(chapters[start])
    for idx in range(start + 1, len(chapters)):
        if _chapter_level(chapters[idx]) <= level:
            return idx
    return len(chapters)


def _has_descendant_at_level(chapters: list[dict], start: int, target_level: int) -> bool:
    end = _chapter_span_end(chapters, start)
    parent_level = _chapter_level(chapters[start])
    return any(
        parent_level < _chapter_level(chapters[idx]) <= target_level
        for idx in range(start + 1, end)
    )


def _session_groups(chapters: list[dict], granularity: str) -> list[dict]:
    if granularity == "all":
        return [
            {
                "group_id": chapter.get("chapter_id", f"ch{idx + 1:03d}"),
                "title": chapter.get("title", f"第 {idx + 1} 章"),
                "chapters": [chapter],
                "level": _chapter_level(chapter),
            }
            for idx, chapter in enumerate(chapters)
        ]

    target_level = 1 if granularity == "level1" else 2
    groups: list[dict] = []
    idx = 0
    while idx < len(chapters):
        chapter = chapters[idx]
        level = _chapter_level(chapter)
        end = _chapter_span_end(chapters, idx)

        if level < target_level and _has_descendant_at_level(chapters, idx, target_level):
            # 父章节有目标层级子章节 → 跳过父章节本身，让子章节独立成组
            idx += 1
            continue

        if level > target_level:
            # 深层章节：归入最近一个已创建的组（如果有的话）
            if groups:
                groups[-1]["chapters"].append(chapter)
            else:
                # 没有父组，单独建一个
                groups.append({
                    "group_id": chapter.get("chapter_id", f"ch{idx + 1:03d}"),
                    "title": chapter.get("title", f"第 {idx + 1} 章"),
                    "chapters": [chapter],
                    "level": level,
                })
            idx += 1
            continue

        grouped = chapters[idx:end]
        groups.append({
            "group_id": chapter.get("chapter_id", f"ch{idx + 1:03d}"),
            "title": chapter.get("title", f"第 {idx + 1} 章"),
            "chapters": grouped,
            "level": level,
        })
        idx = end
    return groups or _session_groups(chapters, "all")


def _group_first_message(group: dict, title: str, index: dict, book_title: str) -> str:
    chapters = group.get("chapters") or []
    parts = [
        f"# {group.get('title', '章节组')}",
        "",
        f"📚 本对话整合 {len(chapters)} 个目录节点，已绑定《{book_title}》全书检索索引。",
        "",
    ]
    note_paths = []
    for chapter in chapters:
        note_path = chapter.get("note_path", "")
        if note_path and note_path not in note_paths:
            note_paths.append(note_path)
    if len(chapters) > 1 and len(note_paths) == 1:
        note = _read_note(note_paths[0])
        if note:
            page_start = chapters[0].get("page_start")
            page_end = chapters[-1].get("page_end")
            page_range = f"p.{page_start}-{page_end}" if page_start and page_end else ""
            child_lines = [
                f"- {chapter.get('title', '')} (p.{chapter.get('page_start')}-{chapter.get('page_end')})"
                for chapter in chapters
            ]
            parts.extend([
                "---",
                f"## 合并章节笔记 <span style=\"color:#4F8EF7;font-weight:600\">{page_range}</span>",
                "",
                "### 包含小节",
                "",
                "\n".join(child_lines),
                "",
                note,
                "",
            ])
            return "\n".join(parts).strip()

    rendered_note_paths: set[str] = set()
    for chapter in chapters:
        page_range = f"p.{chapter.get('page_start')}-{chapter.get('page_end')}"
        note_path = chapter.get("note_path", "")
        note = _read_note(note_path)
        if note:
            if note_path in rendered_note_paths:
                continue
            rendered_note_paths.add(note_path)
            parts.extend([
                "---",
                f"## {chapter.get('title', '')} <span style=\"color:#4F8EF7;font-weight:600\">{page_range}</span>",
                "",
                note,
                "",
            ])
            continue
        preview = _chapter_preview(chapter.get("text_path", ""))
        parts.extend([
            "---",
            f"## {chapter.get('title', '')} <span style=\"color:#D99A2B;font-weight:600\">{page_range}</span>",
            "",
            "⚠️ 本章节尚未生成重构讲解，暂时显示原文预览。",
            "",
            f"- 章节：{chapter.get('chapter_id', '')}",
            f"- 全书索引：{index.get('chunk_count', 0)} chunks",
            "",
            "### 原文预览",
            "",
            preview,
            "",
        ])
    return "\n".join(parts).strip()


def _prune_empty_generated_book_sessions(book_id: str, keep_ids: set[str]):
    meta = _load_meta()
    book_folder = _SESSIONS_DIR / f"book_{book_id}"
    candidates = list(book_folder.glob(f"{book_id}_*")) if book_folder.is_dir() else []
    # 兼容：迁移前可能仍有顶层扁平 session
    candidates += [d for d in _SESSIONS_DIR.glob(f"{book_id}_*") if d.is_dir()]
    for session_dir in candidates:
        sid = session_dir.name
        if sid in keep_ids:
            continue
        hfile = session_dir / "chat_history.json"
        if not hfile.is_file():
            continue
        try:
            data = json.loads(hfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("book_id") != book_id:
            continue
        rounds = sum(1 for msg in data.get("messages", []) if msg.get("role") == "user")
        if rounds == 0:
            shutil.rmtree(session_dir, ignore_errors=True)
            meta.pop(sid, None)
    _save_meta(meta)


def create_book_sessions(book_json_path: str | Path,
                         provider_config: Optional[dict] = None,
                         session_granularity: str = "level2") -> list[str]:
    """Create/update one folder, chapter sessions, and a final overview session."""
    book_path = Path(book_json_path)
    book = load_book(book_path)
    book_id = book.get("book_id") or book_path.parent.name
    title = book.get("title") or book_id
    book_dir = str(book_path.parent)
    index = book.get("index") or {}
    chapters = book.get("chapters") or []
    folder_id = _ensure_book_folder(book_id, title)
    cfg = provider_config or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta = _load_meta()
    created_ids: list[str] = []
    groups = _session_groups(chapters, session_granularity)

    def write_session(session_id: str, name: str, order: int,
                      chapter: dict | None, first_message: str,
                      grouped_chapters: list[dict] | None = None):
        session_dir = book_path.parent / session_id
        session = ChatSession(str(session_dir), cfg)
        session.name = name
        session.created_at = now
        session.folder_id = folder_id
        session.book_id = book_id
        session.book_title = title
        session.book_dir = book_dir
        session.book_json_path = str(book_path)
        session.index_version = index.get("version", "")
        session.slides_path = str(book_path)
        if chapter:
            session.chapter_id = chapter.get("chapter_id", "")
            session.chapter_title = chapter.get("title", "")
            session.notes_path = chapter.get("note_path") or chapter.get("text_path", "")
        session.system_prompt = (
            "你是这本书的学习导师。回答时应基于已绑定的 book.json、章节文本和检索索引，"
            "优先给出章节/页码出处；证据不足时说明需要查看原文。"
        )
        session.system_prompt = f"{session.system_prompt}\n{RICH_TEXT_FORMATTING_PROMPT}"
        session.messages = [_message("assistant", first_message)]
        session._save_history()
        if grouped_chapters:
            try:
                data = json.loads(Path(session.history_path).read_text(encoding="utf-8"))
                data["chapter_ids"] = [c.get("chapter_id", "") for c in grouped_chapters]
                data["chapter_text_paths"] = [c.get("text_path", "") for c in grouped_chapters]
                data["session_granularity"] = session_granularity
                out = relativize_session_paths(data, Path(session.session_dir))
                Path(session.history_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        m = _get_meta(meta, session_id)
        m["folder_id"] = folder_id
        m["order"] = order
        m["book_json_path"] = str(book_path)
        created_ids.append(session_id)

    for idx, group in enumerate(groups, 1):
        group_chapters = group.get("chapters") or []
        chapter = group_chapters[0] if group_chapters else None
        if not chapter:
            continue
        suffix = group.get("group_id") or chapter.get("chapter_id", f"ch{idx:03d}")
        session_id = _session_id_for_book(book_id, suffix)
        first_message = _group_first_message(group, title, index, title)
        write_session(
            session_id,
            f"{idx:03d} - {group.get('title', '未命名章节')}",
            idx,
            chapter,
            first_message,
            group_chapters,
        )

    for idx, chapter in []:
        session_id = _session_id_for_book(book_id, chapter.get("chapter_id", f"ch{idx:03d}"))
        page_range = f"p.{chapter.get('page_start')}-{chapter.get('page_end')}"
        preview = _chapter_preview(chapter.get("text_path", ""))
        note = _read_note(chapter.get("note_path", ""))
        first_message = note or (
            f"# {chapter.get('title', f'第 {idx} 章')}\n\n"
            f"⚠️ 本章节尚未生成重构讲解，因此这里只显示原文预览。"
            f"请确认书籍整合模型的 URL、Key、模型名可用，然后重新蒸馏。\n\n"
            f"已为本章节建立对话入口，并绑定全书索引。\n\n"
            f"- 书籍：{title}\n"
            f"- 章节：{chapter.get('chapter_id', '')}\n"
            f"- 页码：{page_range}\n"
            f"- 全书索引：{index.get('chunk_count', 0)} 个 chunks\n\n"
            f"## 原文预览\n\n{preview}"
        )
        write_session(
            session_id,
            f"{idx:03d} - {chapter.get('title', '未命名章节')}",
            idx,
            chapter,
            first_message,
        )

    overview_id = _session_id_for_book(book_id, "overview")
    overview_note = _read_note((book.get("memory") or {}).get("overview_path", ""))
    toc_lines = [
        f"{i:03d}. {c.get('title', '')} (p.{c.get('page_start')}-{c.get('page_end')})"
        for i, c in enumerate(chapters, 1)
    ]
    overview = overview_note or (
        f"# 全书总览\n\n"
        f"已为《{title}》建立全书对话入口，并绑定全书索引。\n\n"
        f"- PDF：{book.get('source_pdf', '')}\n"
        f"- 页数：{book.get('page_count', 0)}\n"
        f"- 文本页：{book.get('text_page_count', 0)}\n"
        f"- 章节/目录节点：{len(chapters)}\n"
        f"- 检索 chunks：{index.get('chunk_count', 0)}\n\n"
        "## 目录\n\n" + "\n".join(toc_lines[:120])
    )
    write_session(overview_id, "全书总览", len(groups) + 1, None, overview)
    _save_meta(meta)
    _prune_empty_generated_book_sessions(book_id, set(created_ids))
    return created_ids


def clear_book_notes_and_cache(folder_id: str) -> dict:
    """Clear generated notes/cache for a book folder without deleting sessions."""
    if not folder_id.startswith("book_"):
        raise ValueError("仅书籍文件夹支持清理书籍笔记与缓存")

    sessions = [
        s for s in list_sessions()
        if s.get("folder_id") == folder_id
    ]
    book_json_path = ""
    for s in sessions:
        hfile = Path(s["session_dir"]) / "chat_history.json"
        try:
            data = json.loads(hfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        resolve_session_paths(data, s["session_dir"])
        if data.get("book_json_path"):
            book_json_path = data["book_json_path"]
            break
    if not book_json_path:
        raise FileNotFoundError("未找到该书籍文件夹绑定的 book.json")

    book_path = Path(book_json_path)
    book = load_book(book_path)
    book_dir = book_path.parent
    ws = workspace_dir(book)
    removed = []
    # notes 在仓库，cache 在 workspace
    for base, name in ((book_dir, "notes"), (ws, "cache")):
        p = Path(base) / name
        if p.exists():
            shutil.rmtree(p)
            removed.append(str(p))
    (book_dir / "notes").mkdir(exist_ok=True)
    Path(ws).mkdir(parents=True, exist_ok=True)
    (Path(ws) / "cache").mkdir(exist_ok=True)

    for chapter in book.get("chapters", []):
        chapter.pop("note_path", None)
    if isinstance(book.get("memory"), dict):
        book["memory"].pop("overview_path", None)
    save_book(book_path, book)

    for s in sessions:
        hfile = Path(s["session_dir"]) / "chat_history.json"
        try:
            data = json.loads(hfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("chapter_id"):
            data["notes_path"] = ""
            data["messages"] = [_message(
                "assistant",
                (
                    "# 章节笔记已清理\n\n"
                    "本章节的重构讲解和缓存已删除。请在批量蒸馏页调整 Prompt 后重新蒸馏，"
                    "系统会重新生成章节笔记。"
                ),
            )]
        elif data.get("book_id"):
            data["notes_path"] = ""
            data["messages"] = [_message(
                "assistant",
                (
                    "# 全书总览已清理\n\n"
                    "全书总览笔记和缓存已删除。请重新蒸馏以生成新的总览。"
                ),
            )]
        out = relativize_session_paths(data, s["session_dir"])
        hfile.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "book_json_path": str(book_path),
        "sessions": len(sessions),
        "removed": removed,
    }


def delete_book_output_and_sessions(folder_id: str) -> dict:
    """Delete a generated book output directory and all sessions in its book folder."""
    if not folder_id.startswith("book_"):
        raise ValueError("仅书籍文件夹支持删除输出与对话")

    sessions = [
        s for s in list_sessions()
        if s.get("folder_id") == folder_id
    ]
    book_json_path = ""
    for s in sessions:
        hfile = Path(s["session_dir"]) / "chat_history.json"
        try:
            data = json.loads(hfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        resolve_session_paths(data, s["session_dir"])
        if data.get("book_json_path"):
            book_json_path = data["book_json_path"]
            break
    if not book_json_path:
        raise FileNotFoundError("未找到该书籍文件夹绑定的 book.json")

    book_path = Path(book_json_path).resolve()
    book_dir = book_path.parent
    if not book_path.is_file() or book_path.name != "book.json":
        raise FileNotFoundError("book.json 不存在，无法确认要删除的书籍输出目录")
    if book_dir == book_dir.anchor or book_dir == book_dir.parent:
        raise ValueError("输出目录异常，已拒绝删除")

    # 删除前先取出 workspace（pages/cache）位置
    try:
        ws_path = workspace_dir(load_book(book_path))
    except Exception:
        ws_path = None

    removed_sessions = 0
    for s in sessions:
        session_dir = Path(s["session_dir"])
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
            removed_sessions += 1

    if book_dir.exists():
        shutil.rmtree(book_dir)

    # workspace 与仓库分离，单独清理
    if ws_path:
        ws = Path(ws_path)
        if ws.exists() and ws.resolve() != book_dir.resolve():
            shutil.rmtree(ws, ignore_errors=True)

    folders = [f for f in load_folders() if f.get("id") != folder_id]
    save_folders(folders)

    meta = _load_meta()
    for s in sessions:
        meta.pop(s.get("session_id", ""), None)
    _save_meta(meta)

    return {
        "book_dir": str(book_dir),
        "sessions": removed_sessions,
    }


def _session_info_from_dir(sdir: str, sid: str, meta: dict) -> dict | None:
    hfile = os.path.join(sdir, "chat_history.json")
    if not os.path.isfile(hfile):
        return None
    try:
        data = json.loads(Path(hfile).read_text(encoding="utf-8"))
    except Exception:
        return None
    # 返回给 GUI 的路径解析成绝对（GUI 用 os.path.exists 判断文件是否存在）
    resolve_session_paths(data, sdir)
    msgs = data.get("messages", [])
    rounds = sum(1 for m in msgs if m.get("role") == "user")
    name = data.get("name", sid)
    m = _get_meta(meta, sid)
    return {
        "name": name,
        "session_id": sid,
        "session_dir": sdir,
        "rounds": rounds,
        "folder_id": m.get("folder_id", "") or data.get("folder_id", ""),
        "created_at": data.get("created_at", ""),
        "slides_path": data.get("slides_path", ""),
        "notes_path": data.get("notes_path", ""),
        "hidden": m.get("hidden", False),
        "favorite": m.get("favorite", False),
        "order": m.get("order", 0),
    }


def _find_session_dir(session_id: str) -> str | None:
    """按 session_id 定位目录：先在 book_* 文件夹下找，再回退顶层（迁移前扁平）。"""
    if not _SESSIONS_DIR.is_dir():
        return None
    for book_folder in _SESSIONS_DIR.iterdir():
        if book_folder.is_dir() and book_folder.name.startswith("book_"):
            cand = book_folder / session_id
            if (cand / "chat_history.json").is_file():
                return str(cand)
    flat = _SESSIONS_DIR / session_id
    if (flat / "chat_history.json").is_file():
        return str(flat)
    return None


def list_sessions() -> list[dict]:
    """扫描 sessions 目录（递归进 book_* 书文件夹），folder_id/order/hidden 从 session_meta.json 读取"""
    results: list[dict] = []
    if not _SESSIONS_DIR.is_dir():
        return results

    meta = _load_meta()

    # 收集 (session_id, abs_dir)：book_* 下的嵌套 session + 顶层手动/遗留 session
    session_dirs: list[tuple[str, str]] = []
    for entry in sorted(_SESSIONS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("book_"):
            for sub in entry.iterdir():
                if sub.is_dir() and (sub / "chat_history.json").is_file():
                    session_dirs.append((sub.name, str(sub)))
        else:
            if (entry / "chat_history.json").is_file():
                session_dirs.append((entry.name, str(entry)))

    for sid, sdir in session_dirs:
        info = _session_info_from_dir(sdir, sid, meta)
        if info:
            results.append(info)

    # 按 folder_id 分组，组内按 order / session_id 排序
    grouped: dict[str, list] = {}
    for s in results:
        grouped.setdefault(s["folder_id"], []).append(s)
    ordered = []
    for fid, items in grouped.items():
        has_custom_order = any(s["order"] != 0 for s in items)
        if has_custom_order:
            items.sort(key=lambda s: s["order"])
        else:
            items.sort(key=lambda s: s["session_id"], reverse=True)
        ordered.extend(items)
    return ordered


def toggle_session_hidden(session_ids: list[str]):
    """批量切换 session 的隐藏状态"""
    meta = _load_meta()
    for sid in session_ids:
        m = _get_meta(meta, sid)
        m["hidden"] = not m.get("hidden", False)
    _save_meta(meta)


def set_session_favorite(session_id: str, favorite: bool):
    """设置 session 收藏状态"""
    meta = _load_meta()
    _get_meta(meta, session_id)["favorite"] = favorite
    _save_meta(meta)


def rename_session(session_id: str, new_name: str):
    """重命名 session"""
    sdir = _find_session_dir(session_id)
    if not sdir:
        return
    hfile = os.path.join(sdir, "chat_history.json")
    if not os.path.isfile(hfile):
        return
    try:
        data = json.loads(Path(hfile).read_text(encoding="utf-8"))
        data["name"] = new_name
        out = relativize_session_paths(data, sdir)
        Path(hfile).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def reorder_session(session_id: str, direction: int):
    """调整 session 显示顺序。direction: -1=上移, 1=下移"""
    sdir = _find_session_dir(session_id)
    if not sdir:
        return
    hfile = os.path.join(sdir, "chat_history.json")
    if not os.path.isfile(hfile):
        return
    try:
        data = json.loads(Path(hfile).read_text(encoding="utf-8"))
        order = data.get("order", 0)
        data["order"] = order + direction
        out = relativize_session_paths(data, sdir)
        Path(hfile).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
