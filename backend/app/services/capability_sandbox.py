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
        # 自由循环审批（2026-09-06）：approval 租约允许空授权起步，模型逐步调用时
        # 由 grant_operation 在用户批准后逐条追加（固化02/03 内容生成型任务的断点续跑）。
        capabilities = []
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


def grant_operation(lease_id: str, operation: str, *, path: str = "", tool: str = "",
                      domain: str = "") -> dict[str, Any]:
    """往已有租约追加一条能力授权（approval 自由循环批准后调用）。

    与 grant 不同：不新建租约，只把用户刚批准的 operation/path 追加进现有租约，
    让 awaiting_approval 的自由循环从断点续跑时该操作可执行。
    """
    operation = str(operation or "").strip()
    if not operation:
        raise ValueError("缺少 operation")
    with _LOCK:
        lease = _LEASES.get(lease_id)
        if not lease or lease.get("revoked"):
            raise PermissionError("能力租约不存在或已撤销")
        if float(lease.get("expires_at") or 0) <= time.time():
            # 2026-09-14 修复：过期租约禁止追加授权。此前只查存在+未撤销即追加
            # 「成功」，续跑时 authorize 才查过期再拦 → 「批准→续跑→再拦→再批准」
            # 死循环（09-14 实盘复现：断点停 43h > TTL 24h）。
            raise PermissionError("能力租约已过期")
        caps = lease.setdefault("capabilities", [])
        for item in caps:
            if item.get("operation") == operation and item.get("path", "") == str(path or ""):
                return dict(lease)
        caps.append({"operation": operation,
                     "path": str(path or "").strip(),
                     "domain": str(domain or "").strip().lower(),
                     "tool": str(tool or "").strip()})
        result = dict(lease)
    _save_persisted()
    return result


def revoke(lease_id: str) -> bool:
    with _LOCK:
        lease = _LEASES.get(lease_id)
        if not lease:
            return False
        lease["revoked"] = True
    _save_persisted()
    return True


def lease_registered(lease_id: str) -> bool:
    """租约是否仍登记（未撤销；允许已过期——过期租约经 renew 可救活）。"""
    with _LOCK:
        lease = _LEASES.get(str(lease_id or ""))
    if not lease or lease.get("revoked"):
        return False
    return True


def renew(lease_id: str, *, ttl_seconds: int = 86400) -> dict[str, Any]:
    """续期租约（approval 断点批准 = 用户此刻的新授权动作，可救活过期租约）。

    2026-09-14：断点停在审批点超过 TTL 时，批准若不续期则续跑必再被拦。
    续期只延长 expires_at，**不扩大授权清单**（追加授权仍走 grant_operation 逐条）；
    撤销/不存在的租约不可续。
    """
    with _LOCK:
        lease = _LEASES.get(str(lease_id or ""))
        if not lease or lease.get("revoked"):
            raise PermissionError("能力租约不存在或已撤销，无法续期")
        lease["expires_at"] = time.time() + max(1, min(ttl_seconds, 86400))
        result = dict(lease)
    _save_persisted()
    return result


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
