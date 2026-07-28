"""
Book-Distiller AI 对话界面
- 左侧：session 列表（按时间排序，显示关联文件状态）
- 右侧：消息气泡 + 模型切换 + 齿轮配置 + 新建对话
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QTextDocument, QTextOption, QCursor
from PySide6.QtWidgets import (
    QTreeWidgetItemIterator,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTextBrowser, QScrollArea, QSizePolicy,
    QTreeWidget, QTreeWidgetItem, QFrame, QComboBox, QFileDialog,
    QMenu, QDialog, QGridLayout, QLineEdit, QDialogButtonBox,
    QInputDialog, QSplitter, QApplication, QMessageBox,
)

from src.chat import ChatSession, create_empty_session, list_sessions
from src.paths import load_book, resolve_session_paths, save_book


_THINKING_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class _ActionPanel(QWidget):
    """消息操作栏：独立插入 messages_layout，常驻显示在气泡下方"""

    actionTriggered = Signal(str, int)  # (action, msg_index)

    _STYLE = None  # 缓存主题颜色

    def __init__(self, role: str, msg_index: int, parent=None, created_at: str = ""):
        super().__init__(parent)
        self._role = role
        self._msg_index = msg_index
        self._created_at = created_at
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 按钮样式：内联设置，绕开全局 QPushButton QSS
        from src.gui.theme import _current_colors
        c = _current_colors()
        btn_ss = (
            f"background: transparent; border: none; border-radius: 4px;"
            f" padding: 1px 3px; color: {c['text_secondary']};"
            f" font-size: 12px; font-weight: normal; min-height: 0px;"
        )
        btn_hover_ss = (
            f"background: {c['btn_secondary']}; color: {c['text']};"
        )

        def _btn(label: str, action: str, tooltip: str = ""):
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setMinimumWidth(4)
            b.setToolTip(tooltip or label)
            b.setStyleSheet(f"QPushButton {{ {btn_ss} }} QPushButton:hover {{ {btn_hover_ss} }}")
            b.clicked.connect(
                lambda checked=False, a=action: self.actionTriggered.emit(a, self._msg_index))
            return b

        btns = []
        btns.append(_btn("复制", "copy", "复制"))
        if role == "user":
            btns.append(_btn("编辑", "edit", "编辑并重新生成"))
        else:
            btns.append(_btn("重试", "retry", "重新生成"))
        btns.append(_btn("删除", "delete", "删除"))
        btns.append(_btn("引用", "quote", "引用到输入框"))

        # margin 对齐气泡
        self._time_label = QLabel(created_at)
        self._time_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._time_label.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 11px; padding: 0 0 0 10px;"
        )

        if role == "assistant":
            layout.setContentsMargins(0, 0, 36, 0)
            layout.addStretch()
            for b in btns:
                layout.addWidget(b)
            layout.addSpacing(6)
            self._btn_good = _btn("👍", "good", "好评")
            self._btn_bad = _btn("👎", "bad", "差评")
            layout.addWidget(self._btn_good)
            layout.addWidget(self._btn_bad)
            layout.addSpacing(6)
            layout.addWidget(_btn("风格", "style", "修改回复风格"))
            if created_at:
                layout.addWidget(self._time_label)
        else:
            layout.setContentsMargins(36, 0, 0, 0)
            layout.addStretch()
            for b in btns:
                layout.addWidget(b)
            if created_at:
                layout.addWidget(self._time_label)

    def set_feedback(self, state, c: dict = None):
        if c is None:
            from src.gui.theme import _current_colors
            c = _current_colors()
        active_ss = (
            f"background: transparent; border: none; border-radius: 4px;"
            f" padding: 1px 3px; color: {c['accent']};"
            f" font-size: 12px; font-weight: bold; min-height: 0px;"
        )
        inactive_ss = (
            f"background: transparent; border: none; border-radius: 4px;"
            f" padding: 1px 3px; color: {c['text_secondary']};"
            f" font-size: 12px; font-weight: normal; min-height: 0px;"
        )
        self._btn_good.setStyleSheet(
            f"QPushButton {{ {active_ss if state == 'good' else inactive_ss} }}")
        self._btn_bad.setStyleSheet(
            f"QPushButton {{ {active_ss if state == 'bad' else inactive_ss} }}")

    def _refresh_style(self, c: dict = None):
        """主题切换后，重新应用所有按钮的内联样式"""
        if c is None:
            from src.gui.theme import _current_colors
            c = _current_colors()
        btn_ss = (
            f"background: transparent; border: none; border-radius: 4px;"
            f" padding: 1px 3px; color: {c['text_secondary']};"
            f" font-size: 12px; font-weight: normal; min-height: 0px;"
        )
        btn_hover_ss = (
            f"background: {c['btn_secondary']}; color: {c['text']};"
        )
        full_ss = f"QPushButton {{ {btn_ss} }} QPushButton:hover {{ {btn_hover_ss} }}"
        self._time_label.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 11px; padding: 0 0 0 10px;"
        )
        feedback_buttons = tuple(
            btn for btn in (
                getattr(self, "_btn_good", None),
                getattr(self, "_btn_bad", None),
            )
            if btn is not None
        )
        for btn in self.findChildren(QPushButton):
            if btn not in feedback_buttons:
                btn.setStyleSheet(full_ss)
        # 重新应用 feedback 状态
        if len(feedback_buttons) == 2:
            # 查找当前 feedback 状态
            fb = None
            if self._btn_good.styleSheet() and f"color: {c['accent']}" in self._btn_good.styleSheet():
                fb = "good"
            elif self._btn_bad.styleSheet() and f"color: {c['accent']}" in self._btn_bad.styleSheet():
                fb = "bad"
            self.set_feedback(fb, c)


class _MessageTimeLabel(QLabel):
    def __init__(self, role: str, msg_index: int, text: str, parent=None):
        super().__init__(text, parent)
        self._role = role
        self._msg_index = msg_index
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._refresh_style()

    def _refresh_style(self, c: dict = None):
        if c is None:
            from src.gui.theme import _current_colors
            c = _current_colors()
        margin = "0 36px 0 4px" if self._role == "assistant" else "0 4px 0 36px"
        self.setAlignment(Qt.AlignmentFlag.AlignLeft if self._role == "assistant" else Qt.AlignmentFlag.AlignRight)
        self.setStyleSheet(
            f"color: {c['text_secondary']}; font-size: 11px; "
            f"padding: 0; margin: {margin};"
        )


class _ImageViewerDialog(QDialog):
    """点击图片后弹出的大图查看器"""
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(os.path.basename(image_path))
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QApplication

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        pixmap = QPixmap(image_path)
        screen = QApplication.primaryScreen().availableGeometry()
        max_w = int(screen.width() * 0.85)
        max_h = int(screen.height() * 0.85)
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(max_w, max_h,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)

        label = QLabel()
        label.setPixmap(pixmap)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("background: #1a1a1a;")
        layout.addWidget(label)
        self.resize(pixmap.size())


class MessageBubble(QTextBrowser):
    _font_family = ""
    _font_scale = 100
    _base_dir = ""
    _img_map: dict = {}

    def __init__(self, role: str, text: str = "", index: int = -1, parent=None):
        super().__init__(parent)
        self._role = role
        self._raw_text = text
        self._msg_index = index
        self._img_map: dict = {}
        self._action_panel = None
        self._theme_colors: dict = {}

        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenExternalLinks(False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._apply_wrap_mode()
        self.document().documentLayout().documentSizeChanged.connect(self._adjust_size)

        self._apply_bubble_style()

        if text:
            self._apply_font()
        else:
            self._sync_widget_font()

    def set_msg_index(self, idx: int):
        self._msg_index = idx

    # ─── 尺寸自适应 ───

    def _adjust_size(self):
        doc_h = int(self.document().documentLayout().documentSize().height())
        target = doc_h + 26
        if abs(self.height() - target) > 2:
            self.setFixedHeight(target)
            self.updateGeometry()

    def _apply_wrap_mode(self):
        option = self.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)

    # ─── 图片预加载到文档资源缓存 ───

    _MAX_IMG_HEIGHT = 200

    def _preload_images(self, img_map: dict):
        """在 setHtml 之前，把图片缩放后作为 ImageResource 注入文档缓存"""
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import QUrl

        doc = self.document()
        for src_key, value in img_map.items():
            if isinstance(value, QPixmap):
                # 公式图片：直接是 QPixmap
                pix = value
            else:
                pix = QPixmap(value)
                if not pix.isNull() and pix.height() > self._MAX_IMG_HEIGHT:
                    pix = pix.scaledToHeight(self._MAX_IMG_HEIGHT,
                        Qt.TransformationMode.SmoothTransformation)
            if not pix.isNull():
                url = QUrl(src_key)
                doc.addResource(QTextDocument.ResourceType.ImageResource, url, pix)

    # ─── 图片点击查看大图 ───

    def mouseReleaseEvent(self, event):
        # 先让 QTextBrowser 处理（选择文本等）
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # 通过文档光标检测点击位置是否在图片上
        cursor = self.cursorForPosition(event.position().toPoint())
        # 向前扫描找到 <img> 的 src
        doc = self.document()
        block = cursor.block()
        pos_in_block = cursor.position() - block.position()
        text = block.text()
        # 在 QTextDocument 内部格式中查找 ImageFormat
        fmt = cursor.charFormat()
        if fmt.isImageFormat():
            img_fmt = fmt.toImageFormat()
            src = img_fmt.name()
            abs_path = self._img_map.get(src, "")
            if abs_path and os.path.isfile(abs_path):
                dlg = _ImageViewerDialog(abs_path, self.window())
                dlg.exec()
                event.accept()

    # ─── Markdown → HTML 渲染 ───

    @staticmethod
    def _find_image(src: str) -> str:
        """在 base_dir 的 frames/、key_frames/、images/ 子目录中搜索图片，返回绝对路径或空串"""
        if not MessageBubble._base_dir:
            return ""
        # 尝试原始名 + 冒号→下划线规范化
        candidates = [src]
        if ":" in src:
            candidates.append(src.replace(":", "_", 1))
        for name in candidates:
            for subdir in ("frames", "key_frames", "images", ""):
                full = os.path.join(MessageBubble._base_dir, subdir, name) if subdir else os.path.join(MessageBubble._base_dir, name)
                if os.path.isfile(full):
                    return full
        return ""

    @staticmethod
    def _render_md(text: str, font_family: str, font_scale: int,
                   colors: dict = None, role: str = "assistant") -> tuple:
        """返回 (html, img_map)，img_map = {src_key: abs_path}"""
        import html as html_lib
        import re
        from PySide6.QtGui import QFont

        if colors is None:
            from src.gui.theme import _current_colors
            colors = _current_colors()

        base_px = 14.0 * font_scale / 100.0
        family = font_family if font_family else ("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI")
        font = QFont(family)
        font.setPixelSize(int(base_px))

        code_blocks = []

        # ── 预处理 Mermaid 代码块 → 占位 HTML ──
        def _extract_mermaid(m):
            src = m.group(1).strip()
            lines = src.splitlines()
            # 提取 subgraph 标题或前几行作为摘要
            summary_parts = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(('subgraph', 'graph ', 'flowchart', 'sequenceDiagram',
                                        'classDiagram', 'stateDiagram', 'erDiagram', 'gantt')):
                    summary_parts.append(stripped)
                if len(summary_parts) >= 3:
                    break
            summary = " | ".join(summary_parts) if summary_parts else (lines[0].strip() if lines else "")
            summary = html_lib.escape(summary)
            escaped_src = html_lib.escape(src)
            return (
                f'<div style="border-left:3px solid #4F8EF7;padding:8px 12px;margin:8px 0;'
                f'background:rgba(79,142,247,0.08);border-radius:4px;">'
                f'<div style="color:#4F8EF7;font-weight:600;margin-bottom:4px;">📊 关系图</div>'
                f'<div style="font-size:0.9em;opacity:0.8;">{summary}</div>'
                f'<details><summary style="cursor:pointer;color:#4F8EF7;">查看源码</summary>'
                f'<pre style="margin:4px 0;font-size:0.85em;white-space:pre-wrap;">{escaped_src}</pre>'
                f'</details></div>'
            )

        text = re.sub(r"```mermaid\s*\n(.*?)```", _extract_mermaid, text, flags=re.DOTALL)

        def _extract_code_block(m):
            lang = (m.group(1) or "").strip()
            code = m.group(2).rstrip("\n")
            key = f"VD_CODE_BLOCK_{len(code_blocks)}"
            code_blocks.append(
                MessageBubble._render_code_block_html(
                    code, lang, colors, role, font_scale
                )
            )
            return f"\n\n{key}\n\n"

        # Protect fenced code before image/math/link preprocessing touches it.
        text = re.sub(r"```([^\n`]*)\n(.*?)```", _extract_code_block, text, flags=re.DOTALL)

        # 预处理：统一各种非标准图片引用为标准 Markdown 格式
        # 时间戳格式：XX_XX 或 XX:XX，可选后缀，.jpg/.jpeg/.png
        _TS = r'\d{1,2}[:_]\d{2}'
        _IMG_RE = rf'({_TS}(?:_\w+)?\.(?:jpg|jpeg|png))'

        def _normalize_colon(m):
            """把 XX:XX_frame.jpg 规范化为 XX_XX_frame.jpg"""
            return m.group(0).replace(":", "_", 1)

        # 格式1: "XX_XX_frame.jpg (描述)" 或 "05:00_frame.jpg (描述)" → ![描述](XX_XX_frame.jpg)
        text = re.sub(
            _IMG_RE + r'\s*\(([^)]+)\)',
            lambda m: f'![{m.group(2)}]({_normalize_colon(m)})',
            text,
        )

        # 格式2: "XX_XX_frame.jpg: 描述" → ![描述](XX_XX_frame.jpg)
        text = re.sub(
            _IMG_RE + r':\s*(.+?)(?:\n|$)',
            lambda m: f'![{m.group(2).strip()}]({_normalize_colon(m)})\n',
            text,
        )

        # 格式3: "[截图引用：a.jpg, b.jpg]" 或 "截图引用： a.jpg" → 逐个展开
        def _expand_img_list(m):
            content = m.group(1)
            files = re.findall(rf'{_TS}(?:_\w+)?\.(?:jpg|jpeg|png)', content)
            return "\n".join(f"![截图]({f.replace(':', '_', 1)})" for f in files)
        text = re.sub(r'(?:\[)?截图引用[：:]\s*([^\]]+?)(?:\])?(?:\n|$)', _expand_img_list, text)

        # 格式4: 裸文件名单独一行
        text = re.sub(
            rf'(?<!!\[)\b({_TS}(?:_\w+)?\.(?:jpg|jpeg|png))\b(?!\))',
            lambda m: f'![截图]({_normalize_colon(m)})',
            text,
        )

        # LaTeX 公式 → 图片（$$...$$ 块级 和 $...$ 行内）
        img_map = {}  # src_key → abs_path 或 QPixmap
        formula_counter = [0]

        def _render_latex_to_img(latex: str, display: bool) -> str:
            """将 LaTeX 渲染为临时 PNG，返回 img src key"""
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                from io import BytesIO

                fig, ax = plt.subplots(figsize=(0.01, 0.01))
                ax.set_axis_off()
                fontsize = 14 if display else 12
                ax.text(0, 0.5, f"${latex}$", fontsize=fontsize,
                        va="center", ha="left", transform=ax.transAxes)
                buf = BytesIO()
                fig.savefig(buf, format="png", dpi=150,
                            bbox_inches="tight", pad_inches=0.08,
                            transparent=True)
                plt.close(fig)
                buf.seek(0)

                from PySide6.QtGui import QPixmap
                pix = QPixmap()
                pix.loadFromData(buf.read())
                if pix.isNull():
                    return latex  # 渲染失败，返回原文

                # 缩放到合适高度
                max_h = 60 if not display else 100
                if pix.height() > max_h:
                    pix = pix.scaledToHeight(max_h, Qt.TransformationMode.SmoothTransformation)

                # 存入文档资源（用临时 key）
                key = f"_formula_{formula_counter[0]}"
                formula_counter[0] += 1
                img_map[key] = pix  # 特殊标记：直接存 QPixmap
                return key
            except Exception:
                return latex  # matplotlib 不可用时返回原文

        # 先处理块级公式: $$...$$ 和 \[...\]
        def _replace_display_math(m):
            latex = m.group(1).strip()
            key = _render_latex_to_img(latex, display=True)
            if key.startswith("_formula_"):
                return f"\n\n![formula]({key})\n\n"
            return m.group(0)

        text = re.sub(r'\$\$(.+?)\$\$', _replace_display_math, text, flags=re.DOTALL)
        text = re.sub(r'\\\[(.+?)\\\]', _replace_display_math, text, flags=re.DOTALL)

        # 再处理行内公式: $...$ 和 \(...\)
        def _replace_inline_math(m):
            latex = m.group(1).strip()
            key = _render_latex_to_img(latex, display=False)
            if key.startswith("_formula_"):
                return f"![formula]({key})"
            return m.group(0)

        text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', _replace_inline_math, text)
        text = re.sub(r'\\\((.+?)\\\)', _replace_inline_math, text)

        # 收集图片路径，生成唯一 src key
        # img_map 已包含公式图片 (_formula_N → QPixmap)
        img_counter = [formula_counter[0]]

        def _resolve_md_img(m):
            alt, src = m.group(1), m.group(2)
            if src.startswith(("http://", "https://", "data:")):
                return m.group(0)
            # 公式图片已经渲染好，直接保留
            if src.startswith("_formula_") and src in img_map:
                return m.group(0)
            # file:/// 绝对路径：提取本地路径
            if src.startswith("file:///"):
                local = src[8:]
                if os.path.isfile(local):
                    key = f"img_{img_counter[0]}"
                    img_counter[0] += 1
                    img_map[key] = local
                    return f"![{alt}]({key})"
                return m.group(0)
            # 相对路径：搜索 frames/ 和 key_frames/
            abs_path = MessageBubble._find_image(src)
            if abs_path:
                key = f"img_{img_counter[0]}"
                img_counter[0] += 1
                img_map[key] = abs_path
                return f"![{alt}]({key})"
            return m.group(0)

        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _resolve_md_img, text)

        def _render_inline_code(m):
            palette = MessageBubble._code_palette(colors, role)
            code = html_lib.escape(m.group(1))
            return (
                f'<span style="font-family:{palette["font"]}; '
                f'background-color:{palette["inline_bg"]}; color:{palette["fg"]}; '
                f'padding:1px 4px;">{code}</span>'
            )

        text = re.sub(r"(?<!`)`([^`\n]+)`(?!`)", _render_inline_code, text)
        for idx, block_html in enumerate(code_blocks):
            text = text.replace(f"VD_CODE_BLOCK_{idx}", block_html)

        # ── Markdown → HTML（使用 Python markdown 库） ──
        import markdown as md_lib
        md_converter = md_lib.Markdown(extensions=[
            'tables',
            'fenced_code',
            'toc',
            'pymdownx.tasklist',
            'pymdownx.magiclink',
        ])
        # markdown 库会把代码块转为 <pre><code>，但我们已经自己渲染了代码块
        # 所以先还原代码块占位符，再转换
        html_body = md_converter.convert(text)

        # 用 QTextDocument 包裹以获取 Qt 默认字体和样式
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(html_body)
        html = doc.toHtml()

        def _patch(tag, top, bottom):
            nonlocal html
            html = re.sub(
                rf'(<{tag}\s[^>]*?)margin-top:\s*\d+px;(\s*)margin-bottom:\s*\d+px',
                rf'\g<1>margin-top:{top}px;\2margin-bottom:{bottom}px',
                html,
            )

        _patch('h1', 20, 8)
        _patch('h2', 18, 6)
        _patch('h3', 14, 4)
        _patch('p',  4,  4)
        _patch('li', 2,  2)

        # 把连续的 <p><img/></p> 合并为横向可点击的图片行
        html = MessageBubble._group_consecutive_images(html, img_map)

        # 压缩空白段落（Markdown 图片经常产生多余的空 <p></p>）
        html = re.sub(r'<p\s[^>]*>\s*</p>', '', html)

        return html, img_map

    @staticmethod
    def _group_consecutive_images(html: str, img_map: dict) -> str:
        import re
        img_p = re.compile(r'<p\s[^>]*>(?:\s*<img\s[^>]*>\s*)+</p>')

        last_end = 0
        chunks = []
        for m in img_p.finditer(html):
            if m.start() > last_end:
                chunks.append(('text', html[last_end:m.start()]))
            srcs = re.findall(r'<img\s[^>]*?src="([^"]*)"', m.group(0))
            chunks.append(('imgs', srcs))
            last_end = m.end()
        if last_end < len(html):
            chunks.append(('text', html[last_end:]))

        result_parts = []
        img_buf = []
        for typ, content in chunks:
            if typ == 'imgs':
                img_buf.extend(content)
            else:
                if not content.strip() and img_buf:
                    continue
                if img_buf:
                    result_parts.append(MessageBubble._make_img_row(img_buf, img_map))
                    img_buf = []
                result_parts.append(content)
        if img_buf:
            result_parts.append(MessageBubble._make_img_row(img_buf, img_map))
        return ''.join(result_parts)

    @staticmethod
    def _make_img_row(srcs: list, img_map: dict) -> str:
        from PySide6.QtGui import QPixmap
        items = []
        for src in srcs:
            value = img_map.get(src, "")
            if isinstance(value, QPixmap):
                items.append(f'<img src="{src}" /> ')
            elif value:
                href = "imgview:///" + value.replace(os.sep, "/")
                items.append(
                    f'<a href="{href}">'
                    f'<img src="{src}" />'
                    f'</a> '
                )
            else:
                items.append(f'<img src="{src}" /> ')
        return '<p style="margin:0;">' + ''.join(items) + '</p>'

    @staticmethod
    def _code_palette(c: dict, role: str) -> dict:
        if role == "user":
            return {
                "bg": "rgba(255,255,255,0.14)",
                "inline_bg": "rgba(255,255,255,0.18)",
                "border": "rgba(255,255,255,0.28)",
                "fg": "#f7f7fb",
                "comment": "#c8d5ef",
                "keyword": "#ffffff",
                "string": "#dff3ff",
                "number": "#fff1b8",
                "font": "'Cascadia Mono', 'Consolas', 'Menlo', monospace",
            }
        is_dark = c.get("bg", "").lower() in ("#1c1c1e", "#000000")
        if is_dark:
            return {
                "bg": "#202124",
                "inline_bg": "#34363a",
                "border": "#4a4d52",
                "fg": "#e8eaed",
                "comment": "#7fb069",
                "keyword": "#8ab4f8",
                "string": "#f6c177",
                "number": "#c58af9",
                "font": "'Cascadia Mono', 'Consolas', 'Menlo', monospace",
            }
        return {
            "bg": "#f3f6f8",
            "inline_bg": "#e9eef3",
            "border": "#d4dbe3",
            "fg": "#1f2328",
            "comment": "#5f7f3f",
            "keyword": "#0550ae",
            "string": "#9a6700",
            "number": "#8250df",
            "font": "'Cascadia Mono', 'Consolas', 'Menlo', monospace",
        }

    @staticmethod
    def _render_code_block_html(code: str, lang: str, c: dict,
                                role: str, font_scale: int) -> str:
        import html as html_lib

        palette = MessageBubble._code_palette(c, role)
        highlighted = MessageBubble._highlight_code(code, lang, palette)
        lang_label = html_lib.escape(lang.upper()) if lang else "CODE"
        font_px = max(11, int(13 * font_scale / 100))
        return (
            f'<table width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin-top:7px; margin-bottom:9px; '
            f'background-color:{palette["bg"]}; border:1px solid {palette["border"]};">'
            f'<tr><td style="padding:4px 9px; color:{palette["comment"]}; '
            f'font-family:{palette["font"]}; font-size:{max(10, font_px - 1)}px;">'
            f'{lang_label}</td></tr>'
            f'<tr><td style="padding:2px 9px 8px 9px;">'
            f'<pre style="margin:0; color:{palette["fg"]}; '
            f'font-family:{palette["font"]}; font-size:{font_px}px; '
            f'white-space:pre-wrap;">{highlighted}</pre>'
            f'</td></tr></table>'
        )

    @staticmethod
    def _highlight_code(code: str, lang: str, palette: dict) -> str:
        import html as html_lib
        import re

        keywords = {
            "and", "as", "async", "await", "break", "case", "catch", "class",
            "const", "continue", "def", "delete", "do", "else", "enum", "except",
            "export", "extends", "false", "finally", "for", "from", "func",
            "function", "if", "import", "in", "interface", "let", "match", "new",
            "none", "null", "or", "private", "protected", "public", "return",
            "self", "static", "struct", "switch", "this", "throw", "true", "try",
            "type", "using", "var", "void", "while", "yield",
            "int", "float", "double", "bool", "char", "auto", "string",
        }
        keyword_re = re.compile(r"\b(" + "|".join(sorted(keywords)) + r")\b")
        token_re = re.compile(
            r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\b\d+(?:\.\d+)?\b)"
        )

        def span(color_key: str, value: str) -> str:
            return f'<span style="color:{palette[color_key]};">{value}</span>'

        def highlight_plain(part: str) -> str:
            out = []
            pos = 0
            for m in token_re.finditer(part):
                before = html_lib.escape(part[pos:m.start()])
                before = keyword_re.sub(
                    lambda km: span("keyword", km.group(0)), before
                )
                out.append(before)
                raw = m.group(0)
                escaped = html_lib.escape(raw)
                out.append(span("string" if raw[:1] in ("'", '"') else "number", escaped))
                pos = m.end()
            rest = html_lib.escape(part[pos:])
            rest = keyword_re.sub(lambda km: span("keyword", km.group(0)), rest)
            out.append(rest)
            return "".join(out)

        def comment_span(value: str) -> str:
            return span("comment", html_lib.escape(value))

        out_lines = []
        in_block = False
        for line in code.splitlines() or [""]:
            i = 0
            pieces = []
            while i < len(line):
                if in_block:
                    end = line.find("*/", i)
                    if end == -1:
                        pieces.append(comment_span(line[i:]))
                        i = len(line)
                    else:
                        pieces.append(comment_span(line[i:end + 2]))
                        i = end + 2
                        in_block = False
                    continue

                candidates = []
                for token in ("/*", "//", "#", "--", "<!--"):
                    pos = line.find(token, i)
                    if pos != -1:
                        candidates.append((pos, token))
                if not candidates:
                    pieces.append(highlight_plain(line[i:]))
                    break

                pos, token = min(candidates, key=lambda x: x[0])
                pieces.append(highlight_plain(line[i:pos]))
                if token == "/*":
                    end = line.find("*/", pos + 2)
                    if end == -1:
                        pieces.append(comment_span(line[pos:]))
                        in_block = True
                        break
                    pieces.append(comment_span(line[pos:end + 2]))
                    i = end + 2
                elif token == "<!--":
                    end = line.find("-->", pos + 4)
                    if end == -1:
                        pieces.append(comment_span(line[pos:]))
                        break
                    pieces.append(comment_span(line[pos:end + 3]))
                    i = end + 3
                else:
                    pieces.append(comment_span(line[pos:]))
                    break
            out_lines.append("".join(pieces))
        return "\n".join(out_lines)

    def _apply_font(self):
        html, img_map = self._render_md(
            self._raw_text,
            self._font_family,
            self._font_scale,
            self._theme_colors or None,
            self._role,
        )
        self._img_map = img_map
        self._preload_images(img_map)
        if self._theme_colors:
            self._apply_document_style(self._theme_colors)
        self.setHtml(html)
        self._apply_wrap_mode()
        self._sync_widget_font()

    def _sync_widget_font(self):
        base_px = 14.0 * self._font_scale / 100.0
        family = self._font_family if self._font_family else ("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI")
        font = QFont(family)
        font.setPixelSize(int(base_px))
        self.document().setDefaultFont(font)
        self._apply_wrap_mode()

    def _apply_bubble_style(self, c: dict = None):
        """Apply bubble colors to the widget, viewport, and rich-text document."""
        if c is None:
            from src.gui.theme import _current_colors
            c = _current_colors()
        self._theme_colors = c
        if self._role == "user":
            bg = c['accent']
            fg = "#e8e8ee"
        else:
            bg = c['surface']
            fg = c['text']
        ss = (
            f"QTextBrowser {{ background: {bg}; color: {fg}; border: none;"
            f" border-radius: 10px; padding: 12px 18px; }}"
        )
        self.setStyleSheet(ss)
        self.viewport().setStyleSheet(f"background: {bg}; color: {fg};")
        self._apply_document_style(c)
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().style().unpolish(self.viewport())
        self.viewport().style().polish(self.viewport())
        self.viewport().update()
        self.update()

    def _apply_document_style(self, c: dict):
        """Keep QTextDocument-rendered markdown in sync with the current theme."""
        fg = "#e8e8ee" if self._role == "user" else c["text"]
        link = "#ffffff" if self._role == "user" else c["accent"]
        code_bg = "rgba(255,255,255,0.16)" if self._role == "user" else c["input_bg"]
        table_border = "rgba(255,255,255,0.26)" if self._role == "user" else c["border_group"]
        table_head_bg = "rgba(255,255,255,0.12)" if self._role == "user" else c["input_bg"]
        self.document().setDefaultStyleSheet(f"""
            body, p, li, h1, h2, h3, h4, h5, h6, table, tr, th, td {{
                color: {fg};
                background: transparent;
            }}
            a {{ color: {link}; }}
            table {{
                border-collapse: collapse;
                margin-top: 8px;
                margin-bottom: 8px;
                width: 100%;
            }}
            th, td {{
                border: 1px solid {table_border};
                padding: 5px 7px;
                vertical-align: top;
                white-space: normal;
            }}
            th {{
                background: {table_head_bg};
                font-weight: 600;
            }}
            code, pre {{
                color: {fg};
                background: {code_bg};
            }}
        """)

    @classmethod
    def set_chat_font(cls, family: str, scale: int):
        cls._font_family = family
        cls._font_scale = scale

    @classmethod
    def set_base_dir(cls, base_dir: str):
        cls._base_dir = base_dir


class _DraggableTreeWidget(QTreeWidget):
    """支持 session 拖拽排序的 QTreeWidget"""
    orderChanged = Signal()
    favoriteClicked = Signal(QTreeWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self._drag_item = None

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "session":
                rect = self.visualItemRect(item)
                depth = 0
                p = item.parent()
                while p:
                    depth += 1
                    p = p.parent()
                star_left = rect.left() + self.indentation() * depth
                if star_left <= event.position().toPoint().x() <= star_left + 24:
                    self.favoriteClicked.emit(item)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "session":
            return
        self._drag_item = item
        super().startDrag(supportedActions)

    def dragEnterEvent(self, event):
        if self._drag_item:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._drag_item:
            target = self.itemAt(event.position().toPoint())
            if target:
                target_data = target.data(0, Qt.ItemDataRole.UserRole)
                if target_data and target_data.get("type") == "session":
                    if self._drag_item.parent() is target.parent():
                        event.acceptProposedAction()
                        return
            event.ignore()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not self._drag_item:
            event.ignore()
            return

        target = self.itemAt(event.position().toPoint())
        if not target:
            event.ignore()
            self._drag_item = None
            return

        target_data = target.data(0, Qt.ItemDataRole.UserRole)
        if not target_data or target_data.get("type") != "session":
            event.ignore()
            self._drag_item = None
            return

        if self._drag_item.parent() is not target.parent():
            event.ignore()
            self._drag_item = None
            return

        event.acceptProposedAction()

        parent = self._drag_item.parent()
        drag_data = self._drag_item.data(0, Qt.ItemDataRole.UserRole)

        # 收集该文件夹下所有 session item，保持当前顺序
        children = []
        for i in range(parent.childCount()):
            child = parent.child(i)
            children.append(child)

        # 找到拖拽项和目标项的索引
        drag_idx = children.index(self._drag_item) if self._drag_item in children else -1
        target_idx = children.index(target) if target in children else -1
        if drag_idx < 0 or target_idx < 0 or drag_idx == target_idx:
            self._drag_item = None
            return

        # 判断放在目标的上方还是下方
        rect = self.visualItemRect(target)
        drop_pos = "above" if event.position().toPoint().y() < rect.center().y() else "below"

        # 从列表中移除拖拽项
        item = children.pop(drag_idx)

        # 重新计算目标索引（因为移除了一项）
        new_target_idx = children.index(target) if target in children else 0

        # 插入到目标位置
        if drop_pos == "above":
            insert_idx = new_target_idx
        else:
            insert_idx = new_target_idx + 1
        children.insert(insert_idx, item)

        # 从 parent 中移除所有子项，再按新顺序添加回去
        self.setUpdatesEnabled(False)
        for i in range(parent.childCount()):
            parent.takeChild(0)
        for child in children:
            parent.addChild(child)
        self.setUpdatesEnabled(True)

        self._persist_order()
        self._drag_item = None
        self.orderChanged.emit()

    def _persist_order(self):
        """写入统一的 session_meta.json，只更新 order 变化的 session"""
        from src.chat import _load_meta, _save_meta, _get_meta

        meta = _load_meta()
        changed = False
        idx = 0
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "session":
                if data.get("order", 0) != idx:
                    data["order"] = idx
                    item.setData(0, Qt.ItemDataRole.UserRole, data)
                    _get_meta(meta, data["session_id"])["order"] = idx
                    changed = True
                idx += 1
            it.__next__()

        if changed:
            _save_meta(meta)


class _ChatWorker(QThread):
    finished = Signal(str, int)
    error = Signal(str)

    def __init__(self, session: ChatSession, message: str):
        super().__init__()
        self.session = session
        self.message = message
        self._cancel = False
        self._method = "chat"
        self._edit_msg_index = -1
        self._distill_level = ""
        self._output_language = "中文"

    def run(self):
        try:
            if self._method == "regenerate":
                reply = self.session.regenerate()
            elif self._method == "edit_and_regenerate":
                reply = self.session.edit_and_regenerate(
                    self._edit_msg_index, self.message)
            elif self._method == "regenerate_note":
                reply = self._regenerate_chapter_note()
                if reply is None:
                    self.error.emit("笔记生成失败：请检查书籍整合模型配置")
                    return
            elif self._method == "reply_to_last_user":
                reply = self.session.reply_to_last_user()
            else:
                reply = self.session.chat(self.message)
            if self._cancel:
                return
            total = sum(len(m["content"]) for m in self.session.messages)
            self.finished.emit(reply, total)
        except Exception as e:
            if not self._cancel:
                self.error.emit(str(e))

    def _regenerate_chapter_note(self) -> str | None:
        """重新生成当前对话的章节笔记，替换 messages[0]。"""
        import json as _json
        from pathlib import Path as _Path
        from src.note_builder import generate_single_chapter_note
        from src.chat import _group_first_message, _read_note

        session = self.session
        book_json_path = session.book_json_path
        if not book_json_path or not _Path(book_json_path).is_file():
            return None

        # 读取 chapter_ids 和绑定的原文路径
        hist_path = _Path(session.history_path)
        hist = _json.loads(hist_path.read_text(encoding="utf-8"))
        resolve_session_paths(hist, hist_path.parent)  # chapter_text_paths → 绝对
        chapter_ids = hist.get("chapter_ids") or ([session.chapter_id] if session.chapter_id else [])
        text_paths = hist.get("chapter_text_paths") or []
        if not chapter_ids:
            return None

        # 用 session 绑定的原文路径覆盖 book.json 中的 text_path
        if text_paths and _Path(book_json_path).is_file():
            book_tmp = load_book(book_json_path)
            for ch in book_tmp.get("chapters", []):
                cid = ch.get("chapter_id", "")
                if cid in set(chapter_ids):
                    idx_in_list = chapter_ids.index(cid)
                    if idx_in_list < len(text_paths) and text_paths[idx_in_list]:
                        ch["text_path"] = text_paths[idx_in_list]
            save_book(book_json_path, book_tmp)

        # 调用 note_builder 生成笔记文件
        ok = generate_single_chapter_note(
            book_json_path, chapter_ids, session.provider,
            self._output_language, self._distill_level,
        )
        if not ok:
            return None

        # 重建首条消息
        book = load_book(book_json_path)
        chapters = book.get("chapters") or []
        index = book.get("index") or {}
        book_title = book.get("title", "")
        group_chapters = [c for c in chapters if c.get("chapter_id", "") in set(chapter_ids)]
        if not group_chapters:
            return None

        group = {
            "title": hist.get("chapter_title") or group_chapters[0].get("title", ""),
            "chapters": group_chapters,
        }
        first_msg = _group_first_message(group, book_title, index, book_title)

        # 替换 messages[0]
        session.messages[0]["content"] = first_msg
        session._save_history()
        return first_msg


class _SessionConfigDialog(QDialog):
    """对话配置：笔记 + 数据文件"""

    def __init__(self, session: ChatSession, parent=None):
        super().__init__(parent)
        self.setWindowTitle("对话配置")
        self.setMinimumWidth(520)
        self.session = session

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)

        row = 0

        # 章节原文（book session 才有）
        text_paths = getattr(session, "chapter_text_paths", []) or []
        self._text_edits = []
        if text_paths:
            for i, tp in enumerate(text_paths[:5]):
                label = f"章节原文 {i + 1}:" if len(text_paths) > 1 else "章节原文:"
                grid.addWidget(QLabel(label), row, 0)
                edit = QLineEdit()
                edit.setPlaceholderText("章节 text.md...")
                edit.setText(tp)
                grid.addWidget(edit, row, 1)
                self._text_edits.append(edit)
                btn = QPushButton("浏览")
                btn.setProperty("class", "secondary")
                btn.setFixedWidth(56)
                btn.clicked.connect(lambda checked=False, e=edit: self._browse(e, "章节原文", "Markdown (*.md);;所有文件 (*)"))
                grid.addWidget(btn, row, 2)
                row += 1

        # Notes
        grid.addWidget(QLabel("笔记 (notes.md):"), row, 0)
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("选择笔记文件...")
        self.notes_edit.setText(session.notes_path)
        grid.addWidget(self.notes_edit, row, 1)
        btn_notes = QPushButton("浏览")
        btn_notes.setProperty("class", "secondary")
        btn_notes.setFixedWidth(56)
        btn_notes.clicked.connect(lambda: self._browse(self.notes_edit, "笔记文件", "Markdown (*.md);;所有文件 (*)"))
        grid.addWidget(btn_notes, row, 2)
        row += 1

        # Data JSON (book.json 或章节结构化数据)
        grid.addWidget(QLabel("数据文件 (JSON):"), row, 0)
        self.data_edit = QLineEdit()
        self.data_edit.setPlaceholderText("book.json 或章节结构化 JSON...")
        self.data_edit.setText(session.slides_path)
        grid.addWidget(self.data_edit, row, 1)
        btn_data = QPushButton("浏览")
        btn_data.setProperty("class", "secondary")
        btn_data.setFixedWidth(56)
        btn_data.clicked.connect(lambda: self._browse(self.data_edit, "数据文件", "JSON (*.json)"))
        grid.addWidget(btn_data, row, 2)
        row += 1

        layout.addLayout(grid)

        hint = QLabel("章节原文是笔记生成的来源文件。修改原文路径后重新生成笔记将使用新的原文。")
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def _browse(self, edit: QLineEdit, title: str, filter: str):
        path, _ = QFileDialog.getOpenFileName(self, title, "", filter)
        if path:
            edit.setText(path)

    def get_paths(self) -> tuple:
        text_paths = [e.text().strip() for e in getattr(self, "_text_edits", [])]
        return (
            text_paths,
            self.notes_edit.text().strip(),
            self.data_edit.text().strip(),
        )


class _QuickQuestionsDialog(QDialog):
    """快捷提问内联编辑器"""
    def __init__(self, questions: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑快捷提问")
        self.setMinimumWidth(420)
        self.setMinimumHeight(200)
        layout = QVBoxLayout(self)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(6)
        self._rows = []
        for q in questions:
            self._create_row(q.get("name", ""), q.get("text", ""))

        layout.addLayout(self._rows_layout)

        add_btn = QPushButton("+ 添加快捷提问")
        add_btn.setProperty("class", "secondary")
        add_btn.clicked.connect(self._add_row)
        layout.addWidget(add_btn)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def _create_row(self, name: str, text: str):
        row_layout = QHBoxLayout()
        row_layout.setSpacing(6)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("名称")
        name_edit.setFixedWidth(200)
        text_edit = QLineEdit(text)
        text_edit.setPlaceholderText("提问内容")
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(24, 24)
        del_btn.setProperty("class", "secondary")
        row_layout.addWidget(name_edit)
        row_layout.addWidget(text_edit)
        row_layout.addWidget(del_btn)
        self._rows_layout.addLayout(row_layout)
        row = (name_edit, text_edit, row_layout)
        self._rows.append(row)
        del_btn.clicked.connect(lambda checked=False, r=row: self._remove_row(r))

    def _add_row(self):
        self._create_row("", "")
        self.adjustSize()

    def _remove_row(self, row):
        name_edit, text_edit, row_layout = row
        # 清除该行的所有 widget
        while row_layout.count():
            item = row_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self._rows_layout.removeItem(row_layout)
        if row in self._rows:
            self._rows.remove(row)
        self.adjustSize()

    def get_questions(self) -> list[dict]:
        result = []
        for name_edit, text_edit, _ in self._rows:
            name = name_edit.text().strip()
            text = text_edit.text().strip()
            if name and text:
                result.append({"name": name, "text": text})
        return result


class ChatWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: Optional[ChatSession] = None
        self._worker: Optional[_ChatWorker] = None
        self._provider_config: dict = {}
        self._all_providers: list = []
        self._output_dir: str = ""
        self._theme_colors: Optional[dict] = None
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(150)
        self._thinking_timer.timeout.connect(self._tick_thinking)
        self._thinking_frame = 0
        self._thinking_start = 0.0
        self._thinking_bubble: Optional[MessageBubble] = None
        self._active_worker_session_dir: str = ""
        self._build_ui()

    def _build_ui(self):
        # ─── 左侧：session 列表 ───
        left_panel = QWidget()
        left_panel.setProperty("class", "chat-sidebar")
        left_panel.setMinimumWidth(120)
        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # 顶部按钮行
        top_bar = QWidget()
        top_bar.setProperty("class", "chat-sidebar-top")
        top_bar.setFixedHeight(36)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(8, 4, 8, 4)

        title = QLabel("对话历史")
        title.setProperty("class", "sidebar-title")
        top_layout.addWidget(title)
        top_layout.addStretch()

        self.btn_new_folder = QPushButton("📁")
        self.btn_new_folder.setFixedSize(28, 28)
        self.btn_new_folder.setProperty("class", "secondary")
        self.btn_new_folder.setToolTip("新建文件夹")
        self.btn_new_folder.clicked.connect(self._on_new_folder)
        top_layout.addWidget(self.btn_new_folder)

        self.btn_new_chat = QPushButton("＋")
        self.btn_new_chat.setFixedSize(28, 28)
        self.btn_new_chat.setProperty("class", "secondary")
        self.btn_new_chat.setToolTip("新建对话")
        self.btn_new_chat.clicked.connect(self._on_new_chat)
        top_layout.addWidget(self.btn_new_chat)

        self.btn_import = QPushButton("📥")
        self.btn_import.setFixedSize(28, 28)
        self.btn_import.setProperty("class", "secondary")
        self.btn_import.setToolTip("导入对话 (.vdc)")
        self.btn_import.clicked.connect(self._on_import_sessions)
        top_layout.addWidget(self.btn_import)

        self._show_hidden = False
        self.btn_show_hidden = QPushButton("👁")
        self.btn_show_hidden.setFixedSize(28, 28)
        self.btn_show_hidden.setProperty("class", "secondary")
        self.btn_show_hidden.setToolTip("显示隐藏的对话")
        self.btn_show_hidden.clicked.connect(self._toggle_show_hidden)
        top_layout.addWidget(self.btn_show_hidden)

        left_layout.addWidget(top_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setProperty("class", "chat-sep")
        sep.setFixedHeight(1)
        left_layout.addWidget(sep)

        # 搜索框
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索对话...")
        self._search_edit.setProperty("class", "chat-search")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self._search_edit)

        self.session_tree = _DraggableTreeWidget()
        self.session_tree.setProperty("class", "session-tree")
        self.session_tree.setMinimumWidth(0)
        self.session_tree.setColumnCount(1)
        self.session_tree.setHeaderHidden(True)
        self.session_tree.setIndentation(16)
        self.session_tree.setAnimated(True)
        self.session_tree.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection
        )
        self.session_tree.currentItemChanged.connect(self._on_tree_item_changed)
        self.session_tree.favoriteClicked.connect(self._toggle_favorite)
        self.session_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.session_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.session_tree.itemDoubleClicked.connect(self._on_tree_double_click)
        left_layout.addWidget(self.session_tree, 1)

        # ─── 右侧：聊天区 ───
        right_panel = QWidget()
        right_panel.setProperty("class", "chat-right")
        right_panel.setMinimumWidth(300)
        right_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 状态栏：session 名 + 笔记级别/按钮 + 齿轮
        status_row = QHBoxLayout()
        status_row.setContentsMargins(4, 0, 4, 0)
        status_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 透明按钮样式（同消息操作栏的 复制/重试 风格）
        def _status_btn_ss():
            from src.gui.theme import _current_colors
            c = _current_colors()
            return (
                f"QPushButton {{ background: transparent; border: none; border-radius: 4px;"
                f" padding: 2px 6px; color: {c['text_secondary']}; font-size: 12px; min-height: 0px; }}"
                f" QPushButton:hover {{ background: {c['btn_secondary']}; color: {c['text']}; }}"
            )

        self.status_label = QLabel("选择或新建一个对话")
        self.status_label.setProperty("class", "chat-status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFixedHeight(32)
        status_row.addWidget(self.status_label, 1)

        # 笔记级别（仅 book session 可见，齿轮按钮左侧）
        self._distill_level_combo = QComboBox()
        from src.config import BOOK_DISTILL_LEVELS
        self._distill_level_combo.addItems(BOOK_DISTILL_LEVELS)
        self._distill_level_combo.setFixedWidth(78)
        self._distill_level_combo.setFixedHeight(26)
        self._distill_level_combo.setProperty("class", "chat-model-combo")
        self._distill_level_combo.setVisible(False)
        status_row.addWidget(self._distill_level_combo)

        self.btn_regenerate_note = QPushButton("重新生成笔记")
        self.btn_regenerate_note.setFixedSize(96, 26)
        self.btn_regenerate_note.setStyleSheet(_status_btn_ss())
        self.btn_regenerate_note.setToolTip("以当前级别重新生成章节笔记")
        self.btn_regenerate_note.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_regenerate_note.clicked.connect(self._regenerate_note)
        self.btn_regenerate_note.setVisible(False)
        status_row.addWidget(self.btn_regenerate_note)

        self.btn_config = QPushButton("⚙")
        self.btn_config.setFixedSize(28, 28)
        self.btn_config.setStyleSheet(_status_btn_ss())
        self.btn_config.setToolTip("配置关联文件")
        self.btn_config.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_config.clicked.connect(self._on_config)
        status_row.addWidget(self.btn_config)
        header_balance = QWidget()
        header_balance.setFixedWidth(230)
        status_row.insertWidget(0, header_balance)

        right_layout.addLayout(status_row)

        # 关联文件指示
        self.files_label = QLabel("")
        self.files_label.setProperty("class", "hint")
        self.files_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.files_label.setFixedHeight(22)
        self.files_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files_label.setTextFormat(Qt.TextFormat.RichText)
        self.files_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.files_label.setOpenExternalLinks(False)
        self.files_label.linkActivated.connect(self._on_file_link_activated)
        right_layout.addWidget(self.files_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setProperty("class", "chat-sep")
        sep2.setFixedHeight(1)
        right_layout.addWidget(sep2)

        # 消息列表
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.messages_widget = QWidget()
        self.messages_widget.setProperty("class", "chat-messages")
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.messages_layout.setSpacing(16)
        self.messages_layout.setContentsMargins(20, 16, 20, 16)
        self.messages_layout.addStretch()

        self.scroll.setWidget(self.messages_widget)
        right_layout.addWidget(self.scroll, 1)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setProperty("class", "chat-sep")
        sep3.setFixedHeight(1)
        right_layout.addWidget(sep3)

        # 输入区域
        input_bar = QWidget()
        input_bar.setProperty("class", "chat-input-bar")
        input_layout = QVBoxLayout(input_bar)
        input_layout.setContentsMargins(16, 10, 16, 10)
        input_layout.setSpacing(8)

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("输入你的问题...")
        self.input_edit.setFixedHeight(72)
        self.input_edit.setMaximumHeight(100)
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        self.token_label = QLabel("")
        self.token_label.setProperty("class", "hint")
        bottom_row.addWidget(self.token_label)
        bottom_row.addStretch()

        self.quick_btn = QPushButton("快捷提问")
        self.quick_btn.setProperty("class", "chat-quick-btn")
        self.quick_btn.setFixedWidth(76)
        self.quick_btn.clicked.connect(self._show_quick_menu)
        self.quick_btn.setStyleSheet("padding: 3px 6px;")
        bottom_row.addWidget(self.quick_btn)

        self.quick_edit_btn = QPushButton("✏")
        self.quick_edit_btn.setFixedSize(26, 26)
        self.quick_edit_btn.setProperty("class", "secondary")
        self.quick_edit_btn.setToolTip("编辑快捷提问")
        self.quick_edit_btn.clicked.connect(self._edit_quick_questions)
        bottom_row.addWidget(self.quick_edit_btn)

        self.model_combo = QComboBox()
        self.model_combo.setProperty("class", "chat-model-combo")
        self.model_combo.setMinimumWidth(110)
        self.model_combo.setMaximumWidth(160)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        bottom_row.addWidget(self.model_combo)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedWidth(72)
        self.send_btn.clicked.connect(self._on_send)
        bottom_row.addWidget(self.send_btn)

        input_layout.addLayout(bottom_row)
        right_layout.addWidget(input_bar)

        # 用 Splitter 支持拖拽调整侧边栏宽度
        self.chat_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.chat_splitter.setHandleWidth(5)
        self.chat_splitter.setChildrenCollapsible(False)
        self.chat_splitter.addWidget(left_panel)
        self.chat_splitter.addWidget(right_panel)
        self.chat_splitter.setCollapsible(0, False)
        self.chat_splitter.setCollapsible(1, False)
        self.chat_splitter.setSizes([260, 620])
        self.chat_splitter.setStretchFactor(0, 0)
        self.chat_splitter.setStretchFactor(1, 1)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.chat_splitter)

    # ─── 模型切换 ───

    def set_providers(self, providers: list):
        self._all_providers = [dict(p) for p in providers if p.get("api_key")]
        self._refresh_model_combo()

    def apply_font_settings(self, family: str, scale: int):
        MessageBubble.set_chat_font(family, scale)
        # 刷新已有气泡的字体
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), MessageBubble):
                item.widget()._apply_font()

    def refresh_theme_styles(self, c: dict = None):
        """主题切换后，重新应用所有气泡和操作栏的内联样式"""
        if c is None:
            from src.gui.theme import _current_colors
            c = _current_colors()
        self._theme_colors = c
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, MessageBubble):
                w._apply_bubble_style(c)
                if w._raw_text:
                    w._apply_font()
            elif isinstance(w, _ActionPanel):
                w._refresh_style(c)
            elif isinstance(w, _MessageTimeLabel):
                w._refresh_style(c)
        it = QTreeWidgetItemIterator(self.session_tree)
        while it.value():
            item = it.value()
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "session":
                self._apply_session_star(item, bool(data.get("favorite", False)))
            it.__next__()

    def _refresh_model_combo(self):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for p in self._all_providers:
            self.model_combo.addItem(f"{p.get('name','')}: {p.get('model','')}", p)
        if self._provider_config:
            for i in range(self.model_combo.count()):
                if self.model_combo.itemData(i).get("base_url") == self._provider_config.get("base_url") \
                   and self.model_combo.itemData(i).get("model") == self._provider_config.get("model"):
                    self.model_combo.setCurrentIndex(i)
                    break
        self.model_combo.blockSignals(False)

    def _on_model_changed(self, index: int):
        if index < 0:
            return
        config = self.model_combo.itemData(index)
        if config:
            self._provider_config = config
            if self.session:
                self.session.provider = config
                self.session.base_url = config.get("base_url", "").rstrip("/")
                self.session.api_key = config.get("api_key", "")
                self.session.model = config.get("model", "")

    # ─── Session 列表 ───

    def refresh_session_list(self, provider_config: dict):
        self._provider_config = provider_config
        self._build_session_tree()

    def _build_session_tree(self):
        """重建侧边栏树：文件夹 → 对话"""
        self.session_tree.clear()
        from src.chat import load_folders
        folders = load_folders()
        sessions = list_sessions()
        search_text = self._search_edit.text().strip().lower()

        # 按 folder_id 分组，同时过滤隐藏和搜索
        grouped: dict[str, list] = {}
        ungrouped: list = []
        for s in sessions:
            if s.get("hidden", False) and not self._show_hidden:
                continue
            if search_text and search_text not in s.get("name", "").lower():
                continue
            fid = s.get("folder_id", "")
            if fid:
                grouped.setdefault(fid, []).append(s)
            else:
                ungrouped.append(s)

        def _has_custom_order(items: list) -> bool:
            return any(int(s.get("order", 0) or 0) != 0 for s in items)

        # 创建文件夹节点
        for f in folders:
            fid = f["id"]
            children = grouped.get(fid, [])
            if search_text and not children:
                continue  # 搜索时隐藏空文件夹
            if not _has_custom_order(children):
                children.reverse()  # 没有自定义顺序时，最新的在上面
            folder_item = QTreeWidgetItem(self.session_tree, [f["name"]])
            folder_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "folder", "folder_id": fid})
            folder_item.setExpanded(True)
            font = folder_item.font(0)
            font.setBold(True)
            folder_item.setFont(0, font)
            for s in children:
                self._add_session_item(folder_item, s)

        # 未分组
        if ungrouped or (not folders and not search_text):
            if not _has_custom_order(ungrouped):
                ungrouped.reverse()
            ungrouped_item = QTreeWidgetItem(self.session_tree, ["未分组"])
            ungrouped_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "folder", "folder_id": ""})
            ungrouped_item.setExpanded(True)
            font = ungrouped_item.font(0)
            font.setBold(True)
            ungrouped_item.setFont(0, font)
            for s in ungrouped:
                self._add_session_item(ungrouped_item, s)

    def _on_search_changed(self, text: str):
        self._build_session_tree()

    def _add_session_item(self, parent: QTreeWidgetItem, s: dict):
        from PySide6.QtGui import QColor
        rounds_str = f" ({s['rounds']}轮)" if s["rounds"] > 0 else ""
        label = f"{s['name']}{rounds_str}"
        favorite = bool(s.get("favorite", False))
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, Qt.ItemDataRole.UserRole, {"type": "session", **s, "display_label": label})
        self._apply_session_star(item, favorite)
        if s.get("hidden", False):
            item.setForeground(0, QColor(120, 120, 120) if self._is_dark_theme() else QColor(170, 170, 170))

    def _apply_session_star(self, item: QTreeWidgetItem, favorite: bool):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        item.setText(0, data.get("display_label", data.get("name", "")))
        item.setIcon(0, self._star_icon(favorite))
        item.setToolTip(0, "")
        if data.get("hidden", False):
            from PySide6.QtGui import QColor
            item.setForeground(
                0,
                QColor(120, 120, 120) if self._is_dark_theme() else QColor(170, 170, 170),
            )
        else:
            item.setForeground(0, self.palette().text())

    @staticmethod
    def _star_icon(favorite: bool):
        from math import cos, pi, sin
        from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap, QColor, QPen

        pix = QPixmap(16, 16)
        pix.fill(Qt.GlobalColor.transparent)
        path = QPainterPath()
        cx, cy = 8.0, 8.3
        outer, inner = 6.8, 3.0
        for i in range(10):
            angle = -pi / 2 + i * pi / 5
            radius = outer if i % 2 == 0 else inner
            x = cx + cos(angle) * radius
            y = cy + sin(angle) * radius
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()

        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#f5b301" if favorite else "#a1a1a6")
        painter.setPen(QPen(color, 1.4))
        if favorite:
            painter.fillPath(path, color)
        painter.drawPath(path)
        painter.end()
        return QIcon(pix)

    def _is_dark_theme(self) -> bool:
        from src.config import load_settings
        return load_settings().theme == "dark"

    def _toggle_show_hidden(self):
        self._show_hidden = not self._show_hidden
        if self._show_hidden:
            self.btn_show_hidden.setText("👁‍🗨")
            self.btn_show_hidden.setToolTip("隐藏已隐藏的对话")
        else:
            self.btn_show_hidden.setText("👁")
            self.btn_show_hidden.setToolTip("显示隐藏的对话")
        self._build_session_tree()

    def _on_tree_item_changed(self, current: QTreeWidgetItem, _prev):
        if not current:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "session":
            return
        info = data
        session_dir = info["session_dir"]
        self.session = ChatSession(session_dir, self._provider_config)
        self.session._load_history()

        # 推导 base_dir：导入的 session 图片在 session_dir/images/ 下
        base_dir = ""
        session_dir_path = Path(session_dir)
        if (session_dir_path / "images").is_dir():
            base_dir = str(session_dir_path)
        elif self.session.notes_path and os.path.exists(self.session.notes_path):
            base_dir = str(Path(self.session.notes_path).parent.parent)
        elif self.session.slides_path and os.path.exists(self.session.slides_path):
            base_dir = str(Path(self.session.slides_path).parent)
        MessageBubble.set_base_dir(base_dir)

        n_msgs = sum(1 for m in self.session.messages if m.get("role") == "user")
        self.status_label.setText(f"{self.session.name} | {self.session.model} | {n_msgs} 轮")
        self._update_files_label()
        self._restore_history()

        # 笔记级别行：仅 book session 显示
        is_book = bool(self.session and self.session.book_json_path)
        self._distill_level_combo.setVisible(is_book)
        self.btn_regenerate_note.setVisible(is_book)
        if is_book:
            from src.config import load_settings
            self._distill_level_combo.setCurrentText(
                load_settings().book_distill_level or "high")

    def _update_files_label(self):
        parts = []
        if self.session:
            if self.session.notes_path and os.path.exists(self.session.notes_path):
                parts.append("notes ✓")
            else:
                parts.append("notes ✗")
            if self.session.slides_path and os.path.exists(self.session.slides_path):
                parts.append("数据 ✓")
            else:
                parts.append("数据 ✗")
        self.files_label.setText("  |  ".join(parts))

    def _on_files_label_click(self, event):
        if not self.session:
            return
        import subprocess
        if self.session.notes_path and os.path.exists(self.session.notes_path):
            os.startfile(self.session.notes_path)
        elif self.session.slides_path and os.path.exists(self.session.slides_path):
            os.startfile(self.session.slides_path)

    def _update_files_label(self):
        def link(key: str, label: str, ok: bool) -> str:
            mark = "✓" if ok else "✗"
            color = "#8e8e93"
            if ok:
                return f'<a href="{key}" style="color:{color}; text-decoration:none;">{label} {mark}</a>'
            return f'<span style="color:{color};">{label} {mark}</span>'

        parts = []
        if self.session:
            notes_ok = bool(self.session.notes_path and os.path.exists(self.session.notes_path))
            text_paths = getattr(self.session, "chapter_text_paths", []) or []
            data_ok = any(p and os.path.exists(p) for p in text_paths)
            if not data_ok:
                data_ok = bool(self.session.slides_path and os.path.exists(self.session.slides_path))
            book_ok = bool(self.session.book_json_path and os.path.exists(self.session.book_json_path))
            parts.append(link("notes", "笔记", notes_ok))
            parts.append(link("data", "原文", data_ok))
            parts.append(link("book", "索引", book_ok))
        self.files_label.setText("  |  ".join(parts))

    def _on_file_link_activated(self, key: str):
        if not self.session:
            return
        if key == "notes" and self.session.notes_path and os.path.exists(self.session.notes_path):
            os.startfile(self.session.notes_path)
        elif key == "data":
            text_paths = getattr(self.session, "chapter_text_paths", []) or []
            target = next((p for p in text_paths if p and os.path.exists(p)), "")
            if target:
                os.startfile(target)
            elif self.session.slides_path and os.path.exists(self.session.slides_path):
                os.startfile(self.session.slides_path)
        elif key == "book" and self.session.book_json_path and os.path.exists(self.session.book_json_path):
            os.startfile(self.session.book_json_path)

    def _on_tree_context_menu(self, pos):
        item = self.session_tree.itemAt(pos)
        menu = QMenu(self)

        if not item:
            # 空白处：新建文件夹 + 导入对话
            menu.addAction("新建文件夹", self._on_new_folder)
            menu.addSeparator()
            menu.addAction("导入对话...", self._on_import_sessions)
            menu.exec(self.session_tree.mapToGlobal(pos))
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)

        if data.get("type") == "folder":
            folder_id = data.get("folder_id", "")
            if folder_id:  # 非默认"未分组"
                menu.addAction("重命名", lambda: self._rename_folder(folder_id, item))
                menu.addAction("反转排序", lambda: self._reverse_folder_order(item))
                menu.addSeparator()
                if folder_id.startswith("book_"):
                    menu.addAction("重建章节对话", lambda: self._rebuild_book_sessions(folder_id))
                menu.addAction("删除文件夹", lambda: self._delete_folder(folder_id))
                if folder_id.startswith("book_"):
                    menu.addAction(
                        "删除书籍输出、缓存与对话",
                        lambda: self._delete_book_output_and_sessions(folder_id),
                    )
                    menu.addSeparator()
                    menu.addAction("导出至 PDF", lambda: self._export_book_pdf(folder_id))
                    menu.addAction("导出书籍对话包...", lambda: self._export_book_package(folder_id))
            else:
                menu.addAction("新建文件夹", self._on_new_folder)
                menu.addAction("反转排序", lambda: self._reverse_folder_order(item))
            menu.exec(self.session_tree.mapToGlobal(pos))
            return

        # Session 项（支持多选）
        selected = self.session_tree.selectedItems()
        session_items = [si for si in selected
                         if si.data(0, Qt.ItemDataRole.UserRole).get("type") == "session"]

        if not session_items:
            return

        # 移动到子菜单
        from src.chat import load_folders
        folders = load_folders()
        if folders:
            move_menu = menu.addMenu("移动到")
            for f in folders:
                move_menu.addAction(f["name"],
                                    lambda checked=False, fid=f["id"]: self._move_to_folder(session_items, fid))
            move_menu.addAction("未分组",
                                lambda checked=False: self._move_to_folder(session_items, ""))

        # 移到顶部/底部（仅单个 session 时）
        if len(session_items) == 1:
            parent = session_items[0].parent()
            if parent and parent.childCount() > 1:
                menu.addAction("移到顶部", lambda: self._move_to_edge(session_items[0], parent, "top"))
                menu.addAction("移到底部", lambda: self._move_to_edge(session_items[0], parent, "bottom"))

            # 重新生成笔记（仅 book session）
            s_data = session_items[0].data(0, Qt.ItemDataRole.UserRole)
            s_dir = s_data.get("session_dir", "")
            hfile = os.path.join(s_dir, "chat_history.json")
            try:
                hist = json.loads(Path(hfile).read_text(encoding="utf-8"))
                if hist.get("book_id"):
                    menu.addSeparator()
                    menu.addAction("重新生成笔记", lambda: self._regenerate_note_from_tree(session_items[0]))
            except Exception:
                pass

        # 批量删除
        n = len(session_items)
        label = f"删除选中的 {n} 个对话" if n > 1 else "删除此对话"
        menu.addAction(label, lambda: self._delete_sessions(session_items))

        # 隐藏/取消隐藏
        hidden_items = [si for si in session_items
                        if si.data(0, Qt.ItemDataRole.UserRole).get("hidden", False)]
        visible_items = [si for si in session_items if si not in hidden_items]
        if visible_items:
            h_label = f"隐藏选中的 {len(visible_items)} 个对话" if len(visible_items) > 1 else "隐藏此对话"
            menu.addAction(h_label, lambda: self._toggle_hidden(visible_items, True))
        if hidden_items:
            u_label = f"取消隐藏 {len(hidden_items)} 个对话" if len(hidden_items) > 1 else "取消隐藏此对话"
            menu.addAction(u_label, lambda: self._toggle_hidden(hidden_items, False))

        # 导出
        menu.addSeparator()
        exp_label = f"导出选中的 {n} 个对话..." if n > 1 else "导出此对话..."
        menu.addAction(exp_label, lambda: self._on_export_sessions(session_items))

        menu.exec(self.session_tree.mapToGlobal(pos))

    def _on_new_folder(self):
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not ok or not name.strip():
            return
        from src.chat import load_folders, save_folders
        folders = load_folders()
        fid = f"f{len(folders) + 1}_{int(datetime.now().timestamp())}"
        folders.append({"id": fid, "name": name.strip(), "order": len(folders)})
        save_folders(folders)
        self._build_session_tree()

    def _on_export_sessions(self, items: list):
        """导出选中的对话为 .vdc 文件"""
        from src.session_io import export_sessions
        session_ids = []
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data.get("type") == "session":
                session_ids.append(data["session_id"])
        if not session_ids:
            return

        dest, _ = QFileDialog.getSaveFileName(
            self, "导出对话", "", "Book-Distiller 对话包 (*.bdc);;兼容对话包 (*.vdc)"
        )
        if not dest:
            return
        if not dest.endswith((".bdc", ".vdc")):
            dest += ".bdc"

        try:
            ok = export_sessions(session_ids, dest)
            if ok:
                n = len(session_ids)
                self.status_label.setText(f"已导出 {n} 个对话")
            else:
                self.status_label.setText("导出失败：没有可导出的对话")
        except Exception as e:
            self.status_label.setText(f"导出失败：{e}")

    def _on_import_sessions(self):
        """从 .vdc/.bdc 文件导入对话"""
        from src.session_io import import_sessions
        import zipfile

        path, _ = QFileDialog.getOpenFileName(
            self, "导入对话", "", "Book-Distiller 对话包 (*.bdc *.vdc)"
        )
        if not path:
            return

        # 检测是否为书籍包（需要选择输出目录）
        output_dir = ""
        try:
            with zipfile.ZipFile(path, "r") as zf:
                meta = json.loads(zf.read("export_meta.json"))
                is_book = meta.get("type") == "book" or any(
                    n.startswith("book_dir/") for n in zf.namelist()
                )
        except Exception:
            is_book = False

        if is_book:
            output_dir = QFileDialog.getExistingDirectory(
                self, "选择书籍数据输出目录", ""
            )
            if not output_dir:
                return

        try:
            new_ids = import_sessions(path, output_dir=output_dir)
            if new_ids:
                self._build_session_tree()
                self.status_label.setText(f"已导入 {len(new_ids)} 个对话")
            else:
                self.status_label.setText("导入失败：文件中没有可导入的对话")
        except Exception as e:
            self.status_label.setText(f"导入失败：{e}")

    def _rename_folder(self, folder_id: str, item: QTreeWidgetItem):
        old_name = item.text(0)
        name, ok = QInputDialog.getText(self, "重命名文件夹", "新名称：", text=old_name)
        if not ok or not name.strip():
            return
        from src.chat import load_folders, save_folders
        folders = load_folders()
        for f in folders:
            if f["id"] == folder_id:
                f["name"] = name.strip()
                break
        save_folders(folders)
        self._build_session_tree()

    def _reverse_folder_order(self, folder_item: QTreeWidgetItem):
        """反转文件夹内对话的排列顺序"""
        children = [folder_item.child(i) for i in range(folder_item.childCount())]
        if len(children) < 2:
            return
        children.reverse()
        self.session_tree.setUpdatesEnabled(False)
        for i in range(folder_item.childCount()):
            folder_item.takeChild(0)
        for child in children:
            folder_item.addChild(child)
        self.session_tree.setUpdatesEnabled(True)
        self.session_tree._persist_order()

    def _get_session_granularity(self) -> str:
        try:
            from src.config import load_settings
            return load_settings().book_session_granularity or "level2"
        except Exception:
            return "level2"

    def _rebuild_book_sessions(self, folder_id: str):
        """重建书籍文件夹下的所有章节对话（从已有笔记刷新）"""
        from src.chat import _load_meta, _SESSIONS_DIR
        import json

        # 从 book_ 前缀提取 book_id，查找 book.json
        book_id = folder_id[5:] if folder_id.startswith("book_") else ""
        if not book_id:
            return

        # 书文件夹 = sessions/<folder_id>/，book.json 就在其中
        book_json_path = _SESSIONS_DIR / folder_id / "book.json"

        if not book_json_path.is_file():
            # 没有已有对话可参考，让用户手动选择 book.json
            from PySide6.QtWidgets import QFileDialog
            bjp, _ = QFileDialog.getOpenFileName(
                self, "选择 book.json", "",
                "book.json (book.json);;所有文件 (*)"
            )
            if not bjp:
                return
            book_json_path = bjp

        from src.chat import create_book_sessions
        try:
            session_ids = create_book_sessions(
                book_json_path,
                provider_config={},
                session_granularity=self._get_session_granularity(),
            )
            self.refresh_session_list({})
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "重建完成", f"已重建 {len(session_ids)} 个对话。")
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "重建失败", str(exc))

    def _delete_folder(self, folder_id: str):
        """删除文件夹及其下所有对话 session"""
        from src.chat import load_folders, save_folders, _load_meta, _save_meta, _SESSIONS_DIR

        # 收集该文件夹下的所有 session id
        meta = _load_meta()
        folder_session_ids = [sid for sid, m in meta.items() if m.get("folder_id") == folder_id]
        folder_name = ""
        for f in load_folders():
            if f["id"] == folder_id:
                folder_name = f.get("name", folder_id)
                break

        n = len(folder_session_ids)
        msg = f"确定删除文件夹「{folder_name}」及其下的 {n} 个对话？" if n else f"确定删除空文件夹「{folder_name}」？"
        reply = QMessageBox.question(
            self, "删除文件夹", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 删除 session 目录
        import shutil
        for sid in folder_session_ids:
            session_dir = _SESSIONS_DIR / sid
            if session_dir.is_dir():
                try:
                    shutil.rmtree(str(session_dir))
                except Exception:
                    pass
            meta.pop(sid, None)

        # 删除文件夹记录
        folders = load_folders()
        folders = [f for f in folders if f["id"] != folder_id]
        save_folders(folders)
        _save_meta(meta)

        # 如果当前正在看这个文件夹里的对话，清空
        if self.session and getattr(self.session, "folder_id", "") == folder_id:
            self.session = None
            self._clear_messages()
            self.status_label.setText("文件夹已删除")

        self._build_session_tree()

    def _delete_book_output_and_sessions(self, folder_id: str):
        reply = QMessageBox.question(
            self,
            "删除书籍输出、缓存与对话",
            "将删除该书的输出目录、缓存、章节对话、全书总览对话，以及左侧书籍文件夹。\n\n"
            "这个操作用于彻底重跑一本书；删除后需要重新蒸馏才能恢复。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from src.chat import delete_book_output_and_sessions
            result = delete_book_output_and_sessions(folder_id)
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return

        if self.session and getattr(self.session, "folder_id", "") == folder_id:
            self.session = None
            self._clear_messages()
            self.status_label.setText("已删除书籍输出与对话，请重新蒸馏")

        self._build_session_tree()
        QMessageBox.information(
            self,
            "删除完成",
            f"已删除书籍输出目录，并移除 {result.get('sessions', 0)} 个对话。\n\n"
            "现在可以回到批量蒸馏页重新运行。",
        )

    def _export_book_pdf(self, folder_id: str):
        """从右键菜单导出书籍笔记和对话为 PDF"""
        from src.gui.book_pdf_export import export_book_pdf
        export_book_pdf(self, folder_id)

    def _export_book_package(self, folder_id: str):
        """从右键菜单导出书籍对话包（.bdc），包含完整书籍数据+对话。"""
        from PySide6.QtWidgets import QFileDialog
        from src.session_io import export_book
        from src.chat import _load_meta, _get_meta

        # 获取书名作为默认文件名
        sess_meta = _load_meta()
        first_sid = None
        for sid, info in sess_meta.items():
            if info.get("folder_id") == folder_id:
                first_sid = sid
                break
        book_title = "书籍"
        if first_sid:
            hfile = Path.home() / ".Book-Distiller" / "sessions" / first_sid / "chat_history.json"
            if hfile.is_file():
                try:
                    import json
                    data = json.loads(hfile.read_text(encoding="utf-8"))
                    book_title = data.get("book_title", "书籍")
                except Exception:
                    pass

        default_name = f"{book_title}.bdc"
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出书籍对话包", default_name,
            "Book-Distiller 对话包 (*.bdc)"
        )
        if not dest:
            return

        try:
            ok = export_book(folder_id, dest)
            if ok:
                self.status_label.setText(f"书籍对话包已导出: {dest}")
            else:
                self.status_label.setText("导出失败：没有可导出的对话或书籍数据")
        except Exception as e:
            self.status_label.setText(f"导出失败: {e}")

    def _move_to_folder(self, items: list, folder_id: str):
        from src.chat import _load_meta, _save_meta, _get_meta
        meta = _load_meta()
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data.get("type") != "session":
                continue
            _get_meta(meta, data["session_id"])["folder_id"] = folder_id
        _save_meta(meta)
        self._build_session_tree()

    def _move_to_edge(self, item: QTreeWidgetItem, parent: QTreeWidgetItem, edge: str):
        """将 session 移到当前文件夹的顶部或底部"""
        children = [parent.child(i) for i in range(parent.childCount())]
        if item not in children:
            return
        children.remove(item)
        if edge == "top":
            children.insert(0, item)
        else:
            children.append(item)

        self.session_tree.setUpdatesEnabled(False)
        for i in range(parent.childCount()):
            parent.takeChild(0)
        for child in children:
            parent.addChild(child)
        self.session_tree.setUpdatesEnabled(True)

        self.session_tree._persist_order()
        self.session_tree.setCurrentItem(item)

    def _toggle_favorite(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "session":
            return
        favorite = not bool(data.get("favorite", False))
        from src.chat import set_session_favorite
        set_session_favorite(data["session_id"], favorite)
        data["favorite"] = favorite
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._apply_session_star(item, favorite)

    def _delete_sessions(self, items: list):
        import shutil
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data.get("type") != "session":
                continue
            session_dir = data["session_dir"]
            if self.session and self.session.session_dir == session_dir:
                self.session = None
                self._clear_messages()
                self.status_label.setText("选择或新建一个对话")
                self.files_label.setText("")
            shutil.rmtree(session_dir, ignore_errors=True)
        self._build_session_tree()

    def _toggle_hidden(self, items: list, hide: bool):
        from src.chat import toggle_session_hidden
        session_ids = []
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data.get("type") == "session":
                session_ids.append(data["session_id"])
        if session_ids:
            toggle_session_hidden(session_ids)
            self._build_session_tree()

    def _on_tree_double_click(self, item: QTreeWidgetItem, col: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "session":
            return
        old_name = data.get("name", "")
        name, ok = QInputDialog.getText(self, "重命名对话", "新名称：", text=old_name)
        if not ok or not name.strip() or name.strip() == old_name:
            return
        from src.chat import rename_session
        rename_session(data["session_id"], name.strip())
        data["name"] = name.strip()
        rounds_str = f" ({data.get('rounds', 0)}轮)" if data.get("rounds", 0) > 0 else ""
        data["display_label"] = f"{name.strip()}{rounds_str}"
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._apply_session_star(item, bool(data.get("favorite", False)))
        if self.session and self.session.session_dir == data.get("session_dir"):
            self.session.name = name.strip()
            n_msgs = sum(1 for m in self.session.messages if m.get("role") == "user")
            self.status_label.setText(f"{self.session.name} | {self.session.model} | {n_msgs} 轮")

    # ─── 新建对话 ───

    def _on_new_chat(self):
        session = create_empty_session(self._provider_config)
        self.session = session

        self._build_session_tree()
        self._select_session_in_tree(session.session_dir)

        self.status_label.setText(f"{session.name} | 点击 ⚙ 配置文件")
        self.files_label.setText("notes ✗  |  数据 ✗")
        self._clear_messages()

    def _select_session_in_tree(self, session_dir: str):
        """在树中选中指定 session"""
        it = QTreeWidgetItemIterator(self.session_tree)
        while it.value():
            item = it.value()
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "session" and data.get("session_dir") == session_dir:
                self.session_tree.setCurrentItem(item)
                return
            it.__next__()

    def _select_first_session(self):
        """选中树中第一个 session"""
        it = QTreeWidgetItemIterator(self.session_tree)
        while it.value():
            item = it.value()
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "session":
                self.session_tree.setCurrentItem(item)
                return
            it.__next__()

    # ─── 齿轮配置 ───

    def _on_config(self):
        if not self.session:
            self.status_label.setText("请先选择或新建一个对话")
            return

        dlg = _SessionConfigDialog(self.session, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        text_paths, notes_path, data_path = dlg.get_paths()
        if text_paths:
            self.session.chapter_text_paths = text_paths
            self.session._save_history()
        self.session.update_files(notes_path, data_path)

        # 刷新 UI
        self._update_files_label()
        self._restore_history()
        self._refresh_session_name()

        n_msgs = sum(1 for m in self.session.messages if m.get("role") == "user")
        self.status_label.setText(f"{self.session.name} | {self.session.model} | {n_msgs} 轮")

    def _refresh_session_name(self):
        item = self.session_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "session":
            return
        rounds = sum(1 for m in self.session.messages if m.get("role") == "user") if self.session else 0
        name = self.session.name if self.session else data.get("name", "")
        label = f"{name} ({rounds}轮)" if rounds > 0 else name
        data["display_label"] = label
        data["name"] = name
        if self.session:
            data["notes_path"] = self.session.notes_path
            data["slides_path"] = self.session.slides_path
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._apply_session_star(item, bool(data.get("favorite", False)))

    # ─── 外部接口（Step 5 跳转） ───

    def init_session(self, project_dir: str, provider_config: dict,
                     notes_path: str = "", slides_path: str = "",
                     transcript_path: str = ""):
        from src.chat import create_session
        self._provider_config = provider_config
        output_dir = str(Path(project_dir).parent)
        self._output_dir = output_dir

        video_name = os.path.basename(project_dir)
        session = create_session(
            project_dir, video_name, notes_path, provider_config,
        )
        self.session = session

        self._build_session_tree()
        self._select_session_in_tree(session.session_dir)

    # ─── 消息 ───

    def _restore_history(self):
        self._clear_messages()
        if not self.session:
            return
        for idx, msg in enumerate(self.session.messages):
            self._add_bubble(msg["role"], msg["content"], idx,
                             feedback=msg.get("feedback"),
                             created_at=msg.get("created_at"))
        if self._is_active_worker_session_current():
            self._show_thinking_bubble(restart_timer=False)

    def _fallback_message_time(self) -> str:
        return self.session.created_at if self.session else ""

    @staticmethod
    def _format_message_time(value: str) -> str:
        if not value:
            return ""
        value = str(value).strip()
        for fmt, size in (
            ("%Y-%m-%d %H:%M:%S", 19),
            ("%Y-%m-%dT%H:%M:%S", 19),
            ("%Y%m%d_%H%M%S", 15),
        ):
            try:
                return datetime.strptime(value[:size], fmt).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        return value

    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == event.Type.KeyPress:
            key = event.key()
            mod = event.modifiers()
            if key == Qt.Key.Key_Return and not (mod & Qt.KeyboardModifier.ShiftModifier or mod & Qt.KeyboardModifier.ControlModifier):
                self._on_send()
                return True
            if key == Qt.Key.Key_Escape:
                self._on_cancel_send()
                return True
        return super().eventFilter(obj, event)

    def _show_quick_menu(self):
        menu = QMenu(self)
        from src.config import load_settings
        questions = load_settings().quick_questions
        for q in questions:
            name = q.get("name", "")
            text = q.get("text", "")
            if name and text:
                action = menu.addAction(name)
                action.setData(text)
        if menu.actions():
            menu.triggered.connect(self._on_quick_question)
            menu.exec(self.quick_btn.mapToGlobal(self.quick_btn.rect().bottomLeft()))

    def _on_quick_question(self, action):
        text = action.data()
        if not text:
            return
        current = self.input_edit.toPlainText().strip()
        if current:
            self.input_edit.setPlainText(current + "\n" + text)
        else:
            self.input_edit.setPlainText(text)
        self.input_edit.setFocus()

    def _edit_quick_questions(self):
        from src.config import load_settings, save_settings
        settings = load_settings()
        dlg = _QuickQuestionsDialog(settings.quick_questions, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            settings.quick_questions = dlg.get_questions()
            save_settings(settings)

    def _on_send(self):
        if not self.session:
            return
        # 如果正在等待回复，点击按钮则取消
        if self._worker and self._worker.isRunning():
            self._on_cancel_send()
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        self.input_edit.clear()
        msg_index = len(self.session.messages)
        user_entry = self.session.add_user_message(text)
        self._add_bubble(
            "user",
            text,
            msg_index,
            created_at=user_entry.get("created_at", ""),
        )

        self.send_btn.setText("取消")
        self.send_btn.clicked.disconnect()
        self.send_btn.clicked.connect(self._on_cancel_send)
        self.input_edit.setEnabled(False)
        self.model_combo.setEnabled(False)
        self._active_worker_session_dir = self.session.session_dir

        self._show_thinking_bubble()

        self._worker = _ChatWorker(self.session, text)
        self._worker._method = "reply_to_last_user"
        self._worker.finished.connect(self._on_reply)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _is_session_current(self, session_dir: str) -> bool:
        return bool(self.session and self.session.session_dir == session_dir)

    def _is_active_worker_session_current(self) -> bool:
        return bool(self._active_worker_session_dir and self._is_session_current(self._active_worker_session_dir))

    def _show_thinking_bubble(self, restart_timer: bool = True):
        if self._thinking_bubble:
            return
        self._thinking_bubble = MessageBubble("assistant", "")
        self._insert_widget(self._thinking_bubble)
        if restart_timer:
            self._thinking_start = __import__("time").time()
            self._thinking_frame = 0
        if not self._thinking_timer.isActive():
            self._thinking_timer.start()

    def _on_cancel_send(self):
        if not self._worker or not self._worker.isRunning():
            return
        self._worker._cancel = True
        self._stop_thinking()
        if self._is_active_worker_session_current():
            # 显示取消占位（不写入 session.messages，刷新后消失）
            idx = self.messages_layout.count() - 1
            placeholder = MessageBubble("assistant", "（已取消生成）", idx)
            placeholder.setStyleSheet("color: #888; font-style: italic;")
            self._insert_widget(placeholder)
        self._restore_send_btn()
        self._active_worker_session_dir = ""
        self.status_label.setText("已取消")

    def _tick_thinking(self):
        if not self._thinking_bubble:
            return
        import time
        elapsed = time.time() - self._thinking_start
        s = int(elapsed)
        t = f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"
        frame = _THINKING_FRAMES[self._thinking_frame % len(_THINKING_FRAMES)]
        self._thinking_frame += 1
        self._thinking_bubble.setText(f"{frame} 思考中... {t}")
        self._scroll_to_bottom()

    def _stop_thinking(self):
        self._thinking_timer.stop()
        if self._thinking_bubble:
            idx = self.messages_layout.indexOf(self._thinking_bubble)
            if idx >= 0:
                self.messages_layout.takeAt(idx)
            self._thinking_bubble.setParent(None)
            self._thinking_bubble.deleteLater()
            self._thinking_bubble = None

    def _on_reply(self, reply: str, total_chars: int):
        worker = self._worker
        finished_session = worker.session if worker else self.session
        self._stop_thinking()
        is_current = bool(finished_session and self._is_session_current(finished_session.session_dir))
        self._active_worker_session_dir = ""
        if is_current:
            self.session = finished_session
            self._restore_history()
        self._restore_send_btn()
        if is_current:
            self.input_edit.setFocus()

        import time
        elapsed = time.time() - self._thinking_start
        n_msgs = sum(1 for m in finished_session.messages if m.get("role") == "user") if finished_session else 0
        if is_current:
            self.status_label.setText(
                f"{finished_session.name} | {finished_session.model} | {n_msgs} 轮"
            )
        self.token_label.setText(f"~{total_chars} chars | {elapsed:.1f}s")
        if finished_session:
            self._update_session_item_rounds(finished_session.session_dir, n_msgs)

    def _on_error(self, err: str):
        self._stop_thinking()
        if self._is_active_worker_session_current():
            self._add_bubble("assistant", f"[错误] {err}")
        self._restore_send_btn()
        self._active_worker_session_dir = ""
        self.status_label.setText(f"请求失败: {err[:60]}")

    # ─── 笔记重新生成 ───

    def _regenerate_note(self):
        """从顶部按钮触发，重新生成当前对话的章节笔记。"""
        if not self.session or not self.session.book_json_path:
            return
        if self._worker and self._worker.isRunning():
            return

        from src.config import load_settings
        settings = load_settings()
        level = self._distill_level_combo.currentText()
        output_lang = getattr(settings, "book_output_language", "中文") or "中文"

        # 锁定 UI + thinking 状态
        self.btn_regenerate_note.setEnabled(False)
        self.btn_regenerate_note.setText("生成中...")
        self.input_edit.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._active_worker_session_dir = self.session.session_dir

        self._thinking_bubble = MessageBubble("assistant", "")
        self._insert_widget(self._thinking_bubble)
        self._thinking_start = __import__("time").time()
        self._thinking_frame = 0
        self._thinking_timer.start()
        self.status_label.setText("正在重新生成笔记...")

        worker = _ChatWorker(self.session, "")
        worker._method = "regenerate_note"
        worker._distill_level = level
        worker._output_language = output_lang
        worker.finished.connect(self._on_note_regenerated)
        worker.error.connect(self._on_note_regen_error)
        self._worker = worker
        worker.start()

    def _regenerate_note_from_tree(self, item):
        """从右键菜单触发：先选中该对话，再触发重新生成。"""
        self.session_tree.setCurrentItem(item)
        # 等待 session 加载后触发
        QTimer.singleShot(50, self._regenerate_note)

    def _on_note_regenerated(self, reply: str, total_chars: int):
        worker = self._worker
        finished_session = worker.session if worker else self.session
        self._stop_thinking()
        is_current = bool(finished_session and self._is_session_current(finished_session.session_dir))
        if is_current:
            self.session = finished_session
            self._restore_history()
        self._restore_regen_btn()
        self._restore_send_btn()
        self._active_worker_session_dir = ""
        import time
        elapsed = time.time() - self._thinking_start
        n_msgs = sum(1 for m in finished_session.messages if m.get("role") == "user") if finished_session else 0
        if is_current:
            self.status_label.setText(
                f"{finished_session.name} | {finished_session.model} | {n_msgs} 轮"
            )
        if finished_session:
            self._update_session_item_rounds(finished_session.session_dir, n_msgs)
        self.token_label.setText(f"笔记已重新生成 | {elapsed:.1f}s")

    def _on_note_regen_error(self, err: str):
        self._stop_thinking()
        self._restore_regen_btn()
        self._restore_send_btn()
        self._active_worker_session_dir = ""
        self.status_label.setText(f"笔记生成失败: {err[:80]}")

    def _restore_regen_btn(self):
        self.btn_regenerate_note.setEnabled(True)
        self.btn_regenerate_note.setText("重新生成")

    def _restore_send_btn(self):
        self.send_btn.setText("发送")
        try:
            self.send_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.send_btn.clicked.connect(self._on_send)
        self.send_btn.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.model_combo.setEnabled(True)
        self.session_tree.setEnabled(True)

    # ─── 消息操作 ───

    def _on_msg_action(self, action: str, msg_index: int):
        if not self.session or msg_index < 0 or msg_index >= len(self.session.messages):
            return
        if self._worker and self._worker.isRunning():
            return
        getattr(self, f"_msg_{action}")(msg_index)

    def _get_bubble_at(self, msg_index: int):
        """通过 msg_index 找到对应的 MessageBubble"""
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, MessageBubble) and w._msg_index == msg_index:
                return w
        return None

    def _remove_widgets_from(self, start_index: int):
        """移除 msg_index >= start_index 的所有 MessageBubble 和 _ActionPanel"""
        to_remove = []
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, MessageBubble) and w._msg_index >= start_index:
                to_remove.append(w)
            elif isinstance(w, (_ActionPanel, _MessageTimeLabel)) and w._msg_index >= start_index:
                to_remove.append(w)
        for w in to_remove:
            self.messages_layout.removeWidget(w)
            w.setParent(None)
            w.deleteLater()

    def _msg_copy(self, msg_index: int):
        b = self._get_bubble_at(msg_index)
        if b:
            QApplication.clipboard().setText(b._raw_text)

    def _start_async_generation(self, method: str, message: str = ""):
        """启动异步生成（regenerate/edit_and_regenerate），统一 UI 状态"""
        self.send_btn.setText("取消")
        try:
            self.send_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.send_btn.clicked.connect(self._on_cancel_send)
        self.input_edit.setEnabled(False)
        self.model_combo.setEnabled(False)
        self._active_worker_session_dir = self.session.session_dir
        self._thinking_bubble = MessageBubble("assistant", "")
        self._insert_widget(self._thinking_bubble)
        self._thinking_start = __import__("time").time()
        self._thinking_frame = 0
        self._thinking_timer.start()
        worker = _ChatWorker(self.session, message)
        worker._method = method
        worker.finished.connect(self._on_reply)
        worker.error.connect(self._on_error)
        self._worker = worker
        worker.start()

    def _msg_edit(self, msg_index: int):
        bubble = self._get_bubble_at(msg_index)
        if not bubble or bubble._role != "user":
            return

        bubble._action_panel.hide()
        edit = QTextEdit()
        edit.setProperty("class", "msg-edit")
        edit.setPlainText(bubble._raw_text)
        edit.setFixedHeight(min(max(bubble.height(), 60), 150))

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(8)
        btn_confirm = QPushButton("确认编辑")
        btn_confirm.setFixedWidth(80)
        btn_cancel = QPushButton("取消")
        btn_cancel.setProperty("class", "secondary")
        btn_cancel.setFixedWidth(60)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_confirm)
        btn_layout.addWidget(btn_cancel)

        idx = self.messages_layout.indexOf(bubble)
        self.messages_layout.insertWidget(idx + 1, edit)
        self.messages_layout.insertWidget(idx + 2, btn_row)

        def _confirm():
            new_text = edit.toPlainText().strip()
            if not new_text:
                return
            edit.setParent(None)
            edit.deleteLater()
            btn_row.setParent(None)
            btn_row.deleteLater()
            self._remove_widgets_from(msg_index + 1)

            # 更新 bubble 文本
            bubble._raw_text = new_text
            html, img_map = MessageBubble._render_md(
                new_text,
                MessageBubble._font_family,
                MessageBubble._font_scale,
                getattr(bubble, "_theme_colors", None),
                bubble._role,
            )
            bubble._img_map = img_map
            bubble._preload_images(img_map)
            bubble.setHtml(html)

            # 异步重新生成
            worker = _ChatWorker(self.session, new_text)
            worker._method = "edit_and_regenerate"
            worker._edit_msg_index = msg_index
            worker.finished.connect(self._on_reply)
            worker.error.connect(self._on_error)

            self.send_btn.setText("取消")
            try:
                self.send_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.send_btn.clicked.connect(self._on_cancel_send)
            self.input_edit.setEnabled(False)
            self.model_combo.setEnabled(False)
            self._active_worker_session_dir = self.session.session_dir
            self._thinking_bubble = MessageBubble("assistant", "")
            self._insert_widget(self._thinking_bubble)
            self._thinking_start = __import__("time").time()
            self._thinking_frame = 0
            self._thinking_timer.start()
            self._worker = worker
            worker.start()

        def _cancel():
            edit.setParent(None)
            edit.deleteLater()
            btn_row.setParent(None)
            btn_row.deleteLater()

        btn_confirm.clicked.connect(_confirm)
        btn_cancel.clicked.connect(_cancel)

    def _msg_delete(self, msg_index: int):
        removed = self.session.delete_message(msg_index)
        if removed:
            self._restore_history()
        for _ in range(0):
            # 先删 action panel，再删 bubble
            for i in range(self.messages_layout.count()):
                item = self.messages_layout.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, (_ActionPanel, _MessageTimeLabel, MessageBubble)) and w._msg_index == msg_index:
                    self.messages_layout.removeWidget(w)
                    w.setParent(None)
                    w.deleteLater()
                    break
        self._refresh_session_name()
        n = sum(1 for m in self.session.messages if m.get("role") == "user")
        self._update_current_item_rounds(n)
        self.status_label.setText(
            f"{self.session.name} | {self.session.model} | {n} 轮")

    def _msg_retry(self, msg_index: int):
        bubble = self._get_bubble_at(msg_index)
        if not bubble or bubble._role != "assistant":
            return
        # 移除 bubble + action panel
        to_del = []
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, (MessageBubble, _ActionPanel, _MessageTimeLabel)) and w._msg_index == msg_index:
                to_del.append(w)
        for w in to_del:
            self.messages_layout.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        if (msg_index < len(self.session.messages)
                and self.session.messages[msg_index]["role"] == "assistant"):
            self.session.messages.pop(msg_index)
            self.session._save_history()
        self._start_async_generation("regenerate")

    def _msg_quote(self, msg_index: int):
        b = self._get_bubble_at(msg_index)
        if not b:
            return
        text = b._raw_text[:200]
        current = self.input_edit.toPlainText().strip()
        quote = f"> {text}\n"
        self.input_edit.setPlainText((current + "\n" + quote) if current else quote)
        self.input_edit.setFocus()
        cursor = self.input_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_edit.setTextCursor(cursor)

    def _msg_good(self, msg_index: int):
        self._toggle_feedback(msg_index, "good")

    def _msg_bad(self, msg_index: int):
        self._toggle_feedback(msg_index, "bad")

    def _toggle_feedback(self, msg_index: int, feedback: str):
        msg = self.session.messages[msg_index]
        new_state = None if msg.get("feedback") == feedback else feedback
        msg["feedback"] = new_state
        self.session._save_history()
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _ActionPanel) and w._msg_index == msg_index:
                w.set_feedback(new_state)
                break

    def _msg_style(self, msg_index: int):
        menu = QMenu(self)
        styles = [
            ("更详细", "请用更详细、更充分的方式重新回答"),
            ("更简洁", "请用更简洁、更精炼的方式重新回答"),
            ("更通俗", "请用更通俗、更易懂的语言重新回答"),
            ("更专业", "请用更专业、更严谨的方式重新回答"),
        ]
        for label, instruction in styles:
            a = menu.addAction(label)
            a.setData(instruction)
        chosen = menu.exec(QCursor.pos())
        if not chosen:
            return
        instruction = chosen.data()
        # 找前一条 user 消息
        user_idx = msg_index - 1
        while user_idx >= 0:
            if self.session.messages[user_idx]["role"] == "user":
                break
            user_idx -= 1
        if user_idx < 0:
            return
        original = self.session.messages[user_idx]["content"]
        self._remove_widgets_from(msg_index)

        # 异步重新生成
        worker = _ChatWorker(self.session, original + "\n" + instruction)
        worker._method = "edit_and_regenerate"
        worker._edit_msg_index = user_idx
        worker.finished.connect(self._on_reply)
        worker.error.connect(self._on_error)

        self.send_btn.setText("取消")
        try:
            self.send_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.send_btn.clicked.connect(self._on_cancel_send)
        self.input_edit.setEnabled(False)
        self.model_combo.setEnabled(False)
        self._active_worker_session_dir = self.session.session_dir
        self._thinking_bubble = MessageBubble("assistant", "")
        self._insert_widget(self._thinking_bubble)
        self._thinking_start = __import__("time").time()
        self._thinking_frame = 0
        self._thinking_timer.start()
        self._worker = worker
        worker.start()
        self._refresh_session_name()

    def _update_current_item_rounds(self, rounds: int):
        item = self.session_tree.currentItem()
        if not item:
            return
        self._update_item_rounds(item, rounds)

    def _update_session_item_rounds(self, session_dir: str, rounds: int):
        it = QTreeWidgetItemIterator(self.session_tree)
        while it.value():
            item = it.value()
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "session" and data.get("session_dir") == session_dir:
                self._update_item_rounds(item, rounds)
                return
            it.__next__()

    def _update_item_rounds(self, item: QTreeWidgetItem, rounds: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "session":
            return
        base = data.get("name", "")
        label = f"{base} ({rounds}轮)" if rounds > 0 else base
        data["display_label"] = label
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._apply_session_star(item, bool(data.get("favorite", False)))

    # ─── UI 工具 ───

    def _get_bubble_max_width(self) -> int:
        viewport_w = self.scroll.viewport().width()
        return max(viewport_w, 200)

    def _insert_widget(self, widget):
        if isinstance(widget, MessageBubble):
            if self._theme_colors:
                widget._apply_bubble_style(self._theme_colors)
            widget.setMaximumWidth(self._get_bubble_max_width())
        idx = self.messages_layout.count() - 1
        self.messages_layout.insertWidget(idx, widget)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _add_bubble(self, role: str, text: str, index: int = None,
                    feedback: str = None, created_at: str = ""):
        if index is None:
            index = self.messages_layout.count() - 1
        bubble = MessageBubble(role, text, index)
        self._insert_widget(bubble)
        timestamp = self._format_message_time(created_at or self._fallback_message_time())
        time_label = None
        if False and timestamp:
            time_label = _MessageTimeLabel(role, index, timestamp)
            if self._theme_colors:
                time_label._refresh_style(self._theme_colors)
            idx = self.messages_layout.indexOf(bubble)
            self.messages_layout.insertWidget(idx + 1, time_label)

        # 操作栏：紧接在 bubble 后面插入
        panel = _ActionPanel(role, index, created_at=timestamp)
        panel.actionTriggered.connect(self._on_msg_action)
        if self._theme_colors:
            panel._refresh_style(self._theme_colors)
        if feedback and role == "assistant":
            panel.set_feedback(feedback, self._theme_colors)
        # 插到 bubble 后面、stretch 前面
        idx = self.messages_layout.indexOf(time_label or bubble)
        self.messages_layout.insertWidget(idx + 1, panel)
        return bubble

    def resizeEvent(self, event):
        super().resizeEvent(event)
        max_w = self._get_bubble_max_width()
        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), MessageBubble):
                item.widget().setMaximumWidth(max_w)

    def _scroll_to_bottom(self):
        sb = self.scroll.verticalScrollBar()
        # 只在用户接近底部时自动滚动，避免抢夺滚动控制权
        at_bottom = sb.value() >= sb.maximum() - 60
        if at_bottom:
            sb.setValue(sb.maximum())

    def _clear_messages(self):
        self._distill_level_combo.setVisible(False)
        self.btn_regenerate_note.setVisible(False)
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            w = item.widget()
            if w:
                if w is self._thinking_bubble:
                    self._thinking_bubble = None
                w.setParent(None)
                w.deleteLater()
