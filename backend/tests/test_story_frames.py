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


# ===== 第 34 刀：取材＝「前/尾部分」（连续累计），修两处形态缺陷 =====
# 实锤（2026-09-21 探针 D:\pytest-tmp\i36）：L1 首帧描述 `“嗯嗯……！哈啊…！”`、
# L3 首尾都是台词 ⇒ 帧描述无画面信息 ⇒ 提取器空转 ⇒ 帧成品掉词表兜底。


def test_引号内的动词不算画面词():
    """形态缺陷回归锁：`_VISUAL_TERMS` 含「坐/看/走/站」等动作词 ⇒
    纯对白「…这样坐就行吗？」被判成「有画面」⇒ 借段闸被绕过。
    画面词只算**引号外**的叙述文本。"""
    assert story_frames._has_visual("宥夏坐在沙发边缘。") is True
    assert story_frames._has_visual("“这样坐就行吗？”") is False
    # 引号外仍有叙述 ⇒ 照旧算有画面（不把整段判死）
    assert story_frames._has_visual("“走吧。”她转身推开门。") is True


def test_长纯对白首段仍取到开头部分叙述():
    """形态缺陷回归锁：旧判据是「引号字符数 / 字数 > 0.25」，一对引号恒为 2/len
    ⇒ 同一形态的对白只要 ≥8 字就 ≤0.25、永不借段（11 字 ⇒ 2/11 = 0.18）。
    新口径：开头部分＝首段起**连续累计**（不跳段），必然带上后文的叙述。"""
    text = (
        "“这、这样坐就行吗……？泰宇老师？”\n\n"
        "宥夏抬起眼，指尖攥紧了裙摆，膝盖并拢坐在沙发边缘。"
    )
    f = story_frames.extract_story_frames(text)
    assert f.opening.startswith("“这、这样坐就行吗")
    assert "指尖攥紧" in f.opening


def test_结尾纯对白向前连续累计到有画面段():
    text = (
        "雨夜，面馆门口挂起暖黄的灯笼。\n\n"
        "温知夏推门而入，沈糯已经坐定。\n\n"
        "「明天见。」\n\n"
        "“路上小心。”"
    )
    f = story_frames.extract_story_frames(text)
    assert "明天见" in f.closing       # 末段是台词 ⇒ 末两段一起纳入
    assert "推门而入" in f.closing      # 继续向前直到有画面感的那段
    assert "灯笼" not in f.closing      # 到有画面段即收束，不无限回溯


def test_前尾部分不跳段且各自从边界起():
    text = (
        "雨夜，面馆门口挂起暖黄的灯笼。\n\n"
        "温知夏推门而入。\n\n"
        "三人举杯同框，笑声在暖光里散开。"
    )
    f = story_frames.extract_story_frames(text)
    assert f.opening.startswith("雨夜")            # 一定含首段
    assert f.closing.endswith("散开。")            # 一定含末段
    assert f.evidence == "first_last"


def test_前尾部分长度上限():
    head = "雨夜，面馆门口挂起暖黄的灯笼。" * 40          # 单段远超上限
    f = story_frames.extract_story_frames(head + "\n\n" + "尾段。")
    assert len(f.opening) <= 500
    assert f.closing == "尾段。" or f.closing.endswith("尾段。")


def test_复用判定只看curr首段_宽窗口后段切换词不误判():
    """宽窗口 opening 是多段累计 ⇒ 若整段参与地点/切换词匹配，
    后段的「后来/离开」会把本来能接上的首帧误判成 regenerate。只取首段。"""
    d = story_frames.judge_frame_reuse(
        "三人围坐面馆，举杯同框。",
        "面馆里的暖光依旧，沈糯抿了口汤。\n\n她后来离开面馆，走到街上。",
    )
    assert d.decision == "reuse"
    assert d.evidence == "shared_locale:面馆"


def test_复用判定只看prev末段():
    """prev 的尾端＝末段；地点词只出现在更早的段里 ⇒ 不算共享（交 L1 判）。"""
    d = story_frames.judge_frame_reuse(
        "深夜的面馆里，三人围坐。\n\n她在窗边站了很久，什么也没说。",
        "面馆里的暖光依旧。",
    )
    assert d.decision == "ambiguous"


# ===== 首帧复用判断（F1，L0 三态）=====


def test_reuse_shared_locale_same_scene():
    # 两段共享具体地点词且无切换信号 → reuse
    d = story_frames.judge_frame_reuse(
        "三人围坐面馆，举杯同框。", "面馆里的暖光依旧，沈糯抿了口汤。",
    )
    assert d.decision == "reuse"
    assert d.evidence == "shared_locale:面馆"


def test_regenerate_time_jump():
    # N+1 首段含日期跳跃词 → regenerate
    d = story_frames.judge_frame_reuse(
        "三人围坐面馆，举杯同框。", "次日清晨，温知夏在车站送别。",
    )
    assert d.decision == "regenerate"
    assert d.evidence.startswith("time_jump:")


def test_regenerate_scene_change():
    # N+1 首段含跨场景移动词 → regenerate
    d = story_frames.judge_frame_reuse(
        "三人围坐面馆，举杯同框。", "她离开面馆，走到街上。",
    )
    assert d.decision == "regenerate"
    assert d.evidence.startswith("scene_change:")


def test_scene_change_wins_over_shared_locale():
    # curr 既有「离开」又有「面馆」时，切换信号优先 → regenerate（不因共享地点误判 reuse）
    d = story_frames.judge_frame_reuse(
        "三人围坐面馆，举杯同框。", "离开面馆后，他们走向车站。",
    )
    assert d.decision == "regenerate"


def test_ambiguous_no_strong_signal():
    # 两段无切换词、无共享地点词 → ambiguous（交 L1）
    d = story_frames.judge_frame_reuse(
        "她抬眼笑了笑。", "他低头搅动着什么。",
    )
    assert d.decision == "ambiguous"
    assert d.evidence == "no_strong_signal"


def test_ambiguous_empty_input():
    # 任一输入为空 → ambiguous（不猜）
    assert story_frames.judge_frame_reuse("", "开门").decision == "ambiguous"
    assert story_frames.judge_frame_reuse("开门", "").decision == "ambiguous"


def test_scene_internal_motion_not_regenerate():
    # 场景内微移动（走到/转身）不触发 regenerate → 无共享地点时 ambiguous
    d = story_frames.judge_frame_reuse(
        "她站在窗边。", "他走到窗边，也望向外面。",
    )
    assert d.decision == "ambiguous"  # 「走到」不是跨场景切换词，也不含共享地点词


# ===== 首帧复用决策合并（W2，L0/L1 合并，坑B/坑I）=====


def test_merge_l0_reuse_wins_over_l1():
    # L0 确定 reuse（共享地点词）→ 忽略 <transition> regenerate
    d = story_frames.merge_frame_reuse(
        "三人围坐面馆，举杯同框。", "面馆里的暖光依旧，沈糯抿了口汤。", "regenerate",
    )
    assert d.decision == "reuse"
    assert d.evidence.startswith("shared_locale:")


def test_merge_l0_regenerate_wins_over_l1():
    # L0 确定 regenerate（时间跳跃）→ 忽略 <transition> reuse
    d = story_frames.merge_frame_reuse(
        "三人围坐面馆，举杯同框。", "次日清晨，她在车站送别。", "reuse",
    )
    assert d.decision == "regenerate"
    assert d.evidence.startswith("time_jump:")


def test_merge_l0_ambiguous_consumes_l1_reuse():
    d = story_frames.merge_frame_reuse("她抬眼笑了笑。", "他低头搅动什么。", "reuse")
    assert d.decision == "reuse"
    assert d.evidence == "l1_transition:reuse"


def test_merge_l0_ambiguous_consumes_l1_regenerate():
    d = story_frames.merge_frame_reuse("她抬眼笑了笑。", "他低头搅动什么。", "regenerate")
    assert d.decision == "regenerate"
    assert d.evidence == "l1_transition:regenerate"


def test_merge_l0_ambiguous_l1_missing():
    # L0 ambiguous + <transition> 缺失/非法 → ambiguous（交前端坑C 前提裁决）
    d = story_frames.merge_frame_reuse("她抬眼笑了笑。", "他低头搅动什么。", None)
    assert d.decision == "ambiguous"
    assert d.evidence == "l0_ambiguous_l1_missing"
    d = story_frames.merge_frame_reuse("她抬眼笑了笑。", "他低头搅动什么。", "maybe")
    assert d.decision == "ambiguous"

