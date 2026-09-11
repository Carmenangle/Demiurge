"""正常对话长期事实记忆（chat_facts，M3 2026-09-06 用户定案）回归测试。

门控抽取（每 4 回合搭一次 LLM）、指认式替代复用、失败静默降级、注入块 recency-first。
全部走 tmp 目录（monkeypatch DATA_DIR），绝不触碰真实数据。
"""
from __future__ import annotations

import json

import pytest

from app.services import chat_facts, temporal_fact_store as tfs


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_facts, "DATA_DIR", tmp_path)
    return str(tmp_path)


def _stub(facts: list[dict]):
    return lambda *a, **k: json.dumps({"facts": facts}, ensure_ascii=False)


def test_门控_未到cadence不抽取(base):
    calls = []
    written = chat_facts.maybe_extract(
        lambda *a, **k: calls.append(1) or "{}", "t1", window_text="闲聊", turn=3,
        chat_base="b", chat_key="k", chat_model="m")
    assert written is False and calls == []  # turn 3 % 4 ≠ 0 → 零成本跳过


def test_到cadence抽取落事实(base):
    fact = {"subject": "用户", "predicate": "常用模型", "object": "glm-5.3-flash",
            "evidence": "我一直用这个模型"}
    written = chat_facts.maybe_extract(
        _stub([fact]), "t1", window_text="对话窗口……", turn=4,
        chat_base="b", chat_key="k", chat_model="m")
    assert written is True
    got = tfs.as_of(chat_facts.facts_base(), "t1", 4)
    assert [f["object"] for f in got] == ["glm-5.3-flash"]


def test_指认式替代_新偏好收口旧偏好(base):
    tfs.record(chat_facts.facts_base(), "t1", subject="用户", predicate="常用模型",
               object_="旧模型", valid_from_turn=4, evidence="旧对话", source="chat")
    fact = {"subject": "用户", "predicate": "常用模型", "object": "新模型",
            "evidence": "以后换用新模型", "supersedes_id": "x" * 8}
    # 前缀必须唯一命中现存事实——桩直接用真实 id 前缀
    real_id = tfs.as_of(chat_facts.facts_base(), "t1", 4)[0]["id"]
    fact["supersedes_id"] = real_id[:12]
    assert chat_facts.maybe_extract(
        _stub([fact]), "t1", window_text="换模型了", turn=8,
        chat_base="b", chat_key="k", chat_model="m") is True
    assert [f["object"] for f in tfs.as_of(chat_facts.facts_base(), "t1", 8)] == ["新模型"]
    old = [f for f in tfs.timeline(chat_facts.facts_base(), "t1") if f["object"] == "旧模型"][0]
    assert old["valid_to_turn"] == 7  # 收口不删除


def test_指认校验失败降级普通ADD不丢信息(base):
    fact = {"subject": "项目A", "predicate": "截止日", "object": "周五",
            "evidence": "用户说周五交", "supersedes_id": "ffffffffffff"}  # 不存在 → 解析 None → 普通 ADD
    assert chat_facts.maybe_extract(
        _stub([fact]), "t1", window_text="项目排期", turn=4,
        chat_base="b", chat_key="k", chat_model="m") is True
    got = tfs.as_of(chat_facts.facts_base(), "t1", 4)
    assert [f["object"] for f in got] == ["周五"]


def test_抽取失败静默跳过(base):
    def broken(*a, **k):
        raise ConnectionError("网关拦截")

    assert chat_facts.maybe_extract(
        broken, "t1", window_text="敏感内容", turn=4,
        chat_base="b", chat_key="k", chat_model="m") is False
    assert tfs.as_of(chat_facts.facts_base(), "t1", 9) == []  # 旧记忆不动，无错误记忆


def test_注入块recency_first且空库为空(base):
    assert chat_facts.render_facts_block("t-none", 1) == ""
    tfs.record(chat_facts.facts_base(), "t1", subject="偏好", predicate="语言", object_="中文",
               valid_from_turn=1, evidence="e", source="chat")
    tfs.record(chat_facts.facts_base(), "t1", subject="项目", predicate="名称",
               object_="Demiurge", valid_from_turn=5, evidence="e", source="chat")
    block = chat_facts.render_facts_block("t1", 9)
    assert "偏好｜语言｜中文" in block
    assert "项目｜名称｜Demiurge" in block
    assert block.index("项目｜名称") < block.index("偏好｜语言")  # M2 同款：新→旧
    assert block.startswith("【已记住的长期事实")
