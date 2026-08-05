"""state 只读端点：薄委托 character_state，直测路由函数（无需起 TestClient）。"""
from __future__ import annotations

from app.routers import state as state_router
from app.services import character_state as cs


def test_读空状态不报错(tmp_path):
    got = state_router.get_state(output_dir=str(tmp_path), repo_id="nope", card_name="卡")
    assert got["数值"] == {} and got["叙事"] == {}
    assert got["快照"] == {"text": "", "turn": 0}


def test_读已存状态含快照(tmp_path):
    base = str(tmp_path)
    st = cs.CharacterState(card_name="卡", repo_id="r1")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 8, "evidence": "救援"}], turn=1))
    st.快照 = cs.Snapshot("[臣服] 叶燃眉=100", turn=1)
    cs.save_state(base, st)
    got = state_router.get_state(output_dir=base, repo_id="r1", card_name="卡")
    assert got["数值"]["好感度"]["value"] == 8.0
    assert got["快照"]["text"] == "[臣服] 叶燃眉=100"
    assert len(got["历史"]) == 1


def _req(model, **kw):
    return model(**kw)


def test_手改设精确值并标user(tmp_path):
    base = str(tmp_path)
    st = cs.CharacterState(card_name="卡", repo_id="r1")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 8}], turn=3))
    cs.save_state(base, st)
    req = _req(state_router.PatchStateRequest, output_dir=base, repo_id="r1",
               card_name="卡", edits=[{"field": "数值/好感度", "value": 50}])
    got = state_router.patch_state(req)
    assert got["updated"] == 1
    assert got["数值"]["好感度"]["value"] == 50.0        # 精确设值非累加(不是 58)
    assert got["数值"]["好感度"]["source"] == "user"
    # 落盘可重建
    re = cs.load_state(base, "r1", "卡")
    assert re.数值["好感度"].value == 50.0


def test_回滚撤销最近变更(tmp_path):
    base = str(tmp_path)
    st = cs.CharacterState(card_name="卡", repo_id="r1")
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 8}], turn=1))
    cs.apply_deltas(st, cs.parse_deltas(
        [{"field": "数值/好感度", "op": "add", "value": 5}], turn=2))
    cs.save_state(base, st)  # 好感度 = 13
    req = _req(state_router.RollbackRequest, output_dir=base, repo_id="r1", card_name="卡", n=1)
    got = state_router.rollback_state(req)
    assert got["undone"] == 1
    assert got["数值"]["好感度"]["value"] == 8.0          # 还原到第2次变更前


def test_写口缺参数报400(tmp_path):
    import pytest
    from fastapi import HTTPException
    req = _req(state_router.PatchStateRequest, output_dir="", repo_id="r1",
               edits=[{"field": "数值/好感度", "value": 1}])
    with pytest.raises(HTTPException):
        state_router.patch_state(req)
