"""后台任务查询（2026-10-18）：前端「后台活动」面板轮询 GET /api/jobs 拿所有长时操作
（视觉索引/数据表补充/缩略图/反推…）的进度，展示 X/N 与进度条。

另提供**客户端驱动的 job 上报口**（create/tick/finish）：反推打标这类跑在浏览器里的
长操作（要读本机图、走前端配置的视觉模型）也登记进同一注册表，面板才能显示它的
进度与成败——否则「后台活动」对它完全瞎眼（2026-10-19 用户实锤：单图反推面板无任务）。
kind 走白名单，防止任意客户端往面板里灌垃圾条目。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import jobs

router = APIRouter()

# 允许前端登记的 kind（与 JOB_KIND_LABEL 一一对应；后端任务用的 kind 也在这里面）。
CLIENT_KINDS = {"tag_infer", "vlm_describe", "visual_index", "thumbnails", "sync_index"}


class JobCreateReq(BaseModel):
    kind: str = "tag_infer"
    label: str = ""
    total: int = 0


class JobTickReq(BaseModel):
    n: int = 1


class JobFinishReq(BaseModel):
    status: str = "done"        # done | failed | cancelled
    error: str = ""
    detail: str = ""


@router.get("")
def list_jobs() -> dict[str, object]:
    """列出进行中 + 保留期内的已结束任务（新的在前）。"""
    return {"jobs": jobs.snapshot()}


@router.get("/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("")
def create_job(req: JobCreateReq) -> dict[str, object]:
    """客户端驱动的 job：前端长操作（如单图/批量反推）拿它登记进度。"""
    if req.kind not in CLIENT_KINDS:
        raise HTTPException(status_code=400, detail=f"不支持的 kind：{req.kind}")
    job = jobs.create(req.kind, req.label or req.kind, req.total)
    return {"ok": True, "job_id": job.id}


@router.post("/{job_id}/tick")
def tick_job(job_id: str, req: JobTickReq) -> dict[str, object]:
    if jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    jobs.tick(job_id, req.n)
    return {"ok": True}


@router.post("/{job_id}/finish")
def finish_job(job_id: str, req: JobFinishReq) -> dict[str, object]:
    """收尾（可带 detail 摘要，如「成功 8 / 失败 2」）。"""
    if jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if req.detail:
        jobs.set_detail(job_id, req.detail)
    jobs.finish(job_id, req.status, req.error)
    return {"ok": True}


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, object]:
    """协作式取消：置信号，worker 在循环里查 should_cancel 后收尾。"""
    if not jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}
