"""聊天主动发起判定与冷却（2026-11-09）。

用户口径：像游戏一样——剧情推进、与角色有过共同经历、好感度上升到一定程度后，
角色会自己找上门来聊天。实现为「打开私聊时按需触发」，不做后台常驻推送：

- 有剧情沉淀（往事纪要或非空数据表）——经历是聊天资本；
- 该角色在状态表里已有好感度字段，且 ≥ `_AFFINITY_THRESHOLD`（认识彼此 + 关系够近）；
- 聊天快照闲置 ≥ `_IDLE_SECONDS`（刚聊过不打扰）；
- 距上次主动发起 ≥ `_COOLDOWN_SECONDS`（冷却，避免每次切模式都来一句）。

判定只读；真正生成与落盘在 `chat_room.proactive_turn`。任何一步异常都按「不发起」
处理，绝不阻断正常聊天。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.config import DATA_DIR
from app.services.agent_contracts import CtxLike
from app.services import chat_snapshot, chat_story_context, run_trace

#: 好感度门槛（-100..100 连续标量）：30 ≈ 明显友好，但还不是恋人级。
_AFFINITY_THRESHOLD = 30.0
#: 聊天快照最后写入距今不足该秒数 ⇒ 用户刚聊过，不发主动消息。
_IDLE_SECONDS = 2 * 3600
#: 同一聊天线程两次主动发起的最小间隔。
_COOLDOWN_SECONDS = 6 * 3600
#: 每角色每天最多主动发起几次（设置 `chat_initiative_daily_max`，0=不限）。
_DAILY_MAX = 6
_STATE_PATH = DATA_DIR / "chat_initiative.json"

# 冷却文件读改写锁：并发 mark 不丢其他角色的记录。
_STATE_LOCK = threading.Lock()
# 主动生成占位锁：同一角色线程同时只允许一次 proactive 生成；
# 与 thread_admission 分工——这里防「两次扫描同时通过四闸」，admission 防「用户发送 + 主动」。
_CLAIM_LOCK = threading.Lock()
_CLAIMED: set[str] = set()


def _today(now: float | None = None) -> str:
    """本地日期键（每日上限按用户时区重置）。"""
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def _load() -> dict[str, dict]:
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, dict] = {}
    for key, value in raw.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            # 旧格式 {thread: 上次主动时间}：当日计数从 0 起，跨天自然重置。
            state[str(key)] = {"last": float(value), "day": "", "count": 0}
        elif isinstance(value, dict):
            state[str(key)] = {
                "last": float(value.get("last") or 0.0),
                "day": str(value.get("day") or ""),
                "count": int(value.get("count") or 0),
            }
    return state


def _save(state: dict[str, dict]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 冷却记录写失败只影响节流，不影响聊天


def last_at(thread_id: str) -> float:
    with _STATE_LOCK:
        return float((_load().get(thread_id) or {}).get("last") or 0.0)


def daily_count(thread_id: str, *, now: float | None = None) -> int:
    """该角色线程今天已主动发起的次数（跨天自动归零）。"""
    day = _today(now)
    with _STATE_LOCK:
        record = _load().get(thread_id) or {}
    if str(record.get("day") or "") != day:
        return 0
    return int(record.get("count") or 0)


def mark(thread_id: str, *, now: float | None = None) -> None:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return
    stamp = time.time() if now is None else float(now)
    day = _today(stamp)
    with _STATE_LOCK:
        state = _load()
        record = state.get(thread_id) or {}
        count = int(record.get("count") or 0)
        if str(record.get("day") or "") != day:
            count = 0
        state[thread_id] = {"last": stamp, "day": day, "count": count + 1}
        _save(state)


def claim(thread_id: str) -> bool:
    """原子占位：同一角色线程同时只允许一次主动生成；调用方必须 finally release。"""
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return False
    with _CLAIM_LOCK:
        if thread_id in _CLAIMED:
            return False
        _CLAIMED.add(thread_id)
        return True


def release_claim(thread_id: str) -> None:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return
    with _CLAIM_LOCK:
        _CLAIMED.discard(thread_id)


def affinity_for(ctx: CtxLike, speaker: str) -> float | None:
    """读该角色在状态表里的好感度：优先 `角色·好感度`，回退裸 `好感度`。"""
    base = str(ctx.get("output_dir") or "").strip()
    repo_id = str(ctx.get("repo_id") or "").strip()
    speaker = (speaker or "").strip()
    if not (base and repo_id and speaker):
        return None
    try:
        from app.services import character_state
        state = character_state.load_state(base, repo_id, speaker)
        field = state.数值.get(f"{speaker}·好感度")
        if field is None:
            field = state.数值.get("好感度")
        return float(field.value) if field is not None else None
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 状态读失败按未认识处理
        run_trace.emit(ctx, "chat.initiative_affinity_error", error=str(exc)[:200])
        return None


def _minutes_to_seconds(value: object, default_minutes: float) -> float:
    """设置里的分钟值 → 秒；非法/缺省用内置默认，0 表示取消该门槛。"""
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        minutes = float(default_minutes)
    return max(0.0, minutes) * 60.0


def decide(
    ctx: CtxLike, *, speaker: str, thread_id: str = "", now: float | None = None,
) -> tuple[bool, str]:
    """返回 (是否发起, 原因)。原因用于 trace，不展示给用户。

    `thread_id` 为空时用 ctx 的线程；私聊按角色分线程后，扫描名册必须显式传
    该角色自己的线程，闲置/冷却才不会把别的角色算进来。
    闲置/冷却门槛由设置注入（`chat_initiative_idle_minutes` /
    `chat_initiative_cooldown_minutes`），0 表示取消该门槛。
    """
    thread_id = str(thread_id or ctx.get("thread_id") or "").strip()
    if not thread_id:
        return False, "no_thread"
    if not chat_story_context.has_story_material(ctx):
        return False, "no_story_material"
    affinity = affinity_for(ctx, speaker)
    if affinity is None:
        return False, "no_affinity"
    min_affinity = _AFFINITY_THRESHOLD
    try:
        min_affinity = float(ctx.get("chat_min_affinity"))
    except (TypeError, ValueError):
        pass
    if affinity < min_affinity:
        return False, f"affinity_low:{affinity:g}"
    timestamp = time.time() if now is None else float(now)
    idle_seconds = _minutes_to_seconds(
        ctx.get("chat_initiative_idle_minutes"), _IDLE_SECONDS / 60,
    )
    cooldown_seconds = _minutes_to_seconds(
        ctx.get("chat_initiative_cooldown_minutes"), _COOLDOWN_SECONDS / 60,
    )
    chat_at = chat_snapshot.last_write_at(thread_id)
    idle = timestamp - chat_at
    if idle < idle_seconds:
        # 剧情刚推进时允许「想跟你说说刚才的事」：私聊还没更新，但作品剧情快照
        # 在私聊之后写过（例如表格刚记录她到了海边）。这不算打扰，仍受冷却约束。
        story_at = chat_snapshot.last_write_at(str(ctx.get("repo_id") or "").strip())
        if not (story_at > chat_at):
            return False, f"recent_chat:{int(idle)}s"
    if timestamp - last_at(thread_id) < cooldown_seconds:
        return False, "cooldown"
    # 每日上限：闲置/冷却都设 0 时仍兜住刷屏与 token 成本（0=不限）。
    daily_max = _DAILY_MAX
    try:
        daily_max = int(ctx.get("chat_initiative_daily_max"))
    except (TypeError, ValueError):
        pass
    if daily_max > 0:
        sent = daily_count(thread_id, now=timestamp)
        if sent >= daily_max:
            return False, f"daily_cap:{sent}"
    return True, "ok"


def state_path() -> Path:
    """供测试 monkeypatch / 诊断；运行态文件真源。"""
    return _STATE_PATH
