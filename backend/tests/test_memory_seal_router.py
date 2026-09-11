"""M1 回合号持久化 + 记忆封口端点回归（2026-09-06 用户定案）。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import ai_agent
from app.services import (
    agent_graph,
    character_state as cs,
    memory_rollback,
    narrative_memory as nm,
    narrative_store as ns,
    temporal_fact_store as tfs,
)


# ── story_turn 透传：持久回合号优先，未提供退回派生 ──


def test_下一回合号优先用前端持久值():
    ctx = {"story_turn": 12, "repo_id": "r1", "history": [
        {"role": "assistant", "content": "x"}] * 5}
    assert agent_graph._next_story_turn(ctx) == 12  # 不再数消息（删除/重生成不倒退）


def test_下一回合号未提供时退回派生():
    ctx = {"story_turn": 0, "repo_id": "r1", "history": [
        {"role": "assistant", "content": "甲"},
        {"role": "assistant", "content": "乙"},
        {"role": "assistant", "content": ""},
    ]}
    assert agent_graph._next_story_turn(ctx) == 3


def test_下一回合号脏值退回派生():
    ctx = {"story_turn": "abc", "history": [{"role": "assistant", "content": "甲"}]}
    assert agent_graph._next_story_turn(ctx) == 2


# ── /ai/memory/seal 端点：薄委托 memory_rollback ──


def _seed(base, repo, card):
    ns.append(base, repo, nm.ChronicleEntry(text="舞会事件", turn_start=4, turn_end=6,
                                            overview="舞会"))
    tfs.record(base, repo, subject="城主", predicate="担任", object_="新城主",
               valid_from_turn=5, evidence="回合5剧情", source="chronicle")
    st = cs.CharacterState(card_name=card, repo_id=repo)
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 15, "evidence": "回合5事件"}], turn=5))
    cs.save_state(base, st)


def test_封口端点级联三存储(tmp_path):
    base, repo, card = str(tmp_path), "r1", "卡"
    _seed(base, repo, card)
    req = ai_agent.MemorySealRequest(output_dir=base, repo_id=repo, card_name=card, turns=[5])
    got = ai_agent.memory_seal(req)
    assert got["chronicle_sealed"] == 1 and got["facts_sealed"] == 1 and got["state_undone"] == 1
    assert got["state_incomplete"] == []
    assert ns.recent(base, repo, k=10) == []
    assert tfs.as_of(base, repo, 9) == []
    assert cs.load_state(base, repo, card).数值["好感度"].value == 0.0


def test_封口端点repo缺省用thread并校验参数(tmp_path):
    req = ai_agent.MemorySealRequest(output_dir=str(tmp_path), thread_id="t1", turns=[1])
    assert ai_agent.memory_seal(req)["chronicle_sealed"] == 0  # 空仓库零副作用
    with pytest.raises(HTTPException, match="repo_id"):
        ai_agent.memory_seal(ai_agent.MemorySealRequest(output_dir="", thread_id="t", turns=[1]))
    with pytest.raises(HTTPException, match="turns"):
        ai_agent.memory_seal(ai_agent.MemorySealRequest(output_dir="o", repo_id="r", turns=[]))
    # memory_rollback 对非法回合零副作用（端点防御的底层兜底）
    assert memory_rollback.seal_turns(str(tmp_path), "r", card_name="", turns=[]) == {
        "chronicle_sealed": 0, "facts_sealed": 0, "state_undone": 0,
        "state_incomplete": [], "last_turn_rewound": False}


# ── M1 审计 #5：会话有活动生成任务时拒绝封口（防旧任务收尾写回残留） ──


def test_活动运行中封口被409拒绝(tmp_path):
    import threading

    from app.services import thread_admission
    base, repo, card = str(tmp_path), "r1", "卡"
    _seed(base, repo, card)
    admission = thread_admission.admit("r1", threading.Event())
    try:
        req = ai_agent.MemorySealRequest(output_dir=base, repo_id=repo, card_name=card, turns=[5])
        with pytest.raises(HTTPException) as exc:
            ai_agent.memory_seal(req)
        assert exc.value.status_code == 409
        # 数据未被半途封口（拒绝是前置守卫，非中途失败）
        assert len(ns.recent(base, repo, k=10)) == 1
    finally:
        thread_admission.release(admission)
    # 释放后可正常封口
    got = ai_agent.memory_seal(ai_agent.MemorySealRequest(
        output_dir=base, repo_id=repo, card_name=card, turns=[5]))
    assert got["chronicle_sealed"] == 1
