"""统一后台任务注册表（2026-10-18）：create/tick/finish/cancel/snapshot。"""
from app.services import jobs


def test_create_tick_finish():
    j = jobs.create("visual_index", "视觉索引", 157)
    assert j.total == 157 and j.done == 0 and j.status == "running"
    jobs.tick(j.id, 3)
    assert jobs.get(j.id)["done"] == 3
    jobs.finish(j.id, "done")
    assert jobs.get(j.id)["status"] == "done"


def test_set_total():
    j = jobs.create("thumbnails", "补缩略图")
    jobs.set_total(j.id, 42)
    assert jobs.get(j.id)["total"] == 42


def test_cancel_signal_and_status():
    j = jobs.create("sync_index", "同步", 10)
    assert jobs.should_cancel(j.id) is False
    assert jobs.cancel(j.id) is True
    assert jobs.should_cancel(j.id) is True
    assert jobs.get(j.id)["status"] == "cancelled"
    # 取消态优先：之后的 finish(done) 不能翻回 done
    jobs.finish(j.id, "done")
    assert jobs.get(j.id)["status"] == "cancelled"


def test_tick_ignored_after_finish():
    j = jobs.create("x", "X", 5)
    jobs.finish(j.id, "done")
    jobs.tick(j.id, 2)
    assert jobs.get(j.id)["done"] == 0  # 非 running 不再累加


def test_get_and_cancel_missing():
    assert jobs.get("no-such-job") is None
    assert jobs.cancel("no-such-job") is False


def test_snapshot_contains_created():
    a = jobs.create("x", "A", 1)
    b = jobs.create("y", "B", 2)
    ids = {item["id"] for item in jobs.snapshot()}
    assert a.id in ids and b.id in ids


def test_finish_failed_carries_error():
    j = jobs.create("x", "X", 3)
    jobs.finish(j.id, "failed", "模型未安装")
    snap = jobs.get(j.id)
    assert snap["status"] == "failed" and "模型未安装" in snap["error"]


def test_set_detail_records_summary():
    """收尾摘要（成功/失败拆分）要能被面板与前端 toast 读到。"""
    from app.services import jobs as j
    job = j.create("vlm_describe", "视觉描述", 10)
    assert j.get(job.id)["detail"] == ""
    j.set_detail(job.id, "成功 8 / 失败 2")
    assert j.get(job.id)["detail"] == "成功 8 / 失败 2"
    j.finish(job.id, "done")
    assert j.get(job.id)["detail"] == "成功 8 / 失败 2"


def test_set_detail_truncates_and_is_lenient():
    """超长摘要截断；任务已消失时静默（不抛）。"""
    from app.services import jobs as j
    job = j.create("x", "x", 0)
    j.set_detail(job.id, "长" * 500)
    assert len(j.get(job.id)["detail"]) <= 200
    j.set_detail("no-such-job", "abc")  # 不抛


def test_client_driven_job_lifecycle(monkeypatch):
    """前端驱动的 job 全链路：create → tick → finish(detail) → snapshot 能看到。

    这是单图/批量反推能进「后台活动」的前提（2026-10-19 用户实锤：反推面板无任务）。
    ⚠ 直接调路由函数、**不起 TestClient**：起 app 会跑 lifespan，把计划任务 worker
    等后台线程拉起来，抢走其它测试的任务状态（本次全量跑曾因此挂掉 6 个 plan_tasks）。
    """
    from app.routers import jobs as jobs_router
    monkeypatch.setattr(jobs, "_JOBS", {})

    created = jobs_router.create_job(jobs_router.JobCreateReq(
        kind="tag_infer", label="反推标签 · 服设", total=3))
    job_id = created["job_id"]

    jobs_router.tick_job(job_id, jobs_router.JobTickReq(n=2))
    snap = jobs.get(job_id)
    assert snap["kind"] == "tag_infer" and snap["done"] == 2 and snap["total"] == 3

    jobs_router.finish_job(job_id, jobs_router.JobFinishReq(
        status="done", detail="成功 3 / 失败 0"))
    listed = [j for j in jobs.snapshot() if j["id"] == job_id]
    assert listed and listed[0]["status"] == "done" and listed[0]["detail"] == "成功 3 / 失败 0"


def test_client_driven_job_rejects_unknown_kind():
    """kind 白名单：防止任意客户端往面板灌垃圾条目。"""
    from fastapi import HTTPException
    from app.routers import jobs as jobs_router
    try:
        jobs_router.create_job(jobs_router.JobCreateReq(kind="evil", label="x", total=1))
    except HTTPException as e:
        assert e.status_code == 400 and "不支持" in str(e.detail)
    else:  # pragma: no cover
        raise AssertionError("白名单外的 kind 必须 400")


def test_client_driven_job_unknown_id_404():
    """tick/finish 不存在的 id ⇒ 404（前端好判断「进程重启过，任务已丢」）。"""
    from fastapi import HTTPException
    from app.routers import jobs as jobs_router
    for call in (
        lambda: jobs_router.tick_job("nope", jobs_router.JobTickReq(n=1)),
        lambda: jobs_router.finish_job("nope", jobs_router.JobFinishReq(status="done")),
    ):
        try:
            call()
        except HTTPException as e:
            assert e.status_code == 404
        else:  # pragma: no cover
            raise AssertionError("不存在的 job id 必须 404")
