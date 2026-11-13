"""《堕落魔戒》婚礼堂三步视频前置流程的口径锁（2026-11-12）。

只测纯函数与常量，不碰 ComfyUI / 云端：场景分组、露骨镜中性化、参考板不裁切。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_SCRIPT = BACKEND / "scripts" / "probe_wedding_video_flow.py"
_spec = importlib.util.spec_from_file_location("probe_wedding_video_flow", _SCRIPT)
assert _spec and _spec.loader
flow = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = flow
_spec.loader.exec_module(flow)


def _shot(index: int, *, locale: str = "旧仓库", prompt: str = "a warehouse shot") -> dict:
    return {"shot_index": index, "locale": locale, "image_prompt": prompt}


@pytest.mark.parametrize(
    ("locale", "prompt", "expected"),
    [
        ("旧仓库的木门前", "a cinematic, abandoned warehouse", "scene-0"),
        ("红毯上", "standing on a red carpet", "scene-0"),
        ("房间内床边", "lying on her side on the bed, pillow", "scene-1"),
        ("门外", "bed, pillow, dim bedroom", "scene-1"),
    ],
)
def test_scene_for_shot_groups_bedroom_and_warehouse(locale, prompt, expected):
    assert flow.scene_for_shot(_shot(0, locale=locale, prompt=prompt)) == expected


def test_explicit_shots_use_neutral_prompt():
    for index in (6, 7):
        prompt, neutral = flow.shot_prompt(
            _shot(index, prompt="breasts exposed, white semen"), neutralize=True)
        assert neutral is True
        lowered = prompt.lower()
        for forbidden in ("breasts exposed", "white semen", "panting", "parted lips"):
            assert forbidden not in lowered
        assert "white wedding gown" in lowered
        assert flow.BOARD_NOTE not in prompt  # 纯文生图不带图生图遗留句
    # 镜 7 中性化时曾漏写发色 ⇒ 出成黑发，已补回身份锚点。
    assert "golden blonde long hair" in flow.shot_prompt(_shot(7), neutralize=True)[0].split("\n", 1)[0]


def test_shot_prompt_defaults_to_raw_nsfw_prompt():
    """用户定案 R-18 不过滤：默认走原稿露骨提示词，中性化必须显式开启。

    这条锁的是「链路不做内容审查」——不是锁某张图长什么样。
    """
    raw = "masterpiece, 1girl, breasts exposed, white semen on inner thighs"
    prompt, neutral = flow.shot_prompt(_shot(6, prompt=raw))
    assert neutral is False
    assert "breasts exposed" in prompt
    assert "white semen" in prompt

    # 只有显式 neutralize=True 才换成中性化稿
    neutral_prompt, flag = flow.shot_prompt(_shot(6, prompt=raw), neutralize=True)
    assert flag is True
    assert "breasts exposed" not in neutral_prompt.lower()
    assert "white semen" not in neutral_prompt.lower()


def test_non_explicit_shot_keeps_compiled_prompt_and_adds_anchors():
    source = "masterpiece, 1girl, white wedding gown, warehouse"
    prompt, neutral = flow.shot_prompt(_shot(1, prompt=source))
    assert neutral is False
    assert source in prompt
    assert flow.WAREHOUSE_ANCHORS[1] in prompt
    assert flow.WARDROBE_ANCHOR in prompt
    assert flow.BOARD_NOTE not in prompt  # 纯文生图不带图生图遗留句


def test_scene_only_base_is_9_16_and_keeps_wide_scene_edges(tmp_path):
    """环境镜基底：16:9 完整入画（左右色带不丢），且是单幅竖构图不是上下分栏。"""
    scene = Image.new("RGB", (1600, 900), (30, 30, 30))
    for x, color in ((0, (255, 0, 0)), (1550, (0, 0, 255))):
        for xx in range(x, x + 50):
            for yy in range(scene.height):
                scene.putpixel((xx, yy), color)
    scene_path = tmp_path / "scene.png"
    scene.save(scene_path)
    char_path = tmp_path / "character.png"
    Image.new("RGB", (864, 1536), (200, 200, 200)).save(char_path)

    dest = tmp_path / "scene_base.png"
    assert flow.build_reference_board(
        scene_path, char_path, dest, with_character=False,
    ) == str(dest)
    board = Image.open(dest)
    assert board.size == (flow.BOARD_W, flow.BOARD_H) == (864, 1536)

    # 中央区左右色带都在 ⇒ 16:9 完整、未被 center 裁掉。
    center = board.crop((0, 525, 864, 1011))
    colors = set(center.get_flattened_data())
    assert any(c[0] > 180 and c[1] < 80 for c in colors), "left red band lost"
    assert any(c[2] > 180 and c[0] < 80 for c in colors), "right blue band lost"

    # 🔴 回归：旧分栏板会在 y≈620 出现整行单一深灰硬分界。逐行取样不允许纯色分隔行。
    for y in (100, 400, 620, 800, 1100, 1450):
        row = {board.getpixel((x, y)) for x in range(0, 864, 8)}
        assert len(row) > 1, f"row {y} looks like a hard divider / blank band"


def test_character_base_is_one_continuous_image_not_a_split_board(tmp_path):
    """人物镜基底：全幅角色图 + 全局均匀混色 ⇒ 上下像素连续，没有分栏接缝。"""
    scene_path = tmp_path / "scene.png"
    Image.new("RGB", (1600, 900), (20, 20, 120)).save(scene_path)
    char_path = tmp_path / "character.png"
    Image.new("RGB", (864, 1536), (240, 240, 240)).save(char_path)

    dest = tmp_path / "char_base.png"
    assert flow.build_reference_board(
        scene_path, char_path, dest, with_character=True,
    ) == str(dest)
    board = Image.open(dest)
    assert board.size == (864, 1536)

    top = board.getpixel((432, 100))
    middle = board.getpixel((432, 760))
    bottom = board.getpixel((432, 1450))
    # 全局 blend ⇒ 三点应几乎同色；分栏板则中间会换到另一幅图。
    for a, b in ((top, middle), (middle, bottom)):
        assert max(abs(a[i] - b[i]) for i in range(3)) < 12, "hard split detected"
    # 角色（白）主导，场景（蓝）只是氛围层。
    assert middle[0] > 180 and middle[2] < 255


def test_shot_has_character_splits_environment_and_people():
    assert flow.shot_has_character({"shot_index": 0, "image_prompt": (
        "masterpiece, cinematic, abandoned warehouse, first person view, pov,\n"
        "The scene is filled with soft light."
    )}) is False
    assert flow.shot_has_character({"shot_index": 1, "image_prompt": (
        "masterpiece, 1girl, solo, white wedding gown,\nThe tall woman stands still."
    )}) is True
    # 镜 6/7 默认走原稿（R-18 不过滤），原稿 tag 行自身就含 1girl ⇒ 判为人物镜。
    assert flow.shot_has_character({"shot_index": 6, "image_prompt": "masterpiece, 1girl, white wedding gown"}) is True
    assert flow.shot_has_character({"shot_index": 7, "image_prompt": "masterpiece, 1girl, white wedding gown"}) is True


def test_hair_anchor_only_touches_the_two_drifted_shots():
    """8 宫格复核：仅 shot_02 / shot_07 发色漂移 ⇒ 只让这两镜的 prompt 变化、其余 skip。"""
    tag2 = flow.shot_prompt(_shot(2, prompt=(
        "masterpiece, 1girl, dark-yellow long hair, white wedding gown,\nstanding on a red carpet."
    )))[0].split("\n", 1)[0]
    assert flow.HAIR_ANCHOR_TAGS in tag2
    assert "golden blonde long hair" in flow.NEUTRAL_PROMPTS[7].split("\n", 1)[0]

    tag1 = flow.shot_prompt(_shot(1, prompt="masterpiece, 1girl, white wedding gown"))[0]
    tag3 = flow.shot_prompt(_shot(3, prompt="masterpiece, 1girl, white wedding gown"))[0]
    assert flow.HAIR_ANCHOR_TAGS not in tag1
    assert flow.HAIR_ANCHOR_TAGS not in tag3

    lowered = flow.NEGATIVE_SHOT.lower()
    for token in ("dark brown hair", "black hair", "dark hair"):
        assert token in lowered, token


def test_board_note_and_negative_block_divided_layout():
    assert "ONE undivided reference image" in flow.BOARD_NOTE
    assert "upper area" not in flow.BOARD_NOTE
    lowered = flow.NEGATIVE_SHOT.lower()
    for token in ("diptych", "two stacked images", "horizontal divider",
                  "upper and lower panels", "divided layout"):
        assert token in lowered, token


def test_character_prompt_contains_current_state_and_framing():
    assert "Current state:" in flow.STATE_ANCHOR
    assert "white wedding gown" in flow.STATE_ANCHOR
    # 用户定案 R-18 不过滤：负面词里不得出现 NSFW/nude/explicit 这类内容审查词
    for neg in (flow.NEGATIVE_CHARACTER, flow.NEGATIVE_SHOT):
        assert "NSFW" not in neg and "nude" not in neg and "explicit" not in neg


def test_state_prompt_drops_manjiu_paragraph_and_locks_gown():
    """真机实证：镜 1 第二段的 `several manjiu` 会在状态图串出两个拿抄本的小人。"""
    review = {"shots": [
        {"shot_index": 0, "image_prompt": "ignored"},
        {"shot_index": 1, "image_prompt": (
            "masterpiece, 1girl, solo, white wedding gown, full body,\n"
            "several manjiu freeze mid-task with their hands suspended."
        )},
    ]}
    prompt = flow.state_prompt(review)
    assert "masterpiece, 1girl, solo, white wedding gown, full body" in prompt
    assert "manjiu" not in prompt
    assert "several manjiu" not in prompt
    assert "pure white wedding gown" in prompt
    assert "no other characters" in prompt
    assert flow.STATE_ANCHOR in prompt
    assert flow.STATE_FRAMING in prompt


def test_negative_prompt_blocks_black_dress_and_bystanders():
    lowered = flow.NEGATIVE_CHARACTER.lower()
    for token in ("black dress", "dark dress", "black gown", "other people",
                  "background figures", "manjiu", "holding documents"):
        assert token in lowered, token
    for token in ("black dress", "dark dress", "black gown", "split screen", "collage"):
        assert token in flow.NEGATIVE_SHOT.lower(), token


def test_state_base_image_is_the_white_gown_full_body_still():
    """预设定妆照是黑礼服；状态底图必须是同批次白婚纱全身像（真机验收修正）。"""
    assert flow.STATE_BASE_IMAGE.is_file()
    assert flow.STATE_BASE_IMAGE.name == "shot_01.png"
    assert Image.open(flow.STATE_BASE_IMAGE).size == (flow.BOARD_W, flow.BOARD_H)
    assert "黑色礼服" in flow.STATE_BASE_REASON
    assert "cfg=1" in flow.STATE_BASE_REASON
    assert flow.STATE_BASE_NAME == "腓特烈"


def test_stage_order_and_template_contract():
    assert flow.TEMPLATE_TEXT2IMG != flow.TEMPLATE_IMG2IMG
    assert flow.MESSAGE_ID.startswith("bff28b0a")
    assert (flow.BOARD_W, flow.BOARD_H) == (864, 1536)


def test_stage_shots_is_text2img_per_shot_not_composite_base():
    """用户 2026-11-12 定案：回归正常分镜，每镜独立文生图，不再合成参考底图。

    源码文本锁：`stage_shots` 里不允许再出现合成基底/图生图模板的痕迹，
    防止后续又把「场景+角色合成板」那条走不通的路接回来。
    """
    import inspect

    assert flow.TEMPLATE_SHOT == flow.TEMPLATE_TEXT2IMG
    assert flow.IMAGE_MODE_SHOT == "text2img_per_shot"
    assert (flow.SHOT_W, flow.SHOT_H) == (576, 1024)

    src = inspect.getsource(flow.stage_shots)
    assert "build_reference_board" not in src
    assert "reference_boards" not in src
    assert "TEMPLATE_IMG2IMG" not in src
    assert "TEMPLATE_SHOT" in src


def test_merge_flow_steps_accumulates_instead_of_overwriting():
    """三步分开跑时，最后一次运行不能把前两步从总览里抹掉。"""
    first = flow.merge_flow_steps({}, {"stage": "scenes", "scenes": {"status": "ok"}})
    assert first["steps_completed"] == ["scenes"]
    assert first["steps"]["scenes"] == {"status": "ok"}

    second = flow.merge_flow_steps(first, {"stage": "characters", "characters": {"status": "ok"}})
    assert second["steps_completed"] == ["scenes", "characters"]

    third = flow.merge_flow_steps(second, {"stage": "shots", "shots": {"shots": {}}})
    assert third["steps_completed"] == ["scenes", "characters", "shots"]
    assert set(third["steps"]) == {"scenes", "characters", "shots"}
    assert "scenes" not in third and "shots" not in third


def test_merge_flow_steps_accepts_legacy_top_level_manifest():
    legacy = {"stage": "characters", "characters": {"characters": {"腓特烈": "generated"}}}
    merged = flow.merge_flow_steps(legacy, {"stage": "shots", "shots": {"shots": {}}})
    assert merged["steps_completed"] == ["characters", "shots"]
    assert merged["steps"]["characters"] == legacy["characters"]
