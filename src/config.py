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

RESOLUTION_SCALES = ["原始", "3/4", "1/2", "1/4", "1/6", "1/8", "1/10", "1/12"]
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

ASR_CLOUD_MODELS = [
    "whisper-large-v3",        # Groq
    "whisper-large-v3-turbo",  # Groq turbo
    "whisper-1",               # OpenAI
    "qwen3-asr-flash",         # DashScope 百炼
    "sensevoice-v1",           # DashScope 百炼
]
ASR_CLOUD_PRESETS = {
    "Groq": "https://api.groq.com/openai/v1",
    "OpenAI": "https://api.openai.com/v1",
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
class ProviderConfig:
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class VocabConfig:
    name: str = ""
    terms: str = ""  # 逗号分隔的术语字符串


@dataclass
class Settings:
    last_video_path: str = ""
    last_output_dir: str = ""
    last_asr_model: str = ""       # 蒸馏 Step 2 转录模型
    last_select_provider: str = "" # 蒸馏 Step 3 选帧 AI
    last_agg_provider: str = ""    # 蒸馏 Step 5 聚合 AI
    last_batch_asr: str = ""       # 批量 转录模型
    last_batch_select: str = ""    # 批量 选帧 AI
    last_batch_vision: str = ""    # 批量 图片理解
    last_batch_agg: str = ""       # 批量 聚合 AI
    last_batch_embedding: str = "" # 批量 书籍索引 Embedding
    chat_font_family: str = ""    # 对话字体（空=默认）
    chat_font_scale: int = 100    # 字体缩放百分比
    book_support_scanned_pdf: bool = True
    book_overview_position: str = "after_chapters"
    cite_sources_by_default: bool = True
    embedding_active: str = "nomic-embed-text"
    embedding_models: list = field(default_factory=lambda: [
        {"name": "nomic-embed-text 本地", "type": "ollama", "model": "nomic-embed-text", "url": "http://localhost:11434", "api_key": ""},
        {"name": "bge-m3 本地", "type": "ollama", "model": "bge-m3", "url": "http://localhost:11434", "api_key": ""},
    ])
    theme: str = "dark"
    resolution_scale: str = "原始"
    sample_rate: int = 16000
    frame_interval: float = 1.0  # 秒，每隔几秒截一帧
    ssim_threshold: float = 0.95
    whisper_model: str = "large-v3"
    whisper_batch_size: int = 16
    whisper_language: str = ""
    segment_length: int = 180
    asr_type: str = "local"  # "local" | "cloud"
    asr_cloud_active: str = "Groq"
    asr_cloud_configs: list = field(default_factory=lambda: [
        {"name": "DashScope", "base_url": "", "api_key": "", "model": "qwen3-asr-flash", "api_type": "dashscope"},
        {"name": "Groq", "base_url": "https://api.groq.com/openai/v1", "api_key": "", "model": "whisper-large-v3", "api_type": "whisper"},
        {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "api_key": "", "model": "whisper-1", "api_type": "whisper"},
    ])
    ollama_url: str = "http://localhost:11434"
    vision_concurrent: int = 4  # 图片理解并发数（本地 Ollama 4，云端可 4-8）
    vision_active: str = "minicpm-v 本地"  # 当前激活的视觉模型名称
    vision_models: list = field(default_factory=lambda: [
        {"name": "Gemma4:26b 本地", "type": "ollama", "model": "gemma4:26b", "url": "http://localhost:11434", "api_key": "", "prompt_strategy": "single"},
        {"name": "minicpm-v 本地", "type": "ollama", "model": "minicpm-v:8b", "url": "http://localhost:11434", "api_key": "", "prompt_strategy": "triple"},
        {"name": "GLM-4V 云端", "type": "cloud", "model": "glm-4v-plus", "url": "https://open.bigmodel.cn/api/paas/v4", "api_key": "", "prompt_strategy": "single"},
        {"name": "Qwen-VL 云端", "type": "cloud", "model": "qwen-vl-max", "url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "", "prompt_strategy": "single"},
    ])
    providers: list = field(default_factory=lambda: [
        {"name": "Gemini", "base_url": "", "api_key": "", "model": "gemini-1.5-pro"},
        {"name": "OpenAI", "base_url": "", "api_key": "", "model": "gpt-4o"},
        {"name": "Claude", "base_url": "", "api_key": "", "model": "claude-sonnet-4-6"},
        {"name": "Ollama", "base_url": "http://localhost:11434", "api_key": "", "model": "llama3"},
    ])
    vocabularies: list = field(default_factory=lambda: [
        {"name": "GDC 通用", "terms": "GDC, shader, rendering, rasterization, ray tracing, path tracing, global illumination, PBR, LOD, culling, GPU, CPU, ECS, data oriented design, compute shader"},
        {"name": "Unreal Engine", "terms": "Unreal, UE5, Nanite, Lumen, MetaHuman, Niagara, Chaos, Blueprint, World Partition, Gameplay Ability System, GAS, Enhanced Input, Lyra, Verse"},
        {"name": "Unity", "terms": "Unity, GameObject, MonoBehaviour, Prefab, ScriptableObject, NavMesh, Animator, URP, HDRP, Shader Graph, VFX Graph, DOTS, ECS, Burst, Job System"},
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
        "按作者的推进顺序重建本章逻辑，必要时用“问题 -> 观点 -> 证据 -> 结论”表达。\n\n"
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


def get_project_dir(output_dir: str, video_path: str) -> Path:
    name = Path(video_path).stem
    project_dir = Path(output_dir) / name
    project_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("audio", "frames", "key_frames", "transcript", "notes"):
        (project_dir / sub).mkdir(exist_ok=True)
    return project_dir


def get_unified_json_path(output_dir: str, video_path: str) -> Path:
    """返回统一 JSON 路径: {project_dir}/{video_name}.json"""
    name = Path(video_path).stem
    return Path(output_dir) / name / f"{name}.json"


def read_unified_json(json_path: str | Path) -> dict:
    """读取统一 JSON，不存在则返回空字典"""
    p = Path(json_path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def write_unified_json(json_path: str | Path, data: dict):
    """写入统一 JSON"""
    Path(json_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_vocab_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""
