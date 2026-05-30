"""
Video-Distiller 配置管理
- 用户配置持久化到 settings.json
- 支持多 AI Provider、自定义术语词表
"""

import json
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# 用户数据目录: C:\Users\{user}\.Video-Distiller\
USER_DATA_DIR = Path.home() / ".Video-Distiller"
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
        "你是一位经验丰富的技术导师。我会给你提供一段技术演讲的幻灯片文字描述和语音转录。\n"
        "请生成一份面向学习者的结构化笔记，严格按以下层次输出：\n\n"
        "## 概括\n"
        "用一段话概括本教程的核心主题和学习价值。\n\n"
        "## 目录\n"
        "列出教程的章节大纲（基于内容变化划分）。\n\n"
        "## 核心思路流程\n"
        "用文字描述教程的主线逻辑和关键决策节点（如适用，用 → 符号表示流程）。\n\n"
        "## 详细内容\n"
        "按章节展开，每个章节包含：\n"
        "- [时间戳] 章节标题\n"
        "- 核心概念和原理（通俗解释）\n"
        "- 讲者提到的关键数值、性能指标\n"
        "- 根据幻灯片文字修正转录中的术语错误\n"
        "- 如果该章节有关键幻灯片截图，用以下格式引用（每张图独占一行）：\n"
        "  `![章节描述](文件名)`\n"
        "  其中文件名必须逐字复制幻灯片描述中「文件:」后面的文件名，例如 `![UE编辑器界面](05_12_frame.jpg)`\n"
        "  绝对不要自己编造文件名（如 img_0、image_1 等），必须使用幻灯片描述中提供的真实文件名\n\n"
        "## 截图索引\n"
        "列出笔记中引用的所有截图，格式为每行一个：\n"
        "`![截图描述](文件名.jpg)`\n\n"
        "## 前置知识\n"
        "学习本教程前需要掌握哪些基础知识和概念。\n\n"
        "## 知识点清单\n"
        "列出本教程涵盖的所有知识点（编号列表）。\n\n"
        "## 学习重点\n"
        "标注最重要的 3-5 个学习要点，每个要点说明：\n"
        "- 是什么（一句话）\n"
        "- 为什么重要\n"
        "- 如何掌握\n\n"
        "## 拓展学习\n"
        "推荐的参考文献、书籍、视频资源（讲者提到的或相关的），每条附一句话说明价值。\n\n"
        "【重要】截图引用格式规则：\n"
        "- 只使用标准 Markdown 图片语法：`![描述文字](文件名)`\n"
        "- 文件名必须逐字复制幻灯片描述中「文件:」后面的内容（如 00_05_frame.jpg），不要添加路径前缀\n"
        "- 不要自己编造文件名（如 img_0、image_1、screenshot_1 等都不是有效文件名）\n"
        "- 如果没有可引用的截图，不要强行引用"
    )
    vision_prompt_ocr: str = (
        "请提取这张截图中所有有意义的文字内容。\n"
        "- 如果是软件界面：提取窗口标题、面板中显示的关键参数和数值、按钮/标签中的核心文本；跳过菜单栏、工具栏图标名称等通用 UI 元素\n"
        "- 如果是 PPT/文档：按原文逐字提取，代码保持格式，表格用 Markdown 还原\n"
        "- 如果是纯图片/动画帧且无文字，输出空字符串\n"
        "只输出提取的文字，不要添加解释。"
    )
    vision_prompt_diagram: str = (
        "请描述这张截图中的视觉内容：\n"
        "- 如果有图表、架构图、流程图或节点网络：描述包含哪些元素、元素之间的关系、标注的文字\n"
        "- 如果是软件界面：描述界面布局（有哪些面板、视口显示什么内容、参数面板的 key-value）\n"
        "- 如果是 3D 视口/渲染结果：描述场景中的物体、材质、光照等视觉特征\n"
        "- 如果是纯装饰性背景/动画帧，无实质性视觉内容，输出：无\n"
        "用 2-3 句话描述。"
    )
    vision_prompt_title: str = (
        "请用一句话概括这张截图的主题。不超过 20 个字。\n"
        "只输出标题，不要输出其他内容。"
    )
    vision_prompt_single: str = (
        '请分析这张视频截图，如实描述你看到的内容，不要脑补或猜测不存在的信息。以 JSON 格式输出：\n'
        '{\n'
        '  "type": "画面类型：软件界面/PPT幻灯片/代码/终端/图表/片头动画/片尾/黑屏转场/其他",\n'
        '  "title": "一句话概括（不超过20字）",\n'
        '  "text": "提取有意义的文字。软件界面只提取窗口标题和关键参数，跳过菜单栏；PPT按原文提取，代码保持格式；无文字则输出空字符串",\n'
        '  "layout": "2-3句描述你实际看到的视觉内容：整体布局、主要区域、关键视觉元素。如果只是装饰性动画/黑屏/转场，直接说明即可，不要强行描述为有意义的画面",\n'
        '  "diagrams": "如有图表/节点网络/架构图则详细描述元素和关系；软件界面描述面板内容和参数值；无实质性可视化内容则输出\\"无\\""\n'
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
