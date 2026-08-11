# -*- coding: utf-8 -*-
"""纪要记忆编排：召回注入 / 门控抽取（每N轮）/ 抽取失败不落 / 分层压缩。假 chat_fn 不碰真 LLM。"""
from __future__ import annotations

import random

from app.services import narrative_memory as nm
from app.services import narrative_store as ns
from app.services import roleplay_agency as ra
from app.services import temporal_fact_store


def _deps(tmp_path, chat_fn=None):
    return ra.AgencyDeps(
        chat_fn=chat_fn or (lambda *a, **k: "[]"), rng=random.Random(0),
        state_base=str(tmp_path), renderer=None)


def test_召回_命中往事注入(tmp_path):
    ns.append(str(tmp_path), "r1",
              nm.ChronicleEntry(text="第三章雪山救援后态度转暖"))
    deps = _deps(tmp_path)
    out = ra.recall_chronicle(deps, repo_id="r1", query="雪山救援时发生了什么")
    assert "雪山救援" in out


def test_召回_无repo或空查询空串(tmp_path):
    deps = _deps(tmp_path)
    assert ra.recall_chronicle(deps, repo_id="", query="x") == ""
    assert ra.recall_chronicle(deps, repo_id="r1", query="  ") == ""


def test_抽取_未到cadence不调LLM(tmp_path):
    calls = []

    def spy(*a, **k):
        calls.append(1)
        return '{"summary":"x"}'

    deps = _deps(tmp_path, spy)
    # last=0, turn=3, cadence=6 → 未到，不调
    got = ra.maybe_summarize(deps, repo_id="r1", card_name="卡A",
                             window_text="一些对话", turn=3,
                             chat_base="b", chat_key="k", chat_model="m", cadence=6)
    assert got is False
    assert calls == []


def test_抽取_到cadence落一条纪要并推进进度(tmp_path):
    def fake(*a, **k):
        return '{"summary":"雪山救援后关系转暖","keywords":["雪山","救援"]}'

    deps = _deps(tmp_path, fake)
    got = ra.maybe_summarize(deps, repo_id="r1", card_name="卡A",
                             window_text="大段对话", turn=6,
                             chat_base="b", chat_key="k", chat_model="m", cadence=6)
    assert got is True
    items = ns.recent(str(tmp_path), "r1", k=10)
    assert len(items) == 1 and "雪山救援" in items[0].text
    assert ns.get_last_turn(str(tmp_path), "r1", "卡A") == 6


def test_纪要同一次结构化输出顺带写世界事实账本(tmp_path):
    payload = (
        '{"overview":"政变结束","chronicle":"新王接管王城",'
        '"facts":[{"subject":"王城","predicate":"统治者","object":"新王",'
        '"evidence":"新王在大殿接过王冠"}]}'
    )
    deps = _deps(tmp_path, lambda *a, **k: payload)

    assert ra.maybe_summarize(
        deps, repo_id="r1", card_name="卡A", window_text="三轮剧情", turn=3,
        chat_base="b", chat_key="k", chat_model="m",
    ) is True

    facts = temporal_fact_store.as_of(str(tmp_path), "r1", 3)
    assert [(fact["subject"], fact["predicate"], fact["object"])
            for fact in facts] == [("王城", "统治者", "新王")]


def test_每个纪要频率区间都新建独立卡且不自动压缩(tmp_path):
    def fake(*a, **k):
        return '{"overview":"阶段概览","chronicle":"阶段事件详情","keywords":[]}'

    deps = _deps(tmp_path, fake)
    turns = list(range(3, (nm.LAYER0_CAP + 2) * 3, 3))
    for turn in turns:
        assert ra.maybe_summarize(
            deps, repo_id="r1", card_name="卡A", window_text=f"第{turn - 2}至{turn}回合",
            turn=turn, chat_base="b", chat_key="k", chat_model="m",
        ) is True

    entries = ns.all_entries(str(tmp_path), "r1")
    assert len(entries) == len(turns)
    assert [(entry.turn_start, entry.turn_end, entry.layer) for entry in entries] == [
        (turn - 2, turn, 0) for turn in turns
    ]


def test_抽取失败_不落盘不推进(tmp_path):
    deps = _deps(tmp_path, lambda *a, **k: "模型抽风没吐JSON")
    got = ra.maybe_summarize(deps, repo_id="r1", card_name="卡A",
                             window_text="对话", turn=6,
                             chat_base="b", chat_key="k", chat_model="m")
    assert got is False
    assert ns.recent(str(tmp_path), "r1", k=10) == []
    assert ns.get_last_turn(str(tmp_path), "r1", "卡A") == 0


def test_纪要历史缺口不合并成跨频率大卡(tmp_path):
    calls = []
    deps = _deps(tmp_path, lambda *a, **k: calls.append(1) or '{"summary":"错误大卡"}')
    ns.set_last_turn(str(tmp_path), "r1", "卡A", 3)

    got = ra.maybe_summarize(
        deps, repo_id="r1", card_name="卡A", window_text="最近几轮",
        turn=14, chat_base="b", chat_key="k", chat_model="m", cadence=3,
    )

    assert got is False
    assert calls == []
    assert ns.all_entries(str(tmp_path), "r1") == []
    assert ns.get_last_turn(str(tmp_path), "r1", "卡A") == 3


def test_分层压缩_超上限压成上层(tmp_path):
    base = str(tmp_path)
    # 预置超过 layer0 上限的细纪要
    for i in range(nm.LAYER0_CAP + 1):
        ns.append(base, "r1", nm.ChronicleEntry(text=f"细节事件{i}", turn_start=i, turn_end=i, layer=0))
    before0 = ns.count(base, "r1", layer=0)
    assert before0 == nm.LAYER0_CAP + 1

    deps = _deps(tmp_path, lambda *a, **k: '{"summary":"归并大纲","keywords":[]}')
    ra._compact_layers(deps, repo_id="r1", chat_base="b", chat_key="k", chat_model="m")
    # 吃掉 COMPACT_BATCH 条 layer0，产出 1 条 layer1
    assert ns.count(base, "r1", layer=0) == before0 - nm.COMPACT_BATCH
    assert ns.count(base, "r1", layer=1) == 1


def test_分层压缩_归并失败不动旧条(tmp_path):
    base = str(tmp_path)
    for i in range(nm.LAYER0_CAP + 1):
        ns.append(base, "r1", nm.ChronicleEntry(text=f"事件{i}", layer=0))
    deps = _deps(tmp_path, lambda *a, **k: "没有JSON")
    ra._compact_layers(deps, repo_id="r1", chat_base="b", chat_key="k", chat_model="m")
    assert ns.count(base, "r1", layer=0) == nm.LAYER0_CAP + 1  # 未动
    assert ns.count(base, "r1", layer=1) == 0
