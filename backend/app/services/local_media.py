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
        }
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
        "mkv": "video/x-matroska",
        "gif": "image/gif",
    }
    if ext in videos:
        return videos[ext]
    return f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext or 'png'}"


def open_local_media(path: str, range_header: str | None = None) -> LocalMedia:
    if not is_media_file(path):
        raise LocalMediaError(403, "仅允许访问图片/视频文件")

    media_path = Path(path)
    if not media_path.is_file():
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
