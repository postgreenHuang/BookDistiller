# Book-Distiller — 书籍蒸馏学习伴侣

将 PDF 书籍蒸馏为可检索、可追问、可持续学习的结构化知识库，并支持与“读完整本书、能按需翻书”的 AI 导师对话。

当前项目从 Video-Distiller 演进而来：尽量继承现有 PySide6 GUI、批量任务框架、Settings、Provider 配置、对话气泡与 session 管理；把媒介模型从“视频/音频/帧/转录”迁移为“PDF/页面/章节/索引/检索上下文”。

## 第一性原理

### 用户真正需要什么

- 用户不是要一个“读完 PDF 的大 prompt”，而是要一个能帮助理解、复习、追问、定位原文的学习导师。
- 用户的问题通常是局部的：某章、某概念、某段论证、某个跨章节关系；很少每轮都需要整本书全文。
- 好回答必须同时满足：懂全局结构、能命中局部原文、能用通俗语言解释、能给出出处。

### 约束是什么

- 大型 PDF 全文无法稳定、低成本地每轮塞进上下文。
- 长上下文会带来 token 成本、速度、注意力稀释和缓存命中不稳定的问题。
- 图片/扫描页理解昂贵，应只在必要页面使用视觉模型。
- 批量蒸馏会重复处理相同 PDF、章节、Prompt 和模型结果，必须缓存。

### 因此架构结论

Book-Distiller 不做“全书塞进 prompt”的导师，而做：

> **全书已索引，问题发生时按需唤醒相关记忆，并可继续读取原文证据的导师。**

核心架构是 **RAG + 层级摘要 + 缓存**：

- RAG：每轮问题从全书索引中检索相关章节卡片、术语、原文块、图表说明。
- 层级摘要：全书总览 → 章节卡片 → 小节摘要 → 原文块，按问题需要逐级展开。
- 缓存：PDF 解析、图片识别、章节笔记、摘要、embedding、检索结果、模型回复都可复用。

## 产品目标

- 主界面只保留两个顶层页签：**批量蒸馏**、**对话**。
- 删除原先单本“蒸馏”页签及视频专用的 5 步手动工作流。
- 批量蒸馏支持导入一本或多本 PDF 书籍。
- 每本书蒸馏完成后，在对话区生成一个以书名命名的文件夹。
- 每本书文件夹下包含每个章节的独立对话，并把“全书总览”对话放在章节列表最后。
- 每个章节对话首条可见内容是当前章节的二次重写笔记。
- 每个对话背后绑定同一本书的检索索引；对话时动态取回相关全书内容，而不是每轮注入全书。
- 对话体验完整继承当前工具：左侧 session/文件夹列表、消息气泡、图片显示、历史持久化、Provider 复用、设置入口等。
- 输出应专业、深刻、通俗，帮助用户理解、复述、迁移、定位原文和继续追问。

## 技术栈

- Python 3.10+ / PySide6（Qt6）/ PyInstaller
- PDF 解析与渲染：优先 PyMuPDF（fitz），必要时兼容 pypdf/pdfplumber
- 图片处理：Pillow / OpenCV（页面预览、插图裁剪、OCR 预处理，按需保留）
- OCR：第一版支持扫描版 PDF，优先通过本地图片识别/OCR 管线处理，必要时可接入云端 Vision
- Ollama（本地视觉模型、文本模型、embedding 模型）/ OpenAI 兼容 API（云端 AI）
- 本地索引：SQLite + JSONL；向量索引可用 NumPy/FAISS/Chroma 中择一，先以简单可打包方案为优先
- 独立本地数据目录：`~/.Book-Distiller/`，包含 settings、sessions、folders 和 session_meta

## 模型配置原则

批量蒸馏阶段至少配置两个模型：

- **图片识别模型**：用于扫描页、图表、公式、插图、复杂版式页面。优先本地 Ollama 视觉模型，必须在设置中提供“测试图片理解”按钮。
- **书籍整合模型**：用于章节笔记、全书总览、术语表、知识地图、问题回答。可选本地 Ollama 或 OpenAI 兼容 API。

建议额外配置：

- **Embedding 模型**：用于 RAG 检索。优先本地 embedding 模型，避免每次索引都走云端。
- **重排模型/轻量判断模型**：可选，用于判断用户问题范围、重排检索片段；第一版可用书籍整合模型承担。

注意：Gemma/Qwen 文本模型不等于视觉模型。设置界面需要区分“能看图的模型”和“只处理文本的模型”，避免用户把文本模型配置到图片识别环节。

## 目标项目结构

```
BookDistiller/
├── src/
│   ├── config.py              # 配置管理 (settings.json)，兼容旧字段
│   ├── book_pipeline.py       # 书籍蒸馏管线编排
│   ├── pdf_reader.py          # PDF 元数据、文本层抽取、页面渲染
│   ├── chapter_detector.py    # 目录/章节识别、页码范围切分
│   ├── page_analysis.py       # 页面/插图/表格理解（本地 Vision / 云端 Vision）
│   ├── note_builder.py        # 全书总览、章节笔记、术语表、学习问题生成
│   ├── indexer.py             # 切块、层级摘要、embedding、BM25/向量索引
│   ├── retriever.py           # 混合检索、重排、出处组织
│   ├── context_builder.py     # 对话时构造小而准的上下文包
│   ├── cache.py               # PDF/视觉/LLM/embedding/检索缓存
│   ├── chat.py                # 对话会话管理，扩展为 book/chapter session
│   └── gui/
│       ├── app.py             # 主窗口：批量蒸馏 + 对话
│       ├── chat_widget.py     # AI 对话界面，继承现有 QTextBrowser 气泡
│       ├── theme.py           # Light/Dark 主题 QSS
│       └── settings_dialog.py # 设置：基础/高级/Prompt/模型测试
├── output/{book_name}/
│   ├── pages/                 # 页面渲染图片（按需生成）
│   ├── chapters/              # 每章结构化文本、摘要、视觉结果
│   ├── notes/                 # 全书总览、每章二次重写笔记
│   ├── index/                 # 分块、层级摘要、术语表、向量/BM25 索引
│   ├── cache/                 # 本书级缓存
│   └── book.json              # 统一元数据：书籍、目录、章节、索引、资源路径
├── main.py
└── requirements.txt
```

## 书籍蒸馏工作流

| 阶段 | 模块 | 输入 → 输出 | 说明 |
|------|------|-------------|------|
| 1 | pdf_reader.py | PDF → book_meta + page_texts + page_images(可选) | 读取 PDF 元数据、目录、文本层；仅必要页面渲染为图片 |
| 2 | chapter_detector.py | book_meta + page_texts → chapters.json | 优先使用 PDF 目录；无目录时用规则 + AI 辅助切章 |
| 3 | page_analysis.py | 页面图片/复杂页 → visual_notes.json | 只对扫描页、图表、公式、插图调用图片识别模型 |
| 4 | indexer.py | 页面/章节文本 → chunks + summaries + embeddings + BM25 | 构建全书可检索索引和层级摘要 |
| 5 | note_builder.py | 章节内容 + 检索索引 + 视觉说明 → notes/*.md | 生成全书总览、章节笔记、术语表、知识地图 |
| 6 | chat.py | book.json + index → book folder + chapter sessions | 创建书籍文件夹、全书对话、章节对话；绑定检索索引 |

### 关键原则

- 优先抽取 PDF 文本层，避免把所有页面都当图片处理。
- 仅在文本层缺失、版式复杂、图表/公式重要时才进行页面渲染和视觉理解。
- “全书记忆”不是每轮注入全书，而是每轮动态检索全书索引。
- 首条 assistant 消息展示当前章节笔记；system prompt 保持稳定且短。
- 中间结果全部保留，便于重跑单章、修正目录、补做 OCR、重建索引或更换 Prompt。
- 批量任务可断点续跑：已完成的书籍/章节/索引/笔记默认跳过，失败项可重试。

## RAG 对话流程

每次用户提问时：

```text
用户问题
→ 问题范围判断：当前章节 / 全书 / 跨章节 / 查原文 / 概念解释
→ 查询改写：补充书名、当前章节、关键术语
→ 混合召回：当前章节加权 + BM25 关键词 + 向量检索 + 术语表/章节卡片
→ 重排与去重：优先有页码、章节匹配、信息密度高的片段
→ 上下文打包：全局少量摘要 + 当前章节笔记摘要 + top-k 原文证据
→ 调用书籍整合模型回答
→ 保存引用、检索命中和对话历史
```

默认上下文预算：

- 稳定 system prompt：导师身份、回答原则、引用要求。
- 书籍框架：书名、作者、目录压缩版、当前章节位置。
- 当前章节：章节笔记摘要或首条笔记引用。
- 动态证据：检索命中的 6-12 个片段，带章节/页码/来源类型。
- 最近对话：保留少量轮次，必要时对旧对话做摘要。

## 缓存设计

缓存必须成为一等公民，不是后期优化。

### 文件与解析缓存

- key：`pdf_sha256 + parser_version + settings_version`
- 缓存：元数据、目录、每页文本、页面图片、扫描页判断结果

### 图片识别缓存

- key：`page_image_hash + vision_model + prompt_version + image_settings`
- 缓存：OCR/图表/公式/插图说明
- 目标：同一页不重复调用视觉模型

### LLM 生成缓存

- key：`input_hash + model + prompt_version + generation_settings`
- 缓存：章节笔记、全书总览、术语表、知识地图、小节摘要
- 目标：换模型或 Prompt 才重跑，普通重试直接复用

### Embedding 与索引缓存

- key：`chunk_hash + embedding_model`
- 缓存：向量、BM25 文档、chunk 元数据
- 目标：改笔记 Prompt 不影响原文索引；改 embedding 模型才重建向量

### 对话检索缓存

- key：`normalized_query + book_id + chapter_id + index_version`
- 缓存：召回片段、重排结果
- 目标：常见问题更快响应；同时记录命中来源用于调试

### Provider Prompt Cache 友好性

如果云端 API 支持 prompt caching，稳定内容放在前面：

```text
稳定 system prompt
稳定书籍元信息/目录摘要
当前章节固定摘要
动态检索证据
最近对话
用户问题
```

但设计不能依赖云端 prompt cache。真正节省 token 的主机制是：检索命中后只注入必要证据。

## 输出数据约定

### book.json

```json
{
  "book_id": "clean-book-name",
  "title": "书名",
  "author": "作者",
  "source_pdf": "/absolute/path/book.pdf",
  "pdf_sha256": "...",
  "created_at": "2026-05-30 12:00:00",
  "chapters": [
    {
      "chapter_id": "ch01",
      "title": "第一章 ...",
      "page_start": 1,
      "page_end": 24,
      "text_path": "chapters/ch01/text.md",
      "summary_path": "chapters/ch01/summary.md",
      "visual_path": "chapters/ch01/visual_notes.json",
      "note_path": "notes/ch01.md"
    }
  ],
  "index": {
    "chunks_path": "index/chunks.jsonl",
    "chapter_cards_path": "index/chapter_cards.json",
    "terms_path": "index/terms.json",
    "bm25_path": "index/bm25.sqlite",
    "vector_path": "index/vectors.sqlite",
    "embedding_model": "..."
  },
  "memory": {
    "overview_path": "notes/book_overview.md",
    "knowledge_map_path": "index/knowledge_map.md"
  }
}
```

### chunk 记录

```json
{
  "chunk_id": "ch01_p012_003",
  "book_id": "clean-book-name",
  "chapter_id": "ch01",
  "page": 12,
  "type": "text|figure|table|formula|summary",
  "text": "片段内容",
  "source_path": "chapters/ch01/text.md",
  "tokens_estimate": 420
}
```

### 对话 session

- Session 持久化到 `~/.Book-Distiller/sessions/`，不读取旧 `~/.Video-Distiller` 对话，避免和视频蒸馏器混用。
- 新增元数据字段：`book_id`、`book_title`、`chapter_id`、`chapter_title`、`book_dir`、`book_json_path`、`chapter_note_path`、`index_version`。
- 每本书在左侧对话列表中表现为一个文件夹，文件夹名默认为书名。
- 每本书默认创建一个“全书总览”对话，展示顺序放在章节对话之后。
- 每章对话名默认为 `章节序号 - 章节标题`。
- 每章对话的第一条 assistant 消息是当前章节笔记。
- 每轮对话保存本轮检索命中的 chunk ids，便于复盘和调试回答依据。

## 章节笔记 Prompt — 默认模板

```
你是一位深刻、耐心、善于讲清复杂概念的读书导师。
我会给你一本书的全书目录、当前章节原文、必要的图表说明，以及全书层面的摘要/术语表。

请为当前章节生成一份面向学习者的二次重写笔记。
要求：专业、深刻、通俗；不是摘抄，不是简单摘要，而是帮助读者真正理解作者的论证、概念和方法。

请严格按以下结构输出：

## 本章主旨
用一段话说明本章到底在解决什么问题，以及它在全书中的位置。

## 核心概念
列出本章关键概念。每个概念包含：
- 通俗解释
- 作者为何需要这个概念
- 它和前后章节的关系

## 论证脉络
按作者的推进顺序重建本章逻辑，必要时用 “问题 → 观点 → 证据 → 结论” 表达。

## 关键细节
保留容易被忽略但影响理解的重要细节、限定条件、例子、数据、图表信息。

## 学习者应掌握什么
列出 3-5 个最重要的学习要点。每个要点说明：
- 是什么
- 为什么重要
- 如何判断自己掌握了

## 可能的困惑
预测读者可能卡住的地方，并用通俗语言解释。

## 可追问问题
给出 5-8 个高质量追问，帮助读者继续和 AI 导师对话。
```

## 对话 System Prompt

```
你是一位读完整本书、并且擅长教学的学习导师。
你不会假装每轮都重新阅读整本书；你会基于系统提供的书籍索引、章节笔记和检索证据回答。

回答规则：
1. 优先使用检索证据和当前章节笔记。
2. 如果问题需要跨章节比较，主动连接相关章节。
3. 如果证据不足，说明还需要查看原文的哪些部分，不要编造。
4. 尽量给出章节、页码或图表来源。
5. 用通俗但不浅薄的语言解释复杂内容。
6. 适合学习场景时，给出例子、类比、复述检查或下一步追问。

--- 书籍框架 ---
{book_brief}

--- 当前章节 ---
{chapter_brief}

--- 检索证据 ---
{retrieved_context}
```

所有 Prompt 均可在 Settings > 书籍蒸馏 / 对话中自定义。

## GUI 设计

```
Book-Distiller

┌───────────────┬───────────────┐
│  批量蒸馏      │    对话        │  ← 顶层仅两个 Tab
├───────────────┼───────────────┤
│ 输出目录       │ 书籍文件夹列表 │
│ PDF 列表       │ ├─ 书名 A      │
│ 添加 PDF       │ │  ├─ 第 1 章  │
│ 移除 / 清空    │ │  ├─ 第 2 章  │
│ 模型摘要       │ │  ├─ ...      │
│ 开始 / 停止    │ │  └─ 全书总览 │
│ 进度 / 日志    │ 消息气泡       │
└───────────────┴───────────────┘
```

### 批量蒸馏 Tab

- “视频列表”改为“书籍列表”，支持添加/拖拽 PDF。
- “开始批量蒸馏”改为“开始蒸馏书籍”。
- 主界面展示输出目录、PDF 列表、模型摘要、进度、日志；高级参数收进设置。
- 模型摘要至少显示：图片识别模型、书籍整合模型、embedding 模型。
- 进度以书籍、章节、索引阶段显示，例如：`第 1/3 本：构建索引 420/1180 段`。
- 失败项支持重试，并尽量跳过已完成章节和已缓存结果。

### 对话 Tab

- 完整继承当前 `ChatWidget` 的视觉和交互基础。
- 左侧 session 树按书籍文件夹分组；文件夹下先显示章节对话，最后显示“全书总览”。
- 进入章节对话时，显示当前章节笔记作为首条内容。
- 对话时通过 `retriever.py` 和 `context_builder.py` 动态读取索引原文。
- 对话配置齿轮支持查看/修正 `book.json`、章节笔记、索引路径和模型配置。

## 开发约定

- 模块通过文件系统解耦，输入输出均为文件路径。
- 非破坏性流水线：PDF 原文抽取、页面图片、章节 JSON、视觉分析、索引、笔记、对话历史全部保留。
- 本地优先：能从 PDF 文本层抽取就不做视觉；能本地 OCR/视觉就优先本地；云端 AI 尽量只接收纯文本。
- GUI：PySide6 + Apple 风格 QSS，Light/Dark 主题。
- 改 GUI 必须验证 dark 模式覆盖完整，不能有白底。
- Settings 存储在 `~/.Book-Distiller/settings.json`；字段变更需兼容缺字段默认值。
- 旧视频相关字段和模块先兼容保留，完成迁移后再分阶段清理。

## 依赖计划

保留现有依赖，并新增/评估：

```
PyMuPDF, pypdf, pdfplumber
```

向量检索第一版优先使用易打包方案：

- MVP：SQLite + NumPy 向量余弦相似度 + 简单 BM25/关键词检索
- 后续：FAISS 或 Chroma，视打包复杂度和性能决定

扫描 PDF 是第一版目标能力：优先使用本地图片识别/OCR，云端 Vision 仅作为可配置 fallback。

## 优先级与验证计划

第一性原理下，优先级不是“先做最完整的蒸馏”，而是先验证学习闭环：

> 一本 PDF → 可解析 → 可切章 → 可索引 → 问题能命中原文 → 回答有出处 → token 有上限。

### Phase A — 产品骨架迁移

| # | 任务 | 验证标准 | 涉及文件 |
|---|------|----------|----------|
| A1 | 顶层页签精简 | GUI 只剩“批量蒸馏 / 对话”；窗口标题为 Book-Distiller | app.py, theme.py |
| A2 | 批量入口改书籍 | 可添加/拖拽 PDF；视频文案从主流程消失 | app.py |
| A3 | 模型配置占位 | 设置中能区分图片识别模型、书籍整合模型、embedding 模型 | config.py, settings_dialog.py |
| A4 | 保留对话 GUI | ChatWidget 原有 session、气泡、历史功能不破坏 | chat_widget.py, chat.py |

### Phase B — 最小 RAG 闭环

| # | 任务 | 验证标准 | 涉及文件 |
|---|------|----------|----------|
| B1 | PDF 文本抽取 | 输入一本有文本层 PDF，输出每页文本和 book.json | pdf_reader.py |
| B2 | 基础章节切分 | 有 PDF TOC 时正确生成章节；无 TOC 时至少按整书/页段降级 | chapter_detector.py |
| B3 | 文本切块 | 生成 chunks.jsonl，chunk 带 book/chapter/page/source | indexer.py |
| B4 | 本地检索 | 输入关键词问题，能返回相关 chunk 和页码 | retriever.py |
| B5 | 对话上下文打包 | 每轮 prompt token 受预算控制，只包含 top-k 证据 | context_builder.py, chat.py |

### Phase C — 缓存与断点续跑

| # | 任务 | 验证标准 | 涉及文件 |
|---|------|----------|----------|
| C1 | PDF 解析缓存 | 同一 PDF 二次运行跳过解析 | cache.py, pdf_reader.py |
| C2 | Embedding 缓存 | 未改 embedding 模型时不重复计算向量 | cache.py, indexer.py |
| C3 | LLM 结果缓存 | 同章节同 Prompt/模型不重复生成笔记 | cache.py, note_builder.py |
| C4 | 批量断点续跑 | 中断后重启能跳过已完成书籍/章节/索引 | book_pipeline.py, app.py |

### Phase D — 层级摘要与高质量笔记

| # | 任务 | 验证标准 | 涉及文件 |
|---|------|----------|----------|
| D1 | 章节卡片 | 每章生成 300-800 字结构化摘要，用于检索和全局定位 | note_builder.py |
| D2 | 全书总览 | 生成全书主线、目录解释、核心问题、阅读路线 | note_builder.py |
| D3 | 术语/观点索引 | 能按概念、人物、观点召回相关页码和章节 | note_builder.py, indexer.py |
| D4 | 章节笔记 | 每章首条笔记专业、深刻、通俗，不只是摘抄 | note_builder.py |

### Phase E — 视觉页面处理

| # | 任务 | 验证标准 | 涉及文件 |
|---|------|----------|----------|
| E1 | 页面类型判断 | 区分纯文本页、扫描页、图表页、公式页；扫描页进入 OCR/视觉队列 | pdf_reader.py, page_analysis.py |
| E2 | 图片识别模型测试 | 设置中可测试当前模型是否支持图片输入 | settings_dialog.py |
| E3 | 按需视觉识别 | 只对必要页面调用视觉模型，结果进入 chunk/index | page_analysis.py, indexer.py |
| E4 | 视觉缓存 | 同一页面图片不重复识别 | cache.py, page_analysis.py |

### Phase F — 自动生成书籍对话

| # | 任务 | 验证标准 | 涉及文件 |
|---|------|----------|----------|
| F1 | 书籍文件夹创建 | 蒸馏完成后左侧出现书名文件夹 | chat.py, chat_widget.py |
| F2 | 全书总览对话 | 每本书默认有一个宏观对话入口，并排在章节列表最后 | chat.py |
| F3 | 每章 session | 每章一个对话，首条 assistant 消息为章节笔记 | chat.py |
| F4 | RAG 问答接入 | 对话问题能触发检索，并保存命中 chunk ids | chat.py, retriever.py, context_builder.py |

### Phase G — 体验打磨

| # | 任务 | 验证标准 | 涉及文件 |
|---|------|----------|----------|
| G1 | 中文化与文案 | 清理 Video/视频/转录/选帧等遗留主界面文案 | app.py, chat_widget.py, settings_dialog.py |
| G2 | 章节状态可视化 | 显示每本书章节总数、完成数、失败章节和重试入口 | app.py, theme.py |
| G3 | 引用展示 | 回答可展示章节/页码来源，便于回到原书核对 | chat_widget.py |
| G4 | Dark 模式验收 | 新增控件 dark 模式无白底、无低对比文本 | theme.py |

## 当前阶段

**Phase A 待实施**，但实现顺序以最小 RAG 闭环为北极星：先让一本 PDF 能被解析、切块、检索、对话命中原文，再扩展视觉识别、章节笔记和批量体验。

## 已确认产品决策

1. 第一版只支持 PDF。
2. 第一版支持扫描版 PDF，扫描页通过图片识别/OCR 管线处理。
3. 每本书默认创建“全书总览”对话，但展示在章节列表最后。
4. 回答默认带章节/页码引用，并允许在设置中关闭。
5. 本地 embedding 模型通过 Ollama 配置，作为 RAG 检索的默认方向。
