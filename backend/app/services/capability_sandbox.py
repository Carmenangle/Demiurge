"""短期能力租约：外部技能与流程只能执行被明确批准的机械权限。"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()
_LEASES: dict[str, dict[str, Any]] = {}


def grant(subject: str, capabilities: list[dict[str, str]], *, ttl_seconds: int = 600,
          approved_by: str = "user") -> dict[str, Any]:
    if not subject.strip() or not capabilities:
        raise ValueError("能力租约缺少 subject 或 capabilities")
    normalized = []
    for item in capabilities:
        operation = str(item.get("operation") or "").strip()
        if not operation:
            raise ValueError("每项能力必须声明 operation")
        normalized.append({
            "operation": operation,
            "path": str(item.get("path") or "").strip(),
            "domain": str(item.get("domain") or "").strip().lower(),
            "tool": str(item.get("tool") or "").strip(),
        })
    now = time.time()
    lease_id = uuid.uuid4().hex
    lease: dict[str, Any] = {
        "id": lease_id, "subject": subject.strip(), "capabilities": normalized,
        "created_at": now, "expires_at": now + max(1, min(ttl_seconds, 86400)),
        "approved_by": approved_by or "user", "revoked": False,
    }
    with _LOCK:
        _LEASES[lease_id] = lease
    return dict(lease)


def revoke(lease_id: str) -> bool:
    with _LOCK:
        lease = _LEASES.get(lease_id)
        if not lease:
            return False
        lease["revoked"] = True
        return True


def _path_allowed(requested: str, root: str) -> bool:
    if not root:
        return not requested
    if not requested:
        return False
    try:
        req = Path(requested).expanduser().resolve()
        base = Path(root).expanduser().resolve()
        return req == base or base in req.parents
    except OSError:
        return False


def authorize(lease_id: str, operation: str, *, path: str = "", domain: str = "",
              tool: str = "") -> dict[str, Any]:
    with _LOCK:
        lease = dict(_LEASES.get(lease_id) or {})
    if not lease or lease.get("revoked") or float(lease.get("expires_at") or 0) <= time.time():
        raise PermissionError("能力租约不存在、已撤销或已过期")
    for capability in lease.get("capabilities") or []:
        if capability.get("operation") not in {operation, "*"}:
            continue
        if capability.get("tool") and capability.get("tool") != tool:
            continue
        if capability.get("domain") and capability.get("domain") != domain.lower():
            continue
        if capability.get("path") and not _path_allowed(path, capability["path"]):
            continue
        return lease
    raise PermissionError(f"租约未授权操作：{operation}")


def active(subject: str = "") -> list[dict[str, Any]]:
    now = time.time()
    with _LOCK:
        return [dict(item) for item in _LEASES.values()
                if not item.get("revoked") and item.get("expires_at", 0) > now
                and (not subject or item.get("subject") == subject)]


def _reset_for_tests() -> None:
    with _LOCK:
        _LEASES.clear()
