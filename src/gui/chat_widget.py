"""
Video-Distiller AI 对话界面
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
from PySide6.QtGui import QFont, QFontDatabase, QTextDocument, QCursor
from PySide6.QtWidgets import (
    QTreeWidgetItemIterator,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTextBrowser, QScrollArea, QSizePolicy,
    QTreeWidget, QTreeWidgetItem, QFrame, QComboBox, QFileDialog,
    QMenu, QDialog, QGridLayout, QLineEdit, QDialogButtonBox,
    QInputDialog, QSplitter, QHeaderView, QApplication,
)

from src.chat import ChatSession, create_empty_session, list_sessions


_THINKING_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class _ActionPanel(QWidget):
    """消息操作栏：独立插入 messages_layout，常驻显示在气泡下方"""

    actionTriggered = Signal(str, int)  # (action, msg_index)

    def __init__(self, role: str, msg_index: int, parent=None):
        super().__init__(parent)
        self._role = role
        self._msg_index = msg_index
        self.setProperty("class", f"msg-actions-{role}")
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        def _btn(label: str, action: str, tooltip: str = ""):
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setMinimumWidth(4)
            b.setToolTip(tooltip or label)
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
        if role == "assistant":
            layout.addSpacing(4)
            for b in btns:
                layout.addWidget(b)
            layout.addSpacing(6)
            self._btn_good = _btn("👍", "good", "好评")
            self._btn_bad = _btn("👎", "bad", "差评")
            layout.addWidget(self._btn_good)
            layout.addWidget(self._btn_bad)
            layout.addSpacing(6)
            layout.addWidget(_btn("风格", "style", "修改回复风格"))
            layout.addStretch()
        else:
            layout.addStretch()
            for b in btns:
                layout.addWidget(b)

    def set_feedback(self, state):
        self._btn_good.setProperty("class", "feedback-active" if state == "good" else "")
        self._btn_bad.setProperty("class", "feedback-active" if state == "bad" else "")
        self._btn_good.style().unpolish(self._btn_good)
        self._btn_good.style().polish(self._btn_good)
        self._btn_bad.style().unpolish(self._btn_bad)
        self._btn_bad.style().polish(self._btn_bad)


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
    _img_paths: dict = {}  # src_name → abs_path

    def __init__(self, role: str, text: str, msg_index: int = -1):
        super().__init__()
        self.setProperty("class", f"msg-{role}")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._raw_text = text
        self._img_map = {}
        self._role = role
        self._msg_index = msg_index

        html, img_map = self._render_md(text, self._font_family, self._font_scale)
        self._img_map = img_map
        self._preload_images(img_map)
        self.setHtml(html)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._sync_widget_font()
        self.document().documentLayout().documentSizeChanged.connect(self._adjust_size)

    def set_msg_index(self, idx: int):
        self._msg_index = idx

    # ─── 尺寸自适应 ───

    def _adjust_size(self):
        doc_h = int(self.document().documentLayout().documentSize().height())
        target = doc_h + 26
        if abs(self.height() - target) > 2:
            self.setFixedHeight(target)
            self.updateGeometry()

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
    def _render_md(text: str, font_family: str, font_scale: int) -> tuple:
        """返回 (html, img_map)，img_map = {src_key: abs_path}"""
        import re
        from PySide6.QtGui import QFont

        base_px = 14.0 * font_scale / 100.0
        family = font_family if font_family else ("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI")
        font = QFont(family)
        font.setPixelSize(int(base_px))

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

        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setMarkdown(text)
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

    def _apply_font(self):
        html, img_map = self._render_md(self._raw_text, self._font_family, self._font_scale)
        self._img_map = img_map
        self._preload_images(img_map)
        self.setHtml(html)
        self._sync_widget_font()

    def _sync_widget_font(self):
        base_px = 14.0 * self._font_scale / 100.0
        family = self._font_family if self._font_family else ("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI")
        font = QFont(family)
        font.setPixelSize(int(base_px))
        self.document().setDefaultFont(font)

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self._drag_item = None

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
        for i in range(parent.childCount()):
            parent.takeChild(0)
        for child in children:
            parent.addChild(child)

        self._persist_order()
        self._drag_item = None
        self.orderChanged.emit()

    def _persist_order(self):
        """根据当前树中的顺序更新各 session 的 order 字段"""
        import json
        from pathlib import Path
        from src.chat import _SESSIONS_DIR

        order_map = {}
        idx = 0
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "session":
                order_map[data["session_id"]] = idx
                idx += 1
            it.__next__()

        for sid, order in order_map.items():
            hfile = _SESSIONS_DIR / sid / "chat_history.json"
            if not hfile.is_file():
                continue
            try:
                d = json.loads(hfile.read_text(encoding="utf-8"))
                d["order"] = order
                hfile.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass


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

    def run(self):
        try:
            if self._method == "regenerate":
                reply = self.session.regenerate()
            elif self._method == "edit_and_regenerate":
                reply = self.session.edit_and_regenerate(
                    self._edit_msg_index, self.message)
            else:
                reply = self.session.chat(self.message)
            if self._cancel:
                return
            total = sum(len(m["content"]) for m in self.session.messages)
            self.finished.emit(reply, total)
        except Exception as e:
            if not self._cancel:
                self.error.emit(str(e))


class _SessionConfigDialog(QDialog):
    """对话配置：笔记 + 数据文件"""

    def __init__(self, session: ChatSession, parent=None):
        super().__init__(parent)
        self.setWindowTitle("对话配置")
        self.setMinimumWidth(480)
        self.session = session

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)

        # Notes
        grid.addWidget(QLabel("笔记 (notes.md):"), 0, 0)
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("选择笔记文件...")
        self.notes_edit.setText(session.notes_path)
        grid.addWidget(self.notes_edit, 0, 1)
        btn_notes = QPushButton("浏览")
        btn_notes.setProperty("class", "secondary")
        btn_notes.setFixedWidth(56)
        btn_notes.clicked.connect(lambda: self._browse(self.notes_edit, "笔记文件", "Markdown (*.md);;所有文件 (*)"))
        grid.addWidget(btn_notes, 0, 2)

        # Data JSON (统一 JSON 或 slides.json)
        grid.addWidget(QLabel("数据文件 (JSON):"), 1, 0)
        self.data_edit = QLineEdit()
        self.data_edit.setPlaceholderText("统一 JSON 或 slides.json...")
        self.data_edit.setText(session.slides_path)
        grid.addWidget(self.data_edit, 1, 1)
        btn_data = QPushButton("浏览")
        btn_data.setProperty("class", "secondary")
        btn_data.setFixedWidth(56)
        btn_data.clicked.connect(lambda: self._browse(self.data_edit, "数据文件", "JSON (*.json)"))
        grid.addWidget(btn_data, 1, 2)

        layout.addLayout(grid)

        hint = QLabel("数据文件可包含幻灯片和转录内容。有笔记时笔记将作为对话首条消息显示。")
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
        return (
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
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(150)
        self._thinking_timer.timeout.connect(self._tick_thinking)
        self._thinking_frame = 0
        self._thinking_start = 0.0
        self._thinking_bubble: Optional[MessageBubble] = None
        self._build_ui()

    def _build_ui(self):
        # ─── 左侧：session 列表 ───
        left_panel = QWidget()
        left_panel.setProperty("class", "chat-sidebar")
        left_panel.setMinimumWidth(140)
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
        self.session_tree.setHeaderHidden(True)
        self.session_tree.setIndentation(16)
        self.session_tree.setAnimated(True)
        self.session_tree.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection
        )
        self.session_tree.currentItemChanged.connect(self._on_tree_item_changed)
        self.session_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.session_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.session_tree.itemDoubleClicked.connect(self._on_tree_double_click)
        left_layout.addWidget(self.session_tree, 1)

        # ─── 右侧：聊天区 ───
        right_panel = QWidget()
        right_panel.setProperty("class", "chat-right")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 状态栏：session 名 + 齿轮按钮
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("选择或新建一个对话")
        self.status_label.setProperty("class", "chat-status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFixedHeight(32)
        status_row.addWidget(self.status_label, 1)

        self.btn_config = QPushButton("⚙")
        self.btn_config.setFixedSize(28, 28)
        self.btn_config.setProperty("class", "secondary")
        self.btn_config.setToolTip("配置关联文件")
        self.btn_config.clicked.connect(self._on_config)
        status_row.addWidget(self.btn_config)

        right_layout.addLayout(status_row)

        # 关联文件指示
        self.files_label = QLabel("")
        self.files_label.setProperty("class", "hint")
        self.files_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.files_label.setFixedHeight(22)
        self.files_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files_label.mousePressEvent = self._on_files_label_click
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
        self.quick_btn.setFixedWidth(80)
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
        self.model_combo.setFixedWidth(160)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        bottom_row.addWidget(self.model_combo)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedWidth(80)
        self.send_btn.clicked.connect(self._on_send)
        bottom_row.addWidget(self.send_btn)

        input_layout.addLayout(bottom_row)
        right_layout.addWidget(input_bar)

        # 用 Splitter 支持拖拽调整侧边栏宽度
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([220, 600])
        splitter.setStretchFactor(1, 1)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(splitter)

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

        # 创建文件夹节点
        for f in folders:
            fid = f["id"]
            children = grouped.get(fid, [])
            if search_text and not children:
                continue  # 搜索时隐藏空文件夹
            children.reverse()  # 最新的在上面
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
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, Qt.ItemDataRole.UserRole, {"type": "session", **s})
        if s.get("hidden", False):
            is_dark = self._is_dark_theme()
            gray = QColor(120, 120, 120) if is_dark else QColor(170, 170, 170)
            item.setForeground(0, gray)

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
                menu.addAction("删除文件夹", lambda: self._delete_folder(folder_id))
            else:
                menu.addAction("新建文件夹", self._on_new_folder)
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
            self, "导出对话", "", "Video-Distiller 对话包 (*.vdc)"
        )
        if not dest:
            return
        if not dest.endswith(".vdc"):
            dest += ".vdc"

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
        """从 .vdc 文件导入对话"""
        from src.session_io import import_sessions
        path, _ = QFileDialog.getOpenFileName(
            self, "导入对话", "", "Video-Distiller 对话包 (*.vdc)"
        )
        if not path:
            return

        try:
            new_ids = import_sessions(path)
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

    def _delete_folder(self, folder_id: str):
        """删除文件夹，对话移回未分组"""
        from src.chat import load_folders, save_folders
        folders = load_folders()
        folders = [f for f in folders if f["id"] != folder_id]
        save_folders(folders)
        # 将该文件夹下所有 session 的 folder_id 清空
        sessions = list_sessions()
        for s in sessions:
            if s.get("folder_id") == folder_id:
                sdir = s["session_dir"]
                hfile = os.path.join(sdir, "chat_history.json")
                try:
                    d = json.loads(Path(hfile).read_text(encoding="utf-8"))
                    d["folder_id"] = ""
                    Path(hfile).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        self._build_session_tree()

    def _move_to_folder(self, items: list, folder_id: str):
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data.get("type") != "session":
                continue
            sdir = data["session_dir"]
            hfile = os.path.join(sdir, "chat_history.json")
            try:
                d = json.loads(Path(hfile).read_text(encoding="utf-8"))
                d["folder_id"] = folder_id
                Path(hfile).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        self._build_session_tree()

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

    def _on_tree_double_click(self, item: QTreeWidgetItem, _col: int):
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
        item.setText(0, f"{name.strip()}{rounds_str}")
        item.setData(0, Qt.ItemDataRole.UserRole, data)
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

        notes_path, data_path = dlg.get_paths()
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
        item.setText(0, label)
        data["name"] = name
        if self.session:
            data["notes_path"] = self.session.notes_path
            data["slides_path"] = self.session.slides_path
        item.setData(0, Qt.ItemDataRole.UserRole, data)

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
                             feedback=msg.get("feedback"))

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
        self._add_bubble("user", text, len(self.session.messages))

        self.send_btn.setText("取消")
        self.send_btn.clicked.disconnect()
        self.send_btn.clicked.connect(self._on_cancel_send)
        self.input_edit.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.session_tree.setEnabled(False)

        self._thinking_bubble = MessageBubble("assistant", "")
        self._insert_widget(self._thinking_bubble)
        self._thinking_start = __import__("time").time()
        self._thinking_frame = 0
        self._thinking_timer.start()

        self._worker = _ChatWorker(self.session, text)
        self._worker.finished.connect(self._on_reply)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_cancel_send(self):
        if not self._worker or not self._worker.isRunning():
            return
        self._worker._cancel = True
        self._stop_thinking()
        # 显示取消占位（不写入 session.messages，刷新后消失）
        idx = self.messages_layout.count() - 1
        placeholder = MessageBubble("assistant", "（已取消生成）", idx)
        placeholder.setStyleSheet("color: #888; font-style: italic;")
        self._insert_widget(placeholder)
        self._restore_send_btn()
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
        self._stop_thinking()
        idx = len(self.session.messages) - 1
        self._add_bubble("assistant", reply, idx)
        self._restore_send_btn()
        self.input_edit.setFocus()

        import time
        elapsed = time.time() - self._thinking_start
        n_msgs = sum(1 for m in self.session.messages if m.get("role") == "user")
        self.status_label.setText(
            f"{self.session.name} | {self.session.model} | {n_msgs} 轮"
        )
        self.token_label.setText(f"~{total_chars} chars | {elapsed:.1f}s")
        self._update_current_item_rounds(n_msgs)

    def _on_error(self, err: str):
        self._stop_thinking()
        self._add_bubble("assistant", f"[错误] {err}")
        self._restore_send_btn()
        self.status_label.setText(f"请求失败: {err[:60]}")

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
            elif isinstance(w, _ActionPanel) and w._msg_index >= start_index:
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
        self.session_tree.setEnabled(False)
        self._thinking_bubble = MessageBubble("assistant", "")
        self._insert_widget(self._thinking_bubble)
        self._thinking_start = __import__("time").time()
        self._thinking_frame = 0
        self._thinking_timer.start()
        worker = _ChatWorker(self.session, message)
        worker._method = method
        worker.finished.connect(self._on_reply)
        worker.error.connect(self._on_error)
        worker.start()
        self._worker = worker

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
                new_text, MessageBubble._font_family, MessageBubble._font_scale)
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
            self.session_tree.setEnabled(False)
            self._thinking_bubble = MessageBubble("assistant", "")
            self._insert_widget(self._thinking_bubble)
            self._thinking_start = __import__("time").time()
            self._thinking_frame = 0
            self._thinking_timer.start()
            worker.start()
            self._worker = worker

        def _cancel():
            edit.setParent(None)
            edit.deleteLater()
            btn_row.setParent(None)
            btn_row.deleteLater()

        btn_confirm.clicked.connect(_confirm)
        btn_cancel.clicked.connect(_cancel)

    def _msg_delete(self, msg_index: int):
        removed = self.session.delete_message(msg_index)
        for _ in range(removed):
            # 先删 action panel，再删 bubble
            for i in range(self.messages_layout.count()):
                item = self.messages_layout.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, (_ActionPanel, MessageBubble)) and w._msg_index == msg_index:
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
            if isinstance(w, (MessageBubble, _ActionPanel)) and w._msg_index == msg_index:
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
        self.session_tree.setEnabled(False)
        self._thinking_bubble = MessageBubble("assistant", "")
        self._insert_widget(self._thinking_bubble)
        self._thinking_start = __import__("time").time()
        self._thinking_frame = 0
        self._thinking_timer.start()
        worker.start()
        self._worker = worker
        self._refresh_session_name()

    def _update_current_item_rounds(self, rounds: int):
        item = self.session_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "session":
            return
        base = data.get("name", "")
        label = f"{base} ({rounds}轮)" if rounds > 0 else base
        item.setText(0, label)

    # ─── UI 工具 ───

    def _get_bubble_max_width(self) -> int:
        viewport_w = self.scroll.viewport().width()
        return max(viewport_w, 200)

    def _insert_widget(self, widget):
        if isinstance(widget, MessageBubble):
            widget.setMaximumWidth(self._get_bubble_max_width())
        idx = self.messages_layout.count() - 1
        self.messages_layout.insertWidget(idx, widget)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _add_bubble(self, role: str, text: str, index: int = None,
                    feedback: str = None):
        if index is None:
            index = self.messages_layout.count() - 1
        bubble = MessageBubble(role, text, index)
        self._insert_widget(bubble)

        # 操作栏：紧接在 bubble 后面插入
        panel = _ActionPanel(role, index)
        panel.actionTriggered.connect(self._on_msg_action)
        if feedback and role == "assistant":
            panel.set_feedback(feedback)
        # 插到 bubble 后面、stretch 前面
        idx = self.messages_layout.indexOf(bubble)
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
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
