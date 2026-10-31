"""video_prompt 单测：两套提示词模板（climax 精简 / firstlast 七段式）+ 参数组装 dry-run。"""

from app.services import video_prompt


def _spec(**overrides):
    base = {
        "narrative": "纪含章抬眼笑说「开饭」，三人围坐面馆。",
        "appearance": "纪含章(米色针织开衫+陶杯)、庄听澜(粉卫衣+棒棒糖)、邵允(和风长衫+茶盏)",
        "wardrobe": "全员日常私服",
        "locale": "温暖小面馆内景，暖黄吊灯，木质吧台",
        "actors": ["纪含章", "庄听澜", "邵允"],
        "rating": "sfw",
        "camera": "摇臂俯拍",
        "composition": "三人中景",
        "motion": 3,
        "negative_prompt": "禁止五官漂移",
    }
    base.update(overrides)
    return base


def test_climax_has_time_segments():
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert "[时间分镜]" in p
    assert "0–" in p
    assert "[参考绑定]" in p
    assert "[动作]" not in p


def test_climax_binds_single_frame_and_identity():
    p = video_prompt.compile_climax_video_prompt(_spec(), first_frame_desc="高潮动作画面")
    assert "图片1中心的角色为纪含章、庄听澜、邵允" in p
    # 用户拍板：参考绑定不再说「高潮动作画面」当干扰，只声明「图片1中心的角色为 X」
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "高潮动作画面" not in binding
    assert "纪含章" in p and "庄听澜" in p and "邵允" in p


def test_climax_reference_binding_does_not_duplicate_action():
    # 缺陷回归：first_frame_desc 留空时，画面级动作细节曾同时出现在
    # [参考绑定] 与 [动作] 两段（整段重复）。修复后参考绑定用
    # 「{画面角色}的高潮动作画面」占位，画面细节（composition）只出现在 [动作] 段。
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert "图片1中心的角色为纪含章、庄听澜、邵允" in p
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    action = p.split("[时间分镜]：", 1)[1]
    assert "三人中景" in action
    assert "三人中景" not in binding


def test_climax_ignores_style_prefix_and_respects_negative():
    # 有参考图定调，风格声明整体停用：style_prefix 不再进入提示词
    p = video_prompt.compile_climax_video_prompt(
        _spec(), style_prefix="二次元日常美食", negative="禁止柔化转场",
    )
    assert "[风格声明]" not in p
    assert "二次元日常美食" not in p
    assert "禁止柔化转场" in p
    assert "禁止五官漂移" in p


def test_firstlast_has_six_sections_no_style():
    p = video_prompt.compile_firstlast_video_prompt(_spec(), style_prefix="二次元日常美食 CGDCT")
    for marker in ("[参考绑定]", "[主体/场景]", "[时间分镜]", "[音频]", "[负面约束]"):
        assert marker in p
    assert "[风格声明]" not in p
    assert "二次元日常美食" not in p
    assert "[0s–" in p  # 时间分镜切段


def test_firstlast_binds_two_frames_with_roles():
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(), first_frame_desc="开场三人围坐", last_frame_desc="举杯同框",
    )
    assert "图片1=开场三人围坐" in p
    assert "图片2=举杯同框" in p


def test_firstlast_carries_prev_tail_for_transition():
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(), prev_tail_desc="上楼层：雨夜门口收伞",
    )
    assert "承接上一镜头尾帧" in p
    assert "上楼层：雨夜门口收伞" in p
    assert "自然延续" in p
    assert "无突兀跳切" in p


def test_firstlast_identity_lock_only_in_negative():
    p = video_prompt.compile_firstlast_video_prompt(_spec())
    # 身份锁只进 [负面约束] 一次，不再逐拍重申
    assert p.count("人物身份和五官不能发生变化") == 1
    assert "[负面约束]" in p
    # 时间分镜段内不再出现身份锁
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    assert "人物身份和五官不能发生变化" not in seg


def test_firstlast_segments_cover_narrative_sentence_by_sentence():
    # 首尾帧时间分镜覆盖全文：每个事件/每句对白各占一拍，引语拍标台词同步
    spec = _spec(
        narrative="纪含章推门而入，朝众人点头。她低声说「开饭了」。庄听澜放下筷子站起身，望向门口。",
    )
    p = video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12)
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    for frag in ("推门而入", "开饭了", "放下筷子站起身"):
        assert frag in seg
    assert "台词随口型同步" in seg
    # 中段成拍 + 首尾帧 = 至少 4 拍，且按句切分不合并
    assert seg.count("s｜") >= 4


def test_firstlast_quote_internal_punctuation_keeps_beat_whole():
    # 引号句合并归属：引号内的 ！？；。不得切拍；闭引号后无句点也要断拍
    spec = _spec(narrative="她低声说「开饭了，都过来！」庄听澜放下筷子。")
    p = video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12)
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    assert "开饭了，都过来！」" in seg  # 引语完整，不被 ！ 切碎
    assert "」庄听澜" not in seg  # 闭引号不得粘到下一拍开头（对白归属对白拍）
    assert seg.count("台词随口型同步") == 1  # 引语拍只标一次
    assert "庄听澜放下筷子" in seg


def test_firstlast_quoted_dialogue_matching_refusal_pattern_is_kept():
    # 台词不过滤原则：引号内的「不能满足你」是正常对白，不得被拒答过滤误伤
    spec = _spec(narrative="她红着眼圈说「我不能满足你。」然后转身离开。")
    p = video_prompt.compile_firstlast_video_prompt(spec)
    assert "我不能满足你" in p
    assert "」然后转身离开" not in p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]


def test_climax_bare_refusal_narrative_not_compiled_into_beats():
    # 纯函数兜底路径：narrative 本身是拒答句（主模型拒答当正文）时，
    # 不得把它编译进 [时间分镜]；回退诚实占位，身份锁仍只在 [负面约束] 一次。
    spec = _spec(
        narrative="我不能协助这项请求。", subjects=[], composition=None,
        camera=None, action_sequence=[],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "我不能协助这项请求" not in p
    assert "主体动作按剧情自然演变" in p
    assert p.count("人物身份和五官不能发生变化") == 1


def test_firstlast_camera_uses_vocabulary_not_hardcoded_push():
    # 运镜不再全程「极缓推进」，首拍定场（固定/缓推）、尾拍拉远收束
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(narrative="她推门而入。", camera="", motion=0),
    )
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    assert "极缓推进" not in seg
    assert "镜头慢慢拉远" in seg  # 收尾拍拉远
    assert "摄像机缓缓向主体的面部移动" in seg or "固定镜头，相机完全静止" in seg


def test_climax_identity_lock_only_in_negative():
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert p.count("人物身份和五官不能发生变化") == 1
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    assert "人物身份和五官不能发生变化" not in seg


def test_build_request_firstlast_multipart_two_images():
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(),
        video_config={"base_url": "https://ai.t8star.org/v2/videos/generations", "model": "h3"},
        preset={"stylePrefix": "CGDCT", "negativePrompt": "禁霓虹", "videoDurationHint": 15},
        first_frame="http://x/f.png", last_frame="http://x/l.png",
    )
    s = req["submit"]
    assert s["endpoint"] == "https://ai.t8star.org/v2/videos/generations"  # 原样
    assert s["model"] == "h3"
    assert s["images"] == ["http://x/f.png", "http://x/l.png"]
    assert s["content_type"] == "multipart/form-data"
    assert "[0s–" in s["prompt"]


def test_build_request_climax_json_single_image():
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(),
        video_config={"base_url": "https://api.seedance.tv/v1", "model": "seedance"},
        first_frame="http://x/c.png",
    )
    s = req["submit"]
    assert s["endpoint"] == "https://api.seedance.tv/v1"  # 原样（不猜 v1 形态）
    assert s["images"] == ["http://x/c.png"]
    assert s["content_type"] == "multipart/form-data"


def test_build_request_no_images_is_json():
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(),
        video_config={"base_url": "https://x.com/v1/video/generations", "model": "m"},
    )
    assert req["submit"]["images"] == []
    assert req["submit"]["content_type"] == "application/json"


def test_build_request_separates_role_desc_from_image_address():
    # 关键语义：图职责描述进 prompt 参考绑定，图地址进 images[]，两层必须分开。
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
        first_frame="http://x/f.png", last_frame="http://x/l.png",
        first_frame_desc="三人围坐开场", last_frame_desc="举杯同框收尾",
    )
    # prompt 参考绑定是职责文字，不含 URL
    assert "图片1=三人围坐开场" in req["submit"]["prompt"]
    assert "图片2=举杯同框收尾" in req["submit"]["prompt"]
    assert "http://x/f.png" not in req["submit"]["prompt"]
    # images[] 是真实图地址
    assert req["submit"]["images"] == ["http://x/f.png", "http://x/l.png"]
    # reference_binding 同时展示职责 + 来源，便于核对对应关系
    assert "三人围坐开场 → http://x/f.png" in req["reference_binding"]["图片1"]
    assert "举杯同框收尾 → http://x/l.png" in req["reference_binding"]["图片2"]


def test_firstlast_missing_frames_honest_degredation():
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(), has_first=False, has_last=False,
    )
    # 缺图时诚实标注「无参考图，以文字为准」，不写「图片1/图片2=」
    assert "图片1=" not in p and "图片2=" not in p
    assert "首帧（无参考图，以文字为准）" in p
    assert "尾帧（无参考图，以文字为准）" in p


def test_build_request_missing_frames_warns():
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
    )
    assert req["submit"]["images"] == []
    assert req["submit"]["content_type"] == "application/json"
    assert any("缺首帧图" in w for w in req["warnings"])
    assert any("缺尾帧图" in w for w in req["warnings"])
    assert any("纯文生" in w for w in req["warnings"])


def test_transition_has_six_sections_no_style():
    p = video_prompt.compile_transition_video_prompt(_spec(), style_prefix="二次元日常美食 CGDCT")
    assert "[转场分镜]" in p
    assert "[参考绑定]" in p
    assert "[主体/场景]" in p
    assert "[音频]" in p
    assert "[风格声明]" not in p


def test_transition_binds_prev_tail_and_first_frame():
    p = video_prompt.compile_transition_video_prompt(
        _spec(), prev_tail_desc="三人围坐面馆举杯", first_frame_desc="面馆暖光庄听澜抿汤",
    )
    assert "图片1=三人围坐面馆举杯（上一楼层尾帧/转场起点）" in p
    assert "图片2=面馆暖光庄听澜抿汤（当前楼层首帧/转场终点）" in p
    assert "转场起点" in p and "转场终点" in p


def test_transition_honest_degredation_when_missing_images():
    p = video_prompt.compile_transition_video_prompt(
        _spec(), has_prev_tail=False, has_first=False,
    )
    assert "图片1=" not in p and "图片2=" not in p
    assert "上一楼层尾帧（无参考图，以文字为准）" in p
    assert "当前楼层首帧（无参考图，以文字为准）" in p


def test_transition_meta_no_hardcoded_duration_when_zero():
    # 坑G：duration=0 时不写死秒数（交模型默认），绝不兑底 5s
    p = video_prompt.compile_transition_video_prompt(_spec())
    assert "5 seconds" not in p
    assert "时长=视频模型默认（短桥段）" in p


def test_transition_meta_writes_duration_when_hint_given():
    # 前端提交侧有转场时长（transitionDurationHint）时才写具体秒数
    p = video_prompt.compile_transition_video_prompt(_spec(), duration_hint=4)
    assert "4 seconds" in p
    assert "时长=视频模型默认" not in p


def test_build_request_transition_maps_frames_and_duration():
    req = video_prompt.build_video_request(
        mode="transition", spec=_spec(),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
        preset={
            "transitionDurationHint": 4,
            "videoDurationHint": 15,   # 正片时长不得泄漏进转场
        },
        first_frame="local://prev-tail.png",
        last_frame="local://curr-first.png",
        first_frame_desc="当前首帧：暖光下一人",
        prev_tail_desc="上尾帧：三人围坐举杯",
    )
    sub = req["submit"]
    assert req["mode"] == "transition"
    assert sub["images"] == ["local://prev-tail.png", "local://curr-first.png"]
    assert "4 seconds" in sub["prompt"]
    assert "15 seconds" not in sub["prompt"]
    assert sub["content_type"] == "multipart/form-data"
    # 参考绑定里是职责描述 + 地址（两层分离），不是把地址写进提示词
    assert "图片1" in req["reference_binding"] and "图片2" in req["reference_binding"]
    # 语义边界（坑I/坑G 补测）：图片1=上尾帧（起点）、图片2=当前首帧（终点），
    # 不得把上尾帧描述错填进「当前首帧/终点」。
    assert "上尾帧：三人围坐举杯" in req["reference_binding"]["图片1"]
    assert "当前首帧：暖光下一人" in req["reference_binding"]["图片2"]
    assert "当前首帧：暖光下一人" in sub["prompt"]


def test_build_request_transition_missing_prev_tail_warns_and_degrades():
    req = video_prompt.build_video_request(
        mode="transition", spec=_spec(),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
        last_frame="local://curr-first.png",
        first_frame_desc="当前首帧：暖光下一人",
    )
    assert req["submit"]["images"] == ["local://curr-first.png"]
    assert any("缺上尾帧图" in w for w in req["warnings"])
    assert "无参考图，以文字为准" in req["submit"]["prompt"]


def test_build_request_transition_section_names():
    assert video_prompt._section_names("transition") == [
        "①元信息", "②参考绑定", "③主体/场景", "④转场分镜", "⑤音频", "⑥负面约束",
    ]


def test_meta_passthrough_model_name_not_hardcoded():
    p = video_prompt.compile_climax_video_prompt(
        _spec(), model_name="seedance-1.0", size="1280x720",
    )
    assert "seedance-1.0" in p          # 透传，非硬编码 Minimax H3
    assert "Minimax" not in p
    assert "16:9" in p                  # 画幅从 size 派生


def test_aspect_from_size_variants():
    assert video_prompt._aspect_from_size("1280x720") == "16:9"
    assert video_prompt._aspect_from_size("1024x1024") == "1:1"
    assert video_prompt._aspect_from_size("1080x1920") == "9:16"
    assert video_prompt._aspect_from_size("16:9") == "16:9"


def test_climax_drops_dialogue_from_all_sources():
    # audio_design.lines 与 comfy_audio 兜底 audio_lines 双来源都不进 climax [音频]
    spec = _spec(audio_design={
        "music": "低沉弦乐铺底", "sfx": [],
        "lines": [{"speaker": "宗主(男)", "text": "杀了。", "at_s": 1}],
        "sync": "",
    })
    p = video_prompt.compile_climax_video_prompt(
        spec, audio_lines=[{"speaker": "苏映晚", "text": "他……杀、自己……人……"}],
    )
    assert "台词=" not in p
    assert "杀了。" not in p.split("[音频]：", 1)[1].split("[负面约束]：", 1)[0]


def test_negative_dedup_across_sources():
    p = video_prompt.compile_climax_video_prompt(
        _spec(negative_prompt="禁止五官漂移"),
        negative="禁止五官漂移 / 变成其他画风",
    )
    assert p.count("禁止五官漂移") == 1  # preset 与 scene_spec 重复项去重
    assert "变成其他画风" in p


def test_build_request_default_size_is_16_9():
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(),
        video_config={"base_url": "https://x.com/v", "model": "m"},
    )
    assert req["submit"]["size"] == "1280x720"  # 不再沿用图片的 1024x1024


def test_build_request_restores_jailbreak_markers_in_spec():
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(appearance="纪含章(米@(色)@针织开衫)"),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
    )
    prompt = req["submit"]["prompt"]
    assert "@(" not in prompt and ")@" not in prompt  # 无残留破甲标记
    assert "米色" in prompt and "米@(色)@" not in prompt  # 已还原成正常文字


def test_build_request_restores_markers_in_firstlast():
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(narrative="三@(人)@举杯同框", locale="面@(馆)@内景"),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
    )
    prompt = req["submit"]["prompt"]
    assert "@(" not in prompt and ")@" not in prompt
    assert "三人举杯同框" in prompt and "面馆内景" in prompt


def test_climax_prefers_visual_facts_over_raw_narrative():
    # 高潮动作优先用主模型同轮提炼的画面级要素，而不是围绕锚点截取的中文叙事原文
    spec = _spec(
        narrative="陈旧叙事：她还在孤儿院门口。",  # 锚点陈旧时会截取到对不上剧情的原文
        subjects=[{"name": "顾凝霜", "description": "drawing sword, crimson cloak in lightning", "weight": 1.2}],
        visual_facts=[{"kind": "action", "fact": "leaps upward with blade raised", "evidence": "拔剑跃起"}],
        composition="low-angle dynamic shot",
        camera="fast tracking push-in",
        motion=3,
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "drawing sword, crimson cloak in lightning" in p
    assert "leaps upward with blade raised" in p
    assert "low-angle dynamic shot" in p
    assert "fast tracking push-in" in p
    assert "孤儿院门口" not in p  # 陈旧叙事不再进入高潮动作


def test_climax_falls_back_to_narrative_without_visual_elements():
    # 无画面级要素时回退中文 narrative（旧行为）
    spec = _spec(composition="", camera="")
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "纪含章抬眼笑说「开饭」，三人围坐面馆。" in p


def test_climax_camera_prefers_model_camera_over_motion():
    # 运镜优先主模型 camera，不按 motion 强度兜底
    p = video_prompt.compile_climax_video_prompt(_spec(camera="摇臂俯拍", motion=3))
    assert "摇臂俯拍" in p
    assert "低机位快速丝滑运镜" not in p


def test_climax_prefers_action_sequence_over_visual_facts():
    # 高潮动作延伸优先用 action_sequence（定格动作 → 剧情完整动作），
    # 覆盖画面级要素（subjects/visual_facts/composition）的动态化旧行为
    spec = _spec(
        subjects=[{"name": "顾凝霜", "description": "scooping cream with spoon", "weight": 1.2}],
        visual_facts=[{"kind": "action", "fact": "spoon lifts a dollop of cream", "evidence": "挖出一勺奶油"}],
        composition="close-up",
        action_sequence=[
            {"beat": "定格起点", "desc": "勺子挖出一勺奶油"},
            {"beat": "延伸", "desc": "勺子送向嘴边，吃下"},
        ],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "定格起点" in p
    assert "勺子挖出一勺奶油" in p
    assert "延伸" in p
    assert "勺子送向嘴边，吃下" in p


def test_climax_action_sequence_skips_empty_entries():
    # action_sequence 里 desc 为空的条目被跳过，不产出空 beat 片段
    spec = _spec(
        action_sequence=[
            {"beat": "定格起点", "desc": "勺子挖出一勺奶油"},
            {"beat": "", "desc": ""},
            {"beat": "延伸", "desc": "喂向镜头"},
        ],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "勺子挖出一勺奶油" in p
    assert "喂向镜头" in p
    assert "｜：：" not in p


def test_climax_falls_back_to_visual_facts_without_action_sequence():
    # 无 action_sequence 时回退画面级要素（旧行为不变）
    spec = _spec(
        subjects=[{"name": "顾凝霜", "description": "drawing sword, crimson cloak", "weight": 1.2}],
        visual_facts=[{"kind": "action", "fact": "leaps upward with blade raised", "evidence": "拔剑跃起"}],
        composition="low-angle dynamic shot",
        action_sequence=[],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "drawing sword, crimson cloak" in p
    assert "leaps upward with blade raised" in p


def test_climax_multi_sentence_narrative_splits_beats():
    # 无 action_sequence 且 narrative 多句时，动作段按句子切分多拍，不再退化成整段单拍
    spec = _spec(narrative="她推门而入。庄听澜朝她招手。纪含章坐下。", composition="", camera="")
    p = video_prompt.compile_climax_video_prompt(spec)
    action = p.split("[时间分镜]：", 1)[1]
    assert "推门而入" in action
    assert "庄听澜朝她招手" in action
    assert "纪含章坐下" in action
    assert action.count("s｜") >= 3


def test_climax_action_does_not_degrade_to_appearance():
    # P1/P5 回归：无 action_sequence / visual_facts / composition 时，动作段不得用
    # subjects.description（外貌）兜底——否则 [动作] 段退化成整段外貌描述。
    # 外貌只应出现在 [主体/场景]，动作段回退到 narrative（剧情体态）。
    spec = _spec(
        narrative="纪含章猛地起身，椅子向后倒去，一把攥住林屿的手腕",
        subjects=[{"name": "纪含章", "description": "luxuriant black hair, mature figure", "weight": 1.2}],
        action_sequence=[],
        composition=None,
        camera=None,
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    action = p.split("[时间分镜]：", 1)[1]
    # 外貌描述（luxuriant）不得出现在动作段
    assert "luxuriant" not in action
    assert "攥住林屿的手腕" in action  # 动作回退到剧情原文


def test_subject_scene_prefers_video_subject_scene_over_appearance():
    # P4 回归：agent 已产出简化外貌/场景时，[主体/场景] 用它，不塞中文堆砌 appearance
    spec = _spec(
        appearance="纪含章(丰腴肥熟、酥雌醇媚、女帝气场)",
        video_subject_scene="hourglass figure, large breasts, wide hips, seductive eyes",
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "hourglass figure, large breasts, wide hips, seductive eyes" in p
    assert "丰腴肥熟" not in p


def test_parse_video_plan_extracts_action_sequence_and_subject_scene():
    plan = video_prompt.parse_video_plan(
        '```json\n{"action_sequence":[{"beat":"定格起点","desc":"俯卧在地"},'
        '{"beat":"延伸","desc":"被从身后贯穿"}],'
        '"subject_scene":"hourglass figure, stone prison corridor"}\n```'
    )
    assert plan["action_sequence"] == [
        {"beat": "定格起点", "desc": "俯卧在地"},
        {"beat": "延伸", "desc": "被从身后贯穿"},
    ]
    assert plan["subject_scene"] == "hourglass figure, stone prison corridor"


def test_parse_video_plan_extracts_audio_design():
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"定格起点","desc":"俯卧在地"}],'
        '"audio_design":{"music":"低沉","sfx":["fleshy claps","trickling water"],'
        '"lines":[{"speaker":"苏映晚","text":"你来了","at_s":3},'
        '{"speaker":"庄听澜","text":"没有时点"}],"sync":"卡重音"}}'
    )
    assert plan["audio_design"]["music"] == "低沉"
    assert plan["audio_design"]["sfx"] == ["fleshy claps", "trickling water"]
    assert plan["audio_design"]["lines"] == [
        {"speaker": "苏映晚", "text": "你来了", "at_s": 3.0},
        {"speaker": "庄听澜", "text": "没有时点"},
    ]


def test_parse_video_plan_at_s_only_numeric_passes():
    # at_s 只收数字（含数字字符串）；bool/非数字不透传
    plan = video_prompt.parse_video_plan(
        '{"audio_design":{"lines":['
        '{"speaker":"A","text":"整数","at_s":2},'
        '{"speaker":"B","text":"小数","at_s":7.5},'
        '{"speaker":"C","text":"数字串","at_s":"9"},'
        '{"speaker":"D","text":"布尔","at_s":true},'
        '{"speaker":"E","text":"乱串","at_s":"later"}]}}'
    )
    got = plan["audio_design"]["lines"]
    assert [e.get("at_s") for e in got] == [2.0, 7.5, 9.0, None, None]


def test_parse_video_plan_skips_empty_and_returns_partial():
    # 空 desc 跳过；缺 subject_scene 时只回 action_sequence，不整体失败
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"","desc":""},{"beat":"延伸","desc":"吃下"}]}'
    )
    assert plan == {"action_sequence": [{"beat": "延伸", "desc": "吃下"}]}


def test_parse_video_plan_拒答字段丢弃_不流入提示词():
    # 防拦截回归：拒答句写进 desc/subject_scene/music/sfx/sync 时逐条丢弃；
    # 台词原文（lines.text）不过滤——「我不能满足你」这类正常对白必须保留。
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"定格起点","desc":"我不能协助这项请求"},'
        '{"beat":"延伸","desc":"I cannot help with this request"},'
        '{"beat":"收尾","desc":"she trembles and grips the chains"}],'
        '"subject_scene":"I cannot assist with this request",'
        '"audio_design":{"music":"无法协助","sfx":["铁链哗啦声","I cannot help"],'
        '"lines":[{"speaker":"苏映晚","text":"我不能满足你……"}],"sync":"不能协助"}}'
    )
    assert plan["action_sequence"] == [
        {"beat": "收尾", "desc": "she trembles and grips the chains"},
    ]
    assert "subject_scene" not in plan
    assert plan["audio_design"]["sfx"] == ["铁链哗啦声"]
    assert plan["audio_design"]["lines"] == [{"speaker": "苏映晚", "text": "我不能满足你……"}]
    assert "music" not in plan["audio_design"]
    assert "sync" not in plan["audio_design"]


def test_clamp_climax_duration_夹到4到8():
    # 2026-10-31 用户定案：高潮时长由剧情动作量判定，4–8 秒；非法值交调用方兜底。
    assert video_prompt.clamp_climax_duration(3) == 4
    assert video_prompt.clamp_climax_duration(9) == 8
    assert video_prompt.clamp_climax_duration("6") == 6
    assert video_prompt.clamp_climax_duration(6.6) == 7
    assert video_prompt.clamp_climax_duration(0) is None
    assert video_prompt.clamp_climax_duration(-2) is None
    assert video_prompt.clamp_climax_duration(True) is None
    assert video_prompt.clamp_climax_duration("soon") is None


def test_parse_video_plan_duration_s_extracted_and_clamped():
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"起点","desc":"俯卧"}],"duration_s":9}'
    )
    assert plan["duration_s"] == 8
    assert video_prompt.parse_video_plan('{"duration_s":"6"}')["duration_s"] == 6
    assert "duration_s" not in video_prompt.parse_video_plan('{"duration_s":"soon"}')


def test_build_request_climax_uses_plot_duration():
    # 剧情判定值进提示词元信息，随 submit.duration 下发前端注入工作流时长节点。
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(video_duration_s=6),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
        first_frame="http://x/f.png",
    )
    assert "6 seconds" in req["submit"]["prompt"]
    assert req["submit"]["duration"] == 6


def test_build_request_plot_duration_does_not_override_explicit_preset():
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(video_duration_s=6),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
        preset={"videoDurationHint": 10},
    )
    assert "10 seconds" in req["submit"]["prompt"]
    assert req["submit"]["duration"] == 10


def test_build_request_firstlast_ignores_plot_duration():
    # 首尾帧覆盖整段楼层，时长仍走模板/预设；剧情判定值只在 climax 生效。
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(video_duration_s=6),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
        first_frame="http://x/f.png", last_frame="http://x/l.png",
    )
    assert "15 seconds" in req["submit"]["prompt"]
    assert req["submit"]["duration"] == 0


def test_parse_video_plan_整体拒答返回空():
    # 整体无效时返回 {}，调用方据此触发拒答重试或回退纯函数兜底。
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"定格起点","desc":"我不能协助这项请求"}],'
        '"subject_scene":"I cannot help with this request"}'
    )
    assert plan == {}


def test_reference_binding_binds_identity_gloss_from_subjects():
    # subjects 的 name→description 是「角色→样貌」真源，优先用它做身份绑定
    spec = _spec(
        subjects=[
            {"name": "纪含章", "description": "beige cardigan, chestnut long hair", "weight": 1.2},
            {"name": "庄听澜", "description": "pink hoodie, lollipop", "weight": 1.0},
            {"name": "邵允", "description": "wa-style robe, tea cup", "weight": 0.9},
        ],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "图片1中心的角色为纪含章、庄听澜、邵允" in binding
    assert "纪含章（beige cardigan, chestnut long hair）" in binding
    assert "庄听澜（pink hoodie, lollipop）" in binding
    assert "邵允（wa-style robe, tea cup）" in binding


def test_reference_binding_binds_identity_gloss_from_appearance():
    # 无 subjects 时回退 appearance 的「名字(外貌)」格式，拆出角色→样貌映射
    spec = _spec(appearance="纪含章(米色针织开衫+陶杯)、庄听澜(粉卫衣+棒棒糖)、邵允(和风长衫+茶盏)")
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "纪含章（米色针织开衫+陶杯）" in binding
    assert "庄听澜（粉卫衣+棒棒糖）" in binding
    assert "邵允（和风长衫+茶盏）" in binding


def test_reference_binding_binds_identity_gloss_from_plain_appearance():
    # 纯文本无分隔：用 actors 名字做前缀匹配，拆出单角色样貌（其余角色只写名字）
    spec = _spec(appearance="纪含章米色针织开衫，栗色长发")
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "纪含章（米色针织开衫，栗色长发）" in binding
    assert "庄听澜" in binding  # 无样貌数据，只写名字


def test_reference_binding_does_not_reflow_appearance_when_subject_scene_exists():
    # P4 续：agent 已产出简化 video_subject_scene 时，身份绑定不得再用原始中文
    # appearance 兜底（堆砌词「丰腴肥熟」不得经参考绑定回流）
    spec = _spec(
        appearance="纪含章(丰腴肥熟、酥雌醇媚、女帝气场)",
        video_subject_scene="hourglass figure, large breasts, wide hips, seductive eyes",
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "丰腴肥熟" not in p
    assert "hourglass figure" in p  # 简化外貌仍在 [主体/场景]


def test_climax_does_not_flag_unbound_person_in_action():
    spec = _spec(
        actors=["苏映晚"],
        action_sequence=[
            {"beat": "定格起点", "desc": "Man presses a metal token against the woman's waist"},
        ],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "图片1中心的角色为苏映晚" in binding
    assert "未绑定角色" not in binding


def test_climax_no_unbound_warning_when_all_bound():
    # 动作里只出现已被绑定的角色名，不产生「未绑定角色」提示
    spec = _spec(action_sequence=[{"beat": "定格起点", "desc": "纪含章起身，庄听澜拍照，邵允倒水"}])
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "未绑定角色" not in p


def test_build_request_no_unbound_person_warning():
    spec = _spec(
        actors=["苏映晚"],
        action_sequence=[{"beat": "定格起点", "desc": "Man presses a token against the woman"}],
    )
    req = video_prompt.build_video_request(
        mode="climax", spec=spec,
        video_config={"base_url": "https://x.com/videos", "model": "m"},
        first_frame="http://x/c.png",
    )
    assert not any("未绑定角色" in w for w in req["warnings"])
    assert "图片1中心的角色为苏映晚" in req["reference_binding"]["图片1"]


def test_reference_binding_firstlast_and_transition_bind_identity():
    # firstlast / transition 同样带「画面角色」+ 角色样貌绑定
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(appearance="纪含章(米色针织开衫)、庄听澜(粉卫衣)、邵允(和风长衫)"),
        first_frame_desc="开场三人围坐", last_frame_desc="举杯同框",
    )
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "画面角色：纪含章、庄听澜、邵允" in binding
    assert "纪含章（米色针织开衫）" in binding
    tp = video_prompt.compile_transition_video_prompt(
        _spec(appearance="纪含章(米色针织开衫)、庄听澜(粉卫衣)、邵允(和风长衫)"),
        prev_tail_desc="三人围坐面馆举杯", first_frame_desc="面馆暖光庄听澜抿汤",
    )
    tbinding = tp.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "画面角色：纪含章、庄听澜、邵允" in tbinding

# ===== ⑥[音频]：只有效果音（2026-09-20 用户定案）=====
# 台词/旁白由「多元数据插入」里的**独立音频生成功能**负责 ⇒ 视频提示词一律不写台词；
# 音乐结构同样不写（用户口径「只有效果音」）。数据仍留在 spec["audio_design"] 里，只是不渲染。


def test_视频音频只有效果音不含台词与音乐():
    spec = _spec(audio_design={
        "music": "低音提琴渐强",
        "sfx": ["fleshy claps in steady rhythm", "trickling water"],
        "lines": [{"speaker": "清夏", "text": "不要…", "at_s": 3}],
        "sync": "每 2 秒卡拍",
    })
    p = video_prompt.compile_climax_video_prompt(spec)

    assert "音效=fleshy claps in steady rhythm、trickling water" in p
    assert "同步=每 2 秒卡拍" in p
    assert "台词=" not in p
    assert "音乐=" not in p
    assert "不要…" not in p


def test_firstlast视频音频同样只有效果音():
    spec = _spec(audio_design={
        "music": "低音提琴渐强",
        "sfx": ["fleshy claps in steady rhythm"],
        "lines": [
            {"speaker": "清夏", "text": "我要去了…", "at_s": 2},
            {"speaker": "泰宇", "text": "嗯。"},
        ],
        "sync": "每 2 秒卡拍",
    })
    p = video_prompt.compile_firstlast_video_prompt(spec)

    assert "音效=fleshy claps in steady rhythm" in p
    assert "同步=每 2 秒卡拍" in p
    assert "台词=" not in p
    assert "音乐=" not in p
    assert "我要去了…" not in p


def test_comfy_audio对白参数不再进视频提示词():
    """`audio_lines`（comfy_audio 对白）保留仅为兼容，按新口径一律不渲染。"""
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(), audio_lines=[{"speaker": "清夏", "text": "别停。"}],
    )

    assert "台词=" not in p
    assert "别停。" not in p


def test_视频音频无音效时回退环境声():
    p = video_prompt.compile_climax_video_prompt(
        _spec(audio_design={"music": "", "sfx": [], "lines": [], "sync": ""}),
    )

    assert "音效=环境声" in p
    assert "音乐=" not in p
    assert "台词=" not in p


# ── 2026-10-25 提示词模版：骨架覆盖（skeleton）────────────────────────────────
# 设计合同：skeleton=None 与全部缺省字段 ⇒ 与既有输出逐字节相同（上方 62 例即锁）；
# 骨架只覆盖文案/顺序/词表/默认值，不改分镜/参考绑定/缺图守卫/画幅派生。

def test_skeleton_none_byte_identical():
    """无骨架 ⇒ 三个编译函数输出与加 skeleton=VideoSkeleton() 逐字节相同。"""
    cases = (
        (video_prompt.compile_climax_video_prompt,
         {"first_frame_desc": "首帧"}),
        (video_prompt.compile_firstlast_video_prompt,
         {"first_frame_desc": "首帧", "last_frame_desc": "尾帧", "prev_tail_desc": "上尾帧"}),
        (video_prompt.compile_transition_video_prompt,
         {"first_frame_desc": "首帧", "prev_tail_desc": "上尾帧"}),
    )
    for compile_fn, kwargs in cases:
        base = compile_fn(_spec(), **kwargs)
        with_empty = compile_fn(_spec(), skeleton=video_prompt.VideoSkeleton(), **kwargs)
        assert base == with_empty


def test_skeleton_section_titles_and_order():
    sk = video_prompt.VideoSkeleton(
        section_titles={"meta": "元信息·自定义", "negative": "负向约束"},
        section_order=("negative", "audio", "segments", "subject", "binding", "meta"),
    )
    p = video_prompt.compile_climax_video_prompt(_spec(), skeleton=sk)
    assert "[元信息·自定义]" in p and "[负向约束]" in p
    # 顺序：负面约束第一、元信息最后
    assert p.index("[负向约束]") < p.index("[音频]") < p.index("[元信息·自定义]")
    # prompt_sections 同步反映自定义标题与编号
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(), video_config={}, skeleton=sk)
    assert req["prompt_sections"][0] == "①负向约束"
    assert req["prompt_sections"][-1] == "⑥元信息·自定义"


def test_skeleton_camera_band_override():
    sk = video_prompt.VideoSkeleton(camera_band_overrides={3: "镜头以慢动作环绕主体"})
    # 主模型 camera 优先（既有口径）：本用例清空 spec.camera 才能看到档位覆盖
    p = video_prompt.compile_climax_video_prompt(_spec(camera=""), skeleton=sk)
    # motion=3 ⇒ band 3 ⇒ 全档使用覆盖文案
    assert "镜头以慢动作环绕主体" in p
    # 未覆盖档位仍回落内置（同一提示词里不出现内置 band3 代表句）
    assert "低角度仰拍" not in p


def test_skeleton_beat_labels_and_duration():
    sk = video_prompt.VideoSkeleton(
        beat_labels=("起", "承", "合"), default_duration=6,
    )
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(), duration_hint=0, skeleton=sk)
    assert "｜起]" in p and "｜承]" in p and "｜合]" in p
    assert "6 seconds" in p  # duration_hint 空 ⇒ 用模版默认时长


def test_skeleton_negative_extra_and_meta_note():
    sk = video_prompt.VideoSkeleton(negative_extra="禁止字幕", meta_note="H3 高服从")
    p = video_prompt.compile_climax_video_prompt(_spec(), skeleton=sk)
    assert "禁止字幕" in p
    assert "H3 高服从" in p.split("[参考绑定]")[0]


def test_skeleton_from_data_defensive():
    """坏形状一律忽略该字段；空/非映射 ⇒ None。"""
    assert video_prompt.VideoSkeleton.from_data(None) is None
    assert video_prompt.VideoSkeleton.from_data("x") is None
    sk = video_prompt.VideoSkeleton.from_data({
        "section_titles": {"meta": "元信息·改", "bogus": "x"},
        "section_order": ["meta"],
        "camera_band_overrides": {"9": "x", "2": "镜头横移"},
        "beat_labels": ["起"],
        "default_duration": 99,
        "negative_extra": "y" * 2000,
    })
    assert sk is not None
    assert sk.section_titles == {"meta": "元信息·改"}     # 坏键忽略
    assert sk.section_order is None                        # 非排列忽略
    assert sk.camera_band_overrides == {2: "镜头横移"}      # 坏档位忽略
    assert sk.beat_labels == ("起",)
    assert sk.default_duration is None                      # 超范围忽略
    assert sk.negative_extra == "y" * 1000                  # 截断到上限


def test_default_skeleton_data_projection():
    data = video_prompt.default_skeleton_data("transition")
    assert data["section_titles"]["segments"] == "转场分镜"
    assert data["section_order"] == list(video_prompt.SECTION_KEYS)
    assert video_prompt.default_skeleton_data("climax")["section_titles"]["segments"] == "时间分镜"
