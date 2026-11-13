"""带档位闸的用户态存档读写：`schemaVersion` 决定「读 / 迁移 / 备份+建新」，备份失败一律拒写。

为什么要有这一层（2026-11-02 P1）：「覆盖升级不动数据、重装后设置照旧」是用户对卸载/重装的核
心期待，而档案一旦被**更新的代码**写过（降级回来、或两个版本并跑同一目录），旧代码按老形状硬读
就会读出错；反过来，一个读不懂的档案若被空状态整体覆盖写，就是「设置没了」这类事故的最终形态
（2026-10-28 实测：一次启动回填失败把盘上真 key 抹成空串）。所以规则收在这里，只有两条：

- 档位认识（含未打标的 `0`＝拆分前的单档形状）⇒ 读；低于当前档 ⇒ 由调用方的迁移逻辑就地上档。
- 档位不认识（更高档、字段类型不对、JSON 坏、根不是对象）⇒ 先备份 `<名>.bak-<时间戳>` 再建新；
  **备份失败绝不建新**，改为把该路径标成拒写——宁可设置改不动，也不拿空状态盖掉读不懂的档案。

本模块只管「一个 JSON 档案 ↔ 内存 dict」：不知道作品索引与设置怎么拆、也不知道哪个名字归哪一
档（那是 `services/repo_meta` 与 `app/data_layout.py` 的属主范围）。路径由调用方传入 ⇒ 测试的
注入面仍钉在 `repo_meta.DATA_DIR`，不必在本模块再开一个环境变量。

跨进程（2026-11-07 H 项）：`save` / `mutate` / `delete_with_backup` 共用 `<名>.lock` 这一把锁。
`save` 是整档替换；「读最新 → 改 → 写」必须走 `mutate`，否则两个实例各拿旧副本回写会丢更新。
锁文件常驻不删——删掉会让后来的进程和仍持旧句柄的进程各锁一个文件，互斥直接失效。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from shutil import copy2
from threading import Lock
from typing import TypedDict

from app.file_lock import LockTimeout, exclusive_path_lock

_LOG = logging.getLogger("uvicorn.error")
_BACKUP_LOCK = Lock()


class DeleteWithBackupResult(TypedDict):
    deleted: bool
    backup: Path | None
    reason: str
    error: str

#: 本代码写的档位，也是它认识的**最高**档位。
SCHEMA_VERSION = 1
#: 拆分前的旧档案没有档位字段，按 0 处理——0 是**认识**的形状（走迁移），不是「不认」。
UNMARKED_VERSION = 0

#: 备份失败 ⇒ 拒写的路径集合。档案消失或写成功即解除。
_BLOCKED: set[str] = set()


def blocked_paths() -> tuple[str, ...]:
    """当前处于拒写状态的档案路径（供调用方决定是否回报「没保存」）。"""
    return tuple(sorted(_BLOCKED))


@contextmanager
def path_lock(path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    """独占某个存档路径的读改写/删除；锁文件是 `<名>.lock`。

    目标文件会被 `replace()` 换掉，锁必须落在独立文件上；同线程可重入，
    `delete_with_backup → backup/save` 这类嵌套不会自锁死。
    """
    with exclusive_path_lock(path.with_name(f"{path.name}.lock"), timeout=timeout):
        yield


def _read_version(data: dict) -> int:
    """取档位：字段缺失＝0（认识）；类型不对或负数＝-1（不认，交调用方判建新）。"""
    if "schemaVersion" not in data:
        return UNMARKED_VERSION
    version = data["schemaVersion"]
    # bool 是 int 的子类，`true` 当档位读会静默变成 1 ⇒ 显式排除。
    if isinstance(version, bool) or not isinstance(version, int) or version < UNMARKED_VERSION:
        return -1
    return version


def backup(path: Path) -> Path | None:
    """原子发布时间戳备份，仅复用相同字节；OSError 最多尝试两次后返回 None。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for _ in range(2):
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            copy2(path, temp)
            content = temp.read_bytes()
            with _BACKUP_LOCK:
                index = 0
                while True:
                    suffix = f"-{index}" if index else ""
                    target = path.with_name(f"{path.name}.bak-{stamp}{suffix}")
                    if not target.exists() and not target.is_symlink():
                        temp.replace(target)
                        return target
                    if target.is_file() and target.read_bytes() == content:
                        return target
                    index += 1
        except OSError as exc:
            _LOG.warning("存档备份失败 %s：%s", path, exc)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError as exc:
                _LOG.warning("存档备份临时文件清理失败 %s：%s", temp, exc)
    return None


def delete_with_backup(path: Path) -> DeleteWithBackupResult:
    """备份 → 逐字节复核源文件仍是备份那一份 → 才删除；任何一步不确定都不删。

    整段在 `<名>.lock` 内（与 `save`/`mutate` 同一把锁）⇒ 本代码的写入方不会在
    「备份 → 复核 → unlink」之间插进来。外部手工改档仍不在锁内，所以复核不能省。
    锁本身不可用（目录只读 / 等锁超时）⇒ 返回 `lock-failed`、不删除——删除接缝
    从不抛错，`load` 的「读不懂也不能把档案搞丢」全靠这条。
    """
    try:
        with path_lock(path):
            return _delete_with_backup_locked(path)
    except (LockTimeout, OSError) as exc:
        _LOG.error("删除 %s 前跨进程锁不可用：%s", path, exc)
        return {"deleted": False, "backup": None, "reason": "lock-failed",
                "error": f"跨进程锁不可用，文件没有删除：{exc}"}


def _delete_with_backup_locked(path: Path) -> DeleteWithBackupResult:
    if not path.is_file():
        return {"deleted": False, "backup": None, "reason": "missing",
                "error": "文件不存在，未删除"}
    backup_file = backup(path)
    if backup_file is None:
        _LOG.error("备份失败 ⇒ 拒绝删除 %s", path)
        return {"deleted": False, "backup": None, "reason": "backup-failed",
                "error": "备份失败，文件没有删除（磁盘/权限问题，详见后端日志）"}
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        return {"deleted": False, "backup": backup_file, "reason": "missing",
                "error": f"备份后源文件已不存在（备份为 {backup_file.name}）"}
    except OSError as exc:
        return {"deleted": False, "backup": backup_file, "reason": "changed-during-backup",
                "error": f"无法复核源文件，未删除（备份为 {backup_file.name}）：{exc}"}
    try:
        archived = backup_file.read_bytes()
    except OSError as exc:
        return {"deleted": False, "backup": backup_file, "reason": "changed-during-backup",
                "error": f"无法读取备份复核，未删除（备份为 {backup_file.name}）：{exc}"}
    if current != archived:
        return {"deleted": False, "backup": backup_file, "reason": "changed-during-backup",
                "error": f"备份期间源文件内容发生变化，已保留原文件（备份为 {backup_file.name}）"}
    try:
        path.unlink()
    except OSError as exc:
        _LOG.error("已备份到 %s 但删不掉 %s：%s", backup_file, path, exc)
        return {"deleted": False, "backup": backup_file, "reason": "unlink-failed",
                "error": f"已备份到 {backup_file.name}，但原文件删不掉：{exc}"}
    return {"deleted": True, "backup": backup_file, "reason": "ok", "error": ""}


def _reject_and_rebuild(path: Path, reason: str) -> dict:
    """「不认」这一支：备份且复核一致 ⇒ 删掉原档案（下次写就是新档）；否则拒写。"""
    result = delete_with_backup(path)
    if not result["deleted"]:
        _BLOCKED.add(str(path))
        _LOG.error(
            "存档 %s 不认识（%s），且%s ⇒ 不建新、拒绝对该文件写入；"
            "要恢复请人工把该文件搬走或改对。", path, reason, result["error"])
        return {}
    _LOG.warning("存档 %s 不认识（%s）⇒ 已备份到 %s，按新档重建。",
                 path, reason, result["backup"])
    return {}


def load(path: Path) -> dict:
    """读一份档位化档案：读不懂/不认 ⇒ 走「备份+建新」，恒返回 dict（缺档就是空 dict）。"""
    key = str(path)
    if not path.is_file():
        _BLOCKED.discard(key)  # 档案没了（用户自己搬走）⇒ 拒写状态自动解除
        return {}
    if key in _BLOCKED:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        # OSError 也在吞掉之列：属主不得比它替换掉的代码更不耐错，否则读盘失败（权限/删除竞态）
        # 会从「按未配置处理」退化成裸异常 500。
        return _reject_and_rebuild(path, f"读不出来：{exc}")
    if not isinstance(data, dict):
        return _reject_and_rebuild(path, f"根不是对象（{type(data).__name__}）")
    version = _read_version(data)
    if version < 0:
        return _reject_and_rebuild(path, f"档位字段不认识：{data.get('schemaVersion')!r}")
    if version > SCHEMA_VERSION:
        return _reject_and_rebuild(
            path, f"存档由更新的版本写出（档位 {version} > 本代码 {SCHEMA_VERSION}）")
    if version < SCHEMA_VERSION:
        _LOG.info("存档 %s 档位 %d 低于当前 %d，按迁移处理。", path.name, version, SCHEMA_VERSION)
    return data


def save(path: Path, payload: dict) -> bool:
    """原子写（临时文件 + replace）并打上当前档位；返回 False＝该路径正被拒写。

    这是**整档替换**：不读盘、不做合并。需要「读最新 → 改 → 写」的调用方必须走
    `mutate`，否则两个实例各拿旧副本回写，后写的会把先写的变化抹掉。
    整段在 `<名>.lock` 内 ⇒ 与 `mutate`/`delete_with_backup` 跨进程互斥。

    真正的写失败（磁盘满/权限）与锁超时照旧**抛 OSError / LockTimeout**：那属于
    「操作失败」，调用方该让它冒成 500，不能和「数据不认识所以不敢写」混成同一个信号。
    """
    if str(path) in _BLOCKED:
        _LOG.error("存档 %s 处于拒写状态（不认识且备份失败），本次写入已跳过。", path)
        return False
    with path_lock(path):
        return _save_unlocked(path, payload)


def _save_unlocked(path: Path, payload: dict) -> bool:
    key = str(path)
    if key in _BLOCKED:
        _LOG.error("存档 %s 处于拒写状态（不认识且备份失败），本次写入已跳过。", path)
        return False
    data = {"schemaVersion": SCHEMA_VERSION}
    data.update({k: v for k, v in payload.items() if k != "schemaVersion"})
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    _BLOCKED.discard(key)
    return True


def mutate(
    path: Path,
    apply: Callable[[dict], tuple[dict, bool]],
    *,
    timeout: float = 30.0,
) -> bool:
    """锁内「读最新 → 改 → 有变化才写」；返回 False＝该路径正被拒写。

    `apply(最新档) -> (新档, changed)`：`changed` 必须是**显式布尔**，不能让业务返回值的
    真值兼任——`0` / `''` / 空集合都是合法结果，拿它们判断「改没改」会静默漏写。
    锁内重新 `load`，所以另一实例刚写进去的字段（如未显式提供的 `selection`）不会被
    本实例手里的旧副本抹掉。整档 `settings` 仍是最后写者赢：这是产品语义，不是本层合并。
    """
    with path_lock(path, timeout=timeout):
        doc, changed = apply(load(path))
        if not changed:
            return True
        return _save_unlocked(path, doc)
