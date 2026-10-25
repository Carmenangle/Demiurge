"""剧情插画纯逻辑：触发优先级、跨档复用、SceneRequest 组装、renderer 注册表。"""
from __future__ import annotations

import pathlib

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
        actors=["奥瑞利亚"], reason=si.TRIGGER_CROSS_TIER, fmt=si.FMT_GPT_IMAGE)
    assert r.prompt == "她俯身贴近，银发红瞳，舞会大厅"  # 空 wardrobe 被过滤
    assert r.actors == ["奥瑞利亚"] and r.fmt == si.FMT_GPT_IMAGE


def test_全空段落prompt为空():
    assert si.build_scene_request(paragraph="  ", appearance="").prompt == ""


def test_无锚点时按视觉高潮评分选段而非恒取末段():
    """2026-09-01 用户实锤：无锚点时图插到末尾是巧合的必然（candidates[-1] 恒选末段，
    收束段通常就在末尾）。改约：无锚点 → fallback_illustration_anchor 视觉高潮评分选段，
    末尾收束段不得当选。"""
    text = "<think>思考</think>\n<content>\n铺垫段落。\n\n高潮段落。\n\n收束与余韵段落。\n</content>"
    offset = si.illustration_anchor_offset(text)

    assert text[:offset].endswith("高潮段落。")
    assert text[offset:].startswith("\n\n收束")
def test_没有content时锚点避开尾部状态块():
    text = "铺垫。\n\n高潮正文。\n\n<status>状态</status>"
    offset = si.illustration_anchor_offset(text)

    assert text[:offset].endswith("高潮正文。")
    assert text[offset:].startswith("\n\n<status>")


def test_无锚点且全文无视觉高潮词时退回首段而非末尾():
    text = "第一段闲谈。\n\n第二段闲谈。\n\n第三段闲谈。"
    offset = si.illustration_anchor_offset(text)

    assert offset < len(text)
    assert text[:offset].endswith("第一段闲谈。")
    assert text[offset:].startswith("\n\n第二段闲谈。")


def test_评分平分时选靠后段落而非开头():
    """2026-09-01 用户偏好：最高分段落落在末尾 20% 时优先选前置段落——评分平分
    且只有两段时，选前段（视觉高潮不该总落在收尾段）。"""
    anchor = si.fallback_illustration_anchor("她抬手。\n\n她回眸。")

    assert anchor == "她抬手。"

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


def test_指定锚点完全无效时按视觉高潮评分选段绝不返回None():
    """2026-09-01 用户实锤：锚点失效直接落正文末尾=图插在收束段。改约：锚点失效 →
    按视觉高潮评分选段（fallback_illustration_anchor），选不中也用首段，不再直接末尾。"""
    text = (
        "铺垫段。\n\n"
        "高潮动作。\n\n"
        "事后余韵与收束。"
    )

    offset = si.illustration_anchor_offset(text, "模型虚构且正文不存在的句子")

    assert offset is not None
    assert text[offset - 5:offset] == "高潮动作。"  # 评分选中的视觉高潮段，不是收束段
def test_首轮普通剧情兜底选择动作视觉段而不是结尾收束段():
    text = (
        "卡蜜莉亚终于笑了。她俯下身，猩红竖瞳与少年平视，"
        "抬起的手指悬在他的额前。\n\n"
        "她脚下的暗影忽然翻涌，吐出一只边角泛金的黑匣。\n\n"
        "院长站在门后，低声安排起余下的事务。"
    )

    anchor = si.fallback_illustration_anchor(text)
    offset = si.illustration_anchor_offset(text, anchor)

    assert anchor.startswith("卡蜜莉亚终于笑了")
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
        "顾凝霜在湿布擦过锁骨时骤然绷紧，汗水沿肩颈滑落，随后全身剧烈颤抖。\n\n"
        "我背着包裹沿山道离开。\n\n"
        "台下两个值夜弟子正在交班，远处红衣在雾中鲜艳。"
    )

    anchor = si.resolve_illustration_anchor(
        text, "台下两个值夜弟子正在交班，远处红衣在雾中鲜艳。",
    )

    assert anchor.startswith("顾凝霜在湿布擦过锁骨")


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


# ── 高潮结果帧豁免：颜射/内射完＝完成时（2026-09-24 用户定案）────────────────────
# 用户口径逐字：「颜射完不就是完成时」——体液已经落在角色身上那一刻**本身就是视觉高潮**，
# 既不是「静态收束」也不是「低强度结尾钩子」。真机（轮 `9cbce6c528cd`）：判据挑中了这一帧，
# 却被兜底段「亥时的第二记钟磬…把剑谱搁在案角…借窗纸光」（`_anchor_score`=7，全靠
# 「搁在」+5／「光」+2 两个**陈设词**）以 7 ≥ 0+3 推翻 ⇒ 交付稿从「满是白浊的脸＋还压在
# 她颊边蹭的肉棒」变成「她一个人侧卧、背对镜头、外半边让得更敞」。
# 修法＝给 `resolve_illustration_anchor` 的**两道**推翻闸各加一条同形豁免；两道收紧：
#   ① 兜底段**本身也是**高潮结果帧 ⇒ 不动（两帧同等，保留旧行为）；
#   ② `requested_excerpt` 仍需命中 ≥2 个动作语境词（与既有 `requested_is_action_outcome` 同形，
#      防「孤立体液词」凭一个名词就翻案）。
# ⚠ 不动 3 分门槛、不动 `_FALLBACK_ANCHOR_TERMS` 打分表（红线）。


def test_高潮结果帧词表是躯体事实表的子集():
    """`_CLIMAX_RESULT_TERMS` 只取体液名词族，且必须与 `_VISIBLE_BODY_TERMS` 同源（防两表漂移）。

    刻意**不收**「射在／射完」这类动词——它们会命中台词（见下一条用例）。
    """
    assert set(si._CLIMAX_RESULT_TERMS) <= set(si._VISIBLE_BODY_TERMS)
    assert "射在" not in si._CLIMAX_RESULT_TERMS
    assert "射完" not in si._CLIMAX_RESULT_TERMS


def test_高潮结果帧不被无体液的兜底段推翻():
    text = (
        "<content>她把剑谱搁在案角，借着窗纸透进来的光站了一会儿。\n\n"
        "他把她的双腿分开，动作放得极缓。她仰面躺在枕上，那股白浊顺着人中往下淌，"
        "滴进唇缝。</content>"
    )
    requested = (
        "他把她的双腿分开，动作放得极缓。她仰面躺在枕上，那股白浊顺着人中往下淌，滴进唇缝。"
    )
    # 前提锁：兜底段靠两个陈设词拿到 7 分，而判据句在打分表上**是 0 分**
    assert si.fallback_illustration_anchor(text) == "她把剑谱搁在案角，借着窗纸透进来的光站了一会儿。"
    assert si._anchor_score(requested) == 0
    assert si._anchor_score(si.fallback_illustration_anchor(text)) >= si._anchor_score(requested) + 3
    # 结论：有体液的收束帧**不得**被没有体液的段顶掉
    assert si.resolve_illustration_anchor(text, requested) == requested
    assert "白浊" in si.illustration_scene_excerpt(text, requested)


def test_兜底段本身也是高潮结果帧时保留旧行为():
    """两道收紧之①：两帧都是高潮结果帧时**不豁免**，仍走打分表（零翻转、不制造新分叉）。"""
    text = (
        "<content>他的手按住她的肩头，高潮袭来，精液顺着嘴角淌下来。\n\n"
        "她仰面躺在枕上，那股白浊混着口水从下颌角滑到脖颈。</content>"
    )
    fallback = "他的手按住她的肩头，高潮袭来，精液顺着嘴角淌下来。"
    requested = "她仰面躺在枕上，那股白浊混着口水从下颌角滑到脖颈。"

    assert si.fallback_illustration_anchor(text) == fallback
    assert si.resolve_illustration_anchor(text, requested) == fallback


def test_孤立体液词不带动作语境时不豁免():
    """两道收紧之②：锚点句只挑出一个体液名词、窗口拿不出动作语境 ⇒ 不算高潮结果帧，照旧被推翻。"""
    text = (
        "<content>她伸手把乌木匣子搁在长凳上。\n\n"
        "桌上的白浊已经干了一层。</content>"
    )
    requested = "桌上的白浊已经干了一层。"
    fallback = "她伸手把乌木匣子搁在长凳上。"

    assert si.fallback_illustration_anchor(text) == fallback
    assert sum(t in si.illustration_scene_excerpt(text, requested)
               for t in si._ACTION_CONTEXT_TERMS) < 2, "本用例的前提：窗口里没有动作语境"
    assert si.resolve_illustration_anchor(text, requested) == fallback


def test_台词里的射精措辞不得被当成高潮结果帧():
    """台词只有声音、没有画面——「射在」不在词表内，故含它的对白不得凭此翻案。"""
    text = (
        "<content>她抬起手把帛书搁在案角。\n\n"
        "「等他射在你里头的时候，会想我的。」</content>"
    )
    requested = "「等他射在你里头的时候，会想我的。」"

    assert si.fallback_illustration_anchor(text) == "她抬起手把帛书搁在案角。"
    assert si.resolve_illustration_anchor(text, requested) == "她抬起手把帛书搁在案角。"


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
        "<encounter>\n[WHO] 岑葛（伪装药材商贩）\n"
        "[WHERE] 边地孤儿院门口\n[MOOD] 风尘仆仆的爽朗热络\n</encounter>\n\n"
        "方脸，浓眉，宽肩厚背，褐色短褂袖口挽到肘弯，露出结实小臂和掌心厚茧，"
        "咧嘴一笑时露出小虎牙。\n\n"
        "岑葛把第一口药材木箱搬到门前石台上，招呼仍然不安的院长查看黄芪。\n"
        "</content>"
    )

    anchor, narrative, actors, facts = si.encounter_illustration_context(text)

    assert anchor == (
        "方脸，浓眉，宽肩厚背，褐色短褂袖口挽到肘弯，露出结实小臂和掌心厚茧，"
        "咧嘴一笑时露出小虎牙。"
    )
    assert actors == ["岑葛"]
    assert "骡子拉的平板车" in narrative
    assert "岑葛把第一口药材木箱搬到门前石台上" in narrative
    assert "内部规划" not in narrative
    assert facts == {
        "who": "岑葛（伪装药材商贩）",
        "where": "边地孤儿院门口",
        "mood": "风尘仆仆的爽朗热络",
    }


def test_残缺或无角色名的encounter不触发生图():
    assert si.encounter_illustration_context(
        "<content>庭院里风声渐紧。\n\n<encounter>[WHO] 岑葛"
    ) == ("", "", [], {})


def test_降级画幅按单人特写多人关系与横向动作变化():
    assert si.infer_aspect_ratio("她的面部特写占据画面中心。", ["甲"]) == "1:1"
    assert si.infer_aspect_ratio("甲与乙隔着长桌对峙。", ["甲", "乙"]) == "4:3"
    assert si.infer_aspect_ratio("她躺在长榻上，衣摆横向铺开。", ["甲"]) == "3:2"
    assert si.infer_aspect_ratio("她站在高台上，披风向上扬起。", ["甲"]) == "2:3"
    assert si.infer_aspect_ratio("她安静地坐在栏杆旁。", ["甲"]) == "3:4"


# ── 画幅判定：景别优先（2026-10-19 重构）────────────────────────────────
# 用户实锤：87 次提交里 79 次 4:3。根因是把「有两个人」当成「中景双人」——
# 但两人并肩站立该给竖幅、两人对峙于辽阔战场该给横幅，全被压成 4:3。
# 下列用例把「景别 > 空间 > 姿态 > 人数」的优先级钉住。


def test_画幅判定_两人并肩站立不再被压成4比3():
    """人数只是补充信号：有更强景别/空间证据时必须让位。"""
    # 两人 + 全身 → 竖幅（旧实现：4:3）
    assert si.infer_aspect_ratio("两人并排站着，全身入镜。", ["甲", "乙"]) == "9:16"
    # 两人 + 站立 → 2:3（旧实现：4:3）
    assert si.infer_aspect_ratio("甲与乙并肩站在门口。", ["甲", "乙"]) == "2:3"
    # 两人 + 辽阔战场 → 横幅（旧实现：4:3）
    assert si.infer_aspect_ratio("两人对峙于辽阔战场之上。", ["甲", "乙"]) == "16:9"


def test_画幅判定_景别优先于空间与姿态():
    # 面部特写压过「站立」与「群」的字面
    assert si.infer_aspect_ratio("她的面部特写，身后是群山。", ["甲"]) == "1:1"
    # 全景压过「两人」
    assert si.infer_aspect_ratio("远景中两人并肩而行。", ["甲", "乙"]) == "16:9"
    # 全身压过横卧姿态（展示优先）
    assert si.infer_aspect_ratio("全身展示，她横卧在长榻。", ["甲"]) == "9:16"


def test_画幅判定_空间词独立生效():
    assert si.infer_aspect_ratio("她独自走在空旷的街道上。", ["甲"]) == "16:9"
    assert si.infer_aspect_ratio("大厅的穹顶高悬。", ["甲"]) == "16:9"


def test_画幅判定_无信号时退回3比4():
    """无景别/空间/姿态/多人信号 ⇒ 保持旧默认 3:4（不引入行为漂移）。"""
    assert si.infer_aspect_ratio("她安静地坐着。", ["甲"]) == "3:4"
    assert si.infer_aspect_ratio("", []) == "3:4"
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
    """首尾帧模式首帧应落在正文第一段后，而非高潮/末段（2026-08-29 验收问题①）。

    主图锚点（illustration_anchor_offset 无 needle 时按视觉高潮评分选段）选中
    第二段「剧烈颤抖」；若主图退回首段（全文无视觉高潮词）则首帧与主图同位，
    该断言不成立——故文本必须含可评分的高潮段（2026-09-01 起主图不再恒取末段）。
    """
    text = (
        "<content>\n\n她踏前一步，指尖挑起他的下颌。\n\n"
        "传功时真气如江河灌体，两人俱是剧烈颤抖。\n\n"
        "最终尘埃落定，一切归于平静。\n\n</content>"
    )
    offset = si.first_frame_anchor_offset(text)
    before = text[:offset]
    assert "她踏前一步" in before
    assert "传功时真气" not in before  # 不越过第一段
    # 与主图的视觉高潮段兜底明确区分
    assert offset < si.illustration_anchor_offset(text)


def test_首帧锚点对无content与think形态():
    text = "<think>复述<content>标签</think>\n\n第一段正文。\n\n<status>表格</status>"
    offset = si.first_frame_anchor_offset(text)
    assert text[:offset].find("第一段正文") != -1
    assert "<status>" not in text[:offset]
    # 单段正文：落句末
    assert si.first_frame_anchor_offset("<content>只有一段。</content>") > 0


def test_首帧锚点偏移跳过前导think块():
    """2026-08-30 实锤：同轮剥离后模型不带 <content>，锚点从 0 起算会落进 think，
    前端按偏移切分消息时把 think 切开显示、图插进思考过程。"""
    reply = (
        "<think>思考草稿很长很长，包含各种推演内容</think>\n\n"
        "第一段正文，开场画面。\n\n第二段高潮正文。\n\n<status>\n[时间] 深夜\n</status>"
    )

    offset = si.first_frame_anchor_offset(reply)

    assert offset > reply.index("</think>")  # 不落在 think 内
    assert "第一段正文" in reply[:offset]  # 首帧锚在正文第一段末


def test_主图锚点偏移跳过前导think块():
    reply = (
        "<think>思考草稿</think>\n\n"
        "第一段正文。\n\n她猛地拔剑斩向祭坛，石阶崩裂。\n\n<status>x</status>"
    )

    offset = si.illustration_anchor_offset(reply, "她猛地拔剑斩向祭坛，石阶崩裂。")

    assert offset > reply.index("</think>")
    assert "拔剑" in reply[:offset]


def test_未闭合think吞全文时锚点无正文可落():
    reply = "<think>只有思考没有正文"

    assert si.first_frame_anchor_offset(reply) == len(reply)


def test_世界书锚失配时从重要角色表补外貌与穿着():
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征", "穿着打扮"],
               "rows": [["星织恋", "丰腴雌艳的绝色美母", "藕荷色束腰长裙"]]}]
    out = si.supplement_actor_table_appearance("", ["星织恋"], tables)
    assert "星织恋：" in out
    assert "【外貌】丰腴雌艳的绝色美母" in out
    assert "【穿着】藕荷色束腰长裙" in out


def test_表格里没有的角色不动且原有段落保留():
    # ⚠ 第 36 刀改了「两边都有」的语义（改为融合），本用例只覆盖「表格里没有该角色」
    # 那一支 ⇒ 断言不变；融合面见下面的第 36 刀用例。
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征"], "rows": [["星织恋", "美母"]]}]
    out = si.supplement_actor_table_appearance("谢若冰：冰肌玉骨", ["谢若冰", "星织恋"], tables)
    assert out.startswith("谢若冰：")
    assert "星织恋：" in out


def test_表格查无此角色时原样返回():
    assert si.supplement_actor_table_appearance("既有资料", ["路人甲"], []) == "既有资料"


def test_支持tables包裹形状并跳过无姓名列表():
    wrapped = {"tables": [
        {"name": "杂项", "columns": ["条目", "内容"], "rows": [["x", "y"]]},
        {"name": "重要角色表", "columns": ["姓名", "外貌特征"], "rows": [["星织恋", "美母"]]},
    ]}
    out = si.supplement_actor_table_appearance("", ["星织恋"], wrapped)
    assert "【外貌】美母" in out
    assert "【穿着】" not in out


# ── 第 36 刀：角色事实三源融合（用户 2026-09-21 定案「剧情表现 > 表格补充 > 角色条目」）──
# 原实现是「条目已点名该角色 ⇒ 表格整行跳过」（实为「条目 > 表格」）；现在改为**融合**：
# 表格分句并入同属性桶、条目在前表格在后（最新状态落在最后）、完全重复的不重写。


def _冰表格() -> list[dict]:
    return [{
        "name": "重要角色表",
        "columns": ["姓名", "外貌特征", "穿着打扮"],
        "rows": [[
            "谢若冰",
            "凤眸冷艳，耳根残红未褪；仙裙滑落半肩又拢好",
            "宗主仙裙半褪后重新拢好，肩头衣料微湿",
        ]],
    }]


def test_第36刀_条目与表格同时存在时融合而非整行跳过():
    entry = "谢若冰：【外貌】冷艳高华的绝世美妇，一头乌黑长发；【身材】媚软纤腰；【穿着】素雅华贵的宗主仙裙"
    out = si.supplement_actor_table_appearance(entry, ["谢若冰"], _冰表格())

    assert out != entry                       # 不再是「已点名就跳过」
    assert "一头乌黑长发" in out                 # 条目＝最初设定，保留
    assert "媚软纤腰" in out                     # 表里没有的属性桶（身材）原样保留
    assert "耳根残红未褪" in out                 # 表格＝上轮信息，并入
    assert "肩头衣料微湿" in out


def test_第36刀_条目在前表格在后即最新状态落在最后():
    entry = "谢若冰：【外貌】乌黑长发；【穿着】宗主仙裙"
    out = si.supplement_actor_table_appearance(entry, ["谢若冰"], _冰表格())

    assert out.index("乌黑长发") < out.index("耳根残红未褪")
    assert out.index("宗主仙裙") < out.index("宗主仙裙半褪后重新拢好")
    # 两个桶都留在原位，没有新起第二段
    assert out.count("谢若冰：") == 1
    assert "【身材】" not in out


def test_第36刀_表格分句与条目逐字重复时不重写():
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征"],
               "rows": [["谢若冰", "冰肌玉骨"]]}]
    entry = "谢若冰：【外貌】冰肌玉骨，乌黑长发"

    assert si.supplement_actor_table_appearance(entry, ["谢若冰"], tables) == entry


def test_第36刀_表格缺该属性桶时在块尾新起桶():
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征"],
               "rows": [["谢若冰", "凤眸冷艳"]]}]
    entry = "谢若冰：【穿着】宗主仙裙"
    out = si.supplement_actor_table_appearance(entry, ["谢若冰"], tables)

    assert "【外貌】凤眸冷艳" in out
    assert out.index("【穿着】") < out.index("【外貌】")


def test_第36刀_表格里没有该角色时逐字节不变():
    entry = "谢若冰：【外貌】冰肌玉骨"
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征"],
               "rows": [["星织恋", "美母"]]}]

    assert si.supplement_actor_table_appearance(entry, ["谢若冰"], tables) == entry


def test_第36刀_无表或空主体时逐字节不变():
    entry = "谢若冰：【外貌】冰肌玉骨"

    assert si.supplement_actor_table_appearance(entry, ["谢若冰"], []) == entry
    assert si.supplement_actor_table_appearance(entry, [], _冰表格()) == entry


def test_第36刀_条目没有该角色时仍按旧形态新起一行():
    out = si.supplement_actor_table_appearance(
        "谢若冰：冰肌玉骨", ["谢若冰", "星织恋"],
        [{"name": "重要角色表", "columns": ["姓名", "外貌特征"],
          "rows": [["星织恋", "美母"]]}],
    )

    assert out.startswith("谢若冰：冰肌玉骨\n")
    assert "星织恋：【外貌】美母" in out


def test_第36刀_诊断逐角色报外观事实来源():
    entry = "谢若冰：【外貌】冰肌玉骨\n星织恋：藕荷色长裙"
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征"],
               "rows": [["谢若冰", "耳根残红未褪"], ["路人甲", "魁梧"]]}]

    sources = si.actor_appearance_sources(
        entry, ["谢若冰", "星织恋", "丁", "路人甲"], tables,
    )

    assert sources["谢若冰"] == "merged"   # 条目＋表格都供了事实
    assert sources["星织恋"] == "entry"    # 只有条目
    assert sources["路人甲"] == "table"    # 只有表格（条目缺粮那一支）
    assert sources["丁"] == "none"        # 两边都没有
    assert si.actor_appearance_sources(entry, [], tables) == {}


# ── 第 36 刀补丁：**单槽位属性**（穿着）的互斥筛查 ────────────────────────────
# 用户 2026-09-21 当场点出：「剧情的兔女郎服饰和上个表格的婚纱两个 tag 可能同时存在，
# 即服装被你兼容了」⇒ 穿着＝同一时刻只有一套主装 ⇒ 必须先判「同物」再决定融合还是覆盖。


def test_第36刀_同一套服装的新状态仍融合():
    entry = "谢若冰：【穿着】素雅华贵的宗主仙裙配黑色蕾丝长丝袜与金绑带高跟"
    out = si.supplement_actor_table_appearance(entry, ["谢若冰"], _冰表格())

    assert "素雅华贵的宗主仙裙" in out          # 同物（共享「宗主仙裙」）⇒ 基础款保留
    assert "黑色蕾丝长丝袜" in out
    assert "宗主仙裙半褪后重新拢好" in out        # 当前状态并入
    assert "肩头衣料微湿" in out


def test_第36刀_两套互斥服装不并存按优先级覆盖():
    tables = [{"name": "重要角色表", "columns": ["姓名", "穿着打扮"],
               "rows": [["谢若冰", "曳地白色婚纱"]]}]
    entry = "谢若冰：【穿着】黑色兔女郎紧身衣、兔耳发箍"
    out = si.supplement_actor_table_appearance(entry, ["谢若冰"], tables)

    assert "婚纱" in out            # 表格＝上轮信息，优先级高于条目
    assert "兔女郎" not in out       # 互斥 ⇒ 旧的整套丢掉，**不并存**


def test_第36刀_穿着互斥只覆盖穿着桶不动可叠加属性():
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征", "穿着打扮"],
               "rows": [["谢若冰", "凤眸冷艳", "曳地白色婚纱"]]}]
    entry = "谢若冰：【外貌】冷艳高华的绝世美妇；【身材】媚软纤腰；【穿着】黑色兔女郎紧身衣"
    out = si.supplement_actor_table_appearance(entry, ["谢若冰"], tables)

    assert "冷艳高华的绝世美妇" in out and "媚软纤腰" in out   # 外貌／身材照旧融合
    assert "凤眸冷艳" in out
    assert "婚纱" in out and "兔女郎" not in out


def test_第36刀_表格空值占位不当事实并入():
    # 真机数据实测：陆莹纱的「外貌特征」「穿着打扮」两列字面写的就是「未提及」。
    tables = [{"name": "重要角色表", "columns": ["姓名", "外貌特征", "穿着打扮"],
               "rows": [["陆莹纱", "未提及", "未提及"]]}]
    entry = "陆莹纱：【外貌】红绸束发、粉雕玉琢"

    assert si.supplement_actor_table_appearance(entry, ["陆莹纱"], tables) == entry
    assert si.actor_appearance_sources(entry, ["陆莹纱"], tables) == {"陆莹纱": "entry"}


def test_第36刀_互斥覆盖保留桶内换行不粘连下一个标签():
    # 条目是**整卡正文**（【外貌】…\n【穿着】…\n【绑定世界书】…）；覆盖穿着桶时
    # 若吞掉桶内换行，会把下一个标签粘进穿着内容（真机实测到「未着衣【绑定世界书】」）。
    tables = [{"name": "重要角色表", "columns": ["姓名", "穿着打扮"],
               "rows": [["谢若冰", "灌顶施术中浸泡浮花泉，未着衣"]]}]
    entry = (
        "谢若冰：【外貌】冷艳高华\n"
        "【穿着】素雅华贵的宗主仙裙配黑色蕾丝长丝袜\n"
        "【绑定世界书】苍梧仙闻录"
    )
    out = si.supplement_actor_table_appearance(entry, ["谢若冰"], tables)

    assert "未着衣\n【绑定世界书】" in out
    assert "未着衣【绑定世界书】" not in out
    assert "黑色蕾丝长丝袜" not in out       # 互斥 ⇒ 旧的整套确实丢掉了


# ── 2026-09-18 三段真实文本走查回归（nsfw_test.txt / simple_test.txt）──────────────
# 走查实锤：锚点打分词表只认「射精」这类**结果**词，不认「捅到底 / 挺腰」等真实动作词
# ⇒ 高潮锚点落到射精收尾句；且片段扩展条件同样不覆盖「射精」⇒ 喂给提示词编译的剧情
# 真源只剩一句话（实测 26 字），提示词因此完全无从贴合剧情。


def _nsfw_single_fixture() -> str:
    """nsfw_test.txt 的结构精简：动作段在前、呻吟/对话零分段居中、射精收尾句在末。"""
    return "\n\n".join([
        "我把肉棒抵在她的臀沟上，轻轻地磨蹭着。",
        "我瞄准穴口，一口气捅到了底……！",
        "她的小穴紧紧裹着我的肉棒，一阵阵地蠕动收缩。",
        "噗啾……！噗啾呜！",
        "我紧贴上她饱满的臀部，压得几乎变形。",
        "我在她耳边轻声说道：这里是教室，叫小声点。",
        "噗叽噗叽噗叽噗叽♡",
        "我紧紧抓住她的臀部，用力挺腰！",
        "我连可爱清夏小穴里剩下的最后一滴都要榨干，继续射精。",
    ])


def test_锚点不落在纯结果句而忽略动作段():
    """旧打分把「射精」记 8 分、真实动作词（捅到了底/挺腰）全不在表 ⇒ 锚点选中收尾句。

    修复后应选中含真实动作的段落——插入那一刻才是画面要画的东西。
    """
    text = _nsfw_single_fixture()
    anchor = si.fallback_illustration_anchor(text)
    assert "射精" not in anchor
    assert "捅到了底" in anchor or "挺腰" in anchor


def test_射精类结果句锚点仍能取回前面动作链():
    """片段扩展条件只认「高潮/绝顶/痉挛/颤抖/临界」，不含「射精」⇒ 锚点落在结果句时
    片段退化成单段（实测 26 字）。修复后必须向前取回动作链。"""
    text = _nsfw_single_fixture()
    excerpt = si.illustration_scene_excerpt(
        text, "我连可爱清夏小穴里剩下的最后一滴都要榨干，继续射精。",
    )
    assert "捅到了底" in excerpt
    assert len(excerpt) > 100


def test_比喻性的消失不算状态变化动作():
    """走查实锤（simple_test.txt）：`_STATE_CHANGE_ACTION_TERMS` 含「消失」，把议论句
    「……不会因为一次拒绝就消失」当成动作 +5 分 ⇒ 锚点跑到开篇议论段（offset=72）。

    「消失」在本表里是唯一被大量用作**修辞**的词（关系消失 / 希望消失），应退出该表。
    """
    score = si._anchor_score(
        "青梅竹马这种关系，远比想象中坚韧。不会因为一次受伤就断绝，也不会因为一次拒绝就消失。",
    )
    assert score <= 0


# ── 判据档：输出解析 / evidence 校验 / 只读合同（2026-09-18）──────────────────
# 判据正文属主是 illustration_prompt_store（用户可改）；本节钉的是「程序怎么读」，
# 即字段名、宽容解析与逐字落文校验——这三样一旦被改，判定结果将无法被解析。

def test_判据解析容忍think前缀与代码围栏():
    raw = (
        "<think>先判断呈现度</think>\n```json\n"
        '{"anchor": "浑身无力的清夏靠在公告栏上。", "composition": "中景", '
        '"subjects": ["清夏"], "reason": "事后定型态"}\n```'
    )
    parsed = si.parse_anchor_intent(raw)
    assert parsed["anchor"] == "浑身无力的清夏靠在公告栏上。"
    assert parsed["composition"] == "中景"
    assert parsed["subjects"] == ["清夏"]


def test_判据解析容忍全角引号分隔符():
    """离线探针实锤：模型会用全角引号当 JSON 分隔符，原样 loads 必败——而那一轮模型其实选对了。"""
    raw = (
        "{“anchor”: “那么，我该插进哪一边呢？”, “composition”: “双人同框”, "
        "“subjects”: [“我”, “她”], “reason”: “二人同处一画面”}"
    )
    parsed = si.parse_anchor_intent(raw)
    assert parsed["anchor"] == "那么，我该插进哪一边呢？"
    assert parsed["subjects"] == ["我", "她"]


def test_判据解析混合引号走逐字段兜底():
    """两级 loads 都失败时（半角/全角混用）必须仍能抓出字段，不能整条判无效。"""
    raw = (
        '{"“anchor”: “塞蕾娜正微笑着。一副心满意足的模样。”, “composition”: “特写”, '
        '“subjects”: [“塞蕾娜”], “reason”: “表情已落在脸上”}'
    )
    parsed = si.parse_anchor_intent(raw)
    assert parsed["anchor"] == "塞蕾娜正微笑着。一副心满意足的模样。"
    assert parsed["reason"] == "表情已落在脸上"


def test_判据解析全字段缺失返回None():
    assert si.parse_anchor_intent("模型这次什么都没按格式说。") is None
    assert si.parse_anchor_intent("") is None


def test_判据anchor必须逐字落文():
    text = "前文。\n\n浑身无力的清夏靠在公告栏上。\n\n后文。"
    anchor, detail = si.resolve_anchor_intent(text, '{"anchor": "浑身无力的清夏靠在公告栏上。"}')
    assert anchor == "浑身无力的清夏靠在公告栏上。"
    assert detail["anchor"] == anchor
    bad, _ = si.resolve_anchor_intent(text, '{"anchor": "她心满意足地笑了。"}')
    assert bad == ""


def test_判据anchor归一化忽略空白与各类引号():
    text = "前文。\n\n“那么，我该插进哪一边呢？”\n\n后文。"
    anchor, _ = si.resolve_anchor_intent(text, '{"anchor": "那么,我该插进哪一边呢?"}')
    assert anchor == "那么,我该插进哪一边呢?"


def test_判据不做模糊匹配():
    """evidence 是硬校验：换个说法（不逐字）必须判无效——模糊匹配会让编造句通过。"""
    text = "前文。\n\n浑身无力的清夏靠在公告栏上。\n\n后文。"
    assert si.anchor_evidence_in_text(text, "清夏浑身无力地靠着公告栏") is False
    assert si.anchor_evidence_in_text(text, "") is False


def test_判据跨段锚点收敛到首段():
    """2026-09-18 三段固定文本实锤（simple_test）：模型给出跨段锚点时必须收敛到首段。

    跨段串能过 evidence（归一化剥空白）但过不了下游的逐字定位，会退化成评分兜底、
    把插入点拉到正文中部（实测 45% 处，真位置在 99.6% 处）——等于判据白判。
    """
    text = "前文。\n\n塞蕾娜正微笑着。\n\n一副心满意足的模样。"
    anchor, detail = si.resolve_anchor_intent(
        text, '{"anchor": "塞蕾娜正微笑着。\\n\\n一副心满意足的模样。"}',
    )
    assert anchor == "塞蕾娜正微笑着。"
    assert detail["anchor"] == "塞蕾娜正微笑着。"          # 明细给实际采用的锚点
    assert detail["anchor_raw"] == "塞蕾娜正微笑着。\n\n一副心满意足的模样。"
    # 收敛后必须逐字可定位（这正是原先栽掉的一步）
    assert si.illustration_anchor_offset(text, anchor) is not None


def test_判据单段锚点不带收敛痕迹():
    """单段锚点不得因收敛逻辑产生任何额外字段或改写。"""
    text = "前文。\n\n浑身无力的清夏靠在公告栏上。\n\n后文。"
    anchor, detail = si.resolve_anchor_intent(text, '{"anchor": "浑身无力的清夏靠在公告栏上。"}')
    assert anchor == "浑身无力的清夏靠在公告栏上。"
    assert "anchor_raw" not in detail


def test_只读合同拼在用户判据之后且字段名同源():
    system = si.anchor_intent_system("用户自己的判据")
    assert system.startswith("用户自己的判据")
    assert si.ANCHOR_INTENT_CONTRACT in system
    for field in si.ANCHOR_INTENT_FIELDS:
        assert field in si.ANCHOR_INTENT_CONTRACT


def test_空判据不产生system():
    assert si.anchor_intent_system("") == ""
    assert si.anchor_intent_system("   ") == ""


# ── 判据锚点「造词回退」：位置意图保留，锚点落回真实段落（2026-09-18 第六刀）──────
# 真机实证（生产模型 deepseek-v4-flash-0731，P 档 3 次采样）：模型**想对了、但写了原文没有的句子**。
#   ①「两人都用双手大大掰开了自己的臀缝，把小穴和后庭一同献了上来。」——原文无此句（自己编的）
#   ②「两人同时献上小穴和后庭…」——原文是「**两侧**同时献上小穴和后庭…」（改了主语）
# 逐字闸判无效 ⇒ 整轮退回中文词表档 ⇒ 落到靠后的**单人插入戏**（正是用户抱怨的现象）。
#
# 用户口径（2026-09-18）：**位置意图要保留**——模型表达的是"该插在哪附近"，那个意图是有效的；
# 锚点句本身不作为画面来源（画面始终来自取材窗口）。故：造词时用相似度把位置落回**真实段落**，
# 判据档不再整轮作废。**通用规则，不是 L3 特制**（任何场景、任何造词都走同一条路）。
#
# ⚠ 与 `anchor_evidence_in_text` 的分工：该函数仍是**审查函数**（是否逐字可查证），不删不改；
#   本刀新增的是 `resolve_anchor_intent` 在**校验不过**时的**回退**，且回退结果必须是原文段落
#   （逐字可定位），故不会放行幻觉锚点。


def test_造词锚点回退到最相似的真实段落():
    """锚点句原文不存在时，回退到正文里最相似的真实段落（位置意图保留）。

    合成切片按**真实分布**塑形：模型那句被改写的话（原句 vs 真人名）与目标段
    ratio≈0.73，而无关段落 ≤0.26（真机 L3 留样实测）。
    """
    text = (
        "他推开门走进房间，随手把外套搭在椅背上。\n\n"
        "塞蕾娜用双手大大掰开了自己的臀缝，把小穴和后庭一同献了上来。\n\n"
        "桌上的茶杯还冒着热气。"
    )
    # 模型原句与第 2 段高度相似但**逐字不存在**（原句「两侧」被改写成「两人」）
    raw = '{"anchor": "两人都用双手大大掰开了自己的臀缝，把小穴和后庭一同献了上来。", "composition": "双人同框", "subjects": ["甲", "乙"], "reason": "两人同框"}'
    anchor, detail = si.resolve_anchor_intent(text, raw)
    assert anchor == "塞蕾娜用双手大大掰开了自己的臀缝，把小穴和后庭一同献了上来。", (
        "造词必须回退到真实段落"
    )
    assert si.anchor_evidence_in_text(text, anchor) is True, "回退结果必须逐字可查证"
    assert detail.get("anchor_raw"), "必须保留模型原话给 Trace"
    assert detail.get("anchor_recovered") is True, "必须标记这是回退结果、不是模型原话"


def test_造词锚点不得凭空通过逐字闸():
    """回退的是**真实段落**，不是把幻觉句放行——审查函数结论不变。"""
    text = "他推开门走进房间。\n\n桌上的茶杯还冒着热气。"
    raw = '{"anchor": "两人都用双手大大掰开了自己的臀缝。", "composition": "双人同框", "subjects": ["甲"], "reason": "x"}'
    # 与任何段落都毫不相似 ⇒ 回退也拿不到可信位置 ⇒ 整轮作废（退回词表档）
    anchor, _ = si.resolve_anchor_intent(text, raw)
    assert anchor == ""


def test_逐字命中的锚点不走回退路径():
    """原句确实存在时，采用原句本身，不触发相似度回退。"""
    text = "他推开门走进房间。\n\n两人都用双手大大掰开了自己的臀缝。"
    raw = '{"anchor": "两人都用双手大大掰开了自己的臀缝。", "composition": "双人同框", "subjects": ["甲"], "reason": "x"}'
    anchor, detail = si.resolve_anchor_intent(text, raw)
    assert anchor == "两人都用双手大大掰开了自己的臀缝。"
    assert "anchor_raw" not in detail, "逐字命中不该留回退痕迹"


def test_回退也拿不到位置时才退回词表档():
    """回退是**尽力而为**：超出相似度阈值仍拿不到 ⇒ 空串（调用方走词表档）。"""
    text = "他推开门走进房间。"
    raw = '{"anchor": "完全无关的一串字。", "composition": "远景", "subjects": [], "reason": "x"}'
    anchor, _ = si.resolve_anchor_intent(text, raw)
    assert anchor == ""


def test_造词回退规则通用不依赖任何专有词():
    """通用性锁：回退只看**相似度**，与「掰开/臀」等任何场景词无关。"""
    text = (
        "甲把厨房的灯打开，开始切菜。\n\n"
        "乙走过去把窗户推开，让夜风吹进来。\n\n"
        "丙坐在沙发上翻着杂志。"
    )
    raw = '{"anchor": "乙走过去把窗户推开，让夜风吹了进来。", "composition": "中景", "subjects": ["乙"], "reason": "x"}'
    anchor, _ = si.resolve_anchor_intent(text, raw)
    assert anchor == "乙走过去把窗户推开，让夜风吹进来。", "非 NSFW、非多人场景也必须能回退"


# ── A′ 取材扩窗：锚点句不含主体名 ⇒ 向前取到场景边界（2026-09-18 拍板）────────────
# 真源：docs/illustration-window-handoff-2026-09-18.md §4.2 / §7.3 / §8。
# 三段真实语料不入库（含 NSFW 正文）⇒ 本节全部用**合成最小切片**（一句一段）锁行为。
#
# 规则（分支④ 内）：`subjects` 是本帧主体名集合；锚点句**不含其中任何名字** ⇒ 该句自身
# 不可能是画面（台词 / 内心独白 / 拟声），向前取到**场景边界窗口**；`subjects=None/[]`
# ⇒ 逐字维持旧行为（A′ 的零回归前提）。

_L3_LIKE_TEXT = "\n\n".join([
    "---",
    "明澜把双手贴到墙上，把重心压得很低。",
    "房间里只剩下压抑的呼吸声。",
    "清夏也把重心压低，贴住墙面上前。",
    "那么，我该选哪一边呢？",
])
_L3_LIKE_ANCHOR = "那么，我该选哪一边呢？"
_L3_LIKE_SUBJECTS = ["明澜", "清夏"]
_L3_LIKE_WINDOW = "\n\n".join([
    "明澜把双手贴到墙上，把重心压得很低。",
    "房间里只剩下压抑的呼吸声。",
    "清夏也把重心压低，贴住墙面上前。",
    "那么，我该选哪一边呢？",
])


def test_未传主体名时取材逐字保持旧行为():
    """零回归前提（§7.3 第 2 条）：`subjects=None` 与 `subjects=[]` 都必须原样返回锚点单句。"""
    old = si.illustration_scene_excerpt(_L3_LIKE_TEXT, _L3_LIKE_ANCHOR)

    assert old == _L3_LIKE_ANCHOR
    assert si.illustration_scene_excerpt(
        _L3_LIKE_TEXT, _L3_LIKE_ANCHOR, subjects=None,
    ) == old
    assert si.illustration_scene_excerpt(
        _L3_LIKE_TEXT, _L3_LIKE_ANCHOR, subjects=[],
    ) == old


def test_锚点句含主体名时维持单句():
    """L1/L2 锁（§7.3 第 7 条）：锚点句自身就是画面（含主体名），不得被扩窗稀释。

    ⚠ 本条**刻意只用 subjects 判定**、不引入 composition 信号。真机样本
    （`backend/_probe_anchor_chain_samples.json`）实测：L2 期望句「塞蕾娜正微笑着。」
    被判据自称「双人同框」3/3 次 ⇒ 任何「同框 ⇒ 扩窗」的规则都会把这条 8 字近景
    表情句拉到长窗口，与用户 L2 口径（近景特写、单句够用）直接冲突。
    """
    text = "\n\n".join([
        "---",
        "她把书摊开。",
        "塞蕾娜正微笑着。",
    ])

    assert si.illustration_scene_excerpt(
        text, "塞蕾娜正微笑着。", subjects=["罗瑟琳", "塞蕾娜"],
    ) == "塞蕾娜正微笑着。"


def test_锚点句不含主体名时向前取到场景边界窗口():
    """A′ 主路径：锚点句是内心提问（不含任何主体名）⇒ 取回场景边界后的整段画面。

    对应真机 L3 期望锚点「那么，我该插进哪一边呢？」——旧行为只给 12 字 / 0 角色，
    扩窗后两名主体的动作段同时在窗口内。
    """
    excerpt = si.illustration_scene_excerpt(
        _L3_LIKE_TEXT, _L3_LIKE_ANCHOR, subjects=_L3_LIKE_SUBJECTS,
    )

    assert excerpt == _L3_LIKE_WINDOW
    assert "明澜把双手贴到墙上" in excerpt      # 场景边界之后的第一段
    assert "清夏也把重心压低" in excerpt        # 另一名主体也在窗口内
    assert "---" not in excerpt                 # 边界段自身不进窗口
    assert excerpt.endswith(_L3_LIKE_ANCHOR)    # 锚点＝窗口收口位置


def test_词表档与判据档的主体名口径各锁一条():
    """§7.3 第 4 条：两档的 S 口径不同（判据档 = `subjects`；词表档 = `sig["present"]`）。

    口径差在「锚点句含的名字是否属于本帧主体」上显形：锚点句提到的角色若不在 `subjects`
    里仍算「不含主体名」⇒ 扩窗；若它属于 `present`（词表档更宽的集合）⇒ 维持单句。
    """
    text = "\n\n".join([
        "---",
        "明澜把双手贴到墙上。",
        "泰宇老师把重心压低。",
        "泰宇老师低声问了一句。",
    ])
    anchor = "泰宇老师低声问了一句。"

    judged = si.illustration_scene_excerpt(text, anchor, subjects=["明澜"])
    assert "明澜把双手贴到墙上" in judged
    assert "泰宇老师把重心压低" in judged

    keyword = si.illustration_scene_excerpt(
        text, anchor, subjects=["明澜", "泰宇老师"],
    )
    # 口径差：锚点句含主体名 ⇒ **A′ 不触发** ⇒ 不会把「明澜把双手贴到墙上」收进来
    # （第 15 刀起同主体向前补足会收进「泰宇老师把重心压低」，那是**同主体事实**，
    #  与本条要锁的「S 口径差」无关，故断言只看明澜那句在不在。）
    assert "明澜把双手贴到墙上" not in keyword
    assert anchor in keyword


def test_扩窗沿用生产四十段上限():
    """§7.3 第 1 条：扩窗必须沿用生产 `max(0, index-40)`。

    去掉上限会一路取到开篇（实测 L3 语料变 4460 字 / 段 1→235），窗口就不再是「场景」。
    """
    paragraphs = ["---"] + [f"第{i}段铺垫文字存在。" for i in range(1, 46)]
    paragraphs.append("那么，我该选哪一边呢？")
    text = "\n\n".join(paragraphs)

    excerpt = si.illustration_scene_excerpt(
        text, "那么，我该选哪一边呢？", subjects=["甲"],
    )

    assert "第6段铺垫文字存在。" in excerpt
    assert "第5段铺垫文字存在。" not in excerpt


def test_占位串识别不误伤叙事():
    assert si._is_non_narrative_placeholder("图片加载中...")
    assert si._is_non_narrative_placeholder("  图片加载中  ")
    assert si._is_non_narrative_placeholder("Loading...")
    assert not si._is_non_narrative_placeholder("她靠在公告栏上，浑身无力。")
    # 叙事里正常出现「图片加载」不构成占位串（必须整段就是占位串）
    assert not si._is_non_narrative_placeholder("他把图片加载进来，摊在桌上。")


def test_扩窗剔除UI占位串():
    """§8.3 机械闸：占位串会被扩窗带进窗口（真机实测段 222「图片加载中...」）。

    真机还更狠：L3 有一轮判据**直接把它选成锚点**（`_probe_anchor_chain_samples.json`
    第 8 行 anchor == "图片加载中..."）——所以它既不能进窗口，也不能充当锚点段。
    """
    text = "\n\n".join([
        "---",
        "明澜把双手贴到墙上，把重心压得很低。",
        "图片加载中...",
        "那么，我该选哪一边呢？",
    ])

    excerpt = si.illustration_scene_excerpt(
        text, _L3_LIKE_ANCHOR, subjects=_L3_LIKE_SUBJECTS,
    )

    assert "图片加载中" not in excerpt
    assert "明澜把双手贴到墙上" in excerpt


def test_占位串在既有高潮分支窗口内也被剔除():
    """占位串过滤与 A′ 无关：分支① 的既有扩窗同样不得把它带进窗口。"""
    text = "\n\n".join([
        "---",
        "明澜把双手贴到墙上。",
        "图片加载中...",
        "他猛然高潮袭来。",
    ])

    excerpt = si.illustration_scene_excerpt(text, "他猛然高潮袭来。")

    assert "图片加载中" not in excerpt
    assert "明澜把双手贴到墙上。" in excerpt


def test_占位串不得充当取材锚点段():
    text = "\n\n".join([
        "明澜把双手贴到墙上，把重心压得很低。",
        "图片加载中...",
    ])

    excerpt = si.illustration_scene_excerpt(text, "图片加载中...")

    assert "图片加载中" not in excerpt
    assert excerpt


def test_仲裁机内部取材调用不传主体名(monkeypatch):
    """实现约束（§7.3 第 3 条）：`resolve_illustration_anchor` 内部的取材调用**刻意不传**
    subjects ⇒ `requested_is_action_outcome` 的输入口径不变 ⇒ A′ 的二阶影响为零。

    这条不许"顺手统一"成传参：一旦传了，仲裁机的推翻判据就会随取材窗口一起漂移。
    """
    seen: list = []
    real = si.illustration_scene_excerpt

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(si, "illustration_scene_excerpt", spy)
    si.resolve_illustration_anchor("前文。\n\n她终于承认了。", "她终于承认了。")

    assert seen and all("subjects" not in kwargs for kwargs in seen)


# ── 取材层选址：相似度兜底（2026-09-18，第七刀）──────────────────────────────
# 链路：判据输出被改写 → 判据层回退成**真实段落** → 取材层拿这个真实段落去选址。
# 但下游还会把**模型改写原句**传来的情形（详见下列第二条）：没有相似度兜底就会
# 在取材层断链、退回末段，把图插到正文最后一段。

_CHAIN_TEXT = "\n\n".join([
    "他推开门走进房间，随手把外套搭在椅背上。",
    "塞蕾娜用双手大大掰开了自己的臀缝，把小穴和后庭一同献了上来。",
    "桌上的茶杯还冒着热气。",
])
_CHAIN_REAL_PARAGRAPH = "塞蕾娜用双手大大掰开了自己的臀缝，把小穴和后庭一同献了上来。"
_CHAIN_RAW_SENTENCE = "两人都用双手大大掰开了自己的臀缝，把小穴和后庭一同献了上来。"


def test_取材层用改写原句也能找到真实段落():
    """选址兜底：传进来的锚点是**模型改写句**（不是回退后的真实段落）时也必须命中。

    真机链路里「判据档回退」与「取材选址」拿到的可能是**同一串改写原文**（判据层
    把 `anchor` 落回真实段落，但 `anchor_raw` 才是调用方随后拼进请求的那串）。旧实现
    只有 `needle in paragraph` 逐字命中 + 无条件取「最相似」，前者必然落空；后者虽然
    能取对，但**没有阈值**，任意一串无关文字都会硬凑一个段。本条锁「有阈值且命中」。
    """
    excerpt = si.illustration_scene_excerpt(
        _CHAIN_TEXT, _CHAIN_RAW_SENTENCE, subjects=["塞蕾娜"],
    )

    assert _CHAIN_REAL_PARAGRAPH in excerpt, "改写句必须能兜到真实段落"
    assert "他推开门走进房间" not in excerpt
    assert "桌上的茶杯还冒着热气" not in excerpt


def test_取材层相似度不够时不当最相似硬凑():
    """选址兜底**有阈值**：整篇都不像 ⇒ 交词表档，不许把图插到一个没证据的段落。"""
    text = "\n\n".join([
        "他推开门走进房间，随手把外套搭在椅背上。",
        "塞蕾娜把书摊开，翻到夹着书签的那一页。",
        "桌上的茶杯还冒着热气。",
    ])

    excerpt = si.illustration_scene_excerpt(
        text, "完全无关的一串字，跟谁都不像。", subjects=["塞蕾娜"],
    )

    assert excerpt
    assert "塞蕾娜把书摊开" not in excerpt


def test_取材层选址阈值与判据层回退同源():
    """口径锁：两处必须共用同一个常量，否则会出现「判据层说找到了、取材层换一段」。"""
    assert si._ANCHOR_NEEDLE_MATCH_RATIO == 0.6

    src = pathlib.Path(si.__file__).read_text(encoding="utf-8")
    assert src.count("_ANCHOR_NEEDLE_MATCH_RATIO") >= 4, "两层都必须引用同一个常量"
    assert "_ANCHOR_INTENT_RECOVER_RATIO" not in src, "旧的一次性常量已被同源常量取代"


# ── 取材层窗口：「上下文事实增益」分支（2026-09-19，用户判定 L1 状态丢失）──────
# 缺口：`_visual_action_context` 的三条扩窗分支**都以「锚点句自身」为入口**（含高潮/结果词、
# 含动作词、不含主体名）。**定格结果句**（如「浑身无力的清夏靠在公告栏上。」）句内无动作、
# 无高潮/结果词，又含主体名 ⇒ 三条全不命中 ⇒ 窗口退化成锚点单句 ⇒ 前文承载的躯体状态
# （体液 / 裸露 / 身体部位）整体丢失。真机实测：窗口 14 字，而「我把肉棒抽了出来」
# 「满满含着的精液吧嗒嗒地滴落」「從小穴里吐出精液的样子」都在锚点前 1–4 段。
# 修法：向前回扫（与分支④同尺），**仅当窗口相对锚点句新增 `_VISIBLE_BODY_TERMS`** 时采用。

def test_定格结果句锚点补回前文被丢掉的躯体事实():
    """主路径：锚点句是定格结果句时，前文承载的躯体事实必须进窗口。"""
    text = "\n\n".join([
        "房间里很安静。",
        "精液顺着她的身体往下淌。",
        "她浑身无力地靠在墙上。",
    ])

    excerpt = si.illustration_scene_excerpt(
        text, "她浑身无力地靠在墙上。", subjects=["她"],
    )

    assert excerpt.endswith("她浑身无力地靠在墙上。")   # 锚点仍是窗口收口位置
    assert "精液" in excerpt                           # 被丢掉的躯体事实补回来了
    assert "房间里很安静" in excerpt                   # 连续取回，不做稀疏抽样


def test_定格句前文只有心理议论时维持单句():
    """零回归锁：回扫窗口**没有新增躯体事实**时必须逐字维持锚点单句。

    真机 L2 就是这个形态（前文是「或许，我一直如此孤独」这类心理旁白）——一旦放宽，
    会把前文里的其他角色名卷进窗口、引爆多人契约（该契约按在场人数判，见 §2.2）。
    """
    text = "\n\n".join([
        "或许，我一直如此孤独，并不全是我的错。",
        "这个念头让她感到无比厌恶，她再次捂住了嘴。",
        "塞蕾娜正微笑着。",
    ])

    assert si.illustration_scene_excerpt(
        text, "塞蕾娜正微笑着。", subjects=["罗瑟琳", "塞蕾娜"],
    ) == "塞蕾娜正微笑着。"


def test_准入判据是相对锚点句新增而非窗口内有躯体事实():
    """判据语义锁：必须是「**相对锚点句**新增」。锚点句自带该事实 ⇒ 无新增 ⇒ 维持单句。"""
    text = "\n\n".join([
        "他把外套搭在椅背上。",
        "桌上的茶杯还冒着热气。",
        "她赤裸着靠在墙上。",
    ])

    assert si.illustration_scene_excerpt(
        text, "她赤裸着靠在墙上。", subjects=["她"],
    ) == "她赤裸着靠在墙上。"


def test_锚点句不含主体名时仍走场景边界而非回扫窗口():
    """次序锁：A′（锚点句不是画面）优先于「上下文事实增益」。

    锚点句不含本帧任何主体名 ⇒ 必须取到**场景边界**，而不是只回扫 1–3 段；
    否则窗口起点会随邻居段落长短漂移，两个入口的取材口径就不一致了。
    """
    text = "\n\n".join([
        "---",
        "她把双手贴到墙上，把重心压得很低。",
        "精液顺着身体淌下。",
        "泰宇老师低声问了一句。",
        "那么，我该选哪一边呢？",
    ])

    excerpt = si.illustration_scene_excerpt(
        text, "那么，我该选哪一边呢？", subjects=["明澜"],
    )

    assert "她把双手贴到墙上" in excerpt     # 场景边界之后的第一段
    assert "泰宇老师低声问了一句" in excerpt


def test_动作链回扫两处共用同一把尺():
    """结构锁：分支④ 与「上下文事实增益」分支必须共用 `_action_context_scan_start`。

    两处若各写一份循环，窗口起点会在两条路径上漂移（与 `_scene_boundary_start` 的收口同理）。
    """
    src = pathlib.Path(si.__file__).read_text(encoding="utf-8")

    assert src.count("_action_context_scan_start(") >= 3, "定义 1 处 + 调用 2 处"
    assert src.count(
        "for previous_index in range(index - 1, max(-1, index - 13), -1)",
    ) == 1, "回扫循环只允许存一份"


def test_可见躯体事实表只服务上下文事实增益分支():
    """范围锁：躯体词表被**恰好两个**分支引用、方向相反，不得出现第三处顺手引用。

    ① 「上下文事实增益」：窗口相对锚点句**新增**躯体事实 ⇒ 采用（向前取材）；
    ② 「定格句后向补叙收编」：锚点段**自带**躯体事实 ⇒ 画面信息量足够 ⇒ 不收（低信息量
       限定，门禁实锤：缺了它 NSFW 动作帧会误收「桌上的茶杯还冒着热气。」这类环境静物）。
    """
    src = pathlib.Path(si.__file__).read_text(encoding="utf-8")

    assert src.count("_VISIBLE_BODY_TERMS = (") == 1, "单一定义"
    assert src.count(" in _VISIBLE_BODY_TERMS") == 2, "恰好两处使用：事实增益 1 + 后向收编 1"


# ── 取材层窗口：「定格句后向补叙收编」分支（2026-09-19，用户判定 L2 画面跑偏）──────
# 缺口：「上下文事实增益」分支只认 `_VISIBLE_BODY_TERMS`（躯体事实）⇒ SFW 定格句
# （「塞蕾娜正微笑着。」）前文没有躯体词、后文才有状态补叙时，窗口仍退化成锚点单句。
# 真机实锤（simple_test.txt 段 164–165）：锚点 8 字，紧随段「一副心满意足的模样。」
# 才是本主体的画面事实；而**前**邻域全是另一主体的心理/动作段（罗瑟琳段紧贴锚点），
# 任何向前回扫的连续窗口都必然卷入他人（零 LLM 普查 _l2_window_design.py）。
# 修法：事实增益未采用时，向后看**一段**——紧随段是同主体状态补叙（无动作/结局词、
# 非台词、非场景边界、≤40 字）才收编；只收一段（最小可预期，多段等真机证据再议）。

def test_定格句紧随的状态补叙段被收编进窗口():
    """主路径：锚点定格句 + 紧随状态补叙段 ⇒ 补叙进窗口、前驱他人段不进。"""
    text = "\n\n".join([
        "这个念头让她感到无比厌恶，她再次捂住了嘴。",
        "塞蕾娜正微笑着。",
        "一副心满意足的模样。",
    ])

    excerpt = si.illustration_scene_excerpt(
        text, "塞蕾娜正微笑着。", subjects=["塞蕾娜"],
    )

    assert "塞蕾娜正微笑着。" in excerpt          # 锚点仍在窗口内
    assert "一副心满意足的模样" in excerpt        # 后向补叙被收编（旧码红：窗口只有 8 字单句）
    assert "捂住了嘴" not in excerpt             # 前驱他人段不得卷入


def test_后向补叙段是台词时不收编():
    """边界锁（L1 真机形态）：紧随段是喘息台词 ⇒ 不收，逐字维持单句。"""
    text = "\n\n".join([
        "房间里只剩呼吸声。",
        "她浑身无力地靠在墙上。",
        "“哈啊…哈啊…哈…哈啊…。”",
    ])

    assert si.illustration_scene_excerpt(
        text, "她浑身无力地靠在墙上。", subjects=["她"],
    ) == "她浑身无力地靠在墙上。"


def test_后向补叙段开启新动作时不收编():
    """边界锁：紧随段是下一动作的开启段 ⇒ 不收（画面不能混入尚未发生的新动作）。"""
    text = "\n\n".join([
        "房间里只剩呼吸声。",
        "塞蕾娜正微笑着。",
        "塞蕾娜突然撑起身子转向门口。",
    ])

    assert si.illustration_scene_excerpt(
        text, "塞蕾娜正微笑着。", subjects=["塞蕾娜"],
    ) == "塞蕾娜正微笑着。"


def test_后向补叙段过长时不收编():
    """边界锁：补叙是**短**状态段（≤40 字）；长段更可能是新叙事单元而非补叙。"""
    text = "\n\n".join([
        "房间里只剩呼吸声。",
        "塞蕾娜正微笑着。",
        "她唇边的笑意一点点加深，眼睛弯成了月牙，"
        "仿佛所有压抑已久的得意都在这一刻尽数浮现了出来。",
    ])

    assert si.illustration_scene_excerpt(
        text, "塞蕾娜正微笑着。", subjects=["塞蕾娜"],
    ) == "塞蕾娜正微笑着。"


def test_后向补叙段是场景边界时不收编():
    """边界锁：紧随段是场景分隔 ⇒ 不收（分隔线之后是另一场景，不属于本帧）。"""
    text = "\n\n".join([
        "房间里只剩呼吸声。",
        "塞蕾娜正微笑着。",
        "---",
        "走廊尽头传来脚步声。",
    ])

    assert si.illustration_scene_excerpt(
        text, "塞蕾娜正微笑着。", subjects=["塞蕾娜"],
    ) == "塞蕾娜正微笑着。"


def test_词表档不加后向补叙():
    """零回归锁：`subjects=None`（词表档）时新分支绝不触发，逐字维持旧行为。"""
    text = "\n\n".join([
        "这个念头让她感到无比厌恶，她再次捂住了嘴。",
        "塞蕾娜正微笑着。",
        "一副心满意足的模样。",
    ])

    assert si.illustration_scene_excerpt(text, "塞蕾娜正微笑着。") == "塞蕾娜正微笑着。"


# ── 判据 subjects 机械后滤（人称口径的机械等价物，2026-09-19）──
# 用户定案：第一人称 ⇒ 叙事者「我」不占画面；一律按画面主体数判、不按角色表人数。
# 改判据 rubric 措辞已到天花板（v12/v13/v12.1 三轮实验）⇒ 走机械路径。三步固定顺序：
# ① 名字吸附（判据漏后缀/用简称时补全为角色表全名）② 滤字面「我」
# ③ 锚点句点名收敛（锚点段点名之外的主体出局；未点名则不动——真多人帧保留）。

def test_锚点未点名的角色被收敛出局():
    """L2 形态：特写帧 subjects 卷入他人 ⇒ 锚点句只点名塞蕾娜 ⇒ 罗瑟琳自动出局。"""
    subjects = si.filter_anchor_subjects(
        ["塞蕾娜", "罗瑟琳"], "塞蕾娜正微笑着。", ["塞蕾娜", "罗瑟琳"],
    )

    assert subjects == ["塞蕾娜"]


def test_叙事者不在锚点画面时被锚点收敛自动出局():
    """L1 形态：第一人称叙事者（泰宇老师）被判据列进 subjects，但锚点句只画清夏
    ⇒ 叙事者不占画面由锚点收敛机械达成，无需识别谁是「我」。"""
    subjects = si.filter_anchor_subjects(
        ["清夏", "泰宇老师"], "清夏趴在课桌上，肩膀微微发抖。", ["清夏", "泰宇老师"],
    )

    assert subjects == ["清夏"]


def test_锚点未点名时保留吸附补全的全名():
    """L3 形态：真多人帧锚点用代称不点名 ⇒ 不收敛（保留全部）；判据漏写的
    「老师」后缀由名字吸附补全（真机实锤：判据输出「泰宇」、角色表是「泰宇老师」）。"""
    subjects = si.filter_anchor_subjects(
        ["明澜", "泰宇"], "两人几乎同时到达了顶点。", ["明澜", "清夏", "泰宇老师"],
    )

    assert subjects == ["明澜", "泰宇老师"]


def test_字面我被滤除且不参与锚点收敛():
    """第一人称叙事者字面「我」不占画面；必须在收敛之前滤——否则锚点句里的
    「我」字会把 subjects 收敛成只剩「我」。"""
    subjects = si.filter_anchor_subjects(
        ["我", "清夏"], "我再也忍不住了。", ["清夏"],
    )

    assert subjects == ["清夏"]


def test_名字吸附歧义时不猜保留原样():
    """「泰宇」同时是「泰宇老师」「小泰宇」的子串 ⇒ 唯一性不成立 ⇒ 保留原样，
    宁可下游按子串匹配，也不吸附错人。"""
    subjects = si.filter_anchor_subjects(
        ["泰宇"], "两人对视着。", ["泰宇老师", "小泰宇"],
    )

    assert subjects == ["泰宇"]


# ── 画面人称（生图模式）显式化 + 叙事者/主角剔除收紧（2026-09-20 用户口径）──
# 用户实锤：「有时候因为有主角，导致被误判为了多人」⇒ 准备第一人称/第三人称两种生图模式，
# 以便排除主角名称被视为多人入场。

def test_叙事者别名集与角色状态表同源():
    """分层约定禁止 `scene_illustration` import `character_state` ⇒ 用等值锁防漂移。"""
    from app.services import character_state

    assert si._NARRATOR_ALIASES == character_state._PROTAGONIST_OWNERS


def test_第一人称视角下主角本名不占画面():
    """第一人称 ⇒ 叙事者不占画面；原先只滤**字面**「我」，主角写成**本名**就剔不掉。

    锚点句**不点名**任何角色（真机第 11 刀形态：拟声/心理句）⇒ 第③步收敛无从触发，
    剔除只能靠第②步。旧实现此时返回两人 ⇒ 多人契约误触发（用户实锤）。"""
    subjects = si.filter_anchor_subjects(
        ["甲", "清夏"], "我再也忍不住了。", ["甲", "清夏"], narrator="甲",
    )

    assert subjects == ["清夏"]


def test_第三人称视角下主角本名算一个主体():
    """第三人称 ⇒ 主人公算（用户 2026-09-19 定案）；只有第一人称才剔主角本名。

    锚点用第三人称代词（第三人称**正向证据**）、且不点名任何角色 ⇒ 收敛不介入，
    正好照出第②步**没有**多剔（防止收紧过头杀掉真双人帧）。"""
    subjects = si.filter_anchor_subjects(
        ["甲", "清夏"], "他们几乎同时到达了顶点。", ["甲", "清夏"], narrator="甲",
    )

    assert subjects == ["甲", "清夏"]


def test_叙事者别名全集被滤除():
    """真机实锤的误判形态：判据把「你/玩家/主角」当角色名写进 subjects ⇒ 被算成一个人。"""
    for alias in ("你", "玩家", "主角", "宿主", "用户"):
        assert si.filter_anchor_subjects(
            [alias, "清夏"], "两人几乎同时到达了顶点。", ["清夏"],
        ) == ["清夏"], alias


def test_帧人称只看锚点段不看全文():
    """第三人称帧的正文里也常出现对白「我」⇒ 拿全文判会把第三人称帧误判成第一人称。"""
    known = ["塞蕾娜"]
    assert si.frame_viewpoint("塞蕾娜正微笑着。", "我推开门，塞蕾娜正微笑着。", known=known) == "third_person"
    assert si.frame_viewpoint("我推开门。", "塞蕾娜正微笑着。", known=known) == "first_person"


def test_人称缺省参数逐字节等于旧行为():
    """零回归锁：不传 narrator 时，`narrator` 分支空转 ⇒ 与旧实现逐字节一致。"""
    subjects = si.filter_anchor_subjects(
        ["清夏", "泰宇老师"], "清夏趴在课桌上，肩膀微微发抖。", ["清夏", "泰宇老师"],
    )

    assert subjects == ["清夏"]


def test_自动判定无信号时默认第一人称():
    """2026-09-20 用户定案：自动判定**拿不到信号**（锚点段与正文首段都取不到）⇒ 默认第一人称。"""
    detail = si.frame_viewpoint_detail("", "")

    assert detail == {"viewpoint": "first_person", "auto": "first_person",
                      "source": "default", "conflict": False}


def test_手选优先且冲突留痕():
    """用户定案「手选优先 + 冲突留痕」：手选与自动不一致时采用手选，但必须记录冲突。"""
    detail = si.frame_viewpoint_detail("我推开门。", "", override="third_person")

    assert detail["viewpoint"] == "third_person"      # 手选优先
    assert detail["auto"] == "first_person"           # 自动判成第一人称
    assert detail["source"] == "override"
    assert detail["conflict"] is True


def test_手选与自动一致时不算冲突():
    detail = si.frame_viewpoint_detail("我推开门。", "", override="first_person")

    assert detail["viewpoint"] == "first_person"
    assert detail["source"] == "override"
    assert detail["conflict"] is False


def test_非法手选值退回自动判定():
    detail = si.frame_viewpoint_detail("塞蕾娜正微笑着。", "", override="bird_eye", known=["塞蕾娜"])

    assert detail["viewpoint"] == "third_person"
    assert detail["source"] == "anchor"


def test_第三人称正向证据才算第三人称_否则默认第一人称():
    """真机实锤（2026-09-20 第 14 刀）：只判「锚点段有没有『我』」会把第一人称叙事的帧判成
    第三人称——三份语料全是第一人称，但判据抽到的锚点是拟声词/心理句/触感句，句中都没有「我」。
    改成**有第三人称正向证据才算第三人称**（第三人称代词 / 角色表点名），否则无信号 ⇒ 默认第一人称。"""
    known = ["清夏", "泰宇老师"]

    # 拟声词锚点（L1 真机形态）：无任何人称信号 ⇒ 默认第一人称
    assert si.frame_viewpoint("啵叽叽叽……！！啵嗯…！", "我抓住清夏的手腕。", known=known) == "first_person"
    # 触感句锚点（L3 真机形态）：句中有「我」⇒ 第一人称
    assert si.frame_viewpoint("突然感觉两侧有人在拉我的胳膊。", "", known=known) == "first_person"
    # 角色表点名 ⇒ 第三人称正向证据
    assert si.frame_viewpoint("清夏趴在课桌上。", "", known=known) == "third_person"
    # 第三人称代词 ⇒ 第三人称正向证据
    assert si.frame_viewpoint("她推开门。", "", known=known) == "third_person"
    # 锚点段无信号 ⇒ 退回正文首段判
    assert si.frame_viewpoint("啵嗯…！", "她推开门。", known=known) == "third_person"
    assert si.frame_viewpoint("啵嗯…！", "我抓住清夏的手腕。", known=known) == "first_person"


def test_viewpoint_scope_单一真源接缝():
    """第 51 刀：viewpoint_scope 算一次、打包 mode/narrator/narrator_source/detail。

    下游 6 处消费点一律消费本结果，`filter_anchor_subjects` 不得再自判（修 D1）。"""
    scope = si.viewpoint_scope("我推开门。", ["清夏"], override="")
    assert scope.mode == "first_person"
    assert scope.narrator == ""
    assert scope.narrator_source == ""
    assert scope.detail["viewpoint"] == "first_person"

    # 手选优先 + 三段姓名（narrator/narrator_source 由上游算好传入）
    scope2 = si.viewpoint_scope(
        "她推开门。", ["清夏"], override="first_person",
        narrator="甲", narrator_source="user_name",
    )
    assert scope2.mode == "first_person"
    assert scope2.narrator == "甲"
    assert scope2.narrator_source == "user_name"
    assert scope2.detail["source"] == "override"


def test_空主体表原样透传():
    """零回归锁：词表档（subjects 为空）必须原样返回，绝不凭空造人。"""
    assert si.filter_anchor_subjects([], "塞蕾娜正微笑着。", ["塞蕾娜"]) == []


def test_手选人称同样作用于主体后滤():
    """手选人称（多元数据插入面板按钮组）必须与 `frame_viewpoint` 同口径地作用于后滤。

    否则会出现「场景下传 third_person、主体后滤却按自动判成 first_person 剔掉主人公」
    的自相矛盾：画面认定主人公算一个，主体表里却已经把他剔掉了。"""
    known = ["甲", "清夏"]
    anchor = "他们几乎同时到达了顶点。"      # 第三人称代词 ⇒ 自动判第三人称
    assert si.filter_anchor_subjects(["甲", "清夏"], anchor, known, narrator="甲") == ["甲", "清夏"]
    # 手选第一人称 ⇒ 主人公被剔（即使自动判成第三人称）
    assert si.filter_anchor_subjects(
        ["甲", "清夏"], anchor, known, narrator="甲", viewpoint="first_person",
    ) == ["清夏"]
    # 手选第三人称 ⇒ 主人公保留（即使锚点段是第一人称）
    assert si.filter_anchor_subjects(
        ["甲", "清夏"], "我再也忍不住了。", known, narrator="甲", viewpoint="third_person",
    ) == ["甲", "清夏"]


def test_手选人称缺省参数逐字节等于旧行为():
    """零回归锁：不传 override 时与旧实现逐字节一致（自动判定）。"""
    known = ["甲", "清夏"]
    assert si.filter_anchor_subjects(
        ["甲", "清夏"], "我再也忍不住了。", known, narrator="甲",
    ) == si.filter_anchor_subjects(
        ["甲", "清夏"], "我再也忍不住了。", known, narrator="甲", viewpoint="",
    ) == ["清夏"]


# ── 第 42 刀·B（2026-09-22 用户实锤「手选第一人称，主人公仍在画面内」）──
# 真机事故：判据 `subjects=["甲","乙"]`，其中「甲」是**用户人设名**（叙事者本名），而
# `narrator` 只从用户设置取 ⇒ 设置为空时第 37 刀那条「滤主角本名」空转 ⇒ 叙事者占掉一个
# 主体名额 ⇒ 多人契约误触发 ⇒ 模型按「两人」成稿 ⇒ 主人公入画、女主服装被绑到他身上。
# 收紧口径：画面主体的**身份真源＝已配置角色卡**，第一人称下 ∉ `known` 的名字一律不占画面。


def test_第一人称下非角色卡主体被剔出画面():
    """手选／自动判第一人称 ⇒ 判据 subjects 里 ∉ 已配置角色卡的名字不占画面主体。

    真机形态：`subjects=["甲","乙"]`、角色卡只有 `乙` ⇒ 只剩 `乙`（契约据此判单人帧）。"""
    known = ["乙", "丙"]
    anchor = "我再也忍不住了。"          # 第一人称 ⇒ 叙事者不占画面
    assert si.filter_anchor_subjects(
        ["甲", "乙"], anchor, known, viewpoint="first_person",
    ) == ["乙"]
    # 自动判定同样生效（锚点段第一人称，无需手选）
    assert si.filter_anchor_subjects(["甲", "乙"], anchor, known) == ["乙"]


def test_第一人称下非角色卡主体剔空则回退不剔():
    """剔空的边界：某帧画面里真的没有角色卡人物时，不得把主体数算成 0。

    宁可按原名单交给下游（可能多一个主体），也不能凭空清空——清空 ⇒ 契约退回 `actors`
    （在场全集），画面只会更宽。"""
    known = ["丙", "丁"]
    assert si.filter_anchor_subjects(
        ["甲", "乙"], "我再也忍不住了。", known, viewpoint="first_person",
    ) == ["甲", "乙"]


def test_第三人称下非角色卡主体不剔():
    """第三人称 ⇒ 主人公算一个主体（2026-09-19 用户定案）：本闸只在第一人称下生效。"""
    known = ["乙", "丙"]
    assert si.filter_anchor_subjects(
        ["甲", "乙"], "我再也忍不住了。", known, viewpoint="third_person",
    ) == ["甲", "乙"]


def test_非角色卡剔除在_known_为空时逐字节空转():
    """零回归锁：`known` 为空（无角色卡配置）⇒ 本闸不介入，与旧实现逐字节一致。"""
    assert si.filter_anchor_subjects(
        ["甲", "乙"], "我再也忍不住了。", [], viewpoint="first_person",
    ) == ["甲", "乙"]


def test_非角色卡剔除后锚点收敛仍照常生效():
    """两步叠加且**顺序不变**：B 先剔非角色卡，锚点句点名收敛再按剩余名单收敛。"""
    known = ["乙", "丙"]
    anchor = "乙趴在课桌上，肩膀微微发抖。"
    assert si.filter_anchor_subjects(
        ["甲", "乙", "丙"], anchor, known, viewpoint="first_person",
    ) == ["乙"]


def test_通用表性别列兜底():
    """世界书条目解析不到时，退回通用表（主角信息表 / 重要角色表）的「性别」列。"""
    tables = [
        {"columns": ["姓名", "性别", "外貌特征"], "rows": [["甲", "男", "高大"], ["乙", "女", "娇小"]]},
        {"columns": ["姓名", "外貌特征"], "rows": [["丙", "无性别列"]]},
    ]
    assert si.gender_from_tables(tables, "甲") == "男"
    assert si.gender_from_tables(tables, "乙") == "女"
    assert si.gender_from_tables(tables, "丙") == ""
    assert si.gender_from_tables(tables, "丁") == ""
    assert si.gender_from_tables([], "甲") == ""
    # 包装形态（repo 表返回 {"tables": [...]}）
    assert si.gender_from_tables({"tables": tables}, "乙") == "女"
    # 非「男/女」的自由文本原样透传，不改写语义
    assert si.gender_from_tables(
        [{"columns": ["姓名", "性别"], "rows": [["戊", "无性"]]}], "戊",
    ) == "无性"


def test_主人公性别注入三重短路():
    """只对主人公、只作画面事实：无性别 / 主人公不在本帧 / 已注入 ⇒ 逐字节不动。"""
    assert si.supplement_protagonist_gender("甲：高大", ["甲"], "甲", "") == "甲：高大"
    assert si.supplement_protagonist_gender("甲：高大", ["乙"], "甲", "男") == "甲：高大"
    assert si.supplement_protagonist_gender("甲：高大", ["甲"], "", "男") == "甲：高大"
    already = "甲：高大；【性别】男"
    assert si.supplement_protagonist_gender(already, ["甲"], "甲", "男") == already


def test_主人公性别注入落到本人那一行():
    """主人公已在外观段里 ⇒ 追加到**它那一行**（不另起一行、不动别人的行）。"""
    assert si.supplement_protagonist_gender(
        "甲：高大", ["甲"], "甲", "男",
    ) == "甲：高大；【性别】男"
    assert si.supplement_protagonist_gender(
        "乙：矮小", ["甲", "乙"], "甲", "男",
    ) == "乙：矮小\n甲：【性别】男"
    # 空外观段 ⇒ 只留主人公一行
    assert si.supplement_protagonist_gender(
        "", ["甲"], "甲", "女",
    ) == "甲：【性别】女"


def test_第37刀_第一人称不注入主人公性别():
    """第 37 刀（2026-09-21 用户实锤）：第一人称下主人公＝镜头本身、不占画面 ⇒ 性别事实不注入。

    真机现场：手选第一人称、画面主体 `2girls` 正确，却因注入了主人公性别而多写 `1boy`
    （违规）。⚠ 缺省 `viewpoint=""` ⇒ 逐字节等于旧行为（老调用方零回归）。
    """
    assert si.supplement_protagonist_gender(
        "甲：高大", ["甲"], "甲", "男", viewpoint="first_person",
    ) == "甲：高大"
    assert si.supplement_protagonist_gender(
        "甲：高大", ["甲"], "甲", "男", viewpoint="third_person",
    ) == "甲：高大；【性别】男"
    # 缺省口径不变（老调用方零回归）
    assert si.supplement_protagonist_gender(
        "甲：高大", ["甲"], "甲", "男",
    ) == "甲：高大；【性别】男"


# ══════════════════════════════════════════════════════════════════════════════
# 取材层窗口：「取最近时点」（2026-09-24 第 56 刀）
#
# 用户原话：「有时候你看上下文实际会发现**高潮点片段的那个位置有不同的时点**，
#   前一段描述**肉棒在腰部**，下一段就描述**插入嘴巴**了，而我的建议就是
#   **高潮点选在离它最近的一个时点为准，不要两个都出现**，
#   这样会生成**多个器官肢体冲突**」。
# 真机形态（`_tmp/probe_tp_ab.py` · turn `f926775566b0`）：窗口 36..42、锚点段 42，
# 段 39 是纯 `L`（只贴大腿外侧）＝更早的时点，段 40/41/42 是 `FL`／`F`／`FL` 的**上行链**
# ⇒ 只丢 39、保留 40..42。
# ══════════════════════════════════════════════════════════════════════════════

def test_第56刀_跨时点低带段被丢弃():
    """主路径：锚点在口部（F），前驱有一条只到腰/腿的**更早时点** ⇒ 必须截断。

    锚点句含动作词（`磨`）⇒ 走**动作链回扫**分支（真机投诉帧正是这条路径）。
    """
    text = "\n\n".join([
        "他侧躺过去，隔着裤料让那根硬热贴住她大腿外侧，先蹭。",   # L：更早时点
        "他把顶端抵上去，绕着小腹缓磨，布料塌出一个窝。",          # FL：上行（含动作词）
        "肉棒从她小腹一路往上拖，过了乳沟，从乳尖贴着擦过去。",     # FL：上行
        "它悬在她唇边，拇指压住下唇往下一分，抵住那两片软唇，缓磨。",  # FL：锚点
    ])

    excerpt = si.illustration_scene_excerpt(
        text, "它悬在她唇边，拇指压住下唇往下一分，抵住那两片软唇，缓磨。", subjects=["她"],
    )

    assert "抵住那两片软唇" in excerpt        # 锚点必在
    assert "过了乳沟" in excerpt              # 上行链保留（同一条动作链）
    assert "大腿外侧" not in excerpt          # 更早时点被丢弃


def test_第56刀_无接触证据时窗口逐字不变():
    """零回归锁：锚点段没有任何接触部位词 ⇒ 本机制**整体不介入**。"""
    text = "\n\n".join([
        "她把书放回架子上，转身去熄灯。",
        "她想了想，没说话，只是垂下眼。",
    ])

    assert si.illustration_scene_excerpt(
        text, "她想了想，没说话，只是垂下眼。", subjects=["她"],
    ) == "\n\n".join(text.split("\n\n"))


def test_第56刀_桥接段不触发截断():
    """边界锁：无接触部位词的段（`.`）是桥接段 ⇒ **保留**、且**不**中断上溯。

    真机门禁实锤：`start` 若不随 `.` 段同步推进，尾部桥接段会被丢掉
    （既有用例 `test_高潮重定向保留体位动作链而不是只取单个结果段` 首段
    「她仰卧在冰冷石板上。」＝**体位**事实，被误判成窗口外）。
    """
    text = "\n\n".join([
        "他把她放倒在石板上。",                        # . 体位/桥接
        "他把顶端抵上去，绕着她小腹与乳下缘缓磨。",     # U+L 并存
        "炭盆的火明了又暗。",                          # . 桥接
        "它悬在她唇边，压住下唇，缓磨。",              # U：锚点
    ])

    excerpt = si.illustration_scene_excerpt(
        text, "它悬在她唇边，压住下唇，缓磨。", subjects=["她"],
    )

    assert "绕着她小腹与乳下缘缓磨" in excerpt   # 桥接段没有把它截掉
    assert "炭盆的火明了又暗" in excerpt          # 中间桥接段保留
    assert "她放倒在石板上" in excerpt            # 尾部桥接段保留


def test_第56刀_体位与衣着段不算接触时点():
    """边界锁（真机门禁实锤的两例）：身体名词若只描述**衣着/体位** ⇒ 不得判成高度带。

    ·「散乱的华袍残片**垂在腰侧**」＝衣着位置；
    ·「双手仍被锁链**拷在腰前**，**双腿弯曲分开**」＝体位。
    若把它们当成「下半身时点」，既有的两条高潮重定向用例会当场翻红。
    """
    assert si._timepoint_band("散乱的华袍残片垂在腰侧。") == si._TP_BAND_NONE
    assert si._timepoint_band("她侧卧在石板上，双手仍被锁链拷在腰前，双腿弯曲分开。") == si._TP_BAND_NONE
    # 有接触动作 + 身体名词 ⇒ 才判带
    assert si._timepoint_band("他把它贴住她大腿外侧，先蹭。") == si._TP_BAND_LOWER
    assert si._timepoint_band("他把顶端抵住她下唇。") == si._TP_BAND_FACE
    # 有接触动作但无高度名词 ⇒ 高度未知 ⇒ 桥接（不截断）
    assert si._timepoint_band("他俯身贴近她。") == si._TP_BAND_NONE


def test_第56刀_接触闸不复用动作上下文词表():
    """结构锁：接触闸必须是**独立**表。

    `_ACTION_CONTEXT_TERMS` 含「转向/看向/分开/弯曲」等**非接触**姿态词，
    拿它当接触闸等于没闸（真机门禁：「双腿弯曲分开」那一段会被判成下半身时点）。
    """
    assert si._TP_CONTACT_RE is not si._ACTION_CONTEXT_TERMS
    assert "分开" not in si._TP_CONTACT_RE.pattern
    assert "弯曲" not in si._TP_CONTACT_RE.pattern
    assert "转向" not in si._TP_CONTACT_RE.pattern
    assert "看向" not in si._TP_CONTACT_RE.pattern


def test_第56刀_锚点为下半身时反向截断():
    """对称性锁：锚点带为 `L`（腰腹/下体）时，靠上的 `F` 段属**更早时点** ⇒ 同样截断。"""
    text = "\n\n".join([
        "他抵住她的唇，拇指压着下唇。",           # F：更早时点
        "他退下来，把肉棒贴住她的小腹，缓磨。",     # L：锚点所在时点
    ])

    excerpt = si.illustration_scene_excerpt(
        text, "他退下来，把肉棒贴住她的小腹，缓磨。", subjects=["她"],
    )

    assert "贴住她的小腹" in excerpt
    assert "压着下唇" not in excerpt


def test_第56刀_并存段不误判为更早时点():
    """结构锁：`?`（同段 F+L 并存）必须**同时**含 F 与 L。

    真机踩过：`anchor_band in band` 的字符串包含判定对 `?` 恒为假
    ⇒ 攀爬链 40/41 被判成更早时点、窗口塌成锚点单段（659 字 → 159 字）。
    """
    assert si._tp_has_band("他膝跪在被里，让它悬在她唇边。", si._TP_BAND_FACE)
    mixed = "他把它搁在她腰侧，又凑到她唇边。"
    assert si._timepoint_band(mixed) == si._TP_BAND_UNCLEAR
    assert si._tp_has_band(mixed, si._TP_BAND_FACE)
    assert si._tp_has_band(mixed, si._TP_BAND_LOWER)


def test_第56刀_姿势词不算接触点():
    """边界锁：「膝/跪/骑/坐」是姿势而非接触高度 ⇒ 不进带表。

    真机段 42「**膝跪在被里**不压她，让它悬在她**唇边**」＝接触点在唇 ⇒ 必须判成 F。
    """
    assert si._timepoint_band("他膝跪在被里，把它悬在她唇边。") == si._TP_BAND_FACE
    # 只有姿势、没有任何接触部位词 ⇒ 判「无带」（不得凭「跪/膝」造出一个高度带）
    assert si._timepoint_band("他跪坐着，什么也没碰。") == si._TP_BAND_NONE
    # 纯姿势 + 手肘 ⇒ 仍无带（腕/肘不是高度带表里的词）
    assert si._timepoint_band("他撑着手肘，侧过身去。") == si._TP_BAND_NONE


def test_第56刀_带判定不含单字面():
    """边界锁：带表刻意**不含**单字「面」——真机「布面」「案角压纸的镇石」会误命中。"""
    assert si._TP_FACE_RE.search("布面") is None
    assert si._TP_FACE_RE.search("两面") is None
    assert si._timepoint_band("他隔着中衣布面贴上去。") != si._TP_BAND_FACE


def test_第56刀_高频词陷阱不得进高度带表():
    """**本轮最贵的一条锁**：真机零 LLM 逐段复算（`turn 1339a9a2b586`）抓出的量词/常用词陷阱。

    这些词若进表，整条判据会失效——最严重的是**量词「一股」**被判成下半身带
    （「渗出**一股**雌香」），以及「两**人中**间」「**头**」这类高频噪声：
    当时实测 13 段里 11 段被判成「上下带并存（`?`）」，窗口该收的 2500 字**一个字都没收**。
    """
    # 量词 / 常用词（真机逐条命中过的原文）
    traps_none = [
        "她鼻息里便慢慢漫出一股雌香。",          # 股＝量词
        "两人中间那道被缝照旧留着。",            # 人中＝人/中间
        "剑诀讲到转腕那一节，腕子已经酸得发木。",  # 头/口 类噪声
        "门外脚步声响了。",                      # 脚＝脚步
        "满足于眼前的安静。",                    # 足＝满足
        "阴影里看不清脸色。",                    # 阴＝阴影
        "她心头一跳，抬起头来。",                # 头
    ]
    for trap in traps_none:
        # 无接触动作 ⇒ 一律判「非接触段」（不进入任何带）
        assert si._timepoint_band(trap) == si._TP_BAND_NONE, trap

    # 单字只留双字/限定写法：裸单字不得入表
    for bare in ("头", "口", "牙", "人中", "脚", "足", "阴", "股", "腹"):
        assert si._TP_FACE_RE.fullmatch(bare) is None, bare
        assert si._TP_LOWER_RE.fullmatch(bare) is None, bare

    # 限定写法照常命中（宁缺勿猜，但该认的认）
    assert si._TP_FACE_RE.search("嘴角") is not None
    assert si._TP_FACE_RE.search("口鼻") is not None
    assert si._TP_FACE_RE.search("牙龈") is not None
    assert si._TP_LOWER_RE.search("大腿") is not None
    assert si._TP_LOWER_RE.search("小腹") is not None
    assert si._TP_LOWER_RE.search("脚踝") is not None
    assert si._TP_LOWER_RE.search("阴部") is not None
    assert si._TP_LOWER_RE.search("股间") is not None


def test_第56刀_器官名不算高度带():
    """结构锁：器官（肉棒/阳具/龟头/柱身）是**器官**不是**高度** ⇒ 不进带表。

    真机踩过：把「肉棒」放进下半身带后，整条攀爬链几乎每段都「上下带并存」
    ⇒ 判据失效（窗口 2500 字零收窄）。器官的**位置**由同段的高度名词表达。
    """
    for organ in ("肉棒", "阳具", "龟头", "柱身"):
        assert si._TP_LOWER_RE.search(organ) is None, organ
        assert si._TP_FACE_RE.search(organ) is None, organ


# ══════════════════════════════════════════════════════════════════════════════
# 判据契约扩为「锚点数组 + score」（2026-09-24 第 57 刀 · 多批生图的地基）
#
# 用户口径：高潮点判断改为**打分式多批**——一次产出全部高潮点并各自打分；
#   出图数量 < 锚点数 ⇒ 按分从高到低取前 N，数量 ≥ 锚点数 ⇒ 全部出图。
# 本刀只落**契约与解析**（出图仍 1 张，取分最高者）；派发多事件是后续步骤。
# ⚠ 两条零回归锁：① 旧单条形态逐字维持；② `score` 缺失/非法记 0、不判死。
# ══════════════════════════════════════════════════════════════════════════════

def test_第57刀_新形态锚点数组按正文序解析():
    """主路径：`{"anchors":[…]}` 解析成列表，**保持判据给的正文顺序**（不重排）。"""
    raw = (
        '{"anchors": ['
        '{"anchor": "她仰面躺在枕上。", "composition": "特写", "subjects": ["乙"],'
        ' "reason": "事后定型", "score": 60},'
        '{"anchor": "他把顶端凑过去抵住那两片软唇。", "composition": "特写",'
        ' "subjects": ["乙"], "reason": "动作收束", "score": 90}'
        "]}"
    )

    items = si.parse_anchor_intents(raw)

    assert [item["anchor"] for item in items] == [
        "她仰面躺在枕上。", "他把顶端凑过去抵住那两片软唇。",
    ]
    assert [item["score"] for item in items] == [60, 90]      # 未按分数重排
    assert items[1]["subjects"] == ["乙"]


def test_第57刀_旧单条形态逐字维持旧行为():
    """**零回归锁**：老判据（在播的单锚点 rubric）仍按单条输出 ⇒ 单元素列表 + `score=0`。

    这是「用户手上那份判据不必改也能继续用」的保证（合同变了但解析兼容）。
    """
    raw = (
        '{"anchor": "浑身无力的清夏靠在公告栏上。", "composition": "中景", '
        '"subjects": ["清夏"], "reason": "事后定型态"}'
    )

    items = si.parse_anchor_intents(raw)

    assert len(items) == 1
    assert items[0]["anchor"] == "浑身无力的清夏靠在公告栏上。"
    assert items[0]["score"] == 0                              # 缺 score ⇒ 0，**不判死**
    # 单条兼容函数逐字等价
    assert si.parse_anchor_intent(raw)["anchor"] == "浑身无力的清夏靠在公告栏上。"


def test_第57刀_顶层裸数组也认():
    """容错锁：模型可能直接吐裸数组（不裹 `anchors` 键）⇒ 同样认。"""
    raw = '[{"anchor": "塞蕾娜正微笑着。", "score": "80"}, {"anchor": "她垂下眼。", "score": 10}]'

    items = si.parse_anchor_intents(raw)

    assert [item["anchor"] for item in items] == ["塞蕾娜正微笑着。", "她垂下眼。"]
    assert [item["score"] for item in items] == [80, 10]       # 字符串数字也收敛


def test_第57刀_score缺失或非数一律记0不判死():
    """边界锁：`score` 缺 / 空串 / 非数 / 布尔 ⇒ 0，**绝不因它整条判死**。"""
    for bad in ("null", '""', '"很高"', "true"):
        raw = f'{{"anchor": "她垂下眼。", "score": {bad}}}'
        items = si.parse_anchor_intents(raw)
        assert len(items) == 1, bad
        assert items[0]["score"] == 0, bad


def test_第57刀_数组里没给anchor的项被剔除():
    """边界锁：合同允许「某条 evidence 不过 ⇒ 只废该条」⇒ 无 anchor 的项必须剔。"""
    raw = (
        '{"anchors": [{"anchor": "", "score": 99}, '
        '{"anchor": "她垂下眼。", "score": 10}]}'
    )

    items = si.parse_anchor_intents(raw)

    assert [item["anchor"] for item in items] == ["她垂下眼。"]


def test_第57刀_空数组退回无锚点():
    """零回归锁：`{"anchors": []}` ⇒ 空列表 ⇒ 调用方退回中文词表档。"""
    assert si.parse_anchor_intents('{"anchors": []}') == []
    assert si.parse_anchor_intents("") == []
    assert si.parse_anchor_intents("模型这次什么都没按格式说。") == []
    assert si.parse_anchor_intent('{"anchors": []}') is None


def test_第57刀_单张出图取分最高且同分取正文更早():
    """口径锁（用户定案「数量 < 高潮点数 ⇒ 按分从高到低」）：

    出图数 = 1 时取分最高者；**同分取正文更早**（保序稳定，避免同分随机漂移）。
    """
    raw = (
        '{"anchors": ['
        '{"anchor": "第一处。", "score": 50},'
        '{"anchor": "第二处。", "score": 90},'
        '{"anchor": "第三处。", "score": 50}'
        "]}"
    )

    # 分最高 ⇒ 第二处
    assert si.parse_anchor_intent(raw)["anchor"] == "第二处。"
    # 同分（都把 90 拿掉）⇒ 取正文更早的「第一处」
    tie = raw.replace('"score": 90', '"score": 50')
    assert si.parse_anchor_intent(tie)["anchor"] == "第一处。"


def test_第57刀_取前N按分数降序但结果保持正文序():
    """口径锁：`select_anchor_intents` **只决定选哪几条**；返回顺序仍是**正文序**
    （图要跟着剧情位置摆，不是按分数摆）。"""
    items = [
        {"anchor": "A", "score": 10},
        {"anchor": "B", "score": 70},
        {"anchor": "C", "score": 40},
    ]

    assert [x["anchor"] for x in si.select_anchor_intents(items, 2)] == ["B", "C"]
    # 数量 ≥ 锚点数 ⇒ 全部（「上限不限制」的口径）
    assert [x["anchor"] for x in si.select_anchor_intents(items, 3)] == ["A", "B", "C"]
    assert [x["anchor"] for x in si.select_anchor_intents(items, 99)] == ["A", "B", "C"]
    assert si.select_anchor_intents([], 5) == []


def test_第57刀_合同同时声明数组与score():
    """合同锁：数组键、`score` 字段、以及「按正文先后排列 / 不要按分数排序」都必须写明。"""
    contract = si.ANCHOR_INTENT_CONTRACT

    assert si.ANCHOR_INTENTS_KEY in contract
    assert "score" in contract
    assert "先后" in contract and "不要" in contract
    # `ANCHOR_INTENT_FIELDS` 是**单条**字段集，逐字段都必须出现在合同正文里
    for field in si.ANCHOR_INTENT_FIELDS:
        assert field in contract


def test_第57刀_属性测试_多锚点解析永不抛异常():
    """稳健性锁：判据输出是**模型自由文本** ⇒ 任意输入都不得抛异常（失败退回词表档）。"""
    for raw in (
        "[]", "[1, 2, 3]", '{"anchors": "不是数组"}', '{"anchors": [1, "x"]}',
        '{"anchors": [{"anchor": 5}]}', '{"anchor": ["x"]}', "null", "{}",
        "```json\n{\"anchors\": []}\n```", "<think>想一下</think>\n{\"anchors\": []}",
    ):
        si.parse_anchor_intents(raw)          # 不抛即通过
        si.parse_anchor_intent(raw)


# ── 第 57 刀 · 多锚点去重（高度带 + 相邻段落，用户定案 2026-09-24）──────────────


def test_第57刀_去重_同带相邻两锚点合并留高分():
    """口径锁：**高度带相同**（复用 56 刀 `_timepoint_band`）**且相邻** ⇒ 同一处 ⇒ 合并，
    保留 `score` 更高者（用户 56 刀点名的「前一段在腰间、后一段插进嘴里」正属此列）。"""
    text = "他抵着她的唇角。\n他凑近她颈窝，鼻尖擦过。"
    items = [
        {"anchor": "他抵着她的唇角。", "score": 30},
        {"anchor": "他凑近她颈窝，鼻尖擦过。", "score": 80},
    ]

    merged = si.dedupe_anchor_intents(items, text)

    assert len(merged) == 1
    assert merged[0]["score"] == 80              # 高分那条留下
    assert merged[0]["anchor"] == "他凑近她颈窝，鼻尖擦过。"


def test_第57刀_去重_同带但远隔不合并():
    """口径锁：**只有「相邻段落」才算同一处**。同带但隔得远（这里是 4 段）⇒ 两处，
    ⛔ 不并（否则一次长剧情里两处独立的口交会被吞成一处）。"""
    text = "\n".join([
        "他贴着她的大腿外侧缓缓上移。",   # L
        "她仰着头，唇瓣微启。",           # F
        "他把手压在她肩上。",             # F
        "窗外雨声渐急。",                 # .
        "他碰到她膝弯内侧。",             # L
    ])
    items = [
        {"anchor": "他贴着她的大腿外侧缓缓上移。", "score": 90},
        {"anchor": "他碰到她膝弯内侧。", "score": 20},
    ]

    merged = si.dedupe_anchor_intents(items, text)

    assert len(merged) == 2
    assert [item["score"] for item in merged] == [90, 20]


def test_第57刀_去重_异带相邻不合并():
    """口径锁：带不同 ⇒ 本就是**两个时点**（口/面 vs 腰下）⇒ 不并。"""
    text = "他抵着她的唇角。\n他碰到她膝弯内侧。"
    items = [
        {"anchor": "他抵着她的唇角。", "score": 90},          # F
        {"anchor": "他碰到她膝弯内侧。", "score": 20},        # L
    ]

    assert len(si.dedupe_anchor_intents(items, text)) == 2


def test_第57刀_去重_无接触证据的锚点不参与合并():
    """保守锁：带为空（`.`／`?`，无接触证据）⇒ **不猜**，一律不并（宁多出一张，不并错一处）。"""
    text = "她仰着头望着天。\n她垂下眼，睫毛轻颤。"
    items = [
        {"anchor": "她仰着头望着天。", "score": 90},
        {"anchor": "她垂下眼，睫毛轻颤。", "score": 20},
    ]

    assert len(si.dedupe_anchor_intents(items, text)) == 2


def test_第57刀_去重_不给anchor或在原文找不到时不参与合并():
    """保守锁：`anchor` 为空 / 不逐字落文（模型造词）⇒ 段位无从定位 ⇒ 不并。"""
    text = "他抵着她的唇角。\n他凑近她颈窝，鼻尖擦过。"
    items = [
        {"anchor": "", "score": 90},
        {"anchor": "他凑近她颈窝，鼻尖擦过。", "score": 80},
    ]

    assert len(si.dedupe_anchor_intents(items, text)) == 2


def test_第57刀_去重_同一句在正文出现两次时定位有歧义不并():
    """保守锁：逐字命中的**段位有歧义**（同一句出现两次）⇒ 不并（并错一处比多出一张更贵）。"""
    text = "他抵着她的唇角。\n他抵着她的唇角。\n他凑近她颈窝，鼻尖擦过。"
    items = [
        {"anchor": "他抵着她的唇角。", "score": 90},
        {"anchor": "他凑近她颈窝，鼻尖擦过。", "score": 80},
    ]

    assert len(si.dedupe_anchor_intents(items, text)) == 2


def test_第57刀_去重_结果保持正文原序且同分保留更早的():
    """口径锁：返回**正文原序**（图跟着剧情位置摆）；同分时保留**正文更早**那条。"""
    text = "\n".join([
        "他抵着她的唇角。",           # 0  F
        "他凑近她颈窝，鼻尖擦过。",   # 1  F
        "她垂下眼。",                 # 2  .
    ])
    items = [
        {"anchor": "他抵着她的唇角。", "score": 50},
        {"anchor": "他凑近她颈窝，鼻尖擦过。", "score": 50},   # 同分
        {"anchor": "她垂下眼。", "score": 10},
    ]

    merged = si.dedupe_anchor_intents(items, text)

    assert [item["anchor"] for item in merged] == ["他抵着她的唇角。", "她垂下眼。"]


def test_第57刀_去重_空表与单条原样返回():
    """零回归锁：空表 ⇒ 空表；单条 ⇒ 原样（不触发任何定位/合并逻辑）。"""
    text = "他抵着她的唇角。"
    assert si.dedupe_anchor_intents([], text) == []
    one = [{"anchor": "他抵着她的唇角。", "score": 42}]
    assert si.dedupe_anchor_intents(one, text) == one


def test_第57刀_去重_属性测试_永不抛异常():
    """稳健性锁：正文/锚点都是模型自由文本 ⇒ 任意输入不得抛异常。"""
    for text, items in (
        ("", []),                          # 正文空
        ("只有一段。", [{"anchor": "只有一段。"}]),
        (None, [{"anchor": "她垂下眼。"}]),  # type: ignore[list-item] 故意传 None
        ("他抵着她的唇角。", [{"score": 1}, "不是字典", None]),
        ("他抵着她的唇角。", [{"anchor": None}, {"anchor": 5}]),
    ):
        si.dedupe_anchor_intents(items, text)   # type: ignore[arg-type]  不抛即通过


# ── 第 58 刀：插图插到**段落下方**，不切进段落中间（2026-09-24 用户 999 实测口径）──────
#
# 用户原话：「首先,插入到段落下方,不是段落中间」。锚点句常在段落中间，按句尾插入会把
# 一张图切进段落中间。吸附到段落边界（2+ 换行）后，图落在整段之下。


def test_第58刀_句尾偏移吸附到段落末尾():
    text = "第一段高潮。\n\n第二段，锚点句在中间。后续还有半句。\n\n第三段。"
    off = si.illustration_anchor_offset(text, "锚点句在中间。")
    snapped = si.snap_offset_to_paragraph_end(text, off)
    # 锚点句之后的**同段**内容必须仍在插入点之前（图落在整段之下）
    assert "后续还有半句。" in text[:snapped]
    # 插入点之后是段界或文本末尾
    assert text[snapped:].startswith("\n\n") or snapped == len(text)


def test_第58刀_none原样返回_越界钳制():
    text = "一段。\n\n二段。"
    assert si.snap_offset_to_paragraph_end(text, None) is None
    assert si.snap_offset_to_paragraph_end(text, -5) == 0
    assert si.snap_offset_to_paragraph_end(text, 999) == len(text)


def test_第58刀_锚点在末段时吸附到文本末尾():
    text = "第一段。\n\n末段里有锚点。之后没有了"
    off = si.illustration_anchor_offset(text, "末段里有锚点。")
    snapped = si.snap_offset_to_paragraph_end(text, off)
    assert snapped == len(text)
    assert "末段里有锚点。" in text[:snapped]


def test_第59刀_零分过滤落在本体之外_旧形态包装零回归():
    """用户定案（2026-09-25）：判据显式打 0 分＝「这个时刻不值得画」⇒ 不生成。
    ⚠ 但过滤落**多批选择层**（agent_agency）而非本函数——旧单锚点形态被
    `parse_anchor_intents` 有意包装成 `score=0`（零回归锁）⇒ 本体过滤会误杀
    单条路径（真机回归 11 连红实锤）。锁：本函数对 score=0 的条目照常返回。"""
    items = [
        {"anchor": "A", "score": 0},   # 旧形态包装出的 score=0
        {"anchor": "B", "score": 70},
    ]

    assert [x["anchor"] for x in si.select_anchor_intents(items, 0)] == ["A", "B"]
    assert [x["anchor"] for x in si.select_anchor_intents(items, 1)] == ["B"]
