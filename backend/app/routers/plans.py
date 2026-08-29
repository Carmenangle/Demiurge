"""Autopilot 计划任务端点：提交/查询/批准/跳过/重试/取消/配方（路由薄，语义在 plan_tasks）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import plan_tasks, repo_meta
from app.services.structured_contracts import GenerationPlan

router = APIRouter()


def _trusted_output_dir(output_dir: str) -> str:
    """作品根必须来自后端配置真源，禁止客户端指定任意目录（路径域校验的地基）。"""
    truth = repo_meta.output_dir_from_state()
    if not truth or output_dir != truth:
        raise HTTPException(status_code=400, detail="output_dir 必须是当前配置的仓库文件夹根路径")
    return output_dir


class PlanSubmitRequest(BaseModel):
    plan: dict
    output_dir: str
    repo_id: str = ""
    configured_models: list[str] = []


@router.post("/submit")
def submit_plan(req: PlanSubmitRequest) -> dict:
    try:
        plan = GenerationPlan.model_validate(req.plan)
    except Exception as exc:  # noqa: BLE001 - 统一 400 语义
        raise HTTPException(status_code=400, detail=f"计划格式非法：{exc}") from exc
    try:
        return plan_tasks.submit_task(
            plan, output_dir=_trusted_output_dir(req.output_dir), repo_id=req.repo_id,
            configured_models=set(req.configured_models))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_plans(output_dir: str = "", repo_id: str = "", limit: int = 30) -> list[dict]:
    return plan_tasks.list_tasks(output_dir=output_dir, repo_id=repo_id, limit=limit)


@router.get("/{task_id}")
def get_plan(task_id: str) -> dict:
    task = plan_tasks.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="计划任务不存在")
    return task


class ApproveRequest(BaseModel):
    approved_by: str = "user"


@router.post("/{task_id}/approve")
def approve_plan(task_id: str, req: ApproveRequest | None = None) -> dict:
    try:
        return plan_tasks.approve_task(
            task_id, approved_by=(req.approved_by if req else "user"))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/steps/{step_id}/skip")
def skip_plan_step(task_id: str, step_id: str) -> dict:
    if not plan_tasks.skip_step(task_id, step_id):
        raise HTTPException(status_code=400, detail="步骤不存在或正在执行")
    return {"ok": True}


@router.post("/{task_id}/steps/{step_id}/retry")
def retry_plan_step(task_id: str, step_id: str) -> dict:
    if not plan_tasks.retry_step(task_id, step_id):
        raise HTTPException(status_code=400, detail="步骤不存在或正在执行")
    return {"ok": True}


@router.post("/{task_id}/cancel")
def cancel_plan(task_id: str) -> dict:
    return {"ok": plan_tasks.cancel_task(task_id)}


class RecipeRequest(BaseModel):
    name: str = ""


@router.post("/{task_id}/recipe")
def save_recipe(task_id: str, req: RecipeRequest | None = None) -> dict:
    try:
        return plan_tasks.save_recipe(task_id, name=req.name if req else "")
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/recipes/all")
def list_recipes() -> dict:
    return plan_tasks.list_recipes()


class RecipeInstantiateRequest(BaseModel):
    output_dir: str
    repo_id: str = ""
    param_overrides: dict[str, dict] = {}


@router.post("/recipes/{recipe_id}/instantiate")
def instantiate_recipe(recipe_id: str, req: RecipeInstantiateRequest) -> dict:
    try:
        return plan_tasks.instantiate_recipe(
            recipe_id, output_dir=_trusted_output_dir(req.output_dir), repo_id=req.repo_id,
            param_overrides=req.param_overrides)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
