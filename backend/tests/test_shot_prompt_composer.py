"""`shot_prompt_composer` 的口径锁：把分镜里已有的事实编译进提示词，只追加不改写。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import shot_prompt_composer as composer  # noqa: E402


def test_framing_tags_maps_shot_size_and_multi_subject():
    tags, multi = composer.framing_tags("中景/门口与床")
    assert tags == ["medium shot", "two subjects in one frame"]
    assert multi is True

    tags, multi = composer.framing_tags("特写/蛮啾")
    assert tags == ["close-up"]
    assert multi is False

    tags, multi = composer.framing_tags("环境空镜")
    assert tags == ["wide establishing shot, no people"]
    assert multi is False


def test_framing_tags_ignores_scene_words_when_no_size():
    tags, multi = composer.framing_tags("门口与床")
    assert "medium shot" not in tags
    assert multi is True  # 「与」仍表示多主体


def test_camera_tags_known_and_fallback():
    assert composer.camera_tags("固定") == ["static camera"]
    assert composer.camera_tags("轻微下摇") == ["slight tilt down"]
    assert composer.camera_tags("缓慢横移并下摇") == ["slow lateral truck"] or \
        composer.camera_tags("缓慢横移并下摇") == ["tilt down"]
    assert composer.camera_tags("") == []


def test_compose_appends_to_tag_line_only():
    base = (
        "masterpiece, 1girl, white wedding gown, lying on her side,\n"
        "The woman lies on the bed while a small bird watches from the door."
    )
    out = composer.compose_shot_prompt(
        base,
        framing="中景/门口与床",
        camera_motion="固定",
        appearance=["blonde hair", "yellow eyes"],
        scene_hint="the doorway",
    )
    head, _, rest = out.partition("\n")
    # 追加物全部落在 tag 行
    for token in ("blonde hair", "yellow eyes", "medium shot", "static camera"):
        assert token in head
    # 自然语言正文逐字保留
    assert "The woman lies on the bed while a small bird watches from the door." in rest
    # 构图短句单独成段
    assert "Keep the framing unambiguous" in out
    assert "Both subjects must be visible at once" in out


def test_compose_does_not_duplicate_existing_tags():
    base = "masterpiece, 1girl, blonde hair, medium shot,\nstanding."
    out = composer.compose_shot_prompt(base, framing="中景", appearance=["blonde hair"])
    head = out.split("\n", 1)[0]
    assert head.lower().count("blonde hair") == 1
    assert head.lower().count("medium shot") == 1


def test_is_single_human_excludes_bird_and_first_person():
    # 蛮啾是小动物、「我」是第一人称不入画 ⇒ 都仍算「单个人类主体」
    assert composer.is_single_human(["腓特烈"]) is True
    assert composer.is_single_human(["腓特烈", "蛮啾"]) is True
    assert composer.is_single_human(["腓特烈", "我"]) is True
    # 两个人类角色才是多主体
    assert composer.is_single_human(["腓特烈", "奥古斯特"]) is False
    assert composer.is_single_human([]) is False
    assert composer.is_single_human(None) is False


def test_compose_single_subject_adds_hard_constraint():
    """真机实锤：`solo` 压不住，`shot_02` 单人镜画出两个一模一样的人。"""
    out = composer.compose_shot_prompt(
        "masterpiece, 1girl, solo,\nstanding.",
        single_subject=True,
    )
    assert "only one person in frame" in out
    assert "no duplicate characters" in out
    assert "Exactly one person appears in this frame" in out


def test_compose_returns_base_when_nothing_to_add():
    base = "masterpiece, 1girl,\nstanding."
    assert composer.compose_shot_prompt(base, framing="", camera_motion="") == base


def test_appearance_anchor_is_safe_on_missing_input():
    assert composer.appearance_anchor(None) == []
    assert composer.appearance_anchor("") == []
    assert composer.appearance_anchor(Path("no/such/file.png")) == []
