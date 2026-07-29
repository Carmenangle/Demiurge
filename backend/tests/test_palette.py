"""调色盘提取 + 色彩约束存取 + 注入。"""
import io

from PIL import Image

from app.services import palette, palette_pref
from app.services.palette_inject import inject


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_colors_sorted_by_area():
    """主色应是面积最大的那个，getpalette 的顺序不代表占比。"""
    img = Image.new("RGB", (30, 10))
    img.paste(Image.new("RGB", (20, 10), (200, 30, 40)), (0, 0))
    img.paste(Image.new("RGB", (10, 10), (20, 60, 200)), (20, 0))
    colors, _ = palette.extract(_png(img), max_colors=4)
    assert colors[0] == "#c81e28"
    assert "#143cc8" in colors


def test_bit_depth_reduction_keeps_full_range():
    """降位要撑回满量程，否则纯白会变 #f8f8f8 这类偏暗值。"""
    white = Image.new("RGB", (4, 4), (255, 255, 255))
    colors, _ = palette.extract(_png(white), max_colors=2, bit_depth="rgb565")
    assert colors == ["#ffffff"]


def _grad():
    """色彩丰富的渐变图：位深差异只在多色图上才看得出来。"""
    img = Image.new("RGB", (64, 64))
    img.putdata([((x * 4) % 256, (y * 4) % 256, (x + y) % 256)
                 for y in range(64) for x in range(64)])
    return _png(img)


def test_palette_snapped_to_bit_depth_grid():
    """量化后要把色号吸附回该位深真能表示的档位。

    只在量化前降位是不够的：quantize() 取色箱平均值当代表色，会漂回网格外，
    结果 rgb565 和 rgb888 只差 1~3，用户看不出区别（这就是原来的 bug）。
    """
    for depth, bits in (("rgb444", (4, 4, 4)), ("rgb332", (3, 3, 2))):
        colors, _ = palette.extract(_grad(), max_colors=16, bit_depth=depth)
        assert colors, depth
        for hx in colors:
            rgb = [int(hx[i:i + 2], 16) for i in (1, 3, 5)]
            for v, b in zip(rgb, bits, strict=True):
                levels = (1 << b) - 1
                assert v == round(round(v * levels / 255) * 255 / levels), \
                    f"{depth} {hx} 通道值 {v} 不在 {b}bit 网格上"


def test_bit_depth_changes_palette():
    """不同位深必须给出不同调色盘，否则这个下拉框等于没用。"""
    data = _grad()
    got = {d: palette.extract(data, max_colors=16, bit_depth=d)[0]
           for d in ("rgb888", "rgb444", "rgb332")}
    assert got["rgb888"] != got["rgb444"]
    assert got["rgb444"] != got["rgb332"]


def test_snapped_palette_has_no_duplicate_colors():
    """吸附会把相邻色并成同一个值，必须合并，否则色块列表出现重复。"""
    colors, _ = palette.extract(_grad(), max_colors=64, bit_depth="rgb332")
    assert len(colors) == len(set(colors))


def test_alpha_flattened_to_white_not_black():
    """透明像素若直接参与量化会引入假的黑色主色。"""
    img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (5, 10), (255, 0, 0, 255)), (0, 0))
    colors, _ = palette.extract(_png(img), max_colors=4)
    assert "#000000" not in colors
    assert "#ff0000" in colors


def test_color_count_capped():
    img = Image.new("RGB", (64, 64))
    img.putdata([(i % 256, (i * 5) % 256, (i * 11) % 256) for i in range(64 * 64)])
    colors, _ = palette.extract(_png(img), max_colors=8)
    assert len(colors) <= 8


def test_extract_is_deterministic():
    img = Image.new("RGB", (32, 32))
    img.putdata([(i % 256, (i * 3) % 256, (i * 7) % 256) for i in range(32 * 32)])
    a, _ = palette.extract(_png(img), max_colors=6)
    b, _ = palette.extract(_png(img), max_colors=6)
    assert a == b


def test_octree_method_works():
    img = Image.new("RGB", (16, 16), (12, 34, 56))
    colors, _ = palette.extract(_png(img), max_colors=4, method="octree")
    assert colors == ["#0c2238"]


# ---- 色彩约束的规范化 ----

def test_normalize_expands_shorthand_and_dedupes():
    got = palette_pref.normalize_colors(
        ["#abc", "ABCDEF", "#aabbcc", "rgb(1,2,3)", "", "#GGG"])
    assert got == ["#aabbcc", "#abcdef"]


def test_normalize_caps_length():
    many = [f"#{i:02x}0000" for i in range(30)]
    assert len(palette_pref.normalize_colors(many)) == palette_pref.MAX_COLORS


# ---- 注入 ----

def _nodes():
    return [
        {"id": "1", "type": "CLIPTextEncode",
         "widgets": [{"name": "text", "value": ""}]},
        {"id": "9", "type": "KSampler",
         "inputs": [{"name": "positive", "source_node_id": "1"}]},
    ]


def _plan(text="a cat"):
    return {"is_orchestration": True, "summary": "改提示词",
            "ops": [{"node_id": "1", "input": "text",
                     "action": "set_widget", "value": text}]}


def test_inject_appends_after_prompt():
    """色板是追加而非前置 —— 前置会把画面主体挤到后面。"""
    plan = _plan("a cat on a roof")
    got = inject(plan, _nodes(), "画只猫", ["#ff0000", "#00ff00"])
    assert got == ["#ff0000", "#00ff00"]
    assert plan["ops"][0]["value"].startswith("a cat on a roof")
    assert "color palette: #ff0000, #00ff00" in plan["ops"][0]["value"]
    assert "色彩约束" in plan["summary"]


def test_inject_skipped_when_user_named_colors():
    plan = _plan("a cat")
    assert inject(plan, _nodes(), "把配色改成蓝色调", ["#ff0000"]) == []
    assert plan["ops"][0]["value"] == "a cat"


def test_inject_skipped_when_user_gave_hex():
    plan = _plan("a cat")
    assert inject(plan, _nodes(), "用 #123456 这个色", ["#ff0000"]) == []


def test_inject_skips_when_color_already_in_text():
    plan = _plan("a cat, #ff0000")
    assert inject(plan, _nodes(), "画只猫", ["#ff0000"]) == []


def test_inject_noop_without_colors():
    plan = _plan("a cat")
    assert inject(plan, _nodes(), "画只猫", []) == []
    assert "色彩约束" not in plan.get("summary", "")


def test_inject_does_not_add_ops():
    """用户只改 seed 时计划里没有提示词 op，不该凭空造一个。"""
    plan = {"is_orchestration": True, "summary": "改种子",
            "ops": [{"node_id": "9", "input": "seed",
                     "action": "set_widget", "value": 5}]}
    assert inject(plan, _nodes(), "seed 改成 5", ["#ff0000"]) == []
    assert len(plan["ops"]) == 1
