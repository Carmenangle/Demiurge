"""从图片提取调色盘 + 按调色盘量化。纯 Pillow。

量化交给 Pillow 的 MEDIANCUT/FASTOCTREE，不自己写 —— 手写 median cut 约百行
且更慢。RGB565 这类降位在量化前做，属于独立的一步。
"""
from __future__ import annotations

import io

from PIL import Image

MAX_COLORS = 256

# 降位档位：名字 -> 每通道保留位数。RGB565 是 GBA/复古屏的常见格式，
# 绿色多一位（人眼对绿最敏感）。
BIT_DEPTHS: dict[str, tuple[int, int, int]] = {
    "rgb888": (8, 8, 8),
    "rgb565": (5, 6, 5),
    "rgb555": (5, 5, 5),
    "rgb444": (4, 4, 4),
    "rgb332": (3, 3, 2),
}


def _snap_channel(v: int, bits: int) -> int:
    """把单通道值吸附到该位深真实可表示的档位上。

    降位只在量化「之前」做是不够的：quantize() 会在色箱里取平均值当代表色，
    结果又漂回网格之外 —— rgb565 和 rgb888 只差 1~3，用户肉眼看不出区别，
    且给出的色号并不是 RGB565 真能表示的值。所以量化「之后」还要再吸附一次。
    """
    if bits >= 8:
        return max(0, min(255, v))
    levels = (1 << bits) - 1
    return round(round(v * levels / 255) * 255 / levels)


def _reduce_bits(img: Image.Image, depth: tuple[int, int, int]) -> Image.Image:
    """按每通道位数降位。

    降位要把量化后的值撑回满量程（乘 255/max），否则整张图会偏暗 ——
    比如 5 位只右移 3 位的话最大值只到 248。
    """
    if depth == (8, 8, 8):
        return img
    rgb = img.convert("RGB")
    out = []
    for chan, bits in zip(rgb.split(), depth, strict=True):
        levels = (1 << bits) - 1
        shift = 8 - bits
        out.append(chan.point(
            lambda v, s=shift, lv=levels: round((v >> s) * 255 / lv)))
    return Image.merge("RGB", out)


def extract(data: bytes, max_colors: int = 16, bit_depth: str = "rgb888",
            method: str = "mediancut") -> tuple[list[str], Image.Image]:
    """图片字节 → (hex 色值列表, 量化后的预览图)。

    色值按在图中的占比从多到少排 —— 用户要的「主色」是面积最大的那个，
    而 getpalette() 的顺序不代表占比。
    """
    n = max(1, min(int(max_colors), MAX_COLORS))
    depth = BIT_DEPTHS.get(bit_depth, BIT_DEPTHS["rgb888"])
    src = Image.open(io.BytesIO(data))
    # 有 alpha 的图先合到白底：透明像素参与量化会引入一个假的「黑色」主色
    if src.mode in ("RGBA", "LA", "P"):
        src = src.convert("RGBA")
        flat = Image.new("RGBA", src.size, (255, 255, 255, 255))
        flat.alpha_composite(src)
        src = flat.convert("RGB")
    else:
        src = src.convert("RGB")
    src = _reduce_bits(src, depth)
    algo = (Image.Quantize.FASTOCTREE if method == "octree"
            else Image.Quantize.MEDIANCUT)
    quant = src.quantize(colors=n, method=algo)
    pal = quant.getpalette() or []
    # 量化后再吸附一次，让色号落在该位深真能表示的值上（见 _snap_channel）
    snapped = list(pal)
    for i in range(0, len(snapped) - 2, 3):
        for off, bits in enumerate(depth):
            snapped[i + off] = _snap_channel(snapped[i + off], bits)
    quant.putpalette(snapped)
    # color_counts: [(count, index), ...]，据此按占比排序
    counts = sorted(quant.getcolors(maxcolors=MAX_COLORS * 2) or [], reverse=True)
    # 吸附会让相邻色并成同一个值：按占比合并，否则调色盘里出现重复色块
    merged: dict[str, int] = {}
    for cnt, idx in counts:
        base = idx * 3
        if base + 2 >= len(snapped):
            continue
        hx = "#{:02x}{:02x}{:02x}".format(*snapped[base:base + 3])
        merged[hx] = merged.get(hx, 0) + cnt
    hexes = [hx for hx, _ in sorted(merged.items(), key=lambda kv: -kv[1])]
    return hexes, quant.convert("RGB")


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
