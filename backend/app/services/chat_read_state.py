"""聊天已读状态（2026-11-10）。

用户口径：未读太久／已读不回复要有不同反应（担心、认为用户没看到而沉默、按性格与
好感度决定）。前端在私聊可见且滚到底时上报最后一条已读消息时间；追问/主动开场
读取这份状态，把「未读/已读不回/隔了多久」交给模型，由角色按关系与性格决定怎么说，
也可以选择 [NO_MESSAGE] 沉默。

只做状态读写，不生成消息；任何读写失败都按「没有记录」处理，绝不阻断聊天。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.config import DATA_DIR

_STATE_PATH = DATA_DIR / "chat_read.json"
_LOCK = threading.Lock()


def _load() -> dict[str, dict]:
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(state: dict[str, dict]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 已读记录写失败只影响反应文案，不影响聊天


def mark_read(thread_id: str, last_ts: float = 0.0, *, now: float | None = None) -> dict:
    """记录该线程最后已读消息时间；返回更新后的状态。"""
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return {}
    with _LOCK:
        state = _load()
        previous = state.get(thread_id) or {}
        ts = float(last_ts or 0)
        at = time.time() if now is None else float(now)
        previous_ts = float(previous.get("ts") or 0)
        unread_s = 0.0
        if ts > previous_ts:
            # 本条消息从发出到被看到隔了多久：给「用户隔了很久才看到」的反应提供依据。
            unread_s = max(0.0, at - ts)
        current = {
            "ts": max(ts, previous_ts),
            "at": at,
            "unread_s": unread_s or float(previous.get("unread_s") or 0),
            "unread_at": at if unread_s else float(previous.get("unread_at") or 0),
        }
        state[thread_id] = current
        _save(state)
        return dict(current)


def get(thread_id: str) -> dict:
    with _LOCK:
        return dict(_load().get((thread_id or "").strip(), {}) or {})


def state_path() -> Path:
    """供测试 monkeypatch / 诊断；运行态文件真源。"""
    return _STATE_PATH
