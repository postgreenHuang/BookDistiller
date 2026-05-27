"""
Video-Distiller AI 对话模块
- 每个对话是独立 session（按时间戳命名）
- session 持久化到 ~/.Video-Distiller/sessions/{session_id}/
- 关联 slides.json / transcript.json / notes.md
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

_SESSIONS_DIR = Path.home() / ".Video-Distiller" / "sessions"
_FOLDERS_FILE = Path.home() / ".Video-Distiller" / "folders.json"


def load_folders() -> list[dict]:
    if _FOLDERS_FILE.is_file():
        try:
            return json.loads(_FOLDERS_FILE.read_text(encoding="utf-8")).get("folders", [])
        except Exception:
            pass
    return []


def save_folders(folders: list[dict]):
    _FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FOLDERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"folders": folders}, f, ensure_ascii=False, indent=2)


CHAT_SYSTEM_PROMPT = (
    "你是一位技术学习导师。你刚刚和学生一起学习了一段技术教程。\n"
    "以下是教程的完整学习笔记和原始幻灯片描述，作为你的知识基础：\n\n"
    "--- 学习笔记 ---\n{notes}\n\n"
    "--- 幻灯片描述 ---\n{slides}\n\n"
    "你的任务是：\n"
    "1. 回答学生关于教程内容的问题\n"
    "2. 用通俗的语言解释复杂概念\n"
    "3. 帮助学生建立知识之间的联系\n"
    "4. 指出容易忽略的重要细节\n"
    "5. 建议进一步学习的方向"
)


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
            self.messages.append({
                "role": "assistant",
                "content": notes,
            })

        # 更新名称
        if notes_path:
            stem = Path(notes_path).stem
            self.name = stem

        self._save_history()

    def chat(self, user_message: str) -> str:
        if not self.system_prompt:
            return "请先配置学习资料（点击齿轮按钮），然后再开始对话。"

        self.messages.append({"role": "user", "content": user_message})
        api_messages = [{"role": "system", "content": self.system_prompt}]
        api_messages.extend(self.messages[-40:])

        reply = self._call_provider(api_messages)
        self.messages.append({"role": "assistant", "content": reply})
        self._save_history()
        return reply

    def regenerate(self) -> str:
        """重新生成最后一条 AI 回复"""
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages.pop()
        api_messages = [{"role": "system", "content": self.system_prompt}]
        api_messages.extend(self.messages[-40:])
        reply = self._call_provider(api_messages)
        self.messages.append({"role": "assistant", "content": reply})
        self._save_history()
        return reply

    def edit_and_regenerate(self, msg_index: int, new_text: str) -> str:
        """编辑用户消息，丢弃后续，重新生成"""
        self.messages[msg_index]["content"] = new_text
        self.messages = self.messages[:msg_index + 1]
        api_messages = [{"role": "system", "content": self.system_prompt}]
        api_messages.extend(self.messages[-40:])
        reply = self._call_provider(api_messages)
        self.messages.append({"role": "assistant", "content": reply})
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

    def _call_provider(self, messages: list[dict]) -> str:
        import requests

        if not self.base_url or not self.api_key:
            raise ValueError("请先在 Settings 中配置 AI Provider 的 URL 和 API Key")

        url = self.base_url + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
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
            "model": self.model,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
        }
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_history(self):
        if os.path.exists(self.history_path):
            try:
                data = json.loads(Path(self.history_path).read_text(encoding="utf-8"))
                self.messages = data.get("messages", [])
                self.name = data.get("name", self.name)
                self.created_at = data.get("created_at", "")
                self.folder_id = data.get("folder_id", "")
                self.slides_path = data.get("slides_path", "")
                self.transcript_path = data.get("transcript_path", "")
                self.notes_path = data.get("notes_path", "")
                if data.get("system_prompt"):
                    self.system_prompt = data["system_prompt"]
            except Exception:
                self.messages = []

        # 有笔记但消息为空时，注入笔记作为首条助手消息
        if self.notes_path and not self.messages:
            notes = self._read_file(self.notes_path)
            if notes:
                self.messages.append({
                    "role": "assistant",
                    "content": notes,
                })

    # ─── 工具 ───

    @staticmethod
    def _read_file(path: str) -> str:
        if path and os.path.exists(path):
            try:
                return Path(path).read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return ""

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


def list_sessions() -> list[dict]:
    """扫描 ~/.Video-Distiller/sessions/ 下所有 session"""
    results = []
    if not _SESSIONS_DIR.is_dir():
        return results

    for sid in sorted(os.listdir(_SESSIONS_DIR), reverse=True):
        sdir = str(_SESSIONS_DIR / sid)
        hfile = os.path.join(sdir, "chat_history.json")
        if not os.path.isfile(hfile):
            continue
        try:
            data = json.loads(Path(hfile).read_text(encoding="utf-8"))
        except Exception:
            continue

        msgs = data.get("messages", [])
        rounds = sum(1 for m in msgs if m.get("role") == "user")
        name = data.get("name", sid)
        results.append({
            "name": name,
            "session_id": sid,
            "session_dir": sdir,
            "rounds": rounds,
            "folder_id": data.get("folder_id", ""),
            "created_at": data.get("created_at", ""),
            "slides_path": data.get("slides_path", ""),
            "notes_path": data.get("notes_path", ""),
            "hidden": data.get("hidden", False),
            "order": data.get("order", 0),
        })

    # 按 order 排序，相同 order 按时间戳倒序
    results.sort(key=lambda s: (s["folder_id"], s["order"], s["session_id"]), reverse=False)
    # order 默认 0，时间戳已是倒序，所以需要按 folder_id 分组后反转
    # 实际效果：有 order 的按 order 排，没有的按时间倒序
    grouped = {}
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
    return results


def toggle_session_hidden(session_ids: list[str]):
    """批量切换 session 的隐藏状态"""
    for sid in session_ids:
        hfile = _SESSIONS_DIR / sid / "chat_history.json"
        if not hfile.is_file():
            continue
        try:
            data = json.loads(hfile.read_text(encoding="utf-8"))
            data["hidden"] = not data.get("hidden", False)
            hfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            continue


def rename_session(session_id: str, new_name: str):
    """重命名 session"""
    hfile = _SESSIONS_DIR / session_id / "chat_history.json"
    if not hfile.is_file():
        return
    try:
        data = json.loads(hfile.read_text(encoding="utf-8"))
        data["name"] = new_name
        hfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def reorder_session(session_id: str, direction: int):
    """调整 session 显示顺序。direction: -1=上移, 1=下移"""
    hfile = _SESSIONS_DIR / session_id / "chat_history.json"
    if not hfile.is_file():
        return
    try:
        data = json.loads(hfile.read_text(encoding="utf-8"))
        order = data.get("order", 0)
        data["order"] = order + direction
        hfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
