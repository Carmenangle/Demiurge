"""全局模式分镜序列后端接线（build_shot_requests）测试。"""

from app.services import agency_scene_comfy as asc

TEXT = "她站在门口。\n\n她走到沙发前坐下。\n\n她把茶杯放在桌上。"


def _anchor(anchor: str, shot_type: str = "cut", desc: str = "", original: int = 0):
    return {
        "anchor": anchor, "shot_type": shot_type, "transition_desc": desc,
        "original_index": original, "score": 10,
    }


def test_build_shot_requests_compiles_cut_and_move(monkeypatch):
    anchors = [
        _anchor("她站在门口。", "cut", original=0),
        _anchor("她走到沙发前坐下。", "move", "镜头从门口推进到沙发，跟随她坐下", original=1),
        _anchor("她把茶杯放在桌上。", "cut", original=2),
    ]
    ctx = {
        "illustration_anchor_intents": anchors,
        "turn_id": "t1",
        "video_duration_hint": 12,
    }
    pool = {"known": [], "actor_values": [], "absent": [], "climax_paragraph": "",
            "request_actors": [], "request_appearance": ""}

    def fake_target(_ctx, _repo, _plan, _clean, _sig, _pool, **_kwargs):
        anchor = str(_kwargs.get("anchor_override") or "")
        return {
            "requested_anchor": anchor, "request_prompt": f"img-{anchor}",
            "request_actors": [], "motion": 1, "plan_retargeted": False,
        }

    def fake_scene_spec(_ctx, _repo, _plan, _clean, _sig, _pool, target):
        return {"narrative": target["requested_anchor"], "actors": [], "rating": "sfw",
                "aspect_ratio": "2:3", "composition": "中景"}

    def fake_profile(_ctx, _plan, scene_spec, _retargeted):
        return dict(scene_spec)

    def fake_env(_ctx, _plan, _sig, target, scene_spec, *, motion):
        return {
            "prompt": f"img-{target['requested_anchor']}",
            "motion": motion, "actors": [], "anchor": target["requested_anchor"],
            "scene_spec": scene_spec,
            "video_config": {"base_url": "http://x", "model": "h3", "size": "2:3", "proxy": ""},
        }

    monkeypatch.setattr(asc, "_wb_comfy_scene_target", fake_target)
    monkeypatch.setattr(asc, "_wb_comfy_scene_spec", fake_scene_spec)
    monkeypatch.setattr(asc, "_wb_comfy_profile", fake_profile)
    monkeypatch.setattr(asc, "_wb_first_person_frame_filter", lambda _ctx, spec: spec)
    monkeypatch.setattr(asc, "_wb_comfy_illustrate_env", fake_env)
    monkeypatch.setattr(asc, "_video_caps_for", lambda _ctx: {"duration": 6})
    monkeypatch.setattr(asc, "_video_skeleton_for", lambda _ctx, _mode: None)
    monkeypatch.setattr(asc.run_trace, "emit", lambda *a, **k: None)

    requests = asc.build_shot_requests(
        ctx, "r1", None, TEXT, {"visible_story": TEXT}, pool,
        primary_motion=1, user_text="",
    )

    assert len(requests) == 3
    assert [r["shot_index"] for r in requests] == [0, 1, 2]
    assert all(r["shot_total"] == 3 for r in requests)
    assert all(r["shot_sequence_id"].startswith("shotseq-r1") for r in requests)
    assert requests[0]["shot_type"] == "cut"
    assert requests[0]["video_request"]["mode"] == "shot_cut"
    assert requests[1]["shot_type"] == "move"
    assert "video_request" not in requests[1]
    assert requests[2]["incoming_shot_index"] == 1
    assert requests[2]["incoming_shot_type"] == "move"
    assert requests[2]["transition_video_request"]["mode"] == "shot_move"
    assert requests[2]["video_request"]["mode"] == "shot_cut"
    assert sum(r["shot_duration_s"] for r in requests) == 12
    # 运镜路径只写一次（① 元信息）；④ 每拍演两锚点之间的正文，绝不重复路径。
    move_prompt = requests[2]["transition_video_request"]["submit"]["prompt"]
    assert move_prompt.count("镜头从门口推进到沙发，跟随她坐下") == 1
    assert "她走到沙发前坐下" in move_prompt


def test_build_shot_requests_compiles_establish_without_actors(monkeypatch):
    anchors = [
        {
            "anchor": "几只蛮啾踩在梯子上往横梁挂花。", "shot_type": "establish",
            "transition_desc": "镜头从木门沿红毯缓慢推近中央台子",
            "framing": "远景/环境空镜", "camera_motion": "缓慢推近",
            "subject_motion": "蛮啾在梯子上挂花，尘埃在斜光里浮动",
            "sound_cue": "门轴声", "original_index": 0, "score": 40,
        },
        {
            "anchor": "她推动木门走进来。", "shot_type": "cut",
            "transition_desc": "", "framing": "全身", "camera_motion": "固定",
            "subject_motion": "她持续前行，裙摆随步伐摆动",
            "sound_cue": "", "original_index": 1, "score": 90,
        },
    ]
    text = "几只蛮啾踩在梯子上往横梁挂花。\n\n她推动木门走进来。"
    ctx = {
        "illustration_anchor_intents": anchors,
        "turn_id": "t1",
        "video_duration_hint": 10,
        "character_base_images": {"腓特烈": "f.png"},
    }
    pool = {"known": ["腓特烈"], "actor_values": [], "absent": [], "climax_paragraph": "",
            "request_actors": [], "request_appearance": ""}

    def fake_target(_ctx, _repo, _plan, _clean, _sig, _pool, **_kwargs):
        anchor = str(_kwargs.get("anchor_override") or "")
        return {
            "requested_anchor": anchor, "request_prompt": f"img-{anchor}",
            "request_actors": ["腓特烈"], "motion": 1, "plan_retargeted": False,
        }

    def fake_scene_spec(_ctx, _repo, _plan, _clean, _sig, _pool, target):
        return {
            "narrative": target["requested_anchor"], "actors": ["腓特烈"],
            "appearance": "腓特烈(白色婚纱)", "wardrobe": "白色婚纱",
            "rating": "sfw", "aspect_ratio": "16:9", "composition": "中景",
        }

    def fake_profile(_ctx, _plan, scene_spec, _retargeted):
        return dict(scene_spec)

    def fake_env(_ctx, _plan, _sig, target, scene_spec, *, motion):
        return {
            "prompt": f"img-{target['requested_anchor']}",
            "motion": motion, "actors": ["腓特烈"], "anchor": target["requested_anchor"],
            "scene_spec": scene_spec,
            "video_config": {"base_url": "http://x", "model": "h3", "size": "16:9", "proxy": ""},
        }

    monkeypatch.setattr(asc, "_wb_comfy_scene_target", fake_target)
    monkeypatch.setattr(asc, "_wb_comfy_scene_spec", fake_scene_spec)
    monkeypatch.setattr(asc, "_wb_comfy_profile", fake_profile)
    monkeypatch.setattr(asc, "_wb_first_person_frame_filter", lambda _ctx, spec: spec)
    monkeypatch.setattr(asc, "_wb_comfy_illustrate_env", fake_env)
    monkeypatch.setattr(asc, "_video_caps_for", lambda _ctx: {"duration": 3})
    monkeypatch.setattr(asc, "_video_skeleton_for", lambda _ctx, _mode: None)
    monkeypatch.setattr(asc.run_trace, "emit", lambda *a, **k: None)

    requests = asc.build_shot_requests(
        ctx, "r1", None, text, {"visible_story": text}, pool,
        primary_motion=1, user_text="",
    )

    assert len(requests) == 2
    establish = requests[0]
    assert establish["shot_type"] == "establish"
    assert establish["actors"] == []                       # 环境镜不选角色 LoRA/底图
    assert establish["scene_spec"]["establish"] is True
    assert establish["scene_spec"]["actors"] == []
    assert establish["scene_spec"]["appearance"] == ""
    assert establish["shot_framing"] == "远景/环境空镜"
    assert establish["shot_characters"] == []
    assert establish["shot_character_refs"] == []
    assert establish["video_request"]["mode"] == "shot_establish_move"
    assert "环境空镜" in establish["video_request"]["submit"]["prompt"]
    assert "人物身份和五官不能发生变化" not in establish["video_request"]["submit"]["prompt"]

    detail = requests[1]
    assert detail["shot_framing"] == "全身"
    assert detail["shot_subject_motion"] == "她持续前行，裙摆随步伐摆动"
    assert detail["shot_characters"] == ["腓特烈"]
    assert detail["shot_character_refs"] == [{
        "name": "腓特烈", "image_index": 2,
        "base_image": "f.png", "has_base_image": True,
    }]
    assert detail["shot_missing_character_refs"] == []
    assert detail["video_request"]["mode"] == "shot_cut"
    assert "全身" in detail["video_request"]["submit"]["prompt"]
    assert "裙摆随步伐摆动" in detail["video_request"]["submit"]["prompt"]


def test_build_shot_requests_protected_narrative_gets_same_frame_second(monkeypatch):
    """protected_narrative 是 Profile 的模型可见叙事，落点秒数必须与 narrative 一致。

    真机风险：漏传 second 会写成「首帧」，把第 N 秒的图又按第一帧画。
    这里同时锁「夹值后的秒数」：raw beats 6+6+4 会被压成 5+6+4，
    图片落点必须写 5s，不能写 raw 的 6s。
    """
    text = "庭院空着。\n\n她站在门口，背对镜头。\n\n她转身走开。"
    anchors = [{
        "anchor": "她站在门口，背对镜头。", "shot_type": "cut",
        "transition_desc": "", "framing": "中景", "camera_motion": "固定",
        "subject_motion": "她停住", "sound_cue": "", "original_index": 0, "score": 10,
        "image_timing": "start",
        "video_beats": [
            {"duration_s": 6, "framing": "远景", "description": "庭院空着"},
            {"duration_s": 6, "framing": "中景", "description": "她站在门口，背对镜头"},
            {"duration_s": 4, "framing": "中景", "description": "她转身走开"},
        ],
    }]
    ctx = {
        "illustration_anchor_intents": anchors,
        "turn_id": "t1",
        "video_duration_hint": 12,
    }
    pool = {"known": [], "actor_values": [], "absent": [], "climax_paragraph": "",
            "request_actors": [], "request_appearance": ""}

    def fake_target(_ctx, _repo, _plan, _clean, _sig, _pool, **_kwargs):
        anchor = str(_kwargs.get("anchor_override") or "")
        return {
            "requested_anchor": anchor, "request_prompt": f"img-{anchor}",
            "request_actors": [], "motion": 1, "plan_retargeted": False,
        }

    def fake_scene_spec(_ctx, _repo, _plan, _clean, _sig, _pool, target):
        anchor = target["requested_anchor"]
        return {
            "narrative": f"@({anchor})@",
            "protected_narrative": f"@({anchor})@",
            "actors": [], "rating": "sfw", "aspect_ratio": "2:3",
        }

    def fake_profile(_ctx, _plan, scene_spec, _retargeted):
        return dict(scene_spec)

    def fake_env(_ctx, _plan, _sig, target, scene_spec, *, motion):
        return {
            "prompt": f"img-{target['requested_anchor']}",
            "motion": motion, "actors": [], "anchor": target["requested_anchor"],
            "scene_spec": scene_spec,
            "video_config": {"base_url": "http://x", "model": "h3", "size": "2:3", "proxy": ""},
        }

    monkeypatch.setattr(asc, "_wb_comfy_scene_target", fake_target)
    monkeypatch.setattr(asc, "_wb_comfy_scene_spec", fake_scene_spec)
    monkeypatch.setattr(asc, "_wb_comfy_profile", fake_profile)
    monkeypatch.setattr(asc, "_wb_first_person_frame_filter", lambda _ctx, spec: spec)
    monkeypatch.setattr(asc, "_wb_comfy_illustrate_env", fake_env)
    monkeypatch.setattr(asc, "_video_caps_for", lambda _ctx: {"duration": 6})
    monkeypatch.setattr(asc, "_video_skeleton_for", lambda _ctx, _mode: None)
    monkeypatch.setattr(asc.run_trace, "emit", lambda *a, **k: None)

    requests = asc.build_shot_requests(
        ctx, "r1", None, text, {"visible_story": text}, pool,
        primary_motion=1, user_text="",
    )

    detail = next(item for item in requests if item["shot_type"] == "cut")
    narrative = detail["scene_spec"]["narrative"]
    protected = detail["scene_spec"]["protected_narrative"]
    # 图片按锚点落在第 2 段起点；raw 6s 被夹成 5s，两条叙事都必须写「第 5 秒」。
    assert detail["shot_image_timing"] == "at"
    assert detail["shot_image_time_s"] == 5
    assert "第 5 秒" in narrative
    assert "第 5 秒" in protected
    assert "首帧" not in protected


def test_build_shot_requests_returns_empty_without_anchors(monkeypatch):
    monkeypatch.setattr(asc.run_trace, "emit", lambda *a, **k: None)
    assert asc.build_shot_requests(
        {"illustration_anchor_intents": []}, "r1", None, TEXT, {}, {},
        primary_motion=0, user_text="",
    ) == []


def test_build_shot_requests_injects_auto_continuity_anchors(monkeypatch):
    """全自动连续锚点：同场景各镜拿同一空间锚点，服装从 `[在场]` 自动派生。"""
    text = (
        "旧仓库的木门前铺着红毯。\n\n"
        "腓特烈从侧门走进来。\n\n"
        "她停在台前。"
    )
    anchors = [
        {"anchor": "旧仓库的木门前铺着红毯。", "shot_type": "establish",
         "transition_desc": "", "framing": "远景/环境空镜", "original_index": 0, "score": 20},
        {"anchor": "腓特烈从侧门走进来。", "shot_type": "cut",
         "transition_desc": "", "framing": "全身", "original_index": 1, "score": 90},
        {"anchor": "她停在台前。", "shot_type": "cut",
         "transition_desc": "", "framing": "中景", "original_index": 2, "score": 80},
    ]
    ctx = {
        "illustration_anchor_intents": anchors,
        "turn_id": "t1",
        "video_duration_hint": 12,
        "character_base_images": {"腓特烈": "f.png"},
    }
    pool = {"known": ["腓特烈"], "actor_values": [], "absent": [], "climax_paragraph": "",
            "request_actors": [], "request_appearance": ""}
    sig = {"visible_story": text, "present": "腓特烈（白色婚纱，项圈藏在头纱下）"}

    def fake_target(_ctx, _repo, _plan, _clean, _sig, _pool, **kwargs):
        anchor = str(kwargs.get("anchor_override") or "")
        return {
            "requested_anchor": anchor, "request_prompt": f"img-{anchor}",
            "request_actors": ["腓特烈"], "motion": 1, "plan_retargeted": False,
        }

    def fake_scene_spec(_ctx, _repo, _plan, _clean, _sig, _pool, _target):
        return {
            "narrative": "画面", "actors": ["腓特烈"],
            "appearance": "腓特烈(白色婚纱)", "wardrobe": "白色婚纱",
            "rating": "sfw", "aspect_ratio": "16:9", "composition": "中景",
        }

    def fake_profile(_ctx, _plan, scene_spec, _retargeted):
        return dict(scene_spec)

    def fake_env(_ctx, _plan, _sig, target, scene_spec, *, motion):
        return {
            "prompt": f"img-{target['requested_anchor']}",
            "motion": motion, "actors": ["腓特烈"], "anchor": target["requested_anchor"],
            "scene_spec": scene_spec,
            "video_config": {"base_url": "http://x", "model": "h3", "size": "16:9", "proxy": ""},
        }

    monkeypatch.setattr(asc, "_wb_comfy_scene_target", fake_target)
    monkeypatch.setattr(asc, "_wb_comfy_scene_spec", fake_scene_spec)
    monkeypatch.setattr(asc, "_wb_comfy_profile", fake_profile)
    monkeypatch.setattr(asc, "_wb_first_person_frame_filter", lambda _ctx, spec: spec)
    monkeypatch.setattr(asc, "_wb_comfy_illustrate_env", fake_env)
    monkeypatch.setattr(asc, "_video_caps_for", lambda _ctx: {"duration": 4})
    monkeypatch.setattr(asc, "_video_skeleton_for", lambda _ctx, _mode: None)
    monkeypatch.setattr(asc.run_trace, "emit", lambda *a, **k: None)

    requests = asc.build_shot_requests(
        ctx, "r1", None, text, sig, pool, primary_motion=1, user_text="",
    )

    assert len(requests) == 3
    anchors_on_shots = {r["scene_spec"]["scene_anchor"] for r in requests}
    assert len(anchors_on_shots) == 1
    assert "红毯" in anchors_on_shots.pop()
    assert "侧门" in requests[1]["scene_spec"]["scene_anchor"]
    # 同场景分组键供参考图链/审阅使用。
    assert {r["shot_scene_key"] for r in requests} == {"scene-0"}
    # 环境镜清空角色 ⇒ 不写服装锚点；角色镜必须带上状态块的服装。
    assert requests[0]["scene_spec"].get("wardrobe_anchor", "") == ""
    assert "白色婚纱" in requests[1]["scene_spec"]["wardrobe_anchor"]
    assert "项圈" in requests[2]["scene_spec"]["wardrobe_anchor"]
    # 2026-11-12：逐镜声明图片生成能力，前端按能力槽选模板（当前默认文生图）。
    assert all(r["scene_spec"]["image_mode"] == "text2img" for r in requests)
