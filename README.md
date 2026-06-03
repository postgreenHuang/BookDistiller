<div align="center">

# Book-Distiller

### 将 PDF 书籍蒸馏为可检索、可追问的结构化知识库，并支持与 AI 导师对话

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![PySide6](https://img.shields.io/badge/PySide6-Qt6-green.svg)](https://doc.qt.io/qtforpython-6/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 为什么需要 Book-Distiller？

读一本技术书，你需要的不是"全文塞进 AI"的对话，而是一个**读完整本书、能按需翻书、能指出处**的学习导师。

Book-Distiller 把 PDF 书籍蒸馏为可检索、可追问、可持续学习的结构化知识库：

- **RAG 检索**：每轮问题从全书索引中检索相关内容，不每轮注入全书
- **层级摘要**：全书总览 → 章节卡片 → 原文块，按问题需要逐级展开
- **AI 导师对话**：蒸馏完成后，每章自动创建独立对话，首条消息即为章节笔记

**一句话：扔一本 PDF 进来，拿走一份能读、能搜、能问的学习笔记。**

---

## ✨ 功能亮点

### 📚 一键蒸馏管线

| 阶段 | 做什么 |
|------|--------|
| PDF 解析 | 提取元数据、文本层、页面分类（文本页/扫描页/空白页/封面） |
| 目录识别 | PDF 内置 TOC → AI 视觉识别目录页 → 文本解析 → 正则 → 兜底 |
| 视觉分析 | 扫描页 OCR、图表/公式/插图识别（本地视觉模型或云端 Vision API） |
| 索引构建 | 文本切块 + BM25 关键词索引，支持断点续跑 |
| 笔记生成 | 每章生成面向学习者的二次重写笔记（云端大模型） |
| 对话创建 | 自动创建书籍文件夹、章节对话、全书总览对话 |

管线非破坏性：PDF 原文、页面图片、OCR 结果、索引、笔记全部保留，可单独重跑。

### 💬 AI 导师对话

蒸馏完成后，每本书在对话区生成一个文件夹：

- 每章一个独立对话，首条消息为该章的重写笔记
- "全书总览"对话放在章节列表最后
- 对话时通过 RAG 检索全书索引，动态取回相关内容
- 回答带章节/页码出处，证据不足时说明需要查看原文

### 🏠 本地优先

- 扫描页 OCR：优先本地 Ollama 视觉模型，数据不出机器
- 章节笔记：使用云端 OpenAI 兼容 API（配置一次即可）
- 对话历史完全保存在本地（`~/.Book-Distiller/`）
- 支持扫描版 PDF，全扫描 PDF 自动逐页探测目录

### 🎨 桌面级体验

- PySide6 原生 GUI，Light / Dark 主题
- 拖拽 PDF 文件即可开始
- 实时进度反馈，每步耗时可见
- 批量蒸馏支持断点续跑，已完成的步骤自动跳过

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- NVIDIA GPU（推荐，本地视觉模型用 CUDA 加速）

### 安装

```bash
git clone https://github.com/postgreenHuang/BookDistiller.git
cd BookDistiller
pip install -r requirements.txt
```

### 可选：本地 AI 加速

```bash
# 安装 Ollama，拉取视觉模型
ollama pull qwen3.5:latest
# 或其他支持图片识别的模型
ollama pull gemma4:26b
```

### 运行

```bash
python main.py
```

### 打包为 exe

```bash
pyinstaller build.spec
```

---

## ⚙️ 支持的 AI 服务

| 功能 | 本地 | 云端 |
|------|------|------|
| 图片识别 / OCR | Ollama 视觉模型 | OpenAI Vision / GLM-4V / Qwen-VL |
| 书籍整合（笔记/对话） | — | OpenAI / Gemini / DashScope 等 OpenAI 兼容 API |
| Embedding（检索） | — | 跟随书籍整合模型 |

所有 Provider 在设置中一键切换，Prompt 模板可自定义。

> ⚠️ 文本模型（如 qwen 文本版、gemma 文本版）不支持图片识别。请确保图片识别环节配置的是视觉模型。

---

## 🏗️ 技术架构

```
PDF → 文本抽取(pypdf) → 目录识别(内置TOC/AI视觉/文本解析)
                          ↓
              视觉分析(扫描页OCR/图表识别，带章节上下文)
                          ↓
              章节原文组装(文本层+OCR+图表描述)
                          ↓
              索引构建(切块+BM25) → 笔记生成(每章二次重写)
                                        ↓
                          对话创建(章节session+全书总览+RAG检索)
```

核心技术栈：

- **PySide6** — Qt6 跨平台 GUI
- **PyMuPDF (fitz)** — PDF 文本抽取与页面渲染
- **pypdf** — PDF 解析
- **Pillow** — 图片处理
- **Ollama** — 本地视觉模型（OCR/图表识别）
- **OpenAI 兼容 API** — 云端大模型（笔记生成/对话）
- **SQLite + JSONL** — 本地索引与缓存

---

## 📁 项目结构

```
BookDistiller/
├── main.py                    # 入口
├── src/
│   ├── config.py              # 配置管理 (settings.json)
│   ├── book_pipeline.py       # 蒸馏管线编排（6 阶段）
│   ├── pdf_reader.py          # PDF 文本抽取、页面分类
│   ├── chapter_detector.py    # 目录/章节识别（AI视觉/文本/正则/兜底）
│   ├── page_analysis.py       # 页面视觉分析（OCR/图表，支持并发）
│   ├── note_builder.py        # 章节笔记生成（二次重写）
│   ├── indexer.py             # 文本切块 + BM25 索引
│   ├── retriever.py           # 混合检索
│   ├── context_builder.py     # 对话上下文打包
│   ├── cache.py               # PDF/视觉/检索缓存
│   ├── chat.py                # 对话会话管理
│   ├── image_analysis.py      # 图片理解 API
│   └── gui/
│       ├── app.py             # 主窗口（批量蒸馏 + 对话）
│       ├── chat_widget.py     # AI 对话界面
│       ├── theme.py           # Light/Dark 主题
│       └── settings_dialog.py # 设置（模型配置/Prompt自定义）
└── output/{book_name}/        # 蒸馏产物
    ├── pages/                 # 页面渲染图片
    ├── chapters/              # 每章原文、视觉结果
    ├── notes/                 # 章节笔记、全书总览
    ├── index/                 # 切块、BM25 索引
    └── book.json              # 统一元数据
```

---

## 📄 License

MIT License — 自由使用、修改和分发。

---

<div align="center">

**把书籍变成知识，而不是把时间变成进度条。**

Made with Python + PySide6

</div>
