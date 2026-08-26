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


# ===== climax 精简版：不含时间分镜 =====

def test_climax_has_no_time_segments():
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert "[时间分镜]" not in p
    assert "[0s–" not in p
    assert "[参考绑定]" in p
    assert "[动作]" in p


def test_climax_binds_single_frame_and_identity():
    p = video_prompt.compile_climax_video_prompt(_spec(), first_frame_desc="高潮动作画面")
    assert "图片1=高潮动作画面" in p
    assert "温知夏" in p and "沈糯" in p and "柏言" in p


def test_climax_respects_negative_and_style():
    p = video_prompt.compile_climax_video_prompt(
        _spec(), style_prefix="二次元日常美食", negative="禁止柔化转场",
    )
    assert "[风格]：二次元日常美食" in p
    assert "禁止柔化转场" in p
    assert "禁止五官漂移" in p


# ===== firstlast 七段式：含时间分镜 =====

def test_firstlast_has_seven_sections():
    p = video_prompt.compile_firstlast_video_prompt(_spec(), style_prefix="二次元日常美食 CGDCT")
    for marker in ("[风格]", "[参考绑定]", "[主体/场景]", "[时间分镜]", "[音频]", "[负面约束]"):
        assert marker in p
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


def test_firstlast_identity_reasserted_per_segment():
    p = video_prompt.compile_firstlast_video_prompt(_spec())
    # 身份锁每段重申（3 段应出现 3 次）
    assert p.count("人物身份和五官不能发生变化") >= 3


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
