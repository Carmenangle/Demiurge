"""全局模式分镜成片：按 shot 顺序拼接多支视频（ffmpeg，2026-11-09）。

输入是前端槽位的 local-view URL（已由 `generation_store.finalize_workflow_batch`
落进 `<repo>/videos/`）。本服务只做「按顺序拼接」：
- 各段宽高/fps/codec 一致 ⇒ concat demuxer `-c copy`（快、无损）；
- 不一致 ⇒ 以第一段为基准重编码（libx264 / yuv420p / 固定 fps）；
- 音频暂统一剔除（`-an`）：分镜音轨是否一致无法在拼接层可靠判定，TTS 整段
  混入另开一刀；warnings 明说，不静默假装带音轨。
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from app.services import image_store, repo_meta
from app.services.audio_merge import find_ffmpeg

MAX_CLIPS = 20
_LOCK = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}

_VIDEO_LINE_RE = re.compile(
    r"Video:\s+([^,\n]+).*?([0-9]+x[0-9]+).*?([0-9.]+)\s+fps",
    re.I,
)
_DURATION_RE = re.compile(r"Duration:\s+([0-9:.]+)")


def _lock_for(key: str) -> threading.Lock:
    with _LOCK:
        return _LOCKS.setdefault(key, threading.Lock())


def local_path_from_media_url(value: str) -> Path | None:
    """local-view URL / 裸本地路径 → Path；远程 URL / 非法形态返回 None。"""
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("/api/comfyui/local-view"):
        parsed = urlparse(raw)
        paths = parse_qs(parsed.query).get("path")
        if not paths:
            return None
        path = Path(unquote(paths[0]))
    elif "://" in raw:
        return None
    else:
        path = Path(raw)
    return path if path.is_file() else None


def _probe(ffmpeg: str, path: Path) -> dict[str, Any]:
    """ffmpeg -i 解析宽高/fps/时长（不依赖 ffprobe）。"""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    video = _VIDEO_LINE_RE.search(text)
    duration = _DURATION_RE.search(text)
    width = height = 0
    fps = 0.0
    codec = ""
    if video:
        codec = video.group(1).strip().split(" ")[0]
        size = video.group(2).lower()
        width, height = (int(part) for part in size.split("x", 1))
        fps = float(video.group(3))
    seconds = 0.0
    if duration:
        parts = duration.group(1).split(":")
        try:
            seconds = sum(float(part) * (60 ** (len(parts) - idx - 1))
                          for idx, part in enumerate(parts))
        except ValueError:
            seconds = 0.0
    return {
        "codec": codec, "width": width, "height": height,
        "fps": fps, "duration": round(seconds, 3),
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value or "").strip("._") or "sequence"


def merge_videos(
    urls: list[str], *, output_dir: str, repo_id: str, sequence_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """按给定顺序拼接；返回 `{ok,url,path,duration,width,height,warnings}`。"""
    clips = [str(url or "").strip() for url in urls if str(url or "").strip()]
    if not clips:
        return {"ok": False, "error": "没有可拼接的视频"}
    if len(clips) > MAX_CLIPS:
        return {"ok": False, "error": f"分镜段数 {len(clips)} 超过上限 {MAX_CLIPS}"}
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"ok": False, "error": "未找到 ffmpeg，无法拼接成片"}
    paths: list[Path] = []
    for url in clips:
        path = local_path_from_media_url(url)
        if path is None:
            return {"ok": False, "error": f"不是本地成片地址：{url[:120]}"}
        paths.append(path)
    if not output_dir:
        return {"ok": False, "error": "缺少 output_dir"}
    out_dir = image_store.media_dir(repo_meta.repo_folder(output_dir, repo_id), "video")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"shot_sequence_{_safe_name(sequence_id)}.mp4"
    if output.is_file() and not force:
        return {
            "ok": True, "url": _local_view(output), "path": str(output),
            "duration": 0, "width": 0, "height": 0,
            "warnings": ["已存在同一 sequence_id 的成片，直接复用（force=True 可覆盖）"],
        }
    if not paths:
        return {"ok": False, "error": "没有本地视频片段"}
    with _lock_for(str(output)):
        if output.is_file() and not force:
            return {"ok": True, "url": _local_view(output), "path": str(output),
                    "duration": 0, "width": 0, "height": 0, "warnings": []}
        probes = [_probe(ffmpeg, path) for path in paths]
        first = probes[0]
        same = all(
            item["codec"] == first["codec"]
            and item["width"] == first["width"]
            and item["height"] == first["height"]
            and abs(item["fps"] - first["fps"]) < 0.01
            for item in probes
        )
        warnings: list[str] = []
        if not same:
            warnings.append("分镜段宽高/fps/codec 不一致：已以第一段为基准重编码")
        if first["width"] <= 0 or first["height"] <= 0:
            return {"ok": False, "error": "无法解析第一段视频的宽高"}
        with tempfile.TemporaryDirectory() as tmp:
            list_file = Path(tmp) / "concat.txt"
            list_file.write_text("\n".join(
                "file '%s'" % str(path).replace("'", "'\\''") for path in paths
            ) + "\n", encoding="utf-8")
            cmd = [ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
                   "-i", str(list_file)]
            if same:
                cmd += ["-c", "copy"]
            else:
                cmd += [
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-vf", f"scale={first['width']}:{first['height']}",
                ]
                if first["fps"] > 0:
                    cmd += ["-r", str(int(round(first["fps"])))]
            cmd += ["-an", str(output)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if proc.returncode != 0 or not output.is_file():
                return {
                    "ok": False,
                    "error": f"ffmpeg 拼接失败：{(proc.stderr or '')[-500:]}",
                    "warnings": warnings,
                }
        merged = _probe(ffmpeg, output)
        warnings.append("成片暂不带音轨（-an）：分镜音轨一致性无法在拼接层可靠判定")
        return {
            "ok": True, "url": _local_view(output), "path": str(output),
            "duration": merged["duration"], "width": merged["width"],
            "height": merged["height"], "warnings": warnings,
        }


def _local_view(path: Path) -> str:
    from app.services import view_urls

    return view_urls.local_view(str(path))
