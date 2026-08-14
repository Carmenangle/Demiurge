# -*- coding: utf-8 -*-
"""Trace Replay 深化测试：model_quality 缓存命中率、失败率、请求-响应配对、by_agent 聚合。"""
from __future__ import annotations

from app.services import trace_replay


def _rec(turn_id: str, event: str, **data) -> dict:
    return {"turn_id": turn_id, "repo_id": "repo_x", "event": event, "data": data}


def _mk_turn(*records) -> list[dict]:
    return list(records)


# ── _model_quality ────────────────────────────────────────────────────────────


def test_model_quality_empty():
    q = trace_replay._model_quality([])
    assert q["requests"] == 0
    assert q["responses"] == 0
    assert q["cache_hit_ratio"] == 0.0
    assert q["error_rate"] == 0.0


def test_model_quality_counts():
    records = [
        _rec("t1", "model.request", agent="roleplay", model="m1"),
        _rec("t1", "model.response", agent="roleplay", content="ok"),
        _rec("t1", "model.usage", agent="roleplay", model="m1",
             usage={"prompt_tokens": 1000, "completion_tokens": 200,
                    "cached_tokens": 600, "total_tokens": 1200}),
        _rec("t1", "turn.completed"),
    ]
    q = trace_replay._model_quality(records)
    assert q["requests"] == 1
    assert q["responses"] == 1
    assert q["usage_events"] == 1
    assert q["prompt_tokens"] == 1000
    assert q["cached_tokens"] == 600
    assert q["completion_tokens"] == 200
    assert q["cache_hit_ratio"] == round(600 / 1000, 4)
    assert q["request_response_ratio"] == 1.0
    assert q["by_agent"]["roleplay"]["prompt_tokens"] == 1000
    assert q["by_agent"]["roleplay"]["cached_tokens"] == 600


def test_model_quality_aggregates_multiple():
    records = [
        _rec("t1", "model.request", agent="roleplay", model="m1"),
        _rec("t1", "model.usage", agent="roleplay", model="m1",
             usage={"prompt_tokens": 500, "completion_tokens": 50,
                    "cached_tokens": 200}),
        _rec("t1", "model.request", agent="supervisor", model="m2"),
        _rec("t1", "model.usage", agent="supervisor", model="m2",
             usage={"prompt_tokens": 300, "completion_tokens": 30,
                    "cached_tokens": 100}),
    ]
    q = trace_replay._model_quality(records)
    assert q["requests"] == 2
    assert q["prompt_tokens"] == 800
    assert q["cached_tokens"] == 300
    assert q["cache_hit_ratio"] == round(300 / 800, 4)
    assert set(q["by_agent"].keys()) == {"roleplay", "supervisor"}


def test_model_quality_no_usage_events():
    records = [
        _rec("t1", "model.request", agent="roleplay", model="m1"),
        _rec("t1", "model.response", agent="roleplay", content="ok"),
    ]
    q = trace_replay._model_quality(records)
    assert q["usage_events"] == 0
    assert q["prompt_tokens"] == 0
    assert q["cache_hit_ratio"] == 0.0
    assert q["request_response_ratio"] == 1.0


def test_model_quality_error_rate():
    # model_error 匹配 str(data).lower() 含 "model" ——
    # data = {"error": "..."}  stringify 后 key 名是 "error" 不是 "model"
    # 真实场景 model_error 通常在错误信息正文里，此处测真实错误文本含 model 关键字
    records = [
        _rec("t1", "model.request", agent="roleplay", model="m1"),
        _rec("t1", "agent.error", agent="roleplay", error="model timeout"),
        _rec("t1", "turn.completed"),
    ]
    q = trace_replay._model_quality(records)
    # agent.error data 含 "model" (error 字段值 "model timeout")
    assert q["errors"] == 1
    assert q["error_rate"] == 1.0  # 1 error / 1 request


# ── evaluate_records 集成 ─────────────────────────────────────────────────────


def test_evaluate_records_includes_model_quality():
    records = [
        _rec("t1", "turn.started"),
        _rec("t1", "model.request", agent="roleplay", model="m1"),
        _rec("t1", "model.response", agent="roleplay", content="ok"),
        _rec("t1", "model.usage", agent="roleplay", model="m1",
             usage={"prompt_tokens": 600, "completion_tokens": 120,
                    "cached_tokens": 300, "total_tokens": 720}),
        _rec("t1", "agent.completed", agent="roleplay", route="roleplay"),
        _rec("t1", "turn.completed"),
    ]
    result = trace_replay.evaluate_records(records)
    assert "model_quality" in result
    assert result["model_quality"]["prompt_tokens"] == 600
    assert result["model_quality"]["cache_hit_ratio"] == 0.5
    # 每 case 也带 model_quality
    assert "model_quality" in result["cases"][0]


def test_evaluate_records_version_unchanged():
    """深化不破坏现有返回结构。"""
    records = [
        _rec("t1", "turn.started"),
        _rec("t1", "turn.completed"),
    ]
    result = trace_replay.evaluate_records(records)
    assert result["version"] == 2
    assert "summary" in result
    assert "cases" in result
    assert "model_quality" in result
