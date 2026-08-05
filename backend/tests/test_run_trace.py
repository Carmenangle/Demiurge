from __future__ import annotations

import json

from app.services import run_trace
from app.services.agent_contracts import RunContext


def test_emit写utf8_jsonl并脱敏(tmp_path, monkeypatch):
    target = tmp_path / "logs" / "agent-trace.jsonl"
    monkeypatch.setattr(run_trace, "TRACE_FILE", target)
    monkeypatch.setattr(run_trace, "ENABLED", True)
    monkeypatch.setattr(run_trace, "MAX_BYTES", 10_000)
    ctx = RunContext(thread_id="线一", message="你好", repo_id="作品一")
    ctx.turn_id = "turn-fixed"

    run_trace.emit(ctx, "model.request", agent="剧情", api_key="secret", messages=[{"role": "user", "content": "中文输入"}])

    item = json.loads(target.read_text(encoding="utf-8"))
    assert item["turn_id"] == "turn-fixed"
    assert item["repo_id"] == "作品一"
    assert item["data"]["api_key"] == "***"
    assert item["data"]["messages"][0]["content"] == "中文输入"


def test_emit达到上限时轮转(tmp_path, monkeypatch):
    target = tmp_path / "agent-trace.jsonl"
    target.write_text("x" * 20, encoding="utf-8")
    monkeypatch.setattr(run_trace, "TRACE_FILE", target)
    monkeypatch.setattr(run_trace, "ENABLED", True)
    monkeypatch.setattr(run_trace, "MAX_BYTES", 10)
    monkeypatch.setattr(run_trace, "BACKUPS", 2)
    ctx = RunContext(thread_id="t", message="m")

    run_trace.emit(ctx, "turn.started", raw_input="m")

    assert target.with_name("agent-trace.jsonl.1").read_text(encoding="utf-8") == "x" * 20
    assert json.loads(target.read_text(encoding="utf-8"))["event"] == "turn.started"
