"""音频分条按顺序拼接（自动配音完整版）。

多段台词分别生成 flac 后，按 seq 顺序无损 concat 成一段完整音频，
落回作品目录并追加为消息的 ready part（刷新后仍在）。
ffmpeg 优先取系统 PATH，其次取 ComfyUI 自带的 imageio_ffmpeg 二进制。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.services import chat_snapshot, comfy_launcher, view_urls

_LOG = logging.getLogger(__name__)

_AUDIO_EXTS = {".flac", ".wav", ".mp3", ".ogg", ".m4a", ".opus", ".aac"}
_MERGED_SLOT_PREFIX = "merged-"


def find_ffmpeg() -> str | None:
    """定位 ffmpeg：PATH → ComfyUI 自带 imageio_ffmpeg 二进制。

    注意：便携版 ComfyUI 的 ffmpeg 装在 python 虚拟环境
    （<根>/python/Lib/site-packages/imageio_ffmpeg/binaries/），与 ComfyUI 主目录
    （<根>/ComfyUI，即 config.path）平级，故需同时搜索 config.path 与其父目录。
    """
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        cfg = comfy_launcher.load_config()
        root = Path(str(cfg.get("path") or ""))
    except Exception:  # noqa: BLE001  配置缺失时按无 ComfyUI 路径处理
        root = Path("")
    # 去重候选根：ComfyUI 主目录 + 其父目录（python 虚拟环境所在层）
    candidates: list[Path] = []
    for base in (root, root.parent):
        if base.is_dir() and base not in candidates:
            candidates.append(base)
    for base in candidates:
        hits = sorted(base.glob("**/imageio_ffmpeg/binaries/ffmpeg*.exe"))
        if hits:
            return str(hits[0])
    return None


def local_path_from_url(url: str) -> str | None:
    """从 local-view URL 解析本地绝对路径；非 local-view 或不可用返回 None。"""
    try:
        parsed = urlparse(url)
        if "/api/comfyui/local-view" not in parsed.path:
            return None
        values = parse_qs(parsed.query).get("path")
        if not values:
            return None
        path = unquote(values[0])
        p = Path(path)
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS:
            return str(p)
    except Exception:  # noqa: BLE001
        return None
    return None


def concat_audio(paths: list[str], output: Path, ffmpeg: str | None = None) -> None:
    """按顺序拼接音频文件并重编码为 flac。

    为什么不能 -c copy：flac 文件头的 STREAMINFO 记录总采样数，无损 copy 拼接时
    输出只会保留第一段的采样数 → 播放器播完第一段时长（如 2s）就停，虽然后续
    数据仍在。重编码（flac→flac 无损）会重新计算 STREAMINFO，时长才正确。
    """
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        raise RuntimeError("未找到 ffmpeg：请安装 ffmpeg 或配置 ComfyUI 路径")
    with tempfile.TemporaryDirectory() as tmp:
        list_file = Path(tmp) / "concat.txt"
        lines = []
        for p in paths:
            # concat demuxer 路径用正斜杠；单引号转义为 '\''
            escaped = str(Path(p)).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd = [exe, "-y", "-f", "concat", "-safe", "0",
               "-i", str(list_file), "-c:a", "flac", str(output)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg 拼接失败（{proc.returncode}）：{(proc.stderr or '').strip()[:500]}"
            )
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("ffmpeg 拼接后产物为空")


def merge_audio_for_message(thread_id: str, message_id: str, *, force: bool = False) -> str:
    """把一条消息的音频分条（按 seq）拼接成完整版，落盘并返回 local-view URL。

    幂等：快照里已有 merged- 开头的 ready part 时直接返回其 URL；force=True
    时跳过幂等、覆盖旧结果（用于修复早前 -c copy 导致的时长错误产物）。
    """
    if not thread_id or not message_id:
        raise ValueError("thread_id 与 message_id 不能为空")
    items = chat_snapshot.load(thread_id)
    message = next((m for m in items if isinstance(m, dict) and m.get("id") == message_id), None)
    if message is None:
        raise ValueError("消息不存在")
    parts = [p for p in (message.get("parts") or [])
             if isinstance(p, dict) and p.get("type") == "audio" and p.get("url")]
    existing = next((p for p in parts
                     if str(p.get("slotId") or "").startswith(_MERGED_SLOT_PREFIX)), None)
    if existing and existing.get("url") and not force:
        return str(existing["url"])

    def _seq(part: dict) -> int:
        value = part.get("seq")
        return value if isinstance(value, int) else 0

    paths: list[str] = []
    for part in sorted(parts, key=_seq):
        path = local_path_from_url(str(part.get("url") or ""))
        if path:
            paths.append(path)
    if len(paths) < 2:
        raise ValueError(f"可合并的音频段不足（需要 ≥2 段，当前 {len(paths)} 段）")
    # 输出落在分条文件所在目录（作品目录），与 save_local 同一位置
    target_dir = Path(paths[0]).parent
    output = target_dir / f"merged_{message_id[:8]}_{uuid.uuid4().hex[:8]}.flac"
    try:
        concat_audio(paths, output)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("音频拼接失败 thread=%s mid=%s: %s", thread_id, message_id, exc)
        raise ValueError(f"音频拼接失败：{exc}") from exc

    url = view_urls.local_view(str(output))
    merged_part = {
        "type": "audio", "url": url,
        "slotId": f"{_MERGED_SLOT_PREFIX}{message_id}",
        "status": "ready", "kind": "audio", "speaker": "完整版",
    }
    if force:
        # 覆盖旧结果：先移除旧的 merged part，再写入新的
        try:
            chat_snapshot.remove_parts_matching(
                thread_id, message_id,
                lambda p: str(p.get("slotId") or "").startswith(_MERGED_SLOT_PREFIX),
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("移除旧完整版失败 thread=%s mid=%s: %s", thread_id, message_id, exc)
    try:
        chat_snapshot.append_ready_part(thread_id, message_id, merged_part)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("合并音频回写快照失败 thread=%s mid=%s: %s", thread_id, message_id, exc)
    # 合并成功后移除分条音频，只保留完整版（文本/图片/视频等其余 part 不动），
    # 避免对话里既留一堆分条播放器又占空间。
    try:
        chat_snapshot.remove_parts_matching(
            thread_id, message_id,
            lambda p: p.get("type") == "audio"
            and not str(p.get("slotId") or "").startswith(_MERGED_SLOT_PREFIX),
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("移除分条音频失败 thread=%s mid=%s: %s", thread_id, message_id, exc)
    return url
