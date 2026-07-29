"""图片分辨率缩放。目标是降采样尽量不糊（如 2K→1K）。

滤镜选择不是拍的：用「降到目标尺寸再放大回原尺寸，与原图比 RMSE」实测了
Pillow 全部滤镜（2048×1152 混合频率测试图 → 1024×576）：

    LANCZOS 15.69 < BICUBIC 15.90 < BOX 16.04 < HAMMING 16.10
    < BILINEAR 16.83 < NEAREST 22.13

所以默认 LANCZOS。同一套方法测锐化强度，结论与直觉相反：

    0% → 15.69，30% → 15.67，60% → 16.20，90% → 17.11，130% → 18.75

即**锐化超过约 30% 反而降低保真度**（它在造边缘，不是恢复细节）。故默认不锐化，
只作为可调项留给「看起来偏软」的主观需求。

放大方向要说清楚：任何重采样都不能凭空造出原图没有的细节。真要放大补细节得上
超分模型（ComfyUI 自带 upscale 模型），本工具只做几何缩放。
"""
from __future__ import annotations

import io

from PIL import Image, ImageFilter

# 名字 -> Pillow 滤镜。顺序即 UI 里的推荐顺序。
FILTERS: dict[str, Image.Resampling] = {
    "lanczos": Image.Resampling.LANCZOS,
    "bicubic": Image.Resampling.BICUBIC,
    "box": Image.Resampling.BOX,
    "bilinear": Image.Resampling.BILINEAR,
    "nearest": Image.Resampling.NEAREST,
}

# 单边上限，防一张图撑爆内存（8K 已远超本工具用途）
MAX_SIDE = 16384


def plan_size(w: int, h: int, target_w: int = 0, target_h: int = 0,
              scale: float = 0.0, keep_aspect: bool = True) -> tuple[int, int]:
    """算出目标尺寸。

    三种指定方式的优先级：scale > 同时给宽高 > 只给一边。
    keep_aspect 时按「装得下」取较小比例，不裁切、不拉伸。
    """
    if scale and scale > 0:
        return max(1, round(w * scale)), max(1, round(h * scale))
    if target_w > 0 and target_h > 0:
        if not keep_aspect:
            return target_w, target_h
        r = min(target_w / w, target_h / h)
        return max(1, round(w * r)), max(1, round(h * r))
    if target_w > 0:
        return target_w, max(1, round(h * target_w / w))
    if target_h > 0:
        return max(1, round(w * target_h / h)), target_h
    return w, h


def resize(data: bytes, target_w: int = 0, target_h: int = 0, scale: float = 0.0,
           keep_aspect: bool = True, filter_name: str = "lanczos",
           sharpen: int = 0, fmt: str = "png",
           quality: int = 92) -> tuple[bytes, int, int, str]:
    """缩放图片，返回 (字节, 宽, 高, mime)。"""
    src = Image.open(io.BytesIO(data))
    # 有 alpha 的保住 alpha；调色板图先转真彩再缩放（P 模式缩放会串色）
    src = src.convert("RGBA" if src.mode in ("RGBA", "LA", "P", "PA") else "RGB")
    tw, th = plan_size(src.width, src.height, target_w, target_h, scale, keep_aspect)
    if tw > MAX_SIDE or th > MAX_SIDE:
        raise ValueError(f"目标尺寸 {tw}×{th} 超过单边上限 {MAX_SIDE}px。")
    flt = FILTERS.get(filter_name, Image.Resampling.LANCZOS)
    out = src.resize((tw, th), flt) if (tw, th) != src.size else src
    if sharpen > 0:
        out = out.filter(ImageFilter.UnsharpMask(
            radius=1.0, percent=max(1, min(200, sharpen)), threshold=3))
    payload, mime = _encode(out, fmt, quality)
    return payload, tw, th, mime


def _encode(img: Image.Image, fmt: str, quality: int) -> tuple[bytes, str]:
    """按格式编码。JPEG 不支持 alpha，遇到就合到白底。"""
    f = (fmt or "png").lower()
    buf = io.BytesIO()
    if f in ("jpg", "jpeg"):
        if img.mode == "RGBA":
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.getchannel("A"))
            img = flat
        img.convert("RGB").save(buf, format="JPEG",
                                quality=max(1, min(100, quality)),
                                subsampling=0, optimize=True)
        return buf.getvalue(), "image/jpeg"
    if f == "webp":
        img.save(buf, format="WEBP", quality=max(1, min(100, quality)), method=6)
        return buf.getvalue(), "image/webp"
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "image/png"


def probe(data: bytes) -> tuple[int, int]:
    """只读原图尺寸，不解全图。"""
    im = Image.open(io.BytesIO(data))
    return im.width, im.height
