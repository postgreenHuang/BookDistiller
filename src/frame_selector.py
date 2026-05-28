"""
Video-Distiller AI 智能选帧模块
- 读取 transcript.json，通过 AI 语义分析选出值得保留的帧
- 替代传统 SSIM/pHash 去重，只保留转录无法替代的视觉内容
"""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Callable


FRAME_SELECTION_PROMPT = """\
你正在审阅一段技术演讲的转录文本。原始视频已按固定间隔截图。
你的任务是判断哪些时间点的截图值得保留，用于后续的视觉分析。

保留标准（满足任一即可）：
1. 讲者明确引用了视觉内容（图表、架构图、代码、界面演示）
2. 从内容推断很可能有重要的可视化展示（性能对比、流程演示、数据表格）
3. 讲者的描述模糊不清，视觉内容可能补充理解
4. 明显的话题/章节切换点（可能换了 PPT 页或打开了新工具）
5. 包含具体数值、参数配置，可能有对应的表格或截图

不需要保留的情况：
- 纯概念讲解，转录文字已充分传达内容
- 讲者闲聊、问答、过渡性语言
- 内容与前一段重复或延续，没有视觉变化

以下是转录文本（每段带 [MM:SS] 时间戳）：

{transcript_text}

以下是可用帧的时间点列表（MM:SS 格式）：
{available_frames}

请从中选出值得保留的帧，输出 JSON（只输出 JSON，不要其他内容）：
{{"selected": [{{"timestamp": "MM:SS", "reason": "一句话说明保留原因"}}]}}"""


def _parse_frame_timestamp(filename: str) -> float:
    """从 05_12_frame.jpg 提取秒数"""
    m = re.match(r"(\d{2})_(\d{2})", Path(filename).stem)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return -1.0


def _seconds_to_mmss(seconds: float) -> str:
    from src.utils import seconds_to_mmss
    return seconds_to_mmss(seconds)


def _ensure_renamed_frames(frames_dir: str):
    """如果有残留的 _tmp_NNNNNN.jpg 未重命名，补一轮重命名"""
    tmp_files = sorted(
        f for f in os.listdir(frames_dir) if f.startswith("_tmp_") and f.endswith(".jpg")
    )
    if not tmp_files:
        return
    # 推断 fps：看已有重命名文件的数量推算，或用默认 1fps
    renamed = [f for f in os.listdir(frames_dir) if re.match(r'\d{2}_\d{2}_frame\.jpg', f)]
    if renamed:
        return  # 已有重命名文件，_tmp_ 是多余的重复，跳过
    fps = 1.0  # 默认 1fps
    for idx, fname in enumerate(tmp_files):
        seconds = idx / fps
        m, s = int(seconds) // 60, int(seconds) % 60
        new_name = f"{m:02d}_{s:02d}_frame.jpg"
        src = os.path.join(frames_dir, fname)
        dst = os.path.join(frames_dir, new_name)
        if not os.path.exists(dst):
            os.rename(src, dst)
        else:
            os.remove(src)


def _build_available_frames_list(frames_dir: str) -> list[tuple[str, float]]:
    """返回 [(filename, seconds), ...] 按时间排序"""
    frames = []
    for f in Path(frames_dir).iterdir():
        if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            ts = _parse_frame_timestamp(f.name)
            if ts >= 0:
                frames.append((f.name, ts))
    frames.sort(key=lambda x: x[1])
    return frames


def _build_transcript_text(segments: list[dict]) -> str:
    """将 transcript segments 格式化为 Prompt 中的文本"""
    lines = []
    for seg in segments:
        start = seg.get("start_mmss", _seconds_to_mmss(seg.get("start", 0)))
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{start}] {text}")
    return "\n".join(lines)


def _find_nearest_frame(
    target_seconds: float, available: list[tuple[str, float]]
) -> str | None:
    """找到最接近目标时间的帧文件名"""
    if not available:
        return None
    best = min(available, key=lambda x: abs(x[1] - target_seconds))
    # 只匹配 5 秒内的帧，避免跨太大
    if abs(best[1] - target_seconds) <= 5:
        return best[0]
    return None


def _parse_mmss(mmss: str) -> float:
    """MM:SS → 秒数"""
    parts = mmss.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0.0


def _parse_ai_response(text: str) -> list[dict]:
    """从 AI 输出中提取 JSON，兼容 ```json...``` 包裹"""
    try:
        data = json.loads(text)
        return data.get("selected", [])
    except (json.JSONDecodeError, ValueError):
        pass

    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            return data.get("selected", [])
        except (json.JSONDecodeError, ValueError):
            pass

    return []


def _call_llm(
    prompt: str, provider_config: dict, timeout: int = 120
) -> str:
    """调用 LLM，返回文本响应"""
    import requests

    base_url = provider_config.get("base_url", "").rstrip("/")
    api_key = provider_config.get("api_key", "")
    model = provider_config.get("model", "")

    url = base_url + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def select_frames(
    transcript_path: str,
    frames_dir: str,
    output_dir: str,
    provider_config: dict,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> dict:
    """
    AI 智能选帧：根据转录文本语义选出值得视觉分析的关键帧。

    Args:
        transcript_path: transcript.json 路径
        frames_dir: frames/ 全量帧目录
        output_dir: 项目目录 (key_frames/ 在其下)
        provider_config: {"base_url", "api_key", "model"}
        progress_cb: 进度回调 (0.0 ~ 1.0)

    Returns:
        {"total", "selected", "output", "selections": [{timestamp, reason, file}]}
    """
    if progress_cb:
        progress_cb(0.05)

    # 1. 读取转录
    with open(transcript_path, "r", encoding="utf-8") as f:
        tdata = json.load(f)
    segments = tdata.get("segments", [])
    if not segments:
        raise ValueError("转录文件中没有 segments 数据")

    if progress_cb:
        progress_cb(0.1)

    # 2. 扫描可用帧（先补一轮 _tmp_ → MM_SS_frame.jpg 重命名）
    _ensure_renamed_frames(frames_dir)
    available = _build_available_frames_list(frames_dir)
    if not available:
        raise ValueError(f"帧目录为空: {frames_dir}")

    if progress_cb:
        progress_cb(0.15)

    # 3. 构建可用帧时间列表（压缩显示，每 30 秒一个采样点 + 所有整分钟）
    total_seconds = int(available[-1][1])
    frame_timestamps = [f"{fn}: {ts}" for fn, ts in available]
    # 如果帧数太多，只列关键时间点
    if len(available) > 300:
        # 列出每个帧的 MM:SS，用逗号分隔，每行 20 个
        mmss_list = [_seconds_to_mmss(ts) for _, ts in available]
        lines = []
        for i in range(0, len(mmss_list), 20):
            lines.append(", ".join(mmss_list[i : i + 20]))
        frames_str = "\n".join(lines)
    else:
        frames_str = ", ".join(
            _seconds_to_mmss(ts) for _, ts in available
        )

    # 4. 转录文本
    transcript_text = _build_transcript_text(segments)

    # 5. 长转录分片处理
    max_chunk_chars = 30000
    if len(transcript_text) <= max_chunk_chars:
        # 单次调用
        prompt = FRAME_SELECTION_PROMPT.format(
            transcript_text=transcript_text,
            available_frames=frames_str,
        )
        if progress_cb:
            progress_cb(0.2)

        ai_response = _call_llm(prompt, provider_config)
        selections = _parse_ai_response(ai_response)
    else:
        # 分片：按 10 分钟切片转录，分别选帧后合并
        chunk_seconds = 600
        all_selections = []
        chunks = []
        buf_segments = []
        buf_start = segments[0].get("start", 0)

        for seg in segments:
            seg_start = seg.get("start", 0)
            if seg_start - buf_start >= chunk_seconds and buf_segments:
                chunks.append(buf_segments)
                buf_segments = [seg]
                buf_start = seg_start
            else:
                buf_segments.append(seg)
        if buf_segments:
            chunks.append(buf_segments)

        for ci, chunk_segs in enumerate(chunks):
            chunk_text = _build_transcript_text(chunk_segs)
            chunk_start = chunk_segs[0].get("start", 0)
            chunk_end = chunk_segs[-1].get("end", 0)

            chunk_available = [
                (fn, ts) for fn, ts in available
                if chunk_start <= ts <= chunk_end + 5
            ]
            if not chunk_available:
                continue

            chunk_frames_str = ", ".join(
                _seconds_to_mmss(ts) for _, ts in chunk_available
            )

            prompt = FRAME_SELECTION_PROMPT.format(
                transcript_text=chunk_text,
                available_frames=chunk_frames_str,
            )
            ai_response = _call_llm(prompt, provider_config)
            chunk_selections = _parse_ai_response(ai_response)
            all_selections.extend(chunk_selections)

            if progress_cb:
                progress_cb(0.2 + 0.6 * (ci + 1) / len(chunks))

        selections = all_selections

    if progress_cb:
        progress_cb(0.85)

    # 6. 匹配帧文件
    matched = []
    for sel in selections:
        ts_str = sel.get("timestamp", "")
        target_sec = _parse_mmss(ts_str)
        if target_sec <= 0:
            continue
        filename = _find_nearest_frame(target_sec, available)
        if filename:
            matched.append({
                "timestamp": ts_str,
                "reason": sel.get("reason", ""),
                "file": filename,
                "matched_seconds": target_sec,
            })

    # 去重（可能多个 selection 匹配到同一帧）
    seen_files = set()
    unique_matched = []
    for m in matched:
        if m["file"] not in seen_files:
            seen_files.add(m["file"])
            unique_matched.append(m)

    # 按时间排序
    unique_matched.sort(key=lambda x: x["matched_seconds"])

    # 0 帧回退：每 2 分钟保留 1 帧，确保至少有视觉素材
    if not unique_matched and available:
        interval = 120  # 2 分钟
        fallback_ts = 0.0
        while fallback_ts <= available[-1][1]:
            fn = _find_nearest_frame(fallback_ts, available)
            if fn:
                unique_matched.append({
                    "timestamp": _seconds_to_mmss(fallback_ts),
                    "reason": "回退采样（AI 未选出关键帧）",
                    "file": fn,
                    "matched_seconds": fallback_ts,
                })
            fallback_ts += interval

    if progress_cb:
        progress_cb(0.9)

    # 7. 复制到 key_frames/
    key_frames_dir = os.path.join(output_dir, "key_frames")
    if os.path.exists(key_frames_dir):
        shutil.rmtree(key_frames_dir)
    os.makedirs(key_frames_dir)

    for m in unique_matched:
        src = os.path.join(frames_dir, m["file"])
        dst = os.path.join(key_frames_dir, m["file"])
        shutil.copy2(src, dst)

    if progress_cb:
        progress_cb(1.0)

    return {
        "total": len(available),
        "selected": len(unique_matched),
        "output": key_frames_dir,
        "selections": unique_matched,
    }
