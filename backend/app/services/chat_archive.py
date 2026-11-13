"""对话删除前的软删层：清缓存/压缩/清空覆盖掉会话之前，先把原文归档一份。

三条设计约束都来自 2026-11-05「防误删」审计，改动前请连约束一起读：

- **只读快照真源，不碰 `chat_memory`**：后者在模块级冻结了 `CHECKPOINT_DB`
  （`from app.config import CHECKPOINT_DB`），归档路径一旦依赖它，测试就会去开真实运行态库
  （本机那 14.8 GB 的 `checkpoints.db` 里已经躺着 10 个测试 thread，就是这么漏进去的）。
  压缩例外：它本来就已经把 checkpoint 历史读到手上了，顺手带上。
- **写不进就抛**：调用方（`chat_maintenance`）据此**不执行删除**。归档失败还照删 = 没有这层。
- **不自动清理旧归档**：软删层自己静默删历史，等于把同一个缺陷换个地方再犯一遍。
  要腾盘由用户删文件，不由代码猜哪些不要。

归档名同时就是文件名，语法 `时间戳-原因-条数` ⇒ 列表不必读正文（一份会话快照可到数 MB），
而字符集只含 `[0-9a-z_-]` ⇒ 天然挡掉路径分隔与 `..`，恢复端不用再做二次清洗。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from app.config import DATA_DIR
from app.services.pathnames import safe_seg

#: 归档根（`data_layout` 里登记为产物档：任何「清设置/清缓存」的删除面都不碰它）
ARCHIVE_DIR = DATA_DIR / "chat_archives"

REASON_CLEAR = "clear"                  # /chat/clear：清 checkpoint 历史
REASON_CLEAR_CACHE = "clear-cache"      # /chat/clear-cache：快照 + checkpoint 一起清
REASON_COMPACT = "compact"              # /chat/compact：整条会话压成一条摘要
REASON_RESTORE = "restore"              # 恢复动作自身的覆盖前备份

_NAME_RE = re.compile(r"^(\d{8}-\d{6}-\d{3})-([a-z][a-z0-9_-]{0,31})-(\d{1,7})$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ArchiveNameError(ValueError):
    """归档名不合法 / 文件读不动 ⇒ 恢复拒绝执行（路由层按 400 回）。"""


def _thread_dir(thread_id: str) -> Path:
    """`<归档根>/<会话名>`。

    `strip=True`（比 `chat_snapshot` 更严一档）：`safe_seg` 保留点号，`thread_id=".."`
    在 `strip=False` 下会解析成归档根的**父目录**，把归档写到 `backend/data/` 里。
    """
    return ARCHIVE_DIR / safe_seg(thread_id or "home", "home")


def _stamp_ms(moment_ms: int) -> str:
    return (time.strftime("%Y%m%d-%H%M%S", time.localtime(moment_ms / 1000))
            + f"-{moment_ms % 1000:03d}")


def archive(thread_id: str, snapshot: list, reason: str, history: list | None = None) -> str:
    """归档这份快照，返回归档名；两份内容都空 ⇒ 返回 `""`（无可归档，不算失败）。

    抛出的异常（OSError / TypeError…）必须由调用方判为「删除不执行」。
    """
    history = history or []
    if not snapshot and not history:
        return ""
    if not _REASON_RE.fullmatch(reason or ""):
        raise ValueError(f"归档原因名不合法：{reason!r}")
    folder = _thread_dir(thread_id)
    folder.mkdir(parents=True, exist_ok=True)
    count = len(snapshot) or len(history)
    base_ms = int(time.time() * 1000)
    for seq in range(1000):  # 同毫秒双归档（清空紧接压缩）：往后挪 1ms，绝不动已写文件
        stamp = _stamp_ms(base_ms + seq)
        name = f"{stamp}-{reason}-{count}"
        target = folder / f"{name}.json"
        if not target.exists():
            break
    else:  # pragma: no cover - 只有同毫秒堆出 1000 份归档才走到
        raise OSError(f"归档名连续撞车，放弃写入：{folder}")
    payload = {
        "thread_id": thread_id,
        "reason": reason,
        "created_at": _label(stamp),
        "message_count": count,
        "messages": snapshot,
        "history": history,
    }
    tmp = folder / f"{name}.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return name


def _label(stamp: str) -> str:
    """`20261105-142233-500` → `2026-11-05 14:22:33`（给人看的，解析失败原样回）。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.strptime(stamp[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        return stamp


def list_archives(thread_id: str) -> list[dict]:
    """该会话的全部归档，新的在前。只读文件名，不读正文。

    不符语法的文件（手放的、写坏的）不出现在列表里，但**也不删**——列表面向恢复入口，
    删除面不属于这里。
    """
    folder = _thread_dir(thread_id)
    if not folder.is_dir():
        return []
    items: list[dict] = []
    for path in folder.iterdir():
        if path.suffix != ".json":
            continue
        matched = _NAME_RE.fullmatch(path.stem)
        if not matched:
            continue
        items.append({
            "name": path.stem,
            "reason": matched.group(2),
            "message_count": int(matched.group(3)),
            "created_at": _label(matched.group(1)),
            "bytes": path.stat().st_size,
        })
    return sorted(items, key=lambda item: item["name"], reverse=True)


def read_archive(thread_id: str, name: str) -> dict:
    """取回归档内容 `{"messages": [...], "history": [...]}`；名字不合法/读不动 ⇒ `ArchiveNameError`。"""
    if not _NAME_RE.fullmatch(name or ""):
        raise ArchiveNameError("归档名不合法")
    target = _thread_dir(thread_id) / f"{name}.json"
    if not target.is_file():
        raise ArchiveNameError("归档不存在或已被删除")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveNameError(f"归档读不动：{exc}") from exc
    messages = data.get("messages") if isinstance(data, dict) else None
    history = data.get("history") if isinstance(data, dict) else None
    if not isinstance(messages, list) and not isinstance(history, list):
        raise ArchiveNameError("归档内容为空或格式不对，无法恢复")
    return {"messages": messages if isinstance(messages, list) else [],
            "history": history if isinstance(history, list) else [],
            "reason": str(data.get("reason") or "")}
