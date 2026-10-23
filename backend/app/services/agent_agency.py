"""能动性核心编排域（agent_agency）：回合前 prelude/propose + 写回事务 _agency_writeback。

2026-10-23 方案 B 再拆（2589 → 666 行）：四个域已迁出为独立属主，本模块只留核心编排，
旧名经下方 re-export 门面零破坏（方法论见 docs/memory/agent-graph-seam-plan-2026-09-16.md）：
- agency_maintenance          维护链（表格/信念/文风，含 _MAINTENANCE_* 共享状态）
- agency_illustration_actors  主体解析底座（角色/性别/锚点意图/提示词助手）
- agency_scene_state          场景状态域（媒体块拆分/状态写回/CI/在场信号/初选提示词）
- agency_scene_comfy          ComfyUI 编译域（七段接缝 + 多批 extras + 视频计划提取）

⚠ _agency_writeback 对已迁接缝一律走**模块属性调用**（agency_scene_state._wb_x /
agency_scene_comfy._wb_x）：测试补丁打新属主时，主函数的直调路径才一并生效
（判据＝谁在调用期查这个名字；bare-name 走本模块门面绑定会绕过补丁）。
"""

from __future__ import annotations
from app.services.agent_contracts import CtxLike
import logging
import re
import traceback
from app.services import (
    agency_scene_comfy,
    agency_scene_state,
    agent_context,
    builtin_agents,
    run_trace,
)
from app.services import llm as _llm
from app.services.agent_support import (  # noqa: F401  跨簇底座
    _proxy_kw,
)
from app.services.agent_retrieval import (  # noqa: F401  跨簇底座
    _curator_index_fn,
    _reindex_retrieval_tables,
    _repo_table_absent_actors,
    _repo_tables_for_facts,
)
from app.services.agent_context_assembly import (  # noqa: F401  跨簇底座
    _apply_regex,
    _curator_worldbook_context_fn,
    _curator_worldbook_fn,
    _history_messages,
    _illustration_appearance,
    _mentioned_bound_names,
    _profile_llm_fallback,
)
from app.services.agent_fabric import (  # noqa: F401  跨簇底座
    _live_chat,
)
from app.services.agency_maintenance import (  # noqa: F401  兼容 re-export
    _should_fill,
    _apply_table_ops,
    _MAINTENANCE_THREADS,
    _MAINTENANCE_LOCKS,
    _MAINTENANCE_RUNS,
    _maintenance_lock,
    join_maintenance_threads,
    _agency_maintenance_async,
    _agency_maintenance,
    _belief_maintenance,
    _table_maintenance,
    _MAINTENANCE_THREADS_LOCK,
    _maintenance_enter,
    _maintenance_leave,
    maintenance_snapshot,
)
from app.services.agency_illustration_actors import (  # noqa: F401  兼容 re-export
    _status_snapshot_value,
    _illustration_visual_query,
    _ordered_illustration_names,
    _resolve_illustration_request_actors,
    _protagonist_gender,
    _subject_gender_map,
    _filter_illustration_appearance,
    _resolve_prev_tail_desc,
    _ANCHOR_INTENT_TIMEOUT_SECONDS,
    _frame_subject_names,
    _resolve_anchor_intent_list,
    _anchor_batch_count,
    _anchor_intent_viewpoint_hint,
    _illustration_anchor_intent,
    _illustration_anchor_offset,
    _build_image_prompt,
)
from app.services.agency_scene_state import (  # noqa: F401  兼容 re-export
    _wb_split_media_blocks,
    _wb_apply_state,
    _wb_ratchet_sources,
    _wb_setting_sources,
    _wb_run_narrative_ci,
    _wb_at_climax,
    _wb_scene_signals,
    _wb_build_audio_request,
    _wb_initial_illustration_prompt,
    _wb_illustration_reason,
)
from app.services.agency_scene_comfy import (  # noqa: F401  兼容 re-export
    _extract_video_action_plan,
    _wb_comfy_actor_pool,
    _wb_comfy_scene_target,
    _wb_first_person_frame_filter,
    _wb_comfy_scene_spec,
    _wb_comfy_profile,
    _wb_comfy_illustrate_env,
    _wb_frames_and_video,
    _wb_emit_illustration_trace,
    _wb_comfy_extra_requests,
)

_LOG = logging.getLogger("uvicorn.error")

def _builtin(ctx: CtxLike, agent_id: str, field_name: str, fallback):
    """取内置 Agent 生效参数（含用户覆盖）：优先运行时 ctx.builtin，缺失回退硬编码默认。"""
    table = ctx.get("builtin") or {}
    slot = table.get(agent_id) if isinstance(table, dict) else None
    if isinstance(slot, dict) and field_name in slot:
        return slot[field_name]
    return fallback


def _builtin_sampling(ctx: CtxLike, agent_id: str) -> dict:
    """取某内置 Agent 生效的 top_p/max_tokens（None 则不传，用模型默认）。供 chat 调用透传。"""
    out: dict = {}
    tp = _builtin(ctx, agent_id, "topP", None)
    if isinstance(tp, (int, float)) and not isinstance(tp, bool):
        out["top_p"] = float(tp)
    mt = _builtin(ctx, agent_id, "maxTokens", None)
    if isinstance(mt, int) and not isinstance(mt, bool) and mt > 0:
        out["max_tokens"] = mt
    return out


def _world_event_catalog(base: str, repo_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """从当前仓库世界书快照机械提取「可登门角色」与「事件锚点」白名单。

    角色 = `角色卡·X` 条目的 X；事件 = `E<n>` 编号条目或含「大事件」的条目（排除机制层）。
    读不到快照 / 空书 → 空集 ⇒ 世界事件提案降级关闭，只保留 NPC 自主行动与用户行动裁定
    （老仓库无世界书时不得凭空造人）。
    """
    try:
        from app.services import worldbook_store
        book = worldbook_store.read_repo_snapshot(base, repo_id)
    except Exception:  # noqa: BLE001 [EXC-保留] 来源清单读取失败不得打断主生成
        return (), ()
    if not isinstance(book, dict):
        return (), ()
    raw = book.get("entries")
    entries = raw.values() if isinstance(raw, dict) else raw
    names: list[str] = []
    sources: list[str] = []
    for item in entries or []:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        comment = str(item.get("comment") or "").strip()
        if not comment:
            continue
        if comment.startswith("角色卡·"):
            name = comment.split("·", 1)[1].strip()
            if name and name not in names:
                names.append(name)
        elif (re.match(r"^E\d+\b", comment)
              or ("大事件" in comment and not comment.startswith("系统判定机制"))):
            if comment not in sources:
                sources.append(comment)
    return tuple(names), tuple(sources)


def _has_card(ctx: CtxLike) -> bool:
    """本作品是否关联角色卡（有卡=剧情扮演可用，对话默认走 roleplay）。"""
    return bool((ctx.get("card_name") or "").strip() and (ctx.get("character_dir") or "").strip())




def _streamed_illustration_events(recs: list[dict]) -> list[dict]:
    """正文已流式显示时，只发送带最终正文偏移的插画槽，避免正文重复发送。"""
    events = []
    for rec in recs:
        request = {
            "prompt": rec.get("prompt") or "",
            "motion": rec.get("motion") or 0,
            "actors": rec.get("actors") or [],
            "offset": max(0, int(rec.get("anchor_offset") or 0)),
        }
        # V1.5/B1：视频协议可选字段透传（有值才带；旧前端/旧数据宽松忽略）
        for _key in ("video_mode", "first_frame_desc", "last_frame_desc",
                     "prev_tail_desc", "last_frame_url", "transition"):
            _value = rec.get(_key)
            if isinstance(_value, str) and _value:
                request[_key] = _value
        # 正常链路：comfy_video 开启时 produce 层才编译 video_request，这里随事件下发
        _video_request = _video_request_for(rec)
        if _video_request:
            _prompt = (_video_request.get("submit") or {}).get("prompt") or ""
            if _prompt:
                request["video_prompt"] = _prompt
            request["video_params"] = _video_params_payload(_video_request)
        # W3 转场视频（坑F/坑G）：produce 层已编译 transition_video_request，随事件下发转场提示词+参数
        _transition_vr = rec.get("transition_video_request")
        if isinstance(_transition_vr, dict):
            _tprompt = (_transition_vr.get("submit") or {}).get("prompt") or ""
            if _tprompt:
                request["transition_video_prompt"] = _tprompt
            request["transition_video_params"] = _video_params_payload(_transition_vr)
        if isinstance(rec.get("scene_spec"), dict) and rec["scene_spec"]:
            request["scene_spec"] = rec["scene_spec"]
        events.append({"illustrate_request": request, "id": rec.get("id")})
    return events


def _video_request_for(rec: dict) -> dict | None:
    """正常链路：只复用 produce 层在 comfy_video 开启时编译的 video_request。

    未配置视频工作流模板（comfy_video 关）时 produce 层不编译，事件不下发
    video_prompt/video_params——视频链与图/音链一致，关=零成本（不再 dry-run 供测试）。
    """
    vr = rec.get("video_request")
    return vr if isinstance(vr, dict) and vr else None


def _video_params_payload(vr: dict) -> dict:
    """从 build_video_request 结果抽「视频参数」结构（供人核对参数是否正确上传）。"""
    submit = vr.get("submit")
    if not isinstance(submit, dict):
        submit = {}
    return {
        "mode": vr.get("mode") or "climax",
        "model": str(submit.get("model") or ""),
        "size": str(submit.get("size") or ""),
        "endpoint": str(submit.get("endpoint") or ""),
        "images": list(submit.get("images") or []),
        "reference_binding": vr.get("reference_binding") or {},
        "warnings": list(vr.get("warnings") or []),
    }


def _agency_prelude(ctx: CtxLike, text: str):
    """组装能动性子图依赖 + 算 turn + 读当前好感度 + state 注入块。

    无卡 / 无 output_dir → deps=None（整条子图静默跳过，回退纯扮演）。
    turn = 已发生 assistant 轮次 + 1（供 delta 打标 + 插画每N段判定）。
    """
    if not _has_card(ctx) or not (ctx.get("output_dir") or "").strip():
        return None, 0, None, ""
    try:
        import random as _random
        from app.services import character_state, roleplay_agency
        base = ctx["output_dir"]
        repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
        card_name = ctx.get("card_name") or ""
        turn = _next_story_turn(ctx)
        # ③ 世界 Agent / 裁判参数按 ctx.builtin 生效值注入（用户可覆盖），缺失回退硬编码默认。
        deps = roleplay_agency.AgencyDeps(
            chat_fn=_llm.chat, rng=_random.Random(), state_base=base,
            renderer=_build_renderer(ctx),
            thresholds=list(_builtin(ctx, "judge", "tiers", builtin_agents.DEFAULT_TIERS)),
            world_system=_builtin(ctx, "world", "systemPrompt", builtin_agents.WORLD_SYSTEM),
            world_temperature=_builtin(ctx, "world", "temperature", builtin_agents.WORLD_TEMPERATURE),
            gate_floor=_builtin(ctx, "judge", "gateFloor", builtin_agents.GATE_FLOOR),
            gate_base_rate=_builtin(ctx, "judge", "gateBaseRate", builtin_agents.GATE_BASE_RATE),
            curator_system=_builtin(ctx, "curator", "systemPrompt", builtin_agents.CURATOR_SYSTEM),
            curator_temperature=_builtin(ctx, "curator", "temperature", builtin_agents.CURATOR_TEMPERATURE),
            curator_gate=float(_builtin(ctx, "curator", "gate", 1.0) or 0.0),
            index_fn=_curator_index_fn(ctx, repo_id),
            worldbook_context_fn=_curator_worldbook_context_fn(ctx, repo_id),
            worldbook_fn=_curator_worldbook_fn(ctx, repo_id),
            world_sampling=_builtin_sampling(ctx, "world"),
            curator_sampling=_builtin_sampling(ctx, "curator"))
        deps.trace_fn = lambda event, **data: run_trace.emit(ctx, event, **data)
        # 2026-10-21 世界事件司门：eventsEnabled=0 关闭事件提案；清单来自当前仓库世界书快照，
        # 供 consult_world 机械校验 who/source（防凭空造人）并写进世界 Agent 的输入。
        deps.events_enabled = bool(
            float(_builtin(ctx, "judge", "eventsEnabled", builtin_agents.EVENTS_ENABLED) or 0.0) > 0)
        deps.event_names, deps.event_sources = _world_event_catalog(base, repo_id)
        st = character_state.load_state(base, repo_id, card_name)
        # 供回合内动态注入复用同一份 state（待建好感度字段清单）：本回合只读盘一次，
        # 避免后半段再 load 读到中间态；无卡/无 output_dir 时本函数静默返回，ctx 不写。
        ctx["_character_state"] = st
        deps.affinities = roleplay_agency._affinities(st)
        deps.state_context = character_state.render_state_block(st) if (st.数值 or st.叙事) else ""
        affinity = roleplay_agency._affinity(st)
        # 快照重注入（显示栏延续，抗压缩）+ 数值块（门控 provenance）。从文件重建，不靠历史。
        parts = []
        snap = character_state.render_snapshot_injection(st)
        if snap:
            parts.append(snap)
        if st.数值 or st.叙事:
            parts.append(character_state.render_state_block(st))
        st_block = ("\n\n" + "\n\n".join(parts)) if parts else ""
        return deps, turn, affinity, st_block
    except Exception:  # noqa: BLE001
        return None, 0, None, ""


def _next_story_turn(ctx: CtxLike) -> int:
    """下一剧情回合号（M1 2026-09-06）：优先前端持久化回合号——assistant 楼层绑定
    turnNo，删除/重新生成后不倒退（封口级联靠它对齐）；旧前端/内部调用未提供时
    才退回快照派生（数带正文 assistant 消息 + 1）。

    2026-10-21：**不在后端钳制**。前端已修「水印高于链尾」的跳号（lib/storyTurn.ts），
    后端若再按快照条数压低持久号，会违反 2026-09-06「删除/重生成不倒退」合同
    （合法场景：用户删旧楼层后 persisted 本就远高于快照条数）。
    """
    try:
        persisted = int(ctx.get("story_turn") or 0)
    except (TypeError, ValueError):
        persisted = 0
    if persisted > 0:
        return persisted
    from app.services import chat_snapshot

    thread_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
    full_history = chat_snapshot.load_prompt_history(thread_id) if thread_id else None
    history = full_history if full_history is not None else (ctx.get("history") or [])
    return sum(
        1 for item in history
        if item.get("role") == "assistant"
        and str(item.get("content") or item.get("text") or "").strip()
    ) + 1


def _agency_propose(ctx: CtxLike, deps, affinity, wb: str, text: str = "") -> tuple[str, bool]:
    """阶段 A：世界提案 + 裁判。首轮无好感按中性值交给 World，不静默跳过。"""
    ctx["_agency_goal_deltas"] = []
    if deps is None:
        run_trace.emit(ctx, "agent.skipped", agent="world", reason="agency_unavailable")
        return "", False
    try:
        from app.services import roleplay_agency
        core = _agency_core_context(ctx, wb, text)
        history = agent_context.history_text(ctx)[-1200:].strip()
        scene = "\n\n".join(part for part in (history, text.strip()) if part)
        verdicts = roleplay_agency.consult_world(
            deps, chat_base=ctx["chat_base"], chat_key=ctx["chat_key"],
            chat_model=ctx["chat_model"], core=core, scene=scene, affinity=affinity,
            proxy=ctx.get("chat_proxy", ""))
        if not verdicts:
            return "", False
        ctx["_agency_goal_deltas"] = [
            {
                "field": f"叙事/{verdict.actor}·当前目标", "op": "set",
                "value": verdict.goal,
                "evidence": "World Agent依据在场角色core与本轮场景推导",
            }
            for verdict in verdicts
            if verdict.kind == "npc" and verdict.roll > 0 and verdict.actor and verdict.goal
        ]
        return roleplay_agency.narrative_directive(verdicts), roleplay_agency.agency_lost(verdicts)
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "agent.error", agent="world", error=str(exc))
        return "", False


def _agency_core_context(ctx: CtxLike, wb: str, text: str, *, max_chars: int = 8_000) -> str:
    """从已召回世界书中只取在场 NPC 相关条目，避免把整本书重复交给 World。"""
    persona = (ctx.get("persona") or "").strip()
    history = agent_context.history_text(ctx)[-2_000:]
    scene = f"{history}\n{text}"
    present: list[str] = []
    for raw in re.findall(r"\[在场\]\s*([^\n<]+)", scene):
        for name in re.split(r"[、,，/|与和\s]+", raw):
            name = name.strip(" ·：:（）()")
            if name and name not in {"无", "暂无", "未知"} and name not in present:
                present.append(name)
    chunks = [chunk.strip() for chunk in re.split(r"\n\n(?=- 【)", wb or "") if chunk.strip()]
    selected = [chunk for chunk in chunks if any(name in chunk for name in present)]
    if not selected and wb.strip():
        selected = chunks[:2] if chunks else [wb.strip()]
    parts = [part for part in (persona, "\n\n".join(selected)) if part]
    return "\n\n".join(parts)[:max_chars]






def _visible_roleplay_text(reply: str) -> str:
    """后处理失败时仍剥离内部控制块，禁止把状态、表格和生图提示词暴露给用户。

    与主链同源（2026-08-31 根因修复）：先拆 think 前缀，块提取只看正文，发布时拼回——
    防止 think 内幻影协议标签的跨界匹配吞掉正文。
    """
    from app.services import roleplay_agency

    think_head, body = roleplay_agency.split_think_prefix(reply)
    clean = body
    try:
        from app.services import image_prompt_extract

        clean, _ = image_prompt_extract.extract_illustration_plan(clean)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 剥离失败不阻断，但生图提示词块会漏给用户
        _LOG.warning("生图提示词块剥离失败（该块可能泄漏到用户可见文本）: %s", exc)
    try:
        from app.services import roleplay_agency

        clean, _ = roleplay_agency.parse_state_block(clean)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 剥离失败不阻断，但状态块会漏给用户
        _LOG.warning("状态块剥离失败（该块可能泄漏到用户可见文本）: %s", exc)
    try:
        from app.services import table_update

        clean, _ = table_update.parse_table_block(clean)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 剥离失败不阻断，但表格块会漏给用户
        _LOG.warning("表格块剥离失败（该块可能泄漏到用户可见文本）: %s", exc)
    return (think_head + clean).strip()




































































def _agency_writeback(ctx: CtxLike, deps, reply: str, turn: int, affinity,
                      lost: bool, rag_events: list | None = None,
                      user_text: str = "") -> tuple[str, list, dict, dict]:
    """阶段 C+D：剥离 <状态更新> 写回 → 判插画 → 提取对白配音。返回（去块正文, image_recs, illustrate_req, audio_req）。

    illustrate_req：comfy_illustrate 时高潮点产出的出图请求 {prompt}；前端据本地预设模板走异步 ComfyUI 闭环。
    audio_req：comfy_audio 时产出的对白配音请求 {lines:[{speaker,text,emotion}]}；前端逐角色提交 IndexTTS。
    非 comfy 路径为空 dict。rag_events：可选，收集 RAG 创建（纪要/知识库）状态供前端弹窗。"""
    try:
        from app.services import roleplay_agency
        repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
        card_name = ctx.get("card_name") or ""
        # 阶段 C 前段：think 前缀拆出 + <transition>/<illustration>/<audio> 搭车块抽取
        think_head, clean, transition_decision, illustration_plan, audio_plan = (
            agency_scene_state._wb_split_media_blocks(ctx, reply))
        # 阶段 C：状态写回 + 快照兜底 + 旧表格块清洗
        clean, raw, before, after, snapshot, st = agency_scene_state._wb_apply_state(
            ctx, deps, clean, turn=turn, repo_id=repo_id, card_name=card_name)
        # 连续性 CI（永不阻断或改写正文）
        agency_scene_state._wb_run_narrative_ci(ctx, clean, turn=turn, raw=raw, repo_id=repo_id)
        # 阶段 D 前置：可见正文 + 在场/衣着/所在 + 高潮触发信号
        scene = ctx.get("scene") or ""
        sig = agency_scene_state._wb_scene_signals(
            ctx, deps, st, clean, snapshot, scene, user_text, illustration_plan, lost)
        wardrobe, locale = sig["wardrobe"], sig["locale"]
        # 音频对白配音请求：comfy_audio 时组装（含机械降级兜底），随 writeback 返回给前端逐角色提交。
        audio_req = agency_scene_state._wb_build_audio_request(ctx, audio_plan, sig["visible_story"])
        # 初选出图提示词/动作/角色（comfy 与非 comfy 两条路径共用）
        prompt_override, motion, actors = agency_scene_state._wb_initial_illustration_prompt(
            ctx, deps, illustration_plan, sig, user_text=user_text)
        # comfy_illustrate：不同步 render，把 prompt + motion + actors 作为出图请求返回，
        # 前端据 motion 智能选图/视频、据 actors 按角色选 LoRA/底图，走异步 ComfyUI 闭环。
        # 七段接缝依次组装（候选角色池 → 提示词/锚点 → scene_spec → Profile →
        # 请求外壳 → 首尾帧与视频 → 事件派发），判定逻辑与文案逐字未改。
        if ctx.get("comfy_illustrate"):
            pool = agency_scene_comfy._wb_comfy_actor_pool(
                ctx, repo_id, card_name, illustration_plan, actors, sig,
                user_text=user_text)
            target = agency_scene_comfy._wb_comfy_scene_target(
                ctx, repo_id, illustration_plan, clean, sig, pool,
                prompt_override=prompt_override, motion=motion, user_text=user_text)
            scene_spec = agency_scene_comfy._wb_comfy_scene_spec(
                ctx, repo_id, illustration_plan, clean, sig, pool, target)
            # 第 57 刀 ③：第一人称「画面事实筛分」——必须在 Profile 编译**之前**
            # （`_wb_comfy_profile` 会按字段账本强制补齐 appearance/wardrobe，
            #   剔晚了就来不及：改完的稿子里那些装不下的部位已经写进去了）。
            agency_scene_comfy._wb_first_person_frame_filter(ctx, scene_spec)
            scene_spec = agency_scene_comfy._wb_comfy_profile(
                ctx, illustration_plan, scene_spec, target["plan_retargeted"])
            illustrate_req = agency_scene_comfy._wb_comfy_illustrate_env(
                ctx, illustration_plan, sig, target, scene_spec, motion=target["motion"])
            video_prompt_text = agency_scene_comfy._wb_frames_and_video(
                ctx, illustrate_req, scene_spec, clean, transition_decision,
                target["motion"])
            agency_scene_comfy._wb_emit_illustration_trace(
                ctx, illustrate_req, plan=illustration_plan, sig=sig, pool=pool,
                target=target, scene=scene, local_scene=sig["local_scene"],
                video_prompt_text=video_prompt_text)
            # 第 57 刀 · 多批派发：为其余锚点各编译一条请求，随主请求一起交给 finalize_turn
            # 展开成 N 条 rec（各自 anchor_offset ⇒ 各自插到自己的正文位置）。
            # ⚠ `illustrate_req` 为空（非高潮点）时不挂键 ⇒ 行为与改造前一致。
            if illustrate_req:
                extras = agency_scene_comfy._wb_comfy_extra_requests(
                    ctx, repo_id, illustration_plan, clean, sig, pool,
                    primary_anchor=str(target["requested_anchor"] or ""),
                    primary_motion=target["motion"],
                    prompt_override=prompt_override,
                    transition_decision=transition_decision,
                    user_text=user_text,
                    primary_prompt=str(illustrate_req.get("prompt") or ""),
                )
                if extras:
                    illustrate_req["extra_requests"] = extras
            return think_head + clean, [], illustrate_req, audio_req
        illo = roleplay_agency.maybe_illustrate(
            deps, paragraph=clean, appearance=_illustration_appearance(ctx),
            wardrobe=wardrobe, locale=locale,
            actors=actors or ([card_name] if card_name else []), before=before, after=after,
            turn=turn, cadence=0, explicit=bool(illustration_plan), lost=lost,
            scene=scene, prompt_override=prompt_override,
            character_encounter=sig["character_encounter"])
        if illo:
            # 2026-10-16 修复（用户实锤：云端生成时画布只有参考图节点、没有生成内容节点）：
            # 此前云端插画只把 URL 通过 SSE 下发、**从不入库**，而画布内容节点只从 RAG 的
            # generation 记录投影（rag_store.list_generations）⇒ 结构性看不到产物。
            # 改为经 persist_image 落盘 + RAG 入库（画布/资产库可见），但
            # **write_snapshot=False** 保持 roleplay_agency 的原设计「绝不把图回灌进对话历史」。
            # 与 generation_approval / image_agent 的云端路径逐字同源。
            from app.services import generation_store
            _illo_prompt = prompt_override or str(illo.get("caption") or "")
            rec = generation_store.persist_image(
                str(ctx.get("thread_id") or repo_id), repo_id,
                _illo_prompt,
                str(illo["url"]),
                str(ctx.get("output_dir") or ""),
                str(ctx.get("embed_base") or ""),
                str(ctx.get("embed_key") or ""),
                str(ctx.get("embed_model") or ""),
                {"kind": "ai-image", "prompt": _illo_prompt, "images": []},
                embed_proxy=str(ctx.get("embed_proxy") or ""),
                gen_proxy=str(ctx.get("gen_proxy") or ""),
                write_snapshot=False,
            )
            rec["caption"] = illo["caption"]  # 下游沿用原 caption 字段
            return think_head + clean, [rec], {}, audio_req
        return think_head + clean, [], {}, audio_req
    except Exception as exc:  # noqa: BLE001
        # 插桩（2026-08-29）：writeback failed 等异常文本在工作区源码搜不到，必须自曝来源。
        run_trace.emit(
            ctx, "illustration.pipeline", status="error", error=str(exc),
            error_type=type(exc).__name__,
            error_trace=traceback.format_exc()[-1200:],
        )
        return _visible_roleplay_text(reply), [], {}, {}




def _emit_roleplay_ready(ctx: CtxLike, out: dict) -> bool:
    """正文最终化后立即发正文和媒体任务；返回是否已走即时通道。"""
    sink = ctx.get("stream_sink")
    if not callable(sink) or not out.get("result_text"):
        # 2026-10-20 真机（18:22 轮）：后端建好了插画槽（`illustration.slot` 有记录），
        # 但前端从未认领（无 `illustration.claim`），4 分钟后被快照加载的孤儿清理删掉
        # ⇒ 用户看到「插槽位置都没了」。这一环此前**完全无观测**，落一条记录
        # sink 是否可用 / 正文是否为空 / 待发插画条数，下次能直接定位断在哪。
        try:
            run_trace.emit(
                ctx, "illustration.emit_ready", status="skipped",
                sink_callable=callable(sink),
                result_text_chars=len(str(out.get("result_text") or "")),
                illustrate_recs=len(out.get("illustrate_recs") or []),
            )
        except Exception:  # noqa: BLE001 [EXC-保留] 观测失败不得打断出图链
            pass
        return False
    sink({"replace": out["result_text"]})
    _illustration_events = _streamed_illustration_events(out.get("illustrate_recs") or [])
    for event in _illustration_events:
        sink(event)
    # 2026-10-20：记录「发了几条插画事件」——与前端 `illustration.claim` 对账，
    # 能一眼分清「后端没发」（本事件 events=0）与「前端没认领」（本事件 events>0 但无 claim）。
    try:
        run_trace.emit(
            ctx, "illustration.emit_ready", status="emitted",
            illustrate_recs=len(out.get("illustrate_recs") or []),
            events=len(_illustration_events),
        )
    except Exception:  # noqa: BLE001 [EXC-保留] 观测失败不得打断出图链
        pass
    for rec in out.get("image_recs") or []:
        sink({"image": rec.get("url"), "id": rec.get("id"),
              "regeneration": rec.get("regeneration")})
    # 音频对白配音与插画同属即时通道：漏发会导致 eager 分支跳过 audio_request，
    # 前端永远收不到台词（日志有 emit、SSE 无事件）。格式对齐 agent_graph 的 yield。
    for rec in out.get("audio_recs") or []:
        sink({"audio_request": {"lines": rec.get("lines") or []}, "id": rec.get("id")})
    return True




def _build_renderer(ctx: CtxLike):
    """按设置构建插画 renderer。默认 None（实时对话链路不自动付费出图）。

    前端插画开关（illustrate=True）+ 已配置生图模型 → 复用生图配置建云端 renderer，
    接通能动性 D 阶段自动配图。缺 base/model 则仍 None（静默不出图，不报错）。
    """
    if not ctx.get("illustrate"):
        return None
    # 前端已预设 ComfyUI 模板：走异步 illustrate_request 闭环，后端不再同步付费出图。
    if ctx.get("comfy_illustrate"):
        return None
    base = (ctx.get("gen_base") or "").strip()
    model = (ctx.get("gen_model") or "").strip()
    if not (base and model):
        return None
    from app.services import scene_renderers
    cbi = ctx.get("character_base_images")
    cfg = scene_renderers.CloudConfig(
        base_url=base, api_key=ctx.get("gen_key") or "", model=model,
        size=ctx.get("size") or "1024x1024", quality=ctx.get("image_quality") or "high",
        character_base_images=cbi if isinstance(cbi, dict) else {},
        style_base_image=ctx.get("style_base_image") or "",
        proxy=ctx.get("gen_proxy", ""))
    return scene_renderers.cloud_renderer(cfg)

























