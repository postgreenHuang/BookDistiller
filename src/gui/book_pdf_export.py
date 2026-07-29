"""导出书籍笔记和对话至 PDF。

使用 PySide6 内置的 QPrinter + QTextDocument 渲染，零新依赖。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QProgressDialog,
)


# ═══════════════════════════════════════════════════════════════
# 数据收集
# ═══════════════════════════════════════════════════════════════

def _resolve_book_json(folder_id: str, parent=None) -> str:
    """从 session_meta 查找 book.json 路径，失败时让用户手动选择。"""
    from src.chat import _find_session_dir, _load_meta

    book_id = folder_id[5:] if folder_id.startswith("book_") else ""
    if not book_id:
        return ""

    meta = _load_meta()
    book_json_path = ""

    # 优先从 session_meta 查找
    for _sid, m in meta.items():
        if m.get("folder_id") == folder_id and m.get("book_json_path"):
            book_json_path = m["book_json_path"]
            break

    # 兜底：从 chat_history.json 中查找
    if not book_json_path or not Path(book_json_path).is_file():
        for _sid, m in meta.items():
            if m.get("folder_id") != folder_id:
                continue
            located = _find_session_dir(_sid)
            hf = Path(located) / "chat_history.json" if located else None
            if hf and hf.is_file():
                try:
                    data = json.loads(hf.read_text(encoding="utf-8"))
                    from src.paths import resolve_session_paths
                    resolve_session_paths(data, hf.parent)
                    bjp = data.get("book_json_path") or data.get("slides_path", "")
                    if bjp and Path(bjp).is_file():
                        book_json_path = bjp
                        break
                except Exception:
                    continue

    # 最终兜底：手动选择
    if not book_json_path or not Path(book_json_path).is_file():
        if parent is None:
            return ""
        bjp, _ = QFileDialog.getOpenFileName(
            parent, "选择 book.json", "",
            "book.json (book.json);;所有文件 (*)"
        )
        book_json_path = bjp

    return book_json_path


def _load_session_messages(session_id: str) -> list[dict]:
    """读取 session 的聊天消息，只保留 user/assistant 的有效消息。"""
    from src.chat import _find_session_dir

    located = _find_session_dir(session_id)
    if not located:
        return []
    hfile = Path(located) / "chat_history.json"
    if not hfile.is_file():
        return []
    try:
        data = json.loads(hfile.read_text(encoding="utf-8"))
        return [
            {"role": m.get("role", ""), "content": m.get("content", "")}
            for m in data.get("messages", [])
            if m.get("role") in ("user", "assistant") and m.get("content", "").strip()
        ]
    except Exception:
        return []


def _collect_sections(book_json_path: str) -> tuple[list[dict], dict, dict]:
    """收集所有章节笔记和对话消息。

    Returns:
        (chapter_sections, overview_section, book)
        chapter_sections — 章节列表（不含总览）
        overview_section — 全书总览（单独返回，由 _build_html 决定插入位置）
        book — book.json 数据
    """
    from src.chat import _session_id_for_book

    book = json.loads(Path(book_json_path).read_text(encoding="utf-8"))
    book_id = book.get("book_id") or Path(book_json_path).parent.name
    chapters = book.get("chapters") or []
    chapter_sections: list[dict] = []

    for chapter in chapters:
        ch_id = chapter.get("chapter_id", "")
        title = chapter.get("title", "")
        note_path = chapter.get("note_path", "")

        # 读取笔记
        note_text = ""
        if note_path and Path(note_path).is_file():
            try:
                note_text = Path(note_path).read_text(encoding="utf-8").strip()
            except Exception:
                pass

        # 读取对话
        session_id = _session_id_for_book(book_id, ch_id)
        messages = _load_session_messages(session_id)

        chapter_sections.append({
            "title": title,
            "note_text": note_text,
            "chat_messages": messages,
            "is_overview": False,
        })

    # 全书总览（独立返回）
    overview_path = (book.get("memory") or {}).get("overview_path", "")
    overview_text = ""
    if overview_path and Path(overview_path).is_file():
        try:
            overview_text = Path(overview_path).read_text(encoding="utf-8").strip()
        except Exception:
            pass

    overview_sid = _session_id_for_book(book_id, "overview")
    overview_messages = _load_session_messages(overview_sid)

    overview_section = {
        "title": "全书总览",
        "note_text": overview_text,
        "chat_messages": overview_messages,
        "is_overview": True,
    }

    return chapter_sections, overview_section, book


# ═══════════════════════════════════════════════════════════════
# HTML 构建
# ═══════════════════════════════════════════════════════════════

_CSS_LIGHT = """\
body {
    font-family: __FONT_FAMILY__;
    font-size: __FONT_SIZE__;
    line-height: 1.7;
    color: #222;
}
.title-page {
    text-align: center;
    margin-bottom: 24px;
}
.title-page h1 { font-size: __H1_SIZE__; margin-bottom: 8px; }
.title-page p { font-size: __SMALL_SIZE__; color: #666; margin: 4px 0; }
.toc { margin-bottom: 24px; }
.toc h2 { font-size: __H2_SIZE__; }
.toc ol { font-size: __FONT_SIZE__; line-height: 2.0; }
.chapter { margin-top: __SECTION_GAP__; }
.chapter h2 {
    font-size: __H2_SIZE__;
    color: #333;
    margin-top: 0;
}
.note-content { margin: 4px 0; }
.chat-header {
    font-size: __H3_SIZE__;
    font-weight: bold;
    color: #555;
    margin-top: 12px;
    margin-bottom: 4px;
}
.chat-user {
    padding: __CHAT_PAD__;
    margin: 2px 0;
    color: #1a5276;
}
.chat-assistant {
    padding: __CHAT_PAD__;
    margin: 2px 0;
}
.chat-role {
    font-weight: bold;
    font-size: __SMALL_SIZE__;
    color: #888;
}
.empty-hint { color: #999; font-style: italic; }
hr { border: none; border-top: 1px solid #ddd; margin: 8px 0; }
"""

_CSS_DARK = """\
body {
    font-family: __FONT_FAMILY__;
    font-size: __FONT_SIZE__;
    line-height: 1.7;
    color: #d4d4d4;
    background: #1e1e2e;
}
.title-page {
    text-align: center;
    margin-bottom: 24px;
}
.title-page h1 { font-size: __H1_SIZE__; margin-bottom: 8px; color: #e0e0e0; }
.title-page p { font-size: __SMALL_SIZE__; color: #888; margin: 4px 0; }
.toc { margin-bottom: 24px; }
.toc h2 { font-size: __H2_SIZE__; color: #c0c0c0; }
.toc ol { font-size: __FONT_SIZE__; line-height: 2.0; }
.chapter { margin-top: __SECTION_GAP__; }
.chapter h2 {
    font-size: __H2_SIZE__;
    color: #c8c8c8;
    margin-top: 0;
}
.note-content { margin: 4px 0; }
.chat-header {
    font-size: __H3_SIZE__;
    font-weight: bold;
    color: #aaa;
    margin-top: 12px;
    margin-bottom: 4px;
}
.chat-user {
    background: #1a2744;
    padding: __CHAT_PAD__;
    margin: 2px 0;
    color: #a8c8e8;
}
.chat-assistant {
    background: #1e2e1e;
    padding: __CHAT_PAD__;
    margin: 2px 0;
    color: #b8d8b8;
}
.chat-role {
    font-weight: bold;
    font-size: __SMALL_SIZE__;
    color: #777;
}
.empty-hint { color: #666; font-style: italic; }
hr { border: none; border-top: 1px solid #444; margin: 8px 0; }
"""

# 不同纸张大小的排版参数
_LAYOUT_PARAMS = {
    "default": {
        "__FONT_SIZE__": "10pt", "__H1_SIZE__": "22pt", "__H2_SIZE__": "14pt",
        "__H3_SIZE__": "12pt", "__SMALL_SIZE__": "9pt", "__SECTION_GAP__": "24px",
        "__CHAT_PAD__": "6px 10px",
    },
    "A6": {
        "__FONT_SIZE__": "9pt", "__H1_SIZE__": "16pt", "__H2_SIZE__": "11pt",
        "__H3_SIZE__": "10pt", "__SMALL_SIZE__": "8pt", "__SECTION_GAP__": "12px",
        "__CHAT_PAD__": "3px 0",
    },
}

_DEFAULT_FONT = '"Microsoft YaHei UI", "PingFang SC", "Noto Sans SC", "Segoe UI", sans-serif'


def _md_to_html_body(text: str) -> str:
    """Markdown → HTML，提取 body 内容（去掉 QTextDocument 的完整 HTML 包装）。"""
    doc = QTextDocument()
    doc.setMarkdown(text)
    full_html = doc.toHtml()
    # 提取 <body>...</body> 之间的内容
    m = re.search(r"<body[^>]*>(.*)</body>", full_html, re.DOTALL)
    return m.group(1).strip() if m else ""


def _escape_html(text: str) -> str:
    """轻量 HTML 转义（仅对纯文本使用）。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_html(book: dict, chapter_sections: list[dict],
                overview_section: dict) -> str:
    """组装完整的 HTML 文档。

    排版顺序：封面 → 目录 → 全书总览 → 第 1 章 → ... → 第 N 章
    """
    from src.config import load_settings
    settings = load_settings()

    # 字体
    font = getattr(settings, "pdf_font_family", "")
    font_css = f'"{font}", ' + _DEFAULT_FONT if font else _DEFAULT_FONT

    # 排版参数（A6 用紧凑版）
    paper = getattr(settings, "pdf_paper_size", "A5")
    params = dict(_LAYOUT_PARAMS.get(paper, _LAYOUT_PARAMS["default"]))
    params["__FONT_FAMILY__"] = font_css

    # 主题 CSS
    theme = getattr(settings, "pdf_theme", "light")
    css_template = _CSS_DARK if theme == "dark" else _CSS_LIGHT
    css = css_template
    for key, val in params.items():
        css = css.replace(key, val)

    book_title = book.get("title") or "未命名书籍"
    export_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts: list[str] = []

    # ── 封面（独占一页） ──
    parts.append(f"""<div class="title-page">
<h1>{_escape_html(book_title)}</h1>
<p>Book-Distiller 导出</p>
<p>{export_date}</p>
</div>""")

    # ── 目录（独占一页） ──
    toc_items = [f"<li>{_escape_html(overview_section['title'])}</li>"]
    for s in chapter_sections:
        toc_items.append(f"<li>{_escape_html(s['title'])}</li>")
    parts.append(f"""<div class="toc">
<h2>目录</h2>
<ol>{"".join(toc_items)}</ol>
</div>""")

    # ── 全书总览（目录后、章节前） ──
    parts.append(_render_section(overview_section))

    # ── 各章（自然流式排版） ──
    for i, s in enumerate(chapter_sections):
        parts.append(_render_section(s, is_last=(i == len(chapter_sections) - 1)))

    return f"""<html><head><style>{css}</style></head>
<body>{"".join(parts)}</body></html>"""


def _render_section(s: dict, is_last: bool = False) -> str:
    """渲染单个章节/总览为 HTML 片段。"""
    parts: list[str] = []
    parts.append('<div class="chapter">')
    parts.append(f'<h2>{_escape_html(s["title"])}</h2>')

    # 笔记
    if s["note_text"]:
        parts.append(f'<div class="note-content">{_md_to_html_body(s["note_text"])}</div>')
    else:
        parts.append('<p class="empty-hint">（暂无笔记）</p>')

    # 对话记录（跳过第一条 assistant 消息——那是章节笔记，已单独渲染）
    msgs = s["chat_messages"]
    chat_msgs = msgs
    if msgs and msgs[0]["role"] == "assistant":
        chat_msgs = msgs[1:]

    if chat_msgs:
        parts.append('<div class="chat-header">对话记录</div>')
        for msg in chat_msgs:
            role = msg["role"]
            role_label = "你" if role == "user" else "AI 导师"
            css_class = "chat-user" if role == "user" else "chat-assistant"
            content_html = _md_to_html_body(msg["content"])
            if not content_html:
                content_html = _escape_html(msg["content"]).replace("\n", "<br>")
            parts.append(
                f'<div class="{css_class}">'
                f'<div class="chat-role">{role_label}</div>'
                f'{content_html}'
                f'</div>'
            )
    else:
        parts.append('<p class="empty-hint">（暂无对话）</p>')

    if not is_last:
        parts.append("<hr>")
    parts.append("</div>")
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════
# PDF 渲染（主线程）
# ═══════════════════════════════════════════════════════════════

_PAGE_SIZE_MAP = {
    "A4": QPageSize.PageSizeId.A4,
    "A5": QPageSize.PageSizeId.A5,
    "B5": QPageSize.PageSizeId.B5,
    "A6": QPageSize.PageSizeId.A6,
    "Letter": QPageSize.PageSizeId.Letter,
}


def _render_pdf(html: str, output_path: str) -> None:
    """用 QPrinter + QTextDocument 将 HTML 渲染为 PDF 文件。"""
    from src.config import load_settings
    settings = load_settings()

    paper_name = getattr(settings, "pdf_paper_size", "A5")
    margin_mm = getattr(settings, "pdf_margin_mm", 10)

    page_size_id = _PAGE_SIZE_MAP.get(paper_name, QPageSize.PageSizeId.A5)

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(output_path)
    printer.setPageSize(QPageSize(page_size_id))
    printer.setPageMargins(
        QMarginsF(margin_mm, margin_mm, margin_mm, margin_mm),
        QPageLayout.Unit.Millimeter,
    )

    doc = QTextDocument()
    doc.setHtml(html)
    doc.setPageSize(printer.pageRect(QPrinter.Unit.Point).size())
    doc.print_(printer)


# ═══════════════════════════════════════════════════════════════
# Worker 线程（收集数据 + 构建 HTML）
# ═══════════════════════════════════════════════════════════════

class _PdfExportWorker(QThread):
    finished = Signal(str)       # 成功，传出 output_path
    error = Signal(str)          # 失败，传出错误消息
    progress = Signal(int, int)  # (current, total)

    def __init__(self, book_json_path: str, output_path: str):
        super().__init__()
        self.book_json_path = book_json_path
        self.output_path = output_path
        self._html = ""

    def run(self) -> None:
        try:
            chapter_sections, overview_section, book = _collect_sections(self.book_json_path)
            total = len(chapter_sections) + 1  # +1 for overview
            self.progress.emit(0, total)

            for i in range(total):
                self.progress.emit(i + 1, total)

            self._html = _build_html(book, chapter_sections, overview_section)

            # 调试：保存中间 HTML
            try:
                debug_path = Path(self.output_path).with_suffix(".debug.html")
                debug_path.write_text(self._html, encoding="utf-8")
            except Exception:
                pass

            self.finished.emit(self.output_path)
        except Exception as exc:
            self.error.emit(str(exc))


# ═══════════════════════════════════════════════════════════════
# 公开入口
# ═══════════════════════════════════════════════════════════════

def export_book_pdf(parent, folder_id: str) -> None:
    """从右键菜单导出书籍笔记和对话为 PDF。

    Args:
        parent: 父 widget（用于弹对话框）
        folder_id: 书籍文件夹 ID，格式 "book_{book_id}"
    """
    # 1. 查找 book.json
    book_json_path = _resolve_book_json(folder_id, parent=parent)
    if not book_json_path or not Path(book_json_path).is_file():
        QMessageBox.warning(parent, "导出失败", "无法定位 book.json，请确保该书已完成蒸馏。")
        return

    book = json.loads(Path(book_json_path).read_text(encoding="utf-8"))
    book_title = book.get("title") or "book"

    # 2. 选择保存位置
    default_name = f"{book_title} [笔记].pdf"
    dest, _ = QFileDialog.getSaveFileName(
        parent, "导出至 PDF", default_name,
        "PDF 文件 (*.pdf)"
    )
    if not dest:
        return
    if not dest.lower().endswith(".pdf"):
        dest += ".pdf"

    # 3. 进度对话框
    progress = QProgressDialog("正在生成 PDF...", "取消", 0, 100, parent)
    progress.setWindowTitle("导出至 PDF")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    # 4. 启动 worker
    worker = _PdfExportWorker(book_json_path, dest)

    def _on_progress(current: int, total: int) -> None:
        if progress.wasCanceled():
            worker.quit()
            return
        val = int(current / max(total, 1) * 80)
        progress.setValue(val)

    def _on_finished(output_path: str) -> None:
        progress.setLabelText("正在渲染 PDF...")
        progress.setValue(85)
        try:
            _render_pdf(worker._html, output_path)
            progress.setValue(100)
            progress.close()
            QMessageBox.information(parent, "导出完成", f"PDF 已保存到：\n{output_path}")
        except Exception as exc:
            progress.close()
            QMessageBox.warning(parent, "导出失败", str(exc))

    def _on_error(err: str) -> None:
        progress.close()
        QMessageBox.warning(parent, "导出失败", err)

    worker.progress.connect(_on_progress)
    worker.finished.connect(_on_finished)
    worker.error.connect(_on_error)
    progress.canceled.connect(worker.quit)
    worker.start()
