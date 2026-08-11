"""完整快照与反事实剧情实验室路由。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import scenario_lab

router = APIRouter()


class SnapshotRequest(BaseModel):
    output_dir: str
    repo_id: str
    turn: int = 0
    label: str = ""
    dedupe_key: str = ""


class ForkRequest(BaseModel):
    output_dir: str
    source_repo_id: str
    snapshot_id: str
    target_repo_id: str


class BranchInput(BaseModel):
    choice: str
    target_repo_id: str


class ExperimentRequest(BaseModel):
    output_dir: str
    source_repo_id: str
    snapshot_id: str
    branches: list[BranchInput] = Field(min_length=1, max_length=3)
    rounds: int = Field(default=2, ge=2, le=5)


class SelectRequest(BaseModel):
    target_repo_id: str


@router.post("/snapshots")
def create_snapshot(req: SnapshotRequest):
    try:
        return scenario_lab.create_snapshot(
            req.output_dir, req.repo_id, turn=req.turn, label=req.label,
            dedupe_key=req.dedupe_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/snapshots")
def list_snapshots(repo_id: str):
    return {"items": scenario_lab.list_snapshots(repo_id)}


@router.post("/fork")
def fork_snapshot(req: ForkRequest):
    try:
        return scenario_lab.fork_snapshot(
            req.output_dir, req.source_repo_id, req.snapshot_id, req.target_repo_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/experiments")
def create_experiment(req: ExperimentRequest):
    try:
        return scenario_lab.create_experiment(
            req.output_dir, req.source_repo_id, req.snapshot_id,
            [branch.model_dump() for branch in req.branches], rounds=req.rounds,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/experiments/{experiment_id}/select")
def select_branch(experiment_id: str, req: SelectRequest):
    try:
        return scenario_lab.select_branch(experiment_id, req.target_repo_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
