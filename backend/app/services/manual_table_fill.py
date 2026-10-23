"""基于会话快照的手动补表：范围规划、重叠确认、批次 Agent 调用与局部写回。"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.services import (
    chat_snapshot, narrative_memory, narrative_store, repo_meta, structured_output,
    table_store, table_update,
)
from app.services.structured_contracts import ManualFillResult

CHRONICLE_UID = "__chronicle__"
# 2026-09-13 补数实锤：不传 max_tokens 时网关走自家默认输出上限，一批多表 ops+纪要的
# 大 JSON 会被截断（「模型未返回完整 JSON」）。8192 远低于 deepseek-v4-flash 输出上限
# 393216（cap_max_tokens 兜底 min 语义），足够容纳整批 ops 与纪要。
# 2026-10-20 实锤：第4–4层 / 第8–9层连续报「模型未返回完整 JSON（输出可能被截断）」。
# 单批要同时产出「6 张通用表的 ops」+「1 条纪实」，8192 对层数偏大或模型话多时不够，
# 一旦截断整批作废（含已算好的表格 ops）。抬到 16384（仍远低于各模型输出上限，
# 经 cap_max_tokens 收敛后下发）。
_FILL_MAX_TOKENS = 16384
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


def snapshot_fill_progress() -> dict[str, dict[str, int]]:
    """当前所有**正在跑**的填表批次（`<base>|<repo_id>` → 进度）。

    2026-10-20 用户实锤：「点击整理索引时后台任务还是未看到有任务」。
    原先只有 `get_fill_progress(key)` 这个**单仓库**读口，后台活动面板无从枚举
    ⇒ 「表格整理 / 纪要索引」这个长操作在面板里完全不可见。这里补一个只读快照，
    由 `activity_feed` 投影成 Activity（真源仍在本模块，投影不持有状态）。
    """
    with _FILL_PROGRESS_LOCK:
        return {
            key: dict(state)
            for key, state in _FILL_PROGRESS.items()
            if int(state.get("running") or 0)
        }


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


def normalize_ranges_with_report(
    ranges: list[Any] | None, total_turns: int,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """`normalize_ranges` + 被丢弃区间的清单（2026-10-20 观测补口）。

    为什么要拆出「被丢弃」这一路：越界区间（`start > total`）在旧实现里只是
    `if start <= end` 不成立就**整段消失**，既不处理也不报错 ⇒ 用户点了「补缺口」，
    结果里既没有成功也没有失败，用户只能靠「怎么还没补上」来反推（真机：
    缺口显示第 10–13 层，而 `total_turns` 只有 9 ⇒ 这个区间每次都被静默丢掉）。
    静默丢弃比失败更糟：失败至少有 failed_batches 一行，丢弃什么都没有。

    返回 `(kept, dropped)`；`dropped` 只含**完全落在区间外**的条目（部分越界已裁剪进 kept）。
    """
    total = max(0, int(total_turns))
    if total <= 0:
        # 层数未知时无法判断「是否越界」，故不谎报丢弃。
        return [], []
    items: list[tuple[int, int]] = []
    dropped: list[tuple[int, int]] = []
    for raw in ranges or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            start, end = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            continue
        lo, hi = max(1, min(start, end)), max(start, end)
        clipped_end = min(total, hi)
        if lo > clipped_end:
            dropped.append((lo, hi))
            continue
        items.append((lo, clipped_end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(items):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return merged, dropped


def normalize_ranges(ranges: list[Any] | None, total_turns: int) -> list[tuple[int, int]]:
    """把前端传来的显式回合区间规整成按序不重叠的 [(start, end), ...]（纯逻辑）。

    2026-09-13 用户要求「针对空缺建立索引」：缺口补跑走显式区间模式，允许只处理
    中间某几层。非法项丢弃、越界裁剪到 [1, total_turns]、相邻/重叠区间合并。
    """
    kept, _dropped = normalize_ranges_with_report(ranges, total_turns)
    return kept


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
    return repo_meta.engine_repo_dir(base, repo_id) / PROGRESS_FILE


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


def _compress_to_limits(chat_fn: Callable[..., str], *, base_url: str, api_key: str,
                        model: str, proxy: str, entry: Any,
                        turn_start: int, turn_end: int) -> Any:
    """超限纪要压缩改写：**最多 2 轮，任一达标即收**，返回最后得到的 entry。

    2026-10-21 真机实锤（第 1–3 层永远补不上）：原实现只压一次，压不回来就把整批
    判成「纪要抽取无效或超限」丢掉 —— 而这一批**模型其实正常返回了纪要**（overview
    26 字合格、chronicle 428 字超限 128 字），压缩一次落在 296~320 字之间随机浮动
    ⇒ 同一批时成时败，用户看到的就是「点了补跑，1–3 层还是空的」。
    第 2 轮拿上一轮结果再压（prompt 会带上「现 N 字」，模型会继续收），比直接失败划算。
    字数上限（30/300）是用户 2026-09-13 定稿阈值，此处**不动**；也不做机械截断。
    """
    current = entry
    for _attempt in range(2):
        compressed = chat_fn(
            base_url, api_key, model, narrative_memory.COMPRESS_SYSTEM,
            narrative_memory.build_compress_user(current.overview, current.text),
            temperature=0.2, proxy=proxy, max_tokens=_FILL_MAX_TOKENS)
        parsed = narrative_memory.parse_rich_summary(
            compressed or "", turn_start=turn_start, turn_end=turn_end)
        if parsed is None:
            continue  # 压缩稿解析不动 ⇒ 保留上一版，进入下一轮
        if not parsed.facts and current.facts:
            parsed.facts = current.facts  # 事实账本素材不因压缩丢失
        current = parsed
        if narrative_memory.chronicle_within_limits(parsed.overview, parsed.text):
            return parsed
    return current


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
    spans, dropped_ranges = normalize_ranges_with_report(ranges, len(turns))
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

    # 2026-10-21 用户要求「出现多个 8-9 这类情况要说明是覆盖还是跳过」：
    # 纪要表「只新建、不更新、不删除」（2026-09-01 定案）⇒ 重复跑同一段**必然**并存多条
    # （真机：8-9 变成 3 条，7-9 与 8-9 内容同讲一场婚礼）。补跑前先算「本次范围里已被
    # 纪要覆盖的层」，三态交给调用方：
    #   overwrite=None  ⇒ 需要确认（前端必须明示「覆盖=追加并存 / 跳过」）
    #   overwrite=False ⇒ 跳过：这些层不再出纪要
    #   overwrite=True  ⇒ 覆盖：照常追加（与现有纪要并存，不替换）
    chronicle_covered: set[int] = set()
    if CHRONICLE_UID in selected:
        for entry in narrative_store.live_entries(base, repo_id):
            chronicle_covered.update(
                range(max(1, entry.turn_start), min(entry.turn_end, len(turns)) + 1))
    planned_turns: set[int] = set()
    for _start, _end in batches:
        planned_turns.update(range(_start, _end + 1))
    covered_overlap = sorted(chronicle_covered & planned_turns)
    overlap_ranges = [[a, b] for a, b in narrative_memory.compress_ranges(set(covered_overlap))]
    if overwrite is None and covered_overlap:
        return {
            "ok": False, "needs_confirmation": True, "total_turns": len(turns),
            "requested_start": batches[0][0],
            "minimum_unrecorded": 0,
            # 已被纪要覆盖、且落在本次范围内的层（连续段）——前端据此让用户选覆盖还是跳过。
            "chronicle_overlap": overlap_ranges,
        }

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
    skipped_covered: list[tuple[int, int]] = []
    # 2026-10-21：真正出了纪要的批次区间（用于「覆盖」时精确替换旧条，见落盘段）
    covered_batches: list[tuple[int, int]] = []
    # 2026-10-20：越界区间此前**静默消失**（既不跑也不报错）⇒ 用户只看到「补了但没补上」。
    # 这里把它明写成一条失败，并附上线索（层数口径），便于一眼看出是编号口径不一致。
    for start, end in dropped_ranges:
        failed_batches.append(
            f"第{start}–{end}层（区间越界未处理：本会话仅 {len(turns)} 层"
            f"——缺口若来自另一套回合编号，先统一口径再补）")
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
            # 跳过已覆盖层（overwrite != True）：这批的层已有纪要 ⇒ 再出一条只会并存重复。
            # 通用表照常处理（它是 update 语义，不会被这里影响）。
            if include_chronicle and overwrite is not True and (
                    chronicle_covered & set(range(batch_start, batch_end + 1))):
                include_chronicle = False
                skipped_covered.append((batch_start, batch_end))
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
            system_prompt = _manual_system(batch_tables, include_chronicle)
            # ⚠ 2026-10-21 真机第二次实锤：`maxRetry` 默认 **0** ⇒ 只有 1 次尝试 ⇒
            # 下面那段纠错提示**永远用不上**（4–6 批又一次返回 `[]`，直接判失败）。
            # 结构无效是「模型输出形状错」，值得固定给一次纠错机会，故下限取 1。
            for attempt in range(1 + max(1, max_retry)):
                # 2026-10-21 真机：第 1–3 层连续返回 `[]`（顶层空数组）⇒ 整批判结构无效，
                # 缺口永远补不上。根因是重试**原样重发同一条 prompt**，模型每次都这样退化。
                # ⇒ 首次沿用原 prompt（不改变 4–6/7–9 这些已成功批次的行为），
                #   **只有重试才追加纠错提示**（输出形状 + 「开场/设定层也要概括，不许返空」）。
                prompt = user if attempt == 0 else (
                    f"【上一轮输出不合格：{last_error[:120]}】\n"
                    "必须输出**单个 JSON 对象**（顶层形状 {\"ops\": [...], \"chronicles\": [...]}），"
                    "不要输出数组、更不要输出空数组。\n"
                    "即使某层是开场或设定说明、剧情推进很少，也要用 overview/chronicle 概括"
                    "人物处境与实际发生了什么，不得因此返回空。\n\n" + user)
                raw = chat_fn(base_url, api_key, model, system_prompt,
                              prompt, temperature=0.2, proxy=proxy, max_tokens=_FILL_MAX_TOKENS)
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
                # 2026-10-20 实锤「整理索引跑完了，纪要表还是没更新」：
                # 下面三段都会**静默**失败，且都不进 failed_batches ⇒ 用户只看到「0 条」：
                #   ① 键名不符：只认 `chronicles`（复数列表）；模型给单数 `chronicle` 时
                #      `data.get("chronicles")` 为 None ⇒ 循环不跑、什么都不记；
                #   ② 解析失败：`entry is None` ⇒ 落到 else 记失败（这条本来就有）；
                #   ③ 超限：压缩一次仍超限 ⇒ 记失败。
                # 现在：单数也收；并且**只要本批要求出纪要却一条没产出，必定记进 failed_batches**，
                # 不再让「什么都没发生」伪装成成功（该文件自己的注释就写着「不再静默吞掉」）。
                chronicles = data.get("chronicles")
                if not isinstance(chronicles, list):
                    single = data.get("chronicle")
                    chronicles = [single] if isinstance(single, dict) else []
                produced_here = 0
                chr_start = max(batch_start, plan.starts[CHRONICLE_UID])
                for raw_entry in chronicles[:1]:
                    entry = narrative_memory.parse_rich_summary(
                        json.dumps(raw_entry, ensure_ascii=False),
                        turn_start=chr_start, turn_end=batch_end,
                    )
                    # 字数门槛：填表 prompt 已写明前提，超写走压缩改写（最多 2 轮，任一达标即收）
                    if entry is not None and not narrative_memory.chronicle_within_limits(
                            entry.overview, entry.text):
                        entry = _compress_to_limits(
                            chat_fn, base_url=base_url, api_key=api_key, model=model,
                            proxy=proxy, entry=entry,
                            turn_start=chr_start, turn_end=batch_end)
                    if entry is not None and narrative_memory.chronicle_within_limits(
                            entry.overview, entry.text):
                        generated_entries.append(entry)
                        produced_here += 1
                    else:
                        # 2026-09-13：抽取无效/超限不再静默吞掉（旧逻辑 4–6、7–9 两批
                        # 就是这么丢的），记入失败清单让用户看到并可重跑。
                        failed_batches.append(
                            f"第{batch_start}–{batch_end}层（纪要抽取无效或超限）")
                if include_chronicle and produced_here == 0 and not any(
                        str(batch_start) in item for item in failed_batches):
                    # 本批要求出纪要却一条没产出、且上面没记过原因 ⇒ 记「模型没给 chronicles」
                    keys = ",".join(sorted(str(k) for k in (data or {}).keys())) or "空"
                    failed_batches.append(
                        f"第{batch_start}–{batch_end}层（模型未返回 chronicles；返回键：{keys}）")
                if produced_here > 0:
                    covered_batches.append((chr_start, batch_end))
    finally:
        clear_fill_progress(progress_key)

    # 2026-09-01 定案是「纪要表只新建、不更新、不删除」，当年因此把「删重叠旧条」整段关掉
    # （只留一个 pass）。但 2026-10-21 用户当场质疑：**「覆盖」若不把同层的旧条去掉、只往里
    # 追加，那就名不副实**（真机：6 条重叠里点「确定」后变 9 条，越点越乱）。
    # ⇒ 现在把「用户手点确定」这条路的覆盖做成真的，但用**封口**而不是物理删：
    #   `normalize_apply(seal_rowids=...)` 隐藏旧条（数据保留可审计、可恢复），与「整理索引」
    #   的重复封口同一口径。自动链（overwrite=None/False）完全不受影响，仍是只追加。
    # ⚠ 两条安全约束：① 只在**真生成了纪要**时才封口，新的没来绝不先删旧的；
    #   ② 只封「**被成功批次范围完整包住**」的旧条 —— 部分重叠的保留，避免某批失败就丢层。
    replaced_rowids: list[int] = []
    if overwrite is True and CHRONICLE_UID in selected and covered_batches:
        # 用**成功批次的并集**判断（不是单批）：真机上 5-7 横跨 4-6 与 7-9 两个批，
        # 按单批判断就封不掉它，会留下一条跨批的重叠旧条。
        covered_turns_ok: set[int] = set()
        for start, end in covered_batches:
            covered_turns_ok.update(range(start, end + 1))
        for entry in narrative_store.live_entries(base, repo_id):
            span = set(range(entry.turn_start, entry.turn_end + 1))
            if span and span <= covered_turns_ok:   # 整条都被本次成功范围包住才封
                replaced_rowids.append(entry.rowid)
    applied = table_store.apply_ops(tables, generated_ops)
    if selected_tables and (applied or generated_ops):
        table_store.save(base, repo_id, tables)
    for entry in generated_entries:
        narrative_store.append(base, repo_id, entry)
    sealed_overlap = 0
    if replaced_rowids:
        sealed = narrative_store.normalize_apply(
            base, repo_id, seal_rowids=replaced_rowids)
        sealed_overlap = int(sealed.get("sealed") or 0)
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
        # 2026-10-20：越界被丢弃的区间（同时已写进 failed_batches，这里给程序化消费方）。
        "dropped_ranges": [[start, end] for start, end in dropped_ranges],
        # 2026-10-21：本次范围内已被纪要覆盖的层；以及因「已有纪要」而跳过出纪要的批次。
        # overwrite=True 时 covered_skipped 为空（走覆盖=追加并存），overlap 仍会如实回显。
        "chronicle_overlap": overlap_ranges,
        "covered_skipped": [[start, end] for start, end in skipped_covered],
        # 2026-10-21：覆盖（overwrite=True）时被封口替换掉的旧纪要条数（数据保留可审计）。
        "sealed_overlap": sealed_overlap,
    }
