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


def test_测试期TRACE必须已重定向出真实日志目录():
    """契约守卫（2026-09-15 P2）：**本测试不自己 monkeypatch**。

    全量测试的 trace 隔离靠 `tests/conftest.py::_isolate_run_trace`（autouse）。一旦有人删掉
    那个 fixture，或新写的生产路径绕开 `run_trace` 模块属性直写真实文件，本用例立刻失败——
    它守的是「跑测试不得写真实 `backend/data/logs/`」：真实 trace 被反复写/轮转时会触发
    `_rotate` 的 `unlink`，撞沙箱 safe-delete 守卫抛 `SystemExit(1)`（`emit` 的
    `except Exception` 兜不住），表现为整批假失败。
    """
    from app.config import DATA_DIR

    real_dir = (DATA_DIR / "logs").resolve()
    assert run_trace.TRACE_DIR.resolve() != real_dir, "TRACE_DIR 仍指向真实 logs 目录"
    assert real_dir not in run_trace.TRACE_DIR.resolve().parents
    assert run_trace.TRACE_FILE.resolve() != (real_dir / "agent-trace.jsonl").resolve()
    assert real_dir not in run_trace.TRACE_FILE.resolve().parents
