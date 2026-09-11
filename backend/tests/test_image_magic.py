"""魔数校验单测（M1.3 安全链）。"""
import pytest
from app.services.image_magic import detect_image_format, validate_image_bytes


# ---- 真实文件头（最小合法字节） ----

def test_png_magic():
    fmt = detect_image_format(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    assert fmt == "png"


def test_jpeg_magic():
    fmt = detect_image_format(b"\xff\xd8\xff\xe0" + b"\x00" * 12)
    assert fmt == "jpeg"


def test_webp_magic():
    fmt = detect_image_format(b"RIFF____WEBP" + b"\x00" * 4)
    assert fmt == "webp"


def test_gif87a_magic():
    fmt = detect_image_format(b"GIF87a" + b"\x00" * 10)
    assert fmt == "gif"


def test_gif89a_magic():
    fmt = detect_image_format(b"GIF89a" + b"\x00" * 10)
    assert fmt == "gif"


def test_bmp_magic():
    fmt = detect_image_format(b"BM" + b"\x00" * 14)
    assert fmt == "bmp"


def test_avif_magic():
    # ftypavif box at offset 4
    fmt = detect_image_format(b"\x00\x00\x00\x20ftypavif" + b"\x00" * 4)
    assert fmt == "avif"


# ---- 未知/非图片数据 ----

def test_unknown_magic():
    assert detect_image_format(b"Hello, World!!!!!") is None


def test_short_data():
    assert detect_image_format(b"\x89PNG") is None  # 不足 16 字节


def test_empty_bytes():
    assert detect_image_format(b"") is None


# ---- validate 正常路径 ----

def test_validate_pass():
    ext = validate_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12)
    assert ext == "jpeg"


def test_validate_pass_with_expected():
    ext = validate_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png")
    assert ext == "png"


# ---- validate 拒绝场景 ----

def test_validate_reject_unknown():
    with pytest.raises(ValueError, match="无法识别"):
        validate_image_bytes(b"not-an-image!!!!!!!!")


def test_validate_reject_extension_mismatch():
    with pytest.raises(ValueError, match="不匹配"):
        validate_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12, "png")


# ---- 扩展名别名 ----

def test_jpg_jpeg_alias():
    """jpg 和 jpeg 视为同一格式。"""
    ext = validate_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12, "jpg")
    assert ext == "jpeg"


def test_validate_detects_jpeg():
    ext = validate_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12)
    assert ext == "jpeg"


# ---- L1 WebP RIFF 容器头校验 ----

def test_webp_拒绝无RIFF头的伪造WEBP():
    """L1 修复：仅 offset 8 匹配 WEBP 但前 8 字节不是 RIFF → 拒绝。"""
    # 伪造：前 8 字节全零，仅 offset 8 是 WEBP
    assert detect_image_format(b"\x00" * 8 + b"WEBP" + b"\x00" * 4) is None


def test_webp_接受合法RIFF容器():
    """合法 WebP 文件：RIFF 头 + WEBP 四字节 → 通过。"""
    fmt = detect_image_format(b"RIFF____WEBP" + b"\x00" * 4)
    assert fmt == "webp"


def test_webp_拒绝部分正确的RIFF伪造():
    """前 4 字节不是 RIFF（如 'AAAA'）但 offset 8 是 WEBP → 拒绝。"""
    assert detect_image_format(b"AAAA____WEBP" + b"\x00" * 4) is None