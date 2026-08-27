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


def test_climax_reference_binding_does_not_duplicate_action():
    # 缺陷回归：first_frame_desc 留空时，画面级动作细节曾同时出现在
    # [参考绑定] 与 [动作] 两段（整段重复）。修复后参考绑定用「高潮动作画面」占位，
    # 画面细节（composition）只出现在 [动作] 段。
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert "图片1=高潮动作画面" in p
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    action = p.split("[动作]：", 1)[1]
    assert "三人中景" in action
    assert "三人中景" not in binding


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


# ===== W3 转场视频：短桥段编译 + 坑G 不硬控时长 =====

def test_transition_has_seven_sections():
    p = video_prompt.compile_transition_video_prompt(_spec(), style_prefix="二次元日常美食 CGDCT")
    assert "[转场分镜]" in p
    assert "[参考绑定]" in p
    assert "[风格]" in p
    assert "[主体/场景]" in p
    assert "[音频]" in p


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
        "①元信息", "②风格", "③参考绑定", "④主体/场景", "⑤转场分镜", "⑥音频", "⑦负面约束",
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
    assert "定格起点: 勺子挖出一勺奶油" in p
    assert "延伸: 勺子送向嘴边，吃下" in p


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
    assert "定格起点: 勺子挖出一勺奶油" in p
    assert "延伸: 喂向镜头" in p
    assert "::" not in p


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
    action = p.split("[动作]：", 1)[1]
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


def test_parse_video_plan_skips_empty_and_returns_partial():
    # 空 desc 跳过；缺 subject_scene 时只回 action_sequence，不整体失败
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"","desc":""},{"beat":"延伸","desc":"吃下"}]}'
    )
    assert plan == {"action_sequence": [{"beat": "延伸", "desc": "吃下"}]}


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
    assert "画面角色：温知夏、沈糯、柏言" in binding
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
