"""统一后台任务注册表（2026-10-18）。

把长时操作（视觉索引 / 数据表补充 / 缩略图 / 反推 …）登记成可查询进度的 job，
前端「后台活动」面板轮询 GET /jobs 展示「X/N + 进度条」。此前这些操作是同步阻塞调用、
没有任何进度来源（各子系统各自为政：build/download/plan/chat 各有各的）。

内存态（dict）：进程重启即清空——后台任务本就与进程同生命周期，无需持久化。
线程安全：所有读写走一把锁；job 对象本身不跨线程共享可变引用（更新都过锁）。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

# 完成/失败后保留多久，供前端轮询看到收尾（秒）。
_KEEP_DONE_S = 120


@dataclass
class Job:
    id: str
    kind: str            # visual_index | thumbnails | sync_index | tag_infer | ...
    label: str           # 人类可读标题（面板显示）
    total: int = 0
    done: int = 0
    status: str = "running"   # running | done | failed | cancelled
    error: str = ""
    detail: str = ""          # 收尾摘要（如「成功 240 / 失败 5」），面板与完成 toast 显示
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "label": self.label,
            "total": self.total, "done": self.done, "status": self.status,
            "error": self.error, "detail": self.detail,
            "started_at": self.started_at, "updated_at": self.updated_at,
        }


_LOCK = threading.Lock()
_JOBS: dict[str, Job] = {}


def create(kind: str, label: str, total: int = 0) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label, total=max(0, total))
    with _LOCK:
        _JOBS[job.id] = job
    return job


def set_total(job_id: str, total: int) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.total = max(0, total)
            job.updated_at = time.time()


def tick(job_id: str, n: int = 1) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job.status == "running":
            job.done += max(0, n)
            job.updated_at = time.time()


def set_detail(job_id: str, detail: str) -> None:
    """写收尾摘要（成功/失败条数等）。供面板与前端完成 toast 展示。"""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.detail = str(detail or "")[:200]
            job.updated_at = time.time()


def finish(job_id: str, status: str = "done", error: str = "") -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        # 被取消的不改回 done（取消态优先）
        if job.status == "cancelled" and status == "done":
            job.updated_at = time.time()
            return
        job.status = status
        job.error = error
        job.updated_at = time.time()


def should_cancel(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        return bool(job and job.cancel)


def cancel(job_id: str) -> bool:
    """置取消信号（协作式：worker 在循环里查 should_cancel 后自行收尾）。不存在返回 False。"""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return False
        job.cancel = True
        if job.status == "running":
            job.status = "cancelled"
        job.updated_at = time.time()
        return True


def get(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return job.to_dict() if job else None


def snapshot() -> list[dict]:
    """列出仍在跑 + 保留期内的已结束任务（新的在前）。顺手清掉过期已结束项。"""
    now = time.time()
    out: list[dict] = []
    with _LOCK:
        expired = [
            jid for jid, j in _JOBS.items()
            if j.status != "running" and (now - j.updated_at) > _KEEP_DONE_S
        ]
        for jid in expired:
            _JOBS.pop(jid, None)
        for job in _JOBS.values():
            out.append(job.to_dict())
    out.sort(key=lambda d: d["started_at"], reverse=True)
    return out
