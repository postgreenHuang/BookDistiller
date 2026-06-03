"""
Video-Distiller 图片理解模块
- 本地: Ollama 视觉模型 (minicpm-v, llava, qwen2-vl 等)
- 云端: OpenAI 兼容 Vision API (GLM-4V, Qwen-VL, GPT-4o 等)
- 三步调用 (本地小模型) / 单次调用 (云端高级模型)
- 输出 slides.json（增量写入）
"""

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional, Callable

DEFAULT_OLLAMA_OPTIONS = {
    # num_ctx / num_predict left unset so the model's own defaults apply.
    # Previously hard-coded to 2048/1536, which caused vision models like
    # qwen3.5 (image tokens ~2000+) to crash with "SameBatch" errors.
    # Only set num_batch to a conservative value.
    "num_batch": 128,
}


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _parse_timestamp(filename: str) -> str:
    """从 05_12_frame.jpg 提取 '05:12'"""
    m = re.match(r"(\d{2})_(\d{2})", Path(filename).stem)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return "00:00"


def _get_gpu_free_vram_mb() -> int:
    """查询 nvidia-smi 获取全局空闲显存 (MB)，失败返回 0"""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return int(r.stdout.strip().split("\n")[0].strip())
    except Exception:
        pass
    return 0


def calc_auto_concurrency(max_concurrent: int) -> int:
    """根据空闲显存自动计算安全并发数

    视觉模型每个并发请求约需 1.5-2 GB KV cache，
    保留 2 GB 安全余量给显示驱动。
    """
    if max_concurrent <= 1:
        return 1
    free = _get_gpu_free_vram_mb()
    if free <= 0:
        return 1
    PER_REQUEST_MB = 2048
    SAFETY_MB = 2048
    available = free - SAFETY_MB
    if available <= 0:
        return 1
    auto = max(1, available // PER_REQUEST_MB)
    return min(auto, max_concurrent)


def _call_ollama(model: str, prompt: str, image_b64: str, base_url: str,
                 context: Optional[list] = None,
                 keep_alive: str | int | None = None,
                 options: Optional[dict] = None) -> tuple:
    """返回 (text, tokens_dict, context)

    注意: 调用方负责在调用后 del image_b64 释放内存。
    """
    import requests

    url = base_url.rstrip("/") + "/api/generate"
    body = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {**DEFAULT_OLLAMA_OPTIONS, **(options or {})},
    }
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    if context is not None:
        body["context"] = context
    resp = requests.post(url, json=body, timeout=300)
    resp.raise_for_status()
    try:
        data = resp.json()
        text = data.get("response", "").strip()
        new_ctx = data.get("context", [])
        prompt_tokens = data.get("prompt_eval_count", 0) or 0
        completion_tokens = data.get("eval_count", 0) or 0
        return text, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }, new_ctx
    finally:
        resp.close()


def _call_cloud(model: str, prompt: str, image_b64: str,
                base_url: str, api_key: str) -> tuple:
    """返回 (text, tokens_dict)

    注意: 调用方负责在调用后 del image_b64 释放内存。
    """
    import requests

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    data_url = f"data:image/jpeg;base64,{image_b64}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 1024,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=300)
    resp.raise_for_status()
    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
        return text, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
    finally:
        resp.close()


def _analyze_single(model_type: str, model: str, prompt: str,
                    image_b64: str, base_url: str, api_key: str,
                    context: Optional[list] = None) -> tuple:
    """单次调用，返回 (text, tokens_dict, context_or_None)"""
    if model_type == "ollama":
        text, tokens, ctx = _call_ollama(model, prompt, image_b64, base_url, context)
        return text, tokens, ctx
    else:
        text, tokens = _call_cloud(model, prompt, image_b64, base_url, api_key)
        return text, tokens, None


def _find_transcript_context(timestamp_str: str, segments: list,
                             max_chars: int = 150) -> str:
    """找到该时间点对应的 transcript 段落，截取摘要"""
    parts = timestamp_str.split(":")
    ts_seconds = int(parts[0]) * 60 + int(parts[1])
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        if start <= ts_seconds <= end:
            text = seg.get("text", "").strip()
            if not text:
                return ""
            if len(text) <= max_chars:
                return text
            # 截取到最近的句号
            cut = text[:max_chars]
            for punct in ("。", ".", "！", "!", "？", "?", "；", ";"):
                idx = cut.rfind(punct)
                if idx > 20:
                    return text[:idx + 1]
            return cut + "..."
    return ""


def _parse_json_response(text: str) -> Any:
    """从模型输出中提取 JSON，兼容 ```json...``` 包裹"""
    # 直接解析
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试提取 ```json...``` 代码块
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback for models that add a short explanation before/after JSON.
    for open_char, close_char in (("[", "]"), ("{", "}")):
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass

    # 兜底：把原始文本放入 text 字段
    return {"type": "", "title": "", "text": text, "layout": "", "diagrams": ""}


def _write_slides(output_dir: str, slides: list, model: str, tokens: dict,
                  unified_json_path: str = "") -> str:
    """将当前 slides 列表写入统一 JSON（或回退到 slides.json），返回文件路径"""
    if unified_json_path:
        from src.config import read_unified_json, write_unified_json
        data = read_unified_json(unified_json_path)
        data["slides"] = slides
        data["vision_model"] = model
        data["total_slides"] = len(slides)
        data["vision_tokens"] = tokens
        write_unified_json(unified_json_path, data)
        return unified_json_path
    # 回退：无统一路径时写 slides.json
    out_path = os.path.join(output_dir, "slides.json")
    data = {
        "slides": slides,
        "model": model,
        "total_slides": len(slides),
        "tokens": tokens,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out_path


def analyze_images(
    key_frames_dir: str,
    output_dir: str,
    vision_config: dict,
    prompts: dict,
    progress_cb: Optional[Callable[[float], None]] = None,
    cancel_flag: Optional[dict] = None,
    token_cb: Optional[Callable[[dict], None]] = None,
    transcript_segments: Optional[list] = None,
    unified_json_path: str = "",
    max_concurrent: int = 1,
) -> dict:
    """
    分析关键帧图片，生成 slides.json（增量写入）

    Args:
        key_frames_dir: key_frames/ 目录路径
        output_dir: 项目目录 (slides.json 写到此处)
        vision_config: {"type", "model", "url", "api_key", "prompt_strategy"}
        prompts: {"ocr", "diagram", "title", "single"}
        progress_cb: 进度回调 (0.0 ~ 1.0)
        cancel_flag: {"cancel": False} — 设为 True 可中途取消
        token_cb: token 消耗回调
        transcript_segments: transcript.json 的 segments 列表，用于提供上下文

    Returns:
        {"slides", "model", "total_slides", "output", "cancelled", "tokens"}
    """
    frames = sorted(Path(key_frames_dir).glob("*.jpg"))
    if not frames:
        raise ValueError(f"关键帧目录为空: {key_frames_dir}")

    vtype = vision_config.get("type", "ollama")
    model = vision_config.get("model", "minicpm-v:8b")
    base_url = vision_config.get("url", "http://localhost:11434")
    api_key = vision_config.get("api_key", "")
    strategy = vision_config.get("prompt_strategy", "triple")

    # 本地 Ollama 根据空闲显存自动调整并发
    actual_concurrent = max_concurrent
    if vtype == "ollama" and max_concurrent > 1:
        actual_concurrent = calc_auto_concurrency(max_concurrent)

    slides = []
    total = len(frames)
    cancelled = False
    accumulated_tokens = {"prompt": 0, "completion": 0, "total": 0, "calls": 0}
    _lock = __import__("threading").Lock()
    _done_count = [0]

    def _accumulate(tokens: dict, call_count: int = 1):
        with _lock:
            accumulated_tokens["prompt"] += tokens.get("prompt_tokens", 0)
            accumulated_tokens["completion"] += tokens.get("completion_tokens", 0)
            accumulated_tokens["total"] += tokens.get("total_tokens", 0)
            accumulated_tokens["calls"] += call_count
            if token_cb:
                token_cb(dict(accumulated_tokens))

    def _process_frame(frame_path: Path) -> Optional[dict]:
        """处理单帧，返回 slide dict 或 None（取消时）

        注意: image_b64 在函数结束时自动释放（局部变量）。
        """
        if cancel_flag and cancel_flag.get("cancel"):
            return None

        image_b64 = _encode_image(str(frame_path))
        timestamp = _parse_timestamp(frame_path.name)

        try:
            ctx = ""
            if transcript_segments:
                ctx = _find_transcript_context(timestamp, transcript_segments)
            context_prefix = f'当前讲者正在说："{ctx}"\n\n' if ctx else ""

            if strategy == "single":
                prompt_single = context_prefix + prompts.get("single", "")
                raw, tokens, _ = _analyze_single(
                    vtype, model, prompt_single, image_b64, base_url, api_key,
                )
                _accumulate(tokens, 1)
                parsed = _parse_json_response(raw)
                del raw
                return {
                    "timestamp": timestamp,
                    "file": frame_path.name,
                    "type": parsed.get("type", ""),
                    "title": parsed.get("title", ""),
                    "text": parsed.get("text", ""),
                    "layout": parsed.get("layout", ""),
                    "diagrams": parsed.get("diagrams", ""),
                }
            else:
                # Do not carry image request context across OCR/diagram/title calls.
                # Reusing it inflates Ollama KV cache and can starve desktop VRAM.
                text, t1, ollama_ctx = _analyze_single(
                    vtype, model, context_prefix + prompts["ocr"],
                    image_b64, base_url, api_key, None,
                )
                diagrams, t2, ollama_ctx = _analyze_single(
                    vtype, model, context_prefix + prompts["diagram"],
                    image_b64, base_url, api_key, None,
                )
                title, t3, _ = _analyze_single(
                    vtype, model, context_prefix + prompts["title"],
                    image_b64, base_url, api_key, None,
                )
                _accumulate(t1)
                _accumulate(t2)
                _accumulate(t3)
                return {
                    "timestamp": timestamp,
                    "file": frame_path.name,
                    "type": "",
                    "title": title,
                    "text": text,
                    "layout": diagrams,
                    "diagrams": diagrams,
                }
        finally:
            del image_b64

    if actual_concurrent <= 1:
        # 串行模式
        for i, frame_path in enumerate(frames):
            if cancel_flag and cancel_flag.get("cancel"):
                cancelled = True
                break
            slide = _process_frame(frame_path)
            if slide is None:
                cancelled = True
                break
            slides.append(slide)
            _write_slides(output_dir, slides, model, accumulated_tokens, unified_json_path)
            if progress_cb:
                progress_cb((i + 1) / total)
    else:
        # 并行模式 — 分批提交，避免一次性全部入队导致内存峰值
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import gc as _gc

        batch_size = actual_concurrent * 2
        results: dict[int, dict] = {}

        with ThreadPoolExecutor(max_workers=actual_concurrent) as executor:
            for batch_start in range(0, total, batch_size):
                if cancel_flag and cancel_flag.get("cancel"):
                    cancelled = True
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                batch_frames = frames[batch_start:batch_start + batch_size]
                futures = {
                    executor.submit(_process_frame, fp): batch_start + i
                    for i, fp in enumerate(batch_frames)
                }

                for future in as_completed(futures):
                    if cancel_flag and cancel_flag.get("cancel"):
                        cancelled = True
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    idx = futures[future]
                    try:
                        slide = future.result()
                        if slide is not None:
                            results[idx] = slide
                    except Exception:
                        pass

                    with _lock:
                        _done_count[0] += 1
                        if progress_cb:
                            progress_cb(_done_count[0] / total)

                # 每批完成后 GC 回收碎片内存
                _gc.collect()

            # 按原始顺序组装
            for idx in sorted(results.keys()):
                slides.append(results[idx])

        if slides:
            _write_slides(output_dir, slides, model, accumulated_tokens, unified_json_path)

    out_path = unified_json_path or os.path.join(output_dir, "slides.json")

    # Ollama 模型由 keep_alive 配置管理，不再强制卸载
    # keep_alive=0 瞬间释放大量 VRAM 会导致显示驱动崩溃黑屏

    return {
        "slides": slides,
        "model": model,
        "total_slides": len(slides),
        "output": out_path,
        "cancelled": cancelled,
        "tokens": accumulated_tokens,
    }
