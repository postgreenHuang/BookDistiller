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
    QToolButton, QListView, QScrollArea,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon
from src.config import (
    load_settings, save_settings, Settings,
)
from src.gui.theme import build_stylesheet
from src.gui.settings_dialog import SettingsDialog


def _format_slides_as_text(slides: list) -> str:
    """将 slides 列表格式化为结构化文本，让 AI 清晰看到文件名"""
    lines = []
    for i, s in enumerate(slides, 1):
        ts = s.get("timestamp", "")
        fname = s.get("file", "")
        title = s.get("title", "")
        text = s.get("text", "")
        diagrams = s.get("diagrams", "")
        lines.append(f"[截图{i}] 时间: {ts} | 文件: {fname}")
        if title:
            lines.append(f"  标题: {title}")
        if text:
            lines.append(f"  文字: {text}")
        if diagrams and diagrams != "无":
            lines.append(f"  图表: {diagrams}")
        lines.append("")
    return "\n".join(lines)


def _fix_image_refs(notes: str, slides: list) -> str:
    """后处理：修复 AI 输出中的错误图片引用 (img_N → 实际文件名)"""
    import re

    # 构建文件名映射：按顺序映射 img_0, img_1, ... 和 00_05 这种简写
    filenames = [s.get("file", "") for s in slides]

    # 替换 img_N 模式
    def _replace_img_n(m):
        idx = int(m.group(1))
        if idx < len(filenames) and filenames[idx]:
            return filenames[idx]
        return m.group(0)

    notes = re.sub(r"img_(\d+)", _replace_img_n, notes)

    # 替换只有时间戳没有 _frame.jpg 的情况，如 (00_05) → (00_05_frame.jpg)
    def _fix_bare_timestamp(m):
        mm, ss = m.group(1), m.group(2)
        candidate = f"{mm}_{ss}_frame.jpg"
        if candidate in filenames:
            return candidate
        return m.group(0)

    notes = re.sub(r"\((\d{2})_(\d{2})\)", _fix_bare_timestamp, notes)

    return notes


def _generate_notes(project_dir: str, video_name: str,
                    provider_config: dict, prompt: str) -> str:
    """Step 5 核心：读取 slides + transcript → 调 AI API → 写 notes.md → 创建 session

    Returns:
        生成的笔记文件路径
    Raises:
        RuntimeError: 没有可用数据源或配置不全
    """
    import json, requests
    from pathlib import Path

    slides_text = ""
    transcript_text = ""
    slides_list = []

    # 优先从统一 JSON 读取
    unified_json = os.path.join(project_dir, f"{video_name}.json")
    if os.path.exists(unified_json):
        udata = json.loads(Path(unified_json).read_text(encoding="utf-8"))
        slides = udata.get("slides", [])
        segments = udata.get("segments", [])
        if slides:
            slides_list = slides
            slides_text = _format_slides_as_text(slides)
        if segments:
            transcript_text = json.dumps(segments, ensure_ascii=False, indent=2)

    # 回退：旧格式
    if not slides_text:
        slides_path = os.path.join(project_dir, "slides.json")
        if os.path.exists(slides_path):
            raw = Path(slides_path).read_text(encoding="utf-8")
            try:
                sdata = json.loads(raw)
                if isinstance(sdata, list):
                    slides_list = sdata
                    slides_text = _format_slides_as_text(sdata)
                elif "slides" in sdata:
                    slides_list = sdata["slides"]
                    slides_text = _format_slides_as_text(sdata["slides"])
                else:
                    slides_text = raw
            except json.JSONDecodeError:
                slides_text = raw
    if not transcript_text:
        old_transcript = os.path.join(project_dir, "transcript", "transcript.json")
        if os.path.exists(old_transcript):
            transcript_text = Path(old_transcript).read_text(encoding="utf-8")

    if not slides_text and not transcript_text:
        raise RuntimeError("没有可用的数据源")

    base_url = provider_config.get("base_url", "").rstrip("/")
    api_key = provider_config.get("api_key", "")
    model = provider_config.get("model", "")

    if not base_url or not api_key:
        raise RuntimeError("请先在 Settings 中配置 Provider 的 URL 和 Key")

    url = base_url + "/chat/completions"
    user_content = f"--- 幻灯片描述 ---\n{slides_text}\n\n--- 语音转录 ---\n{transcript_text}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 100000,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=600)
    resp.raise_for_status()
    resp_data = resp.json()
    notes_text = resp_data["choices"][0]["message"]["content"].strip()
    finish_reason = resp_data["choices"][0].get("finish_reason", "")
    if slides_list:
        notes_text = _fix_image_refs(notes_text, slides_list)
    if finish_reason == "length":
        notes_text += "\n\n---\n> ⚠ 笔记被 max_tokens 截断，内容不完整。"

    notes_dir = os.path.join(project_dir, "notes")
    os.makedirs(notes_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    notes_path = os.path.join(notes_dir, f"{video_name}_{ts}.md")
    Path(notes_path).write_text(notes_text, encoding="utf-8")

    from src.chat import create_session
    create_session(project_dir, video_name, notes_path, provider_config)

    return notes_path


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

    # ─── 项目路径自动推导 ───

    def _get_project_dir(self) -> Path | None:
        video = self.video_path_edit.text().strip()
        output = self.output_dir_edit.text().strip()
        if not video or not output:
            return None
        return Path(output) / Path(video).stem

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
        workers = [
            getattr(self, '_batch_worker', None),
            getattr(self, '_generate_worker', None),
            getattr(self, '_audio_worker', None),
            getattr(self, '_frame_worker', None),
            getattr(self, '_frame_select_worker', None),
            getattr(self, '_vision_worker', None),
            getattr(self, '_transcribe_worker', None),
        ]
        for w in workers:
            if w is None:
                continue
            if hasattr(w, '_cancel'):
                w._cancel = True
            if hasattr(w, '_cancel_flag'):
                w._cancel_flag["cancel"] = True
        for w in workers:
            if w is not None and hasattr(w, 'isRunning') and w.isRunning():
                w.wait(3000)
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
            self._refresh_from_settings()
            self.chat_widget.apply_font_settings(
                self.settings.chat_font_family, self.settings.chat_font_scale
            )
            if theme_changed:
                from src.gui.theme import THEMES
                self.chat_widget.refresh_theme_styles(THEMES[self._theme])

    def _refresh_from_settings(self):
        self._refresh_provider_combo()
        self._refresh_model_combo()
        self._refresh_select_provider_combo()
        self._refresh_vision_combo()
        self._refresh_batch_combos()

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

        # Legacy single-video widgets are still built off-screen so existing
        # settings refresh code can run while Phase A removes the visible tab.
        self._build_path_bar_ref = self._build_path_bar()
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_step1(), "  Step 1  媒体提取  ")
        self.tabs.addTab(self._build_step2_transcribe(), "  Step 2  语音转录  ")
        self.tabs.addTab(self._build_step3_select(), "  Step 3  智能选帧  ")
        self.tabs.addTab(self._build_step_vision(), "  Step 4  图片理解  ")
        self.tabs.addTab(self._build_step5(), "  Step 5  AI 聚合  ")

        # 恢复上次路径（在所有 UI 构建之后）
        if self.settings.last_video_path:
            self.video_path_edit.setText(self.settings.last_video_path)
        if self.settings.last_output_dir:
            self.output_dir_edit.setText(self.settings.last_output_dir)
        if self.settings.last_video_path and self.settings.last_output_dir:
            self._auto_fill_step5_paths()

        # 顶层 Tab：Phase A 只保留批量蒸馏 + 对话
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

        layout.addWidget(self.top_tabs, stretch=1)

        self._force_qt_combobox()
        self._status_label = QLabel("就绪")
        self._status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.statusBar().addPermanentWidget(self._status_label, stretch=1)

        # 初始化模型下拉框
        self._refresh_model_combo()
        self._refresh_select_provider_combo()
        self._refresh_vision_combo()

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _on_top_tab_changed(self, index: int):
        """切换到对话 Tab 时扫描 session 列表"""
        if index != 1:
            return

        provider_config = {}
        for p in self.settings.providers:
            if p.get("api_key"):
                provider_config = p
                break

        self.chat_widget.set_providers(self.settings.providers)
        self.chat_widget.refresh_session_list(provider_config)

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
        self.batch_video_list.setToolTip("拖拽 PDF 文件到这里；第一版仅支持 PDF，扫描版 PDF 将通过图片识别/OCR 管线处理。")
        left.addWidget(self.batch_video_list, stretch=1)
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
        model_grid.addWidget(self._label("书籍整合"), row, 0)
        self.batch_agg_combo = QComboBox()
        model_grid.addWidget(self.batch_agg_combo, row, 1)

        row += 1
        model_grid.addWidget(self._label("Embedding"), row, 0)
        self.batch_embedding_combo = QComboBox()
        model_grid.addWidget(self.batch_embedding_combo, row, 1)

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
            (self.batch_agg_combo, "last_batch_agg"),
            (self.batch_embedding_combo, "last_batch_embedding"),
        ]
        for combo, _ in combos:
            combo.blockSignals(True)

        # 图片识别
        self.batch_vision_combo.clear()
        for v in s.vision_models:
            tag = "本地" if v["type"] == "ollama" else "云端"
            self.batch_vision_combo.addItem(f"{v['name']} [{tag}]", v)

        # 书籍整合
        self.batch_agg_combo.clear()
        for p in s.providers:
            if p.get("api_key") or p.get("base_url", "").startswith("http://localhost"):
                self.batch_agg_combo.addItem(f"{p['name']} ({p['model']})", p)

        # Embedding
        self.batch_embedding_combo.clear()
        for e in getattr(s, "embedding_models", []):
            tag = "本地" if e.get("type") == "ollama" else "云端"
            self.batch_embedding_combo.addItem(f"{e.get('name', e.get('model', 'Embedding'))} [{tag}]", e)

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
                self.batch_video_list.addItem(p)

    def _batch_remove_selected(self):
        for item in self.batch_video_list.selectedItems():
            self.batch_video_list.takeItem(self.batch_video_list.row(item))

    def _batch_clear_videos(self):
        self.batch_video_list.clear()

    def _batch_browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.batch_output_edit.setText(path)

    def _batch_retry(self):
        """重试上次失败的书籍。Phase B 接入 PDF 管线后启用。"""
        failed = getattr(self, "_pending_retry_videos", [])
        if not failed:
            return
        self.btn_batch_retry.setVisible(False)
        self.batch_progress.setValue(0)
        self.batch_log.append(f"\n── 等待 PDF 蒸馏管线接入后重试 {len(failed)} 本书籍 ──\n")

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
            books.append(self.batch_video_list.item(i).text())

        vision_data = self.batch_vision_combo.currentData()
        agg_data = self.batch_agg_combo.currentData()
        embedding_data = self.batch_embedding_combo.currentData()

        invalid = [p for p in books if Path(p).suffix.lower() != ".pdf"]
        if invalid:
            self.batch_log.append("第一版仅支持 PDF，请移除非 PDF 文件")
            return

        if not vision_data or not agg_data or not embedding_data:
            self.batch_log.append("请确保图片识别、书籍整合和 Embedding 模型均有可用配置")
            return

        self.batch_progress.setValue(0)
        self.batch_log.clear()
        self.batch_status.setText(f"已准备 {len(books)} 本书籍")
        self.batch_log.append(f"书籍蒸馏已就绪: {len(books)} 本 PDF\n")
        self.batch_log.append(f"输出目录: {output_dir}")
        self.batch_log.append(f"图片识别: {vision_data.get('name', vision_data.get('model', ''))}")
        self.batch_log.append(f"书籍整合: {agg_data.get('name', agg_data.get('model', ''))}")
        self.batch_log.append(f"Embedding: {embedding_data.get('name', embedding_data.get('model', ''))}")
        self.batch_log.append("\nPhase A 已完成入口切换。PDF 解析、扫描页识别、RAG 索引与章节对话将在 Phase B/F 接入。")
        self.batch_progress.setValue(5)

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

    # ─── 顶部路径栏 ───

    def _build_path_bar(self):
        group = QGroupBox("项目")
        grid = QGridLayout(group)
        grid.setSpacing(6)
        grid.setContentsMargins(12, 14, 12, 8)
        grid.setColumnStretch(1, 1)

        grid.addWidget(self._label("视频路径"), 0, 0)
        self.video_path_edit = QLineEdit()
        self.video_path_edit.setPlaceholderText("选择视频文件...")
        grid.addWidget(self.video_path_edit, 0, 1)
        btn = QPushButton("浏览")
        btn.setProperty("class", "secondary")
        btn.setFixedWidth(72)
        btn.clicked.connect(self._browse_video)
        grid.addWidget(btn, 0, 2)

        grid.addWidget(self._label("输出目录"), 1, 0)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("选择输出目录...")
        grid.addWidget(self.output_dir_edit, 1, 1)
        btn2 = QPushButton("浏览")
        btn2.setProperty("class", "secondary")
        btn2.setFixedWidth(72)
        btn2.clicked.connect(self._browse_output)
        grid.addWidget(btn2, 1, 2)

        return group

    def _browse_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.mkv *.avi *.mov *.webm);;所有文件 (*)"
        )
        if path:
            self.video_path_edit.setText(path)
            self.settings.last_video_path = path
            save_settings(self.settings)
            self._auto_fill_step5_paths()

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.output_dir_edit.setText(path)
            self.settings.last_output_dir = path
            save_settings(self.settings)
            self._auto_fill_step5_paths()

    # ─── Step 1: 媒体提取 ───

    def _build_step1(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("媒体提取")
        gl = QVBoxLayout(g)
        gl.setSpacing(6)
        gl.setContentsMargins(12, 14, 12, 8)
        self.btn_extract = QPushButton("开始提取")
        self.btn_extract.clicked.connect(self._start_extract)
        gl.addWidget(self.btn_extract)
        self.extract_progress = QProgressBar()
        gl.addWidget(self.extract_progress)
        self.extract_status = QLabel(" ")
        self.extract_status.setProperty("class", "status")
        gl.addWidget(self.extract_status)
        layout.addWidget(g)

        layout.addStretch()
        return w

    # ─── Step 2: 语音转录 (原 Step 3 提前) ───

    def _build_step2_transcribe(self):
        return self._build_step3_content()

    # ─── Step 4: 图片理解 ───

    def _build_step_vision(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        vg = QGroupBox("图片理解")
        vgl = QHBoxLayout(vg)
        vgl.setSpacing(6)
        vgl.setContentsMargins(12, 14, 12, 8)
        vgl.addWidget(self._label("视觉模型"))
        self.vision_model_combo = QComboBox()
        vgl.addWidget(self.vision_model_combo, stretch=1)
        self.btn_analyze = QPushButton("开始分析")
        self.btn_analyze.clicked.connect(self._start_analyze)
        vgl.addWidget(self.btn_analyze)
        layout.addWidget(vg)

        self.analysis_progress = QProgressBar()
        layout.addWidget(self.analysis_progress)
        self.token_label = QLabel(" ")
        self.token_label.setProperty("class", "hint")
        layout.addWidget(self.token_label)
        self.analysis_status = QLabel("建议先完成 Step 3 选帧 + Step 2 转录，再分析关键帧")
        self.analysis_status.setProperty("class", "status")
        layout.addWidget(self.analysis_status)
        layout.addWidget(vg)
        layout.addStretch()
        return w

    # ─── 转录 UI 构建 (Step 2 共用内容) ───

    def _build_step3_content(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("语音转录")
        gl = QHBoxLayout(g)
        gl.setSpacing(6)
        gl.setContentsMargins(12, 14, 12, 8)
        gl.addWidget(self._label("模型"))
        self.model_combo = QComboBox()
        gl.addWidget(self.model_combo, stretch=1)
        self.btn_transcribe = QPushButton("开始转录")
        self.btn_transcribe.clicked.connect(self._start_transcribe)
        gl.addWidget(self.btn_transcribe)
        layout.addWidget(g)

        self.transcribe_progress = QProgressBar()
        layout.addWidget(self.transcribe_progress)

        rg = QGroupBox("转录预览")
        rl = QVBoxLayout(rg)
        rl.setContentsMargins(12, 14, 12, 8)
        self.transcript_preview = QTextEdit()
        self.transcript_preview.setReadOnly(True)
        self.transcript_preview.setPlaceholderText("转录完成后在此显示...")
        rl.addWidget(self.transcript_preview)
        layout.addWidget(rg, stretch=1)

        return w

    # ─── Step 3: AI 智能选帧 (新) ───

    def _build_step3_select(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("智能选帧")
        gl = QHBoxLayout(g)
        gl.setSpacing(6)
        gl.setContentsMargins(12, 14, 12, 8)
        gl.addWidget(self._label("AI 模型"))
        self.select_provider_combo = QComboBox()
        gl.addWidget(self.select_provider_combo, stretch=1)
        self.btn_select_frames = QPushButton("开始选帧")
        self.btn_select_frames.clicked.connect(self._start_select_frames)
        gl.addWidget(self.btn_select_frames)
        layout.addWidget(g)

        self.select_progress = QProgressBar()
        layout.addWidget(self.select_progress)
        self.select_status = QLabel("需要先完成 Step 1 帧提取 + Step 2 转录")
        self.select_status.setProperty("class", "status")
        gl.addWidget(self.select_status)
        layout.addWidget(g)

        # 关键帧画廊
        gallery_group = QGroupBox("关键帧预览")
        gallery_layout = QVBoxLayout(gallery_group)
        gallery_layout.setContentsMargins(4, 14, 4, 4)

        self.gallery_scroll = QScrollArea()
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setMinimumHeight(120)

        self.gallery_container = QWidget()
        self.gallery_grid = QHBoxLayout(self.gallery_container)
        self.gallery_grid.setSpacing(4)
        self.gallery_grid.setContentsMargins(8, 4, 8, 4)
        self.gallery_grid.addStretch()

        self.gallery_scroll.setWidget(self.gallery_container)
        gallery_layout.addWidget(self.gallery_scroll)

        self.gallery_count_label = QLabel("选帧完成后在此显示关键帧缩略图")
        self.gallery_count_label.setProperty("class", "hint")
        self.gallery_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gallery_layout.addWidget(self.gallery_count_label)

        layout.addWidget(gallery_group, stretch=1)
        return w

    # ─── Step 5: AI 聚合 ───

    def _build_step5(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("AI 聚合")
        gl = QVBoxLayout(g)
        gl.setSpacing(4)
        gl.setContentsMargins(12, 14, 12, 8)

        # 数据源状态
        top = QGridLayout()
        top.setSpacing(6)
        top.setColumnStretch(1, 1)

        top.addWidget(self._label("数据源"), 0, 0)
        self.slides_json_edit = QLineEdit()
        self.slides_json_edit.setPlaceholderText("自动读取: {项目}/{视频名}.json")
        top.addWidget(self.slides_json_edit, 0, 1)
        btn_slides = QPushButton("浏览")
        btn_slides.setProperty("class", "secondary")
        btn_slides.setFixedWidth(56)
        btn_slides.clicked.connect(self._browse_slides_json)
        top.addWidget(btn_slides, 0, 2)



        top.addWidget(self._label("Provider"), 2, 0)
        prov_row = QHBoxLayout()
        prov_row.setSpacing(6)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("手动模式")
        self._refresh_provider_combo()
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        prov_row.addWidget(self.provider_combo, stretch=1)
        self.btn_generate = QPushButton("生成笔记并开始对话")
        self.btn_generate.setFixedWidth(160)
        self.btn_generate.clicked.connect(self._start_generate)
        prov_row.addWidget(self.btn_generate)
        self.btn_export = QPushButton("导出数据")
        self.btn_export.setProperty("class", "secondary")
        self.btn_export.setFixedWidth(100)
        self.btn_export.clicked.connect(self._on_export_data)
        prov_row.addWidget(self.btn_export)
        top.addLayout(prov_row, 2, 1, 1, 1)

        gl.addLayout(top)

        # Prompt（API 模式才显示）
        self.prompt_section = QWidget()
        pl = QVBoxLayout(self.prompt_section)
        pl.setContentsMargins(0, 4, 0, 0)
        pl.setSpacing(2)
        prompt_hdr = QLabel("蒸馏 Prompt")
        prompt_hdr.setStyleSheet("font-weight: 500; font-size: 12px;")
        pl.addWidget(prompt_hdr)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setFixedHeight(90)
        self.prompt_edit.setPlainText(self.settings.default_distill_prompt)
        pl.addWidget(self.prompt_edit)
        self.prompt_section.hide()
        gl.addWidget(self.prompt_section)

        # 生成进度
        self.generate_progress = QProgressBar()
        self.generate_progress.setVisible(False)
        gl.addWidget(self.generate_progress)

        # 状态提示
        self.generate_status = QLabel()
        self.generate_status.setProperty("class", "hint")
        gl.addWidget(self.generate_status)

        layout.addWidget(g)
        return w

    def _on_provider_changed(self, index):
        self.prompt_section.setVisible(index != 0)
        self.btn_generate.setVisible(index != 0)
        self.btn_export.setVisible(index == 0)

    def _on_export_data(self):
        """手动模式：将 slides + transcript 合并为 Markdown 导出到剪贴板"""
        json_path = self.slides_json_edit.text().strip()
        if not json_path or not os.path.exists(json_path):
            self.generate_status.setText("没有可用的数据源")
            return

        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        parts = []

        slides = data.get("slides", [])
        if slides:
            parts.append("## 幻灯片描述\n")
            for s in slides:
                ts = s.get("timestamp", "")
                title = s.get("title", "")
                text = s.get("text", "")
                diagrams = s.get("diagrams", "")
                line = f"[{ts}] **{title}**"
                if text:
                    line += f"\n{text}"
                if diagrams and diagrams != "无":
                    line += f"\n图表: {diagrams}"
                parts.append(line + "\n")

        segments = data.get("segments", [])
        if segments:
            parts.append("\n## 语音转录\n")
            for seg in segments:
                start = seg.get("start", 0)
                m, s = int(start) // 60, int(start) % 60
                parts.append(f"[{m:02d}:{s:02d}] {seg.get('text', '')}\n")

        if not parts:
            self.generate_status.setText("没有可导出的内容")
            return

        combined = "\n".join(parts)
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(combined)
        self.generate_status.setText(f"已复制到剪贴板 ({len(combined)} 字符)，可粘贴到 AI 网页版")

    def _start_generate(self):
        """调用 AI 生成蒸馏笔记，创建对话 session，跳转到对话 Tab"""
        index = self.provider_combo.currentIndex()
        if index <= 0:
            return

        provider_data = self.provider_combo.itemData(index)
        if not provider_data:
            return

        video_path = self.video_path_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()
        if not video_path or not output_dir:
            self.generate_status.setText("请先设置视频路径和输出目录")
            return

        from src.config import get_project_dir
        project_dir = str(get_project_dir(output_dir, video_path))

        slides_path = self.slides_json_edit.text().strip()
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            prompt = self.settings.default_distill_prompt

        if not slides_path:
            self.generate_status.setText("没有可用的数据源，请先完成前面的步骤")
            return

        self.btn_generate.setEnabled(False)
        self.generate_status.setText("正在生成笔记 0s...")
        self.generate_progress.setVisible(True)
        self.generate_progress.setRange(0, 0)
        self._gen_start_time = time.time()
        self._gen_timer = QTimer(self)
        self._gen_timer.timeout.connect(self._tick_gen_timer)
        self._gen_timer.start(1000)

        video_name = os.path.splitext(os.path.basename(video_path))[0]
        self._generate_worker = _GenerateWorker(
            provider_data, prompt,
            project_dir, video_name,
        )
        self._generate_worker.finished.connect(self._on_generate_done)
        self._generate_worker.error.connect(self._on_generate_error)
        self._generate_worker.start()

    def _tick_gen_timer(self):
        elapsed = int(time.time() - self._gen_start_time)
        m, s = divmod(elapsed, 60)
        self.generate_status.setText(
            f"正在生成笔记 {m}m{s:02d}s..." if m else f"正在生成笔记 {s}s..."
        )

    def _on_generate_done(self, notes_path: str):
        self._gen_timer.stop()
        elapsed = int(time.time() - self._gen_start_time)
        m, s = divmod(elapsed, 60)
        t = f"{m}m{s:02d}s" if m else f"{s}s"
        self.btn_generate.setEnabled(True)
        self.generate_progress.setVisible(False)
        self.generate_status.setText(f"笔记已保存: {os.path.basename(notes_path)} ({t})")
        # 跳转到对话 Tab
        self.top_tabs.setCurrentIndex(1)
        self.chat_widget.set_providers(self.settings.providers)
        provider_config = {}
        for p in self.settings.providers:
            if p.get("api_key"):
                provider_config = p
                break
        self.chat_widget.refresh_session_list(provider_config)
        # 选中最新的 session
        self.chat_widget._select_first_session()

    def _on_generate_error(self, err: str):
        self._gen_timer.stop()
        self.btn_generate.setEnabled(True)
        self.generate_progress.setVisible(False)
        self.generate_status.setText(f"生成失败: {err}")

    # ─── Step 5 数据源 ───

    def _browse_slides_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据 JSON", "", "JSON (*.json)"
        )
        if path:
            self.slides_json_edit.setText(path)

    def _auto_fill_step5_paths(self):
        video_path = self.video_path_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()
        if not video_path or not output_dir:
            return
        from src.config import get_unified_json_path
        unified_json = get_unified_json_path(output_dir, video_path)
        if unified_json.exists():
            self.slides_json_edit.setText(str(unified_json))
        else:
            self.slides_json_edit.clear()

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

    def _refresh_provider_combo(self):
        self.provider_combo.blockSignals(True)
        saved = self.settings.last_agg_provider
        self.provider_combo.clear()
        self.provider_combo.addItem("手动模式")
        for p in self.settings.providers:
            self.provider_combo.addItem(f"{p['name']} ({p['model']})", p)
        self._restore_combo(self.provider_combo, saved)
        self.provider_combo.blockSignals(False)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.provider_combo.currentTextChanged.connect(
            lambda t: self._save_combo("last_agg_provider", self.provider_combo)
        )

    def _refresh_model_combo(self):
        self.model_combo.blockSignals(True)
        saved = self.settings.last_asr_model
        self.model_combo.clear()
        if self.settings.asr_type == "cloud":
            for c in self.settings.asr_cloud_configs:
                self.model_combo.addItem(f"{c['name']} ({c['model']})")
        else:
            from src.config import WHISPER_MODELS
            self.model_combo.addItems(WHISPER_MODELS)
        self._restore_combo(self.model_combo, saved)
        self.model_combo.blockSignals(False)
        self.model_combo.currentTextChanged.connect(
            lambda t: self._save_combo("last_asr_model", self.model_combo)
        )

    def _refresh_select_provider_combo(self):
        if not hasattr(self, 'select_provider_combo'):
            return
        self.select_provider_combo.blockSignals(True)
        saved = self.settings.last_select_provider
        self.select_provider_combo.clear()
        for p in self.settings.providers:
            if p.get("api_key"):
                self.select_provider_combo.addItem(f"{p['name']} ({p['model']})", p)
        self._restore_combo(self.select_provider_combo, saved)
        self.select_provider_combo.blockSignals(False)
        self.select_provider_combo.currentTextChanged.connect(
            lambda t: self._save_combo("last_select_provider", self.select_provider_combo)
        )

    def _refresh_vision_combo(self):
        if not hasattr(self, 'vision_model_combo'):
            return
        self.vision_model_combo.blockSignals(True)
        saved = self.settings.vision_active
        self.vision_model_combo.clear()
        for v in self.settings.vision_models:
            tag = "本地" if v["type"] == "ollama" else "云端"
            self.vision_model_combo.addItem(f"{v['name']} [{tag}]", v)
        self._restore_combo(self.vision_model_combo, saved)
        self.vision_model_combo.blockSignals(False)
        self.vision_model_combo.currentTextChanged.connect(
            lambda t: self._save_combo("vision_active", self.vision_model_combo)
        )

    @staticmethod
    def _label(text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 500; color: #48484a;")
        return lbl

    # ─── Step 1: 媒体处理 (音频 → 帧自动链式执行) ───

    def _start_extract(self):
        project_dir = self._get_project_dir()
        if not project_dir:
            self.extract_status.setText("请先选择视频路径和输出目录")
            return

        self.btn_extract.setEnabled(False)
        self.extract_progress.setValue(0)
        self.extract_status.setText("正在提取音频...")

        self._audio_worker = _FFmpegWorker(
            "audio", self.video_path_edit.text().strip(), str(project_dir),
            sample_rate=self.settings.sample_rate,
        )
        self._audio_worker.progress.connect(lambda v: self.extract_progress.setValue(int(v * 50)))
        self._audio_worker.finished.connect(self._on_audio_done_chain)
        self._audio_worker.start()

    def _on_audio_done_chain(self, ok, result):
        if not ok:
            self.btn_extract.setEnabled(True)
            self.extract_status.setText(f"音频提取失败: {result}")
            return
        self.extract_status.setText("音频完成，正在抽取帧...")
        self._extract_project_dir = self._get_project_dir()
        self._frame_worker = _FFmpegWorker(
            "frames", self.video_path_edit.text().strip(), str(self._extract_project_dir),
            fps=1.0 / self.settings.frame_interval if self.settings.frame_interval > 0 else 1.0,
            resolution_scale=self.settings.resolution_scale,
        )
        self._frame_worker.progress.connect(lambda v: self.extract_progress.setValue(50 + int(v * 50)))
        self._frame_worker.finished.connect(self._on_frames_done_chain)
        self._frame_worker.start()

    def _on_frames_done_chain(self, ok, result):
        self.btn_extract.setEnabled(True)
        if ok:
            self.extract_progress.setValue(100)
            count = len([f for f in os.listdir(result) if f.endswith('.jpg')])
            self.extract_status.setText(f"完成: 音频 + {count} 帧")
        else:
            self.extract_status.setText(f"帧提取失败: {result}")

    # ─── Step 3: AI 智能选帧 ───

    def _start_select_frames(self):
        project_dir = self._get_project_dir()
        if not project_dir:
            self.select_status.setText("请先选择视频路径和输出目录")
            return

        frames_dir = project_dir / "frames"
        video_path = self.video_path_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()

        if not frames_dir.exists() or not list(frames_dir.glob("*.jpg")):
            self.select_status.setText("frames 目录为空，请先完成 Step 1 帧提取")
            return

        # 查找 transcript 数据路径（统一 JSON 或旧格式）
        transcript_path = ""
        if video_path and output_dir:
            from src.config import get_unified_json_path
            unified = get_unified_json_path(output_dir, video_path)
            if unified.exists():
                transcript_path = str(unified)
        if not transcript_path:
            old_path = project_dir / "transcript" / "transcript.json"
            if old_path.exists():
                transcript_path = str(old_path)

        if not transcript_path:
            self.select_status.setText("未找到转录数据，请先完成 Step 2 转录")
            return

        # 从下拉框获取选中的 provider
        provider_data = self.select_provider_combo.currentData()
        if not provider_data or not provider_data.get("api_key"):
            self.select_status.setText("请先在 Settings 中配置 AI Provider 的 Key")
            return

        self.btn_select_frames.setEnabled(False)
        self.select_progress.setValue(0)
        self.select_status.setText("正在分析转录文本，选取关键帧...")

        self._frame_select_worker = _FrameSelectWorker(
            str(transcript_path), str(frames_dir), str(project_dir),
            provider_data,
        )
        self._frame_select_worker.progress.connect(
            lambda v: self.select_progress.setValue(int(v * 100))
        )
        self._frame_select_worker.finished.connect(self._on_select_frames_done)
        self._frame_select_worker.start()

    def _on_select_frames_done(self, ok, result):
        self.btn_select_frames.setEnabled(True)
        if ok and self._frame_select_worker.result:
            r = self._frame_select_worker.result
            self.select_progress.setValue(100)
            self.select_status.setText(
                f"完成: 从 {r['total']} 帧中选出 {r['selected']} 帧关键帧"
            )
            self._load_gallery(r['output'])
        else:
            self.select_status.setText(f"失败: {result}")

    def _load_gallery(self, key_frames_dir: str):
        """加载关键帧缩略图到画廊"""
        # 清空旧内容
        while self.gallery_grid.count() > 1:
            item = self.gallery_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        if not key_frames_dir or not os.path.isdir(key_frames_dir):
            self.gallery_count_label.setText("未找到关键帧目录")
            return

        from PySide6.QtWidgets import QLabel as ImgLabel
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt

        frames = sorted(Path(key_frames_dir).glob("*.jpg"))
        if not frames:
            self.gallery_count_label.setText("关键帧目录为空")
            return

        thumb_size = 120
        for fp in frames:
            thumb = ImgLabel()
            thumb.setFixedSize(thumb_size, int(thumb_size * 9 / 16))
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setProperty("class", "gallery-thumb")
            pix = QPixmap(str(fp))
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(
                    thumb.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            thumb.setToolTip(f"{fp.name}")
            # 点击放大
            thumb.mousePressEvent = lambda e, p=str(fp): self._show_full_image(p)
            idx = self.gallery_grid.count() - 1
            self.gallery_grid.insertWidget(idx, thumb)

        self.gallery_count_label.setText(f"{len(frames)} 张关键帧（点击放大）")

    def _show_full_image(self, image_path: str):
        """弹出窗口显示完整图片"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt

        dlg = QDialog(self)
        dlg.setWindowTitle(os.path.basename(image_path))
        dlg.resize(1000, 600)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        label = QLabel()
        pix = QPixmap(image_path)
        label.setPixmap(pix)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(label)
        layout.addWidget(scroll)

        dlg.exec()

    # ─── Step 4: 图片理解 ───

    def _start_analyze(self):
        # 如果正在运行，则停止
        if hasattr(self, "_vision_worker") and self._vision_worker is not None and self._vision_worker.isRunning():
            self._vision_worker.cancel()
            return

        project_dir = self._get_project_dir()
        if not project_dir:
            self.analysis_status.setText("请先选择视频路径和输出目录")
            return

        key_frames_dir = project_dir / "key_frames"
        if not key_frames_dir.exists() or not list(key_frames_dir.glob("*.jpg")):
            self.analysis_status.setText("key_frames 目录为空，请先完成 Step 3 智能选帧")
            return

        # 从下拉框获取视觉模型配置
        s = self.settings
        vision_config = self.vision_model_combo.currentData()
        if not vision_config:
            self.analysis_status.setText("请先在 Settings 中配置视觉模型")
            return
        vision_config = dict(vision_config)
        if not vision_config.get("url"):
            vision_config["url"] = s.ollama_url

        prompts = {
            "ocr": s.vision_prompt_ocr,
            "diagram": s.vision_prompt_diagram,
            "title": s.vision_prompt_title,
            "single": s.vision_prompt_single,
        }

        # 尝试加载 transcript 作为上下文
        transcript_segments = []
        video_path = self.video_path_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()
        if video_path and output_dir:
            from src.config import get_unified_json_path
            unified_path = get_unified_json_path(output_dir, video_path)
            if unified_path.exists():
                try:
                    tdata = json.loads(unified_path.read_text(encoding="utf-8"))
                    transcript_segments = tdata.get("segments", [])
                except Exception:
                    pass
            else:
                # 回退兼容旧格式
                transcript_path = project_dir / "transcript" / "transcript.json"
                if transcript_path.exists():
                    try:
                        with open(transcript_path, "r", encoding="utf-8") as f:
                            tdata = json.load(f)
                        transcript_segments = tdata.get("segments", [])
                    except Exception:
                        pass

        # 获取统一 JSON 路径用于写入
        unified_json_path = ""
        if video_path and output_dir:
            from src.config import get_unified_json_path
            unified_json_path = str(get_unified_json_path(output_dir, video_path))

        self.analysis_progress.setValue(0)
        self.token_label.setText(" ")
        self.analysis_status.setText(
            f"正在分析 ({vision_config['model']})..."
        )
        self.btn_analyze.setText("停止分析")

        self._vision_worker = _VisionWorker(
            str(key_frames_dir), str(project_dir), vision_config, prompts,
            transcript_segments, unified_json_path,
            max_concurrent=self.settings.vision_concurrent,
        )
        self._vision_worker.progress.connect(
            lambda v: self.analysis_progress.setValue(int(v * 100))
        )
        self._vision_worker.token_update.connect(self._on_token_update)
        self._vision_worker.finished.connect(self._on_analyze_done)
        self._vision_worker.start()

    def _on_analyze_done(self, ok, result):
        self.btn_analyze.setText("开始分析")
        r = self._vision_worker.result if self._vision_worker else None
        self._vision_worker = None
        if ok and r:
            suffix = " (已提前停止)" if r.get("cancelled") else ""
            self.analysis_progress.setValue(100)
            self.analysis_status.setText(
                f"完成{suffix}: {r['total_slides']} 张关键帧 → {r['output']}"
            )
            self._auto_fill_step5_paths()
        else:
            self.analysis_status.setText(f"失败: {result}")

    def _on_token_update(self, tokens: dict):
        p = tokens.get("prompt", 0)
        c = tokens.get("completion", 0)
        t = tokens.get("total", 0)
        calls = tokens.get("calls", 0)
        self.token_label.setText(
            f"Token: {p:,} prompt + {c:,} completion = {t:,} total  |  API 调用: {calls}"
        )

    # ─── Step 3: 语音转录 ───

    def _start_transcribe(self):
        project_dir = self._get_project_dir()
        if not project_dir:
            self._set_status("请先选择视频路径和输出目录")
            return

        # 查找音频文件
        audio_dir = project_dir / "audio"
        if not audio_dir.exists():
            self._set_status("请先完成音频提取 (Step 1)")
            return
        mp3_files = list(audio_dir.glob("*.mp3"))
        if not mp3_files:
            self._set_status("未找到 MP3 文件，请先完成音频提取")
            return
        audio_path = str(mp3_files[0])

        # 从 settings 获取 ASR 配置
        s = self.settings
        asr_type = s.asr_type
        language = s.whisper_language if s.whisper_language else None

        if asr_type == "cloud":
            cloud_config = None
            for c in s.asr_cloud_configs:
                if c["name"] == s.asr_cloud_active:
                    cloud_config = c
                    break
            if not cloud_config and s.asr_cloud_configs:
                cloud_config = s.asr_cloud_configs[0]
            if not cloud_config:
                self._set_status("请先在 Settings 中配置云端 ASR")
                return
            model = cloud_config["model"]
            asr_api_url = cloud_config["base_url"]
            asr_api_key = cloud_config["api_key"]
            asr_api_type = cloud_config.get("api_type", "whisper")
        else:
            model = s.whisper_model
            asr_api_url = ""
            asr_api_key = ""
            asr_api_type = "whisper"

        # 使用第一个词表
        vocabulary = s.vocabularies[0]["terms"] if s.vocabularies else None

        self.btn_transcribe.setEnabled(False)
        self.transcribe_progress.setValue(0)
        self._set_status(f"正在转录 ({model})...")

        self._transcribe_worker = _TranscribeWorker(
            audio_path, str(project_dir), asr_type,
            model, language, vocabulary, s.segment_length,
            asr_api_url, asr_api_key, asr_api_type,
            progress_cb=lambda v: self.transcribe_progress.setValue(int(v * 100)),
            video_path=self.video_path_edit.text().strip(),
            batch_size=s.whisper_batch_size,
        )
        self._transcribe_worker.progress.connect(lambda v: self.transcribe_progress.setValue(int(v * 100)))
        self._transcribe_worker.finished.connect(self._on_transcribe_done)
        self._transcribe_worker.start()

    def _on_transcribe_done(self, ok, result):
        self.btn_transcribe.setEnabled(True)
        if ok and self._transcribe_worker.result:
            self.transcribe_progress.setValue(100)
            r = self._transcribe_worker.result
            from src.transcribe import build_preview_text
            self.transcript_preview.setPlainText(build_preview_text(r["segments"]))
            meta = r["metadata"]
            self._set_status(
                f"转录完成: {meta['total_segments']} 段, "
                f"{meta['total_chars']} 字 "
                f"({meta['asr_type']}, {meta['model']})"
            )
        else:
            self._set_status(f"转录失败: {result}")


class _FFmpegWorker(QThread):
    progress = Signal(float)
    finished = Signal(bool, str)

    def __init__(self, task, video, project_dir, **kwargs):
        super().__init__()
        self.task = task
        self.video = video
        self.project_dir = project_dir
        self.kwargs = kwargs

    def run(self):
        from src.media import extract_audio, extract_frames
        try:
            pd = Path(self.project_dir)
            pd.mkdir(parents=True, exist_ok=True)
            for sub in ("audio", "frames", "key_frames", "transcript", "notes"):
                (pd / sub).mkdir(exist_ok=True)

            if self.task == "audio":
                path = extract_audio(
                    self.video, str(pd / "audio"),
                    sample_rate=self.kwargs.get("sample_rate", 16000),
                    progress_cb=lambda v: self.progress.emit(v),
                )
                self.finished.emit(True, path)
            else:
                res = self.kwargs.get("resolution_scale") or "1/2"
                path = extract_frames(
                    self.video, str(pd),
                    fps=self.kwargs.get("fps", 1.0),
                    resolution_scale=res,
                    progress_cb=lambda v: self.progress.emit(v),
                )
                self.finished.emit(True, path)
        except Exception as e:
            self.finished.emit(False, str(e))


class _FrameSelectWorker(QThread):
    progress = Signal(float)
    finished = Signal(bool, str)

    def __init__(self, transcript_path, frames_dir, output_dir, provider_config):
        super().__init__()
        self.transcript_path = transcript_path
        self.frames_dir = frames_dir
        self.output_dir = output_dir
        self.provider_config = provider_config
        self.result = None

    def run(self):
        from src.frame_selector import select_frames
        try:
            self.result = select_frames(
                self.transcript_path, self.frames_dir, self.output_dir,
                self.provider_config,
                progress_cb=lambda v: self.progress.emit(v),
            )
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class _TranscribeWorker(QThread):
    progress = Signal(float)
    finished = Signal(bool, str)

    def __init__(self, audio_path, project_dir, asr_type, model, language,
                 vocabulary, segment_length, asr_api_url, asr_api_key,
                 asr_api_type="whisper", progress_cb=None, video_path="",
                 batch_size=16):
        super().__init__()
        self.audio_path = audio_path
        self.project_dir = project_dir
        self.asr_type = asr_type
        self.model = model
        self.language = language
        self.vocabulary = vocabulary
        self.segment_length = segment_length
        self.asr_api_url = asr_api_url
        self.asr_api_key = asr_api_key
        self.asr_api_type = asr_api_type
        self.result = None
        self._progress_cb = progress_cb
        self.video_path = video_path
        self.batch_size = batch_size

    def run(self):
        from src.transcribe import transcribe
        try:
            self.result = transcribe(
                audio_path=self.audio_path,
                output_dir=self.project_dir,
                video_path=self.video_path,
                asr_type=self.asr_type,
                model=self.model,
                language=self.language,
                vocabulary=self.vocabulary,
                segment_length=self.segment_length,
                asr_api_url=self.asr_api_url,
                asr_api_key=self.asr_api_key,
                asr_cloud_model=self.model,
                asr_api_type=self.asr_api_type,
                progress_cb=lambda v: self.progress.emit(v),
                batch_size=self.batch_size,
            )
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class _VisionWorker(QThread):
    progress = Signal(float)
    token_update = Signal(dict)
    finished = Signal(bool, str)

    def __init__(self, key_frames_dir, output_dir, vision_config, prompts,
                 transcript_segments=None, unified_json_path="",
                 max_concurrent=1):
        super().__init__()
        self.key_frames_dir = key_frames_dir
        self.output_dir = output_dir
        self.vision_config = vision_config
        self.prompts = prompts
        self.transcript_segments = transcript_segments or []
        self._cancel_flag = {"cancel": False}
        self.result = None
        self.unified_json_path = unified_json_path
        self.max_concurrent = max_concurrent

    def cancel(self):
        self._cancel_flag["cancel"] = True

    def run(self):
        from src.image_analysis import analyze_images
        try:
            result = analyze_images(
                self.key_frames_dir, self.output_dir,
                self.vision_config, self.prompts,
                progress_cb=lambda v: self.progress.emit(v),
                cancel_flag=self._cancel_flag,
                token_cb=lambda d: self.token_update.emit(d),
                transcript_segments=self.transcript_segments,
                max_concurrent=self.max_concurrent,
                unified_json_path=self.unified_json_path,
            )
            self.result = result
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class _GenerateWorker(QThread):
    finished = Signal(str)   # notes_path
    error = Signal(str)

    def __init__(self, provider_config: dict, prompt: str,
                 slides_path: str, transcript_path: str,
                 project_dir: str, video_name: str):
        super().__init__()
        self.provider_config = provider_config
        self.prompt = prompt
        self.project_dir = project_dir
        self.video_name = video_name

    def run(self):
        try:
            notes_path = _generate_notes(
                self.project_dir, self.video_name,
                self.provider_config, self.prompt,
            )
            self.finished.emit(notes_path)
        except Exception as e:
            self.error.emit(str(e))


class _BatchWorker(QThread):
    progress = Signal(float)
    video_progress = Signal(int, int)
    log = Signal(str)
    step_time = Signal(str)       # "[视频名] Step N 耗时 Xm Xs"
    step_start = Signal(str)      # 当前步骤名称，用于实时计时
    finished = Signal(bool, str)

    def __init__(self, videos, output_dir, settings,
                 asr_data, select_provider, vision_config, agg_provider):
        super().__init__()
        self.videos = videos
        self.output_dir = output_dir
        self.settings = settings
        self.asr_data = asr_data
        self.select_provider = select_provider
        self.vision_config = vision_config
        self.agg_provider = agg_provider
        self._cancel = False
        self._failed_videos: list[str] = []

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        return f"{s // 60}m {s % 60:02d}s"

    # ─── 单视频 DAG 定义 ───

    _STEP_DAG = {
        "1a": ([],         "Step 1a 音频提取"),
        "1b": ([],         "Step 1b 帧提取"),
        "2":  (["1a"],     "Step 2 语音转录"),
        "3":  (["1b", "2"],"Step 3 智能选帧"),
        "4":  (["3", "2"], "Step 4 图片理解"),
        "5":  (["4"],      "Step 5 AI 聚合"),
    }

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        from src.config import get_project_dir
        from src.pipeline import get_completed_steps

        total = len(self.videos)
        success = 0
        failed = 0

        # ─── 收集视频信息 ───
        self.log.emit(f"准备批量蒸馏: {total} 个视频\n")
        # tasks: [(vi, name, video_path, project_dir, skip_set), ...]
        tasks = []
        for i, video_path in enumerate(self.videos):
            if self._cancel:
                break
            name = Path(video_path).stem
            try:
                project_dir = str(get_project_dir(self.output_dir, video_path))
                completed_steps = get_completed_steps(project_dir)
                skip = set()
                for si, ok in enumerate(completed_steps):
                    if not ok:
                        break
                    skip.add(str(si + 1))
                if all(completed_steps):
                    self.log.emit(f"[{name}] 已全部完成，跳过")
                    success += 1
                    self.video_progress.emit(i + 1, total)
                    continue
                if skip:
                    self.log.emit(f"[{name}] 跳过 Step {','.join(sorted(skip))} (已完成)")
                tasks.append((i, name, video_path, project_dir, skip))
            except Exception as e:
                self.log.emit(f"[{name}] 初始化失败: {e}")
                failed += 1
                self._failed_videos.append(video_path)
                self.video_progress.emit(i + 1, total)

        # ─── 阶段一：Step 1 并行提取所有视频的媒体 ───
        need_step1 = [t for t in tasks if "1" not in t[4]]
        step1_ok = set()
        if need_step1 and not self._cancel:
            self.log.emit(f"阶段一：并行媒体提取 ({len(need_step1)} 个视频)")
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {}
                for vi, name, vpath, pdir, _skip in need_step1:
                    f = pool.submit(self._do_step_1_full, vpath, pdir, name, vi, total)
                    futures[f] = (vi, name)
                while futures:
                    if self._cancel:
                        break
                    done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
                    for f in done:
                        vi, vname = futures.pop(f)
                        try:
                            f.result()
                            step1_ok.add(vi)
                        except Exception as e:
                            self.log.emit(f"[{vname}] ✗ Step 1 失败: {e}")
                            failed += 1
                            self._failed_videos.append(self.videos[vi])
                            self.video_progress.emit(success + failed, total)

        # ─── 构建活跃列表：Step 1 通过的视频 ───
        active = []
        for vi, name, vpath, pdir, skip in tasks:
            if "1" in skip or vi in step1_ok:
                active.append((vi, name, vpath, pdir, skip))

        failed_set = set()          # vi of videos that failed at steps 2-5
        completed_results = {}      # 用于步骤间传递结果 (e.g. Step3→Step4)

        step_fns = {
            "2": self._do_step_2,
            "3": self._do_step_3,
            "4": self._do_step_4,
            "5": self._do_step_5,
        }
        step_labels = {"2": "语音转录", "3": "智能选帧", "4": "图片理解", "5": "AI 聚合"}

        for step_num in ("2", "3", "4", "5"):
            if self._cancel:
                break
            need = [t for t in active
                    if step_num not in t[4] and t[0] not in failed_set]
            if not need:
                continue

            self.log.emit(
                f"\n══ Step {step_num} {step_labels[step_num]} "
                f"({len(need)} 个视频) ══")

            for vi, name, vpath, pdir, skip in need:
                if self._cancel:
                    break
                try:
                    result = step_fns[step_num](
                        vpath, pdir, name, vi, total, completed_results, name)
                    if isinstance(result, dict):
                        completed_results[f"{vi}:{step_num}"] = result
                except Exception as e:
                    self.log.emit(f"[{name}] ✗ Step {step_num} 失败: {e}")
                    failed += 1
                    failed_set.add(vi)
                    self._failed_videos.append(vpath)

            self.video_progress.emit(success + failed, total)

            # Step 2 全部完成后释放 GPU 显存（避免反复加载卸载 Whisper）
            if step_num == "2":
                asr_uses_local = not (
                    self.asr_data and self.asr_data.get("type") != "local")
                if asr_uses_local:
                    import gc
                    gc.collect()
                    try:
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                            self.log.emit("GPU 显存已释放 (Whisper)")
                    except ImportError:
                        pass

        success += len(active) - len(failed_set)
        self.video_progress.emit(success + failed, total)

        msg = f"完成 {success}/{total}"
        if failed:
            msg += f"，失败 {failed}"
        if self._cancel:
            msg = f"已停止 — {msg}"
        self.finished.emit(not self._cancel, msg)

    def _do_step_1_full(self, video_path, project_dir, name, vi, total):
        """执行完整的 Step 1（音频 + 帧提取），用于并行阶段"""
        self._do_step_1a(video_path, project_dir, name, vi, total, {}, name)
        self._do_step_1b(video_path, project_dir, name, vi, total, {}, name)

    # ─── 各步骤实现 ───

    def _do_step_1a(self, video_path, project_dir, name, vi, total, completed, vname):
        if self._cancel:
            raise RuntimeError("已取消")
        from src.media import extract_audio
        _t0 = __import__("time").time()
        self.step_start.emit(f"[{vname}] Step 1a 音频提取")
        self.log.emit(f"[{vname}] Step 1a 音频提取...")
        audio_dir = os.path.join(project_dir, "audio")
        extract_audio(
            video_path, audio_dir,
            sample_rate=self.settings.sample_rate,
            progress_cb=lambda v: self.progress.emit(min(vi / total + 0.025 / total * v, 1.0)),
        )
        _dt = self._fmt_elapsed(__import__("time").time() - _t0)
        self.step_time.emit(f"[{vname}] Step 1a: {_dt}")
        self.log.emit(f"[{vname}] Step 1a 完成 ({_dt})")
        return True

    def _do_step_1b(self, video_path, project_dir, name, vi, total, completed, vname):
        if self._cancel:
            raise RuntimeError("已取消")
        from src.media import extract_frames
        s = self.settings
        _t0 = __import__("time").time()
        self.step_start.emit(f"[{vname}] Step 1b 帧提取")
        self.log.emit(f"[{vname}] Step 1b 帧提取...")
        fps = 1.0 / s.frame_interval if s.frame_interval > 0 else 1.0
        extract_frames(
            video_path, project_dir,
            fps=fps, resolution_scale=s.resolution_scale,
            progress_cb=lambda v: self.progress.emit(min(vi / total + 0.025 / total * v, 1.0)),
        )
        _dt = self._fmt_elapsed(__import__("time").time() - _t0)
        self.step_time.emit(f"[{vname}] Step 1b: {_dt}")
        self.log.emit(f"[{vname}] Step 1b 完成 ({_dt})")
        return True

    def _do_step_2(self, video_path, project_dir, name, vi, total, completed, vname):
        if self._cancel:
            raise RuntimeError("已取消")
        from src.transcribe import transcribe
        s = self.settings
        _t0 = __import__("time").time()
        self.step_start.emit(f"[{vname}] Step 2 语音转录")
        self.log.emit(f"[{vname}] Step 2 语音转录...")

        audio_dir = os.path.join(project_dir, "audio")
        mp3_files = list(Path(audio_dir).glob("*.mp3"))
        if not mp3_files:
            raise RuntimeError("未生成音频文件")
        audio_path = str(mp3_files[0])

        language = s.whisper_language if s.whisper_language else None
        vocabulary = s.vocabularies[0]["terms"] if s.vocabularies else None

        asr_type = "cloud" if self.asr_data and self.asr_data.get("type") != "local" else "local"
        if asr_type == "cloud":
            model = self.asr_data["model"]
            asr_api_url = self.asr_data.get("base_url", "")
            asr_api_key = self.asr_data.get("api_key", "")
            asr_api_type = self.asr_data.get("api_type", "whisper")
        else:
            model = self.asr_data.get("model", s.whisper_model) if self.asr_data else s.whisper_model
            asr_api_url = ""
            asr_api_key = ""
            asr_api_type = "whisper"

        _last_pct = [0]

        def _step2_progress(v):
            self.progress.emit(min((vi + 0.05 + 0.15 * v) / total, 1.0))
            pct = int(v * 100)
            if pct >= _last_pct[0] + 10:
                _last_pct[0] = pct
                self.log.emit(f"[{vname}] Step 2 转录 {pct}%")

        transcribe(
            audio_path=audio_path,
            output_dir=project_dir,
            video_path=video_path,
            asr_type=asr_type,
            model=model,
            language=language,
            vocabulary=vocabulary,
            segment_length=s.segment_length,
            asr_api_url=asr_api_url,
            asr_api_key=asr_api_key,
            asr_cloud_model=model,
            asr_api_type=asr_api_type,
            progress_cb=_step2_progress,
            batch_size=s.whisper_batch_size,
        )
        _dt = self._fmt_elapsed(__import__("time").time() - _t0)
        self.step_time.emit(f"[{vname}] Step 2: {_dt}")
        self.log.emit(f"[{vname}] Step 2 完成 ({_dt})")

        return True

    def _do_step_3(self, video_path, project_dir, name, vi, total, completed, vname):
        if self._cancel:
            raise RuntimeError("已取消")
        from src.frame_selector import select_frames
        _t0 = __import__("time").time()
        self.step_start.emit(f"[{vname}] Step 3 智能选帧")
        self.log.emit(f"[{vname}] Step 3 智能选帧...")

        unified_path = os.path.join(project_dir, f"{name}.json")
        if os.path.exists(unified_path):
            transcript_path = unified_path
        else:
            transcript_path = os.path.join(project_dir, "transcript", "transcript.json")
        frames_dir = os.path.join(project_dir, "frames")

        select_result = select_frames(
            transcript_path, frames_dir, project_dir,
            self.select_provider,
            progress_cb=lambda v: self.progress.emit(min((vi + 0.20 + 0.05 * v) / total, 1.0)),
        )
        _dt = self._fmt_elapsed(__import__("time").time() - _t0)
        self.step_time.emit(f"[{vname}] Step 3: {_dt}")
        self.log.emit(f"[{vname}] Step 3 选出 {select_result['selected']} 帧 ({_dt})")
        return select_result

    def _do_step_4(self, video_path, project_dir, name, vi, total, completed, vname):
        if self._cancel:
            raise RuntimeError("已取消")
        import json
        from src.image_analysis import analyze_images
        s = self.settings

        key_frames_dir = os.path.join(project_dir, "key_frames")
        has_slides = (os.path.exists(key_frames_dir)
                      and list(Path(key_frames_dir).glob("*.jpg")))
        # 也检查 step 3 的结果
        step3_key = f"{vi}:3"
        if step3_key in completed and isinstance(completed[step3_key], dict):
            has_slides = has_slides and completed[step3_key].get("selected", 0) > 0

        if not has_slides:
            self.log.emit(f"[{vname}] Step 4 跳过（无关键帧，仅使用转录）")
            return True

        _t0 = __import__("time").time()
        self.step_start.emit(f"[{vname}] Step 4 图片理解")
        self.log.emit(f"[{vname}] Step 4 图片理解...")

        vision_cfg = dict(self.vision_config) if self.vision_config else {}
        if not vision_cfg.get("url"):
            vision_cfg["url"] = s.ollama_url

        unified_path = os.path.join(project_dir, f"{name}.json")
        if os.path.exists(unified_path):
            transcript_path = unified_path
        else:
            transcript_path = os.path.join(project_dir, "transcript", "transcript.json")

        transcript_segments = []
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            transcript_segments = tdata.get("segments", [])
        except Exception:
            pass

        prompts = {
            "ocr": s.vision_prompt_ocr,
            "diagram": s.vision_prompt_diagram,
            "title": s.vision_prompt_title,
            "single": s.vision_prompt_single,
        }

        from src.image_analysis import calc_auto_concurrency
        actual_conc = calc_auto_concurrency(s.vision_concurrent) if (
            vision_cfg.get("type", "ollama") == "ollama" and s.vision_concurrent > 1) else s.vision_concurrent
        if actual_conc != s.vision_concurrent:
            self.log.emit(f"[{vname}] 显存不足，并发 {s.vision_concurrent} → {actual_conc}")

        analyze_images(
            key_frames_dir, project_dir, vision_cfg, prompts,
            progress_cb=lambda v: self.progress.emit(min((vi + 0.25 + 0.35 * v) / total, 1.0)),
            transcript_segments=transcript_segments,
            unified_json_path=os.path.join(project_dir, f"{name}.json"),
            max_concurrent=actual_conc,
        )
        _dt = self._fmt_elapsed(__import__("time").time() - _t0)
        self.step_time.emit(f"[{vname}] Step 4: {_dt}")
        self.log.emit(f"[{vname}] Step 4 完成 ({_dt})")
        return True

    def _do_step_5(self, video_path, project_dir, name, vi, total, completed, vname):
        if self._cancel:
            raise RuntimeError("已取消")
        _t0 = __import__("time").time()
        self.step_start.emit(f"[{vname}] Step 5 AI 聚合")
        self.log.emit(f"[{vname}] Step 5 AI 聚合...")

        _generate_notes(
            project_dir, name,
            self.agg_provider, self.settings.default_distill_prompt,
        )

        _dt = self._fmt_elapsed(__import__("time").time() - _t0)
        self.step_time.emit(f"[{vname}] Step 5: {_dt}")
        self.log.emit(f"[{vname}] ✓ 笔记已生成 ({_dt})\n")
        return True


class _DropListWidget(QListWidget):
    """支持文件拖拽的 QListWidget"""
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
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and Path(path).suffix.lower() in self.BOOK_EXTS:
                self.addItem(path)
        event.acceptProposedAction()
