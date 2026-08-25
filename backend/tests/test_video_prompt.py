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
    assert "上楼层尾帧衔接：上楼层：雨夜门口收伞" in p


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
