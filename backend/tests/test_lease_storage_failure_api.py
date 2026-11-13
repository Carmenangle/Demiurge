"""租约存储失败的 HTTP 语义（2026-11-05 T2）：坏盘面必须报 5xx，不许假 `ok:true`。

按路由函数直调断言（不起 TestClient，同 `test_appearance_router.py` 口径）。
锁三件事：① OSError → 503 且 detail 可诊断；② PermissionError / LookupError 的既有
语义不混进存储错误；③ 计划提交与批准端点同样把存储失败映射成 503。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import plans, procedures
from app.services import capability_sandbox, plan_tasks, repo_meta
from app.services.structured_contracts import GenerationPlan, PlanBudgets, PlanStep


@pytest.fixture()
def storage_down(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("注入：租约盘面写入失败")
    monkeypatch.setattr(capability_sandbox, "_save_persisted", _boom)
    yield


def _grant_request() -> procedures.GrantRequest:
    return procedures.GrantRequest(
        subject="s:route", capabilities=[procedures.Capability(operation="file.write_text")])


def test_签发租约端点写盘失败返回503(storage_down):
    with pytest.raises(HTTPException) as exc:
        procedures.grant(_grant_request())
    assert exc.value.status_code == 503
    assert "租约存储写入失败" in str(exc.value.detail)


def test_撤销租约端点写盘失败返回503(monkeypatch):
    lease = capability_sandbox.grant(
        "s:revoke-route", [{"operation": "file.write_text", "path": ""}],
        mode=capability_sandbox.ACCESS_APPROVAL)

    def _boom(*_args, **_kwargs):
        raise OSError("注入：租约盘面写入失败")
    monkeypatch.setattr(capability_sandbox, "_save_persisted", _boom)

    with pytest.raises(HTTPException) as exc:
        procedures.revoke(lease["id"])
    assert exc.value.status_code == 503
    assert "租约存储写入失败" in str(exc.value.detail)


def test_撤销不存在的租约仍返回ok_false_不当存储错误():
    assert procedures.revoke("lease-not-exist") == {"ok": False}


def test_执行端点的403语义不混入存储错误(monkeypatch):
    from app.services import procedure_skills

    def _denied(*_args, **_kwargs):
        raise PermissionError("能力租约不存在、已撤销或已过期")
    monkeypatch.setattr(procedure_skills, "execute", _denied)

    req = procedures.RunRequest(lease_id="l", parameters={})
    with pytest.raises(HTTPException) as exc:
        procedures.execute("skill-1", req)
    assert exc.value.status_code == 403


def test_计划批准端点写盘失败返回503(storage_down, monkeypatch):
    def _boom_approve(*_args, **_kwargs):
        raise OSError("注入：租约盘面写入失败")
    monkeypatch.setattr(plan_tasks, "approve_task", _boom_approve)

    with pytest.raises(HTTPException) as exc:
        plans.approve_plan("task-1")
    assert exc.value.status_code == 503
    assert "租约存储写入失败" in str(exc.value.detail)


def test_计划批准端点保留404与400语义(monkeypatch):
    def _missing(*_args, **_kwargs):
        raise LookupError("计划任务不存在")
    monkeypatch.setattr(plan_tasks, "approve_task", _missing)
    with pytest.raises(HTTPException) as exc:
        plans.approve_plan("task-x")
    assert exc.value.status_code == 404

    def _terminal(*_args, **_kwargs):
        raise ValueError("任务已终态，无需批准")
    monkeypatch.setattr(plan_tasks, "approve_task", _terminal)
    with pytest.raises(HTTPException) as exc2:
        plans.approve_plan("task-y")
    assert exc2.value.status_code == 400


def test_计划提交端点写盘失败返回503(storage_down, monkeypatch):
    monkeypatch.setattr(repo_meta, "works_root_violation", lambda _dir: "")

    def _boom_submit(*_args, **_kwargs):
        raise OSError("注入：租约盘面写入失败")
    monkeypatch.setattr(plan_tasks, "submit_task", _boom_submit)

    req = plans.PlanSubmitRequest(
        plan=GenerationPlan(intent="i", repo_id="work", budgets=PlanBudgets(),
                            steps=[PlanStep(id="s1", operation="file.write_text")]).model_dump(),
        output_dir="D:/works/whatever")
    with pytest.raises(HTTPException) as exc:
        plans.submit_plan(req)
    assert exc.value.status_code == 503
    assert "租约存储写入失败" in str(exc.value.detail)
