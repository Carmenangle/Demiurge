"""能力租约与用户演示流程技能端点。"""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import capability_sandbox, procedure_skills

router = APIRouter()


class Capability(BaseModel):
    operation: str
    path: str = ""
    domain: str = ""
    tool: str = ""


class GrantRequest(BaseModel):
    subject: str
    capabilities: list[Capability] = Field(min_length=1)
    ttl_seconds: int = Field(default=600, ge=1, le=86400)


class ProposeRequest(BaseModel):
    repo_id: str
    turn_id: str
    name: str = ""


class ReviewRequest(BaseModel):
    steps: list[dict[str, Any]]
    approved: bool = False


class RunRequest(BaseModel):
    lease_id: str = ""
    parameters: dict[str, Any] = {}


@router.post("/capabilities")
def grant(req: GrantRequest):
    return capability_sandbox.grant(
        req.subject, [item.model_dump() for item in req.capabilities], ttl_seconds=req.ttl_seconds,
    )


@router.delete("/capabilities/{lease_id}")
def revoke(lease_id: str):
    return {"ok": capability_sandbox.revoke(lease_id)}


@router.post("/propose")
def propose(req: ProposeRequest):
    return procedure_skills.propose(req.repo_id, req.turn_id, req.name)


@router.post("/{skill_id}/review")
def review(skill_id: str, req: ReviewRequest):
    try:
        return procedure_skills.review(skill_id, req.steps, approved=req.approved)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{skill_id}/dry-run")
def dry_run(skill_id: str, req: RunRequest):
    try:
        return procedure_skills.dry_run(skill_id, req.parameters)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{skill_id}/execute")
def execute(skill_id: str, req: RunRequest):
    try:
        return procedure_skills.execute(skill_id, req.lease_id, req.parameters)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
