from app.services import trace_replay
from app.routers import ai_agent


def _record(event: str, data: dict, turn: str = "t1") -> dict:
    return {
        "timestamp": "2026-08-10T12:00:00+08:00",
        "turn_id": turn,
        "repo_id": "repo",
        "event": event,
        "data": data,
    }


def test_replay_validates_recorded_supervisor_and_turn_invariants():
    report = trace_replay.evaluate_records([
        _record("turn.started", {}),
        _record("model.response", {
            "agent": "supervisor",
            "content": '{"route":"answer","confidence":"high","alternatives":[],"scene":"dialogue"}',
        }),
        _record("agent.completed", {"agent": "supervisor", "route": "answer"}),
        _record("illustration.request", {"status": "emitted", "reason": "main_plan"}),
        _record("turn.completed", {"interrupted": False}),
    ])

    assert report["summary"] == {
        "cases": 1, "passed": 1, "failed": 0,
        "outcomes": {"local_request_missing": 1},
    }
    case = report["cases"][0]
    assert case["checks"]["turn_completed"] is True
    assert case["checks"]["supervisor_schema"] is True
    assert case["checks"]["route_consistent"] is True
    assert case["checks"]["illustration_terminal"] is True


def test_replay_reports_broken_recorded_json_without_executing_side_effects():
    report = trace_replay.evaluate_records([
        _record("turn.started", {}),
        _record("model.response", {"agent": "supervisor", "content": "{broken"}),
        _record("turn.completed", {}),
    ])

    assert report["summary"]["failed"] == 1
    assert report["cases"][0]["checks"]["supervisor_schema"] is False


def test_trace_replay_router_is_a_thin_offline_adapter(monkeypatch):
    monkeypatch.setattr(
        trace_replay, "replay_recent",
        lambda repo_id, *, turn_id, limit: {
            "repo_id": repo_id, "turn_id": turn_id, "limit": limit,
        },
    )

    result = ai_agent.replay_trace(ai_agent.TraceReplayRequest(
        repo_id="  work  ", turn_id=" t1 ", limit=20,
    ))

    assert result == {"repo_id": "work", "turn_id": "t1", "limit": 20}


def test_replay_distinguishes_missing_preset_injection_from_model_noncompliance():
    report = trace_replay.evaluate_records([
        _record("turn.started", {}),
        _record("turn.context_ready", {"preset_name": "GrayWill"}),
        _record("model.request", {
            "agent": "roleplay", "preset": "", "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "continue"},
            ],
        }),
        _record("model.response", {"agent": "roleplay", "content": "plain reply"}),
        _record("turn.completed", {"assistant_output": "plain reply"}),
    ])

    compliance = report["cases"][0]["compliance"]
    assert compliance["outcome"] == "local_injection_missing"
    assert compliance["preset_requested"] is True
    assert compliance["preset_attached"] is False
    assert report["summary"]["outcomes"] == {"local_injection_missing": 1}


def test_replay_distinguishes_upstream_refusal_after_valid_final_request():
    report = trace_replay.evaluate_records([
        _record("turn.started", {}),
        _record("turn.context_ready", {"preset_name": "GrayWill"}),
        _record("model.request", {
            "agent": "roleplay", "preset": "GrayWill", "messages": [
                {"role": "system", "content": "compiled preset"},
                {"role": "user", "content": "continue"},
            ],
        }),
        _record("model.response", {
            "agent": "roleplay", "content": "我不能描写这类内容，但可以继续其他情节。",
        }),
        _record("illustration.profile", {"strategy": "local_fallback"}),
        _record("turn.completed", {"assistant_output": "我不能描写这类内容。"}),
    ])

    compliance = report["cases"][0]["compliance"]
    assert compliance["outcome"] == "upstream_refusal"
    assert compliance["preset_attached"] is True
    assert compliance["wire_roles"] == ["system", "user"]
    assert compliance["profile_strategy"] == "local_fallback"
