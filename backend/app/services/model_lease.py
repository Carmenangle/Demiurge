"""Model Lease Runtime v2：真实 GPU 探测 + ComfyUI 队列可观测 + 抢占理由可解释。

v1 痛点：
- estimated_mib 纯估算，无法知道 ComfyUI/Ollama 实际占用多少
- status() 不含实际显存状态，无法判断"显存不足"是真是假
- comfyui capability 是 shared（同 capability 可共存），但实际显存是互斥的
- 无排队原因、卸载耗时、冷启动成本可解释性

v2 新增：
- device_probe()：torch.cuda + nvidia-smi 双通道探测实际 GPU 状态
- comfyui_queue_status()：拉取 ComfyUI /queue，透视运行中/等待中的任务
- status() 增强：追加 device/gpu/queue 字段，不破坏现有 items 结构
- can_run()：判断请求能否在当前显存下运行（不抢占）
- evict_candidates()：哪些 lease 可以被卸载，给出可解释的卸载成本
"""
from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable

from app.config import COMFYUI_BASE_URL


# ── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class Lease:
    token: str
    owner: str
    capability: str
    priority: int
    estimated_mib: int
    acquired_at: float
    expires_at: float


@dataclass
class DeviceInfo:
    """真实 GPU 状态（通过 torch.cuda 或 nvidia-smi 探测）。"""
    name: str = ""
    total_mib: int = 0
    used_mib: int = 0       # torch.cuda.memory_reserved()
    free_mib: int = 0       # total - used
    available_mib: int = 0  # free - safety_headroom
    driver: str = ""
    cuda_version: str = ""
    utilization_pct: int = 0  # nvidia-smi 读 GPU 利用率 %
    vram_reserved_by_lease: int = 0  # model_lease 持有的 estimated_mib 总和
    device_count: int = 0
    probe_error: str = ""   # 探测失败原因（人类可读）
    probe_source: str = ""   # "torch_cuda" | "nvidia_smi" | "unavailable"


@dataclass
class ComfyuiQueueItem:
    """ComfyUI 队列中单个任务。"""
    prompt_id: str
    priority: int = 0   # ComfyUI 队列优先级（0=pending, 1=running）
    estimated_mib: int = 0  # 估算显存（基于节点类型）
    # ComfyUI 队列不暴露 prompt 内容，此处从 /history 可读任务名/节点类型
    workflow_class_types: list[str] = field(default_factory=list)
    # 模型相关节点（用于推断显存占用级别）
    model_nodes: list[str] = field(default_factory=list)


@dataclass
class ComfyuiQueueStatus:
    """ComfyUI 队列全景。"""
    running: list[ComfyuiQueueItem] = field(default_factory=list)
    pending: list[ComfyuiQueueItem] = field(default_factory=list)
    total_estimated_mib: int = 0
    probe_error: str = ""


# ── GPU 显存估算表（用于无真实探测时） ────────────────────────────────────────

# 各节点类显存级别（保守估算 MiB），用于从队列任务节点类型估算显存占用
_VRAM_LEVELS: dict[str, int] = {
    # 极高占用
    "CheckpointLoader": 7000, "CheckpointLoaderSimple": 7000,
    "UNETLoader": 5000, "ModelMerge": 5000,
    "ModelMergeMultiplexer": 5000,
    # 高占用
    "DualCLIPLoader": 1000, "VAELoader": 800,
    "LoraLoaderModelOnly": 3000,
    "ControlNetApply": 1500, "ControlNetApplyAdvanced": 1500,
    "ControlNetLoader": 2000,
    "IPAdapterApply": 2000, "IPAdapterAdvanced": 2000,
    "ImageUpscaleWithModel": 2000,
    "VAEDecode": 2000, "VAEDecodeUsingTiles": 2500,
    "VAEEncode": 500, "VAEEncodeUsingTiles": 800,
    # 中占用
    "LoraLoader": 100, "StyleAlign": 500,
    "ModelSamplingContinuous": 500,
    # 低占用（采样/文本/图像 I/O）
    "KSampler": 0, "KSamplerAdvanced": 0,
    "CLIPTextEncode": 0, "CLIPSetLastLayer": 0,
    "EmptyLatentImage": 0, "LatentMultiply": 0,
    "SaveImage": 100, "PreviewImage": 100,
    "LoadImage": 50, "ImageScale": 100,
}


def _estimate_vram_from_nodes(class_types: list[str]) -> int:
    """从节点类型列表估算显存占用（同类取最大）。"""
    seen: dict[str, int] = {}
    for ct in class_types:
        mem = _VRAM_LEVELS.get(ct, 0)
        if ct not in seen or seen[ct] < mem:
            if ct not in seen:
                seen[ct] = 0
            if mem > seen[ct]:
                seen[ct] = mem
    return sum(seen.values())


# ── GPU 探测 ─────────────────────────────────────────────────────────────────

def device_probe() -> DeviceInfo:
    """探测真实 GPU 状态：torch.cuda 优先，nvidia-smi 兜底，全失败返回错误信息。"""
    info = DeviceInfo()
    # 1. torch.cuda
    try:
        import torch
        if torch.cuda.is_available():
            dev = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(dev)
            info.device_count = torch.cuda.device_count()
            info.name = props.name
            total_mib = props.total_memory // (1024 * 1024)
            reserved_mib = torch.cuda.memory_reserved(dev) // (1024 * 1024)
            free_mib = total_mib - reserved_mib
            safety_mib = min(512, int(total_mib * 0.02))  # 保留 2% 或 512MiB
            info.total_mib = total_mib
            info.used_mib = reserved_mib
            info.free_mib = free_mib
            info.available_mib = max(0, free_mib - safety_mib)
            info.cuda_version = torch.version.cuda or ""
            info.probe_source = "torch_cuda"
            return info
    except Exception as e:
        info.probe_error = f"torch.cuda 探测失败：{e}"

    # 2. nvidia-smi
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version",
             "--format=csv,noheader,nounits"],
            timeout=10, stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace").strip()
        if raw:
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) >= 5:
                info.name = parts[0]
                info.total_mib = int(parts[1])
                info.used_mib = int(parts[2])
                info.utilization_pct = int(parts[3])
                info.driver = parts[4]
                info.free_mib = info.total_mib - info.used_mib
                safety_mib = min(512, int(info.total_mib * 0.02))
                info.available_mib = max(0, info.free_mib - safety_mib)
                info.probe_source = "nvidia_smi"
                return info
    except FileNotFoundError:
        info.probe_error = "nvidia-smi 未找到（非 NVIDIA GPU 或未安装驱动）"
    except subprocess.TimeoutExpired:
        info.probe_error = "nvidia-smi 超时"
    except Exception as e:
        info.probe_error = f"nvidia-smi 探测失败：{e}"

    # 3. 完全不可用
    info.probe_source = "unavailable"
    return info


# ── ComfyUI 队列探测 ─────────────────────────────────────────────────────────

def comfyui_queue_status(comfyui_url: str) -> ComfyuiQueueStatus:
    """拉取 ComfyUI /queue，透视运行中/等待中的任务显存估算。"""
    status = ComfyuiQueueStatus()
    try:
        import requests
        from app.services.url_guard import validate_comfyui_url
        base = validate_comfyui_url(comfyui_url).rstrip("/")
        sess = requests.Session()
        sess.trust_env = False
        resp = sess.get(base + "/queue", timeout=5)
        resp.raise_for_status()
        q = resp.json()
    except Exception as e:
        status.probe_error = f"拉取队列失败：{e}"
        return status

    def _item(prompt_id: str, priority: int, prompt: dict) -> ComfyuiQueueItem:
        class_types = [str(v.get("class_type", "")) for v in prompt.values() if isinstance(v, dict)]
        model_nodes = [ct for ct in class_types if ct in _VRAM_LEVELS and _VRAM_LEVELS[ct] >= 1000]
        return ComfyuiQueueItem(
            prompt_id=prompt_id,
            priority=priority,
            estimated_mib=_estimate_vram_from_nodes(class_types),
            workflow_class_types=class_types[:8],  # 只保留前 8 个类型名（防泄漏）
            model_nodes=model_nodes,
        )

    for idx, item in enumerate(q.get("queue_running", []) or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            prompt_id = str(item[1].get("prompt_id", "")) if isinstance(item[1], dict) else str(item[1])
            prompt = item[1].get("prompt", {}) if isinstance(item[1], dict) else {}
            status.running.append(_item(prompt_id, 1, prompt))
    for idx, item in enumerate(q.get("queue_pending", []) or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            prompt_id = str(item[1].get("prompt_id", "")) if isinstance(item[1], dict) else str(item[1])
            prompt = item[1].get("prompt", {}) if isinstance(item[1], dict) else {}
            status.pending.append(_item(prompt_id, 0, prompt))

    status.total_estimated_mib = (
        sum(i.estimated_mib for i in status.running) +
        sum(i.estimated_mib for i in status.pending)
    )
    return status


# ── 可运行判断（不抢占） ───────────────────────────────────────────────────────

# 不同 capability 的显存共享规则（启发式）
_CAPABILITY_SHARED: dict[str, bool] = {
    "comfyui": True,      # ComfyUI 内部模型可共享显存池
    "text_embedding": True,  # 小模型（SentenceTransformer），可共存
    "visual_embedding": False,  # Qwen3-VL 4GB+，不共享
    "reranker": True,     # 小模型
}


def can_run(
    capability: str, estimated_mib: int,
    leases: dict[str, Lease], device: DeviceInfo,
) -> tuple[bool, str]:
    """判断请求能否在不抢占情况下运行。返回 (can_run, reason)。"""
    if not device.available_mib:
        return False, f"GPU 不可用（{device.probe_error or '无 GPU'}）"

    if capability in _CAPABILITY_SHARED and _CAPABILITY_SHARED[capability]:
        # 共享 capability：只要总量不超即可
        shared_used = sum(
            le.estimated_mib for le in leases.values()
            if le.capability in _CAPABILITY_SHARED and _CAPABILITY_SHARED[le.capability]
        )
        if shared_used + estimated_mib <= device.available_mib:
            return True, "共享池可用"
        return False, (
            f"共享池已用 {shared_used} MiB，可用 {device.available_mib} MiB，"
            f"需要 {estimated_mib} MiB，显存不足"
        )

    # 独占 capability：有其他独占 lease 则不能运行
    exclusive_active = [
        le for le in leases.values()
        if le.capability not in _CAPABILITY_SHARED or not _CAPABILITY_SHARED[le.capability]
    ]
    if exclusive_active:
        names = ", ".join(f"{le.owner}({le.estimated_mib}MiB)" for le in exclusive_active)
        return False, f"独占资源被占用：{names}"

    if estimated_mib > device.available_mib:
        return False, f"需要 {estimated_mib} MiB，可用 {device.available_mib} MiB，显存不足"
    return True, "独占资源空闲"


def evict_candidates(
    capability: str, estimated_mib: int,
    leases: dict[str, Lease], device: DeviceInfo,
) -> list[dict]:
    """返回可卸载的候选 lease 列表（按推荐顺序），含卸载理由和节省显存量。"""
    candidates = []
    for le in leases.values():
        if le.capability == capability:
            # 同 capability 的低优先级 lease 可卸载
            saving = le.estimated_mib
            if device.free_mib + saving >= estimated_mib:
                candidates.append({
                    "token": le.token, "owner": le.owner,
                    "priority": le.priority, "estimated_mib": le.estimated_mib,
                    "saving_mib": saving,
                    "eviction_reason": (
                        f"释放 {le.owner}（{saving} MiB）后可用 {device.free_mib + saving} MiB"
                    ),
                    "age_seconds": round(time.time() - le.acquired_at, 1),
                })
    # 按节省量降序（优先卸大块）
    from typing import cast
    candidates.sort(key=lambda x: cast(int, x["saving_mib"]), reverse=True)
    return candidates


# ── 原有逻辑（精简重写） ─────────────────────────────────────────────────────

_LOCK = RLock()
_LEASES: dict[str, Lease] = {}
_RELEASERS: dict[str, Callable[[], bool]] = {}


def register_releaser(capability: str, release: Callable[[], bool]) -> None:
    with _LOCK:
        _RELEASERS[capability] = release


def _prune(now: float | None = None) -> None:
    current = now if now is not None else time.time()
    for token, lease in list(_LEASES.items()):
        if lease.expires_at <= current:
            _LEASES.pop(token, None)


def acquire(owner: str, capability: str, *, priority: int,
            estimated_mib: int = 0, ttl_seconds: int = 1200) -> Lease | None:
    """获取加速器租约；高优先级可调用低优先级 Adapter 的释放回调。"""
    owner, capability = owner.strip(), capability.strip()
    if not owner or not capability:
        raise ValueError("租约 owner 和 capability 不能为空")
    with _LOCK:
        _prune()
        existing = next((lease for lease in _LEASES.values() if lease.owner == owner), None)
        if existing:
            existing.expires_at = time.time() + max(1, ttl_seconds)
            return existing
        conflicts = [
            lease for lease in _LEASES.values()
            if not (capability in _CAPABILITY_SHARED and lease.capability == capability)
        ]
        if any(lease.priority >= priority for lease in conflicts):
            return None
        releasers = {
            lease.capability: _RELEASERS.get(lease.capability) for lease in conflicts
        }
    for _capability, release in releasers.items():
        if release is not None:
            try:
                release()
            except Exception:
                pass
    with _LOCK:
        _prune()
        remaining_conflicts = [
            lease for lease in _LEASES.values()
            if not (capability in _CAPABILITY_SHARED and lease.capability == capability)
        ]
        if any(lease.priority >= priority for lease in remaining_conflicts):
            return None
        for token in [lease.token for lease in remaining_conflicts if lease.priority < priority]:
            _LEASES.pop(token, None)
        now = time.time()
        lease = Lease(
            uuid.uuid4().hex, owner, capability, int(priority), max(0, int(estimated_mib)),
            now, now + max(1, int(ttl_seconds)),
        )
        _LEASES[lease.token] = lease
        return lease


def rebind(token: str, owner: str) -> bool:
    with _LOCK:
        lease = _LEASES.get(token)
        if lease is None or not owner.strip():
            return False
        lease.owner = owner.strip()
        return True


def release(token: str) -> bool:
    with _LOCK:
        return _LEASES.pop(token, None) is not None


def release_owner(owner: str) -> int:
    with _LOCK:
        tokens = [token for token, lease in _LEASES.items() if lease.owner == owner]
        for token in tokens:
            _LEASES.pop(token, None)
        return len(tokens)


def status() -> dict[str, object]:
    """完整状态含三块：leases（原有）、device（真实 GPU 探测）、queue（ComfyUI 队列）。"""
    with _LOCK:
        _prune()
        now = time.time()
        leases_out = [
            {
                "owner": le.owner, "capability": le.capability,
                "priority": le.priority, "estimated_mib": le.estimated_mib,
                "age_seconds": round(now - le.acquired_at, 3),
                "expires_in_seconds": round(max(0.0, le.expires_at - now), 3),
            }
            for le in sorted(_LEASES.values(), key=lambda x: -x.priority)
        ]
        total_lease_mib = sum(le.estimated_mib for le in _LEASES.values())

    # GPU 真实探测（独立，不占租约锁）
    device = device_probe()
    device.vram_reserved_by_lease = total_lease_mib

    # ComfyUI 队列探测（独立，失败不阻断）
    comfy_status = comfyui_queue_status(COMFYUI_BASE_URL)
    # 队列估算显存加入 device info
    device.vram_reserved_by_lease += comfy_status.total_estimated_mib

    return {
        "leases": leases_out,
        "items": leases_out,  # 向后兼容别名
        "busy": bool(leases_out),
        "device": {
            "name": device.name,
            "total_mib": device.total_mib,
            "used_mib": device.used_mib,
            "free_mib": device.free_mib,
            "available_mib": device.available_mib,
            "vram_reserved_by_lease": device.vram_reserved_by_lease,
            "driver": device.driver,
            "cuda_version": device.cuda_version,
            "utilization_pct": device.utilization_pct,
            "device_count": device.device_count,
            "probe_source": device.probe_source,
            "probe_error": device.probe_error,
        },
        "comfyui_queue": {
            "running": [
                {"prompt_id": i.prompt_id, "estimated_mib": i.estimated_mib,
                 "workflow_class_types": i.workflow_class_types}
                for i in comfy_status.running
            ],
            "pending": [
                {"prompt_id": i.prompt_id, "estimated_mib": i.estimated_mib,
                 "workflow_class_types": i.workflow_class_types}
                for i in comfy_status.pending
            ],
            "total_estimated_mib": comfy_status.total_estimated_mib,
            "probe_error": comfy_status.probe_error,
        },
    }


def _reset_for_tests(*, clear_releasers: bool = False) -> None:
    with _LOCK:
        _LEASES.clear()
        if clear_releasers:
            _RELEASERS.clear()
