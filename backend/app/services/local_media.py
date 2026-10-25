"""本地媒体读取：文件校验、类型识别与 HTTP Range 计算。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.services.url_guard import is_media_file


class LocalMediaError(ValueError):
    def __init__(self, status: int, detail: str, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.headers = headers or {}


@dataclass(frozen=True)
class LocalMedia:
    path: Path
    media_type: str
    file_size: int
    start: int | None = None
    end: int | None = None

    @property
    def partial(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def content_length(self) -> int:
        if self.partial:
            return self.end - self.start + 1  # type: ignore[operator]
        return self.file_size

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(self.content_length),
            # ⚠ 角色 LoRA 落盘用「角色_轮次_序号」**同名覆盖**（image_store.save_image_named：
            #   「同一槽位重新生成时覆盖同名文件 → URL 不变、内容更新」）⇒ 重新生图后
            #   `<img src>` 不变。不带缓存指令时浏览器按**启发式新鲜期**（≈距今的 10%）缓存，
            #   旧图能吃上几小时 ⇒ 界面显示重生成**前**的图、要手动刷新才更新
            #   （2026-09-24 用户实锤）。`no-cache`＝允许缓存但**每次都必须协商**：
            #   文件被覆盖 ⇒ mtime/size 变 ⇒ ETag 变 ⇒ 浏览器拿到新字节；未变 ⇒ 304（零流量）。
            #   ⚠ 不能用 `no-store`：整个资产库/头像/背景都走本端点，会变成每次渲染全量重拉。
            "Cache-Control": "no-cache",
            # ⚠ 内容型安全（2026-10-24 审计改进项 2）：白名单含 .svg 且远程 svg 可经
            #   image_proxy（content-type image/* 即放行）落盘，直接导航到一个恶意 svg 的
            #   local-view URL ＝ 脚本在应用同源（回环端口）执行。nosniff 全量加杜绝
            #   浏览器对媒体响应做内容嗅探；svg 另加 CSP sandbox：直航时禁脚本/表单、
            #   静态图形照常渲染（<img>/<video> 引用不受影响——媒体元素上下文本就不执行脚本）。
            "X-Content-Type-Options": "nosniff",
        }
        if self.path.suffix.lower() == ".svg":
            headers["Content-Security-Policy"] = "sandbox"
        if self.partial:
            headers["Content-Range"] = f"bytes {self.start}-{self.end}/{self.file_size}"
        return headers

    def iter_bytes(self, chunk_size: int = 65536) -> Iterator[bytes]:
        if not self.partial:
            raise RuntimeError("完整文件应交给文件响应处理")
        remaining = self.content_length
        with self.path.open("rb") as file:
            file.seek(self.start or 0)
            while remaining > 0:
                data = file.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data


def _media_type(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    videos = {
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mov": "video/quicktime",
        "m4v": "video/x-m4v",
        "ogv": "video/ogg",
        "mkv": "video/x-matroska",
        "gif": "image/gif",
    }
    if ext in videos:
        return videos[ext]
    audios = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "oga": "audio/ogg",
        "opus": "audio/ogg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "wma": "audio/x-ms-wma",
    }
    if ext in audios:
        return audios[ext]
    images = {
        # 显式映射：兜底 image/<ext> 对 svg 产出的是非标准 image/svg（笔误级），浏览器会
        # 内容嗅探；标准 image/svg+xml 配合 local-view 响应头 CSP sandbox 才构成完整防线。
        "svg": "image/svg+xml",
    }
    if ext in images:
        return images[ext]
    return f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext or 'png'}"


def open_local_media(path: str, range_header: str | None = None) -> LocalMedia:
    if not is_media_file(path):
        raise LocalMediaError(403, "仅允许访问图片/视频文件")

    media_path = Path(path)
    if not media_path.is_file():
        # 兼容回退（2026-10-17）：媒体产出已按类型迁到 <repo>/pictures|videos|audios/，
        # 但老 RAG 索引里记的仍是 <repo>/<name> ⇒ 原路径找不到就在同级类型夹里找同名文件。
        # 这样迁移旧图/旧视频不会导致画廊裂图（索引 B 方案同步更新后也不会再走到这里）。
        for _sub in ("pictures", "videos", "audios"):
            alt = media_path.parent / _sub / media_path.name
            if alt.is_file():
                media_path = alt
                break
        else:
            raise LocalMediaError(404, "本地图片不存在")

    file_size = media_path.stat().st_size
    if not range_header:
        return LocalMedia(media_path, _media_type(media_path), file_size)

    try:
        byte_range = range_header.strip().removeprefix("bytes=")
        start_text, end_text = byte_range.split("-", 1)
        start = int(start_text) if start_text else 0
        end = int(end_text) if end_text else file_size - 1
    except (ValueError, AttributeError):
        raise LocalMediaError(416, "Range 头格式错误") from None

    if start < 0 or end >= file_size or start > end:
        raise LocalMediaError(
            416,
            "Range Not Satisfiable",
            {"Content-Range": f"bytes */{file_size}"},
        )
    return LocalMedia(media_path, _media_type(media_path), file_size, start, end)
