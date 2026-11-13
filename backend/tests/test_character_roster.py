"""世界书角色名册（2026-11-10）回归测试。

覆盖：
1. 不依赖「角色卡/角色条目」前缀也能识别普通标题/keys 命名的角色；
2. 事件/势力/机制等非角色条目不进名册；
3. 手动 include 可补漏；exclude/rename/order 覆盖生效；
4. 稳定 uid：dict 用容器键、list 用 id/uid，缺失时内容哈希兜底。
"""
from __future__ import annotations

from app.services import character_roster, worldbook_edit


def _book(entries: list[dict]) -> dict:
    return {"entries": entries}


def _names(roster: dict) -> list[str]:
    return [item["name"] for item in roster["members"]]


def test_普通标题角色条目靠人物档案识别_不要求角色条目前缀():
    book = _book([
        {
            "comment": "林昭",
            "content": "林昭是青云宗掌门，性格清冷。她银发红眸，身高一百七十，惯穿月白长裙。",
            "keys": ["林昭", "掌门"],
        },
    ])

    roster = character_roster.build(book)

    assert _names(roster) == ["林昭"]
    assert roster["members"][0]["confidence"] == "medium"
    assert roster["members"][0]["has_visual"] is True


def test_事件势力机制条目不进名册():
    book = _book([
        {"comment": "大事件·宗门大比", "content": "林昭在台上出剑，她银发飞扬，身法极快。", "keys": ["大比"]},
        {"comment": "势力与种族·青云宗", "content": "青云宗弟子众多，她门规森严。", "keys": ["青云宗"]},
        {"comment": "全局机制·修为", "content": "所有角色按修为境界判定强弱。", "keys": ["修为"]},
        {"comment": "林昭", "content": "林昭是青云宗掌门，她性格清冷，银发红眸。", "keys": ["林昭"]},
    ])

    roster = character_roster.build(book)

    assert _names(roster) == ["林昭"]


def test_标题无名字时用_keys_取名_低置信只进候选不自动进成员():
    book = _book([
        {"comment": "角色", "content": "她性格冷淡地站在门口，没有回头。", "keys": ["阿七"]},
    ])

    roster = character_roster.build(book)

    assert _names(roster) == []
    candidate = next(item for item in roster["candidates"] if item["uid"])
    assert candidate["name"] == "阿七"
    assert candidate["confidence"] == "low"
    assert candidate["selected"] is False


def test_阵营地点作品名与编号不识别为角色():
    book = _book([
        {"comment": "碧蓝航线", "content": "碧蓝航线是舰娘所在的平行世界，她与他都在这里。", "keys": ["碧蓝航线"]},
        {"comment": "舰娘", "content": "舰娘拥有舰装，她们的外貌与性格各异。", "keys": ["舰娘"]},
        {"comment": "铁血后宫", "content": "铁血后宫是港区的一部分，那里住着许多她。", "keys": ["铁血后宫"]},
        {"comment": "重樱", "content": "重樱是东方阵营，她们崇尚礼仪，外貌清秀。", "keys": ["重樱"]},
        {"comment": "摩根财团", "content": "摩根财团控制着港口，她与他都欠它人情。", "keys": ["摩根财团"]},
        {"comment": "荒岛", "content": "荒岛位于航线尽头，她在那里醒来。", "keys": ["荒岛"]},
        {"comment": "梦境篇", "content": "梦境篇讲述她与他的一段幻境，外貌依旧。", "keys": ["梦境篇"]},
        {"comment": "IB031", "content": "IB031 是她的实验编号，她银发红眸。", "keys": ["IB031"]},
        {"comment": "腓特烈大帝", "content": "腓特烈大帝是铁血的战列舰，她银发红眸，身高一百八十，性格威严。", "keys": ["腓特烈大帝"]},
    ])

    roster = character_roster.build(book, exclude_names={"示例作品甲"})

    assert _names(roster) == ["腓特烈大帝"]


def test_作品卡名同名条目不进名册():
    book = _book([
        {"comment": "示例作品甲", "content": "示例作品甲是这部作品的名字，她与他都在其中。", "keys": ["示例作品甲"]},
        {"comment": "腓特烈大帝", "content": "腓特烈大帝是战列舰，她银发红眸，身高一百八十。", "keys": ["腓特烈大帝"]},
    ])

    assert _names(character_roster.build(book, exclude_names={"示例作品甲"})) == ["腓特烈大帝"]
    # 即使不传 exclude_names，作品名条目也没有人物身体/档案信号，本来就不会进名册。
    assert _names(character_roster.build(book)) == ["腓特烈大帝"]


def test_同名条目合并为一个角色并拼接正文():
    book = _book([
        {"comment": "腓特烈大帝", "content": "腓特烈大帝是战列舰，她银发红眸，性格威严。", "keys": ["腓特烈大帝"]},
        {"comment": "腓特烈大帝", "content": "【外貌】她常穿黑色军装，腰侧佩剑。", "keys": ["腓特烈"]},
    ])

    roster = character_roster.build(book)

    assert _names(roster) == ["腓特烈大帝"]
    member = roster["members"][0]
    assert len(member["uids"]) == 2
    assert "黑色军装" in member["content"]
    assert sum(1 for item in roster["candidates"] if item["selected"]) == 2


def test_显式角色卡标记为高置信():
    book = _book([
        {"comment": "角色卡·苏晚", "content": "苏晚是药王谷弟子。", "keys": []},
    ])

    roster = character_roster.build(book)

    assert _names(roster) == ["苏晚"]
    assert roster["members"][0]["confidence"] == "high"


def test_正文首个标题可作角色名_但不把外貌性格小标题当名字():
    book = _book([
        {
            "comment": "",
            "content": "【林昭】\n【外貌】银发红眸，身量高挑。\n【性格】清冷寡言。",
            "keys": [],
        },
    ])

    roster = character_roster.build(book)

    assert _names(roster) == ["林昭"]


def test_手动include可补漏_exclude与rename生效():
    book = _book([
        {"comment": "林昭", "content": "林昭是青云宗掌门，她性格清冷，银发红眸。", "keys": ["林昭"]},
        {"comment": "旧事", "content": "那一年山门被围，众人四散。", "keys": []},
    ])
    entries = worldbook_edit.list_entries(book)
    lin_uid = entries[0]["uid"]
    manual_uid = entries[1]["uid"]

    auto = character_roster.build(book)
    assert _names(auto) == ["林昭"]

    manual = character_roster.build(book, overrides={"include": [manual_uid]})
    assert set(_names(manual)) == {"林昭", "旧事"}
    assert next(item for item in manual["members"] if item["name"] == "旧事")["confidence"] == "manual"

    renamed = character_roster.build(
        book,
        overrides={"include": [manual_uid], "exclude": [lin_uid], "rename": {manual_uid: "旧事·暗线"}},
    )
    assert _names(renamed) == ["旧事·暗线"]
    assert renamed["candidates"][0]["selected"] is False
    assert next(
        item for item in renamed["members"] if item["name"] == "旧事·暗线"
    )["source_name"] == "旧事"


def test_order按uid排序且候选清单覆盖全部启用条目():
    book = _book([
        {"comment": "甲", "content": "甲是剑客，他性格孤僻，黑发黑衣。", "keys": ["甲"]},
        {"comment": "乙", "content": "乙是医师，她性格温和，白衣银发。", "keys": ["乙"]},
        {"comment": "大事件·旧事", "content": "无关叙述。", "keys": []},
    ])
    entries = worldbook_edit.list_entries(book)
    uid_a, uid_b = entries[0]["uid"], entries[1]["uid"]

    roster = character_roster.build(book, overrides={"order": [uid_b, uid_a]})

    assert _names(roster) == ["乙", "甲"]
    assert len(roster["candidates"]) == 3
    assert roster["candidates"][2]["selected"] is False


def test_稳定uid优先容器键与id():
    book = {
        "entries": {
            "u-100": {"comment": "甲", "content": "甲是剑客，他黑发黑衣。", "keys": ["甲"]},
            "u-200": {"comment": "乙", "content": "乙是医师，她白衣银发。", "keys": ["乙"]},
        },
    }
    entries = worldbook_edit.list_entries(book)
    assert [item["uid"] for item in entries] == ["u-100", "u-200"]

    book_list = _book([{"id": "n-1", "comment": "丙", "content": "丙是掌柜。"}])
    assert worldbook_edit.list_entries(book_list)[0]["uid"] == "n-1"
