"""story_frames 单测：首尾帧双锚点提取（空 / 纯对白 / 单段 / 多段 / jailbreak 包裹）。"""

from app.services import story_frames


def test_empty_returns_empty():
    assert story_frames.extract_story_frames("").opening == ""
    assert story_frames.extract_story_frames("   \n\n  ").opening == ""


def test_single_paragraph_opening_equals_closing():
    f = story_frames.extract_story_frames("温知夏站在面馆门口，暖黄灯光照亮她的侧脸。")
    assert f.opening == f.closing
    assert f.evidence == "single_paragraph"


def test_multi_paragraph_first_last():
    text = (
        "雨夜，面馆门口挂起暖黄的灯笼，水珠顺着门帘滴落。\n\n"
        "温知夏推门而入，沈糯已经坐定，朝她招手。\n\n"
        "三人举杯同框，笑声在暖光里散开。"
    )
    f = story_frames.extract_story_frames(text)
    assert "灯笼" in f.opening  # 首段画面
    assert "举杯同框" in f.closing  # 末段画面
    assert f.evidence == "first_last"


def test_pure_dialogue_head_borrows_next_visual():
    # 首段纯对白 → 借相邻有画面段，保持「开头」位置语义
    text = (
        "「你迟到了。」\n\n"
        "沈糯抬眼，放下筷子，朝门口笑了笑。\n\n"
        "温知夏脱下围巾，水汽在暖光里升腾。"
    )
    f = story_frames.extract_story_frames(text)
    assert "筷子" in f.opening  # 借到了第二段画面，而非纯对白首段
    assert "围巾" in f.closing


def test_jailbreak_wrapped_content_stripped():
    text = (
        "<content>雨夜面馆门口，灯笼摇晃。\n\n"
        "三人围坐，举杯同框。\n\n</content>"
        "<status>状态更新</status>"
    )
    f = story_frames.extract_story_frames(text)
    assert "灯笼" in f.opening
    assert "举杯同框" in f.closing
    assert "状态更新" not in f.closing


def test_frames_to_desc_maps_roles():
    f = story_frames.StoryFrames(opening="开场", closing="收尾", evidence="x")
    d = story_frames.frames_to_desc(f)
    assert d["first_frame_desc"] == "开场"
    assert d["last_frame_desc"] == "收尾"
