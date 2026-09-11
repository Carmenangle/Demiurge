# -*- coding: utf-8 -*-
"""Model Lease Runtime v2 测试：显存估算、can_run 判断、evict_candidates、status 结构、队列估算。"""
from __future__ import annotations


from app.services import model_lease as ml


# ── 显存估算 ─────────────────────────────────────────────────────────────────


def test_estimate_vram_from_nodes():
    """高占用节点取最大，同类只算一次。"""
    nodes = ["CheckpointLoaderSimple", "KSampler", "VAEDecode", "SaveImage"]
    # CheckpointLoaderSimple=7000 + VAEDecode=2000 + SaveImage=100 ≈ 9100
    assert ml._estimate_vram_from_nodes(nodes) >= 9000
    # 重复同类只算一次
    dup = ["CheckpointLoaderSimple", "CheckpointLoaderSimple", "VAEDecode"]
    assert ml._estimate_vram_from_nodes(dup) == ml._estimate_vram_from_nodes(
        ["CheckpointLoaderSimple", "VAEDecode"]
    )


def test_estimate_vram_empty():
    assert ml._estimate_vram_from_nodes([]) == 0


# ── can_run 判断 ─────────────────────────────────────────────────────────────


def _device(available=8192, free=10000, total=24576, used=14576, probe_source="torch_cuda"):
    return ml.DeviceInfo(
        name="RTX 4090", total_mib=total, used_mib=used, free_mib=free,
        available_mib=available, probe_source=probe_source,
    )


def test_can_run_shared_ok():
    leases = {
        "t1": ml.Lease("t1", "rag", "text_embedding", 10, 500, 0, 999),
    }
    dev = _device(available=8192)
    ok, reason = ml.can_run("text_embedding", 1000, leases, dev)
    assert ok
    assert "共享" in reason


def test_can_run_shared_insufficient():
    leases = {
        "t1": ml.Lease("t1", "rag", "text_embedding", 10, 8000, 0, 999),
    }
    dev = _device(available=8192)
    ok, reason = ml.can_run("text_embedding", 2000, leases, dev)
    assert not ok
    assert "显存不足" in reason


def test_can_run_exclusive_blocked():
    leases = {
        "t1": ml.Lease("t1", "visual", "visual_embedding", 50, 4600, 0, 999),
    }
    dev = _device(available=8192)
    ok, reason = ml.can_run("visual_embedding", 4600, leases, dev)
    assert not ok
    assert "独占" in reason


def test_can_run_exclusive_idle():
    leases: dict[str, ml.Lease] = {}
    dev = _device(available=8192)
    ok, reason = ml.can_run("visual_embedding", 4600, leases, dev)
    assert ok


def test_can_run_no_gpu():
    dev = ml.DeviceInfo(probe_source="unavailable", probe_error="无 GPU")
    ok, reason = ml.can_run("text_embedding", 100, {}, dev)
    assert not ok
    assert "不可用" in reason


# ── evict_candidates ─────────────────────────────────────────────────────────


def test_evict_candidates_sorts_by_saving():
    leases = {
        "big": ml.Lease("big", "visual", "visual_embedding", 50, 4600, 0, 999),
        "small": ml.Lease("small", "rerank", "reranker", 40, 500, 0, 999),
    }
    dev = _device(free=4000, available=4000)
    cands = ml.evict_candidates("visual_embedding", 5000, leases, dev)
    # 只有 big（4600）释放后够 5000；small 释放后仍不够
    assert cands
    assert cands[0]["owner"] == "visual"
    assert cands[0]["saving_mib"] == 4600
    assert "释放" in cands[0]["eviction_reason"]


# ── status 结构 ──────────────────────────────────────────────────────────────


def test_status_structure():
    ml._reset_for_tests()
    ml.acquire("test-owner", "text_embedding", priority=10, estimated_mib=500, ttl_seconds=60)
    st = ml.status()
    # 原有 leases 结构保留
    assert st["leases"]
    assert st["leases"][0]["owner"] == "test-owner"
    assert st["leases"][0]["estimated_mib"] == 500
    # v2 新增 device 块
    assert "device" in st
    dev = st["device"]
    assert "total_mib" in dev and "free_mib" in dev and "available_mib" in dev
    assert "probe_source" in dev
    # v2 新增 queue 块
    assert "comfyui_queue" in st
    assert "running" in st["comfyui_queue"]
    assert "pending" in st["comfyui_queue"]
    ml._reset_for_tests()


def test_status_unavailable_gpu_still_works():
    """无 GPU 环境 status 仍返回完整结构（probe_error 说明原因）。"""
    ml._reset_for_tests()
    st = ml.status()
    assert "device" in st
    assert st["device"]["probe_source"] in ("torch_cuda", "nvidia_smi", "unavailable")
    if st["device"]["probe_source"] == "unavailable":
        assert st["device"]["probe_error"]
    ml._reset_for_tests()


# ── acquire 原行为保留 ───────────────────────────────────────────────────────


def test_acquire_shared_comfyui_allows_multiple():
    """comfyui 是共享 capability，多个 comfyui lease 可共存。"""
    ml._reset_for_tests()
    a = ml.acquire("comfy-a", "comfyui", priority=100, estimated_mib=7000, ttl_seconds=60)
    b = ml.acquire("comfy-b", "comfyui", priority=90, estimated_mib=2000, ttl_seconds=60)
    assert a is not None
    assert b is not None
    ml._reset_for_tests()


def test_acquire_priority_preempts_lower():
    """高优先级可抢占低优先级（调用 releaser）。"""
    ml._reset_for_tests(clear_releasers=True)
    released: list[str] = []

    def _release():
        released.append("called")
        return True

    ml.register_releaser("text_embedding", _release)
    low = ml.acquire("low-owner", "text_embedding", priority=10, estimated_mib=500, ttl_seconds=60)
    assert low is not None
    high = ml.acquire("high-owner", "visual_embedding", priority=100, estimated_mib=4600, ttl_seconds=60)
    assert high is not None
    assert released == ["called"]
    # 低优先级被移除
    st = ml.status()
    owners = [i["owner"] for i in st["leases"]]
    assert "high-owner" in owners
    assert "low-owner" not in owners
    ml._reset_for_tests(clear_releasers=True)
