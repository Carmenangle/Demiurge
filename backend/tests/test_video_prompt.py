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


def test_climax_ignores_style_prefix_and_retires_negative_block():
    # 有参考图定调，风格声明整体停用：style_prefix 不再进入提示词。
    # 第 81 刀：⑥ 负面约束同样整体退役——`MiniMaxH3ImageToVideo` 只有 `positive` 一路
    # 输入，「worst quality / extra fingers」这类词进模型就是**正面内容**（叫它去看缺陷）。
    p = video_prompt.compile_climax_video_prompt(
        _spec(), style_prefix="二次元日常美食",
    )
    assert "[风格声明]" not in p
    assert "二次元日常美食" not in p
    assert "[负面约束]" not in p
    assert "禁止柔化转场" not in p      # 预设/调用方传来的负面串不再落地
    assert "禁止五官漂移" not in p      # scene_spec.negative_prompt 同样不进


def test_firstlast_has_five_sections_no_style():
    p = video_prompt.compile_firstlast_video_prompt(_spec(), style_prefix="二次元日常美食 CGDCT")
    for marker in ("[参考绑定]", "[主体/场景]", "[时间分镜]", "[音频]"):
        assert marker in p
    assert "[负面约束]" not in p    # 第 81 刀：⑥ 退役（三种形态一致）
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


def test_firstlast_identity_lock_only_in_binding():
    # 第 81 刀：⑥ 退役后身份锁落在 [参考绑定]，全片仍只出现一次。
    p = video_prompt.compile_firstlast_video_prompt(_spec())
    assert "[负面约束]" not in p
    assert p.count("人物身份和五官不能发生变化") == 1
    binding = p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "人物身份和五官不能发生变化" in binding
    # 时间分镜段内不重申身份锁
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
    # 不得把它编译进 [时间分镜]；回退诚实占位，身份锁仍只出现一次（落点＝② [参考绑定]，第 81 刀）。
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


def test_climax_identity_lock_only_in_binding():
    # 第 81 刀：身份锁的唯一落点是 [参考绑定]（⑥ 已退役），且全片只写一次。
    p = video_prompt.compile_climax_video_prompt(_spec())
    assert p.count("人物身份和五官不能发生变化") == 1
    assert "人物身份和五官不能发生变化" in p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
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


def test_firstlast_prefers_whole_floor_narrative_over_climax_excerpt():
    """批 1：firstlast 覆盖整段楼层——中段取材用整轮正文，不是高潮锚点那一段。

    `narrative` 仍保留为高潮段（climax 分支用）；firstlast 的整轮正文落在
    `floor_narrative`。两者不得混用，否则视频只会演「开头 → 高潮段 → 结尾」。
    """
    spec = _spec(
        narrative="高潮段：她跪到床边。",
        floor_narrative=(
            "开场：她推门进屋，收起伞。中段：她点亮台灯。"
            "高潮段：她跪到床边。结尾：她合眼睡去。"
        ),
        camera="", motion=0,
    )
    p = video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12)
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    for frag in ("推门进屋", "点亮台灯", "跪到床边", "合眼睡去"):
        assert frag in seg


def test_firstlast_floor_narrative_restores_jailbreak_markers():
    """整轮正文进提示词前必须还原破甲标记（与 narrative 同一条清洗链）。

    清洗发生在 `build_video_request` 的 `_clean_spec`（`_SPEC_TEXT_FIELDS` 已登记
    `floor_narrative`）⇒ 必须走真实组装入口，直调编译函数不经过这层。
    """
    req = video_prompt.build_video_request(
        mode="firstlast",
        spec=_spec(
            narrative="高潮段。",
            floor_narrative="她@(推)@门进屋。她@(坐)@到窗边。",
            camera="", motion=0,
        ),
        video_config={"base_url": "u", "model": "h3"},
        preset={"videoDurationHint": 8},
        first_frame_desc="开场在门口", last_frame_desc="收束在窗边",
    )
    p = req["submit"]["prompt"]
    assert "她推门进屋" in p
    assert "她坐到窗边" in p
    assert "@(推)@" not in p and "@(坐)@" not in p


def test_build_request_firstlast_pending_frames_declare_both_images():
    """批 1：首尾帧由前端链在编译后才挂上，② 不得再写「无参考图」。

    生产调用点拿不到图地址（图在成帧之后才产出）⇒ 必须按「待挂」声明，
    否则明明挂了图片1/2，提示词却叫模型按文字首帧自由构图。
    """
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(),
        video_config={"base_url": "u", "model": "h3"},
        first_frame_desc="开场三人围坐", last_frame_desc="举杯同框",
        pending_frame=True,
    )
    prompt = req["submit"]["prompt"]
    assert "图片1=开场三人围坐（首帧/起始画面）" in prompt
    assert "图片2=举杯同框（尾帧/目标画面）" in prompt
    assert "无参考图" not in prompt
    assert req["submit"]["images"] == []  # 地址确实没有，不能假装有
    assert req["warnings"] == ["首尾帧图编译时未产出：首帧/尾帧由下游链补挂本张成帧"]
    assert req["reference_binding"]["图片1"].endswith("（编译时未产出，由下游链补挂成帧）")
    assert req["reference_binding"]["图片2"].endswith("（编译时未产出，由下游链补挂成帧）")


def test_transition_has_five_sections_no_style():
    p = video_prompt.compile_transition_video_prompt(_spec(), style_prefix="二次元日常美食 CGDCT")
    assert "[转场分镜]" in p
    assert "[参考绑定]" in p
    assert "[主体/场景]" in p
    assert "[音频]" in p
    assert "[负面约束]" not in p    # 第 81 刀：⑥ 三种形态一起退役
    assert "人物身份和五官不能发生变化" in p.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
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


def test_第104刀_transition_does_not_inherit_whole_floor_subject_and_audio():
    """实稿发现 4：转场是两帧之间的短桥段，③/⑤ 不得复述整楼层事实。

    病灶：转场编译沿用整楼层 `spec` ⇒ ③ 写进整楼层的全部角色外貌与「走廊+卧室+楼下侧室门」
    三个地点、⑤ 写进整楼层的 8 条音效（其中「楼下闷响」发生在**楼层结尾**，与转场起点
    无关）。短桥段只有几秒，堆整楼层事实只会让模型在过渡里凭空换场、塞事件。
    """
    spec = _spec(
        narrative="她推门进屋。",
        actors=["她"],
        locale="卧室",
        video_subject_scene="WHOLE-FLOOR: corridor, bedroom and the downstairs door all at once",
        audio_design={"sfx": ["downstairs muffled thud", "door slam"], "sync": "卡拍于 90s"},
    )
    p = video_prompt.compile_transition_video_prompt(
        spec, prev_tail_desc="上尾帧：门口", first_frame_desc="当前首帧：床边",
    )
    assert "WHOLE-FLOOR" not in p
    assert "downstairs muffled thud" not in p
    assert "90s" not in p
    # ③ 只保留「以两张图为准」的约束，别把整楼场景清单塞进过渡
    assert "以图片1、图片2为准" in p
    # ⑤ 退回中性环境声（没有转场级提取，不假装知道桥段里的音效）
    assert "音效=环境声" in p


def test_第103刀_transition_pending_frame_declares_both_images():
    """实稿发现 3：转场同样在成图之前编译，图随后由 W3 转场块挂上（图片1=上尾帧、图片2=当前首帧）。

    旧实现只认 first_frame/last_frame 地址 ⇒ ②/④ 恒写「无参考图，以文字为准」，
    而前端实际会挂两张图 ⇒ 提示词自认文生、两张参考图白传。
    """
    req = video_prompt.build_video_request(
        mode="transition", spec=_spec(),
        video_config={"base_url": "https://x.com/videos", "model": "h3"},
        first_frame_desc="当前首帧：暖光下一人",
        prev_tail_desc="上尾帧：三人围坐举杯",
        pending_frame=True,
    )
    prompt = req["submit"]["prompt"]
    assert "无参考图" not in prompt
    assert "图片1=上尾帧：三人围坐举杯" in prompt
    assert "图片2=当前首帧：暖光下一人" in prompt
    assert "由下游链补挂" in req["reference_binding"]["图片1"]
    assert "由下游链补挂" in req["reference_binding"]["图片2"]
    assert any("上尾帧/当前首帧由下游链补挂" in w for w in req["warnings"])


def test_build_request_transition_section_names():
    # ⑥ 退役 ⇒ dry-run 的区块清单只列编译器真会写出的段（谎报比少报更坏）。
    assert video_prompt._section_names("transition") == [
        "①元信息", "②参考绑定", "③主体/场景", "④转场分镜", "⑤音频",
    ]
    assert video_prompt._section_names("climax")[-1] == "⑤音频"
    assert video_prompt._section_names("firstlast")[-1] == "⑤音频"


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
    assert "杀了。" not in p.split("[音频]：", 1)[1]


def test_negative_sources_never_reach_the_video_prompt():
    # 第 81 刀退役 ⑥、第 87 刀摘除旋钮：任何一路负面串（scene_spec 的 negative_prompt）
    # 都不进视频提示词——这条链没有 negative 通道，进了就是正面内容。
    # 模版侧连「能写负面」这个字段都不存在了：死旋钮不该留着骗编辑器。
    assert not hasattr(video_prompt.VideoSkeleton(), "negative_extra")
    assert "negative" not in video_prompt.SECTION_KEYS
    sk = video_prompt.VideoSkeleton(section_titles={video_prompt.SECTION_AUDIO: "声音设计"})
    p = video_prompt.compile_climax_video_prompt(
        _spec(negative_prompt="禁止五官漂移"), skeleton=sk,
    )
    assert "[负面约束]" not in p
    assert "禁止五官漂移" not in p
    assert "[声音设计]" in p


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
    # 机位契约（2026-11-04）：这帧判成静机位档 ⇒ 模型运镜句不进逐拍（其余画面级要素照进）
    assert "fast tracking push-in" not in p
    assert "孤儿院门口" not in p  # 陈旧叙事不再进入高潮动作


def test_climax_hold_contract_locks_one_camera():
    """静机位档：全片共用一句机位，幅度条款只在 ① 写一次，且不往 ⑥ 塞机位否定式。

    用户实锤「高潮视频还是像有转场」的直接来源是逐拍换镜（每拍一句互斥机位挤在
    4–8 秒里）＋逐拍重复长条款；第 79 刀再收一层：边界是「机位固定＋只由这一格自身
    形变」，**幅度不是边界**（「live2d就不能有很明显的动作吗」），且 ⑥ 在这条链上会
    并进同一条正面文本流，写「无切镜」＝把切镜触发词念给模型听。
    """
    p = video_prompt.compile_climax_video_prompt(
        _spec(camera="摇臂俯拍", aspect_ratio="2:3"), size="2:3", pending_frame=True)
    cams = [ln.split("｜", 1)[1].split("；")[0] for ln in _segments(p)]
    assert set(cams) == {video_prompt._HOLD_CAMERA_FRAME}
    assert "摇臂俯拍" not in p  # 静机位档不吃模型运镜句（写了就自相矛盾）
    meta = p.split("[元信息]：", 1)[1].split("\n\n", 1)[0]
    assert "全片一条固定镜头（Static Shot）" in meta
    assert "主体的动作幅度按剧情需要，可以明显" in meta
    assert "不引入画面之外的元素、部位或道具" in meta
    assert p.count("主体的动作幅度按剧情需要") == 1  # 全片规则只在 ① 写一次，不逐拍重申
    assert "主体只做微动" not in p
    assert "镜头切换" not in p and "机位变化" not in p


def test_climax_positive_stream_carries_no_cut_trigger_words():
    """H3 节点没有 negative 通道（入参 clip/vae/first_frame/last_frame/prompt/width/height/length，
    输出只有 positive）⇒ 五段全文都是正面文本流。两档文案一律不得出现官方手册 §4.2 的
    切镜触发词与运镜动作名，否则文案自己就是「像有转场」的来源。"""
    triggers = ("镜头切换", "切镜", "跳切", "转场", "多机位", "推拉", "摇移",
                "甩镜头", "cuts to", "transitions to", "switches to", "changes to")
    hold = video_prompt.compile_climax_video_prompt(
        _spec(camera=""), size="9:16", pending_frame=True)
    reveal = video_prompt.compile_climax_video_prompt(
        _spec(camera="", composition="", actors=[], aspect_ratio="16:9", locale="港区",
              narrative="舰队驶入港口，天际线在晨雾里连成一片。"),
        size="16:9", pending_frame=True)
    for name, text in (("action_hold", hold), ("scene_reveal", reveal)):
        assert [t for t in triggers if t in text] == []
        # 身份锁改由 ② 参考绑定承担（⑥ 已于第 87 刀摘除）：机位/运镜类否定式一概不进
        assert "人物身份和五官不能发生变化" in text


def test_climax_reveal_contract_keeps_world_moving():
    """大场面档的 ① 要写明「画面里的世界照常运动」：这一档不是冻帧，人流/车辆/水面
    继续推进，在场人物的动作幅度也不设上限（用户口径「人流往往就是路上很多行人走动」）。"""
    p = video_prompt.compile_climax_video_prompt(
        _spec(camera="", composition="", actors=[], aspect_ratio="16:9", locale="港区",
              narrative="舰队驶入港口，天际线在晨雾里连成一片。"),
        size="16:9", pending_frame=True)
    meta = p.split("[元信息]：", 1)[1].split("\n\n", 1)[0]
    assert "画面中的世界照常运动" in meta and "行人持续走动" in meta


def test_climax_hold_prunes_pose_migration_clauses():
    """首帧＝高潮图那一格 ⇒ 它的构型是前提不是剧情。hold 档逐拍只留往复/物理跟随，
    「抬起／踩上／滑出」这类构型迁移短语退出时间轴；纯迁移的一拍整拍退出。"""
    spec = {**_spec(camera=""), "action_sequence": [
        {"beat": "定格起点", "desc": "her hips rock in rhythm, the chain sways with each impact"},
        {"beat": "延伸", "desc": "the curtain slips off the rod, dust drifting in the light"},
        {"beat": "收尾", "desc": "she lifts her leg and plants it on the stool"},
    ]}
    lines = _segments(video_prompt.compile_climax_video_prompt(spec, size="9:16",
                                                               pending_frame=True))
    assert len(lines) == 2  # 纯迁移的收尾拍退出
    assert "her hips rock in rhythm" in lines[0]
    assert "slips off the rod" not in lines[1] and "dust drifting in the light" in lines[1]


def test_climax_hold_prune_falls_back_when_every_beat_is_migration():
    """全是迁移短语时不得把 ④ 掏空：回落原句，宁可留迁移也不让分镜凭空少一整段。"""
    spec = {**_spec(camera=""), "action_sequence": [
        {"beat": "延伸", "desc": "he stands up and walks to the door"},
        {"beat": "收尾", "desc": "she lifts her arm over her head"},
    ]}
    lines = _segments(video_prompt.compile_climax_video_prompt(spec, size="9:16",
                                                               pending_frame=True))
    assert len(lines) == 2
    assert "stands up" in lines[0] and "lifts her arm" in lines[1]


def test_climax_reveal_and_noframe_keep_migration_clauses():
    """裁剪只在「hold 档 + 有首帧」成立：文生视频没有首帧可锚，大场面档本来就在讲空间
    推进——两者都保留完整动作文本。"""
    spec = {**_spec(camera=""), "action_sequence": [
        {"beat": "延伸", "desc": "she lifts her leg and plants it on the stool"}]}
    no_frame = _segments(video_prompt.compile_climax_video_prompt(spec, size="9:16"))
    reveal = _segments(video_prompt.compile_climax_video_prompt(
        {**spec, "aspect_ratio": "16:9", "locale": "港区", "composition": "", "actors": []},
        size="16:9", pending_frame=True))
    assert "lifts her leg" in no_frame[0]
    assert "lifts her leg" in reveal[0]

def test_climax_reveal_contract_allows_one_continuous_move():
    """大场面档：宽画幅＋环境线索 ⇒ 一条连续运镜（竖直线索判成上摇），
    且负面不得写「运镜/机位变化」否掉这条契约自己要的运动；转场字面彻底退出高潮。
    """
    spec = _spec(camera="", composition="", actors=[], aspect_ratio="16:9",
                 locale="港区", narrative="舰队驶入港口，天际线在晨雾里连成一片。")
    p = video_prompt.compile_climax_video_prompt(spec, size="16:9", pending_frame=True)
    cams = {ln.split("｜", 1)[1].split("；")[0] for ln in _segments(p)}
    assert cams == {video_prompt._REVEAL_CAMERA_TILT}
    assert "甩镜头转场" not in p and "遮挡揭示转场" not in p
    # 第 81 刀：⑥ 整段不产出 ⇒ 不存在「用负面否掉契约自己要的运动」这条路
    assert "[负面约束]" not in p
    assert "运镜" not in p and "机位变化" not in p


def test_climax_reveal_adopts_model_camera():
    # 大场面档允许模型/预设给具体运动（比词表更贴这一帧）；静机位档才一律不吃
    spec = _spec(camera="缓慢后拉，带出整座城市", composition="", actors=[],
                 aspect_ratio="16:9", locale="", narrative="她站在窗前。")
    p = video_prompt.compile_climax_video_prompt(spec, size="16:9", pending_frame=True)
    assert "缓慢后拉，带出整座城市" in p


def test_climax_needs_both_evidence_to_move_camera():
    """判错代价不对称：宽画幅但无环境线索、有线索但窄幅，都留在静机位档。"""
    wide_only = video_prompt.resolve_climax_shot_intent(
        _spec(narrative="她抬起头。", locale=""), "16:9", True)
    assert wide_only.intent == video_prompt.CLIMAX_INTENT_HOLD
    cue_only = video_prompt.resolve_climax_shot_intent(
        _spec(narrative="舰队驶入港口。", locale="港区", actors=[]), "2:3", True)
    assert cue_only.intent == video_prompt.CLIMAX_INTENT_HOLD
    # 证据本身要看得见，才敢说明「为什么没升格」
    assert "港区" in cue_only.reason and "wide=2:3" in cue_only.reason
    both = video_prompt.resolve_climax_shot_intent(
        _spec(narrative="舰队驶入港口。", locale="港区", actors=[]), "16:9", True)
    assert both.intent == video_prompt.CLIMAX_INTENT_REVEAL


def test_climax_hold_uses_text2video_camera_without_frame():
    """缺图守卫（R2）：文生视频的高潮不得在机位句里引用不存在的图片1。"""
    intent = video_prompt.resolve_climax_shot_intent(_spec(), "", has_frame=False)
    assert intent.intent == video_prompt.CLIMAX_INTENT_HOLD
    assert intent.camera == video_prompt._HOLD_CAMERA_NOFRAME
    assert "图片1" not in intent.camera


def test_climax_ignores_the_1280x720_submit_default_as_width_evidence():
    """`video_config.size` 空 ⇒ 提交侧仍回落 1280x720（R9 既有行为），但契约**不吃这个默认值**：
    「没填画幅」不是「横屏」的证据，拿它当宽画幅就把默认偏向升格了。"""
    req = video_prompt.build_video_request(
        mode="climax",
        spec=_spec(narrative="舰队驶入港口。", locale="港区", actors=[]),
        video_config={}, preset={"videoDurationHint": 5},
    )
    assert req["submit"]["size"] == "1280x720"          # 兜底照旧
    assert req["shot_intent"]["intent"] == video_prompt.CLIMAX_INTENT_HOLD
    assert "wide=未知" in req["shot_intent"]["reason"]   # 证据缺位，逐字看得见


def test_climax_beat_count_is_bounded_by_duration():
    """零时长死拍（2026-11-04 修）：拍数不得超过秒数，否则 `dur*idx//n` 整除出
    `0–0s / 1–1s` 这类空窗口；超量按顺序均分合并，动作事实不丢。
    """
    beats = [{"beat": "延伸", "desc": f"动作{i}"} for i in range(8)]
    spec = _spec(camera="", action_sequence=beats)
    p = video_prompt.compile_climax_video_prompt(spec, duration_hint=5, pending_frame=True)
    lines = _segments(p)
    assert len(lines) == 5
    for line in lines:
        start, end = line.split("s｜")[0].split("–")
        assert int(end) - int(start) >= 1
    for i in range(8):
        assert f"动作{i}" in p  # 合并进相邻拍，不丢动作


def test_firstlast_still_rotates_cameras_per_beats():
    """契约只管高潮延伸：首尾帧覆盖整段桥段，逐拍换镜保留（用户口径「镜头固定的仅限高潮点」）。"""
    spec = _spec(camera="")
    p = video_prompt.compile_firstlast_video_prompt(spec)
    cams = {ln.split("：", 1)[1].split("；")[0] for ln in _segments(p)}
    assert len(cams) > 1
    assert "全片机位静止" not in p


def test_climax_falls_back_to_narrative_without_visual_elements():
    # 无画面级要素时回退中文 narrative（旧行为）
    spec = _spec(composition="", camera="")
    p = video_prompt.compile_climax_video_prompt(spec)
    assert "纪含章抬眼笑说「开饭」，三人围坐面馆。" in p


def test_climax_hold_drops_model_camera_even_at_high_motion():
    """静机位档不吃主模型 camera（2026-11-04 契约改口径）。

    旧行为「camera 优先 ⇒ 全片采用模型那句」在高潮档是缺陷：motion=3 时它常给
    「快速推近/环绕」，与「首帧逐字复现图片1机位」直接冲突，第 2 拍就重新构图。
    大场面档仍采用模型运镜句（见 test_climax_reveal_adopts_model_camera）。
    """
    p = video_prompt.compile_climax_video_prompt(
        _spec(camera="摇臂俯拍", motion=3), pending_frame=True)
    assert "摇臂俯拍" not in p
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
    assert "勺子挖出一勺奶油" in p
    assert "勺子送向嘴边，吃下" in p
    # 第 83 刀：拍名是提取层的内部流程词，视频模型读不出含义 ⇒ 不进提示词
    assert "定格起点" not in p and "延伸" not in p and "收尾" not in p


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


def test_clamp_climax_duration_夹到4到15():
    # 2026-10-31 定案由动作量判定；2026-11-04 上限由 8 放到 15（兜底仍 5，下限仍 4）。
    # 非法值交调用方兜底。
    assert video_prompt.clamp_climax_duration(3) == 4
    assert video_prompt.clamp_climax_duration(9) == 9  # 8 与 15 之间不再被夹住
    assert video_prompt.clamp_climax_duration(16) == 15
    assert video_prompt.clamp_climax_duration("6") == 6
    assert video_prompt.clamp_climax_duration(6.6) == 7
    assert video_prompt.clamp_climax_duration(0) is None
    assert video_prompt.clamp_climax_duration(-2) is None
    assert video_prompt.clamp_climax_duration(True) is None
    assert video_prompt.clamp_climax_duration("soon") is None


def test_parse_video_plan_duration_s_extracted_and_clamped():
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":[{"beat":"起点","desc":"俯卧"}],"duration_s":21}'
    )
    assert plan["duration_s"] == 15
    assert video_prompt.parse_video_plan('{"duration_s":"9"}')["duration_s"] == 9
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

# ===== ⑤[音频]：只有效果音（2026-09-20 用户定案）=====
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


# ===== 第 91 刀（2026-11-05）：⑤ 的「同步」拍点必须与 ④ 的实际拍界对账 =====
# 真机 21:27 那一轮（prompt_only 试跑，零云端）同一份成稿：
#   ④ = `0–3s / 3–6s / 6–10s`，⑤ = `同步=action beats land at 0s, 5s, 9s; nothing else until end`
# 时刻是提取 LLM 自己估的（它看不见 ④ 的整除结果），编译侧从没核对过 ⇒ ⑤ 把卡拍点
# 落在 ④ 的第 2、3 拍**中间**，下游模型只能乱对齐。这一刀只改「落地」：
# ⛔ 判据（提取合同里那句 sync 规则）一字不动，只把 ④ 已经写出来的真实拍点回填进 ⑤。
# 失败面必须窄 ⇒ 只在「整串里恰好一处、由逗号/顿号连接的 ≥2 个时刻」时对账，
# 其余（无时刻、单时刻如「每 2 秒卡拍」、两处以上列表、时刻散落在 and 两侧）逐字节不变。


def _three_beat_spec(sync: str) -> dict:
    return _spec(
        action_sequence=[
            {"beat": "定格起点", "desc": "she stands in the doorway, light across her back"},
            {"beat": "延伸", "desc": "her hand tightens on the door frame"},
            {"beat": "收尾", "desc": "the lamp glows steady behind her"},
        ],
        audio_design={"music": "", "sfx": ["door hinges creaking"], "lines": [], "sync": sync},
    )


def test_第91刀_音频同步拍点按时间分镜实际拍界改写():
    """0s, 5s, 9s ⇒ 0s, 3s, 6s（真机那一轮 ④ 就是 0–3 / 3–6 / 6–10）。"""
    p = video_prompt.compile_climax_video_prompt(
        _three_beat_spec("action beats land at 0s, 5s, 9s; nothing else until end"),
        duration_hint=10,
    )

    assert "[时间分镜]：\n0–3s" in p      # ④ 本身不受影响
    assert "6–10s" in p
    assert "同步=action beats land at 0s, 3s, 6s; nothing else until end" in p
    assert "5s" not in p
    assert "9s" not in p


def test_第91刀_对账只换数字不动句子其余部分():
    """顿号 + 中文「秒」同样只换时刻：措辞、标点、句尾一律保留。"""
    got = video_prompt.reconcile_sync_with_beats(
        "音效在 0秒、5秒、9秒 卡点，其余时间保持安静", [0, 3, 6]
    )

    assert got == "音效在 0秒、3秒、6秒 卡点，其余时间保持安静"


def test_第91刀_两处以上时刻列表不对账():
    """无法判定哪个列表对应拍界 ⇒ 宁可不动（失败面收窄，不误改模型的话）。"""
    sync = "foley at 0s、5s and the echo tail at 2s、8s"

    assert video_prompt.reconcile_sync_with_beats(sync, [0, 3, 6]) == sync


def test_第91刀_时刻散落一处列表之外时不对账():
    """`0s、3s and 6s` 只有一处逗号/顿号列表，但串里还有第三个时刻游离在外 ⇒
    逐位替换会只改前两个、留下错位的 6s ⇒ 整条不对账。"""
    sync = "hits at 0s、5s and 9s"

    assert video_prompt.reconcile_sync_with_beats(sync, [0, 3, 6]) == sync


def test_第91刀_无时刻或单时刻的同步逐字节不变():
    for sync in ("按自然节奏推进", "每 2 秒卡拍", "thrust beats land on the flesh-smacking hits"):
        assert video_prompt.reconcile_sync_with_beats(sync, [0, 3, 6]) == sync


def test_第91刀_对账幂等且拍点已正确时不变():
    starts = [0, 3, 6]
    once = video_prompt.reconcile_sync_with_beats("beats land at 0s, 5s, 9s", starts)
    assert once == "beats land at 0s, 3s, 6s"
    assert video_prompt.reconcile_sync_with_beats(once, starts) == once
    # 已经等于真实拍点 ⇒ 逐字节返回原串
    assert video_prompt.reconcile_sync_with_beats("beats land at 0s, 3s, 6s", starts) == \
        "beats land at 0s, 3s, 6s"


def test_第91刀_时刻数与拍数不等时整列表换成真实拍点():
    """列表长度与拍数不一致 ⇒ 逐位换会留下多/少一格 ⇒ 整串换成 ④ 的真实拍点（拍点是唯一真源）。"""
    got = video_prompt.reconcile_sync_with_beats("hits at 0s、4s、7s、9s", [0, 3, 6])

    assert got == "hits at 0s、3s、6s"


def test_第91刀_拍点取不到时音频段逐字节不变():
    """beat_starts 为空（④ 没解析出拍界）⇒ 完全不碰 ⑤，零回归兜底。"""
    sync = "action beats land at 0s, 5s, 9s"

    assert video_prompt.reconcile_sync_with_beats(sync, []) == sync
    p = video_prompt.compile_climax_video_prompt(_three_beat_spec(sync), duration_hint=10)
    assert "同步=action beats land at 0s, 3s, 6s" in p  # 正例仍在（防这条用例写反）


def test_第91刀_firstlast不吃拍点对账():
    """首尾帧链覆盖整段楼层、④ 走的是另一套分段（[转场起点/过渡/终点]），
    没有可对齐的拍界 ⇒ 不传 beat_starts ⇒ ⑤ 逐字节等于旧文案。"""
    spec = _three_beat_spec("action beats land at 0s, 5s, 9s")
    p = video_prompt.compile_firstlast_video_prompt(spec)

    assert "同步=action beats land at 0s, 5s, 9s" in p


def test_第91刀_beat_starts_from_segments_解析与容错():
    assert video_prompt.beat_starts_from_segments("0–3s｜a\n3–6s｜b\n6–10s｜c") == [0, 3, 6]
    assert video_prompt.beat_starts_from_segments("") == []
    assert video_prompt.beat_starts_from_segments("[元信息]：10 seconds\n0–1s｜a") == [0]


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
    # 骨架仍可改标题/顺序（第 87 刀后键表＝五键；⑥ 已不在键表里，见遗留数据归一那两条）。
    sk = video_prompt.VideoSkeleton(
        section_titles={"meta": "元信息·自定义", "audio": "音效"},
        section_order=("audio", "segments", "subject", "binding", "meta"),
    )
    p = video_prompt.compile_climax_video_prompt(_spec(), skeleton=sk)
    assert "[元信息·自定义]" in p and "[音效]" in p
    # 顺序：音频第一、元信息在绑定之后
    assert p.index("[音效]") < p.index("[时间分镜]") < p.index("[元信息·自定义]")
    assert "[负面约束]" not in p and "[负向约束]" not in p
    # prompt_sections 同步反映自定义标题与编号，且编号只排实际出现的区块
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(), video_config={}, skeleton=sk)
    assert req["prompt_sections"][0] == "①音效"
    assert req["prompt_sections"][-1] == "⑤元信息·自定义"
    assert len(req["prompt_sections"]) == 5


def test_skeleton_camera_band_override():
    sk = video_prompt.VideoSkeleton(camera_band_overrides={3: "镜头以慢动作环绕主体"})
    # 模版显式锁档 > 机位契约（用户「这一档就用这句」是显式意图，静机位档让位）
    p = video_prompt.compile_climax_video_prompt(_spec(camera=""), skeleton=sk)
    # motion=3 ⇒ band 3 ⇒ 全档使用覆盖文案
    assert "镜头以慢动作环绕主体" in p
    # 锁档接管这一档后，内置 band3 代表句也不出现（同一条提示词里不留两套机位）
    assert "低角度仰拍" not in p
    # 逐拍机位一律是锁档那句：契约的固定机位标记被顶掉（① 仍按契约声明全程一致）
    cams = {ln.split("｜", 1)[1].split("；")[0] for ln in _segments(p)}
    assert cams == {"镜头以慢动作环绕主体"}


def test_skeleton_beat_labels_and_duration():
    sk = video_prompt.VideoSkeleton(
        beat_labels=("起", "承", "合"), default_duration=6,
    )
    p = video_prompt.compile_firstlast_video_prompt(
        _spec(), duration_hint=0, skeleton=sk)
    assert "｜起]" in p and "｜承]" in p and "｜合]" in p
    assert "6 seconds" in p  # duration_hint 空 ⇒ 用模版默认时长


def test_skeleton_meta_note_consumed_and_no_negative_knob():
    # meta_note 进 ①。负面旋钮整条摘掉（第 87 刀）：骨架数据里再写 `negative_extra`
    # 也只是被忽略——锁住这条，免得将来有人「顺手」把它接回正面流。
    sk = video_prompt.VideoSkeleton.from_data({"negative_extra": "禁止字幕", "meta_note": "H3 高服从"})
    assert sk is not None
    p = video_prompt.compile_climax_video_prompt(_spec(), skeleton=sk)
    assert "禁止字幕" not in p
    assert "H3 高服从" in p.split("[参考绑定]")[0]
    assert not hasattr(sk, "negative_extra")


def test_skeleton_legacy_six_key_data_normalizes():
    """旧模版（六键排列 + ⑥ 标题 + negative_extra）载入 ⇒ 剔 ⑥、五键相对顺序原样保留。

    第 87 刀的数据兼容锁：判非法会让 `get_template` 返回 None ⇒ 整条模版从列表里消失，
    用户看到的是「我做的模版没了」，比留着死旋钮更糟。
    """
    sk = video_prompt.VideoSkeleton.from_data({
        "section_order": ["audio", "negative", "meta", "binding", "subject", "segments"],
        "section_titles": {"meta": "元信息·改", "negative": "不要的东西"},
        "negative_extra": "模糊",
    })
    assert sk is not None
    assert sk.section_order == ("audio", "meta", "binding", "subject", "segments")
    assert sk.order() == ("audio", "meta", "binding", "subject", "segments")
    assert sk.section_titles == {"meta": "元信息·改"}   # 退役键的标题一并丢弃


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
    })
    assert sk is not None
    assert sk.section_titles == {"meta": "元信息·改"}     # 坏键忽略
    assert sk.section_order is None                        # 非排列忽略
    assert sk.camera_band_overrides == {2: "镜头横移"}      # 坏档位忽略
    assert sk.beat_labels == ("起",)
    assert sk.default_duration is None                      # 超范围忽略


def test_default_skeleton_data_projection():
    data = video_prompt.default_skeleton_data("transition")
    assert data["section_titles"]["segments"] == "转场分镜"
    assert data["section_order"] == list(video_prompt.SECTION_KEYS)
    assert video_prompt.default_skeleton_data("climax")["section_titles"]["segments"] == "时间分镜"


# ── 首帧保真三刀（2026-11-01 真机实锤 turn 9a1ecaae）─────────────────────────
# 底图是「卧室里双手捧戒指的特写」，视频第 1 拍却写成「厨房台面上的抽插」，
# 且 7 秒里换了 6 次机位、负面约束里还留着「cropped, bad composition」。
# 下面逐刀锁：元信息声明起始帧 / 首拍不重新定场 / 无角色名也锁身份 / 剔除冲突构图项。

_BEATS6 = {"action_sequence": [
    {"beat": "定格起点", "desc": "cupped hands holding a pink ring"},
    {"beat": "延伸", "desc": "she lifts her face"},
    {"beat": "延伸", "desc": "she pushes the ring into his palm"},
    {"beat": "延伸", "desc": "her breathing slows"},
    {"beat": "收尾", "desc": "she leans onto the pillow"},
    {"beat": "收尾", "desc": "the lamp light steadies"},
]}


def _segments(prompt: str) -> list[str]:
    block = prompt.split("[时间分镜]：", 1)[1].split("\n\n", 1)[0]
    return [ln for ln in block.split("\n") if ln.strip()]


def test_climax_pending_frame_declares_starting_frame():
    """刀③：自动链在成图**之前**编译提示词（first_frame 此时必为空），仍要按 I2V 声明。

    旧行为只有 has_frame=True 才写「使用输入图片作为准确起始帧」，自动链恒为 False ⇒
    这段文字在模型眼里是文生视频，首帧自由构图，底图机位/景别当场丢失。
    """
    p = video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), **_BEATS6}, pending_frame=True)
    meta = p.split("[元信息]：", 1)[1].split("\n\n", 1)[0]
    assert "使用输入图片作为准确起始帧" in meta
    assert "首帧必须逐字复现图片1的机位、构图、人物姿态与服装" in meta
    # 无声明的旧路径逐字保持原样（文生视频仍不得声称有起始帧）
    q = video_prompt.compile_climax_video_prompt({**_spec(camera=""), **_BEATS6})
    assert "使用输入图片作为准确起始帧" not in q


def test_climax_has_frame_and_pending_frame_are_equivalent():
    """首帧已到（手动重试）与首帧待挂（自动链）编出同一段提示词——两条链共用一份文字。"""
    spec = {**_spec(camera=""), **_BEATS6}
    now = video_prompt.compile_climax_video_prompt(spec, has_frame=True)
    later = video_prompt.compile_climax_video_prompt(spec, pending_frame=True)
    assert now == later


def test_climax_first_beat_holds_base_image_camera():
    """刀④-1：有底图时首拍不得重新定场（旧行为按 motion 档位推镜/换景别）。"""
    spec = {**_spec(camera=""), **_BEATS6}
    first = _segments(
        video_prompt.compile_climax_video_prompt(spec, pending_frame=True))[0]
    assert video_prompt._HOLD_CAMERA_FRAME in first
    old_first = _segments(video_prompt.compile_climax_video_prompt(spec))[0]
    assert video_prompt._HOLD_CAMERA_FRAME not in old_first


def test_climax_all_beats_share_one_camera():
    """刀④-2 的升级形态：中段「每 2 拍换一次镜」改成**全片不换镜**。

    原结论（每拍都换＝模型逐拍重新构图，定格机位活不过第 2 拍）是对的，只是收得不够：
    5 秒里两句互斥机位照样丢首帧构图 ⇒ 高潮延伸统一成一条机位句（2026-11-04 用户定案）。
    """
    spec = {**_spec(camera=""), **_BEATS6}
    cams = [ln.split("｜", 1)[1].split("；")[0]
            for ln in _segments(video_prompt.compile_climax_video_prompt(
                spec, pending_frame=True))]
    assert len(cams) == len(_BEATS6["action_sequence"])
    assert set(cams) == {video_prompt._HOLD_CAMERA_FRAME}
    # 文生视频（无首帧）同样一句到底，只是不引用图片1
    t2v = [ln.split("｜", 1)[1].split("；")[0]
           for ln in _segments(video_prompt.compile_climax_video_prompt(spec))]
    assert set(t2v) == {video_prompt._HOLD_CAMERA_NOFRAME}


def test_climax_locks_identity_without_actor_names():
    """刀⑤-1：在场角色名为空也要锁身份——否则没有任何一条约束要求沿用图里那张脸。"""
    spec = {**_spec(actors=[], camera=""), **_BEATS6}
    binding = video_prompt.compile_climax_video_prompt(
        spec, pending_frame=True).split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "保持图片1中人物的身份、脸部、服装、发型、造型完全一致" in binding
    # 有名字时仍走原来的「角色名→样貌」写法，不重复追加无名写法
    named = video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), **_BEATS6}, pending_frame=True)
    assert "图片1中心的角色为纪含章、庄听澜、邵允" in named
    assert "保持图片1中人物的身份" not in named


def test_climax_negative_items_never_enter_the_positive_stream():
    """第 81 刀收掉刀⑤-2：与底图冲突的「cropped / bad composition」不再靠摘词救，
    而是整段 ⑥ 都不产出。

    原缺陷的真实形状是「图片预设的负面串以正面文本进 H3」（这条链只有 positive），
    摘掉两个词只是止血；现在 worst quality / lowres / cropped 这类词一个都不进提示词，
    有底图与无底图两条分支同结果。
    """
    spec = {**_spec(camera="", negative_prompt="worst quality, cropped, bad composition, text"),
            **_BEATS6}
    for kwargs in ({"pending_frame": True}, {}):
        p = video_prompt.compile_climax_video_prompt(spec, **kwargs)
        assert "[负面约束]" not in p
        for banned in ("worst quality", "cropped", "bad composition", "lowres", "text"):
            assert banned not in p


def test_build_request_climax_pending_frame_warns_honestly():
    """刀③接线：pending_frame 的缺图警告不得再说「将以文字生成动作画面」（trace 会指错方向）。"""
    req = video_prompt.build_video_request(
        mode="climax", spec={**_spec(camera=""), **_BEATS6},
        video_config={"base_url": "u", "model": "h3"}, pending_frame=True)
    assert req["submit"]["images"] == []
    assert req["warnings"] == ["高潮参考图编译时未产出：首帧由下游链补挂本张成图"]


# ── 第 80 刀 · 首尾帧分镜层（2026-11-04）────────────────────────────────────
# 离线真链路模拟（5 段剧情 / 12s）暴露三处结构性缺陷：开场定格与中段前 3 拍重复、
# narrative 只取前 8 句导致后半剧情全丢、对白与说话人拆成两拍；5s 版另有 0s–0s 死拍。
# ⚠ 本刀只管 firstlast 的「分镜内容」：机位口径（逐拍换镜）是第 78/79 刀用户定案，不动。


def test_firstlast_mid_skips_opening_duplicates():
    """开场定格已逐字带上 first_frame_desc，中段不得把同一段画面再演一遍。"""
    opening = "她推门进来，收起伞。老周抬头看了一眼。"
    spec = _spec(
        narrative=f"{opening}她坐到靠窗的桌子。面端上来了。",
        camera="", motion=0,
    )
    p = video_prompt.compile_firstlast_video_prompt(
        spec, duration_hint=12, first_frame_desc=opening, last_frame_desc="她放下筷子起身。",
    )
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    assert seg.count("她推门进来，收起伞") == 1
    assert seg.count("老周抬头看了一眼") == 1
    assert "她坐到靠窗的桌子" in seg
    assert "面端上来了" in seg


def test_firstlast_covers_narrative_beyond_eight_sentences():
    """取消 [:8] 硬截断：12 句剧情的后半也必须进分镜，不能演到「面上桌」就断。"""
    spec = _spec(
        narrative="".join(f"第{i}个动作发生在面馆。" for i in range(12)),
        camera="", motion=0,
    )
    p = video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12)
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    for i in range(12):
        assert f"第{i}个动作" in seg


def test_firstlast_dialogue_and_speaker_share_one_beat():
    """「还是老样子？」与「老周问。」不得拆成两拍：口型同步与说话人必须同一镜。"""
    spec = _spec(narrative="「还是老样子？」老周问。她点头，在靠窗坐下。", camera="", motion=0)
    p = video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12)
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    assert any("还是老样子" in ln and "老周问" in ln for ln in _segments(p))
    assert "台词随口型同步" in seg


def test_firstlast_short_duration_has_no_zero_length_beats_and_keeps_plot():
    """5 秒 12 句：拍数≤秒数（无 0s–0s / 1s–1s 空窗口），且合并后不丢任何一句剧情。"""
    import re

    spec = _spec(
        narrative="".join(f"第{i}个动作发生在面馆。" for i in range(12)),
        camera="", motion=0,
    )
    p = video_prompt.compile_firstlast_video_prompt(spec, duration_hint=5)
    lines = _segments(p)
    assert len(lines) <= 5
    for line in lines:
        matched = re.match(r"\[(\d+)s–(\d+)s｜", line)
        assert matched, line
        assert int(matched.group(2)) - int(matched.group(1)) >= 1
    seg = p.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]
    for i in range(12):
        assert f"第{i}个动作" in seg


# ── 第 81 刀 · 提示词去冗余（2026-11-04 审核实锤）────────────────────────────
# 真机一条 3166 字的高潮稿里：③ 逐字灌进整卡 appearance（世界书段落 + 全部角色外貌）
# 占 61 %、④ 每拍复述一遍 45 字机位长句、⑥ 在**没有 negative 通道**的链上把
# 「worst quality / extra fingers」当正面内容念给模型。三处都只删冗余，不加新语义。
# ⚠ 下面的外观串是合成 fixture（真卡专名/正文不得进 tests，见隐私纪律）。

_CARD_DUMP = (
    "世界观：誓约制度把人物当作可交易的资源，戒指是权钱的徽记。；"
    "魔戒设定：钻面里有一只眼睛，戴上后情感夺舍、肉体改造。；"
    "纪含章(暗黄长发、成熟知性、连衣裙侧开衩)；庄听澜(螺甸紫长发、猫耳发饰、黑丝女仆装)"
)


def test_subject_scene_drops_card_lore_dump_and_placeholder():
    """拆不出「画面在场角色」⇒ ③ 就不写外貌，只留衣着/场景；占位值 `(待定)` 一并滤掉。"""
    spec = _spec(actors=[], frame_subjects=[], appearance=_CARD_DUMP,
                 wardrobe="(待定)", locale="IB031港区·宅邸二楼走廊")
    subject = video_prompt._subject_scene(spec)
    assert subject == "IB031港区·宅邸二楼走廊"
    for junk in ("誓约制度", "魔戒设定", "钻面里有一只眼睛", "待定", "庄听澜"):
        assert junk not in subject


def test_subject_scene_uses_frame_subjects_when_actors_empty():
    """actors 空但判据给了画面主体 ⇒ 只取在场那一条外貌，不在这一格里的角色不写。"""
    spec = _spec(actors=[], frame_subjects=["纪含章"], appearance=_CARD_DUMP,
                 wardrobe="(待定)")
    subject = video_prompt._subject_scene(spec)
    assert "纪含章（暗黄长发、成熟知性、连衣裙侧开衩）" in subject
    assert "庄听澜" not in subject and "誓约制度" not in subject


def test_subject_scene_placeholder_variants_are_dropped():
    """`(待定)` / `（待定）` / `暂无` / `-` 都不算内容；真外貌照常保留。"""
    for wardrobe in ("(待定)", "（待定）", "暂无", "-", "待定"):
        subject = video_prompt._subject_scene(
            _spec(wardrobe=wardrobe, appearance="纪含章(米色针织开衫)"))
        assert "待定" not in subject and "暂无" not in subject, wardrobe
        assert "米色针织开衫" in subject, wardrobe


def test_climax_prompt_carries_no_negative_block_end_to_end():
    """走 `build_video_request` 的整链形状：预设 negativePrompt 与 scene_spec 负面
    都不进 `submit.prompt`（这条链只有一路 positive 输入）。"""
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(camera=""),
        video_config={"base_url": "https://x/v", "model": "h3"},
        preset={"negativePrompt": "lowres, jpeg, cropped, watermark"},
        first_frame="http://x/c.png",
    )
    prompt = req["submit"]["prompt"]
    assert "[负面约束]" not in prompt
    for banned in ("lowres", "jpeg", "cropped", "watermark", "禁止五官漂移"):
        assert banned not in prompt
    assert req["prompt_sections"] == [
        "①元信息", "②参考绑定", "③主体/场景", "④时间分镜", "⑤音频",
    ]


def test_climax_beats_name_the_camera_without_restating_the_rule():
    """④ 逐拍机位只报「这一拍用什么机位」；「全程一致／背景光影」由 ① 说一次。

    第 79 刀的教训是别猜 H3 吃哪一句 ⇒ 每拍的机位**标记**保留，只砍与 ① 逐字重复的
    后半句（旧形态 5 拍各复述 45 字长句）。
    """
    spec = {**_spec(camera=""), **_BEATS6}
    p = video_prompt.compile_climax_video_prompt(spec, pending_frame=True)
    cams = [ln.split("｜", 1)[1].split("；")[0] for ln in _segments(p)]
    assert set(cams) == {video_prompt._HOLD_CAMERA_FRAME}
    assert all("全程" not in c for c in cams)
    meta = p.split("[元信息]：", 1)[1].split("\n\n", 1)[0]
    assert "机位、画框、景别与背景光影全程保持一致" in meta
    assert p.count("背景光影") == 1  # 只在 ① 声明一次，不逐拍重申


def test_climax_reveal_beat_camera_keeps_only_the_direction():
    """大场面档同理：逐拍留方向，「全片只做这一条连续运动」收进 ① 的契约声明。"""
    spec = _spec(camera="", composition="", actors=[], aspect_ratio="16:9", locale="港区",
                 narrative="舰队驶入港口，海面辽阔，天际线在晨雾里连成一片。")
    p = video_prompt.compile_climax_video_prompt(spec, size="16:9", pending_frame=True)
    cams = {ln.split("｜", 1)[1].split("；")[0] for ln in _segments(p)}
    assert cams == {video_prompt._REVEAL_CAMERA_TILT}
    assert "中途不间断" in p.split("[元信息]：", 1)[1].split("\n\n", 1)[0]
    assert all("不间断" not in c for c in cams)


# ── 第 82 刀 · 逐拍主语与循环断言（2026-11-04 离线检验实锤）──────────────────────
# 第 79 刀的短语裁剪只管「不把迁移写回时间轴」，不管「剩下的句子还有没有主语」——
# 离线重放真稿入参时 4 拍里 3 拍的**主句本身就是迁移句**，剔完只剩没有名词主语的从句；
# ① 又无条件断言「末态回到起始那一格的同一姿势，可无缝循环」，与末拍落在另一个构型互斥。
# 两条都由离线重放抓到，不需要出片。
# ⚠ 下面的动作句是**按同一语法形状合成的 fixture**（把栏杆练功当场景），真机正文不进 tests。

_SHIFT_FIRST_BEATS = {"action_sequence": [
    {"beat": "定格起点",
     "desc": "A tall dancer bends forward over a low rail, both hands on the wood"},
    {"beat": "延伸", "desc": "The partner pushes slowly, her shoulders rock with each count"},
    {"beat": "收尾",
     "desc": "The shawl slips off her shoulder, swaying with each step"},
]}

_HOLD_FX = "能量爆发式特效，画面保持稳定"


def _beat_actions(prompt: str) -> list[str]:
    """逐拍只取动作文本段（第 0 段是机位句，特效/同步句按常量剔除）。"""
    acts: list[str] = []
    for line in _segments(prompt):
        for part in line.split("｜", 1)[1].split("；")[1:]:
            if part and part != _HOLD_FX and part != "与节拍/重音同步":
                acts.append(part)
    return acts


def _meta(prompt: str) -> str:
    return prompt.split("[元信息]：", 1)[1].split("\n\n", 1)[0]


def test_climax_shift_pruning_does_not_leave_a_subjectless_beat():
    """主句被剔的拍把「谁」留在句首＋回指首帧，④ 不再出现没有施动者的动作。

    第 82 刀只补了构图回指（`承接图片1的同一构型，both hands gripping the edge`），
    读的人仍不知道是谁的双手——离线重放真稿时用户实锤这条；第 83 刀把被剔主句的
    主语短语抽回来放在最前。
    """
    spec = {**_spec(camera=""), **_SHIFT_FIRST_BEATS}
    p = video_prompt.compile_climax_video_prompt(spec, pending_frame=True)
    acts = _beat_actions(p)
    assert len(acts) == 3
    # 迁移过程仍然不写回时间轴
    assert "bends forward over a low rail" not in p
    assert "slips off her shoulder" not in p
    # 两处「主句即迁移」的拍各补一次回指，且回指前带着主语
    assert sum(1 for a in acts if video_prompt._FRAME_ANAPHORA in a) == 2
    assert acts[0].startswith("A tall dancer，")
    assert acts[2].startswith("The shawl，")
    assert "The partner pushes slowly, her shoulders rock with each count" in acts
    for act in acts:
        # 没有任何一拍以回指短语开头＝每一拍都自带施动者
        assert not act.startswith(video_prompt._FRAME_ANAPHORA), act
        assert act[0].isupper(), act


def test_climax_keeps_the_whole_clause_when_no_subject_can_be_recovered():
    """抽不出主语 ⇒ 整句原样保留，宁可留一个迁移短语也不交出一句无主语的动作。"""
    # 迁移动词在句首（主语本来就没写）
    no_head = {"action_sequence": [
        {"beat": "延伸", "desc": "Bends forward over the rail, both hands flat on the wood"},
    ]}
    p = video_prompt.compile_climax_video_prompt({**_spec(camera=""), **no_head},
                                                 pending_frame=True)
    assert "Bends forward over the rail" in p
    assert video_prompt._FRAME_ANAPHORA not in p
    # 切点左侧长过主语（整句其实在讲别的事）⇒ 同样不冒险改写
    too_long = {"action_sequence": [
        {"beat": "延伸",
         "desc": "A broad shouldered young man in a damp linen shirt and rolled sleeves "
                 "leans forward, his hands steady"},
    ]}
    p2 = video_prompt.compile_climax_video_prompt({**_spec(camera=""), **too_long},
                                                  pending_frame=True)
    assert "leans forward" in p2
    assert video_prompt._FRAME_ANAPHORA not in p2


def test_climax_drops_the_loop_claim_when_the_last_beat_ends_elsewhere():
    """末拍落在别的构型 ⇒ ① 不再断言「回到起始姿势／可无缝循环」。"""
    spec = {**_spec(camera=""), **_SHIFT_FIRST_BEATS}
    meta = _meta(video_prompt.compile_climax_video_prompt(spec, pending_frame=True))
    assert "可无缝循环" not in meta
    assert "末态回到起始那一格" not in meta
    assert "末态停在最后一拍给定的那一格" in meta


def test_climax_keeps_the_loop_claim_when_the_last_beat_returns():
    """末拍自己声明回位时，循环断言照旧成立（不是无条件删掉）。"""
    beats = {"action_sequence": [
        {"beat": "定格起点", "desc": "she keeps her hands on the counter edge"},
        {"beat": "收尾", "desc": "she settles back to the same pose as the first frame"},
    ]}
    spec = {**_spec(camera=""), **beats}
    meta = _meta(video_prompt.compile_climax_video_prompt(spec, pending_frame=True))
    assert "可无缝循环" in meta


def test_climax_single_beat_timeline_keeps_the_loop_claim():
    """无动作序列时 ④ 只有一拍（画面级要素兜底）：整条时间轴就是那一格，循环成立。"""
    spec = _spec(camera="", action_sequence=[])
    p = video_prompt.compile_climax_video_prompt(spec, pending_frame=True)
    assert len(_segments(p)) == 1
    assert "可无缝循环" in _meta(p)


def test_climax_loop_claim_follows_narrative_fallback_beats_too():
    """narrative 兜底同样会切出多拍 ⇒ 循环断言要看这份计划，不能只认 action_sequence。"""
    spec = _spec(camera="", composition="", action_sequence=[],
                 narrative="她扶住横杆稳住呼吸。他绕到身后扶稳她的腰。她把重心压到左腿，右手撑住栏杆。")
    p = video_prompt.compile_climax_video_prompt(spec, pending_frame=True)
    assert len(_segments(p)) > 1
    assert "可无缝循环" not in _meta(p)


# ── 第 84 刀 · 第一人称取景贯彻（2026-11-05 真机试跑实锤）────────────────────────
# 图片侧早就是第一人称（`profile_prompt` 首行带 `first person view`，男性只有从画框下缘
# 进入的肢体），视频链却**一次都没读过 `viewpoint`**：成稿 ③ 写 `a younger man behind her`、
# ④ 写 `The man thrusts forward`／`The man，承接图片1…`，把叙事者钉成画面里的第二个全身，
# 而 ① 又要求「逐字复现图片1」⇒ 模型只能凭空补人或忽略首帧。
# 本刀三条落点都在编译层/护栏层，**不依赖名字**（本机 `narrator="(待定)"` ⇒ 名字型闸全空转）。
# ⚠ 下面全部是**合成 fixture**，真机正文与专名不进 tests。

_POV_LIMBS = [
    "a pair of hands entering from the lower frame edge gripping her waist",
    "another hand lifting her thigh from the lower frame edge",
]


def test_climax_pov_declaration_and_limb_facts_land_in_meta():
    """① 用肯定式声明第一人称视点，并把「哪些肢体真的进了这一格」逐字列出。"""
    spec = {**_spec(camera=""), "viewpoint": "first_person", "narrator_limbs": list(_POV_LIMBS)}
    meta = _meta(video_prompt.compile_climax_video_prompt(spec, pending_frame=True))
    assert video_prompt._POV_VIEW_ANCHORED in meta
    for limb in _POV_LIMBS:
        assert limb in meta
    # 两条事实自己已经带画框边缘限定 ⇒ 不再复述一遍（白占 ① 的字数预算）
    assert video_prompt._POV_LIMB_TAIL not in meta


def test_climax_pov_meta_adds_no_meta_language():
    """① 只禁**元语言**，不禁归属解释（第 90 刀收窄，语义变更需复核）。

    本锁原为第 68 刀措辞纪律的视频侧延伸——当时读成「① 里不写任何解释句」，产物就是一串
    裸肢体 tag，没有说明那双手是谁的。用户 2026-11-05 判据「肯定是要解释信息归属于哪个对象的，
    短句是不够用的」推翻的是**那一半**；禁词表本身不变：被禁的是模型无从对应的**英文元语言**
    （`narrator`／`camera`／`point of view`）与第 68 刀点名的三种「读不懂」的中文说法
    （「叙述者」「镜头即」「不出现在画面」）。归属句用画面里看得见的名词写，不落在禁表上。
    """
    p = video_prompt.compile_climax_video_prompt(
        {**_spec(camera="", composition=""), "viewpoint": "first_person",
         "narrator_limbs": list(_POV_LIMBS)}, pending_frame=True)
    lowered = p.lower()
    for banned in ("narrator", "camera", "point of view", "叙述者", "镜头即", "不出现在画面"):
        assert banned not in lowered and banned not in p, banned


def test_climax_third_person_meta_is_byte_identical():
    """零回归锁：`viewpoint` 缺失／非 first_person ⇒ ① 与旧稿件逐字节相同。

    判据只认 `first_person` 这一个值 ⇒ 第三人称帧（用户口径「按剧情正常来」）
    和根本没给人称的既有链路都不新增任何文本。
    """
    plain = video_prompt.compile_climax_video_prompt(_spec(), pending_frame=True)
    third = video_prompt.compile_climax_video_prompt(
        {**_spec(), "viewpoint": "third_person", "narrator_limbs": list(_POV_LIMBS)},
        pending_frame=True)
    assert third == plain
    assert video_prompt._POV_VIEW_PLAIN not in plain


def test_climax_pov_without_frame_does_not_anchor_declaration_to_image1():
    """无首帧不得引用不存在的图：声明退成不带「图片1」的那一档。"""
    meta = _meta(video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), "viewpoint": "first_person"}, size="9:16"))
    assert video_prompt._POV_VIEW_PLAIN in meta
    assert video_prompt._POV_VIEW_ANCHORED not in meta


def test_climax_pov_reveal_branch_uses_unanchored_declaration():
    """大场面档机位本来就在动：「与图片1共用同一视点位置」与「沿路径持续向前」互斥，只用朴素声明。"""
    spec = {**_spec(camera="", composition="", actors=[], aspect_ratio="16:9", locale="港区",
                    narrative="舰队驶入港口，天际线在晨雾里连成一片。",
                    viewpoint="first_person")}
    meta = _meta(video_prompt.compile_climax_video_prompt(spec, size="16:9", pending_frame=True))
    assert video_prompt._POV_VIEW_PLAIN in meta
    assert video_prompt._POV_VIEW_ANCHORED not in meta


def test_climax_pov_limb_tail_only_when_fact_lacks_the_marker():
    """没带画框边缘字样的事实才补限定；已带的不重复补。"""
    bare = _meta(video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), "viewpoint": "first_person",
         "narrator_limbs": ["his hands gripping her hips"]}, pending_frame=True))
    assert "his hands gripping her hips" + video_prompt._POV_LIMB_TAIL in bare
    mixed = _meta(video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), "viewpoint": "first_person",
         "narrator_limbs": [_POV_LIMBS[0], "his other hand on her hip"]}, pending_frame=True))
    assert mixed.count(video_prompt._POV_LIMB_TAIL) == 1
    assert "his other hand on her hip" + video_prompt._POV_LIMB_TAIL in mixed
    empty = _meta(video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), "viewpoint": "first_person", "narrator_limbs": []},
        pending_frame=True))
    assert video_prompt._POV_LIMB_TAIL not in empty


def test_frame_limb_facts_reads_only_the_tag_line():
    """肢体事实的成稿真源＝首行 tag；散文行里出现 `frame edge` 不算（那是环境描述）。"""
    prompt = (
        "1girl, " + _POV_LIMBS[0] + ", black pantyhose, " + _POV_LIMBS[1]
        + ", " + _POV_LIMBS[0] + "\n"
        "masterpiece, the moonlight falling across the frame edge of the balcony"
    )
    assert video_prompt.frame_limb_facts(prompt) == _POV_LIMBS
    assert video_prompt.frame_limb_facts("1girl, solo, kitchen") == []
    assert video_prompt.frame_limb_facts("") == []


def test_climax_pov_drops_the_whole_person_subject_prefix():
    """④ 护栏：第一人称下「整人指称」换成**归属主语**，既不写第二个全身，也不交白卷。

    第 83 刀把被剔主句的主语放回句首（`The man，承接图片1…`）⇒ 第 84 刀删掉那半句、只留回指；
    第 90 刀按用户判据把空着的那半句补成 `_POV_BEAT_ACTOR`——名词中心是**肢体**、定语说明归属，
    于是「谁在做这一拍」有答案，而画面里仍然只有伸进来的那一处，没有第二个全身。
    """
    beats = {"action_sequence": [
        {"beat": "延伸",
         "desc": "The man bends forward over her, her shoulders rock with each count"},
    ]}
    pov = _beat_actions(video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), **beats, "viewpoint": "first_person"}, pending_frame=True))
    assert pov[0].startswith(video_prompt._POV_BEAT_ACTOR + "，" + video_prompt._FRAME_ANAPHORA)
    assert "The man" not in pov[0]
    assert "her shoulders rock with each count" in pov[0]
    # 中文整人指称同判
    zh = video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), "viewpoint": "first_person", "action_sequence": [
            {"beat": "延伸", "desc": "他弓起背，她的手指扣紧台沿"}]}, pending_frame=True)
    assert "他，" not in zh


def test_第90刀_pov_beat_actor_is_a_limb_owned_by_the_camera_holder():
    """归属主语的形状约束：中心语是肢体、有归属定语、不带任何人物全身名词。"""
    actor = video_prompt._POV_BEAT_ACTOR
    assert "肢体" in actor and "镜头" in actor
    for whole_person in ("男", "人，", "man", "he ", "青年", "男子"):
        assert whole_person not in actor, whole_person
    # 与 ① 的归属主句同一口径（两句互相解释，不新造第三种说法）
    assert "镜头后面那一位" in actor and "镜头后面那一位" in video_prompt._POV_LIMB_OWNER_CLAIM


def test_第90刀_ownership_claim_lands_only_where_there_are_limb_facts():
    """① 的归属主句跟着肢体事实走：有事实才解释（没有指代对象时写了只是白占字数）。"""
    with_limbs = _meta(video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), "viewpoint": "first_person",
         "narrator_limbs": list(_POV_LIMBS)}, pending_frame=True))
    assert video_prompt._POV_LIMB_OWNER_CLAIM in with_limbs
    # 主句排在裸 tag 之前，模型先读到归属、再读事实列表
    assert with_limbs.index(video_prompt._POV_LIMB_OWNER_CLAIM) < with_limbs.index(_POV_LIMBS[0])

    without_limbs = _meta(video_prompt.compile_climax_video_prompt(
        {**_spec(camera=""), "viewpoint": "first_person"}, pending_frame=True))
    assert video_prompt._POV_LIMB_OWNER_CLAIM not in without_limbs
    # 第三人称零回归：不新增一个字符（与 84 刀那条逐字节锁同源，这里补归属之后的形态）
    plain = _meta(video_prompt.compile_climax_video_prompt(_spec(), pending_frame=True))
    assert video_prompt._POV_LIMB_OWNER_CLAIM not in plain


def test_climax_third_person_keeps_the_subject_prefix():
    """反向锁：第三人称（或调用方没给人称）⇒ 第 83 刀行为逐字节保留。"""
    beats = {"action_sequence": [
        {"beat": "延伸",
         "desc": "The man bends forward over her, her shoulders rock with each count"},
    ]}
    for spec_view in ({}, {"viewpoint": "third_person"}):
        acts = _beat_actions(video_prompt.compile_climax_video_prompt(
            {**_spec(camera=""), **beats, **spec_view}, pending_frame=True))
        assert acts[0].startswith("The man，" + video_prompt._FRAME_ANAPHORA), spec_view


def test_climax_pov_keeps_limbs_and_descriptive_subjects():
    """只剔「整人指称」：肢体所有格正是该入画的形态，带描述的主语另有其人，都不动。"""
    cases = {
        "His hands lift her thigh, her heel strains against the stool": "His hands，",
        "A tall woman with long hair bends forward, her apron creaks taut": "A tall woman",
    }
    for desc, expected in cases.items():
        acts = _beat_actions(video_prompt.compile_climax_video_prompt(
            {**_spec(camera=""), "viewpoint": "first_person",
             "action_sequence": [{"beat": "延伸", "desc": desc}]}, pending_frame=True))
        assert expected in acts[0], desc


# ── 第 86 刀 · firstlast 说话人归属（2026-11-05，收第 80 刀登记的实测残留）────────
# 残留原文（A/03 §14.4）：「『台词 + 说话人 + 第二句台词』复合句仍拆两拍
# （`_is_speaker_attribution` 只认纯归属短句）」。真机第二句台词与归属**同句**，
# 于是归属被第二句带走、第一句成了没人说过的台词。

def test_firstlast_attribution_then_second_line_keeps_the_first_speaker():
    """`「台词」老周问了一句，「第二句。」`：归属并进含引号的前一拍，两句台词都还在。"""
    spec = _spec(narrative="「还是老样子？」老周问了一句，「面还没好。」她坐到靠窗的桌前。",
                 camera="", motion=0)
    lines = _segments(video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12))
    holders = [ln for ln in lines if "老周问了一句" in ln]
    assert len(holders) == 1, lines
    assert "还是老样子" in holders[0], lines
    assert "面还没好" in holders[0], lines


def test_firstlast_attribution_colon_form_merges_into_the_quoted_beat():
    """冒号式归属 `老周说道：「…」` 与纯短句 `老周问。` 同口径：不得另起一拍。"""
    spec = _spec(narrative="「还是老样子？」老周说道：「面还没好。」", camera="", motion=0)
    lines = _segments(video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12))
    holders = [ln for ln in lines if "老周说道" in ln]
    assert len(holders) == 1, lines
    assert "还是老样子" in holders[0], lines


def test_firstlast_event_prefix_before_quote_stays_its_own_beat():
    """反向锁：引号前那段是**事件**（无说话动词）就不算归属，不许并进上一拍。"""
    spec = _spec(narrative="「先坐。」她站起身，把伞挂到门后，「面还得等会儿。」",
                 camera="", motion=0)
    lines = _segments(video_prompt.compile_firstlast_video_prompt(spec, duration_hint=12))
    holders = [ln for ln in lines if "她站起身" in ln]
    assert len(holders) == 1, lines
    assert "先坐" not in holders[0], lines
    assert "面还得等会儿" in holders[0], lines


# ── 第 99 刀 · 对白时间轴（2026-11-08 用户定案）──────────────────────────────
# 用户口径：同轮 TTS 是一句一句拼起来的**紧凑**录音，整条当参考音频上传；「哪句在哪个
# 场景/动作/镜头说」由 ④ 拍窗口承载。拼接音频只作 `<Audio 1>` 的 reference（音色/语气）。

def _dialogue_lines(*pairs):
    return [{"speaker": speaker, "text": text} for speaker, text in pairs]


def _firstlast_request(spec, *, audio_lines=None, duration=None):
    preset = {"videoDurationHint": duration} if duration else {}
    return video_prompt.build_video_request(
        mode="firstlast", spec=spec, video_config={}, preset=preset,
        audio_lines=audio_lines,
    )


def test_firstlast_dialogue_matches_quote_beat_with_h3_tags():
    """台词命中正文引语那一拍：H3 标签（<Audio 1>/<d>/（S1））与拍窗口都要对。"""
    d = _firstlast_request(_spec(), duration=12)["dialogue"]
    assert d is None  # 无 TTS 台词 ⇒ 不产出对白块

    req = _firstlast_request(
        _spec(), duration=12,
        audio_lines=_dialogue_lines(("纪含章", "开饭")),
    )
    d = req["dialogue"]
    assert d is not None
    assert d["line_count"] == 1
    item = d["timeline"][0]
    assert item["label"] == "剧情演变"          # 正文引语所在拍，不是开场/收尾定格
    assert item["speaker_id"] == "S1"
    assert item["text"] == "开饭"
    assert item["exact"] is True
    assert "<Audio 1> 是本轮角色对白的连续参考录音" in d["text"]
    assert "(S1)=纪含章" in d["text"]
    assert (
        f"[{item['start']}s–{item['end']}s｜剧情演变] (S1) 纪含章：<d>[Chinese] 开饭</d>"
        in d["text"]
    )
    # 对白块**不拼进 video_prompt**：模板没暴露参考音频槽时保持「视频提示词只有效果音」
    assert "<d>" not in req["submit"]["prompt"]
    assert "<Audio 1>" not in req["submit"]["prompt"]


def test_firstlast_dialogue_prose_without_quotes_spreads_over_mid_beats():
    """正文一句引语都没有：台词按顺序铺到中段拍（不含首尾定格），并诚实标非逐字命中。"""
    spec = _spec(narrative="她推门进来。她坐下。面端上来。", camera="", motion=0)
    d = _firstlast_request(
        spec, duration=15,
        audio_lines=_dialogue_lines(("甲", "第一句"), ("乙", "第二句")),
    )["dialogue"]
    assert d is not None
    assert [item["text"] for item in d["timeline"]] == ["第一句", "第二句"]
    assert all(item["exact"] is False for item in d["timeline"])
    plan, _, _stats = video_prompt._firstlast_beat_plan(
        spec, 15, "首帧/楼层开头画面", "尾帧/楼层结尾画面", "", True, True, None)
    mid_windows = {(start, end) for idx, (start, end, *_rest) in enumerate(plan)
                   if 0 < idx < len(plan) - 1}
    assert all((item["start"], item["end"]) in mid_windows for item in d["timeline"])
    starts = [item["start"] for item in d["timeline"]]
    assert starts == sorted(starts)
    assert any("未在正文引语中逐字命中" in w for w in d["warnings"])


def test_firstlast_dialogue_unmatched_line_uses_free_quote_beat_in_order():
    """文本没命中但正文有引语拍：按顺序铺到空余引语拍，exact=False + 告警。"""
    spec = _spec(narrative="「甲说第一句。」「乙说第二句。」", camera="", motion=0)
    d = _firstlast_request(
        spec, duration=12,
        audio_lines=_dialogue_lines(("丙", "一句对不上的台词")),
    )["dialogue"]
    assert d is not None
    assert d["timeline"][0]["exact"] is False
    assert d["timeline"][0]["label"] == "剧情演变"
    assert any("1 句台词未在正文引语中逐字命中" in w for w in d["warnings"])


def test_firstlast_dialogue_speaker_ids_follow_first_appearance():
    spec = _spec(narrative="「开饭了。」「来了。」", camera="", motion=0)
    d = _firstlast_request(
        spec, duration=12,
        audio_lines=_dialogue_lines(("甲", "开饭了"), ("乙", "来了")),
    )["dialogue"]
    assert d is not None
    assert d["speakers"] == {"S1": "甲", "S2": "乙"}
    assert [item["speaker_id"] for item in d["timeline"]] == ["S1", "S2"]


def test_第105刀_dialogue_timeline_caps_lines_by_beat_capacity():
    """单句 96 字（≈21.3s）塞不进 6s 片的任何 2s 拍窗口 ⇒ 从时间轴剔除并诚实告警。

    第 105 刀（实稿发现 5）口径变更：旧行为只告警「台词总量超过视频时长」却照列
    全部台词，与 ④ 的拍窗口自相矛盾（15s 塞 53 句）；现在 ⑤ 与 ④ 同一限速
    （4.5 字/秒），装不下的整句剔除，剔除句数进 warnings（不静默）。
    """
    long_line = "这是一句非常非常长的台词" * 8  # 96 字 ≈ 21.3s
    spec = _spec(narrative=f"纪含章说「{long_line}」。", camera="", motion=0)
    d = _firstlast_request(
        spec, duration=6,
        audio_lines=_dialogue_lines(("纪含章", long_line)),
    )["dialogue"]
    assert d is not None
    assert d["line_count"] == 0
    assert d["timeline"] == []
    assert any("1 句台词超出所在拍可说完的容量" in w for w in d["warnings"])
    assert "未安排进镜头" in d["text"]


def test_第105刀_dialogue_timeline_keeps_only_lines_that_fit_window():
    """同一拍两句台词、窗口只装得下一句 ⇒ 保序保留第一句、剔除第二句并报数（不静默）。

    2s 拍窗口容量 = 2 × 4.5 = 9 字：第一句 5 字留下，第二句再 5 字超容量 ⇒ 剔除。
    与 ④ 的 `_fit_beat_quotes` 同口径——④ 说得出几句，⑤ 就只列几句。
    """
    spec = _spec(narrative="「我们是朋友」「这不是朋友」。", camera="", motion=0)
    d = _firstlast_request(
        spec, duration=6,
        audio_lines=_dialogue_lines(("纪含章", "我们是朋友"), ("邵允", "这不是朋友")),
    )["dialogue"]
    assert d is not None
    assert [item["text"] for item in d["timeline"]] == ["我们是朋友"]
    assert d["speakers"] == {"S1": "纪含章"}
    assert any("1 句台词超出所在拍可说完的容量" in w for w in d["warnings"])


def test_第105刀_firstlast_action_beats_cap_quotes_and_warn():
    """动作流路径：8 句引语全挂一拍、加权后仍装不下 ⇒ 退回均分只写 3 句 + 顶层告警。

    实稿发现 5：15s 片子的中段拍里，8–10 句（108–230 字）被整段拼进两秒拍。
    现在按 `_DIALOGUE_CHARS_PER_SEC` 限速、保序保留整句；被裁句数进 `warnings`。
    第 107 刀：8 × 6 字 ≈ 11s，加首尾与其余拍 2s 下限共需 17s > 15s ⇒ 本夹具
    仍走「退回均分」失败面（加权路径的 9/9 用例见下方第 107 刀一组）。
    """
    quotes = [f"第{i}句长台词" for i in range(8)]  # 每句 6 字，共 48 字 ≈ 10.7s
    spec = _spec(
        narrative="".join(f"「{q}」" for q in quotes) + "。",
        camera="", motion=0,
        action_sequence=_action_beats(
            ("OPEN-DESC they sit around the counter", ""),
            ("She looks up and speaks", ""),
            ("CLOSE-DESC they finish", ""),
        ),
    )
    req = _firstlast_request(spec, duration=15)
    seg = _segments_of(req["submit"]["prompt"])
    assert "「第0句长台词」「第1句长台词」「第2句长台词」" in seg
    assert "第3句长台词" not in seg
    assert any("5/8 句引语" in w for w in req["warnings"])


def test_climax_carries_no_dialogue_block():
    """climax：高潮定格时刻对白通常已说完（2026-08-28 定案）⇒ 恒不产出对白块。"""
    req = video_prompt.build_video_request(
        mode="climax", spec=_spec(), video_config={},
        audio_lines=_dialogue_lines(("纪含章", "开饭")),
    )
    assert req["dialogue"] is None


# ── 批 2（2026-11-08）：firstlast ④ 吃提取层的动作流 + 每拍场景 ────────────────────
# 用户 92／94 刀原始规格：「模版和目前的一样，不一样的是**动作、分镜都要更复杂**」，
# 首尾帧影片覆盖「那一次对话生成的所有内容」。批 1 已把整轮正文喂进提取器，批 2 让
# 提取出的 `action_sequence` 真正撑起 ④ 的中段：开场/收尾仍是两张帧图，中段换成动作流，
# 正文引语按句分组附着（对白时间轴靠引语逐字命中，不能因为换成英文动作拍就丢失）。

def _action_beats(*rows):
    """[(desc, scene)] → 提取层 action_sequence 结构（scene 缺省为空串）。"""
    return [
        {"beat": f"第{idx}拍", "desc": desc, "scene": scene}
        for idx, (desc, scene) in enumerate(rows)
    ]


def _segments_of(prompt: str) -> str:
    return prompt.split("[时间分镜]：", 1)[1].split("[音频]：", 1)[0]


def test_批2_firstlast_mid_beats_follow_action_sequence():
    """中段用英文动作流（分镜更细）；只丢 desc[0]（纯复述首帧），末拍动作仍进 ④。"""
    spec = _spec(
        narrative="纪含章推门而入，朝众人点头。她低声说「开饭了」。庄听澜放下筷子站起身，望向门口。",
        action_sequence=_action_beats(
            ("OPEN-DESC pushes the door open into the noodle shop", ""),
            ("She nods at the two women, steam curling over the wooden counter", ""),
            ("Zhuang Tinglan sets down her chopsticks and rises from the stool", ""),
            ("CLOSE-DESC the three stand together at the doorway", ""),
        ),
    )
    p = video_prompt.compile_firstlast_video_prompt(
        spec, duration_hint=12,
        first_frame_desc="开场：三人已围坐面馆", last_frame_desc="三人并肩站在门口",
    )
    seg = _segments_of(p)
    assert "steam curling over the wooden counter" in seg
    assert "sets down her chopsticks" in seg
    # 首帧由开场拍复述 ⇒ desc[0] 丢弃；末拍是「落在尾帧」的动作收束 ⇒ 保留
    assert "定格于首帧画面：开场：三人已围坐面馆" in seg
    assert "收尾定格于尾帧画面：三人并肩站在门口" in seg
    assert "OPEN-DESC" not in seg
    assert "CLOSE-DESC the three stand together at the doorway" in seg
    # 中段三拍 + 首尾两拍 = 5 拍
    assert seg.count("s｜") == 5


def test_第102刀_firstlast_keeps_last_action_beat_that_lands_on_closing_frame():
    """实稿发现 6：末拍合同是「**落在**尾帧」，不是「只复述尾帧」——整拍排除会丢高潮动作。

    真实楼层（SAVE02 turn 18）末拍＝「Egil 把银戒慢慢戴上 → 靠墙 → 楼下闷响」；旧实现
    `action[1:-1]` 把它整拍丢掉，④ 只剩台词「这戒指，我戴上了」而没有戴戒动作。
    """
    spec = _spec(
        narrative="埃吉尔把银戒慢慢戴上，靠在墙边。楼下传来闷响。",
        action_sequence=_action_beats(
            ("OPEN-DESC she stands in the corridor holding the ring box", ""),
            ("She lifts the silver ring to the emergency light", ""),
            ("Egil slowly slides the silver ring onto her left ring finger, "
             "then braces one hand against the wall", ""),
            ("A muffled thud comes from the downstairs side room", ""),
        ),
    )
    p = video_prompt.compile_firstlast_video_prompt(
        spec, duration_hint=12,
        first_frame_desc="门被敲了第二遍", last_frame_desc="楼下侧室的木门闷响",
    )
    seg = _segments_of(p)
    assert "slides the silver ring onto her left ring finger" in seg
    assert "A muffled thud comes from the downstairs side room" in seg
    assert seg.index("slides the silver ring") < seg.index("收尾定格于尾帧画面")
    # 帧锚点各只出现一次：开场拍复述首帧、收尾拍定格尾帧
    assert "定格于首帧画面：门被敲了第二遍" in seg
    assert seg.count("收尾定格于尾帧画面：楼下侧室的木门闷响") == 1


def test_批2_firstlast_action_beats_keep_narrative_quotes():
    """换成英文动作拍后，正文引语仍要附着在对应拍上：对白时间轴靠它逐字命中。"""
    spec = _spec(
        narrative="纪含章推门而入。她低声说「开饭了」。庄听澜放下筷子站起身。",
        action_sequence=_action_beats(
            ("OPEN-DESC enters the shop", ""),
            ("She nods at the two women at the counter", ""),
            ("Zhuang Tinglan sets down her chopsticks and rises", ""),
            ("CLOSE-DESC the three stand at the doorway", ""),
        ),
    )
    req = _firstlast_request(
        spec, duration=12, audio_lines=_dialogue_lines(("纪含章", "开饭了")),
    )
    seg = _segments_of(req["submit"]["prompt"])
    assert "「开饭了」" in seg
    d = req["dialogue"]
    assert d is not None
    assert d["timeline"][0]["exact"] is True
    assert d["timeline"][0]["text"] == "开饭了"


def test_批2_firstlast_quotes_land_on_nearest_action_beat():
    """6 句正文 / 5 拍中段：引语按相对位置落**最近**一拍，不能整体后移。

    探针实锤（2026-11-08 真渲染）：旧分组用折叠边界反查句子，「开饭了」被挂到
    「把碗筷收进后厨」那一拍——人物已经走开才开口，口型同步也会跟着错拍。
    """
    spec = _spec(
        narrative=(
            "纪含章推门而入，朝众人点头。她低声说「开饭了」。"
            "庄听澜放下筷子站起身，望向门口。"
            "三人把碗筷收拾进后厨。邵允拎起门边的旧伞说「外面下大了」。"
            "三人挤在伞下走进雨里的长街。"
        ),
        action_sequence=_action_beats(
            ("OPEN-DESC pushes the door open", ""),
            ("She nods at the two women at the counter", ""),
            ("They stack the bowls toward the back kitchen", ""),
            ("Shao Yun picks up an old umbrella by the door", ""),
            ("The three step into the rain", ""),
            ("CLOSE-DESC walk away down the street", ""),
        ),
    )
    plan, _dur, _stats = video_prompt._firstlast_beat_plan(
        spec, 15, "开场：三人已在面馆围坐", "三人撑伞走在雨夜长街的背影", "",
    )
    # 7 拍（开场 + 5 中段 + 收尾）→ 15s：0–2 开场、2–4 / 4–6 / 6–8 / 8–10 / 10–12 中段、12–15 收尾
    hits = {
        quote: [(start, end) for start, end, _l, content, _e in plan if quote in content]
        for quote in ("「开饭了」", "「外面下大了」")
    }
    assert hits["「开饭了」"] == [(2, 4)]
    assert hits["「外面下大了」"] == [(8, 10)]


def test_批2_firstlast_scene_change_becomes_transition_beat():
    """每拍场景只在**变化**处渲染「场景切换」；同一地点不重复写。"""
    spec = _spec(
        narrative="三人吃完面。他们推门走进雨里。",
        action_sequence=_action_beats(
            ("OPEN-DESC they sit around the counter", "noodle shop interior"),
            ("They finish the noodles and stand up", "noodle shop interior"),
            ("They step out into the rain under the awning", "rainy street outside the shop"),
            ("CLOSE-DESC they walk away along the wet pavement", "rainy street outside the shop"),
        ),
    )
    seg = _segments_of(video_prompt.compile_firstlast_video_prompt(
        spec, duration_hint=12,
        first_frame_desc="面馆内三人围坐", last_frame_desc="雨夜街道三人背影",
    ))
    assert seg.count("场景切换：") == 1
    assert "场景切换：rainy street outside the shop；" in seg
    # 开场场景＝起点场景，不渲染成切换；中段同一场景也不重复
    assert "noodle shop interior" not in seg


def test_批2_firstlast_scene_without_open_scene_is_adopted_not_switched():
    """开场没给场景时，中段第一个场景只是「认下来」，不得谎报一次切换。"""
    spec = _spec(
        narrative="她推门进来。她坐下。",
        action_sequence=_action_beats(
            ("OPEN-DESC enters", ""),
            ("She walks to the counter", "noodle shop interior"),
            ("She sits down by the window", "noodle shop interior"),
            ("CLOSE-DESC she looks out the window", ""),
        ),
    )
    seg = _segments_of(video_prompt.compile_firstlast_video_prompt(
        spec, duration_hint=12, first_frame_desc="门口", last_frame_desc="窗边",
    ))
    assert "场景切换：" not in seg


def test_批2_firstlast_short_action_sequence_keeps_sentence_beats():
    """动作流不足 3 拍（只有开场＋收尾）时退回正文逐句成拍——不得退化成两拍空片。"""
    spec = _spec(
        narrative="她推门进来。她坐下。面端上来。",
        action_sequence=_action_beats(
            ("OPEN-DESC marker", ""),
            ("CLOSE-DESC marker", ""),
        ),
    )
    seg = _segments_of(video_prompt.compile_firstlast_video_prompt(
        spec, duration_hint=12, first_frame_desc="门口", last_frame_desc="桌边",
    ))
    for frag in ("她推门进来", "她坐下", "面端上来"):
        assert frag in seg
    assert "OPEN-DESC" not in seg and "CLOSE-DESC" not in seg


def test_批2_firstlast_without_action_sequence_is_unchanged():
    """零回归锁：没有动作流（或空数组）⇒ ④ 逐字节等于批 1 的句子成拍。"""
    plain = _spec(
        narrative="她推门进来。她坐下。面端上来。",
        floor_narrative="她推门进来。她坐下。面端上来。",
    )
    baseline = video_prompt.compile_firstlast_video_prompt(
        plain, duration_hint=12, first_frame_desc="门口", last_frame_desc="桌边",
    )
    empty = video_prompt.compile_firstlast_video_prompt(
        {**plain, "action_sequence": []},
        duration_hint=12, first_frame_desc="门口", last_frame_desc="桌边",
    )
    assert baseline == empty


def test_批2_firstlast_action_beats_fold_when_duration_is_short():
    """动作拍多于时长可容纳的拍数 ⇒ 按顺序合并，场景切换不丢。"""
    spec = _spec(
        narrative="她推门进来。",
        action_sequence=_action_beats(
            ("OPEN-DESC", ""),
            ("beat one", "noodle shop"),
            ("beat two", "noodle shop"),
            ("beat three", "rainy street"),
            ("beat four", "rainy street"),
            ("beat five", "rainy street"),
            ("CLOSE-DESC", "rainy street"),
        ),
    )
    seg = _segments_of(video_prompt.compile_firstlast_video_prompt(
        spec, duration_hint=4, first_frame_desc="门口", last_frame_desc="街头",
    ))
    # 4 秒 ⇒ 中段最多 2 拍；五拍动作流被合并，但内容一条不丢
    for frag in ("beat one", "beat two", "beat three", "beat four", "beat five"):
        assert frag in seg
    assert seg.count("s｜") == 4
    assert "场景切换：rainy street；" in seg


def test_批2_parse_video_plan_keeps_scene_and_drops_refusal():
    plan = video_prompt.parse_video_plan(
        '{"action_sequence":['
        '{"beat":"起点","desc":"she walks in","scene":"rainy street"},'
        '{"beat":"延伸","desc":"she sits","scene":"我不能协助这项请求。"}'
        ']}'
    )
    seq = plan["action_sequence"]
    assert seq[0]["scene"] == "rainy street"
    assert "scene" not in seq[1]


# ── 批 3（2026-11-08）：参考生成＝图片3+ 角色参考图 + 当下衣着/道具归属 ───────────
# 用户 94 刀原始规格：「图片 1／2 作为首尾帧图片上传，3、4、5… 后续的就是角色的参考图」
# ⇒ ② 必须把编号与角色对应关系写出来，否则模型拿到多张参考图却不知道哪张是谁。

def test_批3_role_refs_mirror_frontend_reference_plan():
    """后端编号必须与前端 `planFirstlastReferenceImages` 同口径（顺序/占位/去重）。"""
    refs = video_prompt.firstlast_role_refs(
        ["纪含章", "庄听澜", "邵允", "纪含章"],
        {"纪含章": "D:/base/ji.png", "邵允": "D:/base/shao.png"},
    )
    assert refs == ["纪含章", "邵允"]       # 庄听澜没有底图 ⇒ 不占位
    # 同一张底图只占一个槽（前端 seenSources 同口径，否则编号整体错位）
    assert video_prompt.firstlast_role_refs(
        ["甲", "乙"], {"甲": "same.png", "乙": "same.png"}) == ["甲"]
    assert video_prompt.firstlast_role_refs(["", " ", "丙"], {"丙": ""}) == []


def test_批3_firstlast_binds_role_reference_pictures():
    """② 声明图片3+/角色对应；参考图只锁身份，当下衣着与姿势以首尾帧为准。"""
    req = _firstlast_request(_spec(role_refs=["纪含章", "邵允"]), duration=12)
    prompt = req["submit"]["prompt"]
    binding = prompt.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "图片3=纪含章" in binding
    assert "图片4=邵允" in binding
    assert "角色参考图" in binding
    assert "当下衣着、姿势与所在场景以首尾帧画面为准" in binding
    assert req["reference_binding"]["图片3"].startswith("纪含章")
    assert req["reference_binding"]["图片4"].startswith("邵允")


def test_批3_firstlast_without_role_refs_has_no_picture3():
    """没有角色底图 ⇒ 一个编号都不多报（R2 不假装有图；旧稿逐字节不变）。"""
    req = _firstlast_request(_spec(), duration=12)
    assert "图片3" not in req["submit"]["prompt"]
    assert set(req["reference_binding"]) == {"图片1", "图片2"}


# ── 第 106 刀 · 模板能力是图片位/时长的天花板（实稿发现 1/2）─────────────────
# 实机视频模板（Ref2VA 0a891…）exposed 只有 `first_frame_image` / `video_prompt` /
# `video_duration default=10` ⇒ 无尾帧槽、无角色参考图槽、成片 243 帧≈10.125s。
# 提示词只能声明模板真能消费的图，拍界只能按真实成片时长排。

_REAL_CAPS = {"supports_last_frame": False, "reference_slots": 0, "duration": 10}


def test_第106刀_template_caps_limit_declared_images():
    """发现 1：模板无尾帧/参考图槽 ⇒ ②④ 与提交都只认图片1，不指挥模型看空槽。

    旧稿照写「图片2=尾帧、图片3=角色参考图」，但前端按 binding 消费、这些图从不上传
    ⇒ 模型被指挥去用不存在的图片。
    """
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(role_refs=["纪含章", "邵允"]),
        video_config={}, preset={}, first_frame="a.png", last_frame="b.png",
        caps=_REAL_CAPS,
    )
    prompt = req["submit"]["prompt"]
    binding = prompt.split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "图片1=" in binding
    assert "图片2=" not in binding                    # 尾帧槽不存在
    assert "图片3=" not in binding and "图片4=" not in binding
    assert "尾帧（无参考图，以文字为准）" in binding
    assert "以文字尾帧收尾" in _segments_of(prompt)    # ④ 按文字收束，不宣称图片2
    assert req["submit"]["images"] == ["a.png"]        # 尾帧地址不提交
    assert set(req["reference_binding"]) == {"图片1"}
    assert any("无尾帧槽" in w for w in req["warnings"])


def test_第106刀_template_caps_keep_slots_when_template_has_them():
    """反向锁：模板有尾帧槽 + 2 个参考图槽 ⇒ 图片2/3/4 照常声明，第 5 张不声明。"""
    caps = {"supports_last_frame": True, "reference_slots": 2, "duration": 0}
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(role_refs=["纪含章", "邵允", "庄听澜"]),
        video_config={}, preset={}, first_frame="a.png", last_frame="b.png",
        caps=caps,
    )
    binding = req["submit"]["prompt"].split("[参考绑定]：", 1)[1].split("\n\n", 1)[0]
    assert "图片2=" in binding
    assert "图片3=纪含章" in binding and "图片4=邵允" in binding
    assert "图片5=庄听澜" not in binding               # 槽数天花板
    assert req["submit"]["images"] == ["a.png", "b.png"]
    assert set(req["reference_binding"]) == {"图片1", "图片2", "图片3", "图片4"}


def test_第106刀_transition_caps_drop_second_image():
    """发现 1（转场同源）：模板无第二图槽 ⇒ 只挂上尾帧，终点按文字收束。"""
    req = video_prompt.build_video_request(
        mode="transition", spec=_spec(), video_config={},
        first_frame="prev.png", last_frame="cur.png",
        prev_tail_desc="上一楼层尾帧画面", first_frame_desc="当前楼层首帧画面",
        caps=_REAL_CAPS,
    )
    assert req["submit"]["images"] == ["prev.png"]
    assert set(req["reference_binding"]) == {"图片1"}
    prompt = req["submit"]["prompt"]
    assert "图片2=" not in prompt
    assert "以文字收束到当前楼层首帧" in prompt
    assert any("无第二图槽" in w for w in req["warnings"])


def test_第106刀_template_duration_default_drives_beats():
    """发现 2：模板默认 10s ⇒ 无用户时长时 ① 秒数 / ④ 拍界 / 提交时长三处同源。"""
    req = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(), video_config={}, preset={},
        caps=_REAL_CAPS,
    )
    prompt = req["submit"]["prompt"]
    meta = prompt.split("[元信息]：", 1)[1].split("\n\n", 1)[0]
    assert "10 seconds" in meta
    assert "15 seconds" not in meta                    # 不再写内置 15s 而模板只出 10s
    assert req["submit"]["duration"] == 10
    seg = _segments_of(prompt)
    assert "–10s｜" in seg                             # 收尾拍落在 10s 边界
    plan, dur, _stats = video_prompt._firstlast_beat_plan(
        _spec(), 10, "首帧/楼层开头画面", "尾帧/楼层结尾画面", "", True, True, None)
    assert dur == 10 and plan[-1][1] == 10

    # 用户显式时长仍最高优先：hint=6 ⇒ 6s，不被模板默认覆盖
    user = video_prompt.build_video_request(
        mode="firstlast", spec=_spec(), video_config={},
        preset={"videoDurationHint": 6}, caps=_REAL_CAPS,
    )
    assert user["submit"]["duration"] == 6
    assert "6 seconds" in user["submit"]["prompt"]


# ── 第 107 刀（2026-11-08）：拍窗口按台词需求加权 ──────────────────────────────
# 844 字实稿 3 段 × 15s：旧均分把 28 字台词所在的拍压到 2s（容量 9 字），
# `_fit_beat_quotes` 只能整句裁掉，9 句里只剩 6 句。加权后台词拍拿够 4.5 字/秒
# 的说话时间、其余拍保 2s 定格/动作下限 ⇒ 3 段全部 9/9。

def test_第107刀_firstlast_beat_windows_weight_dialogue_beats():
    """28 字台词同拍 ⇒ 该拍 7s、其余拍各 2s；三句台词全保留（15s 9/9 的同型样本）。"""
    spec = _spec(
        narrative=(
            "林雾忽然从口袋里摸出一张折了很多次的纸条，摊在桌上，低声说"
            "「这个地址，你认得吗」。沈迟擦手的动作停了一下，凑近看了一眼，说"
            "「认得，城南那条旧巷，前年就贴了拆迁告示」。她顿了顿，没有再"
            "多说什么。林雾把纸条收回去，说「明天我去一趟」。"
        ),
        camera="", motion=0,
        action_sequence=_action_beats(
            ("OPEN-DESC both women hold their positions in the dim shop", ""),
            ("Lin Wu unfolds a much-creased paper note", ""),
            ("Shen Chi leans in to read the address on the note", ""),
            ("CLOSE-DESC Lin Wu folds the note away and stands", ""),
        ),
    )
    plan, dur, stats = video_prompt._firstlast_beat_plan(
        spec, 15, "首帧/楼层开头画面", "尾帧/楼层结尾画面", "", True, True, None)
    assert dur == 15
    assert stats["quotes_total"] == 3
    assert stats["quotes_dropped"] == 0
    # 5 拍：开场 2s + 28 字拍 7s（ceil(28/4.5)=7）+ 两个空拍各 2s + 收尾 2s
    assert [tuple(beat[:2]) for beat in plan] == [
        (0, 2), (2, 9), (9, 11), (11, 13), (13, 15),
    ]
    pair_beat = next(content for _s, _e, _l, content, _x in plan
                     if "「这个地址，你认得吗」" in content)
    assert "「认得，城南那条旧巷，前年就贴了拆迁告示」" in pair_beat
    assert any("「明天我去一趟」" in content for _s, _e, _l, content, _x in plan)


def test_第107刀_firstlast_beat_windows_infeasible_falls_back_to_uniform():
    """加权后仍超过时长 ⇒ 逐字退回旧均分，并报「本段台词排进各拍至少需要约 Xs」。"""
    quotes = [f"第{i}句长台词" for i in range(8)]  # 48 字 ≈ 10.7s
    spec = _spec(
        narrative="".join(f"「{q}」" for q in quotes) + "。",
        camera="", motion=0,
        action_sequence=_action_beats(
            ("OPEN-DESC they sit around the counter", ""),
            ("She looks up and speaks", ""),
            ("CLOSE-DESC they finish", ""),
        ),
    )
    req = _firstlast_request(spec, duration=15)
    plan, _dur, stats = video_prompt._firstlast_beat_plan(
        spec, 15, "首帧/楼层开头画面", "尾帧/楼层结尾画面", "", True, True, None)
    assert [tuple(beat[:2]) for beat in plan] == [(0, 3), (3, 7), (7, 11), (11, 15)]
    assert stats["quotes_dropped"] == 5
    assert any(
        "至少需要约 17s" in w and "当前 15s" in w for w in req["warnings"]
    ), req["warnings"]


def test_第107刀_firstlast_beat_windows_no_dialogue_stay_uniform():
    """无台词 ⇒ 逐字维持旧均分（开场/收尾/中段等宽），不加权、不告警。"""
    spec = _spec(
        narrative="她推门进来。她坐下。",
        camera="", motion=0,
        action_sequence=_action_beats(
            ("OPEN-DESC enters", ""),
            ("She walks to the counter", ""),
            ("She sits down by the window", ""),
            ("CLOSE-DESC she looks out the window", ""),
        ),
    )
    plan, _dur, stats = video_prompt._firstlast_beat_plan(
        spec, 15, "首帧/楼层开头画面", "尾帧/楼层结尾画面", "", True, True, None)
    assert [tuple(beat[:2]) for beat in plan] == [
        (0, 3), (3, 6), (6, 9), (9, 12), (12, 15),
    ]
    assert stats["quotes_total"] == 0
    assert stats["quotes_dropped"] == 0
