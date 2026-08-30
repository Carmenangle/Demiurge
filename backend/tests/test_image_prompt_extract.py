"""出图提示词提取纯逻辑：破甲还原 + JSON 解析 + booru 拼装。"""
from __future__ import annotations

from app.services import image_prompt_extract as ipe


def test_破甲还原_包裹式():
    # @(裸)@着 → 裸着；@(乳)@尖@(挺)@硬 → 乳尖挺硬
    assert ipe.restore_jailbreak("底下@(裸)@着的@(爆)@乳") == "底下裸着的爆乳"
    assert ipe.restore_jailbreak("@(乳)@尖@(挺)@硬") == "乳尖挺硬"


def test_破甲还原_裸at删除():
    assert ipe.restore_jailbreak("你@好") == "你好"


def test_破甲还原_空串():
    assert ipe.restore_jailbreak("") == ""
    assert ipe.restore_jailbreak("正常文字") == "正常文字"


def test_保护正文清洗控制块但保留防拦截标记():
    source = (
        "<think>隐藏</think><content>她@(抓)@住手腕。\n\n随后松开。</content>"
        "<状态更新>[]</状态更新>"
    )
    assert ipe.protected_narrative_text(source) == "她@(抓)@住手腕。\n\n随后松开。"
    assert ipe.visible_narrative_text(source) == "她抓住手腕。\n\n随后松开。"


def test_解析分析json():
    reply = ('随便前言{"composition":"close-up, pov","characters":["Lyra"],'
             '"action":"lying on bed, blush","lighting":"candlelight","nsfw_level":2}后语')
    out = ipe.parse_analysis(reply)
    assert out["composition"] == "close-up, pov"
    assert out["characters"] == ["Lyra"]
    assert out["action"] == "lying on bed, blush"
    assert out["lighting"] == "candlelight"
    assert out["nsfw_level"] == 2


def test_解析_nsfw_level_clamp():
    assert ipe.parse_analysis('{"nsfw_level":9}')["nsfw_level"] == 3
    assert ipe.parse_analysis('{"nsfw_level":-5}')["nsfw_level"] == 0


def test_解析_motion_信号():
    assert ipe.parse_analysis('{"motion":2}')["motion"] == 2
    assert ipe.parse_analysis('{"motion":9}')["motion"] == 3  # clamp
    assert "motion" not in ipe.parse_analysis('{"composition":"pov"}')  # 缺省不填


def test_本地动作强度不调用模型():
    assert ipe.infer_motion("她在长廊高速追逐") == 3
    assert ipe.infer_motion("她转身走向门口") == 2
    assert ipe.infer_motion("她眨眼微笑") == 1
    assert ipe.infer_motion("她站在窗边") == 0


def test_解析失败返回空():
    assert ipe.parse_analysis("模型拒答，没有 JSON") == {}
    assert ipe.parse_analysis("") == {}


def test_拼装固定顺序_复用锚点():
    analysis = {"composition": "close-up", "action": "arching back", "lighting": "dim light"}
    out = ipe.assemble_prompt(
        analysis, appearance="1girl, silver hair", wardrobe="white dress",
        locale="bedroom", quality="masterpiece", intensity_tags="explicit")
    assert out == ("masterpiece, close-up, 1girl, silver hair, arching back, "
                   "white dress, bedroom, dim light, explicit")


def test_拼装_全空返回空():
    assert ipe.assemble_prompt({}) == ""
    assert ipe.assemble_prompt({}, natural=True) == ""


def test_拼装_自然语言分支_丢质量咒用句号连接():
    analysis = {"composition": "close-up shot", "action": "she arches her back", "lighting": "dim warm light"}
    out = ipe.assemble_prompt(
        analysis, appearance="a girl with silver hair", wardrobe="wearing a white dress",
        locale="in a bedroom", quality="masterpiece", intensity_tags="explicit", natural=True)
    # 自然语言：无质量咒/强度词，字段间用「. 」连接
    assert out == ("close-up shot. a girl with silver hair. she arches her back. "
                   "wearing a white dress. in a bedroom. dim warm light")
    assert "masterpiece" not in out and "explicit" not in out


def test_build_extract_system_含还原指令():
    s = ipe.build_extract_system()
    assert "还原" in s and "JSON" in s


def test_主生成插画计划可解析并从正文剥离():
    reply = (
        "铺垫。\n\n她跃上高台，披风在雷光中扬起。"
        '<illustration>{"anchor":"披风在雷光中扬起。",'
        '"composition":"low angle, triangular composition",'
        '"camera":"35mm medium shot",'
        '"aspect_ratio":"2:3",'
        '"subjects":[{"name":"白绮谷","weight":1.35,"description":"silver-haired swordswoman"}],'
        '"prompt":"lightning, ruined hall","motion":2}</illustration>'
    )

    clean, plan = ipe.extract_illustration_plan(reply)

    assert clean == "铺垫。\n\n她跃上高台，披风在雷光中扬起。"
    assert plan["anchor"] == "披风在雷光中扬起。"
    assert plan["actors"] == ["白绮谷"]
    assert plan["motion"] == 2
    assert plan["aspect_ratio"] == "2:3"
    assert plan["camera"] == "35mm medium shot"
    assert plan["composition"] == "low angle, triangular composition"
    assert plan["prompt"] == (
        "35mm medium shot, low angle, triangular composition, "
        "(silver-haired swordswoman:1.35), lightning, ruined hall"
    )


def test_模型截断未闭合插画块时仍从正文剥离():
    reply = (
        "<content>铺垫。\n\n她把乌木匣子放在长凳上。\n\n院长走出门来。</content>"
        '<illustration>{"anchor":"院长走出门来。","camera":"中近景",'
        '"visual_thesis":"乌木匣子吞噬午后光线","aspect_'
    )

    clean, plan = ipe.extract_illustration_plan(reply)

    assert clean == "<content>铺垫。\n\n她把乌木匣子放在长凳上。\n\n院长走出门来。</content>"
    assert plan == {}
    assert "anchor" not in clean and "visual_thesis" not in clean


def test_插画JSON被模型续写think打断时可恢复():
    reply = (
        "<content>正文高潮。</content>"
        '<illustration>{"anchor":"正文高潮。","camera":"low-angle medium close-up",'
        '"composition":"diagonal composition","aspect_ratio":"2:3",'
        '"subjects":[{"name":"虞妙玥","description":"voluptuous mature woman, '
        'ink-black hair, narrow dark-red eyes","weight":2.0}],'
        '"prompt":"lying on cold stone, amber side light",'
        '"profile_prompt":"The stone floor shows moisture'
        '<think>上一段输出被截断，需要继续完成 illustration JSON。</think>'
        ' and fluid stains.","motion":1}</illustration>'
    )

    clean, plan = ipe.extract_illustration_plan(reply)

    assert clean == "<content>正文高潮。</content>"
    assert plan["actors"] == ["虞妙玥"]
    assert plan["profile_prompt"] == "The stone floor shows moisture and fluid stains."
    assert "dark-red eyes" in plan["prompt"]


def test_插画JSON字符串含未转义换行时只修复字符串控制符并保留计划():
    reply = (
        "<content>她跃上高台，披风在雷光中扬起。</content>"
        '<illustration>{"anchor":"她跃上高台，披风在雷光中扬起。",'
        '"camera":"low angle","composition":"diagonal composition",'
        '"subjects":[{"name":"白绮谷","description":"silver-haired swordswoman jumping"}],'
        '"prompt":"jumping, flowing cape, lightning",'
        '"profile_prompt":"silver-haired swordswoman, jumping, flowing cape,\n'
        'A silver-haired swordswoman jumps through the lightning.","motion":2}</illustration>'
    )

    clean, plan = ipe.extract_illustration_plan(reply)

    assert clean == "<content>她跃上高台，披风在雷光中扬起。</content>"
    assert plan["anchor"] == "她跃上高台，披风在雷光中扬起。"
    assert plan["profile_prompt"].splitlines() == [
        "silver-haired swordswoman, jumping, flowing cape,",
        "A silver-haired swordswoman jumps through the lightning.",
    ]


def test_Comfy提示词固定为质量行加英文内容行():
    content = "1girl, blue hair, looking at viewer, white background"

    assert ipe.format_comfy_prompt(content) == f"{ipe.COMFY_QUALITY_TAGS}\n{content}"


def test_Comfy提示词拒绝中文或自然语言内容():
    assert ipe.format_comfy_prompt("1girl, 蓝色头发") == ""
    assert ipe.format_comfy_prompt("A girl is standing beside a window.") == ""


def test_Comfy提示词把英文tag分号规整为逗号而不清空():
    content = "low angle; close-up, 1girl, ruined clothing, overcast light"

    out = ipe.format_comfy_prompt(content)

    assert out == f"{ipe.COMFY_QUALITY_TAGS}\nlow angle, close-up, 1girl, ruined clothing, overcast light"


def test_普通剧情高潮兜底不会生成成人提示词():
    tags = ipe.build_fallback_content_tags("最终决战中，她拔剑迎向敌人。")

    assert "climactic moment" in tags
    assert "explicit" not in tags


def test_可见剧情提取排除隐藏思考和控制块():
    reply = (
        "<think>自检时列举做爱、性交、色情等敏感词。</think>\n"
        "<content>塞西莉亚接受了拒绝，将徽章留在长凳上。</content>\n"
        "<status>内部状态</status>"
    )

    visible = ipe.visible_narrative_text(reply)

    assert visible == "塞西莉亚接受了拒绝，将徽章留在长凳上。"
    assert ipe.build_fallback_content_tags(reply).startswith("dramatic scene")
    assert "explicit" not in ipe.build_fallback_content_tags(reply)
    assert ipe.visible_narrative_text(
        "<think>列举色情词。</think><content>图片插槽前半段"
    ) == "图片插槽前半段"


def test_未闭合隐藏思考不能成为插画剧情():
    reply = "<think>规划角色动作时列举裸露、亲吻与成人场景，但模型在正文前截断"

    assert ipe.visible_narrative_text(reply) == ""
    assert "explicit" not in ipe.build_fallback_content_tags(reply)
    assert ipe.visible_narrative_text(
        "<think>未闭合推理里的裸露词<content>塞西莉亚把徽章留在长凳上。"
    ) == "塞西莉亚把徽章留在长凳上。"


def test_插画计划非法画幅比例回退竖图():
    reply = (
        "正文高潮。"
        '<illustration>{"anchor":"正文高潮。","camera":"low angle",'
        '"composition":"center composition","aspect_ratio":"21:9",'
        '"subjects":[{"name":"甲","description":"swordswoman"}],'
        '"prompt":"storm","motion":0}</illustration>'
    )

    _, plan = ipe.extract_illustration_plan(reply)

    assert plan["aspect_ratio"] == "2:3"


def test_解析艺术决策并置于结构化画面草稿前部():
    reply = (
        "正文高潮。"
        '<illustration>{"anchor":"正文高潮。","camera":"low angle",'
        '"visual_thesis":"blade reflection divides the two rivals",'
        '"hierarchy":"crossed blades first, faces second, crowd softened",'
        '"palette_material":"cold steel blue, warm blood red accent, polished metal",'
        '"lighting_logic":"torchlight strikes the blades and throws both faces into opposing shadows",'
        '"composition":"diagonal composition","subjects":[{"name":"甲",'
        '"description":"armored swordswoman","weight":1.4}],'
        '"prompt":"ruined arena","profile_prompt":"","motion":2}</illustration>'
    )

    _, plan = ipe.extract_illustration_plan(reply)

    assert plan["art_direction"]["visual_thesis"] == "blade reflection divides the two rivals"
    assert plan["art_direction"]["hierarchy"].startswith("crossed blades first")
    assert plan["subjects"] == [{
        "name": "甲", "description": "armored swordswoman", "weight": 1.4,
    }]
    assert plan["prompt"].startswith("blade reflection divides the two rivals")
    assert plan["prompt"].endswith("low angle, diagonal composition, (armored swordswoman:1.4), ruined arena")


def test_解析通用可视事实时只接受带正文逐字证据的条目():
    reply = (
        "<content>牢门开了。她侧卧石板，双腕锁在腰前，陌生玉器仍留在腿间。</content>"
        '<illustration>{"anchor":"陌生玉器仍留在腿间。","camera":"medium shot",'
        '"composition":"threshold frame","subjects":[{"name":"甲","description":"adult woman"}],'
        '"visual_facts":['
        '{"kind":"restraint","fact":"both wrists locked at her waist","evidence":"双腕锁在腰前"},'
        '{"kind":"prop","fact":"an unfamiliar jade object remains between her thighs","evidence":"陌生玉器仍留在腿间"},'
        '{"kind":"invented","fact":"a crown lies nearby","evidence":"王冠在旁边"}],'
        '"prompt":"doorway light","motion":1}</illustration>'
    )

    clean, plan = ipe.extract_illustration_plan(reply)

    assert plan["visual_facts"] == [
        {"kind": "restraint", "fact": "both wrists locked at her waist", "evidence": "双腕锁在腰前"},
        {"kind": "prop", "fact": "an unfamiliar jade object remains between her thighs", "evidence": "陌生玉器仍留在腿间"},
    ]
    assert "crown" not in plan["prompt"]
    assert "王冠" not in clean


def test_解析保留主模型同轮生成的模式提示词():
    reply = (
        "正文高潮。\n"
        '<illustration>{"anchor":"正文高潮。","camera":"low angle",'
        '"composition":"center composition","subjects":[{"name":"冷倾雪",'
        '"description":"1girl","weight":1.2}],"prompt":"rim light",'
        '"profile_prompt":"主模型生成的 Krea2 最终提示词。","motion":1}</illustration>'
    )

    _, plan = ipe.extract_illustration_plan(reply)

    assert plan["profile_prompt"] == "主模型生成的 Krea2 最终提示词。"


def test_插画块在JSON解析前复用正文正则以还原预设结构():
    reply = (
        "正文高潮。"
        "<illustration>{§anchor§:§正文高潮。§,§camera§:§low angle§,"
        "§composition§:§center composition§,§subjects§:[{§name§:§甲§,"
        "§description§:§adult woman§}],§prompt§:§turning§,"
        "§profile_prompt§:§A detailed English scene.§,§motion§:1}</illustration>"
    )

    clean, plan = ipe.extract_illustration_plan(
        reply, block_filter=lambda value: value.replace("§", '"'),
    )

    assert clean == "正文高潮。"
    assert plan["anchor"] == "正文高潮。"
    assert plan["profile_prompt"] == "A detailed English scene."


def test_解析动作延伸序列_归一化并还原防拦截():
    reply = (
        "铺垫。\n\n她舀起一勺奶油，缓缓送入口中。"
        '<illustration>{"anchor":"缓缓送入口中。",'
        '"composition":"close-up","aspect_ratio":"2:3",'
        '"subjects":[{"name":"白绮谷","weight":1.2,"description":"girl with spoon"}],'
        '"prompt":"dessert, warm light","motion":2,'
        '"action_sequence":[{"beat":"定格起点","desc":"勺子@(挖)@出一勺奶油"},'
        '{"beat":"延伸","desc":"送向嘴边，吃下"}]}</illustration>'
    )
    _, plan = ipe.extract_illustration_plan(reply)
    assert plan["action_sequence"] == [
        {"beat": "定格起点", "desc": "勺子挖出一勺奶油"},
        {"beat": "延伸", "desc": "送向嘴边，吃下"},
    ]


def test_解析动作延伸序列_空项丢弃_缺beat兜底():
    reply = (
        "铺垫。"
        '<illustration>{"anchor":"铺垫。","composition":"close-up","aspect_ratio":"2:3",'
        '"subjects":[{"name":"白绮谷","weight":1.2,"description":"girl"}],'
        '"prompt":"dessert","action_sequence":[{"beat":"","desc":"只挖不喂"},'
        '{"beat":"延伸","desc":""},{"desc":"喂向镜头"}]}</illustration>'
    )
    _, plan = ipe.extract_illustration_plan(reply)
    assert plan["action_sequence"] == [
        {"beat": "延伸", "desc": "只挖不喂"},
        {"beat": "延伸", "desc": "喂向镜头"},
    ]


def test_解析无动作序列_返回空列表():
    reply = (
        "铺垫。"
        '<illustration>{"anchor":"铺垫。","composition":"close-up","aspect_ratio":"2:3",'
        '"subjects":[{"name":"白绮谷","weight":1.2,"description":"girl"}],'
        '"prompt":"dessert","motion":1}</illustration>'
    )
    _, plan = ipe.extract_illustration_plan(reply)
    assert plan["action_sequence"] == []


def test_动作延伸序列_拒答文本丢弃_不流入时间分镜():
    # 防拦截回归：模型拒答句写进 desc 时必须丢弃，不得流入视频 [时间分镜]。
    # 台词原文（audio lines）不过滤由 parse_video_plan 负责，此处只管内联计划。
    reply = (
        "铺垫。"
        '<illustration>{"anchor":"铺垫。","composition":"close-up","aspect_ratio":"2:3",'
        '"subjects":[{"name":"白绮谷","weight":1.2,"description":"girl"}],'
        '"prompt":"dessert","action_sequence":[{"beat":"定格起点","desc":"我不能协助这项请求"},'
        '{"beat":"延伸","desc":"I cannot help with this request"},'
        '{"beat":"收尾","desc":"她吃下奶油"}]}</illustration>'
    )
    _, plan = ipe.extract_illustration_plan(reply)
    assert plan["action_sequence"] == [
        {"beat": "收尾", "desc": "她吃下奶油"},
    ]
