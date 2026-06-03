"""
Book-Distiller 配置管理
- 用户配置持久化到 ~/.Book-Distiller/settings.json
- 支持书籍蒸馏、视觉模型、书籍整合模型和对话设置
"""

import json
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# 用户数据目录: C:\Users\{user}\.Book-Distiller\
USER_DATA_DIR = Path.home() / ".Book-Distiller"
USER_DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = USER_DATA_DIR / "settings.json"

BOOK_OUTPUT_LANGUAGES = ["中文", "English", "日本語", "한국어", "Français", "Deutsch", "Español"]
BOOK_DISTILL_LEVELS = ["tiny", "medium", "high", "ultra"]
BOOK_SESSION_GRANULARITIES = [
    ("1级章节", "level1"),
    ("2级章节", "level2"),
    ("全级细分", "all"),
]

RICH_TEXT_FORMATTING_PROMPT = """

## 富文本排版规范（硬编码）

你的返回会显示在支持 Markdown/部分 HTML 的 QTextBrowser 聊天气泡中。请在不牺牲内容准确性的前提下优化可读性：

1. 使用清晰的 Markdown 层级：`#` 只用于整篇标题，主体优先使用 `##` / `###`。章节之间用空行分隔；大章节之间可用 `---` 拉开距离。
2. 支持适量 emoji 作为视觉锚点，例如：📌 主旨、🧠 概念、🔗 关系、🧩 论证、⚠️ 易混点、✅ 掌握检查、❓ 可追问。不要每行都加 emoji。
3. 内容要"缩减但不变浅"：先给结论，再给必要解释；避免长段堆叠。每段尽量 2-4 行，列表每项尽量 1-3 句。
4. 必要时使用主题友好的轻量颜色区分，但不要大面积彩色背景。允许使用少量 inline HTML：
   - 重点概念：`<span style="color:#4F8EF7;font-weight:600">概念</span>`
   - 方法/步骤：`<span style="color:#3BA776;font-weight:600">方法</span>`
   - 注意/限制：`<span style="color:#D99A2B;font-weight:600">注意</span>`
   - 风险/误区：`<span style="color:#D65A5A;font-weight:600">误区</span>`
   这些颜色需要在 light/dark 主题下都保持克制、可读；不要使用纯黑、纯白或刺眼荧光色。
5. 引用原文出处时保持紧凑格式：`（第 3 章，p.42）` 或 `（chunk: ch01_p0042_002）`。
6. 不要输出完整 HTML 文档，不要使用 `<body>`、`<style>`、表格布局或复杂 CSS。只输出正文 Markdown，可混用少量 `<span>`。
"""

DEFAULT_BOOK_DISTILL_PROMPTS = {
    "tiny": (
        "你是一位专业教授型读书导师。请用简洁、清晰的方式重构当前章节，"
        "说明本章主题、关键概念、知识重点，以及和前后章节的关系。"
    ),
    "medium": (
        "你是一位专业教授、学习导师和知识博主。请把当前章节重构成适合学习者阅读的中文讲解。"
        "要求覆盖：本章要解决的问题、核心概念、模块结构、前后篇章关系、关键知识点、"
        "容易误解处、深入浅出的例子，以及 3-5 个可追问问题。"
    ),
    "high": (
        "你是一位兼具大学教授、课程设计师和知识博主能力的读书导师。"
        "请不要逐句翻译原文，而是先识别当前章节的论证目标、概念网络和知识模块，"
        "再用中文进行二次重构讲解。要求讲清楚：本章在全书中的位置、作者为什么安排这一章、"
        "核心概念如何相互连接、前后章节如何承接、哪些知识点最值得掌握、读者可能卡在哪里、"
        "如何用通俗但不浅薄的例子理解。输出要有层次、有标题、有学习路径。"
    ),
    "ultra": (
        "你是一位顶级教授、深度阅读导师、课程主理人和知识博主。"
        "请把当前章节重构成一份高质量中文学习讲义，不做机械翻译，不做简单摘要，"
        "而是帮助读者真正理解作者的论证、概念、方法和知识结构。"
        "必须覆盖：章节主旨、全书位置、前置知识、后续承接、概念地图、模块拆解、"
        "关键论证链、重要细节、隐含假设、常见误区、类比和例子、学习检查清单、"
        "适合继续追问的问题。风格要专业、透彻、亲切，像一位真正会教书的人。"
    ),
}

VISION_MODELS_OLLAMA = [
    "minicpm-v:8b", "llava:7b-v1.6", "llava-llama3:8b",
    "qwen2-vl:7b", "moondream:1.8b",
]
VISION_MODELS_CLOUD = [
    "glm-4v-plus", "glm-4v-flash",
    "qwen-vl-max", "qwen-vl-plus",
    "gpt-4o", "claude-sonnet-4-6",
]

CLOUD_API_PRESETS = {
    "glm-4v-plus": "https://open.bigmodel.cn/api/paas/v4",
    "glm-4v-flash": "https://open.bigmodel.cn/api/paas/v4",
    "qwen-vl-max": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "qwen-vl-plus": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "gpt-4o": "https://api.openai.com/v1",
    "claude-sonnet-4-6": "https://api.anthropic.com/v1",
}


@dataclass
class Settings:
    last_output_dir: str = ""
    last_batch_books: list = field(default_factory=list)
    last_batch_vision: str = ""    # 上次批量蒸馏选择的图片识别模型（combo text）
    last_batch_agg: str = ""       # 上次批量蒸馏选择的书籍整合模型（combo text）
    chat_font_family: str = ""    # 对话字体（空=默认）
    chat_font_scale: int = 100    # 字体缩放百分比
    book_support_scanned_pdf: bool = True
    book_overview_position: str = "after_chapters"
    book_output_language: str = "中文"
    book_distill_level: str = "high"
    book_session_granularity: str = "level2"
    book_distill_prompts: dict = field(default_factory=lambda: dict(DEFAULT_BOOK_DISTILL_PROMPTS))
    cite_sources_by_default: bool = True
    theme: str = "dark"
    ollama_url: str = "http://localhost:11434"
    vision_concurrent: int = 1  # 图片理解并发数（默认串行，避免显存爆炸）
    vision_max_dimension: int = 0  # 图片识别缩放上限（像素），0=不缩放
    vision_active: str = "minicpm-v 本地"  # 当前激活的视觉模型名称
    vision_models: list = field(default_factory=lambda: [
        {"name": "Gemma4:26b 本地", "type": "ollama", "model": "gemma4:26b", "url": "http://localhost:11434", "api_key": "", "prompt_strategy": "single"},
        {"name": "minicpm-v 本地", "type": "ollama", "model": "minicpm-v:8b", "url": "http://localhost:11434", "api_key": "", "prompt_strategy": "triple"},
        {"name": "GLM-4V 云端", "type": "cloud", "model": "glm-4v-plus", "url": "https://open.bigmodel.cn/api/paas/v4", "api_key": "", "prompt_strategy": "single"},
        {"name": "Qwen-VL 云端", "type": "cloud", "model": "qwen-vl-max", "url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "", "prompt_strategy": "single"},
    ])
    providers: list = field(default_factory=lambda: [
        {"name": "Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key": "", "model": "gemini-1.5-pro"},
        {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "api_key": "", "model": "gpt-4o"},
        {"name": "DashScope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "", "model": "qwen-plus"},
    ])
    quick_questions: list = field(default_factory=lambda: [
        {"name": "总结要点", "text": "请总结当前章节的核心要点"},
        {"name": "术语解释", "text": "请列出这段内容中出现的所有专业术语，并逐一解释"},
        {"name": "举例说明", "text": "请用通俗易懂的例子来说明这个概念"},
        {"name": "对比分析", "text": "请对比分析这里提到的几个概念/方案的异同"},
    ])
    default_distill_prompt: str = (
        "你是一位深刻、耐心、善于讲清复杂概念的读书导师。\n"
        "我会给你一本书的全书目录、当前章节原文、必要的图表说明，以及全书层面的摘要/术语表。\n\n"
        "请为当前章节生成一份面向学习者的二次重写笔记。\n"
        "要求：专业、深刻、通俗；不是摘抄，不是简单摘要，而是帮助读者真正理解作者的论证、概念和方法。\n\n"
        "请严格按以下结构输出：\n\n"
        "## 本章主旨\n"
        "用一段话说明本章到底在解决什么问题，以及它在全书中的位置。\n\n"
        "## 核心概念\n"
        "列出本章关键概念。每个概念包含：通俗解释、作者为何需要这个概念、它和前后章节的关系。\n\n"
        "## 论证脉络\n"
        "按作者的推进顺序重建本章逻辑，必要时用 问题->观点->证据->结论 表达。\n\n"
        "## 关键细节\n"
        "保留容易被忽略但影响理解的重要细节、限定条件、例子、数据、图表信息。\n\n"
        "## 学习者应掌握什么\n"
        "列出 3-5 个最重要的学习要点。每个要点说明：是什么、为什么重要、如何判断自己掌握了。\n\n"
        "## 可能的困惑\n"
        "预测读者可能卡住的地方，并用通俗语言解释。\n\n"
        "## 可追问问题\n"
        "给出 5-8 个高质量追问，帮助读者继续和 AI 导师对话。"
    )
    vision_prompt_ocr: str = (
        "请提取这页书籍/PDF 图像中所有有意义的文字内容。\n"
        "- 如果是扫描页：尽量逐字 OCR，保持段落顺序\n"
        "- 如果包含表格、公式、代码：用 Markdown 尽量还原结构\n"
        "- 如果是纯插图且无文字，输出空字符串\n"
        "只输出提取的文字，不要添加解释。"
    )
    vision_prompt_diagram: str = (
        "请描述这页书籍/PDF 中的视觉内容：\n"
        "- 如果有图表、架构图、流程图、表格或公式：描述包含哪些元素、元素之间的关系、标注的文字\n"
        "- 如果是插图：说明它在解释什么概念或论证\n"
        "- 如果没有实质性视觉内容，输出：无\n"
        "用 2-3 句话描述。"
    )
    vision_prompt_title: str = (
        "请用一句话概括这张截图的主题。不超过 20 个字。\n"
        "只输出标题，不要输出其他内容。"
    )
    vision_prompt_single: str = (
        '请分析这页书籍/PDF 图像，如实描述你看到的内容，不要脑补或猜测不存在的信息。以 JSON 格式输出：\n'
        '{\n'
        '  "type": "页面类型：正文/扫描正文/目录/图表/表格/公式/代码/插图/封面/空白/其他",\n'
        '  "title": "一句话概括（不超过20字）",\n'
        '  "text": "提取有意义的文字，尽量保持段落、表格、公式、代码结构；无文字则输出空字符串",\n'
        '  "layout": "2-3句描述页面布局、主要区域、关键视觉元素",\n'
        '  "diagrams": "如有图表/表格/公式/插图则描述元素和关系；无实质性可视化内容则输出\\"无\\""\n'
        '}\n'
        '只输出 JSON。'
    )


def load_settings() -> Settings:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
        except Exception:
            pass
    s = Settings()
    save_settings(s)  # 首次启动写入默认配置
    return s


def save_settings(s: Settings):
    SETTINGS_FILE.write_text(
        json.dumps(asdict(s), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
