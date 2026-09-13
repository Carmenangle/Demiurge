"""基于会话快照的手动补表：范围规划、重叠确认、批次 Agent 调用与局部写回。"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.services import (
    chat_snapshot, narrative_memory, narrative_store, structured_output, table_store, table_update,
)
from app.services.structured_contracts import ManualFillResult
from app.services.pathnames import safe_seg

CHRONICLE_UID = "__chronicle__"
# 2026-09-13 补数实锤：不传 max_tokens 时网关走自家默认输出上限，一批多表 ops+纪要的
# 大 JSON 会被截断（「模型未返回完整 JSON」）。8192 远低于 deepseek-v4-flash 输出上限
# 393216（cap_max_tokens 兜底 min 语义），足够容纳整批 ops 与纪要。
_FILL_MAX_TOKENS = 8192
PROGRESS_FILE = "table_progress.json"

# ── 填表进度上报（2026-09-13 用户实锤：十几批要跑几分钟，UI 全程无进度）──────
# run_manual_fill 每批更新，前端轮询读取；key = "<base>|<repo_id>"。
_FILL_PROGRESS: dict[str, dict[str, int]] = {}
_FILL_PROGRESS_LOCK = threading.Lock()


def set_fill_progress(key: str, done: int, total: int) -> None:
    with _FILL_PROGRESS_LOCK:
        _FILL_PROGRESS[key] = {"running": 1, "batch_done": done, "batch_total": total}


def get_fill_progress(key: str) -> dict[str, int]:
    with _FILL_PROGRESS_LOCK:
        state = _FILL_PROGRESS.get(key)
        if not state:
            return {"running": 0, "batch_done": 0, "batch_total": 0}
        return dict(state)


def clear_fill_progress(key: str) -> None:
    with _FILL_PROGRESS_LOCK:
        _FILL_PROGRESS.pop(key, None)


@dataclass
class ManualFillPlan:
    total_turns: int
    requested_start: int
    minimum_unrecorded: int
    needs_confirmation: bool
    starts: dict[str, int]
    # 2026-09-13 用户要求：确认弹窗明示「覆盖后果」——每张表与已处理范围的重叠层数。
    overlap_turns: dict[str, int] = field(default_factory=dict)


@dataclass
class DialogueTurn:
    turn: int
    user: str
    assistant: str


def plan_manual_fill(*, total_turns: int, recent_turns: int, selected: list[str],
                     last_turns: dict[str, int], overwrite: bool | None) -> ManualFillPlan:
    total = max(0, int(total_turns))
    recent = max(1, int(recent_turns))
    requested_start = max(1, total - recent + 1) if total else 1
    requested_count = max(0, total - requested_start + 1)
    unrecorded = {uid: max(0, total - int(last_turns.get(uid, 0))) for uid in selected}
    minimum = min(unrecorded.values(), default=total)
    overlaps = any(requested_count > missing for missing in unrecorded.values())
    # 重叠明细（2026-09-13）：每张表已处理范围与本次请求范围的重叠层数，供确认弹窗
    # 明示覆盖后果（通用表按行覆盖重算 / 纪要追加不删旧）。
    overlap_turns = {
        uid: max(0, min(total, int(last_turns.get(uid, 0))) - requested_start + 1)
        for uid in selected
    }
    overlap_turns = {uid: count for uid, count in overlap_turns.items() if count > 0}
    if overwrite is None and overlaps:
        return ManualFillPlan(total, requested_start, minimum, True, {}, overlap_turns)
    starts = {
        uid: requested_start if overwrite else max(requested_start, int(last_turns.get(uid, 0)) + 1)
        for uid in selected
    }
    return ManualFillPlan(total, requested_start, minimum, False, starts, overlap_turns)


def normalize_ranges(ranges: list[Any] | None, total_turns: int) -> list[tuple[int, int]]:
    """把前端传来的显式回合区间规整成按序不重叠的 [(start, end), ...]（纯逻辑）。

    2026-09-13 用户要求「针对空缺建立索引」：缺口补跑走显式区间模式，允许只处理
    中间某几层。非法项丢弃、越界裁剪到 [1, total_turns]、相邻/重叠区间合并。
    """
    total = max(0, int(total_turns))
    if total <= 0:
        return []
    items: list[tuple[int, int]] = []
    for raw in ranges or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            start, end = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            continue
        start, end = max(1, min(start, end)), min(total, max(start, end))
        if start <= end:
            items.append((start, end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(items):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return merged


def chunk_spans(spans: list[tuple[int, int]], batch_size: int) -> list[tuple[int, int]]:
    """把区间按 batch_size 切成批次 [(start, end), ...]（每段单独起批，不跨缺口）。"""
    size = max(1, int(batch_size))
    batches: list[tuple[int, int]] = []
    for start, end in spans:
        cursor = start
        while cursor <= end:
            batches.append((cursor, min(end, cursor + size - 1)))
            cursor += size
    return batches


def dialogue_turns(messages: list[Any]) -> list[DialogueTurn]:
    """把只含文本的可见快照配成 assistant 回合；图片和媒体槽由 chat_snapshot 过滤。"""
    history = chat_snapshot.to_prompt_history(messages)
    pending_users: list[str] = []
    result: list[DialogueTurn] = []
    for item in history:
        if item["role"] == "user":
            pending_users.append(item["content"])
        elif item["role"] == "assistant":
            result.append(DialogueTurn(
                turn=len(result) + 1,
                user="\n".join(pending_users[-1:]),
                assistant=item["content"],
            ))
            pending_users.clear()
    return result


def _progress_path(base: str, repo_id: str) -> Path:
    return Path(base) / safe_seg(repo_id, strip=False) / PROGRESS_FILE


def load_progress(base: str, repo_id: str) -> dict[str, int]:
    path = _progress_path(base, repo_id)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): max(0, int(value)) for key, value in raw.items()}


def mark_processed(base: str, repo_id: str, table_uids: list[str], turn: int) -> None:
    if not (base and repo_id and table_uids):
        return
    progress = load_progress(base, repo_id)
    for uid in table_uids:
        progress[uid] = max(progress.get(uid, 0), int(turn))
    path = _progress_path(base, repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def last_turns(base: str, repo_id: str, card_name: str,
               selected: list[str]) -> dict[str, int]:
    progress = load_progress(base, repo_id)
    return {
        uid: (
            narrative_store.get_last_turn(base, repo_id, card_name)
            if uid == CHRONICLE_UID else progress.get(uid, 0)
        )
        for uid in selected
    }


def remove_overlapping_chronicles(base: str, repo_id: str, start: int, end: int) -> int:
    return narrative_store.delete_overlapping(base, repo_id, start, end)


def _chronicle_coverage(base: str, repo_id: str, total: int) -> tuple[int, int, int]:
    """纪要表**实际覆盖**统计：(已覆盖层数, 条数, 最远 turn_end)。

    按既有纪要的回合区间并集计数（cap 到 total）——2026-09-13 用户实锤：游标口径
    （last_turn）会把「只写过 1 条 35–37」显示成「未记录 0 层」，明显误导；空白库
    更是 0 条纪要也显示未记录 0 层。游标仍以 last_turn 展示（「进度 T37」）。

    2026-09-13 续：口径改为**未封口**集合（`narrative_store.live_entries`），与界面可见列表、
    位序编号同一集合——否则整理让位后状态页仍报「23 条」而界面只有 12 条。
    """
    entries = narrative_store.live_entries(base, repo_id)
    covered: set[int] = set()
    farthest = 0
    for entry in entries:
        covered.update(range(max(1, entry.turn_start), entry.turn_end + 1))
        farthest = max(farthest, entry.turn_end)
    return min(len(covered), max(0, total)), len(entries), farthest


def session_turn_count(repo_id: str) -> int:
    """当前会话的 assistant 回合数（= `/tables/status` 的 `total_turns`）。

    2026-09-13：区间整理预览原先由前端传 `total_turns`，前端默认传 0 ⇒ 缺口检测恒为空
    （用户实锤「没看到针对空缺建立索引的功能」）。回合数以**会话快照**为唯一真源，
    与填表/状态页同源，故由此处统一外供，调用方不必各自数。
    """
    try:
        return len(dialogue_turns(chat_snapshot.load(repo_id)))
    except Exception:  # noqa: BLE001 — 快照不可读时不猜层数，退回 0（预览不报缺口）
        return 0


def table_status(base: str, repo_id: str, card_name: str, messages: list[Any]) -> dict[str, Any]:
    total = len(dialogue_turns(messages))
    config = table_store.load_config(base, repo_id)
    tables = table_store.load(base, repo_id)
    progress = load_progress(base, repo_id)
    items = []
    for table in tables:
        last = progress.get(str(table.get("uid") or ""), 0)
        items.append({
            "uid": table.get("uid", ""), "name": table.get("name", ""),
            "frequency": max(1, int(config.get("fillEvery", 1))),
            "unrecorded": max(0, total - last), "last_turn": last,
            "selectable": True,
        })
    chron_last = narrative_store.get_last_turn(base, repo_id, card_name)
    covered, chron_count, chron_farthest = _chronicle_coverage(base, repo_id, total)
    items.append({
        "uid": CHRONICLE_UID, "name": "纪要表（往事）",
        "frequency": max(1, int(config.get("chronicleEvery", narrative_memory.CADENCE))),
        # 未记录=未覆盖层数（区间并集口径），游标口径见 last_turn——两者并列展示。
        "unrecorded": max(0, total - covered), "last_turn": chron_last,
        "selectable": True,
        "entries": chron_count, "covered_to": chron_farthest,
    })
    return {"total_turns": total, "items": items, "config": config}


def _manual_system(tables: list[dict[str, Any]], include_chronicle: bool) -> str:
    table_specs = [
        {key: table.get(key) for key in ("name", "columns", "note", "rule", "keyCol", "rows")}
        for table in tables
    ]
    return (
        "你是剧情数据库填表 Agent。只根据给定的有编号对话回合处理本次指定表。"
        "通用表严格遵守列名、身份列和更新规则；同一身份已有行时输出 update，不重复 insert。"
        "纪要表每个批次输出一条丰富纪要，保留概览、完整因果、重要对白和实际出场人物；"
        "概览 overview 不超过30字，详细纪要 chronicle 不超过300字。"
        "只输出 JSON：{\"ops\":[通用表 insert/update/delete 操作],"
        "\"chronicles\":[{\"overview\":\"短概览\",\"chronicle\":\"详细纪要\","
        "\"dialogue\":\"重要对白\",\"characters\":[\"人物\"],\"keywords\":[\"关键词\"]}]}。"
        f"\n指定通用表：{json.dumps(table_specs, ensure_ascii=False)}"
        f"\n是否处理纪要表：{'是' if include_chronicle else '否'}"
    )


def _parse_result(raw: str) -> dict[str, Any]:
    try:
        return structured_output.parse_model(raw, ManualFillResult).model_dump()
    except structured_output.StructuredOutputError as exc:
        raise ValueError(f"填表 Agent 返回结构无效：{exc}") from exc


def run_manual_fill(*, base: str, repo_id: str, card_name: str,
                    selected: list[str], recent_turns: int, batch_turns: int,
                    overwrite: bool | None, base_url: str, api_key: str, model: str,
                    proxy: str, chat_fn: Callable[..., str],
                    ranges: list[Any] | None = None) -> dict[str, Any]:
    messages = chat_snapshot.load(repo_id)
    turns = dialogue_turns(messages)
    previous = last_turns(base, repo_id, card_name, selected)
    batch_size = max(1, int(batch_turns))
    # 缺口补跑（2026-09-13 用户要求「针对空缺建立索引」）：给了显式区间就走区间模式，
    # 批次只覆盖这些层；缺口按定义不与既有纪要重叠 ⇒ 不做覆盖确认。
    spans = normalize_ranges(ranges, len(turns))
    if spans:
        plan = ManualFillPlan(
            total_turns=len(turns), requested_start=spans[0][0], minimum_unrecorded=0,
            needs_confirmation=False, starts={uid: spans[0][0] for uid in selected},
        )
        batches = chunk_spans(spans, batch_size)
    else:
        plan = plan_manual_fill(
            total_turns=len(turns), recent_turns=recent_turns, selected=selected,
            last_turns=previous, overwrite=overwrite,
        )
        if plan.needs_confirmation:
            return {
                "ok": False, "needs_confirmation": True,
                "requested_start": plan.requested_start,
                "minimum_unrecorded": plan.minimum_unrecorded,
                "total_turns": plan.total_turns,
                # 2026-09-13：每张表与已处理范围的重叠层数，前端确认弹窗明示覆盖后果。
                "overlap_turns": plan.overlap_turns,
            }
        batches = [
            (start, min(len(turns), start + batch_size - 1))
            for start in range(plan.requested_start, len(turns) + 1, batch_size)
        ]
    if not selected or not turns or not batches:
        return {"ok": True, "needs_confirmation": False, "processed": 0}

    tables = table_store.load(base, repo_id)
    selected_tables = [table for table in tables if table.get("uid") in selected]
    allowed_names = {str(table.get("name") or "") for table in selected_tables}
    config = table_store.load_config(base, repo_id)
    chronicle_freq = max(1, int(config.get("chronicleEvery", narrative_memory.CADENCE)))
    max_retry = max(0, int(config.get("maxRetry", 0)))   # 2026-09-13：接上既有配置（原裸抛）
    generated_ops: list[dict[str, Any]] = []
    generated_entries: list[narrative_memory.ChronicleEntry] = []
    calls = 0
    failed_batches: list[str] = []
    progress_key = f"{base}|{repo_id}"
    set_fill_progress(progress_key, 0, len(batches))
    try:
        for batch_index, (batch_start, batch_end) in enumerate(batches):
            eligible = [uid for uid in selected if plan.starts.get(uid, len(turns) + 1) <= batch_end]
            if not eligible:
                set_fill_progress(progress_key, batch_index + 1, len(batches))
                continue
            batch_tables = [table for table in selected_tables if table.get("uid") in eligible]
            # 2026-09-13 用户要求：不满一个纪要频率的尾批不出纪要（如 37 层÷每 3 层一卷，
            # 第 37 层单层残留不单独总结）。该层照常进通用表；纪要进度仍推进到 len(turns)，
            # 下一条自动纪要从下个完整频率窗口起（38–40）。
            # 例外：显式缺口补跑（spans）是用户点名要这几层 ⇒ 短区间也出纪要，否则缺口补不上。
            include_chronicle = (
                CHRONICLE_UID in eligible
                and (bool(spans) or (batch_end - batch_start + 1) >= chronicle_freq)
            )
            body = []
            for item in turns[batch_start - 1:batch_end]:
                body.append(f"【第{item.turn}回合·用户】{item.user}\n【第{item.turn}回合·助手】{item.assistant}")
            allowed_ranges = {uid: [max(batch_start, plan.starts[uid]), batch_end] for uid in eligible}
            user = f"各表允许处理范围：{json.dumps(allowed_ranges, ensure_ascii=False)}\n" + "\n\n".join(body)
            # 单批容错（2026-09-13 用户实锤：一批 JSON 截断曾毁掉整次任务、已成功批次
            # 全部白跑）：解析失败按 maxRetry 重试，仍失败记入 failed_batches 并跳过，
            # 已成功批次照常落盘。
            data: dict[str, Any] | None = None
            last_error = ""
            for attempt in range(1 + max_retry):
                raw = chat_fn(base_url, api_key, model, _manual_system(batch_tables, include_chronicle),
                              user, temperature=0.2, proxy=proxy, max_tokens=_FILL_MAX_TOKENS)
                calls += 1
                try:
                    parsed = _parse_result(raw)
                except ValueError as exc:
                    last_error = str(exc)
                    continue
                # 2026-09-13 用户实锤：4–6/7–9 两批静默丢失——模型返回了合法 JSON 但
                # chronicles 为空，旧逻辑既不重试也不反馈。空纪要视为无效输出，重试。
                if include_chronicle and not (
                        isinstance(parsed.get("chronicles"), list) and parsed["chronicles"]):
                    last_error = "模型未返回纪要"
                    continue
                data = parsed
                break
            set_fill_progress(progress_key, batch_index + 1, len(batches))
            if data is None:
                failed_batches.append(
                    f"第{batch_start}–{batch_end}层（{last_error or '未知原因'}）")
                continue
            ops = data.get("ops")
            if isinstance(ops, list):
                # 2026-09-13：模型补表 ops 里的文本同样可能带防拦截 `@` 拆字标记，
                # 落库前机械还原（与独立维护 / 搭车块同一属主）。
                generated_ops.extend(
                    op for op in table_update.sanitize_ops(ops)
                    if isinstance(op, dict) and str(op.get("table") or "") in allowed_names
                )
            if include_chronicle:
                chronicles = data.get("chronicles")
                for raw_entry in (chronicles if isinstance(chronicles, list) else [])[:1]:
                    entry = narrative_memory.parse_rich_summary(
                        json.dumps(raw_entry, ensure_ascii=False),
                        turn_start=max(batch_start, plan.starts[CHRONICLE_UID]), turn_end=batch_end,
                    )
                    # 字数门槛：填表 prompt 已写明前提，超写压缩改写一次，仍超限拒绝落盘
                    if entry is not None and not narrative_memory.chronicle_within_limits(
                            entry.overview, entry.text):
                        compressed = chat_fn(
                            base_url, api_key, model, narrative_memory.COMPRESS_SYSTEM,
                            narrative_memory.build_compress_user(entry.overview, entry.text),
                            temperature=0.2, proxy=proxy, max_tokens=_FILL_MAX_TOKENS)
                        entry = narrative_memory.parse_rich_summary(
                            compressed or "",
                            turn_start=max(batch_start, plan.starts[CHRONICLE_UID]),
                            turn_end=batch_end,
                        )
                    if entry is not None and narrative_memory.chronicle_within_limits(
                            entry.overview, entry.text):
                        generated_entries.append(entry)
                    else:
                        # 2026-09-13：抽取无效/超限不再静默吞掉（旧逻辑 4–6、7–9 两批
                        # 就是这么丢的），记入失败清单让用户看到并可重跑。
                        failed_batches.append(
                            f"第{batch_start}–{batch_end}层（纪要抽取无效或超限）")
    finally:
        clear_fill_progress(progress_key)

    # 2026-09-01 用户定案：纪要表只新建、不更新、不删除（只有用户手动删才允许）。
    # 手动填表补建纪要时只追加新存档，不再删除与消息范围重叠的旧纪要。
    if overwrite and CHRONICLE_UID in selected:
        pass
    applied = table_store.apply_ops(tables, generated_ops)
    if selected_tables and (applied or generated_ops):
        table_store.save(base, repo_id, tables)
    for entry in generated_entries:
        narrative_store.append(base, repo_id, entry)
    # 缺口补跑只覆盖被点名的几层：不能推游标（否则「中间补了一卷」会被记成「37 层全处理过」，
    # 既误导进度又让后续自动纪要跳层）。进度改由覆盖并集（_chronicle_coverage）体现。
    if spans:
        generic_uids: list[str] = []
        advance_frontier = False
    else:
        generic_uids = [uid for uid in selected
                        if uid != CHRONICLE_UID and plan.starts.get(uid, 0) <= len(turns)]
        advance_frontier = plan.starts.get(CHRONICLE_UID, len(turns) + 1) <= len(turns)
    mark_processed(base, repo_id, generic_uids, len(turns))
    if CHRONICLE_UID in selected and advance_frontier:
        narrative_store.set_last_turn(base, repo_id, card_name, len(turns))
    processed = (sum(end - start + 1 for start, end in spans) if spans
                 else len(turns) - plan.requested_start + 1)
    return {
        "ok": True, "needs_confirmation": False, "processed": processed,
        "calls": calls, "applied": applied, "chronicles": len(generated_entries),
        # 2026-09-13：解析失败被跳过的批次（重试耗尽）。空串 = 全部成功。
        "failed_batches": failed_batches,
    }
