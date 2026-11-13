"""桌面壳后台补齐进度账本的只读投影源（P3-b）。

壳在后台下载可选层（当前只有 RAG，约 2 GB）时，把字节进度落到 `delivery.json`；
本模块是**唯一读取方**，把它交给 `activity_feed` 投影成「后台活动」面板的一条。

🔴 这是壳与后端之间的第一条跨进程通道。此前两侧只有启动时单向注入的 5 个环境变量
（`LAF_RUNTIME_ROOT` / `LAF_RUNTIME_STATE` / `LAF_DATA_DIR` / `LAF_RUNTIME_PORT` /
`LAF_NO_BROWSER`），壳的进度只写在启动页文案里，进应用后就再也看不见。因此
**文件位置、字段名和陈旧口径都是跨层合同**：写入方是
`desktop/tauri/src/runtime_delivery.rs` 的 `DeliveryLedger`，字段真源见那里的 `flush()`。

三条读侧纪律：

- **只读**。本模块绝不写、绝不删这本账——它是壳的资产，卸载时按缓存档清。
- **僵尸不算在跑**。壳中途崩掉没人把 `running` 改成终态，所以按 `updated_at` 年龄判陈旧；
  这边不留需要谁去清的状态，写侧也就不需要「恢复时先把上次的账收掉」这种额外协议。
- **坏档不打扰面板**。账本缺字段、JSON 损坏、文件根本不存在都返回 `None`，不抛异常：
  它只是可观测性，不该把 `/api/activity/background` 带崩。
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.config import DATA_DIR

#: 账本文件名：壳写在 `LAF_DATA_DIR`（安装版是 `home\userdata`）下，故这里直接用 `DATA_DIR`。
#: 归档在 `app.data_layout` 的**缓存档**，卸载随缓存一起清。
LEDGER_NAME = "delivery.json"

#: `running` 超过这个秒数没有更新 ⇒ 判定写入方已经不在了（崩溃或被强杀），不再展示。
#: 壳的落盘节奏跟着下载 tick 走（约每秒一次），30 秒留了足够余量又不至于让僵尸挂很久。
RUNNING_STALE_S = 30.0

_KNOWN_STATES = {"running", "done", "failed"}


def ledger_path():
    return DATA_DIR / LEDGER_NAME


def read_ledger(now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """读一本补齐账；没有账、账坏了、或它是陈旧的 `running` ⇒ `None`。"""
    path = ledger_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None

    state = str(value.get("state") or "").strip()
    if state not in _KNOWN_STATES:
        return None
    moment = time.time() if now is None else now
    updated = _number(value.get("updated_at"))
    if state == "running" and moment - updated > RUNNING_STALE_S:
        return None
    return {
        "layer": str(value.get("layer") or ""),
        "label": str(value.get("label") or ""),
        "state": state,
        "stage": str(value.get("stage") or ""),
        "done_bytes": int(_number(value.get("done_bytes"))),
        "total_bytes": int(_number(value.get("total_bytes"))),
        "speed_bps": int(_number(value.get("speed_bps"))),
        "started_at": _number(value.get("started_at")) or updated,
        "updated_at": updated,
        "error": str(value.get("error") or ""),
    }


def _number(value: Any) -> float:
    """账本里的数字一律宽容取：缺字段或非数字都当 0，不让一条脏值毁掉整条投影。"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
