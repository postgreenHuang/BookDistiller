"""
Page-level visual analysis for scanned pages, diagrams, and complex layouts.
Renders PDF pages to images and calls vision models for OCR / layout analysis.

Memory/VRAM safety:
- Pixmap objects are freed immediately after saving
- Images are resized to max 1024px before encoding for vision models
- JPEG compression reduces base64 payload by ~70% vs PNG
- Dynamic VRAM monitoring pauses processing when GPU memory is low
- gc.collect() runs periodically to reclaim Python heap memory
"""

from __future__ import annotations

import gc
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.cache import get_cached_visual, save_visual_cache
from src.image_analysis import (
    _call_cloud,
    _call_ollama,
    _encode_image,
    _parse_json_response,
)
from src.pdf_reader import load_pages, save_pages

ProgressCallback = Callable[[str, float], None]
LogCallback = Callable[[str], None]

# Keep PDF page resolution intact for OCR. VRAM safety is handled by serial
# execution, VRAM checks, cooldowns, and prompt/context limits rather than by
# downscaling text-heavy pages.
DEFAULT_VISION_MAX_DIMENSION = 0
# Use high-quality JPEG for regular page OCR; TOC extraction can opt into PNG.
DEFAULT_VISION_JPEG_QUALITY = 95
# Leave headroom for the display driver when Ollama is using the desktop GPU.
OLLAMA_MIN_FREE_VRAM_MB = 2048
OLLAMA_PAGE_COOLDOWN_SECONDS = 2.0


def render_page(pdf_path: str | Path, page_num: int,
                output_dir: str | Path, dpi: int = 200,
                max_dimension: int = 0, jpeg_quality: int = 85) -> Path:
    """Render a single PDF page to image using PyMuPDF (fitz).

    Args:
        pdf_path: Path to the PDF file.
        page_num: 1-indexed page number.
        output_dir: Directory to write the rendered image.
        dpi: Render resolution (default 200).
        max_dimension: If > 0, resize image so longest side <= this value.
                       Recommended: 1024 for vision models.
        jpeg_quality: JPEG quality (1-100). 0 = save as PNG instead.

    Returns:
        Path to the rendered image file.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError("缺少 PDF 渲染依赖 PyMuPDF，请运行: pip install PyMuPDF")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    use_jpeg = jpeg_quality > 0
    ext = ".jpg" if use_jpeg else ".png"
    if max_dimension > 0:
        render_suffix = f"m{max_dimension}_q{jpeg_quality}" if use_jpeg else f"m{max_dimension}_png"
    else:
        render_suffix = f"full_q{jpeg_quality}" if use_jpeg else "full_png"
    output_path = out / f"page_{page_num:04d}_{render_suffix}{ext}"

    # Skip if already rendered (check current format)
    if output_path.is_file():
        return output_path

    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_num - 1]  # 0-indexed
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)

        need_resize = max_dimension > 0 and max(pix.width, pix.height) > max_dimension
        if use_jpeg or need_resize:
            # Convert Pixmap → PIL Image for JPEG output and/or resizing
            from PIL import Image

            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples, "raw", "RGB")
            # Free Pixmap immediately (each page ~15 MB uncompressed)
            del pix

            if need_resize:
                ratio = max_dimension / max(img.width, img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            if use_jpeg:
                img.save(str(output_path), "JPEG", quality=jpeg_quality, optimize=True)
            else:
                img.save(str(output_path), "PNG", optimize=True)
            del img
        else:
            pix.save(str(output_path))
            del pix
    finally:
        doc.close()

    return output_path


def _check_vram_safe(min_free_mb: int = OLLAMA_MIN_FREE_VRAM_MB) -> bool:
    """Check if GPU has enough free VRAM to proceed.

    Returns True if safe to continue, False if VRAM is critically low.
    Always returns True if nvidia-smi is unavailable (non-NVIDIA GPU).
    """
    from src.image_analysis import _get_gpu_free_vram_mb
    free = _get_gpu_free_vram_mb()
    if free <= 0:
        return True  # Can't check, assume OK (non-NVIDIA or no GPU)
    return free >= min_free_mb


def _wait_for_vram(min_free_mb: int = OLLAMA_MIN_FREE_VRAM_MB, max_wait: int = 120,
                   log_cb: LogCallback | None = None) -> bool:
    """Wait for GPU VRAM to free up.

    Returns True if VRAM recovered, False if still low after max_wait seconds.
    """
    waited = 0
    interval = 5
    while waited < max_wait:
        if _check_vram_safe(min_free_mb):
            return True
        if log_cb and waited == 0:
            log_cb(f"⚠ 显存不足，暂停等待释放 (需要 ≥{min_free_mb}MB 空闲)...")
        time.sleep(interval)
        waited += interval
    return _check_vram_safe(min_free_mb)


def analyze_page(image_path: str | Path, vision_config: dict,
                 prompt: str, book_dir: str | Path | None = None,
                 page_num: int = 0, prompt_version: str = "") -> dict[str, Any]:
    """Analyze a single page image with a vision model.

    Checks cache first, then calls the vision model and caches the result.
    Explicitly cleans up base64 data after API call to release memory.

    Returns:
        Parsed result dict with keys: type, text, layout, diagrams, title.
    """
    model = vision_config.get("model", "")
    vision_type = vision_config.get("type", "ollama")
    base_url = vision_config.get("url", "http://localhost:11434")
    api_key = vision_config.get("api_key", "")

    # Check cache
    if book_dir and model and prompt_version:
        cached = get_cached_visual(book_dir, page_num, model, prompt_version)
        if cached:
            return {**cached, "_cached": True}

    # Encode image to base64
    image_b64 = _encode_image(str(image_path))

    # Call vision model
    try:
        if vision_type == "ollama":
            raw_text, tokens, _ = _call_ollama(model, prompt, image_b64, base_url)
        else:
            raw_text, tokens = _call_cloud(model, prompt, image_b64, base_url, api_key)
    finally:
        # Always free base64 string (~6-15 MB per page) after API call
        del image_b64

    # Parse response
    parsed = _parse_json_response(raw_text)
    del raw_text

    # Normalize fields
    result = {
        "type": parsed.get("type", ""),
        "title": parsed.get("title", ""),
        "text": parsed.get("text", ""),
        "layout": parsed.get("layout", ""),
        "diagrams": parsed.get("diagrams", ""),
        "model": model,
        "page": page_num,
    }
    del parsed

    # Save cache
    if book_dir and model and prompt_version:
        save_visual_cache(book_dir, page_num, model, prompt_version, result, image_path)

    return result


def analyze_book_pages(book_json_path: str | Path,
                       vision_config: dict,
                       prompts: dict[str, str],
                       progress_cb: ProgressCallback | None = None,
                       log_cb: LogCallback | None = None,
                       max_concurrent: int = 1,
                       chapter_page_map: dict[int, dict] | None = None,
                       max_dimension: int = 0) -> dict[str, Any]:
    """Analyze all pages that need visual processing in a book.

    Memory/VRAM safety features:
    - Renders pages as compressed JPEG at max 1024px (not full-res PNG)
    - Checks VRAM before each page; pauses if GPU memory is low
    - Runs gc.collect() every N pages to reclaim Python heap
    - Concurrent mode uses batched submission (not all-at-once)
    - Each page's base64 data is freed immediately after API call

    Args:
        book_json_path: Path to book.json.
        vision_config: Vision model configuration dict.
        prompts: Dict with keys like "single", "ocr" containing prompt templates.
        progress_cb: Callback(label, value) for progress reporting.
        log_cb: Callback(msg) for log messages.
        max_concurrent: Max concurrent vision model calls.
        chapter_page_map: Optional page_num → {chapter_id, title} mapping.
                          When provided, chapter context is prepended to the prompt
                          so the vision model can produce richer metadata.

    Returns:
        Stats dict with keys: analyzed, cached, skipped, total_needs_visual, errors.
    """
    book_path = Path(book_json_path)
    book = json.loads(book_path.read_text(encoding="utf-8"))
    book_dir = Path(book["paths"]["book_dir"])
    pdf_path = book.get("source_pdf", "")
    pages_path = book["paths"]["pages_path"]
    pages = load_pages(pages_path)

    model = vision_config.get("model", "")
    prompt_version = prompts.get("_version", "v1")

    # Identify pages needing visual analysis
    needs_visual = [
        p for p in pages
        if p.get("page_type") in ("needs_ocr", "is_blank", "is_cover")
    ]

    if not needs_visual:
        return {"analyzed": 0, "cached": 0, "skipped": 0,
                "total_needs_visual": 0, "errors": 0}

    if not pdf_path or not Path(pdf_path).is_file():
        raise RuntimeError(f"PDF 文件不存在: {pdf_path}")

    # Select prompt: prefer "single" for comprehensive analysis
    prompt = prompts.get("single", prompts.get("ocr", ""))

    # Determine concurrency — always check VRAM first
    vision_type = vision_config.get("type", "ollama")
    actual_concurrent = max_concurrent
    if vision_type == "ollama":
        # Local vision models share VRAM with the desktop compositor. Even when
        # nvidia-smi reports enough memory, concurrent image requests can spike
        # KV cache usage and black out the display, so PDF OCR stays serial.
        actual_concurrent = 1

    stats = {"analyzed": 0, "cached": 0, "skipped": 0, "errors": 0,
             "total_needs_visual": len(needs_visual)}
    _lock = threading.Lock()
    # Track GC interval (collect every 5 pages to balance overhead vs cleanup)
    _gc_counter = [0]
    GC_INTERVAL = 5

    def _process_page(page_data: dict) -> dict | None:
        """Process a single page: render → encode → vision API → cache.

        Memory lifecycle:
        1. render_page: Pixmap freed inside render, output is small JPEG
        2. analyze_page: base64 freed after API call, response parsed and freed
        3. result dict is small (text metadata only)
        """
        page_num = int(page_data.get("page", 0))
        try:
            # Render page to JPEG for vision model
            pages_render_dir = book_dir / "pages" / "rendered"
            render_max = max_dimension if max_dimension > 0 else DEFAULT_VISION_MAX_DIMENSION
            image_path = render_page(
                pdf_path, page_num, str(pages_render_dir),
                max_dimension=render_max,
                jpeg_quality=DEFAULT_VISION_JPEG_QUALITY,
            )

            # 构建带章节上下文的 prompt
            page_prompt = prompt
            ch_info = (chapter_page_map or {}).get(page_num)
            if ch_info:
                page_prompt = (
                    f"[上下文: 此页属于「{ch_info.get('title', '')}」（{ch_info.get('chapter_id', '')}）]\n"
                    + page_prompt
                )

            # Analyze (base64 encoded + API call, cleaned up inside)
            result = analyze_page(
                image_path, vision_config, page_prompt,
                book_dir=str(book_dir), page_num=page_num,
                prompt_version=prompt_version,
            )

            was_cached = result.pop("_cached", False)

            # Extract text from visual result and update page data
            ocr_text = result.get("text", "").strip()
            if ocr_text:
                page_data["text"] = ocr_text
                page_data["char_count"] = len(ocr_text)
                page_data["has_text"] = True
                page_data["page_type"] = "text_ok"  # Upgraded by OCR

            # Store visual metadata (small dict, no image data)
            visual_meta = {
                "type": result.get("type", ""),
                "title": result.get("title", ""),
                "layout": result.get("layout", ""),
                "diagrams": result.get("diagrams", ""),
                "model": model,
            }
            # 附上章节归属（如果有）
            if ch_info:
                visual_meta["chapter_id"] = ch_info.get("chapter_id", "")
                visual_meta["chapter_title"] = ch_info.get("title", "")
            page_data["visual_analysis"] = visual_meta

            with _lock:
                if was_cached:
                    stats["cached"] += 1
                else:
                    stats["analyzed"] += 1
                # Periodic GC to reclaim fragmented Python heap memory
                _gc_counter[0] += 1
                if _gc_counter[0] >= GC_INTERVAL:
                    _gc_counter[0] = 0
                    gc.collect()

            del result
            return None  # Don't need to return large result in batch mode

        except Exception as exc:
            with _lock:
                stats["errors"] += 1
            if log_cb:
                log_cb(f"  页 {page_num} 视觉分析失败: {exc}")
            return None

    if log_cb:
        log_cb(f"视觉页面分析: {len(needs_visual)} 页需要处理 (模型: {model}, 并发: {actual_concurrent})")

    # Process pages
    if actual_concurrent <= 1:
        # Serial mode — safest for VRAM, with per-page VRAM monitoring
        for i, page_data in enumerate(needs_visual):
            # Ollama 本地模型: 处理前检查显存，不足则等待
            if vision_type == "ollama":
                if not _check_vram_safe():
                    recovered = _wait_for_vram(log_cb=log_cb)
                    if not recovered:
                        if log_cb:
                            log_cb("⚠ 显存持续不足，降低到单线程继续处理")

            _process_page(page_data)
            if progress_cb:
                progress_cb(f"视觉分析 {i + 1}/{len(needs_visual)}", (i + 1) / len(needs_visual))

            # Ollama 模型处理完一页后短暂等待，让 KV cache 有时间释放
            if vision_type == "ollama" and not page_data.get("_cached"):
                time.sleep(OLLAMA_PAGE_COOLDOWN_SECONDS)
    else:
        # Concurrent mode — batched submission to avoid all-at-once memory spike
        from concurrent.futures import ThreadPoolExecutor, as_completed

        batch_size = actual_concurrent * 2  # Submit 2x workers worth at a time
        _done = [0]
        total = len(needs_visual)

        with ThreadPoolExecutor(max_workers=actual_concurrent) as executor:
            for batch_start in range(0, total, batch_size):
                batch = needs_visual[batch_start:batch_start + batch_size]
                futures = {
                    executor.submit(_process_page, pd): i
                    for i, pd in enumerate(batch)
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass
                    with _lock:
                        _done[0] += 1
                    if progress_cb:
                        progress_cb(
                            f"视觉分析 {_done[0]}/{total}",
                            _done[0] / total,
                        )
                # Batch boundary: force GC to clean up before next batch
                gc.collect()

    # Save updated pages
    save_pages(pages_path, pages)

    # Update book.json
    from collections import Counter
    text_page_count = sum(1 for p in pages if p.get("has_text"))
    page_type_counts = dict(Counter(p.get("page_type", "unknown") for p in pages))
    book["text_page_count"] = text_page_count
    book["page_types"] = page_type_counts
    book["visual_analysis_stats"] = stats
    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")

    if log_cb:
        log_cb(f"视觉分析完成: 分析 {stats['analyzed']} 页, 缓存 {stats['cached']} 页, "
               f"失败 {stats['errors']} 页, 文本页升级为 {text_page_count}")

    # Final cleanup
    gc.collect()
    if vision_type == "ollama" and stats.get("analyzed", 0) > 0:
        time.sleep(OLLAMA_PAGE_COOLDOWN_SECONDS)

    return stats
