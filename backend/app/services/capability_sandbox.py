"""短期能力租约：外部技能与流程只能执行被明确批准的机械权限。"""
from __future__ import annotations

import copy
import json as _json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.config import DATA_DIR as _DATA_DIR

_LOCK = threading.Lock()
_LEASES: dict[str, dict[str, Any]] = {}

# 租约持久化：uvicorn --reload / 进程重启会把内存租约清空，导致已批准任务
# 执行时误报「租约不存在」。落盘到 DATA_DIR/capability_leases.json，模块导入时
# 恢复未过期未撤销的租约；grant/revoke 时同步写盘。
LEASE_FILE = _DATA_DIR / "capability_leases.json"


def _load_persisted(now: Callable[[], float] = time.time) -> None:
    try:
        if not LEASE_FILE.is_file():
            return
        items = _json.loads(LEASE_FILE.read_text(encoding="utf-8"))
        current = now()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("revoked") or float(item.get("expires_at") or 0) <= current:
                continue
            _LEASES.setdefault(str(item.get("id") or ""), item)
    except (OSError, _json.JSONDecodeError):
        pass


def _save_persisted(state: dict[str, dict[str, Any]] | None = None) -> None:
    """原子发布租约盘面：同目录临时文件 → flush+fsync → `os.replace`。

    **失败不吞**（2026-11-05 T2 实锤：此前吞 OSError，grant/revoke 在盘面根本没写
    成功时也返回成功，用户以为已批准/已撤销）。失败时旧 JSON 逐字节不变（替换未
    发生），临时文件清理的二次异常不得覆盖原始异常。
    """
    snapshot = state if state is not None else _LEASES
    payload = _json.dumps(list(snapshot.values()), ensure_ascii=False, indent=2)
    tmp = LEASE_FILE.with_name(
        f".{LEASE_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        LEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, LEASE_FILE)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _commit(mutator: Callable[[dict[str, dict[str, Any]]], tuple[Any, bool]]) -> Any:
    """租约变更的唯一提交接缝（2026-11-05 T2）。

    同一把锁内完成「深拷贝当前状态 → 改副本 → 副本落盘 → 用副本替换内存」。
    因此：① 并发变更不会在锁外写旧快照（丢另一线程刚提交的租约）；② 落盘失败时
    内存保持旧状态并向上抛 OSError，调用方绝不可能拿到「假成功」。

    `mutator(staged)` 返回 `(结果, 是否有变化)`；无变化（重复授权、租约不存在）
    不落盘也不替换内存，避免只读语义的调用白付一次磁盘写。
    """
    with _LOCK:
        staged = copy.deepcopy(_LEASES)
        result, changed = mutator(staged)
        if changed:
            _save_persisted(staged)
            _LEASES.clear()
            _LEASES.update(staged)
    return result


_load_persisted()

# 两档访问标准（2026-09-02 定案）：
# approval = 默认，租约按 operation/path/domain/tool 逐条授权；
# full     = 用户显式开启的完全访问，authorize 只校验撤销/过期，配额与路径域等硬闸门不豁免。
ACCESS_APPROVAL = "approval"
ACCESS_FULL = "full"
ACCESS_LEVELS = (ACCESS_APPROVAL, ACCESS_FULL)


def grant(subject: str, capabilities: list[dict[str, str]], *, ttl_seconds: int = 600,
          approved_by: str = "user", mode: str = ACCESS_APPROVAL,
          now: Callable[[], float] = time.time) -> dict[str, Any]:
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
    current = now()
    lease_id = uuid.uuid4().hex
    lease: dict[str, Any] = {
        "id": lease_id, "subject": subject.strip(), "capabilities": normalized,
        "mode": mode, "created_at": current,
        "expires_at": current + max(1, min(ttl_seconds, 86400)),
        "approved_by": approved_by or "user", "revoked": False,
    }
    def _add(staged: dict[str, dict[str, Any]]) -> tuple[None, bool]:
        staged[lease_id] = lease
        return None, True

    _commit(_add)
    return dict(lease)


def grant_operation(lease_id: str, operation: str, *, path: str = "", tool: str = "",
                      domain: str = "", action_hash: str = "",
                      now: Callable[[], float] = time.time) -> dict[str, Any]:
    """往已有租约追加一条能力授权（approval 自由循环批准后调用）。

    与 grant 不同：不新建租约，只把用户刚批准的 operation/path 追加进现有租约，
    让 awaiting_approval 的自由循环从断点续跑时该操作可执行。

    `action_hash` 非空 = **一次性动作授权**（2026-11-05 T3）：只有同一个动作
    （operation + 环境归一后的 params + 路径）能命中，且 authorize 消费一次即失效。
    留空 = 既有的持续授权语义（机械计划整计划批准、演示流程、越域读取路径授权）。
    """
    operation = str(operation or "").strip()
    if not operation:
        raise ValueError("缺少 operation")
    action_hash = str(action_hash or "").strip()

    def _append(staged: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        lease = staged.get(lease_id)
        if not lease or lease.get("revoked"):
            raise PermissionError("能力租约不存在或已撤销")
        if float(lease.get("expires_at") or 0) <= now():
            # 2026-09-14 修复：过期租约禁止追加授权。此前只查存在+未撤销即追加
            # 「成功」，续跑时 authorize 才查过期再拦 → 「批准→续跑→再拦→再批准」
            # 死循环（09-14 实盘复现：断点停 43h > TTL 24h）。
            raise PermissionError("能力租约已过期")
        caps = lease.setdefault("capabilities", [])
        for item in caps:
            if (item.get("operation") == operation
                    and item.get("path", "") == str(path or "")
                    # 幂等分支必须连动作指纹一起比：不同 hash 不能撞进「已授权」短路
                    # （否则批准 A 等于同时批了 B；2026-11-05 T3）。
                    and str(item.get("action_hash") or "") == action_hash):
                if action_hash and item.get("consumed"):
                    # 命中的是**已消费**的一次性条目 ⇒ 用户此刻是「把同一个动作再批一次」。
                    # 必须重新装填，否则 grant_operation 报「已授权」而 authorize 仍判 consumed
                    # → 批准→再拦→再批准 死循环（与 2026-09-14 修过的过期租约批准死循环同形）。
                    item["consumed"] = False
                    return dict(lease), True
                return dict(lease), False
        entry: dict[str, Any] = {
            "operation": operation,
            "path": str(path or "").strip(),
            "domain": str(domain or "").strip().lower(),
            "tool": str(tool or "").strip(),
        }
        if action_hash:
            entry["action_hash"] = action_hash
            entry["consumed"] = False
        caps.append(entry)
        return dict(lease), True

    return _commit(_append)


def revoke(lease_id: str) -> bool:
    """撤销租约。返回 True 只代表**盘面已落住**（2026-11-05 T2）；写盘失败抛 OSError，
    调用方不得宣称已取消——否则重载后权限复活，用户以为取消了但下一步仍能执行。"""

    def _mark(staged: dict[str, dict[str, Any]]) -> tuple[bool, bool]:
        lease = staged.get(lease_id)
        if not lease:
            return False, False
        lease["revoked"] = True
        return True, True

    return _commit(_mark)


def lease_registered(lease_id: str) -> bool:
    """租约是否仍登记（未撤销；允许已过期——过期租约经 renew 可救活）。"""
    with _LOCK:
        lease = _LEASES.get(str(lease_id or ""))
    if not lease or lease.get("revoked"):
        return False
    return True


def renew(lease_id: str, *, ttl_seconds: int = 86400,
          now: Callable[[], float] = time.time) -> dict[str, Any]:
    """续期租约（approval 断点批准 = 用户此刻的新授权动作，可救活过期租约）。

    2026-09-14：断点停在审批点超过 TTL 时，批准若不续期则续跑必再被拦。
    续期只延长 expires_at，**不扩大授权清单**（追加授权仍走 grant_operation 逐条）；
    撤销/不存在的租约不可续。
    """
    def _extend(staged: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        lease = staged.get(str(lease_id or ""))
        if not lease or lease.get("revoked"):
            raise PermissionError("能力租约不存在或已撤销，无法续期")
        lease["expires_at"] = now() + max(1, min(ttl_seconds, 86400))
        return dict(lease), True

    return _commit(_extend)


def path_allowed(requested: str, root: str) -> bool:
    """执行期路径域判定（公开接缝，2026-11-05 T1）：`requested` 是否落在 `root` 内。

    用 `Path.resolve()` 而非词法比较——`..`、链接与大小写差异都要按真实文件系统归一。
    计划提交期的词法检查另有属主（`plan_validator._check_paths`，按 `PureWindowsPath`
    比较），两条闸口径互补：提交期挡「声明就越权」，执行期挡「运行时算出来的实际路径越权」。
    `root` 为空 = 未声明路径域，只有同样为空的 `requested` 通过。
    """
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
              tool: str = "", action_hash: str = "",
              now: Callable[[], float] = time.time) -> dict[str, Any]:
    """执行期授权判定。带 `action_hash` 的一次性动作授权在这一步**消费**（2026-11-05 T3）。

    合同：
    - 一次性条目（`action_hash` 非空）必须 hash 相等且未消费才放行，放行即置 `consumed`；
      判定与消费在同锁内完成 ⇒ 并发 authorize 至多一次成功，handler 抛错也算已使用
      （要重试必须重新批准，不给「批一次跑 N 次」留口子）。
    - full 租约直接放行、**不消费**（既定语义：用户已整体授权）。
    - 无 `action_hash` 的条目 = 持续授权，保持机械计划整计划批准 / 演示流程 /
      越域读取路径授权的既有行为；调用方不传 action_hash 时也不会命中一次性条目。
    """

    def _check(staged: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        lease = staged.get(str(lease_id or ""))
        if not lease:
            raise PermissionError("能力租约不存在、已撤销或已过期")
        if lease.get("revoked") or float(lease.get("expires_at") or 0) <= now():
            raise PermissionError("能力租约不存在、已撤销或已过期")
        if lease.get("mode") == ACCESS_FULL:
            return dict(lease), False
        for capability in lease.get("capabilities") or []:
            if capability.get("operation") not in {operation, "*"}:
                continue
            if capability.get("tool") and capability.get("tool") != tool:
                continue
            if capability.get("domain") and capability.get("domain") != domain.lower():
                continue
            if capability.get("path") and not path_allowed(path, capability["path"]):
                continue
            bound = str(capability.get("action_hash") or "")
            if bound:
                if bound != str(action_hash or "") or capability.get("consumed"):
                    continue
                capability["consumed"] = True
                return dict(lease), True
            return dict(lease), False
        raise PermissionError(f"租约未授权操作：{operation}")

    return _commit(_check)


def active(subject: str = "", *,
           now: Callable[[], float] = time.time) -> list[dict[str, Any]]:
    current = now()
    with _LOCK:
        return [dict(item) for item in _LEASES.values()
                if not item.get("revoked") and item.get("expires_at", 0) > current
                and (not subject or item.get("subject") == subject)]


def _reset_for_tests() -> None:
    with _LOCK:
        _LEASES.clear()
    try:
        if LEASE_FILE.is_file():
            LEASE_FILE.unlink()
    except OSError:
        pass
