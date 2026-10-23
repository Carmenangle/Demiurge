"""液态玻璃壁纸上传：视频后缀必须保留，未知后缀回落 png。"""
import asyncio
import io
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from app.routers import user_state


def _upload(tmp_path: Path, filename: str, monkeypatch) -> Path:
    monkeypatch.setattr(user_state, "DATA_DIR", tmp_path)
    upload = UploadFile(file=io.BytesIO(b"0123456789"), filename=filename)
    result = asyncio.run(user_state.upload_bg(upload))
    assert result["ok"] is True
    return Path(result["path"])


@pytest.mark.parametrize("ext", ["mp4", "webm", "mov", "m4v", "ogv", "png", "webp"])
def test_视频与图片后缀原样保留(tmp_path, monkeypatch, ext):
    saved = _upload(tmp_path, f"wallpaper.{ext}", monkeypatch)

    assert saved.suffix == f".{ext}"
    assert saved.parent == tmp_path / "backgrounds"
    assert saved.read_bytes() == b"0123456789"


def test_未知后缀回落png(tmp_path, monkeypatch):
    saved = _upload(tmp_path, "payload.exe", monkeypatch)

    assert saved.suffix == ".png"
    assert saved.read_bytes() == b"0123456789"
