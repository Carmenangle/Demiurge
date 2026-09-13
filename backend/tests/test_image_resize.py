"""分辨率缩放：尺寸推算、格式、以及 Lanczos 保真度优于 nearest 的回归。"""
import io
import math

import pytest
from PIL import Image, ImageChops, ImageDraw

from app.services import image_resize


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _detailed(w=256, h=128):
    """混合频率图：渐变 + 圆 + 细线。纯色图无法区分滤镜好坏。"""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(y * 255 // h, 80, 255 - y * 255 // h))
    for i in range(5):
        r = 8 + i * 10
        d.ellipse([w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r],
                  outline=(255, 255, 255), width=2)
    for x in range(0, w, 7):
        d.line([(x, 0), (x + 20, h)], fill=(0, 255, 120), width=1)
    return img


def _rmse(a, b):
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    hist = diff.histogram()
    total = sum(hist[:256]) or 1
    per = [sum(v * v * hist[base + v] for v in range(256)) / total
           for base in (0, 256, 512)]
    return math.sqrt(sum(per) / 3)


# ---- 尺寸推算 ----

@pytest.mark.parametrize("args,want", [
    (dict(target_w=1024), (1024, 576)),                    # 只给宽
    (dict(target_h=576), (1024, 576)),                     # 只给高
    (dict(scale=0.5), (1024, 576)),                        # 比例
    (dict(target_w=1024, target_h=1024), (1024, 576)),     # 保比：装得下
    (dict(target_w=1024, target_h=1024, keep_aspect=False), (1024, 1024)),
    (dict(), (2048, 1152)),                                # 什么都不给 = 不变
])
def test_plan_size(args, want):
    assert image_resize.plan_size(2048, 1152, **args) == want


def test_plan_size_never_zero():
    """极端缩放不能算出 0 边长。"""
    w, h = image_resize.plan_size(2048, 1152, scale=0.0001)
    assert w >= 1 and h >= 1


def test_scale_beats_target():
    assert image_resize.plan_size(1000, 1000, target_w=10, scale=0.5) == (500, 500)


# ---- 缩放行为 ----

def test_downscale_dimensions_and_default_png():
    data, w, h, mime = image_resize.resize(_png(_detailed(512, 256)), target_w=256)
    assert (w, h) == (256, 128)
    assert mime == "image/png"
    assert Image.open(io.BytesIO(data)).size == (256, 128)


def test_lanczos_beats_nearest_on_fidelity():
    """滤镜默认值的依据：换成 nearest 保真度必须明显更差。"""
    src = _detailed(512, 256)
    raw = _png(src)
    best, _, _, _ = image_resize.resize(raw, target_w=256, filter_name="lanczos")
    worst, _, _, _ = image_resize.resize(raw, target_w=256, filter_name="nearest")
    up = Image.Resampling.LANCZOS
    e_best = _rmse(src, Image.open(io.BytesIO(best)).resize(src.size, up))
    e_worst = _rmse(src, Image.open(io.BytesIO(worst)).resize(src.size, up))
    assert e_best < e_worst


def test_alpha_preserved_in_png():
    img = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
    data, _, _, _ = image_resize.resize(_png(img), target_w=32)
    assert Image.open(io.BytesIO(data)).convert("RGBA").getpixel((0, 0))[3] == 0


def test_jpeg_flattens_alpha_to_white():
    """JPEG 不支持 alpha，透明区必须变白而不是变黑。"""
    img = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    data, _, _, mime = image_resize.resize(_png(img), target_w=32, fmt="jpeg")
    assert mime == "image/jpeg"
    r, g, b = Image.open(io.BytesIO(data)).convert("RGB").getpixel((5, 5))
    assert r > 240 and g > 240 and b > 240


def test_webp_format():
    data, _, _, mime = image_resize.resize(
        _png(_detailed(128, 64)), target_w=64, fmt="webp")
    assert mime == "image/webp"
    assert Image.open(io.BytesIO(data)).size == (64, 32)


def test_palette_image_does_not_shift_colors():
    """P 模式直接缩放会串色，必须先转真彩。"""
    p = Image.new("P", (64, 64))
    p.putpalette([255, 0, 0] + [0] * 765)
    data, _, _, _ = image_resize.resize(_png(p), target_w=32)
    assert Image.open(io.BytesIO(data)).convert("RGB").getpixel((5, 5)) == (255, 0, 0)


def test_sharpen_applies_without_error():
    data, w, h, _ = image_resize.resize(
        _png(_detailed(128, 64)), target_w=64, sharpen=60)
    assert (w, h) == (64, 32) and data


def test_rejects_oversized_target():
    with pytest.raises(ValueError, match="上限"):
        image_resize.resize(_png(_detailed(32, 32)), target_w=image_resize.MAX_SIDE + 1)


def test_probe_reads_dimensions():
    assert image_resize.probe(_png(_detailed(300, 200))) == (300, 200)


def test_unknown_filter_falls_back_to_lanczos():
    data, w, _, _ = image_resize.resize(
        _png(_detailed(128, 64)), target_w=64, filter_name="nope")
    assert w == 64 and data
