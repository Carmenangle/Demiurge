"""剧情插画纯逻辑：触发优先级、跨档复用、SceneRequest 组装、renderer 注册表。"""
from __future__ import annotations

from app.services import scene_illustration as si

TH = [-30.0, 20.0, 55.0, 90.0, 100.0]


def test_显式优先级最高():
    t = si.decide_trigger(explicit=True, agency_lost=True, turn=6, cadence=3)
    assert t.fire and t.reason == si.TRIGGER_EXPLICIT


def test_失控高于跨档():
    t = si.decide_trigger(agency_lost=True, tier_before=19, tier_after=25, thresholds=TH)
    assert t.fire and t.reason == si.TRIGGER_AGENCY_LOST


def test_跨档触发复用agency():
    t = si.decide_trigger(tier_before=19, tier_after=25, thresholds=TH)
    assert t.fire and t.reason == si.TRIGGER_CROSS_TIER


def test_同档内不因跨档触发():
    t = si.decide_trigger(tier_before=21, tier_after=30, thresholds=TH, turn=2, cadence=3)
    assert t.fire is False and t.reason == ""


def test_每N段兜底():
    assert si.decide_trigger(turn=6, cadence=3).reason == si.TRIGGER_CADENCE
    assert si.decide_trigger(turn=5, cadence=3).fire is False
    assert si.decide_trigger(turn=0, cadence=3).fire is False  # 开局不配图


def test_场景nsfw高潮触发():
    assert si.decide_trigger(scene="nsfw").reason == si.TRIGGER_SCENE
    assert si.decide_trigger(scene="climax").reason == si.TRIGGER_SCENE
    assert si.decide_trigger(scene="dialogue").fire is False  # 普通场景不触发


def test_场景低于失控优先级():
    # 失控 > 场景：两者都命中时取失控
    assert si.decide_trigger(agency_lost=True, scene="nsfw").reason == si.TRIGGER_AGENCY_LOST


def test_新角色登场独立触发普通场景():
    trigger = si.decide_trigger(scene="dialogue", character_encounter=True)
    assert trigger.fire is True
    assert trigger.reason == si.TRIGGER_ENCOUNTER


def test_组装拼接顺序与非空过滤():
    r = si.build_scene_request(
        paragraph="她俯身贴近", appearance="银发红瞳", wardrobe="", locale="舞会大厅",
        actors=["埃斯托利亚"], reason=si.TRIGGER_CROSS_TIER, fmt=si.FMT_GPT_IMAGE)
    assert r.prompt == "她俯身贴近，银发红瞳，舞会大厅"  # 空 wardrobe 被过滤
    assert r.actors == ["埃斯托利亚"] and r.fmt == si.FMT_GPT_IMAGE


def test_全空段落prompt为空():
    assert si.build_scene_request(paragraph="  ", appearance="").prompt == ""


def test_插画锚点落在content最后一个正文段落末尾():
    text = "<think>思考</think>\n<content>\n铺垫段落。\n\n高潮段落。\n</content>\n<status>状态</status>"
    offset = si.illustration_anchor_offset(text)

    assert text[:offset].endswith("高潮段落。")
    assert text[offset:].startswith("\n</content>")


def test_没有content时锚点避开尾部状态块():
    text = "铺垫。\n\n高潮正文。\n\n<status>状态</status>"
    offset = si.illustration_anchor_offset(text)

    assert text[:offset].endswith("高潮正文。")
    assert text[offset:].startswith("\n\n<status>")


def test_插画锚点优先采用主生成指定原文():
    text = "第一段高潮。\n\n后续收束段。\n\n<status>状态</status>"
    offset = si.illustration_anchor_offset(text, "第一段高潮。")

    assert text[:offset].endswith("第一段高潮。")
    assert text[offset:].startswith("\n\n后续收束段。")


def test_插画锚点忽略正文破甲标记并映射回原偏移():
    text = (
        "<content>\n铺垫段落。\n\n"
        "浊白的@(精)@液@混着@(淫)@液@缓缓往外淌。\n\n"
        "后续收束段。\n</content>"
    )
    offset = si.illustration_anchor_offset(
        text, "浊白的精液混着淫液缓缓往外淌",
    )

    assert text[:offset].endswith("浊白的@(精)@液@混着@(淫)@液@缓缓往外淌。")
    assert text[offset:].startswith("\n\n后续收束段。")


def test_插画锚点轻微改写时匹配高潮段而非末尾收束段():
    text = "铺垫段。\n\n她跃上高台，披风在雷光中高高扬起。\n\n事后余韵与收束。"

    offset = si.illustration_anchor_offset(text, "披风在雷光中扬起")

    assert offset is not None
    assert text[:offset].endswith("她跃上高台，披风在雷光中高高扬起。")
    assert text[offset:].startswith("\n\n事后余韵")


def test_指定锚点完全无效时失败关闭而非回退消息末尾():
    text = "铺垫段。\n\n高潮动作。\n\n事后余韵与收束。"

    assert si.illustration_anchor_offset(text, "模型虚构且正文不存在的句子") is None


def test_首轮普通剧情兜底选择动作视觉段而不是结尾收束段():
    text = (
        "塞西莉亚终于笑了。她俯下身，猩红竖瞳与少年平视，"
        "抬起的手指悬在他的额前。\n\n"
        "她脚下的暗影忽然翻涌，吐出一只边角泛金的黑匣。\n\n"
        "院长站在门后，低声安排起余下的事务。"
    )

    anchor = si.fallback_illustration_anchor(text)
    offset = si.illustration_anchor_offset(text, anchor)

    assert anchor.startswith("塞西莉亚终于笑了")
    assert offset == text.index("\n\n")
    assert offset < len(text)


def test_兜底锚点优先选择改变剧情状态的信笺动作而非静态收束肖像():
    text = (
        "她抬手，以暗影在信笺上写下三条命令。\n\n"
        "她把信笺对折，信笺化作黑色流光穿出帷幔。\n\n"
        "她靠回椅背，嘴角弯起极浅弧度。\n\n"
        "不急。"
    )

    anchor = si.fallback_illustration_anchor(text)

    assert anchor == "她把信笺对折，信笺化作黑色流光穿出帷幔。"


def test_主计划误选结尾钩子时纠正为正文视觉高潮():
    text = (
        "冷倾雪在湿布擦过锁骨时骤然绷紧，汗水沿肩颈滑落，随后全身剧烈颤抖。\n\n"
        "我背着包裹沿山道离开。\n\n"
        "台下两个值夜弟子正在交班，远处红衣在雾中鲜艳。"
    )

    anchor = si.resolve_illustration_anchor(
        text, "台下两个值夜弟子正在交班，远处红衣在雾中鲜艳。",
    )

    assert anchor.startswith("冷倾雪在湿布擦过锁骨")


def test_兜底锚点只在content正文中选择并忽略think与残缺控制块():
    text = (
        "<think>规划高潮、俯身、凝视、光影与构图。</think>\n"
        "<content>日常铺垫。\n\n"
        "她俯身凝视着他，长发在午后的光影里垂落。\n\n"
        "她弯下腰，将乌木匣子搁在长凳上。\n\n"
        "院长从门后走出来询问情况。</content>\n"
        '<illustration>{"anchor":"院长询问情况","camera":"中近景"'
    )

    anchor = si.fallback_illustration_anchor(text)

    assert anchor == "她弯下腰，将乌木匣子搁在长凳上。"


def test_提示词场景源只截取锚点所在高潮段并还原破甲():
    text = "铺垫段。\n\n她猛然@(转)@身，披风在雷光里扬起。\n\n事后收束段。"

    excerpt = si.illustration_scene_excerpt(text, "披风在雷光里扬起")

    assert excerpt == "她猛然转身，披风在雷光里扬起。"


def test_独立profile场景源取同一高潮段但保留防拦截标记():
    text = "铺垫段。\n\n她猛然@(转)@身，披风在雷光里扬起。\n\n事后收束段。"
    visible = si.illustration_scene_excerpt(text, "披风在雷光里扬起")

    protected = si.protected_illustration_scene_excerpt(text, visible)

    assert protected == "她猛然@(转)@身，披风在雷光里扬起。"


def test_无指定锚点时按高潮词选段而非整篇正文():
    text = "日常铺垫。\n\n她在决战关头拔剑冲向高台。\n\n众人返回营地。"

    assert si.illustration_scene_excerpt(text) == "她在决战关头拔剑冲向高台。"


def test_新角色登场提取前一段外貌锚点与角色名():
    text = (
        "<think>关于<encounter>块的内部规划，不得被解析。</think>\n"
        "<content>骡子拉的平板车停在孤儿院门口，赶车人跳下来时带起一阵尘土。\n\n"
        "<encounter>\n[WHO] 方葛（伪装药材商贩）\n"
        "[WHERE] 边地孤儿院门口\n[MOOD] 风尘仆仆的爽朗热络\n</encounter>\n\n"
        "方脸，浓眉，宽肩厚背，褐色短褂袖口挽到肘弯，露出结实小臂和掌心厚茧，"
        "咧嘴一笑时露出小虎牙。\n\n"
        "方葛把第一口药材木箱搬到门前石台上，招呼仍然不安的院长查看黄芪。\n"
        "</content>"
    )

    anchor, narrative, actors, facts = si.encounter_illustration_context(text)

    assert anchor == (
        "方脸，浓眉，宽肩厚背，褐色短褂袖口挽到肘弯，露出结实小臂和掌心厚茧，"
        "咧嘴一笑时露出小虎牙。"
    )
    assert actors == ["方葛"]
    assert "骡子拉的平板车" in narrative
    assert "方葛把第一口药材木箱搬到门前石台上" in narrative
    assert "内部规划" not in narrative
    assert facts == {
        "who": "方葛（伪装药材商贩）",
        "where": "边地孤儿院门口",
        "mood": "风尘仆仆的爽朗热络",
    }


def test_残缺或无角色名的encounter不触发生图():
    assert si.encounter_illustration_context(
        "<content>庭院里风声渐紧。\n\n<encounter>[WHO] 方葛"
    ) == ("", "", [], {})


def test_降级画幅按单人特写多人关系与横向动作变化():
    assert si.infer_aspect_ratio("她的面部特写占据画面中心。", ["甲"]) == "1:1"
    assert si.infer_aspect_ratio("甲与乙隔着长桌对峙。", ["甲", "乙"]) == "4:3"
    assert si.infer_aspect_ratio("她躺在长榻上，衣摆横向铺开。", ["甲"]) == "3:2"
    assert si.infer_aspect_ratio("她站在高台上，披风向上扬起。", ["甲"]) == "2:3"
    assert si.infer_aspect_ratio("她安静地坐在栏杆旁。", ["甲"]) == "3:4"
    assert si.encounter_illustration_context(
        "<content>庭院里风声渐紧。\n\n<encounter>[WHAT] 有人来访</encounter></content>"
    ) == ("", "", [], {})


def test_renderer注册与查询():
    si.register_renderer("_test_fmt", lambda req: f"img://{req.prompt}")
    fn = si.get_renderer("_test_fmt")
    assert fn is not None and fn(si.SceneRequest(prompt="x")) == "img://x"
    assert "_test_fmt" in si.available_formats()
    assert si.get_renderer("_不存在") is None
