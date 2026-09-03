"""P2/P3/P4 计划执行器单测：执行闭环 / 失败隔离 / Doom Loop / 审批与配额闸门 /
幂等 / 配方 / 进度发布。同步驱动（直接 _claim_next + _run_task），无线程竞态。"""
from __future__ import annotations

import json
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


def test_full模式自动签通配租约免审批(store, fake_capabilities, monkeypatch):
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_FULL)
    plan = _plan([PlanStep(id="s1", operation="test.batch", params={"template_id": "t1"})])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                                     configured_models={"chat", "image"})["task_id"]
    task = plan_tasks.get_task(task_id)
    assert task["lease_id"]  # 提交即租约，无需 approve_task
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    assert _wait_done(store, task_id)["status"] == "done"
    assert fake_capabilities[-1]["op"] == "batch"


def test_full模式doc_create_repo端到端(store, monkeypatch):
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_FULL)
    out_dir = str(store["path"].parent)
    plan = _plan([PlanStep(id="s1", operation="doc.create_repo",
                           params={"rel_path": "notes/里程碑.md",
                                   "content": "# 里程碑\n\n已落地两档访问标准"})])
    task_id = plan_tasks.submit_task(plan, output_dir=out_dir,
                                     configured_models={"chat", "image"})["task_id"]
    assert plan_tasks.get_task(task_id)["lease_id"]
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    task = _wait_done(store, task_id)
    assert task["status"] == "done"
    from pathlib import Path
    written = Path(out_dir).joinpath("docs", "notes", "里程碑.md")
    assert written.read_text(encoding="utf-8") == "# 里程碑\n\n已落地两档访问标准"


def test_write_loop同一文件写超限被阻断(store, monkeypatch):
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_FULL)
    target = store["path"].parent / "loop.txt"
    plan = _plan([
        PlanStep(id="s1", operation="file.write_text",
                 params={"path": str(target), "content": "一", "overwrite": True}),
        PlanStep(id="s2", operation="file.write_text",
                 params={"path": str(target), "content": "二", "overwrite": True}),
        PlanStep(id="s3", operation="file.write_text",
                 params={"path": str(target), "content": "三", "overwrite": True}),
        PlanStep(id="s4", operation="file.write_text",
                 params={"path": str(target), "content": "四", "overwrite": True}),
    ])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                                     configured_models={"chat", "image"})["task_id"]
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    task = plan_tasks.get_task(task_id)
    assert task["status"] == "blocked"
    assert task["steps"][0]["status"] == "done"
    assert task["steps"][3]["status"] == "blocked"
    assert "write_loop" in task["steps"][3]["last_error"]


def test_approval模式默认仍需审批(store, fake_capabilities, monkeypatch):
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_APPROVAL)
    plan = _plan([PlanStep(id="s1", operation="test.batch", params={"template_id": "t1"})])
    task_id = plan_tasks.submit_task(plan, output_dir=str(store["path"].parent),
                                     configured_models={"chat", "image"})["task_id"]
    assert plan_tasks.get_task(task_id)["lease_id"] == ""
    plan_tasks._run_task(plan_tasks._claim_next(), threading.Event())
    assert plan_tasks.get_task(task_id)["status"] == "awaiting_approval"


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


def test_list_tasks聚合终态任务_未终态全显示(store):
    now = int(time.time() * 1000)
    rows = [
        ("open1", "待审批的批量计划", "awaiting_approval", now),
        ("done1", "对比验收图 [1788073707]", "done", now - 1000),
        ("done2", "对比验收图 [1788069937]", "done", now - 2000),
        ("done3", "对比验收图", "done", now - 3000),
        ("done4", "对比验收图", "done", now - 4000),
    ]
    with plan_tasks.get_connection() as connection:
        for task_id, intent, status, created in rows:
            connection.execute(
                "insert into plan_tasks (id, repo_id, output_dir, intent, plan_json,"
                " content_hash, status, created_at, updated_at) values (?,?,?,?,?,?,?,?,?)",
                (task_id, "work", "", intent, "{}", task_id, status, created, created))
    tasks = plan_tasks.list_tasks()
    by_status = [t for t in tasks if t["status"] == "awaiting_approval"]
    done_group = [t for t in tasks if t["status"] == "done"]
    assert len(by_status) == 1 and by_status[0]["merged_count"] == 1
    # 后台活动只显示未终态；已终态任务不占活动面板
    assert done_group == []


def test_dump_task带图片级进度():
    row = {"id": "t1", "repo_id": "work", "output_dir": "", "intent": "批量生成 14 套图",
           "status": "running", "error": "", "lease_id": "", "content_hash": "h",
           "created_at": 0, "updated_at": 0}
    steps = [
        {"seq": 0, "step_id": "s1", "operation": "workflow.list_templates", "status": "done",
         "attempts": 0, "last_error": "", "params_json": "{}", "outputs_json": "{}"},
        {"seq": 1, "step_id": "s2", "operation": "workflow.submit_batch", "status": "done",
         "attempts": 0, "last_error": "",
         "params_json": json.dumps({"variants": [{"name": "套" + str(i)} for i in range(14)]},
                                    ensure_ascii=False),
         "outputs_json": "{}"},
        {"seq": 2, "step_id": "s3", "operation": "media.collect_comfy_outputs", "status": "done",
         "attempts": 0, "last_error": "", "params_json": "{}",
         "outputs_json": json.dumps({"collected": 5}, ensure_ascii=False)},
    ]
    task = plan_tasks._dump_task(row, steps)
    assert task["images_total"] == 14 and task["images_done"] == 5
    assert task["progress"] == "3/3 步 · 图 5/14"


def test_进度发布到task_progress_store(store, fake_capabilities):
    plan = _plan([PlanStep(id="s1", operation="test.echo", params={"a": "1"})])
    task_id = _submit_and_run(store, plan)
    _wait_done(store, task_id)
    entry = store["progress"].get(task_id)
    assert entry is not None
    assert entry["status"] == "done" and entry["progress"] == "1/1 步"


# ── 固化流程预设（fabric 轨迹 → 草稿配方 → 保留/重放，2026-09-03）──────────────

def _fabric_steps(root) -> list[dict]:
    """模拟一次成功的自由循环轨迹：读穿搭文档 → 写套装文档（含失败步与环境参数）。"""
    from pathlib import Path as _Path

    root = str(root)
    return [
        {"tool": "file.read_text", "params": {"path": str(_Path(root) / "时尚穿搭.md")}, "ok": True},
        {"tool": "doc.create_repo",
         "params": {"base": str(root), "repo_id": "work", "output_dir": str(root),
                    "rel_path": "套装/固化.md", "content": "# 四季套装"},
         "ok": True},
        {"tool": "file.read_text", "params": {"path": "D:/不存在.md"}, "ok": False},
    ]


def test_自由循环轨迹固化为草稿配方(store):
    root = str(store["path"].parent)
    recipe = plan_tasks.solidify_steps(
        intent="看图反推外貌生成套装文档", steps=_fabric_steps(root),
        output_dir=root, name="套装文档流程")
    assert recipe["status"] == "draft" and recipe["origin"] == "fabric"
    assert recipe["name"] == "套装文档流程"
    plan = recipe["plan"]
    assert [s["operation"] for s in plan["steps"]] == ["file.read_text", "doc.create_repo"]
    # 环境属主参数剥离：重放时由 submit_task 重新归一注入
    doc_params = plan["steps"][1]["params"]
    assert not ({"base", "repo_id", "output_dir"} & set(doc_params))
    assert plan["approval_required"] == ["doc.create_repo"]
    assert plan["budgets"]["max_gpu_tasks"] == 0
    assert plan["repo_id"] == ""


def test_纯只读轨迹不固化(store):
    with pytest.raises(ValueError, match="不固化"):
        plan_tasks.solidify_steps(
            intent="随便看看", steps=[
                {"tool": "file.read_text", "params": {"path": "D:/x.md"}, "ok": True}],
            output_dir=str(store["path"].parent))


def test_草稿配方不能直接重放_保留后可(store):
    root = str(store["path"].parent)
    recipe = plan_tasks.solidify_steps(
        intent="固化重放链路", steps=_fabric_steps(root), output_dir=root)
    with pytest.raises(ValueError, match="草稿"):
        plan_tasks.instantiate_recipe(recipe["id"], output_dir=root, repo_id="work")
    plan_tasks.keep_recipe(recipe["id"])
    submitted = plan_tasks.instantiate_recipe(recipe["id"], output_dir=root, repo_id="work")
    task = plan_tasks.get_task(submitted["task_id"])
    assert task is not None and task["intent"].startswith("[配方:固化重放链路]")


def test_配方keep与delete(store):
    root = str(store["path"].parent)
    recipe = plan_tasks.solidify_steps(
        intent="保留删除链路", steps=_fabric_steps(root), output_dir=root)
    kept = plan_tasks.keep_recipe(recipe["id"])
    assert kept["status"] == "saved"
    assert plan_tasks.delete_recipe(recipe["id"]) == {"ok": True}
    with pytest.raises(LookupError):
        plan_tasks.keep_recipe(recipe["id"])
    with pytest.raises(LookupError):
        plan_tasks.delete_recipe(recipe["id"])


def test_重放计划内instantiate_recipe的output_dir由环境归一(store):
    root = str(store["path"].parent)
    recipe = plan_tasks.solidify_steps(
        intent="嵌套重放", steps=_fabric_steps(root), output_dir=root)
    plan_tasks.keep_recipe(recipe["id"])
    plan = _plan([PlanStep(id="s1", operation="plan.instantiate_recipe",
                           params={"recipe_id": recipe["id"]})], intent="编排里重放配方")
    out = plan_tasks.submit_task(plan, output_dir=root)
    task = plan_tasks.get_task(out["task_id"])
    params = task["steps"][0]["params"]
    assert params["output_dir"] == root and params["recipe_id"] == recipe["id"]


def test_instantiate_recipe能力注册为durable():
    cap = capability_registry.get("plan.instantiate_recipe")
    assert cap is not None
    assert cap.side_effect_level == "durable"
    assert cap.handler == "app.services.capability_handlers:instantiate_recipe"
    assert "recipe_id" in cap.params_schema["required"]


def test_instantiate_recipe_handler拒绝非真源output_dir():
    # handler 层等值校验：模型/客户端不得借能力指定任意目录（submit 归一之外的兜底）
    with pytest.raises(ValueError, match="仓库根目录"):
        capability_handlers.instantiate_recipe("whatever", output_dir=r"D:\别的目录")


def test_配方keep与delete路由端点(monkeypatch):
    from fastapi import HTTPException

    from app.routers import plans as plans_router

    progress: dict = {}

    class _FakeProgress:
        @staticmethod
        def load(namespace):
            return dict(progress)

        @staticmethod
        def save(namespace, tasks, limit=100):
            progress.clear()
            progress.update(tasks)

    monkeypatch.setattr(plan_tasks, "task_progress_store", _FakeProgress)
    root = "D:/tmp/固化路由"
    recipe = plan_tasks.solidify_steps(intent="路由端点", steps=_fabric_steps(root),
                                       output_dir=root)
    assert plans_router.keep_recipe(recipe["id"])["status"] == "saved"
    assert plans_router.delete_recipe(recipe["id"]) == {"ok": True}
    with pytest.raises(HTTPException) as ei:
        plans_router.delete_recipe(recipe["id"])
    assert ei.value.status_code == 404
