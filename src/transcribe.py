"""
Video-Distiller 语音转录模块
- 本地: faster-whisper (GPU float16 / CPU int8)
- 云端: OpenAI 兼容 API (/audio/transcriptions)
- 输出 transcript.json，时间戳与关键帧 MM:SS 对齐
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Callable


def _seconds_to_mmss(seconds: float) -> str:
    from src.utils import seconds_to_mmss
    return seconds_to_mmss(seconds)


def _mmss_to_seconds(mmss: str) -> float:
    parts = mmss.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _get_audio_duration(path: str) -> float:
    from src.media import find_ffmpeg
    ffmpeg_path = Path(find_ffmpeg())
    ffprobe = str(ffmpeg_path.parent / ffmpeg_path.name.replace("ffmpeg", "ffprobe"))
    if not os.path.exists(ffprobe):
        ffprobe = "ffprobe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _split_audio_chunks(audio_path: str, chunk_seconds: int, tmp_dir: str) -> list[tuple[str, float]]:
    from src.media import find_ffmpeg
    ffmpeg = find_ffmpeg()
    duration = _get_audio_duration(audio_path)
    if duration <= 0:
        return [(audio_path, 0.0)]

    chunks = []
    offset = 0.0
    idx = 0
    while offset < duration:
        out_path = os.path.join(tmp_dir, f"chunk_{idx:04d}.mp3")
        cmd = [
            ffmpeg, "-i", audio_path,
            "-ss", str(offset), "-t", str(chunk_seconds),
            "-y", out_path,
        ]
        subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if os.path.exists(out_path):
            chunks.append((out_path, offset))
        offset += chunk_seconds
        idx += 1
    return chunks


def _merge_segments(raw_segments: list[dict], target_length: int) -> list[dict]:
    if not raw_segments:
        return []
    merged = []
    buf = dict(raw_segments[0])
    for seg in raw_segments[1:]:
        if buf["end"] - buf["start"] < target_length:
            buf["end"] = seg["end"]
            buf["text"] += " " + seg["text"]
        else:
            merged.append(buf)
            buf = dict(seg)
    merged.append(buf)
    return merged


def _enrich_segments(segments: list[dict]) -> list[dict]:
    for seg in segments:
        seg["start_mmss"] = _seconds_to_mmss(seg["start"])
        seg["end_mmss"] = _seconds_to_mmss(seg["end"])
    return segments


# ─── 本地 faster-whisper（BatchedInferencePipeline） ───


def _transcribe_local(
    audio_path: str,
    model_name: str = "large-v3",
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    segment_length: int = 180,
    progress_cb: Optional[Callable[[float], None]] = None,
    batch_size: int = 16,
) -> list[dict]:
    from faster_whisper import WhisperModel, BatchedInferencePipeline

    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    compute = "float16" if device == "cuda" else "int8"

    model = WhisperModel(model_name, device=device, compute_type=compute)
    pipeline = BatchedInferencePipeline(model=model)

    lang = language if language and language != "auto" else None
    prompt = initial_prompt if initial_prompt else None

    segments_iter, info = pipeline.transcribe(
        audio_path,
        language=lang,
        initial_prompt=prompt,
        batch_size=batch_size,
        condition_on_previous_text=True,
        vad_filter=True,
    )

    raw_segments = []
    total_duration = info.duration
    for seg in segments_iter:
        raw_segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })
        if progress_cb and total_duration > 0:
            progress_cb(min(seg.end / total_duration, 1.0))

    del pipeline, model
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

    raw_segments.sort(key=lambda s: s["start"])
    merged = _merge_segments(raw_segments, segment_length)
    return _enrich_segments(merged)


# ─── DashScope SDK (qwen-asr 系列) ───

def _transcribe_dashscope(
    audio_path: str,
    api_key: str,
    cloud_model: str = "qwen3-asr-flash",
    language: Optional[str] = None,
    segment_length: int = 180,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> list[dict]:
    """通过 DashScope SDK 转录音频（qwen-asr / sensevoice 系列）"""
    from dashscope import MultiModalConversation

    if not api_key:
        raise ValueError("请先在 Settings 中配置 DashScope API Key")

    asr_opts: dict = {"enable_lid": True, "enable_itn": False}
    if language and language != "auto":
        asr_opts["language"] = language

    # 用 while 循环支持切片长度自适应（for 循环无法重新生成迭代器）
    chunk_seconds = min(segment_length, 30)
    all_raw: list[dict] = []
    prev_text = ""

    while True:
        with tempfile.TemporaryDirectory() as tmp_dir:
            chunks = _split_audio_chunks(audio_path, chunk_seconds, tmp_dir)
            all_raw.clear()
            prev_text = ""
            need_shorter = False

            for i, (chunk_path, offset) in enumerate(chunks):
                # file:// + 原始绝对路径（不能用 Path.as_uri()，Windows 三斜杠会失败）
                audio_ref = f"file://{os.path.abspath(chunk_path)}"

                ctx = ""
                if prev_text:
                    ctx = f"前文参考（保持术语和上下文一致）：{prev_text[-300:]}"

                messages = [
                    {"role": "system", "content": [{"text": ctx}]},
                    {"role": "user",   "content": [{"audio": audio_ref}]},
                ]
                resp = MultiModalConversation.call(
                    api_key=api_key,
                    model=cloud_model,
                    messages=messages,
                    result_format="message",
                    asr_options=asr_opts,
                )

                if resp.status_code != 200:
                    err_msg = getattr(resp, "message", "") or ""
                    if "too long" in err_msg.lower() and chunk_seconds > 15:
                        chunk_seconds = chunk_seconds // 2
                        need_shorter = True
                        break
                    raise RuntimeError(f"DashScope ASR 错误 ({resp.status_code}): {err_msg}")

                content = resp.output["choices"][0]["message"]["content"]
                text = ""
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and "text" in c:
                            text += c["text"]
                elif isinstance(content, str):
                    text = content
                text = text.strip()

                if text:
                    duration = _get_audio_duration(chunk_path)
                    all_raw.append({
                        "start": round(offset, 2),
                        "end": round(offset + duration, 2),
                        "text": text,
                    })
                    prev_text = text

                if progress_cb:
                    progress_cb((i + 1) / len(chunks))

            if not need_shorter:
                break

    merged = _merge_segments(all_raw, segment_length)
    return _enrich_segments(merged)


# ─── 云端 OpenAI 兼容 API ───

def _transcribe_cloud(
    audio_path: str,
    api_url: str,
    api_key: str,
    cloud_model: str = "whisper-large-v3",
    language: Optional[str] = None,
    segment_length: int = 180,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> list[dict]:
    import requests

    if not api_url or not api_key:
        raise ValueError("请先在 Settings 中配置云端 ASR 的 API 地址和 Key")

    url = api_url.rstrip("/") + "/audio/transcriptions"
    lang = language if language and language != "auto" else None

    with tempfile.TemporaryDirectory() as tmp_dir:
        chunks = _split_audio_chunks(audio_path, segment_length, tmp_dir)
        all_raw = []

        for i, (chunk_path, offset) in enumerate(chunks):
            with open(chunk_path, "rb") as f:
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (os.path.basename(chunk_path), f, "audio/mp3")},
                    data={
                        "model": cloud_model,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                        **({"language": lang} if lang else {}),
                    },
                    timeout=300,
                )
            resp.raise_for_status()
            data = resp.json()

            segments = data.get("segments", [])
            if not segments:
                text = data.get("text", "").strip()
                if text:
                    duration = _get_audio_duration(chunk_path)
                    segments = [{"start": 0.0, "end": duration, "text": text}]

            for seg in segments:
                all_raw.append({
                    "start": round(seg["start"] + offset, 2),
                    "end": round(seg["end"] + offset, 2),
                    "text": seg["text"].strip(),
                })

            if progress_cb:
                progress_cb((i + 1) / len(chunks))

    merged = _merge_segments(all_raw, segment_length)
    return _enrich_segments(merged)


def _transcribe_multimodal(
    audio_path: str,
    api_url: str,
    api_key: str,
    cloud_model: str = "qwen3.6-plus",
    language: Optional[str] = None,
    segment_length: int = 180,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> list[dict]:
    """通过多模态 LLM 的 chat/completions 接口转录音频"""
    import requests
    import base64

    if not api_url or not api_key:
        raise ValueError("请先在 Settings 中配置云端 ASR 的 API 地址和 Key")

    url = api_url.rstrip("/") + "/chat/completions"
    lang_hint = ""
    if language and language != "auto":
        lang_names = {"zh": "中文", "en": "英文", "ja": "日文"}
        lang_hint = f"音频语言为{lang_names.get(language, language)}，"

    with tempfile.TemporaryDirectory() as tmp_dir:
        chunks = _split_audio_chunks(audio_path, segment_length, tmp_dir)
        all_raw = []

        for i, (chunk_path, offset) in enumerate(chunks):
            with open(chunk_path, "rb") as f:
                audio_b64 = base64.b64encode(f.read()).decode("utf-8")

            payload = {
                "model": cloud_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "url": f"data:audio/mp3;base64,{audio_b64}",
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    f"请将这段音频{lang_hint}完整转录为文字。"
                                    "逐字转录，不要省略内容，不要添加解释。"
                                    "如果音频中有多个说话者，标注说话者。"
                                ),
                            },
                        ],
                    }
                ],
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            resp = requests.post(url, json=payload, headers=headers, timeout=300)
            resp.raise_for_status()

            text = resp.json()["choices"][0]["message"]["content"].strip()
            duration = _get_audio_duration(chunk_path)
            if text:
                all_raw.append({
                    "start": round(offset, 2),
                    "end": round(offset + duration, 2),
                    "text": text,
                })

            if progress_cb:
                progress_cb((i + 1) / len(chunks))

    merged = _merge_segments(all_raw, segment_length)
    return _enrich_segments(merged)


# ─── 统一入口 ───

def transcribe(
    audio_path: str,
    output_dir: str,
    video_path: str = "",
    asr_type: str = "local",
    model: str = "large-v3",
    language: Optional[str] = None,
    vocabulary: Optional[str] = None,
    segment_length: int = 180,
    asr_api_url: str = "",
    asr_api_key: str = "",
    asr_cloud_model: str = "whisper-large-v3",
    asr_api_type: str = "whisper",
    progress_cb: Optional[Callable[[float], None]] = None,
    batch_size: int = 16,
) -> dict:
    if asr_type == "cloud":
        if asr_api_type == "dashscope":
            segments = _transcribe_dashscope(
                audio_path, asr_api_key, asr_cloud_model,
                language, segment_length, progress_cb,
            )
        elif asr_api_type == "multimodal":
            segments = _transcribe_multimodal(
                audio_path, asr_api_url, asr_api_key, asr_cloud_model,
                language, segment_length, progress_cb,
            )
        else:
            segments = _transcribe_cloud(
                audio_path, asr_api_url, asr_api_key, asr_cloud_model,
                language, segment_length, progress_cb,
            )
        used_model = asr_cloud_model
    else:
        segments = _transcribe_local(
            audio_path, model, language, vocabulary, segment_length, progress_cb,
            batch_size=batch_size,
        )
        used_model = model

    # 保留 transcript 子目录兼容旧逻辑
    transcript_dir = os.path.join(output_dir, "transcript")
    os.makedirs(transcript_dir, exist_ok=True)

    total_duration = segments[-1]["end"] if segments else 0.0
    metadata = {
        "asr_type": asr_type,
        "model": used_model,
        "language": language or "auto",
        "initial_prompt": vocabulary or "",
        "segment_length": segment_length,
        "total_duration": round(total_duration, 2),
        "total_segments": len(segments),
        "total_chars": sum(len(s["text"]) for s in segments),
    }

    result = {
        "segments": segments,
        "metadata": metadata,
    }

    # 写入统一 JSON（{video_name}.json）
    if video_path:
        from src.config import get_unified_json_path, read_unified_json, write_unified_json
        unified_path = get_unified_json_path(os.path.dirname(output_dir) if os.path.basename(output_dir) == Path(video_path).stem else output_dir, video_path)
        data = read_unified_json(unified_path)
        data["segments"] = segments
        data["metadata"] = metadata
        write_unified_json(unified_path, data)
        result["output"] = str(unified_path)
    else:
        # 兼容：没有 video_path 时写入旧路径
        out_path = os.path.join(transcript_dir, "transcript.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        result["output"] = out_path

    return result


def build_preview_text(segments: list[dict]) -> str:
    return "\n\n".join(f"[{s['start_mmss']}] {s['text']}" for s in segments)
