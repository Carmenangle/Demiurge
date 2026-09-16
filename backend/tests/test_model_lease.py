from app.services import model_lease


def setup_function():
    model_lease._reset_for_tests(clear_releasers=True)


def teardown_function():
    model_lease._reset_for_tests(clear_releasers=True)


def test_comfy高优先级抢占视觉模型并调用释放adapter():
    released = []
    model_lease.register_releaser("visual_embedding", lambda: released.append(True) or True)
    visual = model_lease.acquire(
        "visual-index", "visual_embedding", priority=10, estimated_mib=4500,
    )
    comfy = model_lease.acquire("comfy-submit", "comfyui", priority=100, estimated_mib=0)

    assert visual is not None and comfy is not None
    assert released == [True]
    assert model_lease.status()["items"][0]["capability"] == "comfyui"


def test_后台视觉任务不能抢占正在运行的comfy任务():
    assert model_lease.acquire("comfy:1", "comfyui", priority=100) is not None
    assert model_lease.acquire("visual", "visual_embedding", priority=10) is None


def test_任务id重绑定和终态释放():
    lease = model_lease.acquire("submit", "comfyui", priority=100)
    assert lease is not None
    assert model_lease.rebind(lease.token, "comfyui:prompt-1")
    assert model_lease.release_owner("comfyui:prompt-1") == 1
    assert model_lease.status()["busy"] is False


def test_comfy队列允许多个同能力任务共存():
    first = model_lease.acquire("comfyui:1", "comfyui", priority=100)
    second = model_lease.acquire("comfyui:2", "comfyui", priority=100)
    assert first is not None and second is not None
    assert len(model_lease.status()["items"]) == 2


# ── 租约到期判定（P21：注入假钟，不 sleep、不后拨内部字段）───────────────────


def test_租约到期由注入假钟驱动_未到期在列到期被剪出():
    """P21：租约过期判定与 age/expires_in 展示全部走 now= 注入 —— 不必 sleep 等 TTL。"""
    t0 = 1000.0
    lease = model_lease.acquire("comfy-a", "comfyui", priority=100, ttl_seconds=60,
                                now=lambda: t0)
    assert lease is not None
    assert lease.expires_at == t0 + 60

    # 未到期（t0+59）：仍在列，剩余 1s
    st = model_lease.status(now=lambda: t0 + 59.0)
    assert [i["owner"] for i in st["leases"]] == ["comfy-a"]
    assert st["leases"][0]["expires_in_seconds"] == 1.0
    assert st["leases"][0]["age_seconds"] == 59.0

    # 已到期（t0+61）：status 的 _prune 用同一假钟把它剪出
    st2 = model_lease.status(now=lambda: t0 + 61.0)
    assert st2["leases"] == []
    assert st2["busy"] is False
