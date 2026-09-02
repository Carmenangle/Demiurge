"""短期能力租约：外部技能与流程只能执行被明确批准的机械权限。"""
from __future__ import annotations

import json as _json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import DATA_DIR as _DATA_DIR

_LOCK = threading.Lock()
_LEASES: dict[str, dict[str, Any]] = {}

# 租约持久化：uvicorn --reload / 进程重启会把内存租约清空，导致已批准任务
# 执行时误报「租约不存在」。落盘到 DATA_DIR/capability_leases.json，模块导入时
# 恢复未过期未撤销的租约；grant/revoke 时同步写盘。
LEASE_FILE = _DATA_DIR / "capability_leases.json"


def _load_persisted() -> None:
    try:
        if not LEASE_FILE.is_file():
            return
        items = _json.loads(LEASE_FILE.read_text(encoding="utf-8"))
        now = time.time()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("revoked") or float(item.get("expires_at") or 0) <= now:
                continue
            _LEASES.setdefault(str(item.get("id") or ""), item)
    except (OSError, _json.JSONDecodeError):
        pass


def _save_persisted() -> None:
    try:
        LEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LEASE_FILE.write_text(
            _json.dumps(list(_LEASES.values()), ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass


_load_persisted()

# 两档访问标准（2026-09-02 定案）：
# approval = 默认，租约按 operation/path/domain/tool 逐条授权；
# full     = 用户显式开启的完全访问，authorize 只校验撤销/过期，配额与路径域等硬闸门不豁免。
ACCESS_APPROVAL = "approval"
ACCESS_FULL = "full"
ACCESS_LEVELS = (ACCESS_APPROVAL, ACCESS_FULL)


def grant(subject: str, capabilities: list[dict[str, str]], *, ttl_seconds: int = 600,
          approved_by: str = "user", mode: str = ACCESS_APPROVAL) -> dict[str, Any]:
    if mode not in ACCESS_LEVELS:
        raise ValueError(f"未知访问标准：{mode}")
    if not subject.strip():
        raise ValueError("能力租约缺少 subject")
    if mode == ACCESS_APPROVAL and not capabilities:
        raise ValueError("approval 租约缺少 capabilities")
    if mode == ACCESS_FULL and not capabilities:
        capabilities = [{"operation": "*", "path": ""}]
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
        "mode": mode, "created_at": now,
        "expires_at": now + max(1, min(ttl_seconds, 86400)),
        "approved_by": approved_by or "user", "revoked": False,
    }
    with _LOCK:
        _LEASES[lease_id] = lease
    _save_persisted()
    return dict(lease)


def revoke(lease_id: str) -> bool:
    with _LOCK:
        lease = _LEASES.get(lease_id)
        if not lease:
            return False
        lease["revoked"] = True
    _save_persisted()
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
    if lease.get("mode") == ACCESS_FULL:
        return lease
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
    try:
        if LEASE_FILE.is_file():
            LEASE_FILE.unlink()
    except OSError:
        pass
