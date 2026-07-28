"""
Generate chapter notes and book overview with the cloud aggregation provider.
Supports concurrent API calls and smart resume (skip existing notes).
"""

from __future__ import annotations

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import requests

from src.config import RICH_TEXT_FORMATTING_PROMPT
from src.paths import load_book, save_book


ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]

# 笔记生成并发数（云端 API 天然支持并行）
NOTE_CONCURRENCY = 8

# 瞬时错误重试：连接被重置 / 超时 / 服务端 5xx / 429 时退避重试，
# 避免 8 路并发偶发掉线（如 Windows ConnectionResetError 10054）导致整章笔记失败
_CHAT_MAX_RETRIES = 3
_CHAT_RETRY_BASE_DELAY = 2.0  # 秒，指数退避基数

_RETRYABLE_NETWORK_EXC = (
    requests.exceptions.ConnectionError,        # 含 ConnectionResetError(10054)、连接被中止等
    requests.exceptions.Timeout,                # 连接/读取超时
    requests.exceptions.ChunkedEncodingError,   # 读取响应途中连接被掐断
)


def _is_retryable_chat_error(exc: Exception) -> bool:
    """连接级错误、超时、5xx、429 视为可重试；4xx（鉴权/参数错误等）不重试。"""
    if isinstance(exc, _RETRYABLE_NETWORK_EXC):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        if resp is not None:
            code = resp.status_code
            return code == 429 or 500 <= code < 600
    return False


def _chat_retry_delay(attempt: int, exc: Exception) -> float:
    """指数退避 + 抖动（8 路并发同时重试会错峰）；429 优先尊重 Retry-After。"""
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            if retry_after:
                try:
                    return max(1.0, float(retry_after))
                except ValueError:
                    pass  # HTTP-date 形式，忽略后走指数退避
    return _CHAT_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1.0)


def _clean_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if "\n" in key or "\r" in key or '"' in key or "model" in key.lower():
        raise RuntimeError("API Key 配置不正确：请只填写单行 Key，不要粘贴 JSON 配置片段")
    return key


def _call_chat(provider_config: dict, messages: list[dict],
               timeout: int = 180, max_tokens: int = 8192,
               log_cb: LogCallback | None = None) -> str:
    base_url = provider_config.get("base_url", "").rstrip("/")
    api_key = _clean_api_key(provider_config.get("api_key", ""))
    model = provider_config.get("model", "")
    if not base_url or not api_key or not model:
        raise RuntimeError("请先配置可用的云端书籍整合模型 URL、Key 和模型名")

    url = base_url + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}

    last_exc: Exception | None = None
    for attempt in range(_CHAT_MAX_RETRIES + 1):
        resp = None
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            last_exc = exc
            if attempt >= _CHAT_MAX_RETRIES or not _is_retryable_chat_error(exc):
                raise
            delay = _chat_retry_delay(attempt, exc)
            if log_cb:
                log_cb(
                    f"⚠️ {model} 请求瞬时失败（第 {attempt + 1}/{_CHAT_MAX_RETRIES + 1} 次），"
                    f"{delay:.1f}s 后重试: {exc}"
                )
            time.sleep(delay)
        finally:
            if resp is not None:
                resp.close()
    if last_exc:  # 理论上不可达：循环内必会 return 或 raise
        raise last_exc
    raise RuntimeError("请求失败且无异常信息")


def _toc_text(chapters: list[dict], limit: int = 80) -> str:
    lines = []
    for i, chapter in enumerate(chapters[:limit], 1):
        lines.append(
            f"{i:03d}. {chapter.get('title', '')} "
            f"(p.{chapter.get('page_start')}-{chapter.get('page_end')})"
        )
    if len(chapters) > limit:
        lines.append(f"... 另有 {len(chapters) - limit} 个目录节点")
    return "\n".join(lines)


def _nearby_chapters(chapters: list[dict], idx: int, radius: int = 2) -> str:
    start = max(0, idx - radius)
    end = min(len(chapters), idx + radius + 1)
    lines = []
    for i in range(start, end):
        marker = "当前" if i == idx else "相邻"
        ch = chapters[i]
        lines.append(f"{marker}: {i + 1:03d}. {ch.get('title', '')} (p.{ch.get('page_start')}-{ch.get('page_end')})")
    return "\n".join(lines)


def _chapter_text(chapter: dict, max_chars: int = 60000) -> str:
    """读取章节原文，用于发给 AI 生成笔记。

    max_chars 默认 60000（约 15000-20000 tokens），对大多数章节够用。
    超长章节截取前 60000 字符并提示。
    """
    path = Path(chapter.get("text_path", ""))
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[本章原文较长，已截取前部分。AI 应重点覆盖前半部分内容，但不要遗漏核心概念。]"
    return text


def _find_chapter_index(chapters: list[dict], chapter: dict) -> int:
    """查找 chapter 在 chapters 列表中的索引。"""
    for i, ch in enumerate(chapters):
        if ch.get("chapter_id") == chapter.get("chapter_id"):
            return i
    return 0


def _group_prompt(book: dict, chapters: list[dict], group: dict,
                  output_language: str, prompt_template: str) -> list[dict]:
    """为合并章节组构建 prompt，拼接所有子章节原文。"""
    chs = group.get("chapters") or []
    title = group.get("title", "")
    # 拼接所有子章节原文
    combined_parts = []
    for ch in chs:
        ch_title = ch.get("title", "")
        ch_text = _chapter_text(ch)
        combined_parts.append(f"## {ch_title}\n\n{ch_text}")
    combined_text = "\n\n---\n\n".join(combined_parts)
    if len(combined_text) > 60000:
        combined_text = combined_text[:60000] + "\n\n[合并章节原文较长，已截取前部分。]"

    page_ranges = ", ".join(f"p.{ch.get('page_start')}-{ch.get('page_end')}" for ch in chs)
    sub_titles = "、".join(ch.get("title", "") for ch in chs[:10])
    if len(chs) > 10:
        sub_titles += f"等 {len(chs)} 个小节"

    system = (
        f"无论原书是什么语言，你必须使用{output_language}输出。"
        "保留必要专有名词原文，并用括号补充中文解释或译名。"
        "你不是翻译器，而是帮助学习者理解整本书结构和当前章节的读书导师。\n\n"
        f"{prompt_template}"
        f"{RICH_TEXT_FORMATTING_PROMPT}"
    )
    user = (
        f"书名: {book.get('title', '')}\n"
        f"作者: {book.get('author', '') or '未知'}\n\n"
        f"全书目录:\n{_toc_text(chapters)}\n\n"
        f"当前章节组: {title}\n"
        f"包含 {len(chs)} 个子节: {sub_titles}\n"
        f"页码范围: {page_ranges}\n\n"
        "请为这组章节生成一份合并的重构讲解，覆盖所有子节的核心概念和知识要点。\n\n"
        "在笔记最后，请额外输出一个「关键概念」列表，格式如下：\n"
        "## 关键概念\n"
        "- **概念名**: 一句话解释。首次出现于本章。\n"
        "- **概念名**: 一句话解释。首次出现于本章。与 XX 概念相关。\n\n"
        f"合并章节原文:\n{combined_text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _chapter_prompt(book: dict, chapters: list[dict], idx: int,
                    output_language: str, prompt_template: str) -> list[dict]:
    chapter = chapters[idx]
    system = (
        f"无论原书是什么语言，你必须使用{output_language}输出。"
        "保留必要专有名词原文，并用括号补充中文解释或译名。"
        "你不是翻译器，而是帮助学习者理解整本书结构和当前章节的读书导师。\n\n"
        f"{prompt_template}"
        f"{RICH_TEXT_FORMATTING_PROMPT}"
    )
    user = (
        f"书名: {book.get('title', '')}\n"
        f"作者: {book.get('author', '') or '未知'}\n\n"
        f"全书目录:\n{_toc_text(chapters)}\n\n"
        f"当前章节及相邻关系:\n{_nearby_chapters(chapters, idx)}\n\n"
        f"当前章节标题: {chapter.get('title', '')}\n"
        f"页码范围: p.{chapter.get('page_start')}-{chapter.get('page_end')}\n\n"
        "请生成一份重构讲解，必须覆盖：概念、模块/结构、前后篇章关系、知识重点、深入浅出的解释、可追问问题。\n\n"
        "在笔记最后，请额外输出一个「关键概念」列表，格式如下：\n"
        "## 关键概念\n"
        "- **概念名**: 一句话解释。首次出现于本章。\n"
        "- **概念名**: 一句话解释。首次出现于本章。与 XX 概念相关。\n\n"
        f"当前章节原文:\n{_chapter_text(chapter)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _overview_prompt(book: dict, chapters: list[dict], output_language: str) -> list[dict]:
    chapter_summaries = []
    seen_note_paths: set[str] = set()
    for chapter in chapters:
        note_path = Path(chapter.get("note_path", ""))
        note_key = str(note_path)
        if note_key in seen_note_paths:
            continue
        if note_path.is_file():
            seen_note_paths.add(note_key)
            text = note_path.read_text(encoding="utf-8").strip()
            chapter_summaries.append(f"## {chapter.get('title', '')}\n{text[:1200]}")
    system = (
        f"无论原书是什么语言，你必须使用{output_language}输出。"
        "你是能读完整本书并讲清楚知识结构的学习导师。"
    )
    system += RICH_TEXT_FORMATTING_PROMPT
    user = (
        f"书名: {book.get('title', '')}\n"
        f"作者: {book.get('author', '') or '未知'}\n\n"
        f"全书目录:\n{_toc_text(chapters, limit=160)}\n\n"
        "请生成全书总览，覆盖：本书主线、核心模块、章节之间关系、关键概念地图、学习路线、适合继续追问的问题。\n\n"
        "格式要求：\n"
        "- 可以使用 Mermaid 图表表达模块关系，但节点标签必须是纯文本（不要 HTML 标签、内联样式）\n"
        "- 不要使用 <span>、<div> 等 HTML 标签加样式\n"
        "- 优先使用 Markdown 列表、表格表达结构关系\n\n"
        "章节笔记摘录:\n" + "\n\n".join(chapter_summaries[:80])
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_notes(book_json_path: str | Path, provider_config: dict,
                   output_language: str, prompt_template: str,
                   progress_cb: ProgressCallback | None = None,
                   log_cb: LogCallback | None = None,
                   force: bool = False,
                   granularity: str = "all") -> dict[str, Any]:
    """Generate chapter notes and book overview.

    Args:
        force: 默认 False，跳过已存在的笔记文件，只重跑缺失/失败的。
        granularity: 章节分组粒度 — "all"=每章一个笔记, "level1"/"level2"=按层级合并。
                     合并后同组章节的原文会拼接在一起，生成一份笔记。
    """
    book_path = Path(book_json_path)
    book = load_book(book_path)
    chapters = book.get("chapters") or []
    notes_dir = Path(book["paths"]["book_dir"]) / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    generated = 0
    skipped = 0

    # ── 按 granularity 分组 ──
    from src.chat import _session_groups
    groups = _session_groups(chapters, granularity)

    # 为每组确定 note_path（用第一个章节的 chapter_id 命名）
    for group in groups:
        chs = group.get("chapters") or []
        first = chs[0] if chs else None
        if not first:
            continue
        group_id = group.get("group_id") or first.get("chapter_id", "ch_unknown")
        note_path = notes_dir / f"{group_id}.md"
        group["note_path"] = str(note_path)
        # 把 note_path 回写到每个子章节，便于 session 创建时引用
        for ch in chs:
            ch["note_path"] = str(note_path)

    target_count = len(groups)
    total_steps = target_count + 1
    total_t0 = time.time()

    if log_cb:
        ch_total = len(chapters)
        if target_count < ch_total:
            log_cb(f"章节笔记: {ch_total} 个目录节点按 {granularity} 粒度合并为 {target_count} 组")

    # ── 分离：哪些组需要生成，哪些可以跳过 ──
    need_gen: list[tuple[int, dict, Path]] = []  # (group_idx, group, note_path)
    for idx, group in enumerate(groups):
        note_path = Path(group.get("note_path", ""))
        if note_path.is_file() and not force:
            skipped += 1
        else:
            need_gen.append((idx, group, note_path))

    agg_model = provider_config.get("model", "未知模型")

    if skipped > 0 and log_cb:
        log_cb(f"章节笔记: {skipped} 组已有缓存跳过，{len(need_gen)} 组需要生成 ({agg_model})")

    # ── 并发生成笔记（每章只携带目录索引 + 当前内容，无前序笔记累积） ──
    if need_gen:
        if log_cb:
            log_cb(f"章节笔记: {agg_model} 并发生成 {len(need_gen)} 组（并发数 {NOTE_CONCURRENCY}）")

        _gen_lock = threading.Lock()
        _gen_done = [0]  # mutable counter

        def _gen_one(item: tuple[int, dict, Path]) -> tuple[str, bool]:
            """单组笔记生成，返回 (title, success)"""
            gidx, group, note_path = item
            title = group.get("title", "")
            t0 = time.time()
            try:
                chs = group.get("chapters") or []
                if len(chs) == 1:
                    ch_idx = _find_chapter_index(chapters, chs[0])
                    prompt = _chapter_prompt(book, chapters, ch_idx, output_language, prompt_template)
                else:
                    prompt = _group_prompt(book, chapters, group, output_language, prompt_template)

                content = _call_chat(provider_config, prompt, log_cb=log_cb)
                note_path.write_text(content + "\n", encoding="utf-8")
                elapsed = time.time() - t0

                with _gen_lock:
                    _gen_done[0] += 1
                    done = _gen_done[0]
                if log_cb:
                    log_cb(f"章节笔记 [{gidx + 1}/{target_count}] {agg_model} 完成: {title}，耗时 {elapsed:.1f}s，剩余 {len(need_gen) - done} 组")
                if progress_cb:
                    progress_cb(done, total_steps, title)
                return title, True
            except Exception as exc:
                elapsed = time.time() - t0
                with _gen_lock:
                    _gen_done[0] += 1
                    done = _gen_done[0]
                if log_cb:
                    log_cb(f"章节笔记 [{gidx + 1}/{target_count}] {agg_model} 失败: {exc}")
                return title, False

        with ThreadPoolExecutor(max_workers=NOTE_CONCURRENCY) as pool:
            futures = {pool.submit(_gen_one, item): item for item in need_gen}
            for future in as_completed(futures):
                title, success = future.result()
                if success:
                    generated += 1

    # ── 全书总览（在所有章节笔记完成后） ──
    overview_path = notes_dir / "book_overview.md"
    overview_content = overview_path.read_text(encoding="utf-8").strip() if overview_path.is_file() else ""
    # 跳过条件：force=False + 文件存在 + 内容不为空
    if overview_content and not force:
        skipped += 1
        if log_cb:
            log_cb(f"全书总览: 跳过缓存 ({agg_model})")
    else:
        if progress_cb:
            progress_cb(total_steps, total_steps, "全书总览")
        if log_cb:
            log_cb(f"{agg_model} 生成全书总览中...")
        t0 = time.time()
        overview = _call_chat(provider_config, _overview_prompt(book, chapters, output_language),
                              max_tokens=16384, log_cb=log_cb)
        overview_path.write_text(overview + "\n", encoding="utf-8")
        generated += 1
        if log_cb:
            log_cb(f"{agg_model} 全书总览完成，耗时 {time.time() - t0:.1f}s")

    # ── 提取并保存概念表 ──
    _extract_terms_from_notes(notes_dir, chapters, book_path, book, log_cb=log_cb)

    book["chapters"] = chapters
    book.setdefault("memory", {})["overview_path"] = str(overview_path)
    save_book(book_path, book)
    return {
        "generated": generated,
        "skipped": skipped,
        "overview_path": str(overview_path),
        "target_chapters": target_count,
        "total_elapsed": time.time() - total_t0,
    }


def generate_single_chapter_note(
    book_json_path: str | Path,
    chapter_ids: list[str],
    provider_config: dict,
    output_language: str,
    distill_level: str,
) -> bool:
    """为指定的章节列表重新生成笔记文件。

    复用 _chapter_prompt / _call_chat 等内部函数，逐章调用 API 并写入 .md。
    用于单对话级别的笔记重新生成（区别于批量 generate_notes）。

    Returns:
        True 表示至少成功生成一章笔记。
    """
    from src.config import DEFAULT_BOOK_DISTILL_PROMPTS, load_settings

    book_path = Path(book_json_path)
    book = load_book(book_path)
    chapters = book.get("chapters") or []
    notes_dir = Path(book["paths"]["book_dir"]) / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # 解析 prompt：优先用户自定义，fallback 到默认
    settings = load_settings()
    prompts = getattr(settings, "book_distill_prompts", None) or {}
    prompt_template = prompts.get(distill_level) or DEFAULT_BOOK_DISTILL_PROMPTS.get(distill_level, "")
    if not prompt_template:
        return False

    id_set = set(chapter_ids)
    generated = False
    for idx, chapter in enumerate(chapters):
        if chapter.get("chapter_id", "") not in id_set:
            continue
        note_path = notes_dir / f"{chapter.get('chapter_id', f'ch{idx + 1:03d}')}.md"
        try:
            content = _call_chat(
                provider_config,
                _chapter_prompt(book, chapters, idx, output_language, prompt_template),
            )
            note_path.write_text(content + "\n", encoding="utf-8")
            chapter["note_path"] = str(note_path)
            generated = True
        except Exception:
            pass  # 单章失败不阻断其余章节

    # 写回 book.json（更新 note_path）
    if generated:
        save_book(book_path, book)

    return generated


def _extract_terms_from_notes(notes_dir: Path, chapters: list[dict],
                               book_path: Path, book: dict,
                               log_cb: LogCallback | None = None) -> None:
    """从已生成的章节笔记中提取「关键概念」段落，汇总为 terms.json。"""
    terms: list[dict[str, str]] = []
    _seen: set[str] = set()

    for chapter in chapters:
        note_path = Path(chapter.get("note_path", ""))
        if not note_path.is_file():
            continue
        text = note_path.read_text(encoding="utf-8")
        # 查找 "## 关键概念" 段落
        import re
        match = re.search(r"##\s*关键概念\s*\n([\s\S]*?)(?=\n##|\Z)", text)
        if not match:
            continue
        block = match.group(1)
        # 解析 "- **概念名**: 解释" 格式
        for line in block.splitlines():
            m = re.match(r"-\s+\*\*(.+?)\*\*:\s*(.+)", line.strip())
            if m:
                term_name = m.group(1).strip()
                term_def = m.group(2).strip()
                if term_name and term_name not in _seen:
                    _seen.add(term_name)
                    terms.append({
                        "term": term_name,
                        "definition": term_def,
                        "chapter_id": chapter.get("chapter_id", ""),
                        "chapter_title": chapter.get("title", ""),
                    })

    if not terms:
        return

    # 保存到 index/terms.json
    index_dir = Path(book["paths"]["book_dir"]) / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    terms_path = index_dir / "terms.json"
    terms_path.write_text(
        json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    book.setdefault("index", {})["terms_path"] = str(terms_path)
    book["index"]["terms_count"] = len(terms)

    if log_cb:
        log_cb(f"概念提取完成: {len(terms)} 个关键概念已保存")
