# -*- coding: utf-8 -*-
"""纪要落盘+召回测试（真实 SQLite FTS5，临时目录）：追加 / 召回 / 最近 / 进度 / 重建。"""
from app.services import narrative_store as ns
from app.services.narrative_memory import ChronicleEntry


def _e(text, ts=1, te=1, layer=0, kws=None):
    return ChronicleEntry(text=text, turn_start=ts, turn_end=te, layer=layer, keywords=kws or [])


def test_追加与最近(tmp_path):
    base = str(tmp_path)
    rid = ns.append(base, "r1", _e("雪山救援事件"))
    assert rid > 0
    ns.append(base, "r1", _e("舞会下药事件"))
    recent = ns.recent(base, "r1", k=10)
    assert len(recent) == 2
    assert recent[0].text == "舞会下药事件"  # rowid 降序


def test_丰富纪要字段落盘往返(tmp_path):
    entry = ChronicleEntry(
        text="两人协力脱险。", overview="雪山救援", dialogue="我欠你一次。",
        characters=["林月", "主角"], turn_start=1, turn_end=3, keywords=["雪山"],
    )
    rid = ns.append(str(tmp_path), "r1", entry)

    got = ns.get_by_rowid(str(tmp_path), "r1", rid)

    assert got is not None
    assert (got.overview, got.dialogue, got.characters) == (
        "雪山救援", "我欠你一次。", ["林月", "主角"],
    )


def test_追加空文本跳过(tmp_path):
    assert ns.append(str(tmp_path), "r1", _e("")) == 0
    assert ns.append("", "r1", _e("x")) == 0


def test_召回_trigram命中(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("第三章雪山救援后她对用户态度转暖"))
    ns.append(base, "r1", _e("两人在图书馆讨论魔法理论"))
    hits = ns.recall(base, "r1", "雪山救援的经过", k=4)
    assert len(hits) == 1
    assert "雪山救援" in hits[0].text


def test_召回_查询过短返回空(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("某事件发生了"))
    assert ns.recall(base, "r1", "ab") == []


def test_更新条目改写正文与索引(tmp_path):
    base = str(tmp_path)
    rid = ns.append(base, "r1", _e("旧的雪山事件"))
    assert ns.update_entry(base, "r1", rid, _e("新的沙漠绿洲事件", kws=["绿洲"]))
    got = ns.get_by_rowid(base, "r1", rid)
    assert got and got.text == "新的沙漠绿洲事件"
    # 索引已重算：新词能召回，旧词召不到
    assert ns.recall(base, "r1", "沙漠绿洲的经过", k=4)
    assert ns.recall(base, "r1", "雪山事件的经过", k=4) == []


def test_更新不存在的rowid返回False(tmp_path):
    assert ns.update_entry(str(tmp_path), "r1", 999, _e("x")) is False


def test_全量导出按rowid升序(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("事件一"))
    ns.append(base, "r1", _e("事件二"))
    alle = ns.all_entries(base, "r1")
    assert [e.text for e in alle] == ["事件一", "事件二"]


def test_召回_无库返回空(tmp_path):
    assert ns.recall(str(tmp_path), "空仓", "任意查询词") == []


def test_按层过滤(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("细节纪要", layer=0))
    ns.append(base, "r1", _e("粗略大纲", layer=1))
    assert ns.count(base, "r1", layer=0) == 1
    assert ns.count(base, "r1", layer=1) == 1
    assert len(ns.recent(base, "r1", k=10, layer=1)) == 1


def test_最旧与删除(tmp_path):
    base = str(tmp_path)
    ids = [ns.append(base, "r1", _e(f"事件{i}", layer=0)) for i in range(5)]
    olds = ns.oldest(base, "r1", k=2, layer=0)
    assert [o.text for o in olds] == ["事件0", "事件1"]
    removed = ns.delete_rows(base, "r1", [ids[0], ids[1]])
    assert removed == 2
    assert ns.count(base, "r1", layer=0) == 3


def test_抽取进度往返(tmp_path):
    base = str(tmp_path)
    assert ns.get_last_turn(base, "r1", "卡A") == 0
    ns.set_last_turn(base, "r1", "卡A", 12)
    assert ns.get_last_turn(base, "r1", "卡A") == 12
    ns.set_last_turn(base, "r1", "卡A", 18)  # 覆盖
    assert ns.get_last_turn(base, "r1", "卡A") == 18
    assert ns.get_last_turn(base, "r1", "卡B") == 0  # 别卡隔离


def test_重建索引_条目不丢仍可召回(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("雪山救援的关键一幕", kws=["雪山"]))
    ns.append(base, "r1", _e("舞会上的阴谋"))
    n = ns.rebuild(base, "r1")
    assert n == 2
    assert ns.count(base, "r1", layer=0) == 2
    hits = ns.recall(base, "r1", "雪山救援发生了什么", k=4)
    assert len(hits) == 1 and "雪山救援" in hits[0].text


def test_重建_无库返回0(tmp_path):
    assert ns.rebuild(str(tmp_path), "空仓") == 0


def test_物理隔离_不同repo(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("r1的事"))
    ns.append(base, "r2", _e("r2的事"))
    assert len(ns.recent(base, "r1", k=10)) == 1
    assert len(ns.recent(base, "r2", k=10)) == 1
    assert ns.recent(base, "r1", k=10)[0].text == "r1的事"
