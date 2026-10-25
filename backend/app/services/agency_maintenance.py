"""能动性维护链：回合后的表格/信念/文风维护（后台线程 + 单一入口锁）。

从 agent_agency.py 按域迁出（方案 B，2026-10-23）：搬移块 sha1 逐字节一致，
旧名经 agent_agency re-export 零破坏。monkeypatch 接缝判据＝谁在调用期查这个名字：
消费方在本模块内的（_agency_maintenance_async→_agency_maintenance→_table_maintenance）
patch 必须打本模块；消费方留驻 agent_agency 的照旧打 agent_agency。"""
from __future__ import annotations

from app.services.agent_contracts import CtxLike
import threading
import time

from app.services import llm as _llm
from app.services import run_trace
from app.services.agent_context_assembly import _history_messages
from app.services.agent_fabric import _live_chat
from app.services.agent_retrieval import _reindex_retrieval_tables
from app.services.agent_support import _proxy_kw


def _should_fill(ctx: CtxLike, repo_id: str, turn: int) -> bool:
    """按填表参数判断本轮是否注入填表指令：轮次 <= skipLatest 不填；否则每 fillEvery 轮填一次。

    默认 fillEvery=1、skipLatest=0，从首轮开始；用户可调低频率以省 token。
    """
    try:
        from app.services import table_store
        cfg = table_store.load_config(ctx.get("output_dir") or "", repo_id)
    except Exception:  # noqa: BLE001
        return True
    if turn <= int(cfg.get("skipLatest", 1)):
        return False
    every = max(1, int(cfg.get("fillEvery", 1)))
    return every <= 1 or (turn % every == 0)

def _apply_table_ops(ctx: CtxLike, repo_id: str, clean: str, ops: list,
                     turn: int, *, mark_empty: bool = True) -> str:
    """应用独立维护调用产出的结构化 ops；不接触对话输出协议。"""
    from app.services import table_store

    output_dir = ctx.get("output_dir") or ""
    tables = table_store.load(output_dir, repo_id)
    if not ops:
        if mark_empty and tables and turn > 0:
            from app.services import manual_table_fill
            processed = table_store.tables_for_maintenance(tables, _should_fill(ctx, repo_id, turn))
            manual_table_fill.mark_processed(
                output_dir, repo_id,
                [str(table.get("uid") or "") for table in processed], turn,
            )
        run_trace.emit(ctx, "table.writeback", status="skipped", reason="no_ops")
        return clean
    cfg = table_store.load_config(output_dir, repo_id)
    effective_ops = ops
    short_reply = len(clean) < int(cfg.get("minReplyLen", 0))
    if short_reply:
        effective_ops = [op for op in ops if isinstance(op, dict)
                         and op.get("table") == table_store.GLOBAL_TABLE]
    applied = table_store.apply_ops(tables, effective_ops) if tables else 0
    if tables and applied:
        table_store.save(output_dir, repo_id, tables)
        _reindex_retrieval_tables(ctx, repo_id, tables)
    if tables and turn > 0:
        from app.services import manual_table_fill
        processed = table_store.tables_for_maintenance(
            tables, False if short_reply else _should_fill(ctx, repo_id, turn),
        )
        manual_table_fill.mark_processed(
            output_dir, repo_id, [str(table.get("uid") or "") for table in processed], turn,
        )
    run_trace.emit(ctx, "table.writeback", status="ok", ops=effective_ops, applied=applied)
    return clean

_MAINTENANCE_THREADS: dict[str, list[threading.Thread]] = {}

_MAINTENANCE_LOCKS: dict[str, threading.Lock] = {}

# 在跑的维护登记：repo_key -> {turn, queued, started_at}。仅供「后台活动」面板投影，
# 生命周期跟随线程（进入 +1、退出 -1，归零即删）；不进任何持久化。
_MAINTENANCE_RUNS: dict[str, dict] = {}

def _maintenance_lock(repo_key: str) -> threading.Lock:
    """同一作品线的先后两轮维护串行化（写表格/纪要不互相踩）。"""
    with _MAINTENANCE_THREADS_LOCK:
        return _MAINTENANCE_LOCKS.setdefault(repo_key, threading.Lock())

def join_maintenance_threads() -> None:
    """测试/关停用：等待所有在途维护线程结束。"""
    with _MAINTENANCE_THREADS_LOCK:
        threads = [t for threads in _MAINTENANCE_THREADS.values() for t in threads]
    for thread in threads:
        thread.join(timeout=120)

def _agency_maintenance_async(ctx: CtxLike, deps, clean: str, turn: int,
                              rag_events: list | None = None) -> None:
    """维护与 turn 完成解耦（2026-08-30 用户实锤：表格维护 LLM 单次 4m52s 挂住对话完成，
    生图早已完毕）。正文/插画发布后立即 turn.completed；维护转后台线程，
    同作品线用锁串行化先后两轮的写入。代价：本轮 rag_events 弹窗不再随结果下发。"""
    import threading

    repo_key = str(ctx.get("repo_id") or ctx.get("thread_id") or "home")
    lock = _maintenance_lock(repo_key)

    def run() -> None:
        _maintenance_enter(repo_key, turn)
        try:
            with lock:
                try:
                    _agency_maintenance(ctx, deps, clean, turn, rag_events)
                except Exception as exc:  # noqa: BLE001 后台维护失败只留痕
                    run_trace.emit(ctx, "memory.maintenance", status="error", error=str(exc))
        finally:
            _maintenance_leave(repo_key)

    thread = threading.Thread(target=run, name=f"maintenance-{repo_key[:8]}", daemon=True)
    with _MAINTENANCE_THREADS_LOCK:
        _MAINTENANCE_THREADS.setdefault(repo_key, []).append(thread)
    thread.start()

def _agency_maintenance(ctx: CtxLike, deps, clean: str, turn: int,
                        rag_events: list | None = None) -> None:
    """正文/插画已发出后的记忆维护；失败不得改写已完成正文。"""
    try:
        from app.services import roleplay_agency
        repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
        card_name = ctx.get("card_name") or ""
        _table_maintenance(ctx, deps, repo_id, clean, turn)
        _belief_maintenance(ctx, repo_id, clean, turn)
        from app.services import narrative_memory, table_store
        cadence = max(1, int(table_store.load_config(
            ctx.get("output_dir") or "", repo_id,
        ).get("chronicleEvery", narrative_memory.CADENCE)))
        recent_messages = _history_messages(ctx)[-max(0, cadence * 2 - 1):]
        history_window = "\n".join(
            f"{message.get('role', '')}: {message.get('content', '')}"
            for message in recent_messages
        )
        window = (history_window + "\nassistant: " + clean).strip()
        roleplay_agency.maybe_summarize(
            deps, repo_id=repo_id, card_name=card_name, window_text=window, turn=turn,
            chat_base=ctx["chat_base"], chat_key=ctx["chat_key"], chat_model=ctx["chat_model"],
            cadence=cadence, events=rag_events, proxy=ctx.get("chat_proxy", ""))
        roleplay_agency.maybe_curate(
            deps, window_text=window, turn=turn,
            chat_base=ctx["chat_base"], chat_key=ctx["chat_key"], chat_model=ctx["chat_model"],
            events=rag_events, proxy=ctx.get("chat_proxy", ""))
        # S2 活人感通审：采样制走维护通道（review_every 控制，0=关），失败静默降级。
        try:
            from app.services import style_review
            style_review.maybe_review(
                cfg=ctx.get("_style_config"), text=clean, turn=turn,
                output_dir=ctx.get("output_dir") or "", repo_id=repo_id,
                chat_base=ctx["chat_base"], chat_key=ctx["chat_key"],
                chat_model=ctx["chat_model"],
                chat_fn=_live_chat(ctx),
                structured_chat_fn=ctx.get("structured_chat_fn"),
                proxy_kwargs=_proxy_kw(ctx),
                trace=lambda event, **data: run_trace.emit(ctx, event, **data))
        except Exception as exc:  # noqa: BLE001 - 通审永不阻断维护
            run_trace.emit(ctx, "style_review", status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "memory.maintenance", status="error", error=str(exc))

def _belief_maintenance(ctx: CtxLike, repo_id: str, clean: str, turn: int) -> None:
    """正文维护：抽取角色认知变化（知道/相信/怀疑/误解/隐瞒/未知）。

    纯规则启发式，零额外 LLM 调用；失败只记 Trace，绝不阻断正文或维护流程。
    """
    try:
        from app.services import belief_extractor
        known_names = [
            str(name).strip()
            for name in (
                (ctx.get("illustration_actor_names") or [])
                + [ctx.get("card_name") or ""]
            )
            if str(name).strip()
        ]
        output_dir = ctx.get("output_dir") or ""
        if not (output_dir and repo_id):
            run_trace.emit(ctx, "belief.extract", status="skipped", reason="no_output_dir")
            return
        result = belief_extractor.ingest(
            output_dir, repo_id, text=clean, turn=turn,
            known_names=known_names, source="auto",
        )
        run_trace.emit(
            ctx, "belief.extract", status="ok",
            extracted=result.get("extracted", 0), recorded=result.get("recorded", 0),
            skipped=result.get("skipped", 0),
            errors=result.get("errors") or [],
        )
    except Exception as exc:  # noqa: BLE001 认知抽取失败不阻断维护
        run_trace.emit(ctx, "belief.extract", status="error", error=str(exc))

def _table_maintenance(ctx: CtxLike, deps, repo_id: str, clean: str, turn: int) -> None:
    """正文发出后独立生成并写回表格 ops；响应只进 Trace，不进入对话。

    2026-09-18 兜底状态抽取：主协议（<状态更新>/<status>）失守的轮次由 ctx 信号
    `_agency_state_fallback`＝(need_deltas, need_snapshot) 驱动，维护 LLM 改收对象
    协议顺带产出状态增量与状态栏快照并补写回（0 额外调用，写回经 roleplay_agency.
    writeback 与主链同源，快照落盘前做好感行对账）；模型回旧数组时优雅降级为只应用
    表格 ops，快照兜底仍由主链 ensure_status_snapshot 承担。need_deltas=False 时
    增量固定不应用——主协议已取得过增量，维护再取会双计。
    """
    try:
        from app.services import character_state, roleplay_agency
        from app.services import table_store, table_update

        output_dir = ctx.get("output_dir") or ""
        need_deltas, need_snapshot = ctx.pop("_agency_state_fallback", (False, False))
        tables = table_store.load(output_dir, repo_id)
        if not tables and not (need_deltas or need_snapshot):
            run_trace.emit(ctx, "agent.skipped", agent="table_maintenance", reason="no_tables")
            return
        cfg = table_store.load_config(output_dir, repo_id)
        scheduled = _should_fill(ctx, repo_id, turn)
        if len(clean) < int(cfg.get("minReplyLen", 0)):
            scheduled = False
        selected = table_store.tables_for_maintenance(tables, scheduled)
        previous_snapshot = ""
        if need_snapshot:
            st = character_state.load_state(output_dir, repo_id, ctx.get("card_name") or "")
            previous_snapshot = getattr(getattr(st, "快照", None), "text", "") or ""
        system = table_update.maintenance_instruction(
            selected, extract_deltas=need_deltas, extract_snapshot=need_snapshot)
        if not system:
            run_trace.emit(ctx, "agent.skipped", agent="table_maintenance", reason="cadence_not_reached")
            return
        user_input = str(ctx.get("message") or "").strip()
        user = f"【本轮用户输入】\n{user_input}\n\n【已生成剧情正文】\n{clean}".strip()
        if need_snapshot and previous_snapshot.strip():
            user += f"\n\n【上轮状态栏】\n{previous_snapshot.strip()}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        run_trace.emit(ctx, "agent.started", agent="table_maintenance")
        run_trace.emit(ctx, "model.request", agent="table_maintenance",
                       model=ctx["chat_model"], messages=messages)
        raw = _llm.chat(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"], system, user,
            temperature=0.2, proxy=ctx.get("chat_proxy", ""), retries=2,
        )
        run_trace.emit(ctx, "model.response", agent="table_maintenance", content=raw or "")
        ops, state_deltas, status_snapshot = table_update.parse_maintenance_payload(raw)
        if ops is None:
            run_trace.emit(ctx, "agent.error", agent="table_maintenance",
                           error="invalid_or_truncated_json")
            return
        _apply_table_ops(ctx, repo_id, clean, ops, turn)
        applied_deltas = state_deltas if need_deltas else []
        applied_snapshot = status_snapshot if need_snapshot else ""
        state_fallback = bool(applied_deltas or applied_snapshot)
        if state_fallback:
            rejected_adds: list = []
            before, after = roleplay_agency.writeback(
                deps, repo_id=repo_id, card_name=ctx.get("card_name") or "",
                raw_deltas=applied_deltas, turn=turn, snapshot=applied_snapshot,
                rejected=rejected_adds)
            run_trace.emit(ctx, "state.writeback", source="maintenance_fallback",
                           raw_deltas=applied_deltas, snapshot=applied_snapshot,
                           affinity_before=before, affinity_after=after,
                           rejected_adds=rejected_adds)
        run_trace.emit(ctx, "agent.completed", agent="table_maintenance",
                       op_count=len(ops), state_fallback=state_fallback)
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "agent.error", agent="table_maintenance", error=str(exc))

_MAINTENANCE_THREADS_LOCK = threading.Lock()

def _maintenance_enter(repo_key: str, turn: int) -> None:
    with _MAINTENANCE_THREADS_LOCK:
        entry = _MAINTENANCE_RUNS.get(repo_key)
        if entry is None:
            entry = {"turn": 0, "queued": 0, "started_at": time.time()}
            _MAINTENANCE_RUNS[repo_key] = entry
        entry["queued"] += 1
        if turn:
            entry["turn"] = turn

def _maintenance_leave(repo_key: str) -> None:
    with _MAINTENANCE_THREADS_LOCK:
        entry = _MAINTENANCE_RUNS.get(repo_key)
        if entry is None:
            return
        entry["queued"] -= 1
        if entry["queued"] <= 0:
            _MAINTENANCE_RUNS.pop(repo_key, None)

def maintenance_snapshot() -> list[dict]:
    """在跑的对话后置维护（供后台活动面板只读投影）。"""
    with _MAINTENANCE_THREADS_LOCK:
        return [
            {
                "repo_id": key,
                "turn": int(entry.get("turn") or 0),
                "queued": int(entry.get("queued") or 0),
                "started_at": float(entry.get("started_at") or 0),
            }
            for key, entry in _MAINTENANCE_RUNS.items()
        ]
