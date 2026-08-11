"""本地 AI 资源租约：为共享加速器提供可抢占、可观测的轻量调度。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Callable


@dataclass
class Lease:
    token: str
    owner: str
    capability: str
    priority: int
    estimated_mib: int
    acquired_at: float
    expires_at: float


_LOCK = RLock()
_LEASES: dict[str, Lease] = {}
_RELEASERS: dict[str, Callable[[], bool]] = {}
_SHARED_CAPABILITIES = {"comfyui"}


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
    """获取独占 GPU 租约；高优先级可调用低优先级 Adapter 的释放回调。"""
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
            if not (capability in _SHARED_CAPABILITIES and lease.capability == capability)
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
            if not (capability in _SHARED_CAPABILITIES and lease.capability == capability)
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
    with _LOCK:
        _prune()
        now = time.time()
        items = [
            {
                "owner": lease.owner, "capability": lease.capability,
                "priority": lease.priority, "estimated_mib": lease.estimated_mib,
                "age_seconds": round(now - lease.acquired_at, 3),
                "expires_in_seconds": round(max(0.0, lease.expires_at - now), 3),
            }
            for lease in sorted(_LEASES.values(), key=lambda item: -item.priority)
        ]
    return {"items": items, "busy": bool(items)}


def _reset_for_tests(*, clear_releasers: bool = False) -> None:
    with _LOCK:
        _LEASES.clear()
        if clear_releasers:
            _RELEASERS.clear()
