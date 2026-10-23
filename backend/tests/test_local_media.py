import pytest

from app.services.local_media import LocalMediaError, open_local_media


def test_完整媒体返回文件元数据(tmp_path):
    path = tmp_path / "sample.png"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.partial is False
    assert media.media_type == "image/png"
    assert media.headers["Accept-Ranges"] == "bytes"
    assert media.headers["Content-Length"] == "6"
    # ⚠ 必须带 no-cache：角色 LoRA 落盘是「同名覆盖、URL 不变」，缺这条浏览器按启发式
    #   新鲜期缓存 ⇒ 重新生图后界面仍显示旧图、要手动刷新才更新（2026-09-24 用户实锤）。
    assert media.headers["Cache-Control"] == "no-cache"


def test_range读取只返回指定区间(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path), "bytes=1-3")

    assert media.partial is True
    assert media.media_type == "video/mp4"
    assert media.headers["Content-Range"] == "bytes 1-3/6"
    assert media.headers["Cache-Control"] == "no-cache"
    assert b"".join(media.iter_bytes(chunk_size=2)) == b"bcd"


@pytest.mark.parametrize(
    ("name", "media_type"),
    [("clip.m4v", "video/x-m4v"), ("clip.ogv", "video/ogg")],
)
def test_补充视频容器返回正确media_type(tmp_path, name, media_type):
    path = tmp_path / name
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.media_type == media_type
    assert media.headers["Accept-Ranges"] == "bytes"


def test_音频返回audio_media_type(tmp_path):
    path = tmp_path / "voice.wav"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.partial is False
    assert media.media_type == "audio/wav"
    assert media.headers["Accept-Ranges"] == "bytes"
    assert media.headers["Content-Length"] == "6"
    assert media.headers["Cache-Control"] == "no-cache"


def test_音频range读取(tmp_path):
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path), "bytes=2-4")

    assert media.partial is True
    assert media.media_type == "audio/mpeg"
    assert media.headers["Content-Range"] == "bytes 2-4/6"
    assert b"".join(media.iter_bytes()) == b"cde"


@pytest.mark.parametrize(
    ("name", "header", "status"),
    [("secret.txt", None, 403), ("missing.png", None, 404), ("sample.png", "bytes=5-1", 416)],
)
def test_拒绝非法本地媒体请求(tmp_path, name, header, status):
    path = tmp_path / name
    if name == "sample.png":
        path.write_bytes(b"abcdef")

    with pytest.raises(LocalMediaError) as exc_info:
        open_local_media(str(path), header)

    assert exc_info.value.status == status


def test_路由层把no_cache透传给媒体响应(tmp_path):
    """路由级锁：`Cache-Control: no-cache` 必须真正出现在 HTTP 响应头里。

    直测路由函数（仓库惯例，不起 TestClient）：完整文件走 `FileResponse`（Starlette 会
    另行补 ETag/Last-Modified，两者组合＝「每次协商、覆盖即拿新图」）。
    """
    from app.routers.comfyui import local_view

    class _FakeRequest:
        headers: dict[str, str] = {}

    path = tmp_path / "shot.png"
    path.write_bytes(b"abcdef")

    response = local_view(str(path), _FakeRequest())

    cache = (response.headers.get("cache-control")
             or response.headers.get("Cache-Control"))
    assert cache == "no-cache"
