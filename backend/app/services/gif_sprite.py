"""GIF ↔ 精灵图互转。纯 Pillow，无新依赖。

放后端而不放前端的原因：浏览器原生不能逐帧解 GIF（`<img>` 只会播放），
自己写 LZW 解码器成本远高于这里几十行。前端只做预览与交互。

GIF 透明是这个模块唯一的硬坑，见 encode_gif 的注释。
"""
from __future__ import annotations

import io

from PIL import Image, ImageSequence

# 透明索引固定占用 255，量化只用 0-254。GIF 每帧最多 256 色，
# 留一格给透明是标准做法。
_TRANSPARENT_INDEX = 255
_QUANTIZE_COLORS = 255

# 半透明像素在 GIF 里没有中间态，只能二选一。128 是常规阈值。
_ALPHA_CUTOFF = 128


def decode_gif(data: bytes) -> tuple[list[Image.Image], list[int]]:
    """GIF 字节 → (RGBA 帧列表, 每帧毫秒时长)。

    必须走 ImageSequence.Iterator + convert("RGBA")：真实 GIF 常只存变化区域
    （部分帧 + disposal），Pillow 在迭代时才会把前帧当底合成。直接 seek 取 tile
    会得到残缺画面。

    每帧时长要逐帧读 info —— `im.info["duration"]` 只反映当前 seek 到的那一帧。
    """
    im = Image.open(io.BytesIO(data))
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(im):
        frames.append(frame.convert("RGBA"))
        durations.append(int(frame.info.get("duration", 0)) or 100)
    if not frames:
        raise ValueError("这个文件里没有可用帧，可能不是有效的 GIF。")
    return frames, durations


def encode_gif(frames: list[Image.Image], duration_ms: int = 100,
               transparent: bool = True) -> bytes:
    """RGBA 帧列表 → GIF 字节。

    透明必须自己留索引：直接把 RGBA 交给 save(format="GIF") 会静默丢掉 alpha，
    全透明区域会被写成某个不透明色（实测会变成上一帧的残留颜色）。
    做法是量化到 255 色、把 alpha 低于阈值的像素刷成索引 255，再声明该索引为透明。

    disposal=2（每帧前清回背景）配合透明才正确；用默认的 0 会让透明区域
    透出前一帧，形成拖影。
    """
    if not frames:
        raise ValueError("没有帧可以合成。")
    out: list[Image.Image] = []
    for fr in frames:
        rgba = fr.convert("RGBA")
        p = rgba.convert("RGB").quantize(colors=_QUANTIZE_COLORS,
                                         method=Image.Quantize.MEDIANCUT)
        if transparent:
            mask = rgba.getchannel("A").point(
                lambda a: 255 if a <= _ALPHA_CUTOFF else 0)
            p.paste(_TRANSPARENT_INDEX, mask)
        out.append(p)
    buf = io.BytesIO()
    extra: dict[str, object] = {}
    if transparent:
        extra["transparency"] = _TRANSPARENT_INDEX
        extra["disposal"] = 2
    out[0].save(buf, format="GIF", save_all=True, append_images=out[1:],
                duration=max(20, int(duration_ms)), loop=0, optimize=False, **extra)
    return buf.getvalue()


def compose_sheet(frames: list[Image.Image], cols: int = 0, padding: int = 0,
                  background: str | None = None) -> Image.Image:
    """帧列表 → 精灵图（等格网格，逐行铺）。

    cols<=0 时铺成一行。格子尺寸取所有帧的最大宽高，小帧居中放置 —— GIF 逐帧
    尺寸可能不一致，若按各自尺寸紧排，切回来时网格就对不上了。
    """
    if not frames:
        raise ValueError("没有帧可以拼合。")
    per = max(1, cols) if cols > 0 else len(frames)
    rows = (len(frames) + per - 1) // per
    cw = max(f.width for f in frames)
    ch = max(f.height for f in frames)
    pad = max(0, padding)
    sheet_w = per * cw + pad * (per + 1)
    sheet_h = rows * ch + pad * (rows + 1)
    bg = (0, 0, 0, 0) if not background else background
    sheet = Image.new("RGBA", (sheet_w, sheet_h), bg)  # type: ignore[arg-type]
    for i, fr in enumerate(frames):
        r, c = divmod(i, per)
        x = pad + c * (cw + pad) + (cw - fr.width) // 2
        y = pad + r * (ch + pad) + (ch - fr.height) // 2
        sheet.alpha_composite(fr.convert("RGBA"), (x, y))
    return sheet


def slice_sheet(data: bytes, cols: int, rows: int, padding: int = 0,
                drop_empty: bool = True) -> list[Image.Image]:
    """精灵图字节 → 帧列表，按 cols×rows 均分。

    drop_empty 会丢掉完全透明的格子：最后一行常有补空的空格，
    带着它们拼回 GIF 会多出几帧空白闪帧。
    """
    if cols < 1 or rows < 1:
        raise ValueError("行数和列数都必须至少为 1。")
    sheet = Image.open(io.BytesIO(data)).convert("RGBA")
    pad = max(0, padding)
    cw = (sheet.width - pad * (cols + 1)) / cols
    ch = (sheet.height - pad * (rows + 1)) / rows
    if cw < 1 or ch < 1:
        raise ValueError(
            f"按 {cols}×{rows} 加 {pad}px 间距切不开：单格算出来只有 "
            f"{cw:.1f}×{ch:.1f} 像素。请检查行列数或间距。")
    frames: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            # 用浮点算边界再取整，避免累计误差让最后一格偏移几个像素
            left = round(pad + c * (cw + pad))
            top = round(pad + r * (ch + pad))
            cell = sheet.crop((left, top, round(left + cw), round(top + ch)))
            if drop_empty and cell.getbbox() is None:
                continue
            frames.append(cell)
    if not frames:
        raise ValueError("切出来全是空白格，请检查行列数与间距是否匹配这张图。")
    return frames


def count_frames(data: bytes) -> int:
    """GIF 实际帧数。

    Pillow 编码时会把连续相同的帧并成一帧并累加时长（播放效果不变）。所以
    「合成了几帧」必须回读成品，不能拿输入帧数糊弄用户。
    """
    return getattr(Image.open(io.BytesIO(data)), "n_frames", 1)


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()
