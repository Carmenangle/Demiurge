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


def test_独立profile把连续高潮窗口完整映射回防拦截原文():
    text = (
        "<content>他的手@(扣)@住她的脖子。\n\n"
        "墙后的机关发出闷响。\n\n"
        "他重新@(推)@进去了。\n\n"
        "他@(掐)@着她的脖子继续动作，高潮袭来。</content>"
    )
    visible = si.illustration_scene_excerpt(text, "高潮袭来")

    protected = si.protected_illustration_scene_excerpt(text, visible)

    assert "@(扣)@住她的脖子" in protected
    assert "机关发出闷响" in protected
    assert "重新@(推)@进去了" in protected
    assert "@(掐)@着她的脖子" in protected


def test_无指定锚点时按高潮词选段而非整篇正文():
    text = "日常铺垫。\n\n她在决战关头拔剑冲向高台。\n\n众人返回营地。"

    assert si.illustration_scene_excerpt(text) == "她在决战关头拔剑冲向高台。"


def test_高潮重定向保留体位动作链而不是只取单个结果段():
    text = (
        "<content>她仰卧在冰冷石板上。\n\n"
        "他把她的双腿压上肩膀，两人保持面对面的姿势。\n\n"
        "他俯身继续动作，她的腰与臀部随着冲击抬起。\n\n"
        "高潮袭来，她的大腿绷直，手腕扯动锁链。\n\n"
        "那面虚构的旗仍然没有倒下。</content>"
    )

    resolved = si.resolve_illustration_anchor(text, "那面虚构的旗仍然没有倒下。")
    context = si.illustration_scene_excerpt(text, resolved)

    assert "高潮袭来" in resolved
    assert "仰卧在冰冷石板" in context
    assert "双腿压上肩膀" in context
    assert "面对面" in context
    assert "腰与臀部随着冲击抬起" in context
    assert "高潮袭来" in context


def test_高潮重定向保留被叙述桥段隔开的连续复合动作链():
    text = (
        "<content>他的手扣住她的脖子，把她压向石台。\n\n"
        "她的呼吸被迫变得短促，散乱的华袍残片垂在腰侧。\n\n"
        "墙后的机关发出一声闷响。\n\n"
        "他俯身贴近她。\n\n"
        "他在这个时候，重新推进去了。\n\n"
        "他一下一下掐着她的脖子继续动作，喉咙被卡住时高潮骤然袭来。\n\n"
        "她最后只剩下平稳的呼吸。</content>"
    )

    resolved = si.resolve_illustration_anchor(text, "她最后只剩下平稳的呼吸。")
    context = si.illustration_scene_excerpt(text, resolved)

    assert "扣住她的脖子" in context
    assert "华袍残片" in context
    assert "重新推进去了" in context
    assert "掐着她的脖子继续动作" in context


def test_结果句锚点保留本场景从姿态到反复动作再到开口的完整因果链():
    text = (
        "<content>他让她从侧卧换成仰卧，把她的膝盖分开。\n\n"
        "他取出原先放置的物件，改用手在接触点摩擦。\n\n"
        "每次她的身体追过去，他都在临界点撤开。\n\n"
        "这组动作反复多次，她的手腕扯动锁链。\n\n"
        "「上来。」\n\n"
        "说出来之后她立刻移开视线，嘴唇抿住。</content>"
    )
    requested = "说出来之后她立刻移开视线，嘴唇抿住。"

    resolved = si.resolve_illustration_anchor(text, requested)
    context = si.illustration_scene_excerpt(text, resolved)

    assert resolved == requested
    for fact in ("仰卧", "膝盖分开", "取出", "摩擦", "临界点撤开", "锁链", "上来"):
        assert fact in context


def test_时间跳转后的末尾揭示锚点不得被前一时段高潮覆盖():
    text = (
        "<content>他将玉碾安置妥当，锁上牢门离开。\n\n"
        "---\n\n"
        "第三天，她在黑暗里经历又一次高潮。\n\n"
        "---\n\n"
        "牢门开了。光从走廊透进来。\n\n"
        "她侧卧在石板上，双手仍被锁链拷在腰前，双腿弯曲分开，玉碾尚在。\n\n"
        "她缓慢转向门口，用暗红眼眸看向来人。</content>"
    )
    requested = "她缓慢转向门口，用暗红眼眸看向来人。"

    resolved = si.resolve_illustration_anchor(text, requested)
    context = si.illustration_scene_excerpt(text, resolved)

    assert resolved == requested
    assert "牢门开了" in context
    assert "双手仍被锁链拷在腰前" in context
    assert "双腿弯曲分开" in context
    assert "玉碾尚在" in context
    assert "第三天" not in context


def test_中文长横线时间切换同样保护末尾揭示锚点():
    text = (
        "<content>高潮始终没来。\n\n——\n\n第三天，他推开门。\n\n"
        "她侧卧在石板上，锁链限制着抬起的双手。\n\n"
        "她的手停在半空。</content>"
    )
    requested = "她的手停在半空。"

    assert si.resolve_illustration_anchor(text, requested) == requested


def test_插画槽位精确落在画面句末而不是下一段之后():
    text = "<content>画面发生在这里。\n\n后续段落。</content>"
    offset = si.illustration_anchor_offset(text, "画面发生在这里。")

    assert offset is not None
    assert text[:offset].endswith("画面发生在这里。")
    assert text[offset:].startswith("\n\n后续段落。")


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


def test_首帧锚点取正文第一段末():
    """首尾帧模式首帧应落在正文第一段后，而非高潮/末段（2026-08-29 验收问题①）。"""
    text = (
        "<content>\n\n她踏前一步，指尖挑起他的下颌。\n\n"
        "传功时真气如江河灌体，两人俱是动作激烈。\n\n"
        "最终尘埃落定，一切归于平静。\n\n</content>"
    )
    offset = si.first_frame_anchor_offset(text)
    before = text[:offset]
    assert "她踏前一步" in before
    assert "传功时真气" not in before  # 不越过第一段
    # 与主图的末段/高潮兜底明确区分
    assert offset < si.illustration_anchor_offset(text)


def test_首帧锚点对无content与think形态():
    text = "<think>复述<content>标签</think>\n\n第一段正文。\n\n<status>表格</status>"
    offset = si.first_frame_anchor_offset(text)
    assert text[:offset].find("第一段正文") != -1
    assert "<status>" not in text[:offset]
    # 单段正文：落句末
    assert si.first_frame_anchor_offset("<content>只有一段。</content>") > 0
