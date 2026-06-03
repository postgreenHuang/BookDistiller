"""
Chapter detection for text-layer and scanned PDFs.
Priority: PDF built-in TOC > AI vision TOC > regex headings > page fallback.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from src.pdf_reader import load_pages, slugify

LogCallback = Callable[[str], None]

# 目录页关键词
_TOC_KEYWORDS = re.compile(
    r"(目[录錄次]|contents|table\s+of\s+contents|目录|总目|详细目)",
    re.IGNORECASE,
)

# AI 视觉目录提取 Prompt
_TOC_VISION_PROMPT = (
    "这是一本书的目录页图片。请完整提取目录中所有条目，以 JSON 数组格式输出。\n\n"
    "每个条目包含 title（章节标题）和 page（页码，整数）。\n"
    "根据以下线索判断 level（层级深度，1=最顶层，2=子节，3=更深层，以此类推）：\n"
    "- 缩进：越靠右缩进的条目 level 越大\n"
    "- 编号格式：「第X章」「Chapter X」为 level 1；「X.X」「X.X.X」等小数编号的深度对应 level\n"
    "- 字体大小：大号粗体通常是 level 1，逐级缩小\n"
    "- 排版结构：丛书总序、前言、序言、附录、索引等为 level 1；正文正文章节为 level 1；正文下的子节为 level 2+\n\n"
    "输出格式：\n"
    '[{"title": "第一章 引言", "page": 1, "level": 1}, {"title": "1.1 背景", "page": 2, "level": 2}]\n\n'
    "要求：\n"
    "1. 必须完整提取所有条目，不要遗漏\n"
    "2. page 是目录中标注的页码（通常是印刷页码），不是 PDF 页码\n"
    "3. level 必须根据缩进、编号、字体大小等视觉线索判断，不要凭猜测\n"
    "4. 只输出 JSON 数组，不要输出其他内容"
)

_TOC_OCR_PROMPT = (
    "请逐行 OCR 这张书籍目录页。\n"
    "要求：\n"
    "1. 保留每一条目录项的标题、作者名、点线、省略号、括号中的页码或行尾页码。\n"
    "2. 不要总结，不要改写，不要补写不存在的内容。\n"
    "3. 如果有“目录”“附录”“前言”“编译序”等标题，也要原样保留。\n"
    "4. 只输出 OCR 文本。"
)


def _normalize_toc(toc: list[dict[str, Any]], page_count: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    entries: list[dict[str, Any]] = []
    for item in toc:
        title = str(item.get("title", "")).strip()
        page = int(item.get("page") or 0)
        level = int(item.get("level") or 1)
        if not title or page < 1 or page > page_count:
            continue
        key = (title, page)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"title": title, "page_start": page, "level": level})
    entries.sort(key=lambda x: (x["page_start"], x["level"]))
    return entries


def _find_toc_pages(pages: list[dict[str, Any]], max_pages: int = 10) -> list[int]:
    """从文本层找到目录页的页码列表。

    策略：在前 max_pages 页中查找包含目录关键词的页面。
    """
    toc_pages: list[int] = []
    for p in pages[:max_pages]:
        text = str(p.get("text", "")).strip()
        if not text:
            continue
        # 检查是否包含目录关键词
        # 目录页通常在文档开头，且包含多行带页码的内容
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        has_keyword = any(_TOC_KEYWORDS.search(line) for line in lines[:5])
        # 目录页特征：多行文本，很多行末尾有数字（页码）
        ends_with_number = sum(1 for l in lines if re.search(r"\d+\s*$", l))
        if has_keyword or (ends_with_number >= max(3, len(lines) // 3)):
            toc_pages.append(int(p.get("page", 0)))
    return toc_pages


# 视觉 AI 逐页判断是否目录页（极简 prompt，本地模型也能稳定回答）
_TOC_IS_TOC_PROMPT = (
    "这是书中的一页。它是不是目录页（包含章节列表和页码）？\n"
    "只回答 yes 或 no。"
)


def _vision_find_toc_pages(book_json_path: str | Path, vision_config: dict,
                            log_cb: LogCallback | None = None) -> list[int]:
    """全扫描 PDF 专用：逐页渲染前 15 页，让视觉 AI 判断是否是目录页。

    策略：
    - 跳过第 1 页（通常是封面）
    - 逐页渲染第 2-16 页，每页单独问"是目录吗？"（极简 prompt）
    - 找到连续 2 页目录后停止（目录通常不超过 3 页）
    - 本地模型单图 yes/no 判断比多图 JSON 提取可靠得多

    Returns:
        目录页的页码列表（1-indexed）。
    """
    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    book_dir = Path(book["paths"]["book_dir"])
    pdf_path = book.get("source_pdf", "")
    page_count = int(book.get("page_count") or 0)

    if not pdf_path or not Path(pdf_path).is_file():
        return []

    from src.page_analysis import render_page, DEFAULT_VISION_MAX_DIMENSION, DEFAULT_VISION_JPEG_QUALITY
    from src.image_analysis import _encode_image, _call_ollama, _call_cloud

    # 扫描范围：全书前 10%（至少 5 页，最多 50 页），跳过封面
    scan_end = min(max(6, page_count // 10 + 1), page_count + 1, 51)
    scan_pages = list(range(2, scan_end))

    if log_cb:
        log_cb(f"  全扫描 PDF，逐页探测目录 (p.{scan_pages[0]}-{scan_pages[-1]}, 全书 {page_count} 页)...")

    render_dir = book_dir / "pages" / "rendered"
    vision_type = vision_config.get("type", "ollama")
    model = vision_config.get("model", "")
    base_url = vision_config.get("url", "http://localhost:11434")
    api_key = vision_config.get("api_key", "")

    toc_pages: list[int] = []
    consecutive_non_toc = 0  # 连续非目录页计数

    for page_num in scan_pages:
        try:
            # 渲染单页
            image_path = render_page(
                pdf_path, page_num, str(render_dir),
                max_dimension=DEFAULT_VISION_MAX_DIMENSION,
                jpeg_quality=DEFAULT_VISION_JPEG_QUALITY,
            )
            image_b64 = _encode_image(str(image_path))

            # 单页 yes/no 判断
            try:
                if vision_type == "ollama":
                    raw_text, _, _ = _call_ollama(model, _TOC_IS_TOC_PROMPT, image_b64, base_url)
                else:
                    raw_text, _ = _call_cloud(model, _TOC_IS_TOC_PROMPT, image_b64, base_url, api_key)
            finally:
                del image_b64

            answer = raw_text.strip().lower()
            del raw_text

            is_toc = "yes" in answer or "是" in answer

            if is_toc:
                toc_pages.append(page_num)
                consecutive_non_toc = 0
                if log_cb:
                    log_cb(f"  p.{page_num}: ✓ 目录页")
            else:
                consecutive_non_toc += 1
                # 找到目录后，连续 4 页非目录才停止（目录可能被版权页等隔开）
                if consecutive_non_toc >= 4 and len(toc_pages) > 0:
                    break
                # 完全没找到目录时，连续 8 页非目录就放弃
                if consecutive_non_toc >= 8 and len(toc_pages) == 0:
                    break

        except Exception as exc:
            if log_cb:
                log_cb(f"  p.{page_num} 探测失败: {exc}")

    if log_cb:
        if toc_pages:
            log_cb(f"  发现目录页: p.{', p.'.join(str(p) for p in toc_pages)}")
        else:
            log_cb("  未找到目录页，将使用页段切分")

    return toc_pages


def _extract_toc_from_text(pages: list[dict[str, Any]], toc_page_nums: list[int]) -> list[dict[str, Any]]:
    """从目录页文本中尝试提取章节条目（纯文本解析，不调 AI）。"""
    entries: list[dict[str, Any]] = []
    for page in pages:
        if int(page.get("page", 0)) not in toc_page_nums:
            continue
        text = str(page.get("text", ""))
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 2:
                continue
            # 匹配行末带页码的条目：如 "第一章 引言 ... 15" 或 "3.2 方法 42"
            m = re.match(r"^(.+?)\s*[\.…··\-\s]*(\d+)\s*$", line)
            if m:
                title = m.group(1).strip()
                page_num = int(m.group(2))
                if title and page_num > 0 and len(title) <= 120:
                    entries.append({"title": title, "page_start": page_num, "level": 1})
    return entries


def _validate_entries(entries: list[dict[str, Any]], page_count: int,
                       log_cb: LogCallback | None = None) -> list[dict[str, Any]]:
    """校验和清洗目录条目：去异常、保序、限范围。

    过滤规则：
    - 页码 ≤ 0 或远超 page_count 的条目
    - 标题为空或过长的条目
    - 页码严格递减的条目（目录应递增）
    """
    if not entries:
        return entries

    valid: list[dict[str, Any]] = []
    removed = 0
    for entry in entries:
        page = int(entry.get("page_start", 0))
        title = str(entry.get("title", "")).strip()

        # 页码范围检查
        if page <= 0 or page > page_count + 5:
            removed += 1
            continue

        # 标题有效性
        if not title or len(title) > 200:
            removed += 1
            continue

        # 截断过大的页码（略超 page_count 可以容忍，_finalize 会 clamp）
        if page > page_count:
            entry["page_start"] = page_count

        valid.append(entry)

    # 按页码排序（目录应该递增）
    valid.sort(key=lambda x: x.get("page_start", 0))

    # 去重：相同页码只保留第一个
    seen_pages: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for entry in valid:
        page = int(entry.get("page_start", 0))
        if page not in seen_pages:
            seen_pages.add(page)
            deduped.append(entry)
        else:
            removed += 1

    if removed > 0 and log_cb:
        log_cb(f"  目录校验: 移除 {removed} 个异常条目，保留 {len(deduped)} 个")

    return deduped


def _apply_page_offset(entries: list[dict[str, Any]], toc_page_nums: list[int],
                       page_count: int, log_cb: LogCallback | None = None) -> list[dict[str, Any]]:
    """校正 AI 视觉提取的印刷页码 → PDF 页码偏移。

    AI 从目录页图片读到的是印刷页码（从正文第 1 页起算），
    但组装章节原文需要 PDF 页码（从第 1 页 = 封面起算）。
    偏移量 ≈ 目录页之后的页数 = last_toc_pdf_page + 1 - first_printed_page。

    例如：目录在 PDF p.3-4，第一条印刷页码为 1
    → offset = 4 + 1 - 1 = 4
    → 印刷 p.1 对应 PDF p.5
    """
    if not entries or not toc_page_nums:
        return entries

    last_toc_pdf_page = max(toc_page_nums)
    first_printed = min(e.get("page_start", 1) for e in entries if e.get("page_start", 0) > 0)

    # offset: pdf_page = printed_page + offset
    offset = last_toc_pdf_page + 1 - first_printed

    if offset <= 0:
        # 无偏移或负偏移，不需要校正
        return entries

    # 校验：校正后最后一页不能超过 PDF 总页数
    max_corrected = max(e.get("page_start", 0) for e in entries) + offset
    if max_corrected > page_count + 5:
        # 偏移过大，可能估算错误，保守不校正
        if log_cb:
            log_cb(f"  页码偏移校正跳过: 估算 offset={offset}，但校正后最大页码 {max_corrected} 超出 PDF {page_count} 页")
        return entries

    # 应用偏移
    for entry in entries:
        old_page = entry.get("page_start", 0)
        entry["page_start"] = old_page + offset
        # 保留原始印刷页码供参考
        entry["printed_page"] = old_page

    if log_cb:
        log_cb(f"  页码偏移校正: offset=+{offset} (印刷 p.{first_printed} → PDF p.{first_printed + offset})")

    return entries


def _entries_from_parsed_toc(parsed: Any) -> list[Any]:
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []
    entries = parsed.get("entries")
    if entries is None:
        entries = parsed.get("chapters")
    if entries is None:
        entries = parsed.get("toc")
    if entries is None:
        entries = parsed.get("contents")
    if entries is None:
        entries = parsed.get("items")
    return entries if isinstance(entries, list) else []


def _infer_level_from_title(title: str) -> int | None:
    """根据标题的编号格式推断层级，返回 None 表示无法判断。"""
    # "第X章" / "Chapter X" → level 1
    if re.match(r"^(第[一二三四五六七八九十百千\d]+[篇章节部分]|Chapter\s+\d+|Part\s+\d+)", title, re.IGNORECASE):
        return 1
    # "X.X.X" → depth = dots + 1
    m = re.match(r"^(\d+)(\.\d+)+", title)
    if m:
        return title.count(".") + 1
    # "X." 或 "X " 开头（单数字编号）→ level 1
    if re.match(r"^\d+[.\s]", title):
        return 1
    return None


def _normalize_ai_toc_entries(entries_raw: list[Any],
                              source_toc_page: int = 0,
                              order_base: int = 0) -> list[dict[str, Any]]:
    # 第一遍：识别被过滤的分组标题（page=null/0），记录它们的 level
    # 这些是"附录"、"第一部分"等无页码的分组标记
    filtered_levels: set[int] = set()
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        if not title:
            continue
        page_raw = entry.get("page_start", entry.get("pdf_page", entry.get("page", 0)))
        page_match = re.search(r"\d+", str(page_raw))
        page = int(page_match.group(0)) if page_match else 0
        try:
            level = int(entry.get("level", 1))
        except Exception:
            level = 1
        if page < 1:
            filtered_levels.add(level)

    # 第二遍：构建有效条目，修正因分组标题被过滤导致的层级错误
    entries: list[dict[str, Any]] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        page_raw = entry.get("page_start", entry.get("pdf_page", entry.get("page", 0)))
        page_match = re.search(r"\d+", str(page_raw))
        page = int(page_match.group(0)) if page_match else 0
        try:
            level = int(entry.get("level", 1))
        except Exception:
            level = 1

        # 跳过无页码的分组标题
        if not title or page < 1:
            continue

        # 如果这个条目的 level 等于被过滤的分组标题 level+1，
        # 说明它是"附录"之类的子条目，分组标题被过滤后应提升层级
        if level > 1 and (level - 1) in filtered_levels:
            level -= 1

        # 用编号规则校验/修正 AI 返回的 level
        inferred = _infer_level_from_title(title)
        if inferred is not None and inferred != level:
            level = inferred

        item = {
            "title": title,
            "page_start": page,
            "level": max(1, level),
            "_toc_order": order_base + len(entries),
        }
        printed_raw = entry.get("printed_page")
        printed_match = re.search(r"\d+", str(printed_raw)) if printed_raw is not None else None
        if printed_match:
            item["printed_page"] = int(printed_match.group(0))
        item["source_toc_page"] = int(entry.get("source_toc_page") or source_toc_page or 0)
        entries.append(item)
    return entries


def _vision_ocr_toc_pages(book_json_path: str | Path, toc_page_nums: list[int],
                          vision_config: dict, log_cb: LogCallback | None = None) -> list[dict[str, Any]]:
    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    book_dir = Path(book["paths"]["book_dir"])
    pdf_path = book.get("source_pdf", "")
    if not pdf_path or not Path(pdf_path).is_file():
        return []
    if not vision_config or not vision_config.get("model"):
        return []

    from src.page_analysis import render_page
    from src.image_analysis import _encode_image, _call_ollama, _call_cloud

    toc_cache_dir = book_dir / "cache" / "toc"
    toc_cache_dir.mkdir(parents=True, exist_ok=True)
    render_dir = book_dir / "pages" / "toc_rendered"
    vision_type = vision_config.get("type", "ollama")
    model = vision_config.get("model", "")
    base_url = vision_config.get("url", "http://localhost:11434")
    api_key = vision_config.get("api_key", "")

    results: list[dict[str, Any]] = []
    for page_num in toc_page_nums:
        image_path = render_page(
            pdf_path, page_num, str(render_dir),
            max_dimension=0, jpeg_quality=0,
        )
        image_b64 = _encode_image(str(image_path))
        try:
            if vision_type == "ollama":
                raw_text, _, _ = _call_ollama(model, _TOC_OCR_PROMPT, image_b64, base_url)
            else:
                raw_text, _ = _call_cloud(model, _TOC_OCR_PROMPT, image_b64, base_url, api_key)
        finally:
            del image_b64

        raw_text = raw_text.strip()
        raw_path = toc_cache_dir / f"page_{page_num:04d}_ocr.txt"
        raw_path.write_text(raw_text, encoding="utf-8")
        if not raw_text:
            raise RuntimeError(f"目录页 p.{page_num} OCR 为空，原始结果已保存: {raw_path}")
        results.append({"page": page_num, "text": raw_text, "path": str(raw_path)})
        if log_cb:
            log_cb(f"  目录页 p.{page_num}: OCR {len(raw_text)} 字")
    return results


def _structure_toc_with_provider(book_json_path: str | Path,
                                 toc_texts: list[dict[str, Any]],
                                 provider_config: dict,
                                 log_cb: LogCallback | None = None) -> list[dict[str, Any]]:
    if not toc_texts:
        return []
    if not provider_config or not provider_config.get("api_key"):
        return []

    from src.image_analysis import _parse_json_response
    from src.note_builder import _call_chat

    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    book_dir = Path(book["paths"]["book_dir"])
    page_count = int(book.get("page_count") or 0)
    toc_pages = [int(item["page"]) for item in toc_texts]
    toc_cache_dir = book_dir / "cache" / "toc"
    toc_cache_dir.mkdir(parents=True, exist_ok=True)

    joined = "\n\n".join(
        f"=== TOC PDF PAGE {item['page']} ===\n{item['text']}"
        for item in toc_texts
    )
    prompt = f"""
你是书籍目录结构化专家。下面是从一本扫描版 PDF 的目录页逐页 OCR 得到的文本。

任务：把这些目录文本合并，返回本工具用于切分章节的 JSON 数组。

硬性要求：
1. 只返回 JSON，不要解释。
2. 每个目录条目输出一个对象：{{"title": "...", "page_start": 12, "level": 1, "printed_page": 12, "source_toc_page": 8}}。
3. title 保留目录中的原始标题，包含“从书总序”“编译序”“前言”“附录”等，不要只保留正文编号章节。
4. page_start 是最终用于 PDF 拼合的页码。优先使用目录中标注的页码；只有 OCR 文本明确显示需要换算 PDF 页码时才换算，不要凭空猜偏移。
5. printed_page 保留目录中看到的页码数字。
6. level: 主章节/前言/附录用 1；子条目用 2 或更深。
7. 不要编造目录中没有的条目；不要漏掉有页码的目录条目。
8. 页码必须是 1 到 {page_count} 之间的整数。

PDF 总页数: {page_count}
探测到的目录 PDF 页: {toc_pages}

OCR 文本：
{joined}
""".strip()

    raw = _call_chat(
        provider_config,
        [
            {"role": "system", "content": "你只输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ],
        timeout=180,
    )
    raw_path = toc_cache_dir / "cloud_structure_raw.txt"
    raw_path.write_text(raw, encoding="utf-8")
    parsed = _parse_json_response(raw)
    entries = _normalize_ai_toc_entries(_entries_from_parsed_toc(parsed))
    if not entries:
        raise RuntimeError(f"云端书籍整合模型未返回有效章节 JSON，原始返回已保存: {raw_path}")
    if log_cb:
        log_cb(f"  云端目录结构化: {len(entries)} 条，原始返回已保存: {raw_path}")
    return entries


def _vision_extract_toc(book_json_path: str | Path, toc_page_nums: list[int],
                        vision_config: dict, log_cb: LogCallback | None = None) -> list[dict[str, Any]]:
    """用视觉 AI 识别目录页图片，提取章节列表。

    Args:
        book_json_path: book.json 路径
        toc_page_nums: 目录页的页码列表
        vision_config: 视觉模型配置
        log_cb: 日志回调

    Returns:
        章节条目列表 [{"title": ..., "page_start": ..., "level": ...}]
    """
    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    book_dir = Path(book["paths"]["book_dir"])
    pdf_path = book.get("source_pdf", "")
    page_count = int(book.get("page_count") or 0)

    if not pdf_path or not Path(pdf_path).is_file():
        return []
    if not vision_config or not vision_config.get("model"):
        return []

    from src.page_analysis import render_page
    from src.image_analysis import _encode_image, _call_ollama, _call_cloud, _parse_json_response

    all_entries: list[dict[str, Any]] = []
    toc_cache_dir = book_dir / "cache" / "toc"
    toc_cache_dir.mkdir(parents=True, exist_ok=True)

    for page_num in toc_page_nums:
        try:
            # 渲染目录页为图片
            render_dir = book_dir / "pages" / "rendered"
            image_path = render_page(
                pdf_path, page_num, str(render_dir),
                max_dimension=1600, jpeg_quality=92,
            )
            image_b64 = _encode_image(str(image_path))
            page_entries: list[dict[str, Any]] = []

            # 调用视觉模型
            vision_type = vision_config.get("type", "ollama")
            model = vision_config.get("model", "")
            base_url = vision_config.get("url", "http://localhost:11434")
            api_key = vision_config.get("api_key", "")

            try:
                if vision_type == "ollama":
                    raw_text, tokens, _ = _call_ollama(model, _TOC_VISION_PROMPT, image_b64, base_url)
                else:
                    raw_text, tokens = _call_cloud(model, _TOC_VISION_PROMPT, image_b64, base_url, api_key)
            finally:
                del image_b64

            raw_path = toc_cache_dir / f"page_{page_num:04d}_raw.txt"
            raw_path.write_text(raw_text, encoding="utf-8")

            # 解析 AI 返回的 JSON
            parsed = _parse_json_response(raw_text)
            del raw_text

            entries_raw = _entries_from_parsed_toc(parsed)
            page_entries.extend(_normalize_ai_toc_entries(
                entries_raw,
                source_toc_page=page_num,
                order_base=len(all_entries),
            ))

            if not page_entries:
                raise RuntimeError(f"目录页 p.{page_num} 未抽取出有效条目，原始返回已保存: {raw_path}")

            all_entries.extend(page_entries)
            if log_cb:
                log_cb(f"  目录页 p.{page_num}: 抽取 {len(page_entries)} 条")

        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    # 去重
    seen: set[tuple[str, int]] = set()
    unique: list[dict[str, Any]] = []
    for e in all_entries:
        key = (e["title"], e["page_start"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    unique.sort(key=lambda x: (x["page_start"], x.get("_toc_order", 0)))
    for e in unique:
        e.pop("_toc_order", None)
    return unique


def _detect_headings_from_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"^("
        r"chapter\s+\d+"
        r"|chapter\s+[ivxlcdm]+"
        r"|part\s+\d+"
        r"|part\s+[ivxlcdm]+"
        r"|第[一二三四五六七八九十百千万零〇\d]+[章节部分篇]"
        r"|\d+[\.、]\s*\S"
        r"|[一二三四五六七八九十]+[、\.]\s*\S"
        r")",
        re.IGNORECASE,
    )
    entries: list[dict[str, Any]] = []
    for page in pages:
        text = str(page.get("text", ""))
        for raw in text.splitlines()[:18]:
            line = raw.strip()
            if 4 <= len(line) <= 120 and pattern.search(line):
                entries.append({
                    "title": line,
                    "page_start": int(page["page"]),
                    "level": 1,
                })
                break
    deduped: list[dict[str, Any]] = []
    seen_pages: set[int] = set()
    for entry in entries:
        if entry["page_start"] in seen_pages:
            continue
        seen_pages.add(entry["page_start"])
        deduped.append(entry)
    return deduped


def _fallback_chapters(page_count: int, pages_per_part: int = 30) -> list[dict[str, Any]]:
    if page_count <= 0:
        return []
    if page_count <= pages_per_part:
        return [{"title": "整书", "page_start": 1, "level": 1}]
    chapters = []
    for start in range(1, page_count + 1, pages_per_part):
        end = min(start + pages_per_part - 1, page_count)
        chapters.append({
            "title": f"页段 {start}-{end}",
            "page_start": start,
            "level": 1,
        })
    return chapters


def _finalize(entries: list[dict[str, Any]], page_count: int, book_id: str) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    entries = sorted(entries, key=lambda x: x["page_start"])
    for idx, entry in enumerate(entries):
        start = max(1, int(entry["page_start"]))
        next_start = int(entries[idx + 1]["page_start"]) if idx + 1 < len(entries) else page_count + 1
        end = max(start, min(page_count, next_start - 1))
        title = str(entry["title"]).strip() or f"Chapter {idx + 1}"
        chapters.append({
            "chapter_id": f"ch{idx + 1:03d}",
            "book_id": book_id,
            "title": title,
            "page_start": start,
            "page_end": end,
            "level": int(entry.get("level") or 1),
            "slug": slugify(title, f"ch{idx + 1:03d}"),
        })
    return chapters


def detect_chapters(book_json_path: str | Path,
                    log_cb: LogCallback | None = None,
                    vision_config: dict | None = None,
                    provider_config: dict | None = None) -> list[dict[str, Any]]:
    """Detect chapters from a book.

    Priority: PDF built-in TOC > AI vision TOC > text TOC > regex headings > page fallback.

    Args:
        book_json_path: Path to book.json.
        log_cb: Log callback.
        vision_config: Vision model config for AI-based TOC page OCR.
        provider_config: Cloud book aggregation model config for TOC structuring.
    """
    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    pages = load_pages(book["paths"]["pages_path"])
    page_count = int(book.get("page_count") or len(pages))

    t0 = time.time()
    method = ""

    # 优先级 1: PDF 内置目录
    entries = _normalize_toc(book.get("toc") or [], page_count)
    if entries:
        method = "PDF 内置目录"

    # 优先级 2: AI 视觉识别目录页
    toc_page_nums = []  # 记录用于偏移计算
    if not entries and vision_config and vision_config.get("model"):
        # 先从文本层找目录页
        toc_page_nums = _find_toc_pages(pages)
        if not toc_page_nums:
            # 全扫描 PDF（无文本层）：直接用视觉 AI 扫描前几页找目录页
            text_pages = sum(1 for p in pages if p.get("has_text"))
            if text_pages <= max(3, len(pages) // 10):
                toc_page_nums = _vision_find_toc_pages(book_json_path, vision_config, log_cb=log_cb)
        if toc_page_nums:
            if log_cb:
                log_cb(f"尝试 AI 视觉识别目录: {len(toc_page_nums)} 个目录页 (p.{', p.'.join(str(p) for p in toc_page_nums)})")
            entries = _vision_extract_toc(book_json_path, toc_page_nums, vision_config, log_cb)
            if entries:
                method = "AI 视觉识别目录"
                entries = _apply_page_offset(entries, toc_page_nums, page_count, log_cb=log_cb)
            else:
                raise RuntimeError(
                    f"已探测到目录页 p.{', p.'.join(str(p) for p in toc_page_nums)}，"
                    "但 AI 未能抽取出有效目录条目；已停止，避免回退为 30 页一章。"
                )

    # 优先级 3: 文本层目录解析
    if not entries:
        toc_page_nums = _find_toc_pages(pages)
        if toc_page_nums:
            entries = _extract_toc_from_text(pages, toc_page_nums)
            if entries:
                method = "文本目录解析"

    # 优先级 4: 正文标题正则匹配
    if not entries:
        entries = _detect_headings_from_pages(pages)
        if entries:
            method = "正文标题正则匹配"

    # 优先级 5: 按页段自动切分
    if not entries:
        entries = _fallback_chapters(page_count)
        method = "按页段自动切分"

    # 校验和清洗目录条目
    entries = _validate_entries(entries, page_count, log_cb=log_cb)

    chapters = _finalize(entries, page_count, book["book_id"])

    if log_cb:
        elapsed = time.time() - t0
        log_cb(f"章节切分完成 (方法: {method}): 共 {len(chapters)} 章，耗时 {elapsed:.1f}s")
        for ch in chapters[:5]:
            log_cb(f"  {ch['chapter_id']}: {ch['title']} (p.{ch['page_start']}-{ch['page_end']})")
        if len(chapters) > 5:
            log_cb(f"  ... 共 {len(chapters)} 章")

    chapters_dir = Path(book["paths"]["book_dir"]) / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    for chapter in chapters:
        text_parts = []
        for page in pages:
            page_no = int(page["page"])
            if chapter["page_start"] <= page_no <= chapter["page_end"]:
                text = str(page.get("text", "")).strip()
                visual = page.get("visual_analysis") or {}
                diagrams_desc = str(visual.get("diagrams", "")).strip()
                layout_desc = str(visual.get("layout", "")).strip()

                page_header = f"\n\n<!-- page:{page_no} -->"
                parts_for_page = []

                if text:
                    parts_for_page.append(text)
                # 拼入视觉分析结果：图表描述、布局说明
                if diagrams_desc and diagrams_desc != "无":
                    parts_for_page.append(f"[图表/视觉内容: {diagrams_desc}]")
                if layout_desc and layout_desc and text:  # 布局仅在有文本时补充
                    pass  # 布局信息太碎，不拼入原文

                if parts_for_page:
                    text_parts.append(f"{page_header}\n" + "\n".join(parts_for_page))

        chapter_dir = chapters_dir / chapter["chapter_id"]
        chapter_dir.mkdir(parents=True, exist_ok=True)
        text_path = chapter_dir / "text.md"
        text_path.write_text("".join(text_parts).strip() + "\n", encoding="utf-8")
        chapter["text_path"] = str(text_path)

    chapters_path = chapters_dir / "chapters.json"
    chapters_path.write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8")
    book["chapters"] = chapters
    book["paths"]["chapters_path"] = str(chapters_path)
    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return chapters
