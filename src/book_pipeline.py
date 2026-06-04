"""
Book distillation pipeline: PDF → chapters → visual analysis → index → notes → sessions.

Pipeline order (optimized for metadata completeness):
  1. PDF text extraction (read_pdf)
  2. Chapter detection: PDF TOC → AI vision TOC → text/regex → fallback
     - AI vision TOC uses the image model to read table-of-contents pages
     - Chapters are established BEFORE visual analysis so OCR can use chapter context
  3. Visual page analysis (OCR/diagrams) — now knows which chapter each page belongs to
  4. Chapter text refresh (merge OCR + diagrams into chapter files)
  5. Index building (BM25 chunks)
  6. Note generation (force=True, always regenerate)
  7. Session creation (book folder + chapter conversations)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Any

from src.cache import get_cached_book, has_valid_chapters, has_valid_index, load_json
from src.chapter_detector import detect_chapters
from src.context_builder import build_context
from src.indexer import build_index
from src.pdf_reader import read_pdf


ProgressCallback = Callable[[str, float], None]
LogCallback = Callable[[str], None]


def _vision_prompts() -> dict[str, str]:
    """Load vision prompts from settings defaults."""
    from src.config import Settings
    s = Settings()
    return {
        "single": s.vision_prompt_single,
        "ocr": s.vision_prompt_ocr,
        "diagram": s.vision_prompt_diagram,
        "title": s.vision_prompt_title,
    }


def _get_vision_concurrent() -> int:
    """Read vision concurrency from saved settings, fallback to default."""
    try:
        from src.config import load_settings
        return load_settings().vision_concurrent
    except Exception:
        return 1


def _get_toc_start_page() -> int:
    """Read TOC start page from saved settings."""
    try:
        from src.config import load_settings
        return load_settings().toc_start_page
    except Exception:
        return 1
    """Read vision scale percent from saved settings, convert to max_dimension.
    Returns 0=no scaling. For percent mode, uses the PDF's native 200 DPI render
    and scales proportionally."""
    try:
        from src.config import load_settings
        pct = load_settings().vision_scale_percent
        if pct <= 0 or pct >= 100:
            return 0
        # 200 DPI A4 page ≈ 1650×2340 pixels; scale by percent
        # Use the longer side as reference
        return int(2340 * pct / 100)
    except Exception:
        return 0


def _refresh_chapter_texts(book_json_path: str | Path,
                           log_cb: LogCallback | None = None) -> None:
    """刷新所有章节的 text.md 文件，确保包含最新的 OCR/视觉分析结果。

    当章节切分命中缓存但 OCR 是新完成的时，需要重写章节原文。
    """
    from src.chapter_detector import load_pages
    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    chapters = book.get("chapters") or []
    if not chapters:
        return
    pages = load_pages(book["paths"]["pages_path"])
    book_dir = Path(book["paths"]["book_dir"])
    updated = 0

    def visual_cache_text(page_no: int) -> str:
        cache_root = book_dir / "cache" / "visual"
        if not cache_root.is_dir():
            return ""
        candidates = sorted(cache_root.glob(f"page_{page_no:04d}_*/result.json"))
        for path in reversed(candidates):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            text = str(data.get("text", "")).strip()
            if text:
                return text
        return ""

    for chapter in chapters:
        text_path = Path(chapter.get("text_path", ""))
        if not text_path:
            continue
        text_parts = []
        for page in pages:
            page_no = int(page["page"])
            if chapter["page_start"] <= page_no <= chapter["page_end"]:
                text = visual_cache_text(page_no) or str(page.get("text", "")).strip()
                visual = page.get("visual_analysis") or {}
                diagrams_desc = str(visual.get("diagrams", "")).strip()
                page_header = f"\n\n<!-- page:{page_no} -->"
                parts_for_page = []
                if text:
                    parts_for_page.append(text)
                if diagrams_desc and diagrams_desc != "无":
                    parts_for_page.append(f"[图表/视觉内容: {diagrams_desc}]")
                if parts_for_page:
                    text_parts.append(f"{page_header}\n" + "\n".join(parts_for_page))

        new_content = "".join(text_parts).strip() + "\n"
        # 只在内容有变化时写入
        if text_path.is_file():
            old_content = text_path.read_text(encoding="utf-8")
            if old_content != new_content:
                text_path.write_text(new_content, encoding="utf-8")
                updated += 1
        else:
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(new_content, encoding="utf-8")
            updated += 1

    if updated and log_cb:
        log_cb(f"  章节原文刷新: {updated} 个文件因 OCR/视觉结果更新而重写")


def _chapter_tree(chapters: list[dict], limit: int = 220) -> str:
    lines = ["目录树:"]
    for idx, chapter in enumerate(chapters[:limit], 1):
        level = max(1, int(chapter.get("level") or 1))
        prefix = "  " * (level - 1) + ("└─ " if level > 1 else "├─ ")
        lines.append(
            f"{prefix}{idx:03d}. {chapter.get('title', '')} "
            f"(p.{chapter.get('page_start')}-{chapter.get('page_end')})"
        )
    if len(chapters) > limit:
        lines.append(f"... 还有 {len(chapters) - limit} 个目录节点")
    return "\n".join(lines)


def _build_chapter_page_map(chapters: list[dict]) -> dict[int, dict]:
    """构建 page_num → chapter_info 的映射，用于视觉分析时传递章节上下文。"""
    page_map: dict[int, dict] = {}
    for chapter in chapters:
        ch_info = {
            "chapter_id": chapter.get("chapter_id", ""),
            "title": chapter.get("title", ""),
        }
        for page_no in range(chapter.get("page_start", 1), chapter.get("page_end", 0) + 1):
            page_map[page_no] = ch_info
    return page_map


def run_book_pipeline(pdf_path: str | Path, output_dir: str | Path,
                      progress_cb: ProgressCallback | None = None,
                      log_cb: LogCallback | None = None,
                      sample_query: str = "music sound synthesis computer",
                      create_sessions: bool = False,
                      provider_config: dict | None = None,
                      vision_config: dict | None = None,
                      output_language: str = "中文",
                      distill_prompt: str = "",
                      session_granularity: str = "level2",
                      toc_start_page: int = 0) -> dict[str, Any]:
    def progress(label: str, value: float):
        if progress_cb:
            progress_cb(label, value)

    pipeline_t0 = time.time()

    if log_cb:
        pdf_name = Path(pdf_path).name
        log_cb(f"═══ 开始蒸馏: {pdf_name} ═══")

    # ── 阶段 1: PDF 文本抽取 ──
    progress("PDF 缓存检查", 0.03)
    book, pdf_hash = get_cached_book(pdf_path, output_dir)
    cache_hits: list[str] = []
    if book:
        cache_hits.append("pdf")
        progress("跳过 PDF 文本抽取", 0.10)
        if log_cb:
            log_cb("PDF 文本抽取: 命中缓存，跳过")
    else:
        progress("PDF 文本抽取", 0.05)
        t0 = time.time()
        book = read_pdf(pdf_path, output_dir, pdf_sha256=pdf_hash, log_cb=log_cb)
        if log_cb:
            log_cb(f"  文本抽取阶段耗时 {time.time() - t0:.1f}s")
    book_json_path = Path(book["paths"]["book_dir"]) / "book.json"

    # ── 阶段 2: 章节切分（优先于视觉分析） ──
    # 先确定目录结构，这样后续视觉分析可以带着章节信息做 OCR
    if has_valid_chapters(book):
        cache_hits.append("chapters")
        progress("跳过章节切分", 0.20)
        chapters = book.get("chapters") or []
        if log_cb:
            log_cb(f"章节切分: 命中缓存，{len(chapters)} 章")
    else:
        progress("章节切分", 0.12)
        chapters = detect_chapters(
            book_json_path,
            log_cb=log_cb,
            vision_config=vision_config,
            provider_config=provider_config,
            toc_start_page=toc_start_page if toc_start_page > 0 else _get_toc_start_page(),
        )
        book = load_json(book_json_path) or book
    if log_cb:
        log_cb(_chapter_tree(chapters))

    # 构建页码→章节映射，传递给视觉分析
    chapter_page_map = _build_chapter_page_map(chapters)

    # ── 阶段 3: 视觉页面分析（OCR/图表识别，带章节上下文） ──
    page_types = book.get("page_types") or {}
    needs_visual_count = sum(page_types.get(t, 0) for t in ("needs_ocr", "is_blank", "is_cover"))

    if needs_visual_count > 0 and vision_config and vision_config.get("model"):
        progress("视觉页面分析", 0.25)
        if log_cb:
            log_cb(f"视觉页面分析: {needs_visual_count} 页需要处理（已获取 {len(chapters)} 个章节信息）")
        from src.page_analysis import analyze_book_pages
        visual_stats = analyze_book_pages(
            book_json_path,
            vision_config=vision_config,
            prompts={
                "single": _vision_prompts().get("single", ""),
                "ocr": _vision_prompts().get("ocr", ""),
                "_version": "v2-fullres",
            },
            progress_cb=lambda label, value: progress(label, 0.25 + 0.25 * value),
            log_cb=log_cb,
            max_concurrent=_get_vision_concurrent(),
            chapter_page_map=chapter_page_map,
            max_dimension=_get_vision_max_dimension(),
        )
        book = load_json(book_json_path) or book
        cache_hits.extend(["visual"] * visual_stats.get("cached", 0))
        if log_cb:
            log_cb(f"  视觉分析完成: 处理 {visual_stats.get('processed', 0)} 页，缓存命中 {visual_stats.get('cached', 0)} 页")
    elif needs_visual_count > 0 and (not vision_config or not vision_config.get("model")):
        if log_cb:
            log_cb(f"提示: {needs_visual_count} 页需要视觉分析（扫描/图表），但未配置图片识别模型，这些页面将被跳过")
    else:
        progress("跳过视觉分析", 0.25)
        if log_cb:
            log_cb("视觉页面分析: 无需处理，跳过")

    if int(book.get("text_page_count") or 0) == 0 and needs_visual_count > 0:
        if log_cb:
            log_cb("警告: 视觉分析后仍无文本，尝试继续处理")

    # ── 阶段 3.5: 刷新章节原文（合并 OCR + 视觉分析结果） ──
    _refresh_chapter_texts(book_json_path, log_cb=log_cb)
    book = load_json(book_json_path) or book
    chapters = book.get("chapters") or chapters

    # ── 阶段 4: 索引构建 ──
    if has_valid_index(book):
        cache_hits.append("index")
        progress("跳过检索索引构建", 0.70)
        index = book.get("index") or {}
        if log_cb:
            log_cb(f"索引构建: 命中缓存，{index.get('chunk_count', 0)} 段")
    else:
        progress("构建检索索引", 0.60)
        index = build_index(book_json_path, embedding_provider=provider_config, log_cb=log_cb)
        book = load_json(book_json_path) or book

    # ── 阶段 5: 检索冒烟验证 ──
    progress("检索冒烟验证", 0.85)
    smoke = build_context(book_json_path, sample_query, top_k=5, max_chars=5000)
    smoke_path = Path(book["paths"]["book_dir"]) / "index" / "retrieval_smoke.json"
    smoke_path.write_text(json.dumps(smoke, ensure_ascii=False, indent=2), encoding="utf-8")
    if log_cb:
        hits = len(smoke.get("hits") or [])
        log_cb(f"检索冒烟验证: 查询「{sample_query}」命中 {hits} 段")

    # ── 阶段 6: 章节笔记生成（每次强制重生成） ──
    note_stats = {"generated": 0, "skipped": 0}
    if provider_config and provider_config.get("api_key"):
        progress("生成章节重构讲解", 0.88)
        if log_cb:
            log_cb("开始生成章节重构讲解...")
        from src.note_builder import generate_notes

        def note_progress(cur: int, total: int, title: str):
            progress(f"生成章节重构讲解 {cur}/{total}: {title}", 0.88 + 0.08 * cur / max(1, total))

        note_stats = generate_notes(
            book_json_path,
            provider_config,
            output_language,
            distill_prompt,
            progress_cb=note_progress,
            log_cb=log_cb,
            force=False,  # 智能续跑：跳过已有笔记，只重跑缺失/失败的
            granularity=session_granularity,
        )
        book = load_json(book_json_path) or book
    elif log_cb:
        log_cb("未生成章节重构讲解：未配置可用的云端书籍整合模型，章节对话将退回原文预览。")

    session_count = 0
    if create_sessions:
        progress("创建书籍对话", 0.96)
        from src.chat import create_book_sessions
        session_ids = create_book_sessions(
            book_json_path,
            provider_config,
            session_granularity=session_granularity,
        )
        session_count = len(session_ids)

    progress("完成", 1.0)
    return {
        "book_json_path": str(book_json_path),
        "book_dir": book["paths"]["book_dir"],
        "title": book.get("title", ""),
        "page_count": book.get("page_count", 0),
        "text_page_count": book.get("text_page_count", 0),
        "chapter_count": len(chapters),
        "chunk_count": index.get("chunk_count", 0),
        "smoke_hits": len(smoke.get("hits") or []),
        "cache_hits": cache_hits,
        "session_count": session_count,
        "notes_generated": note_stats.get("generated", 0),
        "notes_skipped": note_stats.get("skipped", 0),
        "notes_target_chapters": note_stats.get("target_chapters", 0),
        "total_elapsed": time.time() - pipeline_t0,
    }
