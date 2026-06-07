"""
Book-Distiller 主窗口
PySide6, Apple 风格, Light/Dark 主题, Settings 集成
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QLineEdit,
    QFileDialog, QComboBox, QTextEdit, QListWidget,
    QProgressBar, QGroupBox, QGridLayout, QToolBar,
    QToolButton, QListView, QScrollArea, QDialog,
    QDialogButtonBox, QSpinBox, QListWidgetItem,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon
from src.config import (
    BOOK_DISTILL_LEVELS, BOOK_SESSION_GRANULARITIES, DEFAULT_BOOK_DISTILL_PROMPTS,
    RICH_TEXT_FORMATTING_PROMPT,
    load_settings, save_settings, Settings,
)
from src.gui.theme import build_stylesheet
from src.gui.settings_dialog import SettingsDialog


def _is_cloud_provider(provider: dict) -> bool:
    base_url = provider.get("base_url", "").lower()
    return bool(provider.get("api_key")) and bool(base_url) and "localhost" not in base_url and "127.0.0.1" not in base_url


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings: Settings = load_settings()
        self._theme = self.settings.theme
        self.setWindowTitle("Book-Distiller")
        # 窗口图标：开发时从项目根目录找，打包后从 _MEIPASS 找
        if getattr(sys, 'frozen', False):
            icon_path = os.path.join(sys._MEIPASS, 'icon.ico')
        else:
            icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'icon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(820, 620)
        self.setStyleSheet(build_stylesheet(self._theme))
        self._build_ui()

    # ─── 主题切换 ───

    def _toggle_theme(self):
        self._theme = "dark" if self._theme == "light" else "light"
        self.settings.theme = self._theme
        save_settings(self.settings)
        from src.gui.theme import THEMES
        colors = THEMES[self._theme]
        self.setStyleSheet(build_stylesheet(self._theme))
        self.theme_btn.setText("浅色" if self._theme == "dark" else "深色")
        self._update_dynamic_colors()
        self._force_qt_combobox()
        self.chat_widget.refresh_theme_styles(colors)

    def closeEvent(self, event):
        """确保所有后台线程在退出前正确停止"""
        self._save_batch_books()
        if hasattr(self, '_batch_worker') and self._batch_worker is not None:
            if hasattr(self._batch_worker, '_cancel'):
                self._batch_worker._cancel = True
            if hasattr(self._batch_worker, 'isRunning') and self._batch_worker.isRunning():
                self._batch_worker.wait(3000)
        event.accept()

    def _update_dynamic_colors(self):
        pass

    def _force_qt_combobox(self):
        """强制 QComboBox 使用 Qt 内置弹窗渲染，使 QSS 完全生效"""
        for combo in self.findChildren(QComboBox):
            combo.setView(QListView())

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == 1:
            self.settings = load_settings()
            theme_changed = self.settings.theme != self._theme
            if theme_changed:
                self._theme = self.settings.theme
                self.setStyleSheet(build_stylesheet(self._theme))
                self.theme_btn.setText("浅色" if self._theme == "dark" else "深色")
            self._refresh_batch_combos()
            self.chat_widget.apply_font_settings(
                self.settings.chat_font_family, self.settings.chat_font_scale
            )
            if theme_changed:
                from src.gui.theme import THEMES
                self.chat_widget.refresh_theme_styles(THEMES[self._theme])

    # ─── 构建 UI ───

    def _build_ui(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setStyleSheet("border: none; padding: 0 4px;")

        settings_btn = QToolButton()
        settings_btn.setText("设置")
        settings_btn.clicked.connect(self._open_settings)
        toolbar.addWidget(settings_btn)

        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().horizontalPolicy().Expanding,
            spacer.sizePolicy().verticalPolicy().Preferred,
        )
        toolbar.addWidget(spacer)
        self.theme_btn = QToolButton()
        self.theme_btn.setText("深色" if self._theme == "light" else "浅色")
        self.theme_btn.clicked.connect(self._toggle_theme)
        toolbar.addWidget(self.theme_btn)
        self.addToolBar(toolbar)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)

        # 顶层 Tab：批量蒸馏 + 对话
        self.top_tabs = QTabWidget()

        from src.gui.chat_widget import ChatWidget
        self.chat_widget = ChatWidget()
        self.chat_widget.apply_font_settings(
            self.settings.chat_font_family, self.settings.chat_font_scale
        )
        from src.gui.theme import THEMES
        self.chat_widget.refresh_theme_styles(THEMES[self._theme])
        self.top_tabs.addTab(self._build_batch_tab(), "  批量蒸馏  ")
        self.top_tabs.addTab(self.chat_widget, "  对话  ")
        self.top_tabs.currentChanged.connect(self._on_top_tab_changed)
        self._refresh_chat_history(select_first=False)

        layout.addWidget(self.top_tabs, stretch=1)

        self._force_qt_combobox()
        self._status_label = QLabel("就绪")
        self._status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.statusBar().addPermanentWidget(self._status_label, stretch=1)

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _on_top_tab_changed(self, index: int):
        """切换到对话 Tab 时扫描 session 列表"""
        if index != 1:
            return
        self._refresh_chat_history(select_first=True)

    def _refresh_chat_history(self, select_first: bool = False):
        provider_config = {}
        for p in self.settings.providers:
            if p.get("api_key"):
                provider_config = p
                break

        self.chat_widget.set_providers(self.settings.providers)
        self.chat_widget.refresh_session_list(provider_config)
        if select_first and not self.chat_widget.session:
            self.chat_widget._select_first_session()

    def _current_batch_books(self) -> list[str]:
        if not hasattr(self, "batch_video_list"):
            return []
        return [
            self.batch_video_list.item(i).text()
            for i in range(self.batch_video_list.count())
        ]

    def _save_batch_books(self):
        books = self._current_batch_books()
        if books != getattr(self.settings, "last_batch_books", []):
            self.settings.last_batch_books = books
            save_settings(self.settings)

    def _save_batch_distill_controls(self):
        if hasattr(self, "batch_distill_level_combo"):
            self.settings.book_distill_level = self.batch_distill_level_combo.currentText()
        if hasattr(self, "batch_session_granularity_combo"):
            self.settings.book_session_granularity = (
                self.batch_session_granularity_combo.currentData() or "level2"
            )
        if hasattr(self, "batch_vision_scale_combo"):
            self.settings.vision_scale_percent = (
                self.batch_vision_scale_combo.currentData() or 0
            )
        save_settings(self.settings)

    def _on_book_selected(self, current, previous):
        """选中书籍时，读取该书的目录起始页到 SpinBox"""
        if not current:
            return
        val = current.data(Qt.ItemDataRole.UserRole + 1)
        if val is None:
            val = 1
        self.toc_start_spin.blockSignals(True)
        self.toc_start_spin.setValue(val)
        self.toc_start_spin.blockSignals(False)
        # 更新提示
        name = Path(current.text()).stem
        self.toc_start_label.setText(f"← {name[:30]}")

    def _on_toc_start_changed(self, value):
        """SpinBox 变化时，写回当前选中书籍的 item data"""
        item = self.batch_video_list.currentItem()
        if item:
            item.setData(Qt.ItemDataRole.UserRole + 1, value)

    def _open_batch_prompts(self):
        dlg = _PromptPresetDialog(self.settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.settings.book_distill_prompts = dlg.get_prompts()
            self.settings.book_distill_level = dlg.current_level()
            if hasattr(self, "batch_distill_level_combo"):
                self.batch_distill_level_combo.setCurrentText(self.settings.book_distill_level)
            save_settings(self.settings)

    # ─── 批量蒸馏 Tab ───

    def _build_batch_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 12)

        # 顶部：输出目录（一行，无 GroupBox）
        top = QHBoxLayout()
        top.addWidget(self._label("输出目录"))
        self.batch_output_edit = QLineEdit()
        self.batch_output_edit.setPlaceholderText("选择输出目录...")
        if self.settings.last_output_dir:
            self.batch_output_edit.setText(self.settings.last_output_dir)
        top.addWidget(self.batch_output_edit, stretch=1)
        btn_out = QPushButton("浏览")
        btn_out.setProperty("class", "secondary")
        btn_out.setFixedWidth(56)
        btn_out.clicked.connect(self._batch_browse_output)
        top.addWidget(btn_out)
        layout.addLayout(top)

        # 中部：左（书籍列表）+ 右（配置面板）
        mid = QHBoxLayout()
        mid.setSpacing(12)

        # 左侧：书籍列表
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(self._label("书籍列表"))
        self.batch_video_list = _DropListWidget()
        self.batch_video_list.filesDropped.connect(self._save_batch_books)
        self.batch_video_list.setToolTip("拖拽 PDF 文件到这里；第一版仅支持 PDF，扫描版 PDF 将通过图片识别/OCR 管线处理。")
        left.addWidget(self.batch_video_list, stretch=1)
        for p in getattr(self.settings, "last_batch_books", []):
            if isinstance(p, str) and Path(p).is_file():
                item = QListWidgetItem(p)
                item.setData(Qt.ItemDataRole.UserRole + 1, 1)  # 默认目录起始页=1
                self.batch_video_list.addItem(item)
        self.batch_video_list.currentItemChanged.connect(self._on_book_selected)

        # 目录起始页（绑定到选中的书籍）
        toc_row = QHBoxLayout()
        toc_row.addWidget(self._label("目录起始页"))
        self.toc_start_spin = QSpinBox()
        self.toc_start_spin.setRange(1, 9999)
        self.toc_start_spin.setValue(1)
        self.toc_start_spin.setToolTip("选中书籍的目录大致起始页，加速扫描版 PDF 目录探测")
        self.toc_start_spin.valueChanged.connect(self._on_toc_start_changed)
        toc_row.addWidget(self.toc_start_spin)
        self.toc_start_label = QLabel("（选中书籍后可设置）")
        self.toc_start_label.setStyleSheet("color: #888; font-size: 11px;")
        toc_row.addWidget(self.toc_start_label)
        left.addLayout(toc_row)
        btn_row = QHBoxLayout()
        btn_add = QPushButton("添加 PDF")
        btn_add.clicked.connect(self._batch_add_books)
        btn_remove = QPushButton("移除")
        btn_remove.setProperty("class", "secondary")
        btn_remove.clicked.connect(self._batch_remove_selected)
        btn_clear = QPushButton("清空")
        btn_clear.setProperty("class", "secondary")
        btn_clear.clicked.connect(self._batch_clear_videos)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addWidget(btn_clear)
        left.addLayout(btn_row)
        mid.addLayout(left, stretch=3)

        # 右侧：模型选择（紧凑 Grid，无 GroupBox）
        right = QVBoxLayout()
        right.setSpacing(4)

        model_grid = QGridLayout()
        model_grid.setSpacing(6)
        model_grid.setContentsMargins(0, 0, 0, 0)
        model_grid.setColumnStretch(1, 1)

        row = 0
        model_grid.addWidget(self._label("图片识别"), row, 0)
        self.batch_vision_combo = QComboBox()
        model_grid.addWidget(self.batch_vision_combo, row, 1)

        row += 1
        model_grid.addWidget(self._label("目录探测"), row, 0)
        self.batch_toc_vision_combo = QComboBox()
        model_grid.addWidget(self.batch_toc_vision_combo, row, 1)

        row += 1
        model_grid.addWidget(self._label("书籍整合"), row, 0)
        self.batch_agg_combo = QComboBox()
        model_grid.addWidget(self.batch_agg_combo, row, 1)

        row += 1
        model_grid.addWidget(self._label("蒸馏级别"), row, 0)
        self.batch_distill_level_combo = QComboBox()
        self.batch_distill_level_combo.addItems(BOOK_DISTILL_LEVELS)
        self.batch_distill_level_combo.setCurrentText(getattr(self.settings, "book_distill_level", "high"))
        self.batch_distill_level_combo.currentTextChanged.connect(self._save_batch_distill_controls)
        model_grid.addWidget(self.batch_distill_level_combo, row, 1)

        row += 1
        model_grid.addWidget(self._label("对话细分"), row, 0)
        self.batch_session_granularity_combo = QComboBox()
        for label, value in BOOK_SESSION_GRANULARITIES:
            self.batch_session_granularity_combo.addItem(label, value)
        current_granularity = getattr(self.settings, "book_session_granularity", "level2")
        granularity_index = self.batch_session_granularity_combo.findData(current_granularity)
        self.batch_session_granularity_combo.setCurrentIndex(granularity_index if granularity_index >= 0 else 1)
        self.batch_session_granularity_combo.currentIndexChanged.connect(self._save_batch_distill_controls)
        model_grid.addWidget(self.batch_session_granularity_combo, row, 1)

        row += 1
        model_grid.addWidget(self._label("图片缩放"), row, 0)
        self.batch_vision_scale_combo = QComboBox()
        self.batch_vision_scale_combo.addItem("原图（不缩放）", 0)
        self.batch_vision_scale_combo.addItem("90%", 90)
        self.batch_vision_scale_combo.addItem("80%", 80)
        self.batch_vision_scale_combo.addItem("70%", 70)
        self.batch_vision_scale_combo.addItem("60%", 60)
        self.batch_vision_scale_combo.addItem("50%", 50)
        saved_pct = getattr(self.settings, "vision_scale_percent", 0)
        idx = self.batch_vision_scale_combo.findData(saved_pct)
        self.batch_vision_scale_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.batch_vision_scale_combo.currentIndexChanged.connect(self._save_batch_distill_controls)
        model_grid.addWidget(self.batch_vision_scale_combo, row, 1)

        row += 1
        model_grid.addWidget(self._label(""), row, 0)
        btn_prompt = QPushButton("提示词设置")
        btn_prompt.setProperty("class", "secondary")
        btn_prompt.clicked.connect(self._open_batch_prompts)
        model_grid.addWidget(btn_prompt, row, 1)

        right.addLayout(model_grid)

        hint = QLabel("第一版只支持 PDF；扫描版 PDF 会进入图片识别/OCR 阶段。全书总览对话将放在章节列表最后。")
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        right.addWidget(hint)

        # 开始按钮 + 重试按钮
        btn_row = QHBoxLayout()
        self.btn_batch_start = QPushButton("开始蒸馏书籍")
        self.btn_batch_start.clicked.connect(self._batch_start)
        btn_row.addWidget(self.btn_batch_start)
        self.btn_batch_retry = QPushButton("重试失败")
        self.btn_batch_retry.setProperty("class", "secondary")
        self.btn_batch_retry.clicked.connect(self._batch_retry)
        self.btn_batch_retry.setVisible(False)
        btn_row.addWidget(self.btn_batch_retry)
        right.addLayout(btn_row)

        # 进度
        self.batch_progress = QProgressBar()
        right.addWidget(self.batch_progress)
        self.batch_status = QLabel(" ")
        self.batch_status.setProperty("class", "status")
        self.batch_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.batch_status)

        # 实时计时器
        self.batch_step_timer_label = QLabel(" ")
        self.batch_step_timer_label.setProperty("class", "hint")
        self.batch_step_timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.batch_step_timer_label)
        self._batch_step_t0 = 0.0
        self._batch_step_timer = QTimer(self)
        self._batch_step_timer.setInterval(1000)
        self._batch_step_timer.timeout.connect(self._tick_batch_step_timer)

        right.addStretch()
        mid.addLayout(right, stretch=2)

        layout.addLayout(mid, stretch=1)

        # 底部：运行日志（最大化纵向空间）
        layout.addWidget(self._label("运行日志"))
        self.batch_log = QTextEdit()
        self.batch_log.setReadOnly(True)
        self.batch_log.setPlaceholderText("运行日志...")
        layout.addWidget(self.batch_log, stretch=2)

        # 填充下拉框
        self._refresh_batch_combos()

        return page

    def _refresh_batch_combos(self):
        """填充书籍批量蒸馏的模型下拉框，恢复上次选择"""
        s = self.settings

        combos = [
            (self.batch_vision_combo, "last_batch_vision"),
            (self.batch_toc_vision_combo, "last_batch_toc_vision"),
            (self.batch_agg_combo, "last_batch_agg"),
        ]
        for combo, _ in combos:
            combo.blockSignals(True)

        # 图片识别
        self.batch_vision_combo.clear()
        for v in s.vision_models:
            tag = "本地" if v["type"] == "ollama" else "云端"
            self.batch_vision_combo.addItem(f"{v['name']} [{tag}]", v)

        # 目录探测（同一个 vision_models 列表）
        self.batch_toc_vision_combo.clear()
        self.batch_toc_vision_combo.addItem("跟随图片识别", None)
        for v in s.vision_models:
            tag = "本地" if v["type"] == "ollama" else "云端"
            self.batch_toc_vision_combo.addItem(f"{v['name']} [{tag}]", v)

        # 书籍整合
        self.batch_agg_combo.clear()
        for p in s.providers:
            if _is_cloud_provider(p):
                self.batch_agg_combo.addItem(f"{p['name']} ({p['model']})", p)

        # 恢复上次选择 + 绑定保存
        for combo, attr in combos:
            self._restore_combo(combo, getattr(s, attr, ""))
            combo.blockSignals(False)
            combo.currentTextChanged.connect(
                lambda t, a=attr, c=combo: self._save_combo(a, c)
            )

    def _batch_add_books(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 PDF 书籍", "",
            "PDF 书籍 (*.pdf);;所有文件 (*)"
        )
        for p in paths:
            if Path(p).suffix.lower() == ".pdf":
                item = QListWidgetItem(p)
                item.setData(Qt.ItemDataRole.UserRole + 1, 1)
                self.batch_video_list.addItem(item)
        self._save_batch_books()

    def _batch_remove_selected(self):
        for item in self.batch_video_list.selectedItems():
            self.batch_video_list.takeItem(self.batch_video_list.row(item))
        self._save_batch_books()

    def _batch_clear_videos(self):
        self.batch_video_list.clear()
        self._save_batch_books()

    def _batch_browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.batch_output_edit.setText(path)
            self.settings.last_output_dir = path
            save_settings(self.settings)

    def _batch_retry(self):
        """重试上次失败的书籍。"""
        failed = getattr(self, "_pending_retry_videos", [])
        if not failed:
            return
        self.btn_batch_retry.setVisible(False)
        self.batch_progress.setValue(0)
        self.batch_video_list.clear()
        for path in failed:
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole + 1, 1)
            self.batch_video_list.addItem(item)
        self.batch_log.append(f"\n── 重试 {len(failed)} 本书籍 ──\n")
        self._batch_start()

    def _batch_start(self):
        if self.batch_video_list.count() == 0:
            self.batch_log.append("请先添加 PDF 书籍")
            return
        output_dir = self.batch_output_edit.text().strip()
        if not output_dir:
            self.batch_log.append("请先选择输出目录")
            return

        books = []
        for i in range(self.batch_video_list.count()):
            item = self.batch_video_list.item(i)
            books.append({
                "path": item.text(),
                "toc_start_page": item.data(Qt.ItemDataRole.UserRole + 1) or 1,
            })
        self._save_batch_books()
        if output_dir != self.settings.last_output_dir:
            self.settings.last_output_dir = output_dir
            save_settings(self.settings)

        vision_data = self.batch_vision_combo.currentData()
        toc_vision_data = self.batch_toc_vision_combo.currentData()
        agg_data = self.batch_agg_combo.currentData()

        # 目录探测模型：未单独配置时跟随图片识别模型
        toc_vision_final = toc_vision_data if toc_vision_data else vision_data

        invalid = [b for b in books if Path(b["path"]).suffix.lower() != ".pdf"]
        if invalid:
            self.batch_log.append("第一版仅支持 PDF，请移除非 PDF 文件")
            return

        if not vision_data:
            self.batch_log.append("请确保图片识别模型有可用配置")
            return

        self.batch_progress.setValue(0)
        self.batch_log.clear()
        self.batch_status.setText(f"已准备 {len(books)} 本书籍")
        self.batch_log.append(f"书籍蒸馏已就绪: {len(books)} 本 PDF\n")
        self.batch_log.append(f"输出目录: {output_dir}")
        self.batch_log.append(f"图片识别: {vision_data.get('name', vision_data.get('model', ''))}")
        toc_label = toc_vision_final.get('name', toc_vision_final.get('model', '跟随图片识别'))
        self.batch_log.append(f"目录探测: {toc_label}")
        if agg_data:
            self.batch_log.append(f"书籍整合: {agg_data.get('name', agg_data.get('model', ''))}")
        else:
            self.batch_log.append("书籍整合: 未配置云端模型，Phase B 仅构建本地检索索引")
        self.batch_log.append(f"重构输出语言: {getattr(self.settings, 'book_output_language', '中文')}")
        self.batch_log.append(f"蒸馏级别: {getattr(self.settings, 'book_distill_level', 'high')}")
        self.batch_log.append(f"对话细分: {getattr(self.settings, 'book_session_granularity', 'level2')}")
        self.batch_log.append("Prompt: 当前蒸馏级别 Prompt")
        if agg_data:
            self.batch_log.append(f"Embedding: 跟随书籍整合 ({agg_data.get('name', '')} / {agg_data.get('model', '')})")
        else:
            self.batch_log.append("Embedding: 跟随书籍整合（未配置）")
        self.batch_log.append("")

        self._batch_worker = _BookBatchWorker(books, output_dir, self.settings, agg_data or {}, vision_data or {}, toc_vision_final or {})
        self._batch_worker.progress.connect(lambda v: self.batch_progress.setValue(int(v * 100)))
        self._batch_worker.book_progress.connect(self._batch_on_video_progress)
        self._batch_worker.log.connect(self._batch_on_log)
        self._batch_worker.step_start.connect(self._batch_on_step_start)
        self._batch_worker.step_time.connect(self._batch_on_step_time)
        self._batch_worker.sessions_changed.connect(self._batch_on_sessions_changed)
        self._batch_worker.finished.connect(self._batch_on_done)
        self.btn_batch_start.setText("停止")
        try:
            self.btn_batch_start.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_batch_start.clicked.connect(self._batch_stop)
        self._batch_worker.start()

    def _batch_stop(self):
        if hasattr(self, '_batch_worker') and self._batch_worker:
            self._batch_worker._cancel = True
            self.batch_log.append("\n正在停止...")
            self.btn_batch_start.setEnabled(False)
            self.btn_batch_start.setText("正在停止...")

    def _batch_on_log(self, msg: str):
        self.batch_log.append(msg)

    def _batch_on_video_progress(self, cur: int, total: int):
        self.batch_status.setText(f"{cur}/{total} 书籍")

    def _batch_on_step_start(self, step_name: str):
        import time
        self._batch_step_t0 = time.time()
        self.batch_step_timer_label.setText(f"{step_name}  0s")
        self._batch_step_timer.start()

    def _batch_on_step_time(self, msg: str):
        self._batch_step_timer.stop()
        self.batch_step_timer_label.setText(msg)

    def _batch_on_sessions_changed(self):
        self._refresh_chat_history(select_first=False)
        self.batch_log.append("对话列表已刷新")

    def _tick_batch_step_timer(self):
        import time
        if self._batch_step_t0 > 0:
            elapsed = int(time.time() - self._batch_step_t0)
            if elapsed < 60:
                t = f"{elapsed}s"
            else:
                t = f"{elapsed // 60}m {elapsed % 60:02d}s"
            current = self.batch_step_timer_label.text().split("  ")[0]
            self.batch_step_timer_label.setText(f"{current}  {t}")

    def _batch_on_done(self, ok: bool, msg: str):
        self._batch_step_timer.stop()
        self.batch_step_timer_label.setText(" ")
        # 安全恢复按钮状态
        try:
            self.btn_batch_start.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_batch_start.setText("开始蒸馏书籍")
        self.btn_batch_start.setEnabled(True)
        self.btn_batch_start.clicked.connect(self._batch_start)
        self.batch_progress.setValue(100 if ok else self.batch_progress.value())
        failed_videos = self._batch_worker._failed_videos if self._batch_worker else []
        self._batch_worker = None
        self.batch_log.append(f"\n{'全部完成' if ok else '已停止'}: {msg}")
        if failed_videos:
            self.btn_batch_retry.setText(f"重试失败 ({len(failed_videos)})")
            self.btn_batch_retry.setVisible(True)
            self._pending_retry_videos = failed_videos
        else:
            self.btn_batch_retry.setVisible(False)

    # ─── 辅助 ───

    @staticmethod
    def _restore_combo(combo: QComboBox, saved_text: str):
        """按文本匹配恢复上次选择"""
        if saved_text:
            idx = combo.findText(saved_text)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _save_combo(self, attr_name: str, combo: QComboBox):
        """combo 变化时保存选择到 settings"""
        text = combo.currentText()
        if text != getattr(self.settings, attr_name, ""):
            setattr(self.settings, attr_name, text)
            save_settings(self.settings)

    @staticmethod
    def _label(text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 500; color: #48484a;")
        return lbl


class _BookBatchWorker(QThread):
    progress = Signal(float)
    book_progress = Signal(int, int)
    log = Signal(str)
    step_time = Signal(str)
    step_start = Signal(str)
    sessions_changed = Signal()
    finished = Signal(bool, str)

    def __init__(self, books, output_dir, settings, provider_config=None, vision_config=None, toc_vision_config=None):
        super().__init__()
        self.books = books
        self.output_dir = output_dir
        self.settings = settings
        self.provider_config = provider_config or {}
        self.vision_config = vision_config or {}
        self.toc_vision_config = toc_vision_config or {}
        self._cancel = False
        self._failed_videos: list[str] = []

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        return f"{s // 60}m {s % 60:02d}s"

    def run(self):
        import time
        from src.book_pipeline import run_book_pipeline

        total = len(self.books)
        ok_count = 0
        self.log.emit(f"开始 Phase B 最小 RAG 闭环: {total} 本 PDF")
        for idx, book_entry in enumerate(self.books, 1):
            if self._cancel:
                break
            # 兼容旧的 str 格式和新的 dict 格式
            if isinstance(book_entry, dict):
                pdf_path = book_entry["path"]
                toc_start = book_entry.get("toc_start_page", 1)
            else:
                pdf_path = book_entry
                toc_start = 1
            name = Path(pdf_path).stem
            self.book_progress.emit(idx, total)
            self.log.emit(f"\n[{idx}/{total}] {name}")
            t0 = time.time()

            def on_progress(label: str, value: float):
                if self._cancel:
                    raise RuntimeError("已取消")
                self.step_start.emit(f"[{name}] {label}")
                base = (idx - 1) / total
                self.progress.emit(base + value / total)

            try:
                result = run_book_pipeline(
                    pdf_path,
                    self.output_dir,
                    on_progress,
                    log_cb=lambda msg: self.log.emit(msg),
                    create_sessions=True,
                    provider_config=self.provider_config,
                    vision_config=self.vision_config,
                    toc_vision_config=self.toc_vision_config,
                    output_language=getattr(self.settings, "book_output_language", "中文"),
                    distill_prompt=self._distill_prompt(),
                    session_granularity=getattr(self.settings, "book_session_granularity", "level2") or "level2",
                    toc_start_page=toc_start,
                )
                elapsed = self._fmt_elapsed(time.time() - t0)
                self.step_time.emit(f"[{name}] Phase B: {elapsed}")
                self.log.emit(
                    "完成: "
                    f"{result['page_count']} 页 / "
                    f"{result['chapter_count']} 章 / "
                    f"{result['chunk_count']} chunks / "
                    f"检索命中 {result['smoke_hits']} 条 / "
                    f"目标蒸馏 {result.get('notes_target_chapters', 0)} 章 / "
                    f"笔记生成 {result.get('notes_generated', 0)} 篇 / "
                    f"对话 {result.get('session_count', 0)} 个"
                )
                self.log.emit(f"总耗时: {result.get('total_elapsed', 0):.1f}s")
                if result.get("notes_skipped"):
                    self.log.emit(f"笔记缓存跳过: {result['notes_skipped']} 篇")
                cache_hits = result.get("cache_hits") or []
                if cache_hits:
                    self.log.emit(f"缓存命中: {', '.join(cache_hits)}")
                self.log.emit(f"book.json: {result['book_json_path']}")
                if result.get("session_count"):
                    self.sessions_changed.emit()
                ok_count += 1
            except Exception as exc:
                self._failed_videos.append(pdf_path)
                self.log.emit(f"失败: {exc}")

        self.progress.emit(1.0 if ok_count == total and not self._cancel else ok_count / max(1, total))
        if self._cancel:
            self.finished.emit(False, f"已停止，完成 {ok_count}/{total}")
        elif self._failed_videos:
            self.finished.emit(False, f"完成 {ok_count}/{total}，失败 {len(self._failed_videos)}")
        else:
            self.finished.emit(True, f"完成 {ok_count}/{total}")

    def _distill_prompt(self) -> str:
        level = getattr(self.settings, "book_distill_level", "high") or "high"
        prompts = getattr(self.settings, "book_distill_prompts", {}) or {}
        if level not in prompts:
            prompts = DEFAULT_BOOK_DISTILL_PROMPTS
        level_prompt = (prompts.get(level) or DEFAULT_BOOK_DISTILL_PROMPTS["high"]).strip()
        return (
            f"【蒸馏级别：{level}】\n"
            f"{level_prompt}"
            f"{RICH_TEXT_FORMATTING_PROMPT}"
        )


class _PromptPresetDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("蒸馏提示词设置")
        self.setMinimumSize(720, 560)
        if parent:
            self.setStyleSheet(parent.styleSheet())
        self._prompts = dict(DEFAULT_BOOK_DISTILL_PROMPTS)
        self._prompts.update(getattr(settings, "book_distill_prompts", {}) or {})

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(QLabel("级别"))
        self.level_combo = QComboBox()
        self.level_combo.addItems(BOOK_DISTILL_LEVELS)
        self.level_combo.setCurrentText(getattr(settings, "book_distill_level", "high") or "high")
        top.addWidget(self.level_combo)
        top.addStretch()
        layout.addLayout(top)

        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("输入该级别的章节蒸馏提示词...")
        layout.addWidget(self.prompt_edit, stretch=1)

        hint = QLabel("tiny 更短；medium 平衡；high 更细致；ultra 最深入。提示词会以专业教授、导师和知识博主的角度帮助用户阅读书籍。")
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._restore_default)
        layout.addWidget(buttons)

        self.level_combo.currentTextChanged.connect(self._on_level_changed)
        self.prompt_edit.textChanged.connect(self._save_current)
        self._current_level = self.level_combo.currentText()
        self.prompt_edit.setPlainText(self._prompts.get(self._current_level, ""))

    def _save_current(self):
        if hasattr(self, "_current_level"):
            self._prompts[self._current_level] = self.prompt_edit.toPlainText()

    def _on_level_changed(self, level: str):
        self._save_current()
        self._current_level = level
        self.prompt_edit.blockSignals(True)
        self.prompt_edit.setPlainText(self._prompts.get(level, ""))
        self.prompt_edit.blockSignals(False)

    def _restore_default(self):
        level = self.level_combo.currentText()
        self._prompts[level] = DEFAULT_BOOK_DISTILL_PROMPTS[level]
        self.prompt_edit.setPlainText(self._prompts[level])

    def get_prompts(self) -> dict:
        self._save_current()
        return dict(self._prompts)

    def current_level(self) -> str:
        return self.level_combo.currentText()


class _DropListWidget(QListWidget):
    """支持文件拖拽的 QListWidget"""
    filesDropped = Signal()
    BOOK_EXTS = {".pdf"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        added = False
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and Path(path).suffix.lower() in self.BOOK_EXTS:
                item = QListWidgetItem(path)
                item.setData(Qt.ItemDataRole.UserRole + 1, 1)
                self.addItem(item)
                added = True
        event.acceptProposedAction()
        if added:
            self.filesDropped.emit()
