"""全局模式分镜计划纯函数测试（2026-11-09）。"""

from app.services import scene_illustration, shot_plan, video_prompt

TEXT = "她站在门口。\n\n她走到沙发前坐下。\n\n她把茶杯放在桌上。"


def _anchor(anchor: str, shot_type: str = "cut", desc: str = "", original: int | None = None):
    item = {"anchor": anchor, "shot_type": shot_type, "transition_desc": desc}
    if original is not None:
        item["original_index"] = original
    return item


def test_shot_type_normalize_defaults_to_cut():
    assert scene_illustration.normalize_shot_type("MOVE") == "move"
    assert scene_illustration.normalize_shot_type(" move_single ") == "move_single"
    assert scene_illustration.normalize_shot_type("establish") == "establish"
    assert scene_illustration.normalize_shot_type("unknown") == "cut"
    assert scene_illustration.normalize_shot_type(None) == "cut"


def test_scene_shift_cues_share_video_prompt_source():
    assert set(video_prompt._SCENE_SHIFT_CUES).issubset(set(shot_plan.SCENE_SHIFT_CUES))


def test_build_plan_keeps_cut_move_cut_in_text_order():
    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "move", "镜头从门口推进到沙发，跟随她坐下", original=1),
        _anchor("她把茶杯放在桌上。", "cut", original=2),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=12)

    assert plan.sequence_id == "seq"
    assert plan.total_shots == 3
    assert [item.shot_type for item in plan.items] == ["cut", "move", "cut"]
    move = plan.items[1]
    assert move.video_mode == "shot_move"
    assert move.start_anchor == "她走到沙发前坐下。"
    assert move.end_anchor == "她把茶杯放在桌上。"
    assert move.image_key == "seq:1"
    assert move.end_image_key == "seq:2"
    assert all(item.duration_s >= 3 for item in plan.items)
    assert sum(item.duration_s for item in plan.items) == 12
    assert not [w for w in plan.warnings if "降级" in w]


def test_move_without_transition_desc_falls_back_to_cut():
    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "move", "", original=1),
        _anchor("她把茶杯放在桌上。", "cut", original=2),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=12)

    assert plan.items[1].shot_type == "cut"
    assert plan.items[1].video_mode == "shot_cut"
    assert plan.items[1].end_anchor == ""
    assert any("缺少 transition_desc" in w for w in plan.warnings)


def test_last_anchor_move_falls_back_to_cut():
    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "move", "镜头推进", original=1),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=10)

    assert plan.items[-1].shot_type == "cut"
    assert any("最后一锚点没有下一帧" in w for w in plan.warnings)


def test_move_across_scene_cue_falls_back_to_cut():
    text = "她站在门口。\n\n片刻后，她走到沙发前坐下。\n\n她把茶杯放在桌上。"
    anchors = [
        _anchor("她站在门口。", "move", "镜头从门口推向沙发", original=0),
        _anchor("她走到沙发前坐下。", "cut", original=1),
        _anchor("她把茶杯放在桌上。", "cut", original=2),
    ]
    plan = shot_plan.build_shot_plan(anchors, text, sequence_id="seq", total_s=12)

    assert plan.items[0].shot_type == "cut"
    assert any("场景切换" in w for w in plan.warnings)


def test_move_after_skipped_anchor_falls_back_to_cut():
    anchors = [
        _anchor("她站在门口。", "move", "镜头推进", original=0),
        _anchor("她把茶杯放在桌上。", "cut", original=2),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=10)

    assert plan.items[0].shot_type == "cut"
    assert any("不再相邻" in w for w in plan.warnings)


def test_move_single_on_last_anchor_is_kept():
    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "move_single", "镜头缓慢拉远", original=1),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=10)

    assert plan.items[-1].shot_type == "move_single"
    assert plan.items[-1].video_mode == "shot_move_single"
    assert plan.items[-1].end_anchor == ""


def test_move_single_without_desc_falls_back_to_cut():
    anchors = [_anchor("她站在门口。", "move_single", "", original=0)]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=10)

    assert plan.items[0].shot_type == "cut"
    assert any("缺少 transition_desc" in w for w in plan.warnings)


def test_same_scene_detects_cues_and_missing_offsets():
    text = "A。\n\n片刻后，B。"
    assert shot_plan.same_scene({"anchor_offset": 2}, {"anchor_offset": len(text)}, text) is False
    assert shot_plan.same_scene({"anchor_offset": 2}, {"anchor_offset": 8}, "A。\n\nB。") is True
    assert shot_plan.same_scene({"anchor_offset": None}, {"anchor_offset": 8}, text) is False


def test_duration_budget_is_clamped_and_warned():
    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "cut", original=1),
        _anchor("她把茶杯放在桌上。", "cut", original=2),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=100)
    # cut 是单图固定机位延伸：上限 6s（2026-11-09 离线模拟定案），3 镜合计 18s。
    assert sum(item.duration_s for item in plan.items) == 18
    assert all(item.duration_s == 6 for item in plan.items)
    assert any("上限" in w for w in plan.warnings)

    short = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=3)
    assert sum(item.duration_s for item in short.items) == 9
    assert any("抬到" in w for w in short.warnings)


def test_duration_limits_follow_shot_type():
    # cut＝单图延伸（3–6s）；move_single＝单图运镜（4–10s）；move＝双图运镜（5–15s）。
    assert shot_plan.shot_limits("cut") == (3, 6)
    assert shot_plan.shot_limits("move_single") == (4, 10)
    assert shot_plan.shot_limits("move") == (5, 15)

    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "move", "镜头从门口推进到沙发，跟随她坐下", original=1),
        _anchor("她把茶杯放在桌上。", "cut", original=2),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=4)
    # 4s 低于三镜下限之和 11s（cut≥3 + move≥5 + cut≥3）⇒ 抬到 11s，且各镜不越界。
    assert plan.total_duration_s == 11
    assert [item.duration_s for item in plan.items] == [3, 5, 3]

    big = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=100)
    # 上限之和 27s（cut≤6 + move≤15 + cut≤6），硬切不会被分到 10s。
    assert big.total_duration_s == 27
    assert [item.duration_s for item in big.items] == [6, 15, 6]


def test_fixed_template_duration_is_used_when_no_budget():
    anchors = [_anchor("她站在门口。", "cut", original=0)]
    plan = shot_plan.build_shot_plan(
        anchors, TEXT, sequence_id="seq", total_s=0, caps_duration=8,
    )
    assert plan.items[0].duration_s == 8
    assert plan.total_duration_s == 8
    assert any("固定时长" in w for w in plan.warnings)


def test_estimated_total_falls_in_default_range():
    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "cut", original=1),
    ]
    plan = shot_plan.build_shot_plan(anchors, TEXT, sequence_id="seq", total_s=0)
    assert 8 <= plan.total_duration_s <= 30
    assert any("估算整楼总时长" in w for w in plan.warnings)


def test_prepare_storyboard_prepends_establish_when_first_anchor_is_late():
    text = "仓库门口的红毯一直铺到中央台子。\n\n门轴一响，她走进来。\n\n她抬手整理头纱。"
    anchors = [
        _anchor("门轴一响，她走进来。", "cut", original=0),
        _anchor("她抬手整理头纱。", "cut", original=1),
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["腓特烈"])

    assert prepared[0]["shot_type"] == "establish"
    assert prepared[0]["anchor"] == "仓库门口的红毯一直铺到中央台子。"
    assert prepared[0]["framing"] == "远景/环境空镜"
    assert prepared[1]["anchor"] == "门轴一响，她走进来。"


def test_prepare_storyboard_promotes_environment_first_anchor():
    anchors = [
        {
            "anchor": "几只蛮啾踩在梯子上往横梁挂花。", "shot_type": "cut",
            "composition": "远景", "subjects": ["蛮啾"], "original_index": 0,
        },
        {
            "anchor": "她推动木门走进来。", "shot_type": "cut",
            "composition": "全身", "subjects": ["腓特烈"], "original_index": 1,
        },
    ]
    text = "几只蛮啾踩在梯子上往横梁挂花。\n\n她推动木门走进来。"
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["腓特烈"])

    assert prepared[0]["shot_type"] == "establish"
    assert prepared[0]["framing"] == "远景"
    assert len(prepared) == 2


def test_prepare_storyboard_keeps_first_paragraph_anchor_untouched():
    anchors = [_anchor("她站在门口。", "cut", original=0)]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, TEXT, ["她"])
    assert len(prepared) == 1
    assert prepared[0]["shot_type"] == "cut"


def test_prepare_storyboard_demotes_mislabeled_establish_with_main_actor():
    """判据把「以主要角色开头的人物动作」误标成 establish ⇒ 降回 cut。

    2026-11-09 多文段实测：开场人物镜被 establish 清成空景，图片提示词只剩环境词，
    与锚点正文对不上；但开场空镜允许蛮啾/群演入画，不能只看 subjects ∩ main。
    """
    text = "角色甲没有立刻接话，一步步走到院门口的槐树下。\n\n她抬手抚过树皮。"
    anchors = [
        {"anchor": "角色甲没有立刻接话，一步步走到院门口的槐树下。",
         "shot_type": "establish", "composition": "远景",
         "subjects": ["角色甲"], "original_index": 0},
        {"anchor": "她抬手抚过树皮。", "shot_type": "cut",
         "subjects": ["角色甲"], "original_index": 1},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["角色甲"])

    assert prepared[0]["shot_type"] == "cut"
    assert prepared[0]["anchor"] == "角色甲没有立刻接话，一步步走到院门口的槐树下。"


def test_prepare_storyboard_demotes_pronoun_led_establish():
    """锚点句以人称代词开头 ⇒ 人物动作句，不是环境空镜（某西幻作品实锤）。"""
    text = "她没有立刻接话，走到院门口的槐树下。\n\n她抬手抚过树皮。"
    anchors = [
        {"anchor": "她没有立刻接话，走到院门口的槐树下。",
         "shot_type": "establish", "composition": "远景",
         "subjects": ["角色甲"], "original_index": 0},
        {"anchor": "她抬手抚过树皮。", "shot_type": "cut",
         "subjects": ["角色甲"], "original_index": 1},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["角色甲"])

    assert prepared[0]["shot_type"] == "cut"


def test_image_timing_carried_to_shot():
    """图片相对本镜的位置（mid/end）必须带到 ShotPlanItem，供视频提示词声明。"""
    anchors = [{
        "anchor": "她站在门口。", "shot_type": "cut", "subjects": ["她"],
        "image_timing": "mid", "original_index": 0,
    }]
    plan = shot_plan.build_shot_plan(anchors, "她站在门口。", sequence_id="seq", total_s=0)

    assert plan.items[0].image_timing == "mid"


def test_shot_locale_carried_to_plan():
    """每镜 locale 必须带到 ShotPlanItem（否则结尾卧房会带进剑场镜头）。"""
    anchors = [{
        "anchor": "她站在剑场中央。", "shot_type": "cut", "subjects": ["她"],
        "locale": "剑场", "original_index": 0,
    }]
    plan = shot_plan.build_shot_plan(anchors, "她站在剑场中央。", sequence_id="seq", total_s=0)

    assert plan.items[0].locale == "剑场"


def test_dialogue_anchor_replaced_by_visual_sentence():
    """判据把纯对白当 anchor 时，必须换成前一句画面动作句（用户 2026-11-10 实锤）。"""
    text = (
        "她把剑搁在门后，走过去在石案边坐下。\n\n"
        "「前殿点卯的人比往年多一倍。」\n\n她收起单子。"
    )
    anchors = [{
        "anchor": "前殿点卯的人比往年多一倍。", "shot_type": "cut", "subjects": ["她"],
        "original_index": 0,
    }]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["她"])

    assert prepared[0]["anchor"] == "她把剑搁在门后，走过去在石案边坐下。"


def test_cross_paragraph_dialogue_not_injected():
    """跨段对白不属于本镜，不能塞进口型（否则石案镜头会出现前殿点卯台词）。"""
    text = "她把单子摊平。\n\n「你看这一行。」\n\n她收起单子。"
    anchors = [{
        "anchor": "她把单子摊平。", "shot_type": "cut", "subjects": ["她"],
        "original_index": 0, "video_beats": [{
            "duration_s": 5, "cut": False, "framing": "中景",
            "camera_motion": "固定", "subject_motion": "她把单子摊平",
            "description": "摊平席次单",
        }],
    }, {
        "anchor": "她收起单子。", "shot_type": "cut", "subjects": ["她"],
        "original_index": 1,
    }]
    plan = shot_plan.build_shot_plan(anchors, text, sequence_id="seq", total_s=0)

    assert "开口说话" not in plan.items[0].video_beats[-1]["description"]
    assert any("不在本镜第一段" in warning for warning in plan.warnings)


def test_dialogue_without_speaking_action_warns():
    """对白不做音频也要拍口型：beats 没写说话动作时给出告警（不改稿）。"""
    text = "她把单子摊平。「你看这一行。」\n\n她收起单子。"
    anchors = [
        {"anchor": "她把单子摊平。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 0, "video_beats": [{
             "duration_s": 5, "cut": False, "framing": "中景",
             "camera_motion": "固定", "subject_motion": "她把单子摊平",
             "description": "摊平席次单",
         }]},
        {"anchor": "她收起单子。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 1},
    ]
    plan = shot_plan.build_shot_plan(anchors, text, sequence_id="seq", total_s=0)

    assert any("未写说话动作" in warning for warning in plan.warnings)
    # 确定性补口型：末段描述必须出现「嘴唇开合（无声音）」，不依赖判据是否听话。
    assert "嘴唇开合（无声音）" in plan.items[0].video_beats[-1]["description"]


def test_dialogue_with_speaking_action_does_not_warn():
    text = "她把单子摊平。「你看这一行。」\n\n她收起单子。"
    anchors = [
        {"anchor": "她把单子摊平。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 0, "video_beats": [{
             "duration_s": 5, "cut": False, "framing": "中景",
             "camera_motion": "固定", "subject_motion": "她抬起头，嘴唇开合",
             "description": "她开口念出纸上那一行的名字",
         }]},
        {"anchor": "她收起单子。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 1},
    ]
    plan = shot_plan.build_shot_plan(anchors, text, sequence_id="seq", total_s=0)

    assert not any("未写说话动作" in warning for warning in plan.warnings)


def test_back_facing_dialogue_does_not_inject_mouth():
    """背对/背影构图不补口型：否则会强迫模型把脸转过来，破坏构图。"""
    text = "她背对镜头把单子摊平。「你看这一行。」\n\n她收起单子。"
    anchors = [
        {"anchor": "她背对镜头把单子摊平。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 0, "video_beats": [{
             "duration_s": 5, "cut": False, "framing": "背影",
             "camera_motion": "固定", "subject_motion": "她背对镜头，把单子摊平",
             "description": "背影，席次单摊在案上",
         }]},
        {"anchor": "她收起单子。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 1},
    ]
    plan = shot_plan.build_shot_plan(anchors, text, sequence_id="seq", total_s=0)

    assert "嘴唇开合" not in plan.items[0].video_beats[-1]["description"]
    assert any("背对" in warning for warning in plan.warnings)


def test_back_facing_with_turn_back_still_injects_mouth():
    """背对后「回头」⇒ 脸已可见，仍补口型。"""
    text = "她背对镜头把单子摊平，随后回头。「你看这一行。」\n\n她收起单子。"
    anchors = [
        {"anchor": "她背对镜头把单子摊平，随后回头。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 0, "video_beats": [{
             "duration_s": 5, "cut": False, "framing": "近景",
             "camera_motion": "固定", "subject_motion": "她背对镜头，随后回头看他",
             "description": "她回过头，脸转向镜头",
         }]},
        {"anchor": "她收起单子。", "shot_type": "cut", "subjects": ["她"],
         "original_index": 1},
    ]
    plan = shot_plan.build_shot_plan(anchors, text, sequence_id="seq", total_s=0)

    assert "嘴唇开合（无声音）" in plan.items[0].video_beats[-1]["description"]


def test_prepare_storyboard_demotes_establish_when_head_clause_names_main():
    """subjects 为空但首分句点名主要角色 ⇒ 仍是人物镜（判据常把这类标成环境空镜）。"""
    text = "角色甲的手停在半空。\n\n她站直身子。"
    anchors = [
        {"anchor": "角色甲的手停在半空。", "shot_type": "establish",
         "composition": "环境空镜", "subjects": [], "original_index": 0},
        {"anchor": "她站直身子。", "shot_type": "cut",
         "subjects": ["角色甲"], "original_index": 1},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["角色甲"])

    assert prepared[0]["shot_type"] == "cut"


def test_prepare_storyboard_keeps_environment_establish_when_name_is_later_clause():
    """主要角色名只在后面的分句出现 ⇒ 仍是环境开场（「日头…她便回来了」）。"""
    text = "日头爬到檐角，钟磬声刚落，角色甲便从前殿回来了。\n\n她走进院门。"
    anchors = [
        {"anchor": "日头爬到檐角，钟磬声刚落，角色甲便从前殿回来了。",
         "shot_type": "establish", "composition": "环境空镜",
         "subjects": [], "original_index": 0},
        {"anchor": "她走进院门。", "shot_type": "cut",
         "subjects": ["角色甲"], "original_index": 1},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["角色甲"])

    assert prepared[0]["shot_type"] == "establish"


def test_prepare_storyboard_keeps_environment_establish_with_mascots():
    """开场空镜里有蛮啾/群演（同属 [在场]）仍保持 establish——它们不是「主要角色」。"""
    text = "旧仓库的门框缠着白纱，几只蛮啾在梯子上挂花。\n\n腓特烈从侧门走进来。"
    anchors = [
        {"anchor": "旧仓库的门框缠着白纱，几只蛮啾在梯子上挂花。",
         "shot_type": "establish", "composition": "环境空镜",
         "subjects": ["蛮啾"], "original_index": 0},
        {"anchor": "腓特烈从侧门走进来。", "shot_type": "cut",
         "subjects": ["腓特烈"], "original_index": 1},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(
        anchors, text, ["腓特烈", "蛮啾"],
    )

    assert prepared[0]["shot_type"] == "establish"
    assert prepared[1]["anchor"] == "腓特烈从侧门走进来。"


def test_prepare_storyboard_skips_establish_when_opening_mentions_main_actor():
    """开场句里已有主要角色 ⇒ 不是环境镜，补开场会把人物清空（2026-11-09 实锤）。"""
    text = "角色甲的手停在半空。\n\n她站直身子。\n\n她走到槐树下。"
    anchors = [
        {"anchor": "她走到槐树下。", "shot_type": "cut",
         "subjects": ["角色甲"], "original_index": 0},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["角色甲"])

    assert len(prepared) == 1
    assert prepared[0]["shot_type"] == "cut"
    assert prepared[0]["anchor"] == "她走到槐树下。"


def test_prepare_storyboard_inserts_entrance_shot():
    text = (
        "旧仓库的门框缠着白纱，几只蛮啾在梯子上挂花。\n\n"
        "腓特烈从侧门走进来。\n\n"
        "白色的婚纱堆到脚踝。"
    )
    anchors = [
        {"anchor": "旧仓库的门框缠着白纱，几只蛮啾在梯子上挂花。",
         "shot_type": "establish", "subjects": ["蛮啾"], "original_index": 0},
        {"anchor": "白色的婚纱堆到脚踝。",
         "shot_type": "cut", "subjects": ["腓特烈"], "original_index": 1},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["腓特烈"])

    assert [item["shot_type"] for item in prepared] == ["establish", "cut", "cut"]
    assert prepared[1]["anchor"] == "腓特烈从侧门走进来。"
    assert prepared[1]["framing"] == "全身"
    assert prepared[1]["sound_cue"] == "门轴声"
    assert prepared[1]["subjects"] == ["腓特烈"]
    assert prepared[0]["original_index"] < prepared[1]["original_index"] < prepared[2]["original_index"]


def test_prepare_storyboard_keeps_existing_entrance_shot():
    text = "旧仓库的门框缠着白纱。\n\n腓特烈从侧门走进来。\n\n白色的婚纱堆到脚踝。"
    anchors = [
        {"anchor": "旧仓库的门框缠着白纱。", "shot_type": "establish",
         "subjects": [], "original_index": 0},
        {"anchor": "腓特烈从侧门走进来。", "shot_type": "cut",
         "subjects": ["腓特烈"], "original_index": 1},
        {"anchor": "白色的婚纱堆到脚踝。", "shot_type": "cut",
         "subjects": ["腓特烈"], "original_index": 2},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["腓特烈"])
    assert len(prepared) == 3
    assert prepared[1]["anchor"] == "腓特烈从侧门走进来。"


def test_establish_video_mode_follows_camera_move():
    anchors = [
        {
            "anchor": "仓库门口的红毯一直铺到中央台子。", "shot_type": "establish",
            "transition_desc": "镜头从门口沿红毯缓慢推近台子", "original_index": 0,
        },
        {
            "anchor": "门轴一响，她走进来。", "shot_type": "cut",
            "transition_desc": "", "original_index": 1,
        },
    ]
    text = "仓库门口的红毯一直铺到中央台子。\n\n门轴一响，她走进来。"
    plan = shot_plan.build_shot_plan(anchors, text, sequence_id="seq", total_s=10)
    assert plan.items[0].video_mode == "shot_establish_move"

    # 首镜环境镜没写运镜 ⇒ 默认沿主动线推近（用户口径「从红毯运镜延伸到台子」）。
    default_move = [dict(anchors[0], transition_desc=""), *anchors[1:]]
    plan_default = shot_plan.build_shot_plan(
        default_move, text, sequence_id="seq", total_s=10,
    )
    assert plan_default.items[0].video_mode == "shot_establish_move"
    assert plan_default.items[0].transition_desc == shot_plan._DEFAULT_ESTABLISH_MOVE

    # 非首镜的环境镜尊重判据的「固定」，不强行加运镜。
    later_fixed = [
        anchors[0],
        {"anchor": "仓库里安静了一瞬。", "shot_type": "establish", "transition_desc": "",
         "camera_motion": "固定", "original_index": 1},
    ]
    plan_later = shot_plan.build_shot_plan(
        later_fixed, "仓库门口的红毯一直铺到中央台子。\n\n仓库里安静了一瞬。",
        sequence_id="seq", total_s=10,
    )
    assert plan_later.items[1].video_mode == "shot_establish"


def test_coverage_anchor_intents_keeps_first_and_last():
    items = [{"anchor": f"第{i}句。", "score": 100 - i} for i in range(20)]
    picked = scene_illustration.coverage_anchor_intents(items, 5)
    assert len(picked) == 5
    assert picked[0]["anchor"] == "第0句。"
    assert picked[-1]["anchor"] == "第19句。"


def test_closeups_merge_into_fullbody_keyframe():
    text = (
        "旧仓库门框缠着白纱。\n\n"
        "腓特烈从侧门走进来，白色婚纱。\n\n"
        "上身是束腰的，胸部线条被婚纱托起。\n\n"
        "脚上是白色的高跟，踩在红毯上。"
    )
    anchors = [
        {"anchor": "旧仓库门框缠着白纱。", "shot_type": "establish",
         "composition": "环境空镜", "subjects": [], "original_index": 0},
        {"anchor": "腓特烈从侧门走进来，白色婚纱。", "shot_type": "cut",
         "composition": "全身", "framing": "全身", "subjects": ["腓特烈"],
         "original_index": 1},
        {"anchor": "上身是束腰的，胸部线条被婚纱托起。", "shot_type": "cut",
         "composition": "特写", "framing": "胸部俯拍", "subjects": ["腓特烈"],
         "original_index": 2},
        {"anchor": "脚上是白色的高跟，踩在红毯上。", "shot_type": "cut",
         "composition": "特写", "framing": "脚部特写", "subjects": ["腓特烈"],
         "original_index": 3},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(anchors, text, ["腓特烈"])

    # 近景不再单独成帧：只剩 establish + 全身两个关键帧。
    assert len(prepared) == 2
    assert prepared[1]["shot_type"] == "cut"
    beats = prepared[1]["video_beats"]
    assert len(beats) == 3
    assert [beat["cut"] for beat in beats] == [False, True, True]
    assert beats[1]["framing"] == "胸部俯拍"
    assert beats[2]["framing"] == "脚部特写"
    assert "胸部" in beats[1]["description"]


def test_closeup_does_not_merge_into_establish_or_move():
    text = "旧仓库门框缠着白纱。\n\n她走进来。\n\n胸部特写。"
    establish_then_closeup = [
        {"anchor": "旧仓库门框缠着白纱。", "shot_type": "establish", "subjects": [],
         "original_index": 0},
        {"anchor": "胸部特写。", "shot_type": "cut", "composition": "特写",
         "framing": "胸部俯拍", "subjects": ["腓特烈"], "original_index": 1},
    ]
    prepared = shot_plan.prepare_storyboard_anchors(
        establish_then_closeup, text, ["腓特烈"],
    )
    assert len(prepared) == 2                       # 环境镜不能吞近景

    move_then_closeup = [
        {"anchor": "旧仓库门框缠着白纱。", "shot_type": "establish", "subjects": [],
         "original_index": 0},
        {"anchor": "她走进来。", "shot_type": "move", "composition": "全身",
         "subjects": ["腓特烈"], "transition_desc": "镜头跟随她向前",
         "original_index": 1},
        {"anchor": "胸部特写。", "shot_type": "cut", "composition": "特写",
         "framing": "胸部俯拍", "subjects": ["腓特烈"], "original_index": 2},
    ]
    prepared_move = shot_plan.prepare_storyboard_anchors(
        move_then_closeup, text, ["腓特烈"],
    )
    assert len(prepared_move) == 3                  # 双图运镜不能被硬切断开


def test_video_beats_drive_keyframe_duration():
    anchors = [{
        "anchor": "她走进来，白色婚纱。", "shot_type": "cut",
        "composition": "全身", "subjects": ["腓特烈"], "original_index": 0,
        "video_beats": [
            {"duration_s": 3, "cut": False, "framing": "全身",
             "camera_position": "红毯尽头·平视（机位在台前，正对她）",
             "camera_motion": "跟拍", "subject_motion": "她沿红毯向前走"},
            {"duration_s": 3, "cut": True, "framing": "胸部俯拍",
             "camera_position": "她侧前方·略俯视（机位在台侧，拍胸口）",
             "camera_motion": "固定", "subject_motion": "胸口随步伐起伏"},
            {"duration_s": 2, "cut": True, "framing": "脚部特写",
             "camera_position": "地面·俯视（机位贴地，拍鞋跟）",
             "camera_motion": "固定", "subject_motion": "裙摆扫过鞋跟"},
        ],
    }]
    plan = shot_plan.build_shot_plan(
        anchors, "她走进来，白色婚纱。", sequence_id="seq", total_s=30,
    )
    assert plan.items[0].duration_s == 8
    assert plan.total_duration_s == 8
    assert len(plan.items[0].video_beats) == 3
    assert plan.items[0].video_beats[0]["camera_position"] == (
        "红毯尽头·平视（机位在台前，正对她）"
    )
    assert any("video_beats" in warning for warning in plan.warnings)


def test_parse_video_beats_from_judge_json():
    raw = (
        '{"anchors": [{"anchor": "她走进来。", "composition": "全身", '
        '"subjects": ["腓特烈"], "shot_type": "cut", "video_beats": ['
        '{"duration_s": 3, "cut": false, "framing": "全身", '
        '"camera_position": "红毯尽头·平视", '
        '"camera_motion": "跟拍", "subject_motion": "她向前走", '
        '"description": "沿红毯走", "viewpoint": "pov"}, '
        '{"duration_s": 2, "cut": true, "framing": "胸部俯拍", '
        '"camera_position": "她侧前方·略俯视", '
        '"camera_motion": "固定", "subject_motion": "胸口起伏", '
        '"viewpoint": "observer"}]}]}'
    )
    items = scene_illustration.parse_anchor_intents(raw)
    assert len(items) == 1
    beats = items[0]["video_beats"]
    assert len(beats) == 2
    assert beats[1]["cut"] is True
    assert beats[1]["framing"] == "胸部俯拍"
    assert beats[1]["camera_position"] == "她侧前方·略俯视"
    assert beats[1]["duration_s"] == 2
    # 逐拍视角必须全链保留：判据 → fit_video_beats → video_prompt。
    assert [beat["viewpoint"] for beat in beats] == ["pov", "observer"]
    fitted = shot_plan.fit_video_beats(beats)
    assert [beat["viewpoint"] for beat in fitted] == ["pov", "observer"]


def test_establish_duration_limit():
    assert shot_plan.shot_limits("establish") == (5, 10)
    anchors = [{
        "anchor": "仓库门口的红毯一直铺到中央台子。", "shot_type": "establish",
        "transition_desc": "", "original_index": 0,
    }]
    plan = shot_plan.build_shot_plan(anchors, "仓库门口的红毯一直铺到中央台子。", total_s=30)
    assert plan.items[0].duration_s == 10


def test_image_time_second_follows_timing():
    """图片位置必须换算成**具体秒数**（用户 2026-11-10：不能只说中段/尾帧）。"""
    assert shot_plan.image_time_second(0, "start") == 0
    assert shot_plan.image_time_second(8, "start") == 0
    assert shot_plan.image_time_second(8, "mid") == 4
    assert shot_plan.image_time_second(8, "end") == 8
    assert shot_plan.image_time_second(0, "end") == 0


def test_resolve_image_position_matches_anchor_beat():
    """锚点落在第 2 段 ⇒ 图片不是首帧，位置是 3s（用户 2026-11-10 实锤）。"""
    item = shot_plan.ShotPlanItem(
        shot_index=0, shot_type="establish",
        start_anchor="日头爬到檐角那株狐尾槐的树冠上，钟磬声才刚落定，示例角色甲便从前殿回来了。",
        end_anchor="", transition_desc="", duration_s=15,
        video_mode="shot_establish_move", image_key="s:0", end_image_key="",
        image_timing="start",
        video_beats=[
            {"duration_s": 3, "description": "日头照在狐尾槐树冠上，钟磬声刚落，院落窄道空着"},
            {"duration_s": 3, "description": "她从宫道方向走来，衣摆随急步晃动",
             "subject_motion": "示例角色甲穿窄道快步走向院门，狐耳尖压着"},
        ],
    )

    assert shot_plan.resolve_image_position(item) == (3, "at")


def test_resolve_image_position_skips_environment_beat_for_person_anchor():
    """锚点有人物、尾句匹配不到时，不能把图片位让给 0s 的纯环境段。"""
    item = shot_plan.ShotPlanItem(
        shot_index=0, shot_type="cut",
        start_anchor="她望着院门，像是自言自语。",
        end_anchor="", transition_desc="", duration_s=6,
        video_mode="shot_cut", image_key="s:0", end_image_key="",
        image_timing="start",
        video_beats=[
            {"duration_s": 3, "description": "暮色里的院门与半枯老槐，门外土路空着"},
            {"duration_s": 3, "description": "枯槐下她侧身而立，几缕发丝贴着脸"},
        ],
    )

    assert shot_plan.resolve_image_position(item) == (3, "at")


def test_resolve_image_position_move_stays_first_frame():
    item = shot_plan.ShotPlanItem(
        shot_index=0, shot_type="move",
        start_anchor="她推门进来。", end_anchor="她坐下。",
        transition_desc="镜头推进", duration_s=8,
        video_mode="shot_move", image_key="s:0", end_image_key="s:1",
        video_beats=[{"duration_s": 3, "description": "她推门进来。"}],
    )

    assert shot_plan.resolve_image_position(item) == (0, "start")


def test_bind_character_images_orders_and_reports_missing():
    """图片2 起步绑定角色底图；缺底图的角色默认不占编号，避免后续整体前移。"""
    bindings, missing = shot_plan.bind_character_images(
        ["示例角色甲", "腓特烈", "蛮啾"],
        {"示例角色甲": "a.png", "腓特烈": "b.png"},
    )
    assert bindings == [
        {"name": "示例角色甲", "image_index": 2, "base_image": "a.png", "has_base_image": True},
        {"name": "腓特烈", "image_index": 3, "base_image": "b.png", "has_base_image": True},
    ]
    assert missing == ["蛮啾"]


def test_bind_character_images_include_missing_keeps_planned_slot():
    """审阅侧预排槽位：底图没配也占图片3，标 has_base_image=False。"""
    bindings, missing = shot_plan.bind_character_images(
        ["示例角色甲", "蛮啾"],
        {"示例角色甲": "a.png"},
        include_missing=True,
    )
    assert bindings == [
        {"name": "示例角色甲", "image_index": 2, "base_image": "a.png", "has_base_image": True},
        {"name": "蛮啾", "image_index": 3, "base_image": "", "has_base_image": False},
    ]
    assert missing == ["蛮啾"]


def test_bind_character_images_shares_same_source_and_dedupes_names():
    bindings, missing = shot_plan.bind_character_images(
        ["甲", "甲", "乙"], {"甲": "same.png", "乙": "same.png"},
    )
    assert bindings == [
        {"name": "甲", "image_index": 2, "base_image": "same.png", "has_base_image": True},
        {
            "name": "乙", "image_index": 2, "base_image": "same.png",
            "has_base_image": True, "shared": True,
        },
    ]
    assert missing == []


def test_shot_cast_names_ignores_roles_only_in_neighbouring_paragraph():
    """出场名单只认本镜分镜/锚点句：相邻段落里提名的角色不得提前绑定底图。"""
    cast = shot_plan.shot_cast_names(
        ["蛮啾"], ["蛮啾"], [], ["腓特烈", "蛮啾"],
        anchor_text="旧仓库的木门被拆掉了一半，几只蛮啾踩在梯子上挂花。",
        beats=[{"description": "白纱鼓动，蛮啾在梯子上挂花", "subject_motion": "白纱鼓动"}],
    )

    assert cast == ["蛮啾"]


def test_shot_cast_names_keeps_anchor_named_role_even_when_beats_clear():
    """环境镜 beats 没写人，但锚点句点名了配置角色 ⇒ 仍要绑定（用户 2026-11-10 口径）。"""
    cast = shot_plan.shot_cast_names(
        [], [], [], ["示例角色甲"],
        anchor_text="示例角色甲便从前殿回来了。",
        beats=[{"description": "庭院空着", "subject_motion": ""}],
    )

    assert cast == ["示例角色甲"]


def test_shot_cast_names_keeps_prompt_actors_and_filters_unknown_subjects():
    """提示词 actors 原样保留（含群演）；锚点/帧主体只收配置角色；顺序去重。"""
    cast = shot_plan.shot_cast_names(
        ["蛮啾"], ["蛮啾", "腓特烈"], ["腓特烈"], ["腓特烈"],
        anchor_text="蛮啾跳下梯子。", beats=[],
    )

    assert cast == ["蛮啾", "腓特烈"]


def test_ownership_errors_flags_bare_body_part_and_passive():
    errors = shot_plan.ownership_errors("手探出画面；手腕被拽；腰侧、腹股沟被隔布蹭磨")
    assert any("无主身体部位" in error for error in errors)
    assert any("被动句缺施动者" in error for error in errors)


def test_ownership_errors_accepts_named_bindings():
    text = (
        "镜头角色的手牵着示例角色甲的手腕；"
        "示例角色甲的腰侧、腹股沟被镜头角色的隔布蹭磨"
    )
    assert shot_plan.ownership_errors(text, cast=["示例角色甲"]) == []


def test_ownership_errors_requires_name_in_nsfw():
    text = "镜头角色的肉棒贴她臂弯外侧"
    assert shot_plan.ownership_errors(text, cast=["示例角色甲"], require_named=True)
    assert shot_plan.ownership_errors(text, cast=["示例角色甲"], require_named=False) == []


def test_ownership_errors_allows_pronoun_after_name_in_sentence():
    text = "镜头角色的肉棒贴示例角色甲的臂弯外侧，沿她的腕骨碾到指根"
    assert shot_plan.ownership_errors(text, cast=["示例角色甲"], require_named=True) == []


def test_ownership_errors_allows_subject_continuity_within_sentence():
    """`，` 之内主语延续：前面点名后，后面的裸身体部位合法。"""
    text = "腓特烈低头，肩膀轻抖"
    assert shot_plan.ownership_errors(text, cast=["腓特烈"], require_named=True) == []


def test_ownership_errors_flags_new_sentence_without_subject():
    """`；` 换句后主语不延续：新句子必须重新点名。"""
    text = "腓特烈低头；肩膀轻抖"
    assert shot_plan.ownership_errors(text, cast=["腓特烈"], require_named=True)


def test_with_frame_focus_keeps_paragraph_and_appends_anchor():
    """段落摘录不截断，只在末尾追加「本帧落点」定位本镜那一刻。"""
    paragraph = "她推门进来。她把剑搁在门后。她走到石案边坐下。"
    out = shot_plan.with_frame_focus(paragraph, "她把剑搁在门后。")

    assert out.startswith(paragraph)
    focus = out.split("【本帧落点】", 1)[1]
    assert "她把剑搁在门后。" in focus
    # 只画锚点这一瞬间：段落里前后的动作只作静态事实，不能画进本帧。
    assert "只取锚点这一句" in focus
    assert "不得画进本帧" in focus
    empty = shot_plan.with_frame_focus("", "她站着。")
    assert empty.startswith("【本帧落点】首帧的定格画面")
    assert "不写过程、不写后续动作" in empty
    assert empty.endswith("锚点：她站着。")


def test_plan_sets_image_time_s():
    anchors = [{"anchor": "她走进来，白色婚纱。", "shot_type": "cut", "original_index": 0}]
    plan = shot_plan.build_shot_plan(anchors, "她走进来，白色婚纱。", total_s=6)
    assert plan.items[0].image_timing == "start"
    assert plan.items[0].image_time_s == 0
    assert plan.items[0].duration_s == 6


# ── 全自动连续锚点（2026-11-12）──────────────────────────────────────────────

def test_parse_wardrobe_anchors_reads_present_parentheses():
    """`[在场]` 括号里的服装状态必须成为角色服装真源；动作括号不能算。"""
    present = "腓特烈（白色婚纱，项圈藏在头纱下），蕾娜（黑色修女服），蛮啾（若干，端奶瓶）"
    assert shot_plan.parse_wardrobe_anchors(present) == {
        "腓特烈": "白色婚纱，项圈藏在头纱下",
        "蕾娜": "黑色修女服",
    }


def test_parse_wardrobe_anchors_skips_action_only_parentheses():
    assert shot_plan.parse_wardrobe_anchors("她（入夜检查功法→睡中）") == {}
    assert shot_plan.parse_wardrobe_anchors("我（白色婚纱）") == {}


def test_wardrobe_anchor_for_matches_short_name_and_skips_unknown():
    anchored = shot_plan.wardrobe_anchor_for(
        ["腓特烈大帝", "蛮啾"], {"腓特烈": "白色婚纱"},
    )
    assert "腓特烈大帝：白色婚纱" in anchored
    assert "蛮啾" not in anchored


def test_build_plan_assigns_scene_anchor_and_wardrobe_automatically():
    """同场景各镜共用空间锚点；换场景才换锚点；服装从 `[在场]` 自动派生。"""
    text = (
        "旧仓库的木门前铺着红毯，门框缠着白纱，红毯一直铺到仓库中央的木板台子。\n\n"
        "腓特烈从侧门走进来，白色婚纱拖在红毯上。\n\n"
        "她停在台前。\n\n"
        "镜头一转，卧室里只有床头的灯亮着。\n\n"
        "她坐在床边。"
    )
    anchors = [
        {"anchor": text.split("\n\n")[0], "shot_type": "establish", "original_index": 0},
        {"anchor": "腓特烈从侧门走进来，白色婚纱拖在红毯上。", "shot_type": "cut",
         "original_index": 1},
        {"anchor": "她停在台前。", "shot_type": "cut", "original_index": 2},
        {"anchor": "她坐在床边。", "shot_type": "cut", "original_index": 3},
    ]
    plan = shot_plan.build_shot_plan(
        anchors, text, total_s=20, main_actors=["腓特烈"],
        present="腓特烈（白色婚纱，项圈藏在头纱下），蛮啾（若干，端奶瓶）",
    )

    assert [item.scene_key for item in plan.items] == [
        "scene-0", "scene-0", "scene-0", "scene-1",
    ]
    assert plan.items[0].scene_anchor == plan.items[1].scene_anchor == plan.items[2].scene_anchor
    assert "红毯" in plan.items[0].scene_anchor
    assert "侧门" in plan.items[1].scene_anchor
    assert "卧室" in plan.items[3].scene_anchor
    assert plan.wardrobe_by_actor == {"腓特烈": "白色婚纱，项圈藏在头纱下"}
    assert shot_plan.wardrobe_anchor_for(
        ["腓特烈"], plan.wardrobe_by_actor,
    ) == "腓特烈：白色婚纱，项圈藏在头纱下"


def test_build_plan_derives_scene_cards_from_scene_windows():
    """每个场景从正文窗口抽一张 scene_card（空间/陈设/机位），不再只留一句锚点。"""
    text = (
        "旧仓库的木门前铺着红毯，门框缠着白纱，红毯一直铺到仓库中央的木板台子。\n\n"
        "腓特烈从侧门走进来，白色婚纱拖在红毯上。\n\n"
        "她停在台前。\n\n"
        "镜头一转，卧室里只有床头的灯亮着。\n\n"
        "她坐在床边。"
    )
    anchors = [
        {"anchor": text.split("\n\n")[0], "shot_type": "establish", "original_index": 0},
        {"anchor": "腓特烈从侧门走进来，白色婚纱拖在红毯上。", "shot_type": "cut",
         "original_index": 1},
        {"anchor": "她停在台前。", "shot_type": "cut", "original_index": 2},
        {"anchor": "她坐在床边。", "shot_type": "cut", "original_index": 3},
    ]
    plan = shot_plan.build_shot_plan(anchors, text, total_s=20, main_actors=["腓特烈"])

    assert set(plan.scene_cards) == {"scene-0", "scene-1"}
    warehouse = plan.scene_cards["scene-0"]
    assert warehouse["scene_key"] == "scene-0"
    assert warehouse["space_type"] == "warehouse"
    props = {entry["item"] for entry in warehouse["fixed_props"]}
    assert {"red carpet", "wooden platform"} <= props
    architecture = {entry["item"] for entry in warehouse["architecture"]}
    assert {"wooden door", "door frame"} <= architecture

    bedroom = plan.scene_cards["scene-1"]
    assert bedroom["space_type"] == "bedroom"
    assert "bed" in {entry["item"] for entry in bedroom["fixed_props"]}


def test_scene_group_keeps_entrance_in_same_scene():
    """人物「走进来/停在台前」不是场景切换；否则首镜与入场镜会拿到两套空间锚点。"""
    text = "旧仓库的木门前铺着红毯。\n\n腓特烈从侧门走进来。\n\n她停在台前。"
    anchors = [
        {"anchor": "旧仓库的木门前铺着红毯。", "shot_type": "cut", "original_index": 0},
        {"anchor": "腓特烈从侧门走进来。", "shot_type": "cut", "original_index": 1},
        {"anchor": "她停在台前。", "shot_type": "cut", "original_index": 2},
    ]
    plan = shot_plan.build_shot_plan(anchors, text, total_s=12)

    assert len({item.scene_key for item in plan.items}) == 1
    assert len({item.scene_anchor for item in plan.items}) == 1


def test_scene_group_splits_on_locale_change_without_explicit_cue():
    """没有「镜头一转」但 locale 从仓库变卧室 ⇒ 必须切开，不能共用仓库锚点。"""
    text = "仓库里很安静。\n\n她坐在床边。"
    anchors = [
        {"anchor": "仓库里很安静。", "shot_type": "cut", "locale": "仓库",
         "original_index": 0},
        {"anchor": "她坐在床边。", "shot_type": "cut", "locale": "卧室",
         "original_index": 1},
    ]
    plan = shot_plan.build_shot_plan(anchors, text, total_s=10)

    assert plan.items[0].scene_key != plan.items[1].scene_key
