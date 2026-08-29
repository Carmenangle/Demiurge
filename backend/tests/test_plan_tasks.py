"""P2/P3/P4 计划执行器单测：执行闭环 / 失败隔离 / Doom Loop / 审批与配额闸门 /
幂等 / 配方 / 进度发布。同步驱动（直接 _claim_next + _run_task），无线程竞态。"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from app.services import capability_handlers, capability_registry, capability_sandbox, plan_tasks
from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep


SCHEMA = """
create table plan_tasks (
    id text primary key, repo_id text not null default '', output_dir text not null default '',
    intent text not null default '', plan_json text not null, content_hash text not null,
    status text not null, lease_id text not null default '', error text not null default '',
    result_json text not null default '', created_at integer not null, updated_at integer not null,
    worker_id text not null default '', lease_expires_at integer not null default 0
);
create table plan_task_steps (
    task_id text not null, seq integer not null, step_id text not null, operation text not null,
    params_json text not null default '{}', inputs_from_json text not null default '[]',
    outputs_json text not null default '{}', status text not null default 'pending',
    attempts integer not null default 0, last_error text not null default '',
    updated_at integer not null, primary key (task_id, seq)
);
"""


def _connection_factory(path):
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "tasks.db"
    with _connection_factory(path)() as connection:
        connection.executescript(SCHEMA)
    monkeypatch.setattr(plan_tasks, "get_connection", _connection_factory(path))
    progress: dict[str, dict] = {}

    class _FakeProgress:
        @staticmethod
        def load(namespace):
            return dict(progress)

        @staticmethod
        def save(namespace, tasks, limit=100):
            progress.clear()
            progress.update(tasks)

    monkeypatch.setattr(plan_tasks, "task_progress_store", _FakeProgress)
    capability_sandbox._reset_for_tests()
    yield {"path": path, "progress": progress}
    capability_sandbox._reset_for_tests()


def _plan(steps, *, intent="测试计划", max_gpu=8):
    from app.services.plan_tasks import _step_level
    return GenerationPlan(
        intent=intent, repo_id="work", budgets=PlanBudgets(
            max_steps=max(1, len(steps)), max_gpu_tasks=max_gpu, max_llm_calls=4),
        steps=steps,
        approval_required=sorted({s.operation for s in steps
                                  if _step_level(s.operation) in ("durable", "expensive")}))


def _submit_and_run(store, plan, *, run_times=1):
    out = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                                 configured_models={"chat", "image"})
    for _ in range(run_times):
        task = plan_tasks._claim_next()
        if task is None:
            break
        plan_tasks._run_task(task, threading.Event())
    return out["task_id"]


def _wait_done(store, task_id, timeout=5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = plan_tasks.get_task(task_id)
        if task and task["status"] in plan_tasks.TASK_TERMINAL:
            return task
        time.sleep(0.05)
    return plan_tasks.get_task(task_id)


# ── 测试能力注册 ─────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_capabilities(monkeypatch):
    calls: list[dict] = []

    def echo_handler(**params):
        calls.append({"op": "echo", "params": params})
        return {"echoed": params, "out_key": "v1"}

    def boom_handler(**params):
        calls.append({"op": "boom", "params": params})
        raise RuntimeError("上游 502")

    flaky = {"used": False}

    def batch_handler(**params):
        calls.append({"op": "batch", "params": params})
        if params.get("template_id") == "t-fail":
            raise RuntimeError("GPU 提交失败")
        if params.get("template_id") == "t-flaky" and not flaky["used"]:
            flaky["used"] = True
            raise RuntimeError("首次超时")
        return {"prompt_id": "p-1"}

    monkeypatch.setattr(capability_handlers, "echo_handler", echo_handler, raising=False)
    monkeypatch.setattr(capability_handlers, "boom_handler", boom_handler, raising=False)
    monkeypatch.setattr(capability_handlers, "batch_handler", batch_handler, raising=False)
    capability_registry.register(capability_registry.Capability(
        operation="test.echo", category="comfyui", description="测试回显",
        params_schema={"type": "object", "properties": {"a": {"type": "string"},
                                                        "b": {"type": "string"},
                                                        "out_key": {"type": "string"}}},
        side_effect_level="readonly", channel="sync",
        handler="app.services.capability_handlers:echo_handler"))
    capability_registry.register(capability_registry.Capability(
        operation="test.boom", category="comfyui", description="必败测试",
        params_schema={"type": "object", "properties": {}},
        side_effect_level="readonly", channel="sync",
        handler="app.services.capability_handlers:boom_handler"))
    capability_registry.register(capability_registry.Capability(
        operation="test.batch", category="comfyui", description="烧卡测试",
        params_schema={"type": "object", "properties": {"template_id": {"type": "string"}}},
        needs_model="image", side_effect_level="expensive", channel="queue",
        handler="app.services.capability_handlers:batch_handler"))
    yield calls
    for op in ("test.echo", "test.boom", "test.batch"):
        capability_registry._REGISTRY.pop(op, None)


# ── P2 执行闭环 ──────────────────────────────────────────────────────────────

def test_readonly计划同步执行完成且inputs传递(store, fake_capabilities):
    plan = _plan([
        PlanStep(id="s1", operation="test.echo", params={"a": "1"}, outputs=["out_key"]),
        PlanStep(id="s2", operation="test.echo", params={"b": "2"}, inputs_from=["s1.out_key"]),
    ])
    task_id = _submit_and_run(store, plan)
    task = _wait_done(store, task_id)
    assert task["status"] == "done"
    assert all(s["status"] == "done" for s in task["steps"])
    # inputs_from 点引用把 s1 产出并入 s2 缺省参数
    s2_call = fake_capabilities[-1]
    assert s2_call["op"] == "echo" and s2_call["params"] == {"b": "2", "out_key": "v1"}


def test_幂等_同hash拒绝重复提交(store, fake_capabilities):
    plan = _plan([PlanStep(id="s1", operation="test.echo", params={"a": "1"})])
    first = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                                   configured_models={"chat", "image"})
    second = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                                    configured_models={"chat", "image"})
    assert first["deduped"] is False and second["deduped"] is True
    assert second["task_id"] == first["task_id"]
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    assert _wait_done(store, first["task_id"])["status"] == "done"


def test_失败隔离_单步失败其余blocked任务partial(store, fake_capabilities):
    plan = _plan([
        PlanStep(id="s1", operation="test.boom"),
        PlanStep(id="s2", operation="test.echo", params={"a": "1"}),
    ])
    task_id = _submit_and_run(store, plan)
    task = _wait_done(store, task_id)
    assert task["status"] == "partial"          # 防 premature completion：不是 done
    assert task["steps"][0]["status"] == "failed"
    assert task["steps"][1]["status"] == "blocked"


def test_doom_loop_连败两次blocked禁自动重试(store, fake_capabilities):
    plan = _plan([PlanStep(id="s1", operation="test.boom")])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                           configured_models={"chat", "image"})["task_id"]
    # 第一次失败：attempts=1，任务 partial 可由用户 retry
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    assert plan_tasks.get_task(task_id)["steps"][0]["attempts"] == 1
    # 用户 retry 不清零连败计数 → 第二次失败即 Doom Loop blocked
    assert plan_tasks.retry_step(task_id, "s1") is True
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    task = plan_tasks.get_task(task_id)
    assert task["steps"][0]["status"] == "blocked"
    assert "doom_loop" in task["steps"][0]["last_error"]
    assert task["status"] == "blocked"


# ── P3 审批与配额 ────────────────────────────────────────────────────────────

def test_expensive无租约blocked批准后继续执行(store, fake_capabilities):
    plan = _plan([
        PlanStep(id="s1", operation="test.batch", params={"template_id": "t1"}),
    ])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                           configured_models={"chat", "image"})["task_id"]
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    task = plan_tasks.get_task(task_id)
    assert task["status"] == "awaiting_approval"
    assert task["steps"][0]["status"] == "blocked"
    assert "needs_approval" in task["steps"][0]["last_error"]

    # 批准 = capability_sandbox 一次性租约
    approved = plan_tasks.approve_task(task_id)
    assert approved["lease_id"]
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    task = _wait_done(store, task_id)
    assert task["status"] == "done"
    assert fake_capabilities[-1]["op"] == "batch"


def test_租约撤销后expensive再次被拦(store, fake_capabilities):
    plan = _plan([PlanStep(id="s1", operation="test.batch", params={"template_id": "t1"})])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                           configured_models={"chat", "image"})["task_id"]
    plan_tasks.approve_task(task_id)
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    assert plan_tasks.get_task(task_id)["status"] == "done"

    # 第二个 expensive 计划复用同 subject 前先撤销租约 → 安全优先重新批准
    capability_sandbox._reset_for_tests()
    plan2 = _plan([PlanStep(id="s1", operation="test.batch", params={"template_id": "t2"})])
    task2 = plan_tasks.submit_task(plan2, output_dir=str(store["path"].parent),
                            configured_models={"chat", "image"})["task_id"]
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    assert plan_tasks.get_task(task2)["status"] == "awaiting_approval"


def test_配额超限expensive步骤blocked(store, fake_capabilities):
    # 校验器拦「声明步数>预算」；运行时配额拦「重试等实际消耗超预算」
    plan = _plan([
        PlanStep(id="s1", operation="test.batch", params={"template_id": "t-flaky"}),
        PlanStep(id="s2", operation="test.batch", params={"template_id": "t1"}),
    ], max_gpu=2)
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                           configured_models={"chat", "image"})["task_id"]
    plan_tasks.approve_task(task_id)
    # run1：s1 首次失败（消耗 1），s2 blocked
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    assert plan_tasks.get_task(task_id)["steps"][0]["status"] == "failed"
    # retry s1：成功（再消耗 1，共 2）→ s2 运行时配额超限 blocked
    assert plan_tasks.retry_step(task_id, "s1") is True
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    task = plan_tasks.get_task(task_id)
    assert task["steps"][0]["status"] == "done"
    assert task["steps"][1]["status"] == "blocked"
    assert "quota_exceeded" in task["steps"][1]["last_error"]
    assert task["status"] == "blocked"


def test_取消queued任务(store, fake_capabilities):
    plan = _plan([PlanStep(id="s1", operation="test.echo", params={"a": "1"})])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                           configured_models={"chat", "image"})["task_id"]
    assert plan_tasks.cancel_task(task_id) is True
    assert plan_tasks.get_task(task_id)["status"] == "cancelled"


# ── P4 配方与进度 ────────────────────────────────────────────────────────────

def test_配方固化与实例化(store, fake_capabilities):
    plan = _plan([PlanStep(id="s1", operation="test.echo", params={"a": "1"})])
    task_id = _submit_and_run(store, plan)
    _wait_done(store, task_id)

    recipe = plan_tasks.save_recipe(task_id, name="回显配方")
    assert recipe["name"] == "回显配方"
    assert recipe["id"] in plan_tasks.list_recipes()

    inst = plan_tasks.instantiate_recipe(
        recipe["id"], output_dir=str(store["path"].parent),
        param_overrides={"s1": {"a": "覆盖值"}})
    assert inst["deduped"] is False   # 参数不同 → hash 不同 → 允许重投
    task = plan_tasks.get_task(inst["task_id"])
    assert task["steps"][0]["params"]["a"] == "覆盖值"


def test_进度发布到task_progress_store(store, fake_capabilities):
    plan = _plan([PlanStep(id="s1", operation="test.echo", params={"a": "1"})])
    task_id = _submit_and_run(store, plan)
    _wait_done(store, task_id)
    entry = store["progress"].get(task_id)
    assert entry is not None
    assert entry["status"] == "done" and entry["progress"] == "1/1 步"
