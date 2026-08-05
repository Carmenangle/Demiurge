"""基于会话快照的手动补表：范围规划、重叠确认、批次 Agent 调用与局部写回。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.services import chat_snapshot, narrative_memory, narrative_store, table_store
from app.services.pathnames import safe_seg

CHRONICLE_UID = "__chronicle__"
PROGRESS_FILE = "table_progress.json"


@dataclass
class ManualFillPlan:
    total_turns: int
    requested_start: int
    minimum_unrecorded: int
    needs_confirmation: bool
    starts: dict[str, int]


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
    if overwrite is None and overlaps:
        return ManualFillPlan(total, requested_start, minimum, True, {})
    starts = {
        uid: requested_start if overwrite else max(requested_start, int(last_turns.get(uid, 0)) + 1)
        for uid in selected
    }
    return ManualFillPlan(total, requested_start, minimum, False, starts)


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
    items.append({
        "uid": CHRONICLE_UID, "name": "纪要表（往事）",
        "frequency": max(1, int(config.get("chronicleEvery", narrative_memory.CADENCE))),
        "unrecorded": max(0, total - chron_last), "last_turn": chron_last,
        "selectable": True,
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
        "纪要表每个批次输出一条丰富纪要，保留概览、完整因果、重要对白和实际出场人物。"
        "只输出 JSON：{\"ops\":[通用表 insert/update/delete 操作],"
        "\"chronicles\":[{\"overview\":\"短概览\",\"chronicle\":\"详细纪要\","
        "\"dialogue\":\"重要对白\",\"characters\":[\"人物\"],\"keywords\":[\"关键词\"]}]}。"
        f"\n指定通用表：{json.dumps(table_specs, ensure_ascii=False)}"
        f"\n是否处理纪要表：{'是' if include_chronicle else '否'}"
    )


def _parse_result(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw or "")
    if not match:
        raise ValueError("填表 Agent 未返回 JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("填表 Agent 返回结构无效")
    return data


def run_manual_fill(*, base: str, repo_id: str, card_name: str,
                    selected: list[str], recent_turns: int, batch_turns: int,
                    overwrite: bool | None, base_url: str, api_key: str, model: str,
                    proxy: str, chat_fn: Callable[..., str]) -> dict[str, Any]:
    messages = chat_snapshot.load(repo_id)
    turns = dialogue_turns(messages)
    previous = last_turns(base, repo_id, card_name, selected)
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
        }
    if not selected or not turns:
        return {"ok": True, "needs_confirmation": False, "processed": 0}

    tables = table_store.load(base, repo_id)
    selected_tables = [table for table in tables if table.get("uid") in selected]
    allowed_names = {str(table.get("name") or "") for table in selected_tables}
    batch_size = max(1, int(batch_turns))
    generated_ops: list[dict[str, Any]] = []
    generated_entries: list[narrative_memory.ChronicleEntry] = []
    calls = 0
    for batch_start in range(plan.requested_start, len(turns) + 1, batch_size):
        batch_end = min(len(turns), batch_start + batch_size - 1)
        eligible = [uid for uid in selected if plan.starts.get(uid, len(turns) + 1) <= batch_end]
        if not eligible:
            continue
        batch_tables = [table for table in selected_tables if table.get("uid") in eligible]
        include_chronicle = CHRONICLE_UID in eligible
        body = []
        for item in turns[batch_start - 1:batch_end]:
            body.append(f"【第{item.turn}回合·用户】{item.user}\n【第{item.turn}回合·助手】{item.assistant}")
        ranges = {uid: [max(batch_start, plan.starts[uid]), batch_end] for uid in eligible}
        user = f"各表允许处理范围：{json.dumps(ranges, ensure_ascii=False)}\n" + "\n\n".join(body)
        raw = chat_fn(base_url, api_key, model, _manual_system(batch_tables, include_chronicle),
                      user, temperature=0.2, proxy=proxy)
        data = _parse_result(raw)
        calls += 1
        ops = data.get("ops")
        if isinstance(ops, list):
            generated_ops.extend(
                op for op in ops if isinstance(op, dict) and str(op.get("table") or "") in allowed_names
            )
        if include_chronicle:
            chronicles = data.get("chronicles")
            if isinstance(chronicles, list):
                for raw_entry in chronicles[:1]:
                    entry = narrative_memory.parse_rich_summary(
                        json.dumps(raw_entry, ensure_ascii=False),
                        turn_start=max(batch_start, plan.starts[CHRONICLE_UID]), turn_end=batch_end,
                    )
                    if entry is not None:
                        generated_entries.append(entry)

    if overwrite and CHRONICLE_UID in selected:
        remove_overlapping_chronicles(base, repo_id, plan.requested_start, len(turns))
    applied = table_store.apply_ops(tables, generated_ops)
    if selected_tables and (applied or generated_ops):
        table_store.save(base, repo_id, tables)
    for entry in generated_entries:
        narrative_store.append(base, repo_id, entry)
    generic_uids = [uid for uid in selected if uid != CHRONICLE_UID and plan.starts.get(uid, 0) <= len(turns)]
    mark_processed(base, repo_id, generic_uids, len(turns))
    if CHRONICLE_UID in selected and plan.starts.get(CHRONICLE_UID, len(turns) + 1) <= len(turns):
        narrative_store.set_last_turn(base, repo_id, card_name, len(turns))
    return {
        "ok": True, "needs_confirmation": False, "processed": len(turns) - plan.requested_start + 1,
        "calls": calls, "applied": applied, "chronicles": len(generated_entries),
    }
