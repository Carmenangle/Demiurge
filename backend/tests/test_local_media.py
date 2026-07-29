import pytest

from app.services.local_media import LocalMediaError, open_local_media


def test_完整媒体返回文件元数据(tmp_path):
    path = tmp_path / "sample.png"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.partial is False
    assert media.media_type == "image/png"
    assert media.headers == {"Accept-Ranges": "bytes", "Content-Length": "6"}


def test_range读取只返回指定区间(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path), "bytes=1-3")

    assert media.partial is True
    assert media.media_type == "video/mp4"
    assert media.headers["Content-Range"] == "bytes 1-3/6"
    assert b"".join(media.iter_bytes(chunk_size=2)) == b"bcd"


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
