"""
Book-Distiller Settings 对话框
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QComboBox, QTextEdit,
    QGroupBox, QGridLayout, QListView, QCheckBox, QRadioButton,
    QDialogButtonBox, QSpinBox, QDoubleSpinBox, QScrollArea,
)
from PySide6.QtCore import Qt
from src.config import (
    BOOK_OUTPUT_LANGUAGES, BOOK_SESSION_GRANULARITIES, Settings, save_settings,
)


def _is_local_provider(provider: dict) -> bool:
    base_url = provider.get("base_url", "").lower()
    return "localhost" in base_url or "127.0.0.1" in base_url


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._current_vision_active = ""
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
        tabs.addTab(self._build_aggregation_tab(), "书籍整合")
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

        # 基础参数
        g = QGroupBox("基础")
        grid = QGridLayout(g)
        grid.setSpacing(6)

        row = 0
        grid.addWidget(QLabel("主题:"), row, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["light", "dark"])
        grid.addWidget(self.theme_combo, row, 1)

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
        grid.addWidget(QLabel("对话细分:"), row, 0)
        self.book_session_granularity_combo = QComboBox()
        for label, value in BOOK_SESSION_GRANULARITIES:
            self.book_session_granularity_combo.addItem(label, value)
        grid.addWidget(self.book_session_granularity_combo, row, 1)

        row += 1
        grid.addWidget(QLabel("重构输出语言:"), row, 0)
        self.book_output_language_combo = QComboBox()
        self.book_output_language_combo.setEditable(True)
        self.book_output_language_combo.addItems(BOOK_OUTPUT_LANGUAGES)
        grid.addWidget(self.book_output_language_combo, row, 1)

        row += 1
        self.book_citation_check = QCheckBox("回答默认带章节/页码引用")
        grid.addWidget(self.book_citation_check, row, 0, 1, 2)

        layout.addWidget(g)

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
        self.vision_concurrent_spin.setToolTip("默认 1（串行），显存充裕可适当增加")
        conc_row.addWidget(self.vision_concurrent_spin)
        conc_hint = QLabel("默认串行，避免显存溢出")
        conc_hint.setProperty("class", "hint")
        conc_row.addWidget(conc_hint)
        conc_row.addStretch()
        self._vision_tab_layout.addLayout(conc_row)

        # 测试按钮
        self.btn_test_vision = QPushButton("测试图片理解")
        self.btn_test_vision.setProperty("class", "secondary")
        self.btn_test_vision.clicked.connect(self._test_vision_model)
        self._vision_tab_layout.addWidget(self.btn_test_vision)

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
        # 标题行: 激活 radio + 名称 + 类型 + 删除按钮
        active_radio = QRadioButton("激活")
        active_radio.setToolTip("设为当前使用的图片识别模型")
        # name 匹配 vision_active 则选中
        active_radio.setChecked(data.get("name", "") == self._current_vision_active)
        layout.addWidget(active_radio, row, 0)

        name_edit = QLineEdit(data.get("name", ""))
        name_edit.setPlaceholderText("模型名称，如: minicpm-v 本地")

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
            "active_radio": active_radio,
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

    def _test_vision_model(self):
        """测试当前第一个配置的视觉模型是否能理解图片。"""
        from PySide6.QtWidgets import QMessageBox
        self._collect_vision_data()
        if not self._vision_data:
            QMessageBox.warning(self, "测试失败", "请先配置至少一个视觉模型")
            return

        # Pick the first model with a name
        config = None
        for d in self._vision_data:
            if d.get("model"):
                config = d
                break
        if not config:
            QMessageBox.warning(self, "测试失败", "请先填写模型名称")
            return

        vision_type = config.get("type", "ollama")
        model = config.get("model", "")
        base_url = config.get("url", "") or self.settings.ollama_url
        api_key = config.get("api_key", "")

        # Create a small test image with Pillow
        import tempfile, os
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (400, 200), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 390, 190], outline="blue", width=2)
        draw.text((30, 60), "Book-Distiller Vision Test", fill="black")
        draw.text((30, 100), "这是一页测试图片", fill="gray")
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name)
        tmp.close()

        try:
            self.btn_test_vision.setEnabled(False)
            self.btn_test_vision.setText("测试中...")
            from src.image_analysis import _encode_image, _call_ollama, _call_cloud
            image_b64 = _encode_image(tmp.name)
            prompt = "请用一句话描述这张图片的内容。只输出描述，不要其他内容。"

            if vision_type == "ollama":
                text, tokens, _ = _call_ollama(model, prompt, image_b64, base_url)
            else:
                text, tokens = _call_cloud(model, prompt, image_b64, base_url, api_key)

            total_tokens = tokens.get("total_tokens", 0)
            QMessageBox.information(
                self, "测试成功",
                f"模型: {model}\n"
                f"类型: {vision_type}\n"
                f"Token: {total_tokens}\n\n"
                f"模型回复:\n{text}"
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "测试失败",
                f"模型: {model}\n"
                f"类型: {vision_type}\n\n"
                f"错误: {exc}\n\n"
                f"常见原因:\n"
                f"- Ollama 未启动或模型未拉取\n"
                f"- 云端 API Key 无效\n"
                f"- 模型不支持图片输入\n"
                f"- 网络连接失败"
            )
        finally:
            os.unlink(tmp.name)
            self.btn_test_vision.setEnabled(True)
            self.btn_test_vision.setText("测试图片理解")

    # ════════════════════════════════════════════
    # Tab: 书籍整合
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

        btn_add = QPushButton("+ 新增书籍整合模型")
        btn_add.clicked.connect(self._add_provider_card)
        layout.addWidget(btn_add)

        hint = QLabel("章节笔记提示词已移到批量蒸馏页的“提示词设置”，并按 tiny / medium / high / ultra 四档管理。")
        hint.setProperty("class", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
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
        self._current_vision_active = s.vision_active
        self._vision_data = [dict(v) for v in s.vision_models]
        # 如果没有任何 radio 被选中（旧数据没有 active），选中匹配名称的那个
        self._rebuild_vision_cards()
        has_checked = any(
            d.get("_widgets", {}).get("active_radio", None) and d["_widgets"]["active_radio"].isChecked()
            for d in self._vision_data
        )
        if not has_checked and self._vision_data:
            # 尝试匹配 vision_active
            for d in self._vision_data:
                w = d.get("_widgets")
                if w and w["name"].text().strip() == self._current_vision_active:
                    w["active_radio"].setChecked(True)
                    break
            else:
                self._vision_data[0]["_widgets"]["active_radio"].setChecked(True)
        self.vision_concurrent_spin.setValue(s.vision_concurrent)
        self.vision_ocr_edit.setPlainText(s.vision_prompt_ocr)
        self.vision_diagram_edit.setPlainText(s.vision_prompt_diagram)
        self.vision_title_edit.setPlainText(s.vision_prompt_title)
        self.vision_single_edit.setPlainText(s.vision_prompt_single)

        # 书籍蒸馏
        self.book_scanned_pdf_check.setChecked(s.book_support_scanned_pdf)
        idx = self.book_overview_position_combo.findData(s.book_overview_position)
        self.book_overview_position_combo.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self.book_session_granularity_combo.findData(getattr(s, "book_session_granularity", "level2"))
        self.book_session_granularity_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self.book_output_language_combo.setCurrentText(getattr(s, "book_output_language", "中文") or "中文")
        self.book_citation_check.setChecked(s.cite_sources_by_default)
        # 书籍整合
        self._providers_data = [dict(p) for p in s.providers if not _is_local_provider(p)]
        self._rebuild_provider_cards()

        # 快捷提问
        self._qq_data = [dict(q) for q in s.quick_questions]
        self._rebuild_qq_cards()

    def _save(self):
        # 从卡片收集数据
        self._collect_vision_data()
        self._collect_provider_data()
        self._collect_qq_data()

        s = self.settings

        # 通用
        s.theme = self.theme_combo.currentText()
        s.ollama_url = self.ollama_url_edit.text()

        # 对话字体
        font_text = self.font_family_combo.currentText()
        s.chat_font_family = "" if font_text == "默认" else font_text
        s.chat_font_scale = self.font_scale_spin.value()

        # 图片识别
        s.vision_models = [{k: v for k, v in d.items() if k != "_widgets"} for d in self._vision_data]
        # 从 radio 按钮读取激活模型
        active_name = ""
        for d in self._vision_data:
            w = d.get("_widgets")
            if w and w["active_radio"].isChecked():
                active_name = w["name"].text().strip()
                break
        if not active_name and s.vision_models:
            active_name = s.vision_models[0].get("name", "")
        s.vision_active = active_name
        s.vision_concurrent = self.vision_concurrent_spin.value()
        s.vision_prompt_ocr = self.vision_ocr_edit.toPlainText()
        s.vision_prompt_diagram = self.vision_diagram_edit.toPlainText()
        s.vision_prompt_title = self.vision_title_edit.toPlainText()
        s.vision_prompt_single = self.vision_single_edit.toPlainText()

        # 书籍蒸馏
        s.book_support_scanned_pdf = self.book_scanned_pdf_check.isChecked()
        s.book_overview_position = self.book_overview_position_combo.currentData() or "after_chapters"
        s.book_session_granularity = self.book_session_granularity_combo.currentData() or "level2"
        s.book_output_language = self.book_output_language_combo.currentText().strip() or "中文"
        s.cite_sources_by_default = self.book_citation_check.isChecked()
        # 书籍整合
        s.providers = [
            {k: v for k, v in d.items() if k != "_widgets"}
            for d in self._providers_data
            if not _is_local_provider(d)
        ]

        # 快捷提问
        s.quick_questions = [{k: v for k, v in d.items() if k != "_widgets"} for d in self._qq_data]

        save_settings(s)
        self.accept()
