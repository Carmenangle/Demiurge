"""GIF ↔ 精灵图互转。重点覆盖两个实测踩过的坑：GIF 透明和部分帧合成。"""
import io

import pytest
from PIL import Image

from app.services import gif_sprite


def _rgba(size, color):
    return Image.new("RGBA", size, color)


def _png(img):
    return gif_sprite.to_png_bytes(img)


def test_transparent_frame_survives_round_trip():
    """RGBA 直存 GIF 会静默丢 alpha，必须自己留透明索引。"""
    frames = [_rgba((8, 8), (255, 0, 0, 255)),
              _rgba((8, 8), (0, 255, 0, 255)),
              _rgba((8, 8), (0, 0, 0, 0))]
    back, _ = gif_sprite.decode_gif(gif_sprite.encode_gif(frames, 120))
    assert len(back) == 3
    assert back[0].getpixel((0, 0))[:3] == (255, 0, 0)
    assert back[2].getpixel((0, 0))[3] == 0, "全透明帧的 alpha 必须仍是 0"


def test_encode_without_transparency_keeps_opaque():
    frames = [_rgba((6, 6), (10, 20, 30, 255)), _rgba((6, 6), (200, 100, 50, 255))]
    back, _ = gif_sprite.decode_gif(
        gif_sprite.encode_gif(frames, 100, transparent=False))
    assert all(f.getpixel((0, 0))[3] == 255 for f in back)


def test_decode_composes_partial_frames():
    """真实 GIF 常只存变化区域，靠前帧当底；解码必须还原完整画面。"""
    f0 = Image.new("P", (16, 16))
    f0.putpalette([255, 0, 0] + [0] * 765)
    f1 = Image.new("P", (16, 16))
    f1.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    for y in range(4):
        for x in range(4):
            f1.putpixel((x, y), 1)
    buf = io.BytesIO()
    f0.save(buf, format="GIF", save_all=True, append_images=[f1],
            duration=100, loop=0, disposal=1)
    frames, _ = gif_sprite.decode_gif(buf.getvalue())
    assert frames[1].getpixel((0, 0))[:3] == (0, 0, 255)
    assert frames[1].getpixel((15, 15))[:3] == (255, 0, 0), "未变区域应保留前帧内容"


def test_decode_reads_per_frame_duration():
    """duration 要逐帧读 —— im.info 只反映当前 seek 到的那帧。"""
    frames = [_rgba((4, 4), c) for c in
              [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)]]
    data = gif_sprite.encode_gif(frames, 250)
    _, durations = gif_sprite.decode_gif(data)
    assert durations == [250, 250, 250]


def test_identical_frames_collapse_but_keep_total_time():
    """Pillow 会把连续相同帧并成一帧并累加时长。

    播放效果不变（停顿两帧 = 一帧停两倍时长），但帧数会少 —— 所以
    对外报帧数必须报编码后的真实值，不能报输入帧数。
    """
    hold = [_rgba((4, 4), (255, 0, 0, 255)), _rgba((4, 4), (255, 0, 0, 255)),
            _rgba((4, 4), (0, 0, 255, 255))]
    frames, durations = gif_sprite.decode_gif(gif_sprite.encode_gif(hold, 100))
    assert len(frames) == 2
    assert sum(durations) == 300, "总时长必须守恒"


def test_count_frames_reports_encoded_count():
    hold = [_rgba((4, 4), (255, 0, 0, 255))] * 3
    assert gif_sprite.count_frames(gif_sprite.encode_gif(hold, 100)) == 1


def test_decode_rejects_non_gif():
    with pytest.raises((ValueError, OSError)):
        gif_sprite.decode_gif(b"not an image at all")


def test_compose_grid_geometry_with_padding():
    frames = [_rgba((8, 8), (255, 0, 0, 255)) for _ in range(4)]
    sheet = gif_sprite.compose_sheet(frames, cols=2, padding=4)
    # 2 列 × 8px + 3 条 4px 间距 = 28
    assert sheet.size == (28, 28)


def test_compose_uses_max_cell_for_mixed_sizes():
    """逐帧尺寸不一时必须按最大格，否则切回来网格对不上。"""
    frames = [_rgba((10, 6), (255, 0, 0, 255)), _rgba((4, 8), (0, 0, 255, 255))]
    sheet = gif_sprite.compose_sheet(frames, cols=2)
    assert sheet.size == (20, 8)


def test_compose_single_row_when_cols_zero():
    frames = [_rgba((5, 5), (1, 1, 1, 255)) for _ in range(3)]
    assert gif_sprite.compose_sheet(frames, cols=0).size == (15, 5)


def test_slice_round_trips_compose():
    frames = [_rgba((8, 8), c) for c in
              [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]]
    sheet = gif_sprite.compose_sheet(frames, cols=2, padding=4)
    cells = gif_sprite.slice_sheet(_png(sheet), cols=2, rows=2, padding=4)
    assert len(cells) == 4
    assert [c.getpixel((4, 4))[:3] for c in cells] == [
        (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def test_slice_drops_empty_cells_by_default():
    frames = [_rgba((8, 8), (255, 0, 0, 255)), _rgba((8, 8), (0, 255, 0, 255)),
              _rgba((8, 8), (0, 0, 255, 255))]
    sheet = gif_sprite.compose_sheet(frames, cols=2)   # 2x2 网格，第 4 格空
    assert len(gif_sprite.slice_sheet(_png(sheet), 2, 2)) == 3


def test_slice_can_keep_empty_cells():
    """真空白帧和补空格分不出来，所以开关要能关掉。"""
    frames = [_rgba((8, 8), (255, 0, 0, 255))] * 3
    sheet = gif_sprite.compose_sheet(frames, cols=2)
    kept = gif_sprite.slice_sheet(_png(sheet), 2, 2, drop_empty=False)
    assert len(kept) == 4


def test_slice_rejects_impossible_grid():
    sheet = gif_sprite.compose_sheet([_rgba((8, 8), (1, 1, 1, 255))], cols=1)
    with pytest.raises(ValueError, match="切不开"):
        gif_sprite.slice_sheet(_png(sheet), cols=99, rows=99, padding=4)


def test_slice_rejects_bad_dimensions():
    sheet = gif_sprite.compose_sheet([_rgba((8, 8), (1, 1, 1, 255))], cols=1)
    with pytest.raises(ValueError):
        gif_sprite.slice_sheet(_png(sheet), cols=0, rows=1)


def test_encode_empty_raises():
    with pytest.raises(ValueError):
        gif_sprite.encode_gif([])


def test_compose_empty_raises():
    with pytest.raises(ValueError):
        gif_sprite.compose_sheet([])
