"""
Generate chapter notes and book overview with the cloud aggregation provider.
Supports concurrent API calls and smart resume (skip existing notes).
"""

from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import requests

from src.config import RICH_TEXT_FORMATTING_PROMPT


ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]

# 笔记生成并发数（云端 API 天然支持并行）
NOTE_CONCURRENCY = 3


def _clean_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if "\n" in key or "\r" in key or '"' in key or "model" in key.lower():
        raise RuntimeError("API Key 配置不正确：请只填写单行 Key，不要粘贴 JSON 配置片段")
    return key


def _call_chat(provider_config: dict, messages: list[dict], timeout: int = 180) -> str:
    base_url = provider_config.get("base_url", "").rstrip("/")
    api_key = _clean_api_key(provider_config.get("api_key", ""))
    model = provider_config.get("model", "")
    if not base_url or not api_key or not model:
        raise RuntimeError("请先配置可用的云端书籍整合模型 URL、Key 和模型名")

    resp = requests.post(
        base_url + "/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": 8192,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    finally:
        resp.close()


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
    for chapter in chapters:
        note_path = Path(chapter.get("note_path", ""))
        if note_path.is_file():
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
        "章节笔记摘录:\n" + "\n\n".join(chapter_summaries[:80])
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_notes(book_json_path: str | Path, provider_config: dict,
                   output_language: str, prompt_template: str,
                   progress_cb: ProgressCallback | None = None,
                   log_cb: LogCallback | None = None,
                   force: bool = False) -> dict[str, Any]:
    """Generate chapter notes and book overview.

    Smart resume: by default skips chapters whose note file already exists.
    Set force=True to regenerate all notes (e.g. after changing prompt).

    Concurrent: generates up to NOTE_CONCURRENCY chapters in parallel.

    Args:
        force: 默认 False，跳过已存在的笔记文件，只重跑缺失/失败的。
               设为 True 强制全部重生成（用于更换 Prompt/模型后）。
    """
    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    chapters = book.get("chapters") or []
    notes_dir = Path(book["paths"]["book_dir"]) / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    generated = 0
    skipped = 0

    target_count = len(chapters)
    total_steps = target_count + 1
    total_t0 = time.time()

    # ── 分离：哪些章节需要生成，哪些可以跳过 ──
    need_gen: list[tuple[int, dict, Path]] = []  # (idx, chapter, note_path)
    for idx, chapter in enumerate(chapters):
        note_path = notes_dir / f"{chapter.get('chapter_id', f'ch{idx + 1:03d}')}.md"
        chapter["note_path"] = str(note_path)
        if note_path.is_file() and not force:
            skipped += 1
        else:
            need_gen.append((idx, chapter, note_path))

    if skipped > 0 and log_cb:
        log_cb(f"章节笔记: {skipped} 章已有缓存跳过，{len(need_gen)} 章需要生成")

    # ── 并发生成章节笔记 ──
    if need_gen:
        _lock = threading.Lock()
        _done_count = [0]

        def _gen_one(idx: int, chapter: dict, note_path: Path) -> tuple[int, float, str]:
            """生成单章笔记，返回 (idx, elapsed, error_msg)"""
            t0 = time.time()
            try:
                content = _call_chat(
                    provider_config,
                    _chapter_prompt(book, chapters, idx, output_language, prompt_template),
                )
                note_path.write_text(content + "\n", encoding="utf-8")
                elapsed = time.time() - t0
                return idx, elapsed, ""
            except Exception as exc:
                elapsed = time.time() - t0
                return idx, elapsed, str(exc)

        if len(need_gen) == 1 or NOTE_CONCURRENCY <= 1:
            # 单章或串行模式
            for idx, chapter, note_path in need_gen:
                if progress_cb:
                    progress_cb(idx + 1, total_steps, chapter.get("title", ""))
                if log_cb:
                    log_cb(f"章节笔记 [{idx + 1}/{target_count}] 生成中: {chapter.get('title', '')}...")
                result_idx, elapsed, error = _gen_one(idx, chapter, note_path)
                if error:
                    if log_cb:
                        log_cb(f"章节笔记 [{idx + 1}/{target_count}] 失败: {error}")
                else:
                    generated += 1
                    if log_cb:
                        remaining = len(need_gen) - generated
                        log_cb(f"章节笔记 [{idx + 1}/{target_count}] 完成: {chapter.get('title', '')}，耗时 {elapsed:.1f}s，剩余 {remaining} 章")
        else:
            # 并发模式
            actual_concurrent = min(NOTE_CONCURRENCY, len(need_gen))
            if log_cb:
                log_cb(f"章节笔记: 并发生成 {len(need_gen)} 章 (并发数 {actual_concurrent})")

            with ThreadPoolExecutor(max_workers=actual_concurrent) as executor:
                future_map = {
                    executor.submit(_gen_one, idx, ch, np): idx
                    for idx, ch, np in need_gen
                }
                for future in as_completed(future_map):
                    idx, elapsed, error = future.result()
                    chapter = chapters[idx]
                    with _lock:
                        _done_count[0] += 1
                    if error:
                        if log_cb:
                            log_cb(f"章节笔记 [{idx + 1}/{target_count}] 失败: {error}")
                    else:
                        generated += 1
                        if log_cb:
                            remaining = len(need_gen) - _done_count[0]
                            log_cb(f"章节笔记 [{idx + 1}/{target_count}] 完成: {chapter.get('title', '')}，耗时 {elapsed:.1f}s，剩余 {remaining} 章")
                    if progress_cb:
                        progress_cb(idx + 1, total_steps, chapter.get("title", ""))

    # ── 全书总览（在所有章节笔记完成后） ──
    overview_path = notes_dir / "book_overview.md"
    if overview_path.is_file() and not force:
        skipped += 1
        if log_cb:
            log_cb("全书总览: 跳过缓存")
    else:
        if progress_cb:
            progress_cb(total_steps, total_steps, "全书总览")
        if log_cb:
            log_cb("生成全书总览中...")
        t0 = time.time()
        overview = _call_chat(provider_config, _overview_prompt(book, chapters, output_language))
        overview_path.write_text(overview + "\n", encoding="utf-8")
        generated += 1
        if log_cb:
            log_cb(f"全书总览完成，耗时 {time.time() - t0:.1f}s")

    # ── 提取并保存概念表 ──
    _extract_terms_from_notes(notes_dir, chapters, book_path, book, log_cb=log_cb)

    book["chapters"] = chapters
    book.setdefault("memory", {})["overview_path"] = str(overview_path)
    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
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
    book = json.loads(book_path.read_text(encoding="utf-8"))
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
        book_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")

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
