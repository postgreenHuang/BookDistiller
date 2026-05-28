# Video-Distiller — 技术视频学习伴侣

将长技术演讲视频蒸馏为面向学习者的结构化笔记，并支持与"咀嚼了教程知识"的 AI 导师持续对话。

面向非技术用户，本地桌面 GUI 应用，可打包为 .exe。

## 技术栈

- Python 3.10+ / PySide6（Qt6）/ PyInstaller
- FFmpeg（音频提取 + 抽帧）/ OpenCV（SSIM 去重）/ faster-whisper（本地 ASR）
- Ollama（本地视觉模型）/ OpenAI 兼容 API（云端 AI）
- settings.json 持久化配置

## 项目结构

```
VideoSteamer/
├── src/
│   ├── config.py           # 配置管理 (settings.json)
│   ├── pipeline.py         # 管线编排 (5 阶段顺序/并发调度)
│   ├── media.py            # ① MP3 提取 + ② 帧提取 (FFmpeg)
│   ├── frame_selector.py   # ③ AI 智能选帧 (转录语义分析替代 SSIM)
│   ├── visual.py           # (备用) SSIM/pHash 去重
│   ├── image_analysis.py   # ④ 图片理解 (Ollama 视觉 / 云端 Vision)
│   ├── transcribe.py       # ⑤ 语音转录 (本地 Whisper / 云端 ASR)
│   ├── chat.py             # ⑥ AI 对话会话管理
│   └── gui/
│       ├── app.py          # 主窗口 (两 Tab: 蒸馏 + 对话)
│       ├── chat_widget.py  # AI 对话界面 (MessageBubble = QTextBrowser)
│       ├── theme.py        # Light/Dark 主题 QSS
│       └── settings_dialog.py
├── output/{project_name}/
│   ├── audio/ frames/ key_frames/ transcript/ notes/
│   └── chat/               # 对话历史 (chat_history.json)
├── main.py                 # 入口
└── requirements.txt
```

## 工作流

### 蒸馏管线（5 阶段）

| 阶段 | 模块 | 输入 → 输出 | 说明 |
|------|------|-------------|------|
| 1 | media.py | 视频 → MP3 + 帧 | FFmpeg 提取，进度回调 |
| 2 | transcribe.py | MP3 → transcript.json | faster-whisper 本地 / DashScope 云端 ASR |
| 3 | frame_selector.py | transcript + 帧 → 关键帧 | AI 语义选帧，替代 SSIM 去重 |
| 4 | image_analysis.py | 关键帧 → slides.json | Ollama/云端视觉模型，三步或单步 Prompt |
| 5 | Step 5 UI | slides + transcript → notes.md | AI 聚合，教育化 Prompt，纯文本输入 (~30k tokens) |

ASR 双层修正：转录时注入术语 (initial_prompt) + AI 聚合时根据幻灯片文字自动纠错。AI 选帧根据转录语义判断哪些帧值得视觉分析，跳过纯文字已充分传达的内容。

### AI 对话

蒸馏完成后，学习者可以在对话 Tab 与 AI 导师持续对话：

- **上下文注入**：slides.json + transcript.json + 蒸馏笔记全部作为 system prompt
- **对话历史持久化**：每个项目独立保存 `{project_dir}/chat/chat_history.json`
- **Provider**：复用 Settings 中已配置的 AI Provider（Gemini/OpenAI/Codex/Ollama）
- **非流式输出**：等待完整回复后显示

## GUI 设计

```
Video-Distiller

📹 视频路径: [____________]  [浏览...]
📁 输出目录: [____________]  [浏览...]

┌─────────────┬─────────────┐
│   蒸 馏      │   对 话      │  ← 顶层两个 Tab
├─────────────┼─────────────┤
│ Step1: 媒体  │ 消息列表     │
│ Step2: 转录  │ (气泡样式)   │
│ Step3: 选帧  │             │
│ Step4: 理解  │             │
│ Step5: 聚合  │ 状态: 已加载  │
│             │ 输入框 [发送] │
└─────────────┴─────────────┘
```

### 蒸馏 Tab

内嵌 5 个子 Tab（现有工作流不变）。

### 对话 Tab

- 消息列表：QScrollArea + 消息气泡（用户右对齐深色、AI 左对齐浅色）
- 状态栏：显示已加载的蒸馏笔记项目名和段数
- 输入区域：QTextEdit + 发送/清空按钮
- 自动加载：切换到对话 Tab 时，检测项目目录是否有蒸馏结果，有则自动初始化会话

## 蒸馏 Prompt — 教育化模板

Phase 4 AI 聚合时使用，输入为纯文本（slides.json + transcript.json）。

```
你是一位经验丰富的技术导师。我会给你提供一段技术演讲的幻灯片文字描述和语音转录。
请生成一份面向学习者的结构化笔记，严格按以下层次输出：

## 概括
用一段话概括本教程的核心主题和学习价值。

## 目录
列出教程的章节大纲（基于内容变化划分）。

## 核心思路流程
用文字描述教程的主线逻辑和关键决策节点（如适用，用 → 符号表示流程）。

## 详细内容
按章节展开，每个章节包含：
- [时间戳] 章节标题
- 核心概念和原理（通俗解释）
- 讲者提到的关键数值、性能指标
- 根据幻灯片文字修正转录中的术语错误

## 前置知识
学习本教程前需要掌握哪些基础知识和概念。

## 知识点清单
列出本教程涵盖的所有知识点（编号列表）。

## 学习重点
标注最重要的 3-5 个学习要点，每个要点说明：
- 是什么（一句话）
- 为什么重要
- 如何掌握

## 拓展学习
推荐的参考文献、书籍、视频资源（讲者提到的或相关的），每条附一句话说明价值。
```

所有 Prompt 均可在 Settings > AI 聚合中自定义。

## 对话 System Prompt

```
你是一位技术学习导师。你刚刚和学生一起学习了一段技术教程。
以下是教程的完整学习笔记和原始幻灯片描述，作为你的知识基础：

--- 学习笔记 ---
{distilled_notes}

--- 幻灯片描述 ---
{slides_summary}

你的任务是：
1. 回答学生关于教程内容的问题
2. 用通俗的语言解释复杂概念
3. 帮助学生建立知识之间的联系
4. 指出容易忽略的重要细节
5. 建议进一步学习的方向
```

## 开发约定

- 模块通过文件系统解耦，输入输出均为文件路径
- 非破坏性流水线：所有中间结果保留，支持多次迭代
- 本地优先：图片理解用 Ollama 本地处理，云端 AI 只接收纯文本
- 输出按时间戳命名（`05_12_frame.jpg` = 5分12秒）
- GUI：PySide6 + Apple 风格 QSS，Light/Dark 主题
- 改 GUI 必须验证 dark 模式覆盖完整，不能有白底
- Settings 四个 Tab：通用 / 图片识别 / 语音识别 / AI 聚合
- 配置字段变更需兼容旧 settings.json（缺字段用默认值）

## 依赖

```
PySide6>=6.5, python-dotenv, opencv-python, opencv-contrib-python,
scikit-image, faster-whisper, google-generativeai, openai, anthropic,
requests, Pillow, pyinstaller, numpy, dashscope
```

## 当前硬件

i9-11代 / 64GB RAM / NVIDIA RTX 3090 24GB VRAM
- PyTorch 2.11.0+cu126 (CUDA 12.6)
- Ollama 可用 CUDA 加速，视觉模型可本地跑
- faster-whisper 用 CUDA float16 跑 large-v3
- 同时有 Mac 设备用于跨平台测试

## 对话系统架构决策

### 消息气泡：QTextBrowser（非 QLabel）

- QLabel 的 RichText 不支持加载外部图片，`file:///` URL 无效
- 改用 QTextBrowser，支持图片、链接点击、富文本交互
- 图片通过 `QTextDocument.addResource(ImageResource, ...)` 预加载到文档缓存
  - 在 `setHtml()` 之前调用 `_preload_images()`
  - 绕过 `file:///` URL 加载机制（Windows 不可靠）
- 每张图用唯一 key（`img_0`, `img_1`...）作为 src，映射到本地绝对路径
- 支持三种图片引用格式：`file:///` 绝对 URL、相对文件名、`http/https/data` 外部链接

### 连续图片横向排列

- HTML 后处理 `_group_consecutive_images()` 检测连续 `<p><img/></p>` 并合并为一行
- 每张图包裹 `<a href="imgview:///path">` 支持点击查看大图
- `_ImageViewerDialog`：弹出窗口显示缩放后原图（不超过屏幕 85%）

### 气泡高度自适应

- 监听 `documentSizeChanged` 信号，`setFixedHeight(doc_h + 26)`（26 = 24px QSS padding + 2px buffer）
- QTextBrowser 需要手动收缩高度，不像 QLabel 自动收缩

### 对话 Session 管理

- Session 持久化到 `~/.Video-Distiller/sessions/{timestamp}/`
- 支持文件夹分组、移动、批量删除
- 蒸馏笔记作为首条 assistant 消息注入对话
- 齿轮按钮配置关联文件（notes.md + 数据 JSON）

---

## v2.0 重构计划

> 基于产品审视反馈，从"开发者工具"向"用户产品"演进。

### 设计原则

- 主界面只暴露"选择视频 → 一键开始"的最小路径，技术参数全部收进设置
- 让 80% 场景只需要 20% 的界面，Power User 去设置里调参
- 步骤有前置依赖，未满足条件的步骤不可操作
- 全中文界面，技术术语只在设置高级选项中保留
- 改 GUI 必须验证 dark 模式覆盖完整（延续 v1 规则）

### 实施阶段

#### Phase A — 体验骨架

| # | 任务 | 说明 | 涉及文件 |
|---|------|------|----------|
| A1 | 主界面简化 | Step 1-4 各步骤只保留"开始"按钮+进度+结果，技术参数（采样率/fps/SSIM阈值/Whisper模型/Provider/Prompt）全部移入设置，主界面用设置默认值 | app.py, settings_dialog.py |
| A2 | 步骤状态可视化+前置依赖 | Tab 标签显示状态图标（○未开始/◉可执行/✓已完成），未满足前置条件的步骤灰掉+tooltip 提示，依赖：1→2, 1→3, 2+3→4, 4+3→5，每次完成步骤自动刷新 | app.py, theme.py |
| A3 | Empty State 引导 | 首次启动路径栏显示拖拽提示，空白步骤显示引导文案+居中 CTA，对话 Tab 无对话时显示新建引导，对话已创建无文件时显示醒目的配置提示 | app.py, chat_widget.py |
| A4 | 拖拽支持 | 主窗口/视频路径输入框支持拖拽视频文件，Step 5 的 JSON 路径输入框支持拖拽文件 | app.py |

#### Phase B — 交互打磨

| # | 任务 | 说明 | 涉及文件 |
|---|------|------|----------|
| B1 | 导航重构：侧边栏步骤指示器 | 去掉顶层 Tab+Step 子 Tab 双层嵌套，改为左侧窄边栏（~60px）竖向步骤图标+文字，右侧显示当前步骤内容，底部放对话入口+设置按钮，进入对话时隐藏路径栏 | app.py, theme.py |
| B2 | 中英文统一 | 全部界面中文化：Settings→设置，Light/Dark→浅色/深色，Provider→AI模型，Step N→中文步骤名，技术术语从主界面消失 | app.py, chat_widget.py, settings_dialog.py |
| B3 | 状态反馈视觉化 | 进度条带文字（`已提取 128/256 帧`），成功/失败颜色区分（绿✓/红✗），窗口标题动态（`Video-Distiller — {视频名}`），错误信息友好化 | app.py, theme.py |
| B4 | 对话体验优化 | 齿轮按钮移到对话区域右上角，侧边栏宽度→260px+名称过长省略号，列表项 hover 显示删除按钮，新建对话后显示醒目 empty state，对话支持流式输出(SSE) | chat_widget.py, chat.py, theme.py |
| B5 | 关键帧画廊改进 | Step 2 去重结果改为 Flow/Grid 布局（非水平滚动），图片弹窗支持滚轮缩放+拖拽平移，缩略图显示时间戳覆盖层 | app.py |

#### Phase C — 细节完善

| # | 任务 | 说明 | 涉及文件 |
|---|------|------|----------|
| C1 | 设置分级 | 设置拆为"基础"（主题/AI模型/Ollama地址）和"高级"（采样率/SSIM/ASR接口/Prompt模板），首次无配置弹出快速设置向导 | settings_dialog.py |
| C2 | 转录预览增强 | Step 3 转录结果支持搜索和高亮 | app.py |
| C3 | 快捷键 | Ctrl+O 打开视频，Ctrl+Enter 发送消息，Ctrl+, 打开设置，Ctrl+N 新建对话 | app.py |
| C4 | 管线全局进度 | 状态栏或侧边栏底部显示管线整体进度（0/5 步骤完成），完成所有步骤显示"笔记已就绪"横幅 | app.py, theme.py |
| C5 | 笔记查看器 | Step 5 完成后在区域内嵌 Markdown 渲染预览，支持"复制全文"和"在外部编辑器打开" | app.py |

### 当前阶段

**Phase A 待实施**，从 A1 开始。

### 已完成的重构项（v2.0 之前）

- 统一 JSON 输出：slides + transcript 合并为单文件
- 对话 GUI 视觉优化：气泡样式、侧边栏 session 管理、文件夹分组
- 跨平台适配：macOS 打包支持，字体按平台选择
- AI 智能选帧：用转录语义分析替代 SSIM 去重（frame_selector.py）
- 图片显示：QTextBrowser + addResource 方案，连续图片横向排列 + 点击查看大图
