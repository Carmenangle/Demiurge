"""聊天追问 / 续聊（2026-11-10）。

对标 starlit-desktop `app.autonomy.conversation.ConversationRunner` 的节奏，按本项目
规模收敛。解决的问题：角色发了一句后用户一直没回，私聊会永远停在旧消息上；进入私聊
时的「主动找上门」又有 2h 闲置 + 6h 冷却，不能覆盖「刚聊完没回」和「隔一阵再续」。

规则（全部只读判定，生成与落盘在 `chat_room.followup_turn`）：

- 角色发话后 60–180 秒没等到用户 → 追问一次；第 2 次退避 2–5 分钟、第 3 次 10–40 分钟；
- 单个话题最多 3 次追问；连续 bot 气泡 ≥5 条收住，不再补；
- 会话窗口 15 分钟；窗口结束后如果最后一句是角色说的，20–90 分钟后可重开，
  最多重开 2 次（用户一开口就重新计数）；
- 深夜 00:30–08:30 最多补 1 条，避免凌晨刷屏；
- 用户发言会重置追问/重开计数与冷却。

状态落 `chat_followup.json`，按 thread 隔离；读改写由 `_STATE_LOCK` 保护。
"""
from __future__ import annotations

import json
import random
import threading
import time
from typing import Any

from app.config import DATA_DIR

# 会话窗口：最后一条消息（用户或角色）之后 15 分钟内仍算「正在聊」。
_ACTIVE_WINDOW_SECONDS = 15 * 60
# 追问延迟（按已追问次数）：第 1 次 60–180s，第 2 次 2–5min，第 3 次 10–40min。
_FOLLOWUP_DELAYS = ((60, 180), (120, 300), (600, 2400))
_MAX_FOLLOWUPS = len(_FOLLOWUP_DELAYS)
# 追问/重开频率档位（设置→聊天室）：不逐项暴露秒数，避免 UI 过载。
_CADENCE_DELAYS = {
    "quiet": ((180, 600), (600, 1800), (1800, 5400)),
    "normal": _FOLLOWUP_DELAYS,
    "frequent": ((30, 90), (60, 180), (300, 900)),
}
# 会话窗口结束后的重开延迟（从最后一条 bot 消息算起）。
_REOPEN_DELAY = (20 * 60, 90 * 60)
_CADENCE_REOPEN = {
    "quiet": (40 * 60, 180 * 60),
    "normal": _REOPEN_DELAY,
    "frequent": (10 * 60, 45 * 60),
}
# 连续 bot 气泡上限：超过后不再追问（防止单人刷屏）。
_MAX_BOT_TURNS = 5
_MAX_REOPENS = 2
# 深夜安静时段默认值（本地时间）：可由设置覆盖；空串=关闭。
_QUIET_MAX_TURNS = 1

_STATE_PATH = DATA_DIR / "chat_followup.json"
_STATE_LOCK = threading.Lock()


def _timestamp(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _load() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(state: dict[str, dict[str, Any]]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 状态落盘失败只影响节奏，不影响聊天


def _thread_state(state: dict[str, dict[str, Any]], thread_id: str) -> dict[str, Any]:
    value = state.get(thread_id)
    return dict(value) if isinstance(value, dict) else {}


def _mutate(thread_id: str, fn) -> dict[str, Any]:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return {}
    with _STATE_LOCK:
        state = _load()
        current = _thread_state(state, thread_id)
        updated = fn(current) or current
        state[thread_id] = updated
        _save(state)
        return dict(updated)


def _cadence(value: object) -> str:
    name = str(value or "normal").strip().lower()
    return name if name in _CADENCE_DELAYS else "normal"


def _followup_delay(followups: int, cadence: object = "normal") -> float:
    delays = _CADENCE_DELAYS[_cadence(cadence)]
    index = max(0, min(int(followups), len(delays) - 1))
    low, high = delays[index]
    return random.uniform(float(low), float(high))


def _parse_hhmm(value: object, fallback: str) -> tuple[int, int] | None:
    """解析 HH:MM；空串/非法返回 None（=关闭安静时段）。"""
    raw = str(value if value is not None else fallback).strip()
    if not raw:
        return None
    try:
        hour, minute = raw.split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        return None
    return hour_i, minute_i


def _in_quiet_hours(
    now: float, start: str = "00:30", end: str = "08:30",
) -> bool:
    start_parts = _parse_hhmm(start, "00:30")
    end_parts = _parse_hhmm(end, "08:30")
    if start_parts is None or end_parts is None:
        return False
    local = time.localtime(now)
    start_minute = start_parts[0] * 60 + start_parts[1]
    end_minute = end_parts[0] * 60 + end_parts[1]
    minute = local.tm_hour * 60 + local.tm_min
    if start_minute <= end_minute:
        return start_minute <= minute < end_minute
    return minute >= start_minute or minute < end_minute


def note_user(thread_id: str, *, now: float | None = None) -> None:
    """用户发言：重置追问/重开计数；不在这里排追问（等角色真正回复后 note_bot）。"""
    ts = _timestamp(now)

    def apply(_current: dict[str, Any]) -> dict[str, Any]:
        return {
            "last_user": ts,
            "last_bot": 0.0,
            "bot_turns": 0,
            "followups": 0,
            "reopens": 0,
            "next_at": 0.0,
            "reopen_at": 0.0,
            "quiet_turns": 0,
        }

    _mutate(thread_id, apply)


def _followup_cap(max_followups: int | None) -> int:
    return _MAX_FOLLOWUPS if max_followups is None else max(0, int(max_followups))


def _reopen_cap(max_reopens: int | None) -> int:
    return _MAX_REOPENS if max_reopens is None else max(0, int(max_reopens))


def note_bot(
    thread_id: str, *, bubbles: int = 1, now: float | None = None,
    max_followups: int | None = None, max_reopens: int | None = None,
    cadence: str | None = None,
) -> None:
    """角色发话后重新排追问与重开；bubbles 按实际气泡数计入连续发言上限。"""
    ts = _timestamp(now)
    cadence_name = _cadence(cadence)

    def apply(current: dict[str, Any]) -> dict[str, Any]:
        followups = int(current.get("followups") or 0)
        followup_cap = _followup_cap(max_followups)
        reopen_cap = _reopen_cap(max_reopens)
        next_at = (
            ts + _followup_delay(followups, cadence_name)
            if followups < followup_cap else 0.0
        )
        return {
            **current,
            "last_bot": ts,
            "bot_turns": int(current.get("bot_turns") or 0) + max(1, int(bubbles)),
            "next_at": next_at,
            "reopen_at": (
                ts + random.uniform(*_CADENCE_REOPEN[cadence_name])
                if reopen_cap > 0 else 0.0
            ),
        }

    _mutate(thread_id, apply)


def is_tracked(thread_id: str) -> bool:
    """该线程是否已有追问节奏状态；老会话首次打开时用来做一次 bootstrap。"""
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return False
    return thread_id in _load()


def followups(thread_id: str) -> int:
    """该线程已经发出的追问次数（当前这次生成之前）。

    供生成侧决定「第一次可轻轻追问，第二次起必须换话题」——同一话题最多探一次，
    对齐 starlit-desktop 的 `max_probes_per_topic=1`。
    """
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return 0
    return int(_thread_state(_load(), thread_id).get("followups") or 0)


def due(
    thread_id: str, *, now: float | None = None,
    max_followups: int | None = None, max_reopens: int | None = None,
    max_bot_turns: int | None = None, active_window_minutes: int | None = None,
    quiet_start: str | None = None, quiet_end: str | None = None,
) -> str:
    """返回到期的动作：`followup` / `reopen` / 空串（不发起）。

    `max_followups` / `max_reopens` 由设置注入；None=沿用内置默认。0=该类动作关闭。
    会话窗口、连续 bot 上限、深夜安静时段同样可由设置覆盖。
    """
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return ""
    ts = _timestamp(now)
    state = _thread_state(_load(), thread_id)
    if not state:
        return ""
    last_bot = float(state.get("last_bot") or 0.0)
    last_user = float(state.get("last_user") or 0.0)
    if last_bot <= 0 or last_bot <= last_user:
        return ""  # 用户说过话还没等到角色回复：不追
    bot_turn_cap = (
        _MAX_BOT_TURNS if max_bot_turns is None else max(1, int(max_bot_turns))
    )
    if int(state.get("bot_turns") or 0) >= bot_turn_cap:
        return ""
    if (_in_quiet_hours(
            ts,
            quiet_start if quiet_start is not None else "00:30",
            quiet_end if quiet_end is not None else "08:30",
    ) and int(state.get("quiet_turns") or 0) >= _QUIET_MAX_TURNS):
        return ""
    active_seconds = (
        _ACTIVE_WINDOW_SECONDS if active_window_minutes is None
        else max(0, int(active_window_minutes)) * 60
    )
    active = ts - max(last_user, last_bot) <= active_seconds
    if active:
        followups = int(state.get("followups") or 0)
        next_at = float(state.get("next_at") or 0.0)
        if followups < _followup_cap(max_followups) and next_at > 0 and ts >= next_at:
            return "followup"
        return ""
    reopens = int(state.get("reopens") or 0)
    reopen_at = float(state.get("reopen_at") or 0.0)
    if reopens < _reopen_cap(max_reopens) and reopen_at > 0 and ts >= reopen_at:
        return "reopen"
    return ""


def mark(
    thread_id: str, kind: str, *, now: float | None = None,
    max_followups: int | None = None, max_reopens: int | None = None,
    cadence: str | None = None,
    quiet_start: str | None = None, quiet_end: str | None = None,
) -> None:
    """记录一次追问/重开，并排下一次；空回复也要 mark，避免每轮重试烧钱。"""
    if kind not in ("followup", "reopen"):
        return
    ts = _timestamp(now)
    cadence_name = _cadence(cadence)

    def apply(current: dict[str, Any]) -> dict[str, Any]:
        state = dict(current)
        if kind == "followup":
            followups = int(state.get("followups") or 0) + 1
            state["followups"] = followups
            state["next_at"] = (
                ts + _followup_delay(followups, cadence_name)
                if followups < _followup_cap(max_followups) else 0.0
            )
        else:
            reopens = int(state.get("reopens") or 0) + 1
            state["reopens"] = reopens
            state["reopen_at"] = (
                ts + random.uniform(*_CADENCE_REOPEN[cadence_name])
                if reopens < _reopen_cap(max_reopens) else 0.0
            )
        if _in_quiet_hours(
            ts,
            quiet_start if quiet_start is not None else "00:30",
            quiet_end if quiet_end is not None else "08:30",
        ):
            state["quiet_turns"] = int(state.get("quiet_turns") or 0) + 1
        return state

    _mutate(thread_id, apply)


def reset(thread_id: str) -> None:
    """清空某线程状态（测试/诊断用）。"""
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return
    with _STATE_LOCK:
        state = _load()
        state.pop(thread_id, None)
        _save(state)


def state_path():
    """供测试 monkeypatch / 诊断；运行态文件真源。"""
    return _STATE_PATH
