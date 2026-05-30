"""
Book-Distiller Settings 对话框
保留旧视频配置页以兼容历史设置，新增书籍蒸馏配置。
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QComboBox, QTextEdit,
    QGroupBox, QGridLayout, QListView, QCheckBox,
    QDialogButtonBox, QSpinBox, QDoubleSpinBox, QScrollArea,
)
from PySide6.QtCore import Qt
from src.config import (
    Settings, save_settings, RESOLUTION_SCALES, WHISPER_MODELS,
    ASR_CLOUD_MODELS,
)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Book-Distiller 设置")
        self.setMinimumSize(600, 580)
        if parent:
            self.setStyleSheet(parent.styleSheet())
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "通用")
        tabs.addTab(self._build_book_tab(), "书籍蒸馏")
        tabs.addTab(self._build_vision_tab(), "图片识别")
        tabs.addTab(self._build_asr_tab(), "语音识别")
        tabs.addTab(self._build_aggregation_tab(), "AI 聚合")
        tabs.addTab(self._build_quick_questions_tab(), "快捷提问")
        layout.addWidget(tabs)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # 强制所有 ComboBox 使用 Qt 内置弹窗 (Windows 原生弹窗不响应 QSS)
        self._force_qt_combobox()

    # ════════════════════════════════════════════
    # Tab 1: 通用
    # ════════════════════════════════════════════

    def _build_general_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 4, 0)

        # 默认参数
        g = QGroupBox("默认参数")
        grid = QGridLayout(g)
        grid.setSpacing(6)

        row = 0
        grid.addWidget(QLabel("主题:"), row, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        grid.addWidget(self.theme_combo, row, 1)

        row += 1
        grid.addWidget(QLabel("抽帧分辨率:"), row, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTION_SCALES)
        grid.addWidget(self.resolution_combo, row, 1)

        row += 1
        grid.addWidget(QLabel("采样率:"), row, 0)
        self.sample_rate_spin = QSpinBox()
        self.sample_rate_spin.setRange(8000, 48000)
        self.sample_rate_spin.setSingleStep(1000)
        grid.addWidget(self.sample_rate_spin, row, 1)

        row += 1
        grid.addWidget(QLabel("抽帧间隔 (秒):"), row, 0)
        self.frame_interval_spin = QDoubleSpinBox()
        self.frame_interval_spin.setRange(0.5, 30.0)
        self.frame_interval_spin.setSingleStep(0.5)
        self.frame_interval_spin.setValue(1.0)
        grid.addWidget(self.frame_interval_spin, row, 1)

        row += 1
        grid.addWidget(QLabel("SSIM 阈值:"), row, 0)
        self.ssim_spin = QDoubleSpinBox()
        self.ssim_spin.setRange(0.80, 0.99)
        self.ssim_spin.setSingleStep(0.01)
        self.ssim_spin.setDecimals(2)
        grid.addWidget(self.ssim_spin, row, 1)

        row += 1
        grid.addWidget(QLabel("转录分段 (秒):"), row, 0)
        self.segment_spin = QSpinBox()
        self.segment_spin.setRange(30, 600)
        self.segment_spin.setSingleStep(30)
        self.segment_spin.setValue(180)
        grid.addWidget(self.segment_spin, row, 1)

        layout.addWidget(g)

        # 对话字体
        fg = QGroupBox("对话字体")
        fgl = QGridLayout(fg)
        fgl.setSpacing(6)

        fgl.addWidget(QLabel("字体:"), 0, 0)
        self.font_family_combo = QComboBox()
        from PySide6.QtGui import QFontDatabase
        db = QFontDatabase()
        families = db.families()
        self.font_family_combo.addItem("默认")
        self.font_family_combo.addItems(families)
        self.font_family_combo.setEditable(True)
        fgl.addWidget(self.font_family_combo, 0, 1)

        fgl.addWidget(QLabel("缩放 (%):"), 1, 0)
        self.font_scale_spin = QSpinBox()
        self.font_scale_spin.setRange(50, 200)
        self.font_scale_spin.setSingleStep(10)
        self.font_scale_spin.setValue(100)
        self.font_scale_spin.setSuffix("%")
        fgl.addWidget(self.font_scale_spin, 1, 1)

        layout.addWidget(fg)

        # Ollama 地址
        og = QGroupBox("Ollama 服务")
        ogl = QGridLayout(og)
        ogl.setSpacing(6)
        ogl.addWidget(QLabel("地址:"), 0, 0)
        self.ollama_url_edit = QLineEdit()
        self.ollama_url_edit.setPlaceholderText("http://localhost:11434")
        ogl.addWidget(self.ollama_url_edit, 0, 1)
        layout.addWidget(og)

        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    # ════════════════════════════════════════════
    # Tab 2: 书籍蒸馏
    # ════════════════════════════════════════════

    def _build_book_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 4, 0)

        g = QGroupBox("书籍输入")
        grid = QGridLayout(g)
        grid.setSpacing(6)
        grid.setColumnStretch(1, 1)

        row = 0
        self.book_pdf_only_label = QLabel("第一版仅支持 PDF。")
        self.book_pdf_only_label.setProperty("class", "hint")
        grid.addWidget(self.book_pdf_only_label, row, 0, 1, 2)

        row += 1
        self.book_scanned_pdf_check = QCheckBox("支持扫描版 PDF（通过图片识别/OCR 管线处理）")
        grid.addWidget(self.book_scanned_pdf_check, row, 0, 1, 2)

        row += 1
        grid.addWidget(QLabel("全书总览位置:"), row, 0)
        self.book_overview_position_combo = QComboBox()
        self.book_overview_position_combo.addItem("放在章节最后", "after_chapters")
        self.book_overview_position_combo.addItem("放在章节最前", "before_chapters")
        grid.addWidget(self.book_overview_position_combo, row, 1)

        row += 1
        self.book_citation_check = QCheckBox("回答默认带章节/页码引用")
        grid.addWidget(self.book_citation_check, row, 0, 1, 2)

        layout.addWidget(g)

        eg = QGroupBox("索引模型")
        egl = QGridLayout(eg)
        egl.setSpacing(6)
        egl.setColumnStretch(1, 1)

        egl.addWidget(QLabel("Embedding 模型:"), 0, 0)
        self.embedding_model_combo = QComboBox()
        self.embedding_model_combo.setEditable(True)
        egl.addWidget(self.embedding_model_combo, 0, 1)

        hint = QLabel("用于 RAG 检索。建议使用本地 Ollama embedding 模型，避免大型书籍索引反复消耗云端 token。")
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        egl.addWidget(hint, 1, 0, 1, 2)

        layout.addWidget(eg)
        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    # ════════════════════════════════════════════
    # Tab 2: 图片识别
    # ════════════════════════════════════════════

    def _build_vision_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._vision_tab_layout = QVBoxLayout(content)
        self._vision_tab_layout.setSpacing(8)
        self._vision_tab_layout.setContentsMargins(0, 0, 4, 0)

        # 卡片容器 — 在 _rebuild_vision_cards 中动态填充
        self._vision_cards_container = QWidget()
        self._vision_cards_container.setStyleSheet("background: transparent;")
        self._vision_cards_layout = QVBoxLayout(self._vision_cards_container)
        self._vision_cards_layout.setSpacing(8)
        self._vision_cards_layout.setContentsMargins(0, 0, 0, 0)
        self._vision_tab_layout.addWidget(self._vision_cards_container)

        # 新增按钮
        btn_add = QPushButton("+ 新增视觉模型")
        btn_add.clicked.connect(self._add_vision_card)
        self._vision_tab_layout.addWidget(btn_add)

        # 并发设置
        conc_row = QHBoxLayout()
        conc_row.addWidget(QLabel("并发数:"))
        self.vision_concurrent_spin = QSpinBox()
        self.vision_concurrent_spin.setRange(1, 16)
        self.vision_concurrent_spin.setToolTip("云端 API 可设 4-8，本地 Ollama 建议 1-2")
        conc_row.addWidget(self.vision_concurrent_spin)
        conc_hint = QLabel("云端可 4-8，本地建议 1-2")
        conc_hint.setProperty("class", "hint")
        conc_row.addWidget(conc_hint)
        conc_row.addStretch()
        self._vision_tab_layout.addLayout(conc_row)

        # Prompt
        pg = QGroupBox("图片分析 Prompt")
        pl = QVBoxLayout(pg)
        pl.setSpacing(4)

        pl.addWidget(QLabel("文字提取 Prompt:"))
        self.vision_ocr_edit = QTextEdit()
        self.vision_ocr_edit.setMaximumHeight(60)
        pl.addWidget(self.vision_ocr_edit)

        pl.addWidget(QLabel("图表描述 Prompt:"))
        self.vision_diagram_edit = QTextEdit()
        self.vision_diagram_edit.setMaximumHeight(60)
        pl.addWidget(self.vision_diagram_edit)

        pl.addWidget(QLabel("标题概括 Prompt:"))
        self.vision_title_edit = QTextEdit()
        self.vision_title_edit.setMaximumHeight(40)
        pl.addWidget(self.vision_title_edit)

        pl.addWidget(QLabel("单次调用 Prompt (strategy=single 时使用):"))
        self.vision_single_edit = QTextEdit()
        self.vision_single_edit.setMaximumHeight(60)
        pl.addWidget(self.vision_single_edit)

        self._vision_tab_layout.addWidget(pg)
        self._vision_tab_layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _build_vision_card(self, data: dict) -> QGroupBox:
        """构建单个视觉模型卡片"""
        card = QGroupBox()
        card.setStyleSheet("QGroupBox { margin-top: 10px; }")
        layout = QGridLayout(card)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 14, 12, 8)
        layout.setColumnStretch(1, 1)

        row = 0
        # 标题行: 名称 + 类型 + 删除按钮
        name_edit = QLineEdit(data.get("name", ""))
        name_edit.setPlaceholderText("模型名称，如: minicpm-v 本地")
        layout.addWidget(QLabel("名称:"), row, 0)

        type_combo = QComboBox()
        type_combo.addItems(["ollama", "cloud"])
        type_combo.setCurrentText(data.get("type", "ollama"))
        layout.addWidget(type_combo, row, 2)

        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(56)
        btn_del.setProperty("class", "secondary")
        layout.addWidget(btn_del, row, 3)

        row += 1
        layout.addWidget(name_edit, row, 0, 1, 4)

        row += 1
        model_edit = QLineEdit(data.get("model", ""))
        model_edit.setPlaceholderText("模型名，如: minicpm-v:8b / glm-4v-plus")
        layout.addWidget(QLabel("模型:"), row, 0)
        layout.addWidget(model_edit, row, 1, 1, 3)

        row += 1
        url_edit = QLineEdit(data.get("url", ""))
        url_edit.setPlaceholderText("Ollama 留空则使用通用设置的地址")
        layout.addWidget(QLabel("URL:"), row, 0)
        layout.addWidget(url_edit, row, 1, 1, 3)

        row += 1
        key_edit = QLineEdit(data.get("api_key", ""))
        key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_edit.setPlaceholderText("云端模型需要，Ollama 留空")
        layout.addWidget(QLabel("Key:"), row, 0)
        layout.addWidget(key_edit, row, 1, 1, 3)

        row += 1
        strategy_combo = QComboBox()
        strategy_combo.addItems(["triple", "single"])
        strategy_combo.setCurrentText(data.get("prompt_strategy", "triple"))
        layout.addWidget(QLabel("策略:"), row, 0)
        layout.addWidget(strategy_combo, row, 1, 1, 2)
        strategy_hint = QLabel("triple=3次调用(本地小模型) single=1次调用(高级模型)")
        strategy_hint.setProperty("class", "hint")
        layout.addWidget(strategy_hint, row + 1, 1, 1, 3)

        # 存储控件引用到 data 字典
        data["_widgets"] = {
            "name": name_edit,
            "type": type_combo,
            "model": model_edit,
            "url": url_edit,
            "key": key_edit,
            "strategy": strategy_combo,
            "card": card,
        }

        btn_del.clicked.connect(lambda checked, d=data: self._del_vision_card(d))

        # 强制 combo 使用 Qt 弹窗
        type_combo.setView(QListView())
        strategy_combo.setView(QListView())

        return card

    def _rebuild_vision_cards(self):
        """清空并重建所有视觉模型卡片"""
        # 先从旧 widgets 收集数据
        # (如果是首次 _load，_vision_data 里还没有 _widgets)
        # 清空容器
        while self._vision_cards_layout.count():
            item = self._vision_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        for data in self._vision_data:
            card = self._build_vision_card(data)
            self._vision_cards_layout.addWidget(card)

    def _collect_vision_data(self):
        """从卡片控件收集数据回 _vision_data"""
        for data in self._vision_data:
            w = data.get("_widgets")
            if w:
                data["name"] = w["name"].text()
                data["type"] = w["type"].currentText()
                data["model"] = w["model"].text()
                data["url"] = w["url"].text()
                data["api_key"] = w["key"].text()
                data["prompt_strategy"] = w["strategy"].currentText()

    def _add_vision_card(self):
        self._collect_vision_data()
        new_data = {"name": "", "type": "ollama", "model": "", "url": "", "api_key": ""}
        self._vision_data.append(new_data)
        card = self._build_vision_card(new_data)
        self._vision_cards_layout.addWidget(card)

    def _del_vision_card(self, data: dict):
        self._collect_vision_data()
        if data in self._vision_data:
            self._vision_data.remove(data)
        self._rebuild_vision_cards()

    # ════════════════════════════════════════════
    # Tab 3: 语音识别
    # ════════════════════════════════════════════

    def _build_asr_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 4, 0)

        # ASR 模式切换
        mode_g = QGroupBox("识别模式")
        mode_l = QGridLayout(mode_g)
        mode_l.setSpacing(6)

        mode_l.addWidget(QLabel("模式:"), 0, 0)
        self.asr_type_combo = QComboBox()
        self.asr_type_combo.addItems(["本地 faster-whisper", "云端 API"])
        self.asr_type_combo.currentIndexChanged.connect(self._on_asr_type_changed)
        mode_l.addWidget(self.asr_type_combo, 0, 1)

        mode_l.addWidget(QLabel("本地模型:"), 1, 0)
        self.whisper_combo = QComboBox()
        self.whisper_combo.addItems(WHISPER_MODELS)
        mode_l.addWidget(self.whisper_combo, 1, 1)

        mode_l.addWidget(QLabel("批处理大小:"), 2, 0)
        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 64)
        self.batch_size_spin.setValue(16)
        self.batch_size_spin.setToolTip("batch_size 越大并行度越高，显存占用越大\nRTX 3090 推荐 16-32")
        mode_l.addWidget(self.batch_size_spin, 2, 1)

        layout.addWidget(mode_g)

        # 云端 ASR 配置 (卡片式)
        self.asr_cloud_group = QGroupBox("云端 ASR 配置")
        self._asr_cloud_container = QWidget()
        self._asr_cloud_container.setStyleSheet("background: transparent;")
        self._asr_cloud_cards_layout = QVBoxLayout(self._asr_cloud_container)
        self._asr_cloud_cards_layout.setSpacing(8)
        self._asr_cloud_cards_layout.setContentsMargins(0, 0, 0, 0)
        acl = QVBoxLayout(self.asr_cloud_group)
        acl.setSpacing(6)
        acl.addWidget(self._asr_cloud_container)
        btn_add_asr = QPushButton("+ 新增云端 ASR")
        btn_add_asr.clicked.connect(self._add_asr_cloud_card)
        acl.addWidget(btn_add_asr)
        layout.addWidget(self.asr_cloud_group)

        # 语言设置
        lg = QGroupBox("语言")
        lgl = QGridLayout(lg)
        lgl.setSpacing(6)
        lgl.addWidget(QLabel("Whisper 语言:"), 0, 0)
        self.whisper_lang_edit = QLineEdit()
        self.whisper_lang_edit.setPlaceholderText("留空=自动检测, en, zh, ja ...")
        lgl.addWidget(self.whisper_lang_edit, 0, 1)
        layout.addWidget(lg)

        # 术语词表 (卡片式)
        self._vocab_cards_container = QWidget()
        self._vocab_cards_container.setStyleSheet("background: transparent;")
        self._vocab_cards_layout = QVBoxLayout(self._vocab_cards_container)
        self._vocab_cards_layout.setSpacing(8)
        self._vocab_cards_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._vocab_cards_container)

        btn_add_vocab = QPushButton("+ 新增术语词表")
        btn_add_vocab.clicked.connect(self._add_vocab_card)
        layout.addWidget(btn_add_vocab)

        scroll.setWidget(content)
        return scroll

    def _on_asr_type_changed(self, index):
        is_local = index == 0
        self.whisper_combo.setVisible(is_local)
        mode_layout = self.asr_type_combo.parent().layout()
        for i in range(mode_layout.count()):
            item = mode_layout.itemAt(i)
            if item and item.widget():
                row, _, _, _ = mode_layout.getItemPosition(i)
                if row == 1:
                    item.widget().setVisible(is_local)
        self.asr_cloud_group.setVisible(not is_local)

    # ─── 云端 ASR 卡片管理 ───

    def _build_asr_cloud_card(self, data: dict) -> QGroupBox:
        card = QGroupBox()
        card.setStyleSheet("QGroupBox { margin-top: 10px; }")
        layout = QGridLayout(card)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 14, 12, 8)
        layout.setColumnStretch(1, 1)

        row = 0
        name_edit = QLineEdit(data.get("name", ""))
        name_edit.setPlaceholderText("配置名称，如: Groq / OpenAI")
        layout.addWidget(QLabel("名称:"), row, 0)
        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(56)
        btn_del.setProperty("class", "secondary")
        layout.addWidget(btn_del, row, 2)

        row += 1
        layout.addWidget(name_edit, row, 0, 1, 3)

        row += 1
        url_edit = QLineEdit(data.get("base_url", ""))
        url_edit.setPlaceholderText("https://api.groq.com/openai/v1")
        layout.addWidget(QLabel("URL:"), row, 0)
        layout.addWidget(url_edit, row, 1, 1, 2)

        row += 1
        key_edit = QLineEdit(data.get("api_key", ""))
        key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_edit.setPlaceholderText("sk-...")
        layout.addWidget(QLabel("Key:"), row, 0)
        layout.addWidget(key_edit, row, 1, 1, 2)

        row += 1
        model_combo = QComboBox()
        model_combo.setEditable(True)
        model_combo.addItems(ASR_CLOUD_MODELS)
        model_combo.setCurrentText(data.get("model", "whisper-large-v3"))
        model_combo.setView(QListView())
        layout.addWidget(QLabel("模型:"), row, 0)
        layout.addWidget(model_combo, row, 1, 1, 2)

        row += 1
        api_type_combo = QComboBox()
        api_type_combo.addItems(["whisper", "multimodal", "dashscope"])
        api_type_combo.setCurrentText(data.get("api_type", "whisper"))
        api_type_combo.setView(QListView())
        layout.addWidget(QLabel("接口:"), row, 0)
        layout.addWidget(api_type_combo, row, 1, 1, 2)
        api_type_hint = QLabel("whisper=/audio/transcriptions  multimodal=/chat/completions  dashscope=百炼SDK")
        api_type_hint.setProperty("class", "hint")
        layout.addWidget(api_type_hint, row + 1, 1, 1, 2)

        data["_widgets"] = {
            "name": name_edit, "url": url_edit,
            "key": key_edit, "model": model_combo,
            "api_type": api_type_combo, "card": card,
        }
        btn_del.clicked.connect(lambda checked, d=data: self._del_asr_cloud_card(d))
        return card

    def _rebuild_asr_cloud_cards(self):
        while self._asr_cloud_cards_layout.count():
            item = self._asr_cloud_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for data in self._asr_cloud_data:
            card = self._build_asr_cloud_card(data)
            self._asr_cloud_cards_layout.addWidget(card)

    def _collect_asr_cloud_data(self):
        for data in self._asr_cloud_data:
            w = data.get("_widgets")
            if w:
                data["name"] = w["name"].text()
                data["base_url"] = w["url"].text()
                data["api_key"] = w["key"].text()
                data["model"] = w["model"].currentText()
                data["api_type"] = w["api_type"].currentText()

    def _add_asr_cloud_card(self):
        self._collect_asr_cloud_data()
        new_data = {"name": "", "base_url": "", "api_key": "", "model": "whisper-large-v3", "api_type": "whisper"}
        self._asr_cloud_data.append(new_data)
        card = self._build_asr_cloud_card(new_data)
        self._asr_cloud_cards_layout.addWidget(card)

    def _del_asr_cloud_card(self, data: dict):
        self._collect_asr_cloud_data()
        if data in self._asr_cloud_data:
            self._asr_cloud_data.remove(data)
        self._rebuild_asr_cloud_cards()

    # ════════════════════════════════════════════
    # Tab 4: AI 聚合
    # ════════════════════════════════════════════

    def _build_aggregation_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 4, 0)

        # Provider 卡片容器
        self._prov_container = QWidget()
        self._prov_container.setStyleSheet("background: transparent;")
        self._prov_cards_layout = QVBoxLayout(self._prov_container)
        self._prov_cards_layout.setSpacing(8)
        self._prov_cards_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._prov_container)

        btn_add = QPushButton("+ 新增 Provider")
        btn_add.clicked.connect(self._add_provider_card)
        layout.addWidget(btn_add)

        # 蒸馏 Prompt
        dpg = QGroupBox("蒸馏 Prompt (最终 AI 聚合时使用)")
        dpl = QVBoxLayout(dpg)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMinimumHeight(80)
        dpl.addWidget(self.prompt_edit)
        layout.addWidget(dpg, stretch=1)

        scroll.setWidget(content)
        return scroll

    # ─── 快捷提问 Tab ───

    def _build_quick_questions_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 4, 0)

        hint = QLabel("添加常用的学习提问模板，对话界面可一键选用")
        hint.setProperty("class", "hint")
        layout.addWidget(hint)

        # 卡片容器
        self._qq_container = QWidget()
        self._qq_container.setStyleSheet("background: transparent;")
        self._qq_cards_layout = QVBoxLayout(self._qq_container)
        self._qq_cards_layout.setSpacing(8)
        self._qq_cards_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._qq_container)

        btn_add = QPushButton("+ 新增提问")
        btn_add.clicked.connect(self._add_qq_card)
        layout.addWidget(btn_add)

        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _build_qq_card(self, data: dict) -> QGroupBox:
        card = QGroupBox()
        card.setStyleSheet("QGroupBox { margin-top: 10px; }")
        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 14, 12, 8)

        # 标题行: 名称 + 删除
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("名称:"))
        name_edit = QLineEdit(data.get("name", ""))
        name_edit.setPlaceholderText("提问名称，如: 总结要点")
        top.addWidget(name_edit, stretch=1)
        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(56)
        btn_del.setProperty("class", "secondary")
        top.addWidget(btn_del)
        layout.addLayout(top)

        text_edit = QTextEdit()
        text_edit.setPlainText(data.get("text", ""))
        text_edit.setPlaceholderText("输入完整问句，如: 请总结当前章节的核心要点")
        text_edit.setMaximumHeight(70)
        layout.addWidget(text_edit)

        data["_widgets"] = {
            "name": name_edit,
            "text": text_edit,
            "card": card,
        }
        btn_del.clicked.connect(lambda checked, d=data: self._del_qq_card(d))
        return card

    def _rebuild_qq_cards(self):
        while self._qq_cards_layout.count():
            item = self._qq_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for data in self._qq_data:
            card = self._build_qq_card(data)
            self._qq_cards_layout.addWidget(card)

    def _collect_qq_data(self):
        for data in self._qq_data:
            w = data.get("_widgets")
            if w:
                data["name"] = w["name"].text()
                data["text"] = w["text"].toPlainText()

    def _add_qq_card(self):
        self._collect_qq_data()
        new_data = {"name": "", "text": ""}
        self._qq_data.append(new_data)
        card = self._build_qq_card(new_data)
        self._qq_cards_layout.addWidget(card)

    def _del_qq_card(self, data: dict):
        self._collect_qq_data()
        if data in self._qq_data:
            self._qq_data.remove(data)
        self._rebuild_qq_cards()

    # ─── Provider 卡片管理 ───

    def _build_provider_card(self, data: dict) -> QGroupBox:
        card = QGroupBox()
        card.setStyleSheet("QGroupBox { margin-top: 10px; }")
        lo = QGridLayout(card)
        lo.setSpacing(6)
        lo.setContentsMargins(12, 14, 12, 8)
        lo.setColumnStretch(1, 1)

        row = 0
        name_edit = QLineEdit(data.get("name", ""))
        name_edit.setPlaceholderText("Provider 名称，如: Gemini")
        lo.addWidget(QLabel("名称:"), row, 0)
        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(56)
        btn_del.setProperty("class", "secondary")
        lo.addWidget(btn_del, row, 2)

        row += 1
        lo.addWidget(name_edit, row, 0, 1, 3)

        row += 1
        url_edit = QLineEdit(data.get("base_url", ""))
        url_edit.setPlaceholderText("https://api.example.com/v1")
        lo.addWidget(QLabel("URL:"), row, 0)
        lo.addWidget(url_edit, row, 1, 1, 2)

        row += 1
        key_edit = QLineEdit(data.get("api_key", ""))
        key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_edit.setPlaceholderText("sk-...")
        lo.addWidget(QLabel("Key:"), row, 0)
        lo.addWidget(key_edit, row, 1, 1, 2)

        row += 1
        model_edit = QLineEdit(data.get("model", ""))
        model_edit.setPlaceholderText("gpt-4o / gemini-1.5-pro / ...")
        lo.addWidget(QLabel("模型:"), row, 0)
        lo.addWidget(model_edit, row, 1, 1, 2)

        data["_widgets"] = {
            "name": name_edit, "url": url_edit,
            "key": key_edit, "model": model_edit, "card": card,
        }
        btn_del.clicked.connect(lambda checked, d=data: self._del_provider_card(d))
        return card

    def _rebuild_provider_cards(self):
        while self._prov_cards_layout.count():
            item = self._prov_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for data in self._providers_data:
            card = self._build_provider_card(data)
            self._prov_cards_layout.addWidget(card)

    def _collect_provider_data(self):
        for data in self._providers_data:
            w = data.get("_widgets")
            if w:
                data["name"] = w["name"].text()
                data["base_url"] = w["url"].text()
                data["api_key"] = w["key"].text()
                data["model"] = w["model"].text()

    def _add_provider_card(self):
        self._collect_provider_data()
        new_data = {"name": "", "base_url": "", "api_key": "", "model": ""}
        self._providers_data.append(new_data)
        card = self._build_provider_card(new_data)
        self._prov_cards_layout.addWidget(card)

    def _del_provider_card(self, data: dict):
        self._collect_provider_data()
        if data in self._providers_data:
            self._providers_data.remove(data)
        self._rebuild_provider_cards()

    # ════════════════════════════════════════════
    # 词表卡片管理
    # ════════════════════════════════════════════

    def _build_vocab_card(self, data: dict) -> QGroupBox:
        card = QGroupBox()
        card.setStyleSheet("QGroupBox { margin-top: 10px; }")
        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 14, 12, 8)

        # 标题行: 名称 + 删除
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("名称:"))
        name_edit = QLineEdit(data.get("name", ""))
        name_edit.setPlaceholderText("词表名称，如: GDC 通用")
        top.addWidget(name_edit, stretch=1)
        btn_del = QPushButton("删除")
        btn_del.setFixedWidth(56)
        btn_del.setProperty("class", "secondary")
        top.addWidget(btn_del)
        layout.addLayout(top)

        terms_edit = QTextEdit()
        terms_edit.setPlainText(data.get("terms", ""))
        terms_edit.setPlaceholderText("Nanite, Lumen, PBR, ECS, ... (逗号或换行分隔)")
        terms_edit.setMaximumHeight(70)
        layout.addWidget(terms_edit)

        data["_widgets"] = {
            "name": name_edit,
            "terms": terms_edit,
            "card": card,
        }
        btn_del.clicked.connect(lambda checked, d=data: self._del_vocab_card(d))
        return card

    def _rebuild_vocab_cards(self):
        while self._vocab_cards_layout.count():
            item = self._vocab_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for data in self._vocabs_data:
            card = self._build_vocab_card(data)
            self._vocab_cards_layout.addWidget(card)

    def _collect_vocab_data(self):
        for data in self._vocabs_data:
            w = data.get("_widgets")
            if w:
                data["name"] = w["name"].text()
                data["terms"] = w["terms"].toPlainText()

    def _add_vocab_card(self):
        self._collect_vocab_data()
        new_data = {"name": "", "terms": ""}
        self._vocabs_data.append(new_data)
        card = self._build_vocab_card(new_data)
        self._vocab_cards_layout.addWidget(card)

    def _del_vocab_card(self, data: dict):
        self._collect_vocab_data()
        if data in self._vocabs_data:
            self._vocabs_data.remove(data)
        self._rebuild_vocab_cards()

    # ════════════════════════════════════════════
    # 修复 ComboBox 下拉弹窗背景 (Windows QSS 不足)
    # ════════════════════════════════════════════

    def _force_qt_combobox(self):
        """强制 QComboBox 使用 Qt 内置弹窗渲染，使 QSS 完全生效"""
        for combo in self.findChildren(QComboBox):
            combo.setView(QListView())

    # ════════════════════════════════════════════
    # 加载 / 保存
    # ════════════════════════════════════════════

    def _load(self):
        s = self.settings

        # 通用
        self.theme_combo.setCurrentText(s.theme)
        self.resolution_combo.setCurrentText(s.resolution_scale)
        self.sample_rate_spin.setValue(s.sample_rate)
        self.frame_interval_spin.setValue(s.frame_interval)
        self.ssim_spin.setValue(s.ssim_threshold)
        self.segment_spin.setValue(s.segment_length)
        self.ollama_url_edit.setText(s.ollama_url)

        # 对话字体
        if s.chat_font_family:
            idx = self.font_family_combo.findText(s.chat_font_family)
            if idx >= 0:
                self.font_family_combo.setCurrentIndex(idx)
            else:
                self.font_family_combo.setCurrentText(s.chat_font_family)
        self.font_scale_spin.setValue(s.chat_font_scale)

        # 图片识别
        self._vision_data = [dict(v) for v in s.vision_models]
        self._rebuild_vision_cards()
        self.vision_concurrent_spin.setValue(s.vision_concurrent)
        self.vision_ocr_edit.setPlainText(s.vision_prompt_ocr)
        self.vision_diagram_edit.setPlainText(s.vision_prompt_diagram)
        self.vision_title_edit.setPlainText(s.vision_prompt_title)
        self.vision_single_edit.setPlainText(s.vision_prompt_single)

        # 书籍蒸馏
        self.book_scanned_pdf_check.setChecked(s.book_support_scanned_pdf)
        idx = self.book_overview_position_combo.findData(s.book_overview_position)
        self.book_overview_position_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.book_citation_check.setChecked(s.cite_sources_by_default)
        self.embedding_model_combo.clear()
        for e in s.embedding_models:
            name = e.get("name") or e.get("model", "")
            model = e.get("model", "")
            self.embedding_model_combo.addItem(f"{name} ({model})" if model and model not in name else name, e)
        if s.embedding_active:
            found = False
            for i in range(self.embedding_model_combo.count()):
                data = self.embedding_model_combo.itemData(i) or {}
                if data.get("model") == s.embedding_active or self.embedding_model_combo.itemText(i) == s.embedding_active:
                    self.embedding_model_combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                self.embedding_model_combo.setCurrentText(s.embedding_active)

        # 语音识别
        self.asr_type_combo.setCurrentIndex(0 if s.asr_type == "local" else 1)
        self.whisper_combo.setCurrentText(s.whisper_model)
        self.batch_size_spin.setValue(s.whisper_batch_size)
        self.whisper_lang_edit.setText(s.whisper_language)

        self._asr_cloud_data = [dict(c) for c in s.asr_cloud_configs]
        self._rebuild_asr_cloud_cards()
        self._on_asr_type_changed(self.asr_type_combo.currentIndex())

        self._vocabs_data = [dict(v) for v in s.vocabularies]
        self._rebuild_vocab_cards()

        # AI 聚合
        self._providers_data = [dict(p) for p in s.providers]
        self._rebuild_provider_cards()

        # 快捷提问
        self._qq_data = [dict(q) for q in s.quick_questions]
        self._rebuild_qq_cards()
        self.prompt_edit.setPlainText(s.default_distill_prompt)

    def _save(self):
        # 从卡片收集数据
        self._collect_vision_data()
        self._collect_asr_cloud_data()
        self._collect_provider_data()
        self._collect_vocab_data()
        self._collect_qq_data()

        s = self.settings

        # 通用
        s.theme = self.theme_combo.currentText()
        s.resolution_scale = self.resolution_combo.currentText()
        s.sample_rate = self.sample_rate_spin.value()
        s.frame_interval = self.frame_interval_spin.value()
        s.ssim_threshold = self.ssim_spin.value()
        s.segment_length = self.segment_spin.value()
        s.ollama_url = self.ollama_url_edit.text()

        # 对话字体
        font_text = self.font_family_combo.currentText()
        s.chat_font_family = "" if font_text == "默认" else font_text
        s.chat_font_scale = self.font_scale_spin.value()

        # 图片识别
        s.vision_models = [{k: v for k, v in d.items() if k != "_widgets"} for d in self._vision_data]
        if s.vision_models:
            s.vision_active = s.vision_models[0].get("name", "")
        s.vision_concurrent = self.vision_concurrent_spin.value()
        s.vision_prompt_ocr = self.vision_ocr_edit.toPlainText()
        s.vision_prompt_diagram = self.vision_diagram_edit.toPlainText()
        s.vision_prompt_title = self.vision_title_edit.toPlainText()
        s.vision_prompt_single = self.vision_single_edit.toPlainText()

        # 书籍蒸馏
        s.book_support_scanned_pdf = self.book_scanned_pdf_check.isChecked()
        s.book_overview_position = self.book_overview_position_combo.currentData() or "after_chapters"
        s.cite_sources_by_default = self.book_citation_check.isChecked()
        emb_text = self.embedding_model_combo.currentText().strip()
        emb_data = self.embedding_model_combo.currentData()
        if isinstance(emb_data, dict) and emb_data.get("model"):
            s.embedding_active = emb_data["model"]
        elif emb_text:
            s.embedding_active = emb_text
            if not any(e.get("model") == emb_text or e.get("name") == emb_text for e in s.embedding_models):
                s.embedding_models.append({
                    "name": emb_text,
                    "type": "ollama",
                    "model": emb_text,
                    "url": s.ollama_url,
                    "api_key": "",
                })

        # 语音识别
        s.asr_type = "local" if self.asr_type_combo.currentIndex() == 0 else "cloud"
        s.whisper_model = self.whisper_combo.currentText()
        s.whisper_batch_size = self.batch_size_spin.value()
        s.asr_cloud_configs = [{k: v for k, v in d.items() if k != "_widgets"} for d in self._asr_cloud_data]
        if s.asr_cloud_configs:
            s.asr_cloud_active = s.asr_cloud_configs[0].get("name", "")
        s.whisper_language = self.whisper_lang_edit.text()
        s.vocabularies = [{k: v for k, v in d.items() if k != "_widgets"} for d in self._vocabs_data]

        # AI 聚合
        s.providers = [{k: v for k, v in d.items() if k != "_widgets"} for d in self._providers_data]
        s.default_distill_prompt = self.prompt_edit.toPlainText()

        # 快捷提问
        s.quick_questions = [{k: v for k, v in d.items() if k != "_widgets"} for d in self._qq_data]

        save_settings(s)
        self.accept()
