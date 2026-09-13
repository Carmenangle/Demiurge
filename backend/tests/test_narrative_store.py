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


def test_替换导入在单个事务内完成(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("旧纪要"))

    imported = ns.import_entries(base, "r1", [_e("新纪要一"), _e("新纪要二")], replace=True)

    assert imported == 2
    assert [entry.text for entry in ns.all_entries(base, "r1")] == ["新纪要一", "新纪要二"]


def test_替换导入中途失败会保留旧纪要(monkeypatch, tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("旧纪要"))
    original = ns._insert_entry
    calls = 0

    def fail_second(conn, entry):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("bad row")
        return original(conn, entry)

    monkeypatch.setattr(ns, "_insert_entry", fail_second)

    try:
        ns.import_entries(base, "r1", [_e("新纪要一"), _e("新纪要二")], replace=True)
    except RuntimeError as exc:
        assert str(exc) == "bad row"
    else:
        raise AssertionError("应回滚失败导入")
    assert [entry.text for entry in ns.all_entries(base, "r1")] == ["旧纪要"]


# ── M1 记忆封口（2026-09-06 用户定案）：删除/重生成回合的纪要不再注入，保审计不物理删 ──


def test_封口回合后召回与最近不再返回重叠纪要(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("雪山救援事件", ts=1, te=3))
    ns.append(base, "r1", _e("舞会下药事件", ts=4, te=6))
    assert len(ns.recent(base, "r1", k=10)) == 2

    assert ns.seal_turns(base, "r1", [5]) == 1

    # 与封口回合重叠的纪要（4-6 含 5）不再出现在召回/最近/计数里；不重叠的（1-3）保留
    assert [e.text for e in ns.recent(base, "r1", k=10)] == ["雪山救援事件"]
    assert [e.text for e in ns.recall(base, "r1", "雪山救援")] == ["雪山救援事件"]
    assert ns.recall(base, "r1", "舞会下药") == []
    assert ns.count(base, "r1", layer=0) == 1
    assert [e.text for e in ns.oldest(base, "r1", k=5, layer=0)] == ["雪山救援事件"]
    # 数据仍在（封口非物理删）：all_entries 导出完整
    assert len(ns.all_entries(base, "r1")) == 2


def test_封口幂等且独立仓库隔离(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("事件甲", ts=1, te=2))
    ns.append(base, "r2", _e("事件乙", ts=1, te=2))
    assert ns.seal_turns(base, "r1", [1]) == 1
    assert ns.seal_turns(base, "r1", [1]) == 0  # 重复封口幂等
    assert len(ns.recent(base, "r2", k=10)) == 1  # 不影响其他仓库


# ── 区间整理（2026-09-13）：覆盖计数 / 只读计划 / 落盘让位与顺延 ──


def test_覆盖计数返回每回合的rowid列表(tmp_path):
    base = str(tmp_path)
    first = ns.append(base, "r1", _e("事件甲", ts=1, te=3))
    second = ns.append(base, "r1", _e("事件乙", ts=3, te=5))

    cov = ns.coverage(base, "r1")

    assert cov[3] == [first, second]   # 第 3 层被两条纪要覆盖
    assert cov[1] == [first]
    assert cov[5] == [second]


def test_只读计划_同区间多条归组保留最新(tmp_path):
    base = str(tmp_path)
    old = ns.append(base, "r1", _e("旧纪要", ts=1, te=3))
    new = ns.append(base, "r1", _e("新纪要", ts=1, te=3))

    plan = ns.normalize_preview(base, "r1", total_turns=3)

    assert plan["clean"] is False
    assert plan["conflict_turns"] == [1, 2, 3]
    assert plan["duplicates"][0]["rowids"] == [old, new]
    assert plan["duplicates"][0]["keep"] == new       # 覆盖语义：保留最近生成
    assert plan["duplicates"][0]["drop"] == [old]
    assert len(ns.all_entries(base, "r1")) == 2       # 只读，尚未落盘


def test_只读计划_相邻边界顺延保宽(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("甲", ts=33, te=35))
    second = ns.append(base, "r1", _e("乙", ts=35, te=37))

    plan = ns.normalize_preview(base, "r1", total_turns=40)

    assert plan["rebases"] == [{"rowid": second, "old": [35, 37], "new": [36, 38]}]


def test_落盘_封口重复条目并改写区间(tmp_path):
    base = str(tmp_path)
    old = ns.append(base, "r1", _e("旧纪要", ts=1, te=3))
    ns.append(base, "r1", _e("新纪要", ts=1, te=3))
    ns.append(base, "r1", _e("边界甲", ts=7, te=9))
    edge = ns.append(base, "r1", _e("边界乙", ts=9, te=11))

    result = ns.normalize_apply(
        base, "r1", seal_rowids=[old], rebases=[{"rowid": edge, "new": [10, 12]}])

    assert result == {"ok": True, "sealed": 1, "rebased": 1}
    assert [e.text for e in ns.recent(base, "r1", k=10)] == ["边界乙", "边界甲", "新纪要"]
    assert len(ns.all_entries(base, "r1")) == 4        # 封口非物理删，仍可导出
    assert ns.get_by_rowid(base, "r1", edge).turn_start == 10
    assert ns.normalize_preview(base, "r1", total_turns=12)["clean"] is True


def test_落盘_忽略库外rowid(tmp_path):
    base = str(tmp_path)
    ns.append(base, "r1", _e("事件甲", ts=1, te=3))

    result = ns.normalize_apply(
        base, "r1", seal_rowids=[999], rebases=[{"rowid": 999, "new": [1, 2]}])

    assert result == {"ok": True, "sealed": 0, "rebased": 0}
    assert len(ns.recent(base, "r1", k=10)) == 1


def test_落盘_无库返回未命中(tmp_path):
    assert ns.normalize_apply(str(tmp_path), "空仓", seal_rowids=[1]) == {
        "ok": False, "sealed": 0, "rebased": 0}


def test_最近与最旧按回合序而非rowid(tmp_path):
    base = str(tmp_path)
    early = ns.append(base, "r1", _e("第34到36回合的事", ts=34, te=36))
    backfill = ns.append(base, "r1", _e("补跑的第7到9回合", ts=7, te=9))

    # 补跑条目 rowid 更大，但不是「最近发生」——排序必须按回合区间
    assert [e.rowid for e in ns.recent(base, "r1", k=10)] == [early, backfill]
    assert [e.rowid for e in ns.oldest(base, "r1", k=10, layer=0)] == [backfill, early]


# ── 卡号按回合位序编号（2026-09-13 用户定案）────────────────────────────


def test_卡号按回合位序编号而非rowid(tmp_path):
    """用户实锤：整理后最早区间 [1–3] 显示成 T1-13（rowid），应是从头数的第几条。
    卡号必须是 T<层>-<位序>，位序按回合先后连续编，缺口不占号。"""
    base = str(tmp_path)
    ns.append(base, "r1", _e("第10到12回合", ts=10, te=12))
    ns.append(base, "r1", _e("第1到3回合", ts=1, te=3))
    ns.append(base, "r1", _e("第4到6回合", ts=4, te=6))

    items = ns.recent(base, "r1", k=10)
    ns.with_sequence(base, "r1", items)

    by_range = {(e.turn_start, e.turn_end): e.card_id() for e in items}
    assert by_range == {(1, 3): "T1-1", (4, 6): "T1-2", (10, 12): "T1-3"}


def test_卡号分层独立编号且封口后重新补位(tmp_path):
    """封口（整理让位）后，可见集合重新从 T<层>-1 起编号——即用户要的「弥补空缺」。"""
    base = str(tmp_path)
    old = ns.append(base, "r1", _e("第1到3回合重复条", ts=1, te=3))
    ns.append(base, "r1", _e("第1到3回合", ts=1, te=3))
    ns.append(base, "r1", _e("第4到6回合", ts=4, te=6))
    ns.append(base, "r1", _e("中层压缩", ts=1, te=6, layer=1))

    assert ns.sequence_map(base, "r1") == {
        old: 1, old + 1: 2, old + 2: 3, old + 3: 1,
    }
    before = ns.sequence_map(base, "r1")
    assert before[old] == 1 and before[old + 1] == 2   # 未封口时重复条也占号

    ns.normalize_apply(base, "r1", seal_rowids=[old])

    after = ns.sequence_map(base, "r1")
    assert old not in after                            # 封口条目不参与编号
    assert sorted(after.values()) == [1, 1, 2]         # layer0 两条 1,2；layer1 一条 1
    items = ns.recent(base, "r1", k=10)
    ns.with_sequence(base, "r1", items)
    assert {e.card_id() for e in items if e.layer == 0} == {"T1-1", "T1-2"}
    assert [e.card_id() for e in items if e.layer == 1] == ["T2-1"]


def test_导出序号含封口条目(tmp_path):
    base = str(tmp_path)
    old = ns.append(base, "r1", _e("被让位的重复条", ts=1, te=3))
    ns.append(base, "r1", _e("在位的条", ts=1, te=3))
    ns.append(base, "r1", _e("第4到6回合", ts=4, te=6))
    ns.normalize_apply(base, "r1", seal_rowids=[old])

    items = ns.all_entries(base, "r1")
    ns.with_sequence(base, "r1", items, include_sealed=True)

    assert len(items) == 3                              # 导出口径保留可审计条目
    assert sorted(e.seq for e in items) == [1, 2, 3]
