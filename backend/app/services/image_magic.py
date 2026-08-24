"""图片魔数校验：验证字节流确实是声明的图片格式，防文件伪装攻击。

用法：
    from app.services.image_magic import detect_image_format, validate_image_bytes

    fmt = detect_image_format(data)          # → "png" | "jpeg" | "webp" | "gif" | "bmp" | None
    validate_image_bytes(data, "png")        # 不是 PNG 则抛 ValueError

格式覆盖 M1.3 安全链要求的全部图片类型（png/jpg/webp/gif/bmp/avif）。
不依赖第三方库，纯字节比较，可单测。
"""
from __future__ import annotations

# 每种格式： (signature_bytes, offset, extension)
# offset 用于 RIFF 容器（WebP）需要跳过前 8 字节后再匹配
_MAGIC_TABLE: list[tuple[bytes, int, str]] = [
    # PNG
    (b"\x89PNG\r\n\x1a\n", 0, "png"),
    # JPEG
    (b"\xff\xd8\xff", 0, "jpeg"),
    # WebP：需同时校验 RIFF 容器头（offset 0-3）与 WEBP 四字节（offset 8-11），
    # 防止前 8 字节可伪造、仅 offset 8 匹配 WEBP 的假阳性。
    (b"WEBP", 8, "webp"),
    # GIF87a / GIF89a
    (b"GIF8", 0, "gif"),
    # BMP
    (b"BM", 0, "bmp"),
    # AVIF (ftypavif box)
    (b"ftypavif", 4, "avif"),
    # HEIF/HEIC (ftypheic box) — 备用
    (b"ftypheic", 4, "heic"),
    # TIFF (little-endian / big-endian)
    (b"II*\x00", 0, "tiff"),
    (b"MM\x00*", 0, "tiff"),
]

IMAGE_EXTENSION_ALIASES: dict[str, str] = {
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
    "webp": "webp",
    "gif": "gif",
    "bmp": "bmp",
    "avif": "avif",
    "heic": "heic",
    "tiff": "tiff",
}


def detect_image_format(data: bytes) -> str | None:
    """通过文件头魔数检测图片格式，返回扩展名（如 'png'），不识别则返回 None。

    WebP 额外校验：必须同时满足 RIFF 容器头（offset 0-3）和 WEBP 四字节（offset 8-11），
    防止仅 offset 8 匹配 WEBP 的伪造文件。
    """
    if len(data) < 16:
        return None
    for sig, offset, ext in _MAGIC_TABLE:
        end = offset + len(sig)
        if len(data) >= end and data[offset:end] == sig:
            # WebP 额外校验 RIFF 容器头
            if ext == "webp" and data[0:4] != b"RIFF":
                continue
            return ext
    return None


def validate_image_bytes(data: bytes, expected_ext: str = "") -> str:
    """验证字节流是合法图片格式，返回检测到的扩展名。

    若 expected_ext 非空，额外校验检测结果与声明一致。
    不通过抛 ValueError。
    """
    detected = detect_image_format(data)
    if not detected:
        raise ValueError(
            "文件头魔数校验失败：无法识别为已知图片格式（png/jpg/webp/gif/bmp/avif）"
        )
    if expected_ext:
        expected = IMAGE_EXTENSION_ALIASES.get(expected_ext.lower(), expected_ext.lower())
        if detected != expected and not (detected == "jpeg" and expected == "jpg"):
            raise ValueError(
                f"扩展名与文件头不匹配：声明为 .{expected_ext}，实际检测为 {detected}"
            )
    return detected