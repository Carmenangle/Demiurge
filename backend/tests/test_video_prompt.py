"""video_prompt 单测：两套提示词模板（climax 精简 / firstlast 七段式）+ 参数组装 dry-run。"""

from app.services import video_prompt


def _spec(**overrides):
    base = {
        "narrative": "温知夏抬眼笑说「开饭」，三人围坐面馆。",
        "appearance": "温知夏(米色针织开衫+陶杯)、沈糯(粉卫衣+棒棒糖)、柏言(和风长衫+茶盏)",
        "wardrobe": "全员日常私服",
        "locale": "温暖小面馆内景，暖黄吊灯，木质吧台",
        "actors": ["温知夏", "沈糯", "柏言"],
        "rating": "sfw",
        "camera": "摇臂俯拍",
        "composition": "三人中景",
        "motion": 3,
        "negative_prompt": "禁止五官漂移",
    }
    base.update(overrides)
    return base


# ===== climax 七段式：含时间分镜 =====

def test_climax_has_time_segments():
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert "[时间分镜]" in p
    assert "0–" in p
    assert "[参考绑定]" in p
    assert "[动作]" not in p


def test_climax_binds_single_frame_and_identity():
    p = video_prompt.compile_climax_video_prompt(_spec(), first_frame_desc="高潮动作画面")
    assert "图片1中心的角色为温知夏、沈糯、柏言" in p
    # 用户拍板：参考绑定不再说「高潮动作画面」当干扰，只声明「图片1中心的角色为 X」
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "高潮动作画面" not in binding
    assert "温知夏" in p and "沈糯" in p and "柏言" in p


def test_climax_reference_binding_does_not_duplicate_action():
    # 缺陷回归：first_frame_desc 留空时，画面级动作细节曾同时出现在
    # [参考绑定] 与 [动作] 两段（整段重复）。修复后参考绑定用
    # 「{画面角色}的高潮动作画面」占位，画面细节（composition）只出现在 [动作] 段。
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert "图片1中心的角色为温知夏、沈糯、柏言" in p
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


# ===== firstlast 七段式：含时间分镜 =====

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
        narrative="温知夏推门而入，朝众人点头。她低声说「开饭了」。沈糯放下筷子站起身，望向门口。",
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
    spec = _spec(narrative="她低声说「开饭了，都过来！」沈糯放下筷子。")
    p = video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12)
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    assert "开饭了，都过来！」" in seg  # 引语完整，不被 ！ 切碎
    assert "」沈糯" not in seg  # 闭引号不得粘到下一拍开头（对白归属对白拍）
    assert seg.count("台词随口型同步") == 1  # 引语拍只标一次
    assert "沈糯放下筷子" in seg


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


# ===== 参数组装 dry-run =====

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


# ===== R2 缺图守卫 =====

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


# ===== W3 转场视频：短桥段编译 + 坑G 不硬控时长 =====

def test_transition_has_six_sections_no_style():
    p = video_prompt.compile_transition_video_prompt(_spec(), style_prefix="二次元日常美食 CGDCT")
    assert "[转场分镜]" in p
    assert "[参考绑定]" in p
    assert "[主体/场景]" in p
    assert "[音频]" in p
    assert "[风格声明]" not in p


def test_transition_binds_prev_tail_and_first_frame():
    p = video_prompt.compile_transition_video_prompt(
        _spec(), prev_tail_desc="三人围坐面馆举杯", first_frame_desc="面馆暖光沈糯抿汤",
    )
    assert "图片1=三人围坐面馆举杯（上一楼层尾帧/转场起点）" in p
    assert "图片2=面馆暖光沈糯抿汤（当前楼层首帧/转场终点）" in p
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


# ===== R3 元信息三件套（模型名透传 + 画幅派生）=====

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


# ===== R6 音频：无对白不写「台词=逐字」 =====

def test_audio_no_dialogue_no_script_line():
    p = video_prompt.compile_firstlast_video_prompt(_spec())
    assert "音乐=" in p
    assert "台词=" not in p            # 无逐字对白时不诱导幻觉


def test_audio_with_dialogue_lists_script():
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(), audio_lines=[{"speaker": "温知夏", "text": "开饭啦"}],
    )
    assert "台词=" in p
    assert "温知夏：开饭啦" in p


def test_audio_design_renders_sfx_and_lines():
    spec = _spec(audio_design={
        "music": "低沉弦乐铺底",
        "sfx": ["fleshy claps in steady rhythm", "trickling water"],
        "lines": [{"speaker": "虞妙玥", "text": "你来了"}],
        "sync": "掌声卡重音",
    })
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "音乐=低沉弦乐铺底" in p
    assert "音效=fleshy claps in steady rhythm、trickling water" in p
    assert "同步=掌声卡重音" in p
    # climax：高潮定格时刻对白通常已说完（用户定稿）——动作窗口不带台词，
    # 无论来源（audio_design.lines / comfy_audio 兜底）一律不列
    assert "台词=" not in p


def test_climax_drops_dialogue_from_all_sources():
    # audio_design.lines 与 comfy_audio 兜底 audio_lines 双来源都不进 climax [音频]
    spec = _spec(audio_design={
        "music": "低沉弦乐铺底", "sfx": [],
        "lines": [{"speaker": "宗主(男)", "text": "杀了。", "at_s": 1}],
        "sync": "",
    })
    p = video_prompt.compile_climax_video_prompt(
        spec, audio_lines=[{"speaker": "虞妙玥", "text": "他……杀、自己……人……"}],
    )
    assert "台词=" not in p
    assert "杀了。" not in p.split("[音频]：", 1)[1].split("[负面约束]：", 1)[0]


def test_firstlast_audio_design_renders_timed_lines():
    # firstlast：首尾帧影片从头到尾覆盖剧情——对白全部入列并带 at_s 时点
    spec = _spec(audio_design={
        "music": "低沉弦乐铺底",
        "sfx": ["fleshy claps in steady rhythm"],
        "lines": [
            {"speaker": "温知夏", "text": "开饭了，都过来！", "at_s": 2},
            {"speaker": "沈糯", "text": "来了。"},
        ],
        "sync": "掌声卡重音",
    })
    p = video_prompt.compile_firstlast_video_prompt(spec)
    assert "音乐=低沉弦乐铺底" in p
    assert "台词=2s｜温知夏：开饭了，都过来！；沈糯：来了。" in p
    assert "同步=掌声卡重音" in p


def test_audio_design_without_sfx_keeps_env_fallback():
    p = video_prompt.compile_climax_video_prompt(
        _spec(audio_design={"music": "", "sfx": [], "lines": [], "sync": ""}),
    )
    assert "音乐=按本集风格铺底" in p
    assert "音效=环境声" in p
    assert "台词=" not in p


# ===== R7 负面约束去重 =====

def test_negative_dedup_across_sources():
    p = video_prompt.compile_climax_video_prompt(
        _spec(negative_prompt="禁止五官漂移"),
        negative="禁止五官漂移 / 变成其他画风",
    )
    assert p.count("禁止五官漂移") == 1  # preset 与 scene_spec 重复项去重
    assert "变成其他画风" in p


# ===== R9 视频默认 16:9 =====

def test_build_request_default_size_is_16_9():
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(),
        video_config={"base_url": "https://x.com/v", "model": "m"},
    )
    assert req["submit"]["size"] == "1280x720"  # 不再沿用图片的 1024x1024


# ===== 防拦截对齐：spec 破甲标记在编译前还原（对齐图像生成第一层）=====

def test_build_request_restores_jailbreak_markers_in_spec():
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(appearance="温知夏(米@(色)@针织开衫)"),
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


# ===== 高潮动作桥段：画面级要素优先（保证高潮场景用视频结构表现）=====

def test_climax_prefers_visual_facts_over_raw_narrative():
    # 高潮动作优先用主模型同轮提炼的画面级要素，而不是围绕锚点截取的中文叙事原文
    spec = _spec(
        narrative="陈旧叙事：她还在孤儿院门口。",  # 锚点陈旧时会截取到对不上剧情的原文
        subjects=[{"name": "冷倾雪", "description": "drawing sword, crimson cloak in lightning", "weight": 1.2}],
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
    assert "温知夏抬眼笑说「开饭」，三人围坐面馆。" in p


def test_climax_camera_prefers_model_camera_over_motion():
    # 运镜优先主模型 camera，不按 motion 强度兜底
    p = video_prompt.compile_climax_video_prompt(_spec(camera="摇臂俯拍", motion=3))
    assert "摇臂俯拍" in p
    assert "低机位快速丝滑运镜" not in p


def test_climax_prefers_action_sequence_over_visual_facts():
    # 高潮动作延伸优先用 action_sequence（定格动作 → 剧情完整动作），
    # 覆盖画面级要素（subjects/visual_facts/composition）的动态化旧行为
    spec = _spec(
        subjects=[{"name": "冷倾雪", "description": "scooping cream with spoon", "weight": 1.2}],
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
        subjects=[{"name": "冷倾雪", "description": "drawing sword, crimson cloak", "weight": 1.2}],
        visual_facts=[{"kind": "action", "fact": "leaps upward with blade raised", "evidence": "拔剑跃起"}],
        composition="low-angle dynamic shot",
        action_sequence=[],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "drawing sword, crimson cloak" in p
    assert "leaps upward with blade raised" in p


def test_climax_multi_sentence_narrative_splits_beats():
    # 无 action_sequence 且 narrative 多句时，动作段按句子切分多拍，不再退化成整段单拍
    spec = _spec(narrative="她推门而入。沈糯朝她招手。温知夏坐下。", composition="", camera="")
    p = video_prompt.compile_climax_video_prompt(spec)
    action = p.split("[时间分镜]：", 1)[1]
    assert "推门而入" in action
    assert "沈糯朝她招手" in action
    assert "温知夏坐下" in action
    assert action.count("s｜") >= 3


def test_climax_action_does_not_degrade_to_appearance():
    # P1/P5 回归：无 action_sequence / visual_facts / composition 时，动作段不得用
    # subjects.description（外貌）兜底——否则 [动作] 段退化成整段外貌描述。
    # 外貌只应出现在 [主体/场景]，动作段回退到 narrative（剧情体态）。
    spec = _spec(
        narrative="温知夏猛地起身，椅子向后倒去，一把攥住林屿的手腕",
        subjects=[{"name": "温知夏", "description": "luxuriant black hair, mature figure", "weight": 1.2}],
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
        appearance="温知夏(丰腴肥熟、酥雌醇媚、女帝气场)",
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
        '"lines":[{"speaker":"虞妙玥","text":"你来了","at_s":3},'
        '{"speaker":"沈糯","text":"没有时点"}],"sync":"卡重音"}}'
    )
    assert plan["audio_design"]["music"] == "低沉"
    assert plan["audio_design"]["sfx"] == ["fleshy claps", "trickling water"]
    assert plan["audio_design"]["lines"] == [
        {"speaker": "虞妙玥", "text": "你来了", "at_s": 3.0},
        {"speaker": "沈糯", "text": "没有时点"},
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


def test_audio_design_lines_carry_plot_timing():
    # 台词时点：at_s 是提取 LLM 按剧情位置推算的『什么时候说』，渲染成 {t}s｜说话人：台词；
    # 缺 at_s 诚实省略前缀（comfy_audio 兜底台词同样无前缀）。firstlast 专用（climax 无台词）。
    spec = _spec(audio_design={
        "music": "低沉弦乐铺底",
        "sfx": [],
        "lines": [
            {"speaker": "宗主(男)", "text": "杀了。", "at_s": 1},
            {"speaker": "虞妙玥", "text": "他……杀、自己……人……", "at_s": 7.5},
            {"speaker": "沈糯", "text": "没有时点的台词"},
        ],
        "sync": "",
    })
    p = video_prompt.compile_firstlast_video_prompt(spec)
    assert "1s｜宗主(男)：杀了。" in p
    assert "7.5s｜虞妙玥：他……杀、自己……人……" in p
    assert "沈糯：没有时点的台词" in p
    assert "s｜沈糯" not in p


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
        '"lines":[{"speaker":"虞妙玥","text":"我不能满足你……"}],"sync":"不能协助"}}'
    )
    assert plan["action_sequence"] == [
        {"beat": "收尾", "desc": "she trembles and grips the chains"},
    ]
    assert "subject_scene" not in plan
    assert plan["audio_design"]["sfx"] == ["铁链哗啦声"]
    assert plan["audio_design"]["lines"] == [{"speaker": "虞妙玥", "text": "我不能满足你……"}]
    assert "music" not in plan["audio_design"]
    assert "sync" not in plan["audio_design"]


def test_parse_video_plan_整体拒答返回空():
    # 整体无效时返回 {}，调用方据此触发拒答重试或回退纯函数兜底。
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"定格起点","desc":"我不能协助这项请求"}],'
        '"subject_scene":"I cannot help with this request"}'
    )
    assert plan == {}


# ===== 参考绑定角色样貌绑定（角色名必须绑定样貌，否则视频模型不认识角色） =====

def test_reference_binding_binds_identity_gloss_from_subjects():
    # subjects 的 name→description 是「角色→样貌」真源，优先用它做身份绑定
    spec = _spec(
        subjects=[
            {"name": "温知夏", "description": "beige cardigan, chestnut long hair", "weight": 1.2},
            {"name": "沈糯", "description": "pink hoodie, lollipop", "weight": 1.0},
            {"name": "柏言", "description": "wa-style robe, tea cup", "weight": 0.9},
        ],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "图片1中心的角色为温知夏、沈糯、柏言" in binding
    assert "温知夏（beige cardigan, chestnut long hair）" in binding
    assert "沈糯（pink hoodie, lollipop）" in binding
    assert "柏言（wa-style robe, tea cup）" in binding


def test_reference_binding_binds_identity_gloss_from_appearance():
    # 无 subjects 时回退 appearance 的「名字(外貌)」格式，拆出角色→样貌映射
    spec = _spec(appearance="温知夏(米色针织开衫+陶杯)、沈糯(粉卫衣+棒棒糖)、柏言(和风长衫+茶盏)")
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "温知夏（米色针织开衫+陶杯）" in binding
    assert "沈糯（粉卫衣+棒棒糖）" in binding
    assert "柏言（和风长衫+茶盏）" in binding


def test_reference_binding_binds_identity_gloss_from_plain_appearance():
    # 纯文本无分隔：用 actors 名字做前缀匹配，拆出单角色样貌（其余角色只写名字）
    spec = _spec(appearance="温知夏米色针织开衫，栗色长发")
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "温知夏（米色针织开衫，栗色长发）" in binding
    assert "沈糯" in binding  # 无样貌数据，只写名字


def test_reference_binding_does_not_reflow_appearance_when_subject_scene_exists():
    # P4 续：agent 已产出简化 video_subject_scene 时，身份绑定不得再用原始中文
    # appearance 兜底（堆砌词「丰腴肥熟」不得经参考绑定回流）
    spec = _spec(
        appearance="温知夏(丰腴肥熟、酥雌醇媚、女帝气场)",
        video_subject_scene="hourglass figure, large breasts, wide hips, seductive eyes",
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "丰腴肥熟" not in p
    assert "hourglass figure" in p  # 简化外貌仍在 [主体/场景]


# ===== 参考绑定不再写「画面另含未绑定角色」提示（用户拍板 2026-08-28） =====


def test_climax_does_not_flag_unbound_person_in_action():
    spec = _spec(
        actors=["虞妙玥"],
        action_sequence=[
            {"beat": "定格起点", "desc": "Man presses a metal token against the woman's waist"},
        ],
    )
    p = video_prompt.compile_climax_video_prompt(spec)
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "图片1中心的角色为虞妙玥" in binding
    assert "未绑定角色" not in binding


def test_climax_no_unbound_warning_when_all_bound():
    # 动作里只出现已被绑定的角色名，不产生「未绑定角色」提示
    spec = _spec(action_sequence=[{"beat": "定格起点", "desc": "温知夏起身，沈糯拍照，柏言倒水"}])
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "未绑定角色" not in p


def test_build_request_no_unbound_person_warning():
    spec = _spec(
        actors=["虞妙玥"],
        action_sequence=[{"beat": "定格起点", "desc": "Man presses a token against the woman"}],
    )
    req = video_prompt.build_video_request(
        mode="climax", spec=spec,
        video_config={"base_url": "https://x.com/videos", "model": "m"},
        first_frame="http://x/c.png",
    )
    assert not any("未绑定角色" in w for w in req["warnings"])
    assert "图片1中心的角色为虞妙玥" in req["reference_binding"]["图片1"]


def test_reference_binding_firstlast_and_transition_bind_identity():
    # firstlast / transition 同样带「画面角色」+ 角色样貌绑定
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(appearance="温知夏(米色针织开衫)、沈糯(粉卫衣)、柏言(和风长衫)"),
        first_frame_desc="开场三人围坐", last_frame_desc="举杯同框",
    )
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "画面角色：温知夏、沈糯、柏言" in binding
    assert "温知夏（米色针织开衫）" in binding
    tp = video_prompt.compile_transition_video_prompt(
        _spec(appearance="温知夏(米色针织开衫)、沈糯(粉卫衣)、柏言(和风长衫)"),
        prev_tail_desc="三人围坐面馆举杯", first_frame_desc="面馆暖光沈糯抿汤",
    )
    tbinding = tp.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "画面角色：温知夏、沈糯、柏言" in tbinding
