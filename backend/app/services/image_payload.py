"""前端上传图片的 data URI 解码 + 体积上限。gif_sprite / palette 两个工具共用。

走 JSON body 传 base64 而不是 multipart：前端 api/client.ts 只有 JSON helper，
且 RichInput 本来就把图片当 data URI 字符串存。代价是 base64 比原文件大约 1/3，
所以这里要显式限体积并给可读错误，否则用户传个大 GIF 只会看到裸的 413。
"""
from __future__ import annotations

import base64
import re

# 单张上限。精灵图可能很大（几十帧 × 每帧几百像素），16MB 原文件足够；
# base64 后约 21MB，仍在常规 body 限额内。
MAX_IMAGE_BYTES = 16 * 1024 * 1024


class PayloadError(ValueError):
    """入参问题（体积/格式），路由层映射成 400。"""


def decode_data_uri(src: str, field: str = "image") -> bytes:
    """`data:image/...;base64,xxx` 或裸 base64 → 原始字节。"""
    if not src or not src.strip():
        raise PayloadError(f"{field} 为空，请先选择图片。")
    payload = src.split(",", 1)[1] if src.startswith("data:") else src
    # 先按 base64 长度估算原始体积，避免先解出几百 MB 再判断
    approx = len(payload) * 3 // 4
    if approx > MAX_IMAGE_BYTES:
        raise PayloadError(
            f"图片太大（约 {approx / 1024 / 1024:.1f}MB），"
            f"上限 {MAX_IMAGE_BYTES // 1024 // 1024}MB。请先压缩或缩小尺寸。")
    try:
        data = base64.b64decode(re.sub(r"\s+", "", payload))
    except Exception as e:
        raise PayloadError(f"{field} 解码失败，可能不是有效的图片数据：{e}") from e
    if not data:
        raise PayloadError(f"{field} 解码后是空的。")
    return data


def to_data_uri(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
