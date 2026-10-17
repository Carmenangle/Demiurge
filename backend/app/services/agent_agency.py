"""agency·维护域（原 agent_graph B 簇，逐字节搬移）。

逐字节自 agent_graph.py 搬移（方案 B 第二步之 B，见 docs/memory/agent-graph-seam-plan-2026-09-16.md）；
行为等价由既有测试网 + 搬移块 sha1 校验保证。
"""

from __future__ import annotations
import logging
import re
import threading
import time
import traceback
from typing import Any
from app.services import agent_context, builtin_agents, run_trace
from app.services import llm as _llm
from app.services import prompt_clean
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

_LOG = logging.getLogger("uvicorn.error")

def _builtin(ctx: dict, agent_id: str, field_name: str, fallback):
    """取内置 Agent 生效参数（含用户覆盖）：优先运行时 ctx.builtin，缺失回退硬编码默认。"""
    table = ctx.get("builtin") or {}
    slot = table.get(agent_id) if isinstance(table, dict) else None
    if isinstance(slot, dict) and field_name in slot:
        return slot[field_name]
    return fallback


def _builtin_sampling(ctx: dict, agent_id: str) -> dict:
    """取某内置 Agent 生效的 top_p/max_tokens（None 则不传，用模型默认）。供 chat 调用透传。"""
    out: dict = {}
    tp = _builtin(ctx, agent_id, "topP", None)
    if isinstance(tp, (int, float)) and not isinstance(tp, bool):
        out["top_p"] = float(tp)
    mt = _builtin(ctx, agent_id, "maxTokens", None)
    if isinstance(mt, int) and not isinstance(mt, bool) and mt > 0:
        out["max_tokens"] = mt
    return out


def _has_card(ctx: dict) -> bool:
    """本作品是否关联角色卡（有卡=剧情扮演可用，对话默认走 roleplay）。"""
    return bool((ctx.get("card_name") or "").strip() and (ctx.get("character_dir") or "").strip())


def _extract_video_action_plan(
    ctx: dict, spec: dict[str, Any], video_mode: str = "climax",
) -> dict[str, Any]:
    """选 A：从剧情原文理解体态，提取视频提示词原料（动作延伸 + 简化外貌/场景）。

    P1/P5 修复：climax [动作] 段曾退化成 subjects.description（外貌），因为
    plan_retargeted 时 action_sequence/visual_facts/composition 被清空，动作链断掉。
    这里直接从纠正后的高潮片段正文（scene_narrative）理解体态，产出：
    - action_sequence：定格动作 → 剧情完整动作的延伸（只写剧情有证据的动作）；
    - subject_scene：在场角色的简化外貌 + 场景视觉描述（去同义形容词堆砌、
      专名视觉展开），只描述在场 actors。

    台词按视频模式分支（用户定稿 2026-08-28）：
    - climax：高潮定格时刻对白通常已经说完，动作窗口内无对白——lines 一律留空；
    - firstlast：首尾帧影片从头到尾覆盖剧情——列出全部对白并按剧情位置标 at_s 时点。

    防拦截（对齐生图链 _profile_llm_fallback，两层）：
    - 输入层：模型输入优先用 protected_narrative（防拦截原文，模型可自行还原），
      system 经 system_with_preset 挂当前防拦截预设；
    - 输出层：parse_video_plan 逐字段丢弃拒答文本；整体无效时带拒答原因重试一次，
      仍无效则返回 {}，调用方回退纯函数兜底。

    失败静默返回 {}，调用方回退现有纯函数兜底，不阻断出图。
    """
    from app.services import image_prompt_profiles, video_prompt as _vp
    narrative = str(spec.get("protected_narrative") or spec.get("narrative") or "").strip()
    if not narrative:
        return {}
    actors = [str(a).strip() for a in (spec.get("actors") or []) if str(a).strip()]
    appearance = str(spec.get("appearance") or "").strip()
    locale = str(spec.get("locale") or "").strip()
    # 台词规则按视频模式分支（用户定稿 2026-08-28）：
    # climax 定格窗口无对白；firstlast 从头到尾覆盖剧情、含全部对白并标时点。
    if video_mode == "firstlast":
        lines_rule = (
            "   - lines：列出本段正文从头到尾所有角色亲口说出的台词，逐字摘录"
            "（保留防拦截标记原样，去掉『他说』等引述词）；旁白转述、心理描写一律不列；"
            "本段没有对白就留空数组。\n"
            "   - 每句台词必须带 at_s：按剧情位置推算这句台词在画面里说出的时刻（秒，数字，"
            "从本段开头起算、不超过本段时长）——台词发生在剧情哪个事件附近，就标到那个事件的"
            "时间点，让视频模型知道『什么时候说』；推算不了才允许省略 at_s，禁止全部标 0。\n"
        )
    else:
        lines_rule = (
            "   - lines：一律留空数组。高潮定格时刻角色的对白通常已经说完，本段动作窗口"
            "（0–15 秒）内没有对白——不得把剧情任何台词（含当下/前文/回忆/旁白转述）搬进来；"
            "声音细节只写进 sfx。\n"
        )
    system = (
        "你是视频提示词原料提取器。读下面这段剧情高潮正文（可能含防拦截标记，请还原其原义），"
        "理解人物体态、动作与声音，输出 JSON：\n"
        "{\"action_sequence\":[{\"beat\":\"定格起点/延伸/收尾\",\"desc\":\"动作描述\"}],"
        "\"subject_scene\":\"简化外貌+场景英文视觉描述\","
        "\"audio_design\":{\"music\":\"一句话音乐情绪\",\"sfx\":[\"具体音效1\",\"音效2\"],"
        "\"lines\":[{\"speaker\":\"角色名\",\"text\":\"台词原文\",\"at_s\":5}],\"sync\":\"卡拍说明\"}}\n"
        "规则：\n"
        "1. action_sequence 是从高潮图定格动作到剧情完整动作的延伸流程，覆盖整段正文的动作变化，"
        "最多8步；desc[0] 必须对应当前高潮图的定格动作，desc[1..] 必须基于剧情描述的后续动作，"
        "剧情没写的动作不得补；正文有多个动作时至少2拍（仅当正文确为单一动作才允许单拍）；"
        "desc 用简洁英文视觉描述（写清谁、什么体态、做什么）。\n"
        "2. subject_scene 只描述在场角色的外貌与场景：把抽象评价与同义形容词堆砌简化为直白视觉词"
        "（如「丰腴肥熟+酥雌醇媚」→「hourglass figure, large breasts, wide hips, seductive eyes」），"
        "专有名词（地名/建筑/器物）必须展开成可还原的视觉描述，不得照抄原名。\n"
        "3. audio_design 只提取正文确有证据的声音，禁止臆造；且必须锁定在高潮片段当下：\n"
        + lines_rule
        + "   - sfx：把画面里每个可见事件映射成具体拟真音效，按出场顺序 3~8 条（如鼓掌→有节奏的"
        "手掌/肉体拍击声、流水→潺潺水声、金属镣铐→铁链哗啦声、喘息/呻吟/衣料摩擦都要落到具体声音），"
        "用简洁英文描述，不写『环境声』这类抽象词。\n"
        "   - music/sync：各一句话即可。\n"
        "4. 只输出 JSON，不要解释。"
    )
    # 生图链同款：把当前防拦截预设组装进 system（含「不得拒答」任务框定）。
    # 预设缺失/组装失败时保持原 system，不阻断。
    try:
        system = image_prompt_profiles.system_with_preset(
            system, spec,
            preset_dir=str(ctx.get("preset_dir") or ""),
            preset_name=str(ctx.get("preset_name") or ""),
            user_name=str(ctx.get("user_name") or ""),
            task_label="内部视频提示词任务",
        )
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 预设组装失败不阻断，但生成缺防拦截预设
        _LOG.warning("防拦截预设组装失败（保持原 system，本次生成缺该预设）: %s", exc)
    user_lines = [f"剧情高潮正文（可能含防拦截标记，按标记还原原义）：\n{narrative}"]
    if actors:
        user_lines.append(f"在场角色：{'、'.join(actors)}")
    if appearance:
        user_lines.append(f"外貌锚（需简化去堆砌）：\n{appearance}")
    if locale:
        user_lines.append(f"场景：{locale}")
    user = "\n\n".join(user_lines)
    try:
        raw = _llm.chat(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            system, user, temperature=0.3, **_proxy_kw(ctx),
        )
        plan = _vp.parse_video_plan(raw)
        if plan:
            return plan
        # 整体无效（拒答/无 JSON）：拒答时带原因重试一次，仍无效则回退纯函数兜底。
        if not prompt_clean.REFUSAL_RE.search(
            prompt_clean.restore_jailbreak(raw or ""),
        ):
            return {}
        retry = _llm.chat(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            system, user + "\n\n上次回复被拒答：请只输出协议要求的 JSON，不要拒答或解释。",
            temperature=0.3, **_proxy_kw(ctx),
        )
        return _vp.parse_video_plan(retry)
    except Exception:  # noqa: BLE001
        return {}


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
    submit = vr.get("submit") if isinstance(vr.get("submit"), dict) else {}
    return {
        "mode": vr.get("mode") or "climax",
        "model": str(submit.get("model") or ""),
        "size": str(submit.get("size") or ""),
        "endpoint": str(submit.get("endpoint") or ""),
        "images": list(submit.get("images") or []),
        "reference_binding": vr.get("reference_binding") or {},
        "warnings": list(vr.get("warnings") or []),
    }


def _agency_prelude(ctx: dict, text: str):
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


def _next_story_turn(ctx: dict) -> int:
    """下一剧情回合号（M1 2026-09-06）：优先前端持久化回合号——assistant 楼层绑定
    turnNo，删除/重新生成后不倒退（封口级联靠它对齐）；旧前端/内部调用未提供时
    才退回快照派生（数带正文 assistant 消息 + 1）。"""
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


def _agency_propose(ctx: dict, deps, affinity, wb: str, text: str = "") -> tuple[str, bool]:
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
            if verdict.roll > 0 and verdict.actor and verdict.goal
        ]
        return roleplay_agency.narrative_directive(verdicts), roleplay_agency.agency_lost(verdicts)
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "agent.error", agent="world", error=str(exc))
        return "", False


def _agency_core_context(ctx: dict, wb: str, text: str, *, max_chars: int = 8_000) -> str:
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


def _should_fill(ctx: dict, repo_id: str, turn: int) -> bool:
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


def _apply_table_ops(ctx: dict, repo_id: str, clean: str, ops: list,
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


def _status_snapshot_value(snapshot: str, label: str) -> str:
    """只在需要确定性恢复时读取状态栏单行；其余快照内容仍保持不透明。"""
    matches = re.findall(
        rf"(?m)^\s*\[{re.escape(label)}\]\s*(.*?)\s*$",
        snapshot or "",
    )
    return matches[-1].strip() if matches else ""


def _illustration_visual_query(ctx: dict, current_text: str) -> str:
    """为角色外貌匹配保留短上下文，并机械补回最新状态栏的在场角色。"""
    from app.services import roleplay_agency

    present = ""
    for item in reversed(ctx.get("history") or []):
        if item.get("role") != "assistant":
            continue
        snapshot = roleplay_agency.extract_status_snapshot(str(item.get("content") or ""))
        if snapshot:
            present = _status_snapshot_value(snapshot, "在场")
            break
    return "\n".join(filter(None, (
        agent_context.history_text(ctx)[-2000:].strip(),
        (current_text or "").strip(),
        present,
    )))


def _ordered_illustration_names(names: list[str], text: str) -> list[str]:
    """按本轮文本中的实际出现顺序返回精确角色名。"""
    matched = _mentioned_bound_names(names, text or "")
    return sorted(matched, key=lambda name: (text.find(name), names.index(name)))


def _resolve_illustration_request_actors(
    known: list[str], *, planned: list[str], user_text: str, narrative: str,
    present: str, encounter: list[str], priority_text: str = "",
    absent: set | None = None,
) -> list[str]:
    """外貌资料不能充当出场证据；只从本轮事实确定插画角色。

    priority_text（视觉高潮段原文）：无插画计划时，角色排序优先按高潮段内出现顺序，
    而非全文首现顺序——2026-09-01 用户实锤「LoRA 用错」：全文先提 A 后提 B，
    但画面主体是 B，single 模式会加载 A 的 LoRA。
    absent（表格在场状态=不在场 的角色）：点名提及不等于画面在场（2026-09-01
    用户实锤：2girls 实际只有 1 女）。显式计划 subjects / 登场角色除外。
    """
    planned = [str(name).strip() for name in planned if str(name).strip()]
    absent = absent or set()
    if not known:
        return list(dict.fromkeys(planned + encounter))
    valid_planned = [name for name in planned if name in known]
    encounter_group = [name for name in encounter if name in known]
    story_names = _ordered_illustration_names(known, narrative)
    if priority_text and not planned:
        priority_names = _ordered_illustration_names(known, priority_text)
        story_names = list(dict.fromkeys([*priority_names, *story_names]))
    planned_absent = set(valid_planned) & absent
    for group in (encounter_group, story_names, valid_planned):
        if not group:
            continue
        selected = list(dict.fromkeys(group))
        if group is not valid_planned and group is not encounter_group:
            selected = [name for name in selected if name not in absent]
            # 全部被表格判为不在场时，宁可退回无绑定角色也不虚构同框
            if not selected:
                return []
        elif group is valid_planned and planned_absent and len(valid_planned) == len(planned_absent):
            # 模型计划的主体全部被表格判为不在场：视为计划不可用，继续走叙事组
            continue
        return selected
    # 模型已经明确声明画面主体，但主体不是任何绑定角色（典型为“我/你”）时，
    # 不得再从用户输入或状态栏借一个仅被提及的角色来加载其 LoRA。
    if planned:
        return []
    for group in (
        _ordered_illustration_names(known, user_text),
        _ordered_illustration_names(known, present),
    ):
        if group:
            return list(dict.fromkeys(group))
    return []


def _protagonist_gender(ctx: dict, repo_id: str, protagonist: str) -> str:
    """主人公性别（零 LLM）：世界书条目正文优先，通用表「性别」列兜底。

    2026-09-20 用户定案：只服务主人公，且性别只作**画面事实**注入（不机械拼硬标签）。
    两道来源都取不到 ⇒ `""`（调用方不注入，宁缺勿猜）。
    """
    name = str(protagonist or "").strip()
    if not name:
        return ""
    from app.services import scene_illustration, worldbook_store
    base = str(ctx.get("output_dir") or "").strip()
    if base:
        try:
            gender = worldbook_store.repo_protagonist_gender(base, repo_id, name)
        except Exception:  # noqa: BLE001 — 世界书读失败不得打断出图链
            gender = ""
        if gender:
            return gender
    return scene_illustration.gender_from_tables(
        _repo_tables_for_facts(ctx, repo_id), name,
    )


def _subject_gender_map(
    ctx: dict, repo_id: str, names: list[str], *,
    protagonist: str, protagonist_gender: str, tables,
) -> dict[str, str]:
    """逐角色性别映射 `{角色名: "女"/"男"}`（零 LLM，第 46 刀）。

    ⚠ **用途仅限**「按画面主体的实际性别组成写人数词」（`1girl, 2boys` 这种）——
      **不做画面事实注入**（性别注入仍只对主人公，23 刀红线）。
    ⚠ 来源定序与主人公一致：世界书条目正文优先、通用表「性别」列兜底；
      解析不出的名**不进映射**（下游见缺 ⇒ 退回中性措辞，宁缺勿猜）。
    ⚠ 主人公复用已算好的 `protagonist_gender`（省一次世界书 I/O）。
    """
    from app.services import scene_illustration, worldbook_store
    base = str(ctx.get("output_dir") or "").strip()
    out: dict[str, str] = {}
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in out:
            continue
        if protagonist and name == protagonist and protagonist_gender:
            out[name] = protagonist_gender
            continue
        gender = ""
        if base:
            try:
                gender = worldbook_store.repo_character_gender(base, repo_id, name)
            except Exception:  # noqa: BLE001 — 世界书读失败不得打断出图链
                gender = ""
        if not gender:
            gender = scene_illustration.gender_from_tables(tables, name)
        if gender:
            out[name] = gender
    return out


def _filter_illustration_appearance(
    appearance: str, actors: list[str], known: list[str],
) -> str:
    """视觉资料只描述已选角色，禁止旧角色外貌进入最终 Profile。

    段落头用通用「名字[：:]」识别（不限 known 白名单），这样 known 之外的角色段
    （如世界书 NPC 未进入 illustration_actor_names）也能被正确识别并过滤，
    否则会把非选中角色段当成选中角色的续行保留（虞莹纱混入缺陷）。
    """
    source = (appearance or "").strip()
    selected = set(actors)
    if not source or not known:
        return source
    if not selected:
        return ""
    # 通用段落头：行首「任意名字 + 冒号」，不再限定 known 白名单。
    marker = re.compile(r"^\s*([^\s：:]+)\s*[：:]")
    if not any(marker.match(line) for line in source.splitlines()):
        return source
    kept: list[str] = []
    include = False
    for line in source.splitlines():
        match = marker.match(line)
        if match:
            include = match.group(1).strip() in selected
        if include:
            kept.append(line)
    return "\n".join(kept).strip()


def _resolve_prev_tail_desc(ctx: dict) -> str:
    """上楼层尾帧画面描述：历史里最后一条角色（assistant）回复正文 → 结尾画面。

    与生成时透传的 lastFrameDesc 同源同逻辑（同一提取函数 extract_story_frames），
    零新增持久化（P4 反查精神：历史正文即资产）。倒序找第一条非空 assistant 消息
    （对齐预设 lastCharMessage 的取法）；无历史 / 上一楼纯对白 → 空串
    （L0 输入缺失 → ambiguous，由前端坑C「有图前提」兜底）。
    """
    from app.services import story_frames
    for h in reversed(ctx.get("history") or []):
        if (h.get("role") or "") == "assistant" and (h.get("content") or "").strip():
            return story_frames.extract_story_frames(
                (h.get("content") or "").strip(),
            ).closing
    return ""


def _wb_split_media_blocks(ctx: dict, reply: str) -> tuple[str, str, object, dict, dict]:
    """阶段 C 前段：think 前缀拆出 + <transition>/<illustration>/<audio> 搭车块抽取。

    返回 `(think_head, clean, transition_decision, illustration_plan, audio_plan)`。
    逐字自 `_agency_writeback` 搬移（T1.1 函数级拆解），判定逻辑与文案一字未改。
    """
    from app.services import roleplay_agency
    from app.services import image_prompt_extract, transition_extract
    from app.services.regex_engine import Placement

    # 根因修复（2026-08-31 实锤 3389/11977：幻影协议标签跨界匹配吞正文）：think 是
    # 输出协议的第一块，块提取链只应作用于其后的正文。think 内复述协议清单产生的
    # 幻影开标签会让各提取器的懒匹配从 think 内一路吃到真块闭合，把 </think> 与
    # 全部正文整段当块剥掉。拆出 think 前缀原样保留（前端正则折叠为思考过程），
    # 提取链只看正文，发布时拼回前缀。
    think_head, body = roleplay_agency.split_think_prefix(reply)
    # 孤立 </think>（模型漏写 <think> 开标签，2026-09-12 实锤）会随正文发布，
    # 发布链先剥；成对块与未闭合开标签不受影响。规划注释情形由裸正文兜底路径
    # （_wrap_bare_body → repair_orphan_think_close）补包 think 处理。
    body = roleplay_agency.strip_orphan_think_close(body)
    # V1.5/W1：<transition> 剥离放最前（与 <illustration>/<audio> 同为生成时搭车块，
    # 先抽避免干扰插画解析；漏块/非法/只开不闭 → None，L0 永远兕底，不得抛错）
    clean, transition_decision = transition_extract.extract_transition(body)
    clean, illustration_plan = image_prompt_extract.extract_illustration_plan(
        clean,
        block_filter=lambda value: _apply_regex(
            ctx, value, Placement.AI_OUTPUT, is_prompt=False, depth=0,
        ),
    )
    # 音频对白配音：comfy_audio 时剥离 <audio> 块并解析台词 + 8 维情感向量。
    # 与 illustration 块正交（用户可只开配音不开图）；失败只走降级，不阻断正文。
    audio_plan: dict = {}
    if ctx.get("comfy_audio"):
        from app.services import audio_dialogue_extract
        clean, audio_plan = audio_dialogue_extract.extract_audio_dialogue(
            clean,
            block_filter=lambda value: _apply_regex(
                ctx, value, Placement.AI_OUTPUT, is_prompt=False, depth=0,
            ),
        )
    return think_head, clean, transition_decision, illustration_plan, audio_plan


def _wb_apply_state(ctx: dict, deps, clean: str, *, turn: int, repo_id: str,
                    card_name: str) -> tuple[str, list, float, float, dict, Any]:
    """阶段 C 中段：<status> 快照抽取 + <状态更新> 写回 + 快照兜底 + 旧表格块清洗。

    返回 `(clean, raw, before, after, snapshot, st)`。逐字自 `_agency_writeback` 搬移。
    """
    from app.services import character_state, roleplay_agency
    from app.services import table_update

    # 抽 <status> 快照（不剥，留正文供前端正则渲染）+ 剥 <状态更新> 小数值 JSON
    snapshot = roleplay_agency.extract_status_snapshot(clean)
    clean, raw = roleplay_agency.parse_state_block(clean)
    if not snapshot or not raw:
        # 2026-09-18 兜底状态抽取信号：主协议（<状态更新>/<status>）任一失守
        # （2026-09-17 实锤 deepseek-v4-flash 整轮双缺），交给本轮维护调用顺带
        # 补抽取并补写回，0 额外 LLM。元组 = (need_deltas, need_snapshot)。
        # 真值判断而非 .strip()：extract_status_snapshot 产出已 strip，等价且对测试假件宽容。
        ctx["_agency_state_fallback"] = (not raw, not snapshot)
    raw = [*raw, *(ctx.pop("_agency_goal_deltas", []) or [])]
    rejected_adds: list = []
    before, after = roleplay_agency.writeback(
        deps, repo_id=repo_id, card_name=card_name, raw_deltas=raw, turn=turn,
        snapshot=snapshot, rejected=rejected_adds)
    run_trace.emit(ctx, "state.writeback", raw_deltas=raw, snapshot=snapshot,
                   affinity_before=before, affinity_after=after,
                   rejected_adds=rejected_adds)
    st = character_state.load_state(deps.state_base, repo_id, card_name)
    if not snapshot:
        previous_snapshot = getattr(getattr(st, "快照", None), "text", "")
        clean = roleplay_agency.ensure_status_snapshot(clean, previous_snapshot)
    # 展示对账（2026-09-22 用户实锤）：正文 <status> 的数字此前只对账「落盘快照」与
    # 「重注入快照」，用户屏幕上看到的 [好感] 行仍是模型自述 ⇒ 与引擎账漂移（实测同一角色
    # 正文 16→13→10 乱跳，引擎恒 10→13）。此处用同一函数对账**正文**——本节点末尾发
    # replace 全文替换气泡，故显示即修。st 已在上面从磁盘重载（即已对账的真值）。
    clean = roleplay_agency.reconcile_reply_status(clean, st)
    # 旧版/异常模型仍可能输出表格块：这里只清洗，实际更新统一交给独立维护调用。
    had_legacy_table_block = table_update.has_table_block(clean)
    clean, _legacy_ops = table_update.parse_table_block(clean)
    if had_legacy_table_block:
        run_trace.emit(ctx, "table.writeback", status="legacy_ignored")
    return clean, raw, before, after, snapshot, st


def _wb_ratchet_sources(
    ctx: dict, repo_id: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """术语棘轮（CODE_STYLE_TERM_RATCHET）的输入：注入层 + 最近数轮正文 + 豁免人名 + 设定存量。

    2026-09-23：真机「经脉」在注入块里 33 次、跨 31 个不同句子，而正文里"看经脉"0 命中
    ⇒ 该码要能看见**持久层**，否则永远量不到。此处注入层＝**本轮维护后**的读侧数据表
    （＝下一轮会注入的那一份）+ 世界书召回/动态段；正文层＝最近 5 轮 assistant 全文。
    设定存量（第 4 项）见 ``_wb_setting_sources``。仅只读；失败退回空（不产生诊断）。
    """
    injected: list[str] = []
    names: list[str] = []
    dyn = str(ctx.get("_worldbook_dynamic_text") or "")
    recall = str(ctx.get("_worldbook_recall_text") or "")
    if dyn:
        injected.append(dyn)
    if recall:
        injected.append(recall)
    try:
        from app.services import table_store, table_update

        tables = table_store.load(ctx.get("output_dir") or "", repo_id)
        read_tables = table_store.tables_for_read(tables)
        block = table_update.table_context(read_tables)
        if block:
            injected.append(block)
        for tb in read_tables:
            for row in (tb.get("rows") or []):
                first = str(row[0]).strip() if row else ""
                if 1 < len(first) <= 6:
                    names.append(first)
    except Exception:  # noqa: BLE001 棘轮输入缺失不影响 CI 其余项
        pass
    for name in ctx.get("_worldbook_character_names") or ():
        names.append(str(name).strip())
    bodies = [
        (m.get("content") or "").strip()
        for m in reversed(_history_messages(ctx))
        if m.get("role") == "assistant"
    ][:5]
    bodies = [b for b in bodies if b]
    return (injected, bodies, [n for n in dict.fromkeys(names) if n][:40],
            _wb_setting_sources(ctx, repo_id))


def _wb_setting_sources(ctx: dict, repo_id: str) -> list[str]:
    """设定存量来源＝世界书各条目的**静态底座**（`DYNAMIC_MARKER` 之前部分）。

    用途：用户 2026-09-23 口径——「天狐宫多次出现能理解（主角大半后宫在此、后期势力归男主），
    但经脉，你看设定条目都不怎么提及，就前期提了一嘴，结果一个个女主角找的借口都是经脉」。
    ⇒ 判据＝「**叙事用量 vs 设定存量**」。此处给出存量：含设定条目 + 角色卡（不含动态区，
    动态区是叙事、已算进注入层）。失败退回空 ⇒ 报告退回按频次排序，与改造前一致。
    """
    try:
        from app.services import worldbook, worldbook_store

        base = ctx.get("output_dir") or ""
        book = worldbook_store.read_repo_snapshot(base, repo_id)
        if not book:
            return []
        out: list[str] = []
        for entry in book.get("entries") or []:
            content = str(entry.get("content") or "")
            if worldbook.DYNAMIC_MARKER in content:
                content = content.split(worldbook.DYNAMIC_MARKER, 1)[0]
            if content.strip():
                out.append(content)
        return out
    except Exception:  # noqa: BLE001 棘轮输入缺失不影响 CI 其余项
        return []


def _wb_run_narrative_ci(ctx: dict, clean: str, *, turn: int, raw: list,
                         repo_id: str) -> None:
    """连续性 CI 评估 + 落盘（永不阻断/改写正文）。逐字自 `_agency_writeback` 搬移。"""
    try:
        from app.services import narrative_ci

        injected, bodies, exclude, setting = _wb_ratchet_sources(ctx, repo_id)
        diagnostics = narrative_ci.evaluate(
            clean, turn=turn, facts=ctx.get("_continuity_facts") or (), raw_deltas=raw,
            beliefs=ctx.get("_continuity_beliefs") or (),
            world_rules=ctx.get("_world_rules") or (),
            recent_openings=[
                (m.get("content") or "").strip()[:15]
                for m in reversed(_history_messages(ctx))
                if m.get("role") == "assistant"
            ][:3],
            style_config=ctx.get("_style_config"),
            style_injected=injected,
            style_bodies=bodies,
            style_exclude=exclude,
            style_setting=setting,
        )
        saved = narrative_ci.save(
            ctx.get("output_dir") or "", repo_id, diagnostics,
        )
        run_trace.emit(
            ctx, "narrative.ci", status="evaluated", count=len(diagnostics), saved=saved,
            codes=[item.get("code", "") for item in diagnostics],
        )
    except Exception as exc:  # noqa: BLE001 CI 永不阻断或改写正文
        run_trace.emit(ctx, "narrative.ci", status="unavailable", error=str(exc))


def _wb_at_climax(*, illustration_plan, visible_story, lost, scene,
                  local_scene_fallback, missing_plan_fallback,
                  first_story_reply, character_encounter) -> bool:
    """是否进入插画高潮（纯判定，无副作用）。

    逐字自 `_agency_writeback` 内联判定搬移；等价性由
    `tests/test_agency_writeback_seams.py::test_at_climax等价矩阵` 在输入网格上钉住。
    """
    return bool(illustration_plan) or (
        bool(visible_story) and (
            lost or scene in ("nsfw", "climax")
            or local_scene_fallback or missing_plan_fallback
            or first_story_reply or character_encounter
        )
    )


def _wb_scene_signals(ctx: dict, deps, st, clean: str, snapshot, scene: str,
                      user_text: str, plan, lost: bool) -> dict:
    """阶段 D 前置：可见正文 + 在场/衣着/所在 + 高潮触发信号（纯读取，无副作用）。

    返回 dict，**键名即原内联局部名**（调用方按原样解包 ⇒ 下游正文零改动）。
    逐字自 `_agency_writeback` 搬移。
    """
    from app.services import image_prompt_extract, roleplay_agency, scene_classify
    from app.services import scene_illustration

    # 阶段 D：插画（renderer=None 时 maybe_illustrate 直接返回 None）；用去块正文当段落
    wardrobe = roleplay_agency._narr(st, "衣着")
    locale = roleplay_agency._narr(st, "所在")
    snapshot_text = snapshot or str(
        getattr(getattr(st, "快照", None), "text", "") or "",
    )
    # `<status>` 是显示快照，正常不解析；但它经常是唯一的在场人物真源。
    # LoRA 选择只读取其中精确的 `[在场]` 单行，避免要求模型额外写一份叙事 delta。
    present = (
        _status_snapshot_value(snapshot_text, "在场")
        or roleplay_agency._narr(st, "在场")
    )
    locale = _status_snapshot_value(snapshot_text, "所在") or locale
    # 2026-09-20 用户口径：状态表/快照都取不到「所在 / 衣着」时，回落**数据表**里的旧值
    # （全局数据表·地点 / 主角信息表·穿着打扮）。表格本质是**上一轮**参数 ⇒ 只补空缺：
    # 本轮正文或状态表已经给出的一律优先，绝不覆盖（衣服战损、被撕破这类动态以剧情为准）。
    if not (locale or "").strip() or not (wardrobe or "").strip():
        from app.services import table_store
        table_locale, table_wardrobe = table_store.illustration_defaults(
            str(ctx.get("output_dir") or ""),
            str(ctx.get("repo_id") or ctx.get("thread_id") or ""),
        )
        locale = (locale or "").strip() or table_locale
        wardrobe = (wardrobe or "").strip() or table_wardrobe
    # 插画提示词直接由正文 + 已有视觉锚组装，不再额外调用一次聊天模型。
    visible_story = image_prompt_extract.visible_narrative_text(clean)
    local_scene = scene_classify.infer_scene(
        "\n".join((user_text, visible_story)),
    )
    local_scene_fallback = (
        not plan and local_scene in ("nsfw", "climax")
    )
    # ComfyUI 自动插画开启后，主模型即使没带内联计划，也不能让整条请求静默消失。
    # 普通 dialogue/action 直接复用本轮正文最强视觉段落；完整 Profile 由独立 LLM 链
    # （_profile_llm_fallback）或本地事实编译提供，前端无需再补调文本模型。
    missing_plan_fallback = bool(
        ctx.get("comfy_illustrate") and not plan and visible_story
    )
    (encounter_anchor, encounter_narrative, encounter_actors,
     encounter_facts) = scene_illustration.encounter_illustration_context(clean)
    character_encounter = bool(
        not plan and encounter_anchor
        and (deps.renderer is not None or ctx.get("comfy_illustrate"))
    )
    first_story_reply = not any(
        item.get("role") == "user" and str(item.get("content") or "").strip()
        for item in (ctx.get("history") or [])
    )
    at_climax = _wb_at_climax(
        illustration_plan=plan, visible_story=visible_story, lost=lost, scene=scene,
        local_scene_fallback=local_scene_fallback,
        missing_plan_fallback=missing_plan_fallback,
        first_story_reply=first_story_reply,
        character_encounter=character_encounter,
    )
    image_rating = (
        "nsfw" if scene in ("nsfw", "climax")
        or local_scene in ("nsfw", "climax") else "sfw"
    )
    return {
        "wardrobe": wardrobe, "locale": locale, "snapshot_text": snapshot_text,
        "present": present, "visible_story": visible_story, "local_scene": local_scene,
        "local_scene_fallback": local_scene_fallback,
        "missing_plan_fallback": missing_plan_fallback,
        "encounter_anchor": encounter_anchor, "encounter_narrative": encounter_narrative,
        "encounter_actors": encounter_actors, "encounter_facts": encounter_facts,
        "character_encounter": character_encounter,
        "first_story_reply": first_story_reply, "at_climax": at_climax,
        "image_rating": image_rating,
    }


def _wb_build_audio_request(ctx: dict, audio_plan: dict, visible_story: str) -> dict:
    """comfy_audio 时组装对白配音请求（含机械降级兜底）；关时返回空 dict。

    逐字自 `_agency_writeback` 搬移（含两条 run_trace.emit）。
    """
    audio_req: dict = {}
    if ctx.get("comfy_audio"):
        from app.services import audio_dialogue_extract
        audio_lines = audio_plan.get("lines") or audio_dialogue_extract.build_fallback_dialogue(
            visible_story, [str(n).strip() for n in (ctx.get("card_names") or []) if str(n).strip()],
        )
        if audio_lines:
            audio_req = {"lines": audio_lines}
            run_trace.emit(
                ctx, "audio.request", status="emitted", line_count=len(audio_lines),
                speakers=[ln.get("speaker", "") for ln in audio_lines],
                text_chars=[len(ln.get("text", "")) for ln in audio_lines],
            )
        else:
            run_trace.emit(ctx, "audio.request", status="skipped", reason="no_dialogue")
    return audio_req


def _wb_initial_illustration_prompt(ctx: dict, deps, plan, sig: dict, *,
                                    user_text: str) -> tuple[str, int, list]:
    """初选出图提示词/动作/角色（comfy 与非 comfy 两条路径共用的第一轮选择）。

    返回 `(prompt_override, motion, actors)`。逐字自 `_agency_writeback` 搬移。
    """
    from app.services import image_prompt_extract
    from app.services.regex_engine import Placement

    visible_story = sig["visible_story"]
    prompt_override, motion, actors = "", 0, []
    if plan:
        prompt_override = _apply_regex(
            ctx, plan["prompt"], Placement.IMAGE_PROMPT, is_prompt=True).strip()
        motion = plan["motion"]
        actors = plan["actors"]
    elif ctx.get("comfy_illustrate") and sig["local_scene_fallback"]:
        prompt_override = image_prompt_extract.build_fallback_content_tags(
            "\n".join((user_text, visible_story)),
        )
        motion = image_prompt_extract.infer_motion(visible_story)
        actors = []
    elif (deps.renderer is not None or ctx.get("comfy_illustrate")) and sig["at_climax"]:
        prompt_override, motion, actors = _build_image_prompt(
            ctx,
            paragraph=(sig["encounter_narrative"] if sig["character_encounter"]
                       else visible_story),
            appearance=_illustration_appearance(ctx),
            wardrobe=sig["wardrobe"], locale=sig["locale"])
        if sig["character_encounter"]:
            actors = sig["encounter_actors"]
    return prompt_override, motion, actors


def _wb_illustration_reason(*, illustrate_req, plan_retargeted, illustration_plan,
                            character_encounter, local_scene_fallback,
                            first_story_reply, missing_plan_fallback,
                            prompt_source, at_climax) -> str:
    """`illustration.request` 事件的 reason（纯判定，判定次序即优先级）。

    逐字自 `_agency_writeback` 内联嵌套条件搬移；等价性由
    `tests/test_agency_writeback_seams.py::test_illustration_reason等价矩阵` 钉住。
    """
    return ("main_plan_retargeted" if plan_retargeted and illustrate_req else
            "main_plan" if illustration_plan and illustrate_req else
            "character_encounter" if character_encounter and illustrate_req else
            "local_scene_fallback" if local_scene_fallback and illustrate_req else
            "first_story_reply" if first_story_reply and illustrate_req else
            "missing_plan_fallback" if missing_plan_fallback and illustrate_req else
            prompt_source if illustrate_req else
            "scene_not_triggered" if not at_climax else "empty_prompt")


def _wb_comfy_actor_pool(ctx: dict, repo_id: str, card_name: str, plan, actors: list,
                         sig: dict, *, user_text: str) -> dict:
    """comfy 路径①：候选角色全集 → 首轮 request_actors + 外貌过滤。

    返回 dict（**键为原内联局部名**）。逐字自 `_agency_writeback` 搬移。
    """
    from app.services import scene_illustration

    visible_story = sig["visible_story"]
    present = sig["present"]
    character_encounter = sig["character_encounter"]
    # 提取模型失败/拒答/返回坏 JSON 时仍要发请求：用既有纯逻辑组装器把正文、
    # 外观和动态状态拼成降级提示词。旧代码只在 prompt_override 非空时发事件，
    # 与上方“失败回退中文裸拼接”的设计相反，会让整条 ComfyUI 链静默消失。
    # actors 只表示画面真实在场角色；配置全集仅用于正文精确补漏，不能整体塞入，
    # 否则前端会给未出场角色加载 LoRA。
    known = [
        str(name).strip() for name in (ctx.get("illustration_actor_names") or [])
        if str(name).strip()
    ]
    actor_values = list(actors)
    if ctx.get("appearance_source") == "worldbook" and card_name:
        # 世界书作品的 card_name 是作品/父仓库名，不是角色名。旧前端曾把它
        # 混进候选全集，导致本地降级把作品名精确命中并回退风格 LoRA。
        known = [name for name in known if name != card_name]
        actor_values = [name for name in actor_values if name != card_name]
    scene_actor_text = sig["encounter_narrative"] if character_encounter else visible_story
    scene_text = "\n".join(filter(None, (scene_actor_text, user_text, present)))
    climax_paragraph = (
        "" if plan else scene_illustration.fallback_illustration_anchor(scene_actor_text)
    )
    absent = _repo_table_absent_actors(ctx, repo_id, known)
    request_actors = _resolve_illustration_request_actors(
        known,
        planned=actor_values if plan else [],
        user_text=user_text,
        narrative=scene_actor_text,
        present=present,
        encounter=sig["encounter_actors"] if character_encounter else [],
        priority_text=climax_paragraph,
        absent=absent,
    )
    if not request_actors:
        request_actors = list(dict.fromkeys(
            [name for name in actor_values
             if (not known or name in known) and name not in absent] + (
                [card_name]
                if card_name and ctx.get("appearance_source") != "worldbook" else []
            ),
        ))
    request_appearance = _filter_illustration_appearance(
        _illustration_appearance(ctx), request_actors, known,
    )
    return {
        "known": known, "actor_values": actor_values,
        "scene_actor_text": scene_actor_text, "scene_text": scene_text,
        "climax_paragraph": climax_paragraph, "absent": absent,
        "request_actors": request_actors, "request_appearance": request_appearance,
    }


# 判据档单次调用的墙钟上限（项目红线：一切外部调用必须带超时，禁止无超时挂起）。
_ANCHOR_INTENT_TIMEOUT_SECONDS = 60.0


def _frame_subject_names(raw) -> list[str]:
    """归一化「本帧主体名集合」（判据档 `subjects` / 词表档在场角色全集）。

    非序列或全空一律回空表 ⇒ 取材层视为「无信号」＝旧行为，这是 A′ 的零回归前提。
    """
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(name).strip() for name in raw if str(name).strip()]


def _resolve_anchor_intent_list(ctx: dict, text: str, raw: str) -> list[dict]:
    """判据原始输出 ⇒ **去重后的锚点列表**，再按面板数量取前 N（第 57 刀）。

    `count` 取 `ctx["illustration_batch_count"]`（面板「批量生图数量」）：
      · 0/缺省 ⇒ 1（单张：与 `resolve_anchor_intent` 同口径，取分最高、同分取正文更早）；
      · ≥ 锚点数 ⇒ 全部；
      · 否则 ⇒ 按判据 `score` 降序取前 N（结果仍保持正文原序）。
    `list[0]` 恒等于 `resolve_anchor_intent` 单条路径的锚点（两条链**同源**，不会各说各话）。
    任何解析失败 ⇒ `[]`（调用方回落已有单条路径/词表档）。
    """
    from app.services import scene_illustration

    items = scene_illustration.dedupe_anchor_intents(
        scene_illustration.parse_anchor_intents(raw), text,
    )
    # 第 59 刀（用户定案 2026-09-25）：判据**显式打 0 分**＝「这个时刻不值得画」
    # ⇒ 无论批量上限多大都不为它建槽。⚠ 过滤落在本层（多批选择）而非
    # `select_anchor_intents` 本体——旧单锚点形态被 parse 层有意包装成 score=0
    # （零回归锁），本体过滤会误杀单条路径（真机回归 11 连红实锤）。
    items = [item for item in items if int(item.get("score") or 0) > 0]
    if not items:
        return []
    return scene_illustration.select_anchor_intents(items, _anchor_batch_count(ctx))


def _anchor_batch_count(ctx: dict) -> int:
    """面板「批量生图数量」：0/缺省/非法 ⇒ 1（单张，与改造前逐字一致）。"""
    try:
        value = int(ctx.get("illustration_batch_count") or 0)
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


def _anchor_intent_viewpoint_hint(ctx: dict) -> str:
    """第 57 刀 ④：把「画面人称」告知判据（**只加在调用侧，不动用户判据文本**）。

    用户口径（原话，第二次重申）：「第三人称是**内容产出再找视角展示完整内容**，而第一人称
    则是**先内容再核对主人公主视角内容进行筛分**。」
    ⇒ 第一人称下，**选点阶段**就该避开「只有退到远处／绕到对方背后才画得出来」的时刻；
    否则下游再筛也救不回。真机实锤（turn `9bb03d04`）：判据选了事后的**非接触帧**，
    取材窗口只给得出**整夜性场景** ⇒ 编译出「肉棒压在她臀上」这类**只能从外部看到**的画面
    ⇒ 第一人称成图**必然**呈现第三人称观感。

    ⚠ 刻意**不动 `DEFAULT_CLIMAX_ANCHOR_RUBRIC`**：该文件头明令「**不许往判据里加约束/加门**」
      （v7/v9/v10/v11 四次实测净亏）；本提示是**调用侧上下文**、不是判据正文
      ⇒ 用户手写的那份 rubric **逐字不变**、也**不需要 rubric A/B**。
    未手选人称（`""`＝自动）⇒ 返回空串 ⇒ user 消息**逐字节不变**（零回归锁）。
    """
    if str(ctx.get("illustration_viewpoint") or "").strip() != "first_person":
        return ""
    return (
        "画面人称：第一人称（镜头＝叙事者的眼睛、视野有限；叙事者本人不占画面，"
        "他的身体只在正文写到时以局部入画）。\n"
        "⇒ 选点时**优先**挑「就站在叙事者眼睛位置、一眼就能看到」的时刻；"
        "**避开**那些必须退远、绕到对方背后、或要同时看全两人全身才画得出来的时刻"
        "（那种帧在**选点阶段**就注定画不出来）。"
    )


def _illustration_anchor_intent(ctx: dict, clean: str, sig: dict) -> tuple[str, dict]:
    """判据档：用「高潮点定位判据」（用户态配置，属主 illustration_prompt_store）判定锚点。

    只在**同轮无插画计划**（缺块路径，实测占多数回合）时参与。任一步不成立即返回
    `("", {})`，调用方顺序走现有中文词表（**零改动**）：
    判据为空 / 正文为空 / 无模型配置 / 调用异常 / 输出解析不出 anchor / anchor 不逐字落文。

    **本层常开**（2026-09-18 用户定案去掉启用开关）：它只是「词表档之前的一次更聪明的尝试」，
    失败有上界——最坏情况就是退回词表档，所以不需要一个「用不用它」的开关。

    观测四态（`illustration.anchor_intent`）：`skipped`（前置不足：判据空 / 正文空 / 无模型配置）、
    `error`（调用失败，带 error_type/message/耗时）、`invalid`（解析或 evidence 校验不过）、
    `ok`（带 anchor/composition/subjects/reason/耗时）。

    返回 `(anchor, detail)`：`detail` 供**取材层**判「锚点句是否自带画面」（A′ 用
    `subjects`），与 trace 字段同源；anchor 为空时 detail 恒为 `{}`。四态 trace 字段不动。
    """
    from app.services import illustration_prompt_store, scene_illustration

    rubric = illustration_prompt_store.effective_climax_anchor_rubric()
    if not rubric:
        # store 侧保证非空（缺文件 / 坏 JSON / 留空都回缺省 v8）；这里是**防御性闸**：
        # 一旦 store 被替换或回归成可能返回空串，也必须「拿不到判据就不调模型、直接回词表」。
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="skipped", reason="rubric_empty",
        )
        return "", {}
    text = (clean or "").strip()
    if not text:
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="skipped", reason="narrative_empty",
        )
        return "", {}
    if not (ctx.get("chat_base") and ctx.get("chat_key") and ctx.get("chat_model")):
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="skipped",
            reason="model_config_missing",
        )
        return "", {}
    actors = [str(name).strip() for name in (sig.get("present") or []) if str(name).strip()]
    user = "\n\n".join(part for part in (
        # 第 57 刀 ④：画面人称（只 disclose，不约束判据正文）。未手选 ⇒ 空串 ⇒ 逐字不变。
        _anchor_intent_viewpoint_hint(ctx),
        f"在场角色：{'、'.join(actors)}" if actors else "",
        f"剧情正文：\n{text}",
    ) if part)
    started = time.monotonic()
    try:
        raw = _llm.chat(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            scene_illustration.anchor_intent_system(rubric), user,
            temperature=0.2, timeout=_ANCHOR_INTENT_TIMEOUT_SECONDS, **_proxy_kw(ctx),
        )
    except Exception as exc:  # noqa: BLE001 判据是可选增强：任何失败都必须静默降级到词表
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="error",
            error_type=type(exc).__name__, error_message=str(exc)[:500],
            elapsed_seconds=round(time.monotonic() - started, 1),
        )
        return "", {}
    anchor, detail = scene_illustration.resolve_anchor_intent(text, raw or "")
    # 第 57 刀：同步落一份**去重 + 取前 N** 的多锚点列表进 ctx（多批生图的地基）。
    # ⚠ 只在能解析出锚点时写；单张路径的返回值 `(anchor, detail)` **逐字不变**
    #   （`resolve_anchor_intent` 仍是「分最高那条」的老语义 —— 零回归锁）。
    if anchor:
        ctx["illustration_anchor_intents"] = _resolve_anchor_intent_list(
            ctx, text, raw or "",
        )
    elapsed = round(time.monotonic() - started, 1)
    if not anchor:
        run_trace.emit(
            ctx, "illustration.anchor_intent", status="invalid",
            raw_chars=len(raw or ""), parsed=bool(detail),
            anchor_candidate=str(detail.get("anchor") or "")[:120],
            elapsed_seconds=elapsed,
        )
        return "", {}
    run_trace.emit(
        ctx, "illustration.anchor_intent", status="ok",
        anchor=anchor[:120], composition=detail.get("composition") or "",
        subjects=detail.get("subjects") or [], reason=detail.get("reason") or "",
        elapsed_seconds=elapsed,
        # 第 57 刀 · 观测补口（2026-09-24 用户问「选的是不是高分」）：把判据的**完整榜单**
        # （含 score）记下来 —— 此前只记「选中那条」，打分发生了却丢掉。
        # ⚠ 仅进诊断 trace，不进提示词（score 不外泄到生图链路）。
        anchors=(ctx.get("illustration_anchor_intents") or [])[:8],
    )
    return anchor, detail


def _wb_comfy_scene_target(ctx: dict, repo_id: str, plan, clean: str, sig: dict,
                           pool: dict, *, prompt_override: str, motion: int,
                           user_text: str, anchor_override: str = "",
                           subjects_override: list | None = None) -> dict:
    """comfy 路径②：提示词落定 + 锚点定位 + 高潮重定向 + 终选角色与外貌。

    返回 dict（键为原内联局部名；`motion` 可能被重定向改写）。逐字搬移。

    第 57 刀 · 多批派发（2026-09-24）：`anchor_override` 非空时＝**指定锚点**（多批的第 2..N 张），
    此时**不再调用判据 LLM**（锚点列表已由本轮主路径的判据调用落进
    `ctx["illustration_anchor_intents"]`）——否则 N 张图会多花 N-1 次判据调用。
    默认空串 ⇒ 走**原有**判据档 / 词表档 / 计划档，逐字节不变（零回归锁）。
    """
    from app.services import image_prompt_extract, scene_illustration

    visible_story = sig["visible_story"]
    present = sig["present"]
    character_encounter = sig["character_encounter"]
    encounter_narrative = sig["encounter_narrative"]
    encounter_actors = sig["encounter_actors"]
    known = pool["known"]
    actor_values = pool["actor_values"]
    absent = pool["absent"]
    climax_paragraph = pool["climax_paragraph"]
    request_actors = pool["request_actors"]
    request_appearance = pool["request_appearance"]
    # 第 51 刀（2026-09-22 用户定案「给 FP2 建真源」）：叙事者姓名三段读取
    # （user_name → 主角信息表姓名列 → 世界书主角条目），修复 **D4/D5**（生产里
    # `user_name` 常空 ⇒ 第 42 刀数据层保护集体空转）。算一次，下游 filter_anchor_subjects
    # 与 6 处消费点共用同一 `scene_spec["narrator"]`。
    _base = str(ctx.get("output_dir") or "").strip()
    protagonist_name, protagonist_source = "", ""
    try:
        from app.services import worldbook_store as _wbs
        protagonist_name, protagonist_source = _wbs.repo_protagonist_name(
            _base, repo_id, str(ctx.get("user_name") or ""),
        )
    except Exception:  # noqa: BLE001 — 姓名解析失败不得打断出图链
        protagonist_name, protagonist_source = "", ""

    request_prompt = prompt_override.strip()
    prompt_source = "extracted"
    if sig["at_climax"] and not request_prompt:
        request_prompt = scene_illustration.build_scene_request(
            paragraph=encounter_narrative if character_encounter else visible_story,
            appearance=request_appearance,
            wardrobe=sig["wardrobe"],
            locale=sig["locale"],
            actors=request_actors,
        ).prompt
        prompt_source = "fallback"
    fallback_anchor = ""
    # A′（2026-09-18 拍板）：本帧主体名集合，随锚点档一起决定取材是否扩窗。
    # 只有「缺块 + 判据/词表」这条支路会填；有计划那条支路保持旧行为（§7.3 第 4 条）。
    anchor_subjects: list[str] = []
    # 优化项③（2026-09-19）：**判据档**判出的本帧主体名集合 ＝「这帧画面里有几个人」的
    # 唯一正向证据。`anchor_subjects` 会混入词表档的在场全集（口径太宽，只配用来取材扩窗），
    # 故另起一份只装判据结论的副本，随 scene_spec 下传，供多人契约按**画面主体数**判
    # （用户口径：不按角色表人数、也不按在场人数）。
    frame_subjects: list[str] = []
    # 第 50 刀（2026-09-22 用户口径「主人公**不是被剔除，而是等效镜头**」）：判据**未经收敛**的
    # 原始主体名。锚点句点名收敛**只看字面名字**，而锚点句常用**代词**指代真正的主体（"她…"）、
    # 用**名字**指代镜头（被看向/被亲吻的主人公）⇒ 收敛会把「镜头」误当成本帧唯一主体。
    # 真机轮 C（thread `09f79e6f`，12:28）：锚点句「**她**掖完被子，把视线从墙上收回来，
    # 落到**凌渊**脸上。」⇒ 收敛成 `['凌渊']`（凌渊＝镜头、根本不在场）⇒ 画面被读成
    # 「画主人公一个人」。⇒ 当收敛结果与本帧在场角色**无交集**时，必须能回退到这一份。
    judge_subjects: list[str] = []
    # 缺块路径的锚点必须与「同轮有计划」那条支路一样过**既有仲裁机**
    # （`resolve_illustration_anchor`：词表分 ≥ 模型分+3 就推翻）。设计稿 §5.2 早就记了
    # 「§2.2 的推翻逻辑需一并重审」，但实现里仲裁只在 `if plan:` 时跑 ⇒ 同一三档降级链
    # 有计划那条支路有纠偏网、缺块这条没有。2026-09-18 真机复跑实锤（deepseek-v4-flash-0731，
    # 4 run × 3 采样）该缺口导致判据档在 L1/L3 反而劣于词表档（55.3 vs 35、19.0 vs 12）；
    # 接上仲裁后 L1 55.3→35.0、L3 19.0→12.0，且 L2 的完美选点零误伤（推翻 0/12）。
    fallback_anchor_needs_arbitration = False
    if anchor_override and not character_encounter and not plan:
        # 第 57 刀 · 多批派发（2026-09-24）：锚点由调用方指定 —— 判据已在本轮主路径跑过一次，
        # **不再重复调用判据 LLM**（否则 N 张图会多花 N-1 次判据调用）。
        # 主体名沿用该锚点自己的 `subjects`，走与判据档**同一条**过滤链（口径不分叉）。
        # ⚠ 仲裁策略见分支末尾的 B′ 说明（定位成功即不过仲裁）。
        fallback_anchor = anchor_override
        judge_subjects = _frame_subject_names(subjects_override)
        _vp_anchor = scene_illustration.frame_viewpoint_detail(
            fallback_anchor, known=known,
            override=str(ctx.get("illustration_viewpoint") or ""),
        )
        anchor_subjects = scene_illustration.filter_anchor_subjects(
            judge_subjects, fallback_anchor, known,
            narrator=protagonist_name, viewpoint=_vp_anchor["viewpoint"],
        )
        frame_subjects = list(anchor_subjects)
        # 第 57 刀 B′（2026-09-24 用户拍板）：`anchor_override` 是**有意指定**的画面 ——
        # 与 `character_encounter`（「首帧/初遇锚点刻意不过仲裁」）同一性质。
        # 真机实锤（2 张实测，`illustration.slot` 两条）：仲裁 `resolve_illustration_anchor`
        # 是**确定性**的，把判据锚点「手就那么悬…」与 extras 的锚点**都收敛到同一个**
        # 词表最优段「那两团肉…」⇒ 两槽 anchor 逐字相同、offset 同为 1133 ⇒ 两张图叠放。
        # ⇒ 定位成功 ⇒ **不再仲裁**（extras 才能落在**自己**的高潮点）；
        #   定位失败 ⇒ 仍走仲裁→词表兜底（锚点不在正文里就无从取材）。
        # ⚠ 只动本分支：单张/主图路径零回归。
        fallback_anchor_needs_arbitration = not bool(
            scene_illustration.illustration_anchor_offset(clean, anchor_override))
    elif character_encounter:
        # 首帧/初遇锚点是**有意指定**的画面，刻意不过仲裁：仲裁机带「高潮纠偏」语义，
        # 套到首帧上会把画面从开篇拉到高潮段——这正是 `first_frame_anchor_offset`
        # 当初存在的原因。故接线只扩到「缺块 + 判据」这条支路。
        fallback_anchor = sig["encounter_anchor"]
    elif visible_story and (
        sig["local_scene_fallback"] or sig["missing_plan_fallback"]
        or sig["first_story_reply"]
    ) and not plan:
        # 判据档（常开，2026-09-18）：用户态判据 + 一次 LLM 判定；判据空 / 无模型配置 /
        # 调用失败 / 校验不过 一律返回空串，短路到现有中文词表 —— 词表档判定逻辑零改动。
        intent_anchor, intent_detail = _illustration_anchor_intent(ctx, clean, sig)
        if intent_anchor:
            fallback_anchor = intent_anchor
            # A′：判据档的本帧主体名集合取判据 `subjects`，再过人称口径机械后滤
            # （吸附全名 / 滤叙事者·主角 / 锚点句点名收敛——见 filter_anchor_subjects）。
            # 2026-09-20 收紧：把**主角本名**（用户人设名）交给后滤——第一人称视角下
            # 叙事者不占画面，不再被当成画面里的一个人而误触发多人契约。
            # 第 50 刀：**未经收敛**的判据主体名（＝判据的原始结论）。锚点句点名收敛
            # 只认字面名字（见 filter_anchor_subjects 第 ③ 步），会因「代词指主体、名字指
            # 镜头」而收敛错；故留一份未收敛的原件，供后文「收敛失败回退」。仅判据档会填。
            judge_subjects = _frame_subject_names(intent_detail.get("subjects"))
            # 第 51 刀（人称单一真源，修 D1）：`viewpoint` 由上游唯一接缝算好传入
            # （已含手选 override 优先），`narrator` 取三段读取结果；filter_anchor_subjects
            # 不再内部自判人称。
            _vp_anchor = scene_illustration.frame_viewpoint_detail(
                intent_anchor, known=known,
                override=str(ctx.get("illustration_viewpoint") or ""),
            )
            anchor_subjects = scene_illustration.filter_anchor_subjects(
                judge_subjects,
                intent_anchor, known,
                narrator=protagonist_name,
                viewpoint=_vp_anchor["viewpoint"],
            )
            # 优化项③：只有判据档给得出「本帧主体」，词表档给不出（它的 subjects 是在场全集）。
            frame_subjects = list(anchor_subjects)
        else:
            fallback_anchor = scene_illustration.fallback_illustration_anchor(clean)
            # A′：词表档没有判据字段 ⇒ 回退在场角色全集（口径比判据档宽，两档各锁一条）。
            anchor_subjects = _frame_subject_names(present)
        fallback_anchor_needs_arbitration = True
    planned_anchor = plan.get("anchor", "")
    requested_anchor = planned_anchor or fallback_anchor
    if plan or fallback_anchor_needs_arbitration:
        requested_anchor = scene_illustration.resolve_illustration_anchor(
            clean, requested_anchor,
        )
    plan_retargeted = bool(
        plan and planned_anchor
        and image_prompt_extract.restore_jailbreak(planned_anchor).strip()
        != image_prompt_extract.restore_jailbreak(requested_anchor).strip()
    )
    if plan_retargeted:
        corrected_scene = scene_illustration.illustration_scene_excerpt(
            visible_story, requested_anchor, subjects=anchor_subjects,
        )
        # 重定向只替换错误高潮动作，不能把 Krea2 的英文动作底座也清空；
        # 否则独立 Profile 一旦拒答，只能退回没有角色和剧情事实的通用模板。
        request_prompt = image_prompt_extract.build_fallback_content_tags(corrected_scene)
        motion = image_prompt_extract.infer_motion(corrected_scene)
        # 高潮重定向只纠正动作与锚点；主计划 subjects 已通过配置角色全集
        # 精确校验，是角色身份真源。代词化正文不应把这些角色覆盖为空。
        retarget_actors = [
            name for name in known
            if name in corrected_scene or name in present
        ]
        request_actors = list(dict.fromkeys(request_actors + retarget_actors))
    scene_narrative = encounter_narrative if character_encounter else (
            scene_illustration.illustration_scene_excerpt(
                visible_story, requested_anchor, subjects=anchor_subjects,
            )
        )
    final_actors = _resolve_illustration_request_actors(
        known,
        planned=actor_values if plan else [],
        user_text=user_text,
        narrative=scene_narrative,
        present=present,
        encounter=encounter_actors if character_encounter else [],
        priority_text=climax_paragraph,
        absent=absent,
    )
    if final_actors:
        request_actors = final_actors
    # 第 48 刀 · B2（2026-09-22）＋ 第 49 刀 · A2（2026-09-22 用户定案「先 B 再 A2」）：
    # 画面主体（契约/分述/人数词看到的）**只能是「在场角色」的子集** —— 判据 `subjects` 里若混进
    # 了不在场的名字，一律剔出。真机轮 A（thread `09f79e6f`，09:54）：判据 `['妲千沁','凌渊']`
    # 把**不入画的主人公**算成画面主体，而 `actors=['妲千沁']` ⇒ 契约判成双人帧 ⇒
    # 成稿 `2girls, adult woman, adult man`（多画一个人）。
    #  B2（第 48 刀）：**判据锚点被仲裁机推翻**时（画面已换段 ⇒ 判据 `subjects` 不再逐字描述
    #    这一帧）**收窄**，而不是整条撤回、退回「在场全集 `actors`」——旧行为会把
    #    单人帧判成多人帧（真机轮 C，12:28）。
    #  A2（第 49 刀）：**不再以「锚点被推翻」为条件** —— 只要判据主体里有 ∉ 在场角色的名字
    #    就剔。⇒ 也覆盖「锚点没被推翻、但判据仍把不在场者算成主体」的轮 A。
    #    与第 32 刀「归因＝`actors`（在场全集）／输出＝契约主体」一脉相承：
    #    **输出只会是归因的子集，永不超出在场角色**。
    # ⚠ 保护：`in_scene` 为空（本帧取不到在场角色）⇒ **不收窄**（保守＝旧行为；免得把判据主体
    #    整条清空、契约再回落一次空表）。交集为空 ⇒ 空表 ⇒ 契约仍退回 `actors`（零回归兜底）。
    # ⚠ 只收窄「契约/分述/人数词看到的画面主体」。**不动**：判据 rubric、仲裁机 branch① 门槛、
    #   以及**内容优先**那条链——取材扩窗仍用未收窄的 `anchor_subjects`（本条只改 `frame_subjects`），
    #   成稿编译与机位反推（38／39 刀）原样。
    in_scene = {str(name).strip() for name in request_actors if str(name).strip()}
    # 第 50 刀 · B3（2026-09-22 用户定案「主人公**不是被剔除，而是等效镜头**」）：
    # 判据锚点句里的**名字**未必是本帧主体 —— 第一人称下它常是「镜头」（被看向/被亲吻的
    # 主人公）。而收敛只认字面名字 ⇒ 锚点句用**代词**写主体、用**名字**写镜头时，收敛会把
    # **主体丢掉、只留下镜头**。真机轮 C（thread `09f79e6f`，12:28）锚点句「**她**掖完被子，
    # 把视线从墙上收回来，落到**凌渊**脸上。」⇒ 收敛成 `['凌渊']`（＝镜头，且不在场）
    # ⇒ 与在场角色无交集 ⇒ 契约退回在场全集 ⇒ 成稿把单人帧画成 `2girls`（多画一个人）。
    # ⇒ 收敛结果与在场角色**无交集**时视为收敛失败：回退到**未收敛的判据主体**再按在场收窄
    #    （同一份判据，只是跳过收敛这一步；正确画面＝主体 [妲千沁] + 视线朝镜头）。
    # ⚠ 只在「无交集」这一种确定坏掉的情形回退（有交集就尊重收敛结论）；回退结果仍 ⊂ 在场角色。
    if judge_subjects and frame_subjects and in_scene:
        restored = [name for name in judge_subjects if name in in_scene]
        if restored and not [name for name in frame_subjects if name in in_scene]:
            frame_subjects = restored
    if frame_subjects and in_scene:
        frame_subjects = [name for name in frame_subjects if name in in_scene]
    # 视角（生图模式，2026-09-20 用户定案）：对**终选锚点**判——仲裁机换了段，画面就换了。
    # 手选优先（`ctx["illustration_viewpoint"]`＝「多元数据插入」面板人称按钮组，三档
    # 自动/第一人称/第三人称），自动判定拿不到信号时默认第一人称；冲突留痕写进 detail 供审计。
    viewpoint_detail = scene_illustration.frame_viewpoint_detail(
        requested_anchor, scene_narrative,
        override=str(ctx.get("illustration_viewpoint") or ""),
    )
    # 主人公（叙事者化身）性别：只对主人公、只作**画面事实**注入（不机械拼 1girl/1boy）；
    # 姓名取用户人设名（＝世界书「角色卡·<主角名>」条目所绑的主角名），性别先查世界书条目
    # 正文、再退回通用表「性别」列。主人公不入画（第一人称 / 未列为本帧主体）⇒ 不注入。
    # ⚠ 第 37 刀（2026-09-21 用户实锤）：此闸原先只看 `protagonist in request_actors`、
    # **不看视角** ⇒ 第一人称下照样注入 ⇒ 真机在正确的 `2girls` 之外多写违规的 `1boy`。
    # 视角结论上面已算好（`viewpoint_detail`），直接复用。
    # 第 51 刀：`protagonist` 改用三段读取结果（不再是常空的 `user_name`，修复 D4/D5）。
    protagonist = protagonist_name
    protagonist_gender = ""
    if (
        viewpoint_detail["viewpoint"] != "first_person"
        and protagonist
        and protagonist in [str(actor).strip() for actor in request_actors]
    ):
        # 主人公不入画（第一人称 / 未列为本帧主体）⇒ 连世界书都不读，省一次 I/O。
        protagonist_gender = _protagonist_gender(ctx, repo_id, protagonist)
    request_appearance = _filter_illustration_appearance(
        _illustration_appearance(ctx), request_actors, known,
    )
    # 第 36 刀：表格只读一次、两处复用（① 与条目融合 ② 确定性来源诊断）。
    tables_for_facts = _repo_tables_for_facts(ctx, repo_id)
    appearance_sources = scene_illustration.actor_appearance_sources(
        request_appearance, request_actors, tables_for_facts,
    )
    request_appearance = scene_illustration.supplement_actor_table_appearance(
        request_appearance, request_actors, tables_for_facts,
    )
    request_appearance = scene_illustration.supplement_protagonist_gender(
        request_appearance, request_actors, protagonist, protagonist_gender,
        viewpoint=viewpoint_detail["viewpoint"],
    )
    # 第 46 刀：逐主体性别（零 LLM）—— 只为「人数词按画面主体的实际性别组成写」服务，
    # **不做画面事实注入**。候选名＝画面主体三支来源的并集（计划 subjects / 判据
    # frame_subjects / 在场 actors），保证 `_contract_subject_names` 无论走哪支都查得到。
    gender_candidates = [*request_actors, *frame_subjects]
    for subject in (plan.get("subjects") or []) if plan else []:
        if isinstance(subject, dict):
            gender_candidates.append(str(subject.get("name") or ""))
    subject_genders = _subject_gender_map(
        ctx, repo_id, gender_candidates,
        protagonist=protagonist, protagonist_gender=protagonist_gender,
        tables=tables_for_facts,
    )
    return {
        "request_prompt": request_prompt, "prompt_source": prompt_source,
        "requested_anchor": requested_anchor, "plan_retargeted": plan_retargeted,
        "scene_narrative": scene_narrative, "motion": motion,
        "request_actors": request_actors, "request_appearance": request_appearance,
        "appearance_sources": appearance_sources,
        "frame_subjects": frame_subjects,
        "subject_genders": subject_genders,
        "viewpoint": viewpoint_detail["viewpoint"],
        "viewpoint_detail": viewpoint_detail,
        "narrator": protagonist_name,
        "narrator_source": protagonist_source,
    }


def _wb_first_person_frame_filter(ctx: dict, scene_spec: dict) -> dict:
    """第 57 刀 ③：第一人称「画面事实筛分」的接缝（就地改 `scene_spec`，返回被剔片段）。

    用户口径：「第一人称＝先内容，再**核对主人公主视角**、**筛分**镜头里该保留的东西」。
    第 55 刀只把筛分写成提示词指令（靠模型自觉）⇒ 真机实锤提示词自相矛盾
    （`close-up` 同时要求 `broad rounded hips` / `white silk stockings`），因为
    `appearance`/`wardrobe` 在字段账本里是 `required`、编译器**不能省略**。
    本接缝把筛分**下沉到数据层**：进编译器之前先按取景上限剔掉装不下的部位/衣物。

    判定与白名单全在 `image_prompt_profiles.first_person_frame_filter`（纯函数、可单测）。
    `applied=False`（第三人称 / 风景帧 / 下半身帧 / 无可剔片段）⇒ **一个键都不动**
    ⇒ 逐字节等于旧行为（零回归锁）。
    """
    from app.services import image_prompt_profiles

    verdict = image_prompt_profiles.first_person_frame_filter(scene_spec)
    if not verdict["applied"]:
        return {}
    scene_spec["appearance"] = verdict["appearance"]
    scene_spec["wardrobe"] = verdict["wardrobe"]
    run_trace.emit(
        ctx, "illustration.first_person_filter", status="applied",
        scope=verdict["scope"], removed=verdict["removed"],
    )
    return verdict


def _wb_comfy_scene_spec(ctx: dict, repo_id: str, plan, clean: str, sig: dict,
                         pool: dict, target: dict) -> dict:
    """comfy 路径③：组装 scene_spec（叙事/外观/比例 + 计划侧字段按需透传）。

    逐字自 `_agency_writeback` 搬移。
    """
    from app.services import image_prompt_extract, scene_illustration
    from app.services.regex_engine import Placement

    character_encounter = sig["character_encounter"]
    encounter_narrative = sig["encounter_narrative"]
    plan_retargeted = target["plan_retargeted"]
    request_actors = target["request_actors"]
    request_appearance = target["request_appearance"]
    scene_narrative = target["scene_narrative"]

    profile_draft_prompt = target["request_prompt"]
    if not plan:
        # 漏计划时 draft 也只能来自最终高潮片段；整轮正文会把前段离场人物、
        # 动作和外貌重新带回本地 Profile。
        profile_draft_prompt = _apply_regex(
            ctx,
            image_prompt_extract.build_fallback_content_tags(scene_narrative),
            Placement.IMAGE_PROMPT,
            is_prompt=True,
        ).strip()
    protected_narrative = (
        encounter_narrative if character_encounter else
        scene_illustration.protected_illustration_scene_excerpt(
            clean, scene_narrative,
        )
    )
    scene_spec = {
        "narrative": image_prompt_extract.restore_jailbreak(scene_narrative),
        "protected_narrative": protected_narrative,
        "draft_prompt": profile_draft_prompt,
        "appearance": request_appearance,
        "wardrobe": sig["wardrobe"],
        "locale": sig["locale"],
        "actors": request_actors,
        "rating": sig["image_rating"],
        "aspect_ratio": (
            "" if plan_retargeted else plan.get("aspect_ratio")
        ) or (
            "4:3" if character_encounter else scene_illustration.infer_aspect_ratio(
                pool["scene_text"], request_actors,
            )
        ),
        "profile": ctx.get("prompt_profile") or "anima_tags",
    }
    if ctx.get("appearance_source") in {"worldbook", "character_card"}:
        scene_spec["appearance_source"] = ctx.get("appearance_source")
    if target.get("appearance_sources"):
        # 第 36 刀：逐角色外观事实来源（entry/table/merged/none），确定性诊断量。
        scene_spec["appearance_sources"] = target["appearance_sources"]
    if target.get("subject_genders"):
        # 第 46 刀：`{角色名: "女"/"男"}`（零 LLM）。供 `image_prompt_profiles` 按**画面主体的
        # 实际性别组成**写人数词；缺键 / 全空 ⇒ 不写此键 ⇒ 下游退回中性措辞（零回归）。
        scene_spec["subject_genders"] = target["subject_genders"]
    if plan.get("subjects"):
        # 主计划的英文主体描述是拒答降级时仍可用的身份真源；即使高潮锚点
        # 被纠正，稳定外貌不会随动作重定向而失效。
        selected_subjects = [
            subject for subject in plan["subjects"]
            if str(subject.get("name") or "").strip() in request_actors
        ]
        if selected_subjects:
            scene_spec["subjects"] = selected_subjects
    frame_subjects = [
        str(name).strip() for name in (target.get("frame_subjects") or [])
        if str(name).strip()
    ]
    # 视角口径下传（2026-09-20 用户定案）：三档系统提示词据此写「叙事者不入画／身体局部要入画」，
    # 契约据此在第一人称下剔叙事者。`narrator` 只在**有值**时写 ⇒ 无主角名的旧路径零变化。
    viewpoint = str(target.get("viewpoint") or "").strip()
    if viewpoint in scene_illustration.VIEWPOINT_MODES:
        scene_spec["viewpoint"] = viewpoint
        # 第 51 刀（2026-09-22）：叙事者姓名改取三段读取结果（`target["narrator"]`，
        # 由 `repo_protagonist_name` 算好），不再只看常空的 `ctx["user_name"]` —— 修复 D4/D5
        # （生产里 user_name 为空 ⇒ 5 处数据层保护集体空转、主人公入画／女主服装绑错人）。
        narrator_name = str(target.get("narrator") or "").strip()
        if narrator_name:
            scene_spec["narrator"] = narrator_name
            src = str(target.get("narrator_source") or "").strip()
            if src:
                scene_spec["narrator_source"] = src
    if frame_subjects and "subjects" not in scene_spec:
        # 优化项③：缺块路径没有计划 subjects，但有判据判出的本帧主体 ⇒ 下传，让多人契约
        # 按画面主体数判。计划 subjects 是身份真源、优先级更高，故只在它缺席时补位。
        # 空表一律不写键 ⇒ 契约退回 actors 的旧行为逐字节不变（零回归）。
        scene_spec["frame_subjects"] = list(dict.fromkeys(frame_subjects))
    if plan.get("visual_facts"):
        visual_facts = plan["visual_facts"]
        if plan_retargeted:
            # 真正需要纠正锚点时，只淘汰不属于纠正后动作窗口的事实；
            # 不能因为一个锚点变化就把该窗口内已有逐字证据全部清空。
            visible_narrative = image_prompt_extract.restore_jailbreak(
                scene_narrative,
            )
            visual_facts = [
                item for item in visual_facts
                if image_prompt_extract.restore_jailbreak(
                    str(item.get("evidence") or ""),
                ).strip() in visible_narrative
            ]
        if visual_facts:
            scene_spec["visual_facts"] = visual_facts
    if character_encounter:
        scene_spec["encounter"] = sig["encounter_facts"]
    if not plan_retargeted and plan.get("art_direction"):
        scene_spec["art_direction"] = plan["art_direction"]
    if not plan_retargeted and plan.get("camera"):
        scene_spec["camera"] = plan["camera"]
    if not plan_retargeted and plan.get("composition"):
        scene_spec["composition"] = plan["composition"]
    if not plan_retargeted and plan.get("action_sequence"):
        scene_spec["action_sequence"] = plan["action_sequence"]
    return scene_spec


def _wb_comfy_profile(ctx: dict, plan, scene_spec: dict, plan_retargeted: bool) -> dict:
    """comfy 路径④：Profile 成稿编译（同轮成稿 → 独立 LLM 链 → 本地事实兜底）+ 字段账本。

    就地把 `profile_prompt` / `field_ledger` / `negative_prompt` 写进 scene_spec 并返回。
    逐字自 `_agency_writeback` 搬移。
    """
    from app.services import image_prompt_profiles
    from app.services.regex_engine import Placement

    # illustration JSON 已在解析前复用正文的 AI_OUTPUT 正则；成稿再叠加
    # IMAGE_PROMPT 专用清洗。
    # 高潮锚点被纠正时，旧成稿描述的是错误桥段，必须丢弃并从纠正后的事实本地编译。
    inline_profile = ""
    if plan and (
        not plan_retargeted or bool(scene_spec.get("visual_facts"))
    ):
        # 锚点被纠正但计划中仍有逐字证据落在纠正后窗口时，保留 Agent 已完成的
        # 具体英文画面；normalize/字段账本仍会淘汰格式错误或事实不覆盖的成稿。
        inline_profile = str(plan.get("profile_prompt") or "")
        inline_profile = _apply_regex(
            ctx, inline_profile, Placement.IMAGE_PROMPT, is_prompt=True, depth=0,
        )
    compiled_profile = image_prompt_profiles.normalize_inline(
        scene_spec["profile"], inline_profile, scene_spec,
    )
    profile_strategy = "same_turn" if plan else "independent_chain"
    if not compiled_profile:
        # 同轮成稿被清空，或同轮义务剥离后主模型本就没带 illustration 块：
        # 全走一次携带当前防拦截预设的独立 LLM 调用（image_prompt_profiles.generate 链，
        # 已实证 status=ok），让模型按当前正文重写英文画面；失败才回退本地事实兜底。
        compiled_profile, profile_strategy = _profile_llm_fallback(ctx, scene_spec)
    if not compiled_profile:
        local_profile = image_prompt_profiles.deterministic_fallback(
            scene_spec["profile"], scene_spec,
        )
        compiled_profile = image_prompt_profiles.normalize_inline(
            scene_spec["profile"], local_profile, scene_spec,
        ) or local_profile
        profile_strategy = "local_fallback"
    compiled_profile, field_ledger = image_prompt_profiles.complete_field_coverage(
        scene_spec["profile"], compiled_profile, scene_spec,
    )
    missing_fields = [
        name for name, item in field_ledger.items()
        if item.get("required") and not item.get("covered")
    ]
    if profile_strategy == "same_turn" and any(
        item.get("expected") for item in field_ledger.values()
    ) and field_ledger.get("__field_repair__"):
        profile_strategy = "same_turn+field_repair"
    scene_spec["profile_prompt"] = compiled_profile
    scene_spec["field_ledger"] = field_ledger
    appearance_missing_actors = [
        name for name in (scene_spec.get("actors") or [])
        if name and name not in str(scene_spec.get("appearance") or "")
    ]
    run_trace.emit(
        ctx, "illustration.profile", profile=scene_spec["profile"],
        strategy=profile_strategy, inline_chars=len(inline_profile),
        output_chars=len(compiled_profile), plan_retargeted=plan_retargeted,
        field_ledger=field_ledger, missing_fields=missing_fields,
        appearance_missing_actors=appearance_missing_actors,
        # 第 36 刀：逐角色外观来源（entry/table/merged/none）——确定性、可被探针直接判读。
        appearance_sources=scene_spec.get("appearance_sources") or {},
        # 第 66 刀（2026-09-28 · 防呆登记）：第一人称下该 profile **有没有 POV 机械保证**。
        # 只进诊断 trace、**不进提示词**；非第一人称／已覆盖 profile ⇒ 空串（零回归）。
        # 真机取证：`prompt_profile` 33/33 全 `anima_tags` ⇒ `natural_language` 零使用，
        # 而它刻意没有机械层（见手册红线 79 ④）⇒ 一旦被选用要能**看见**，不能静默退回缺陷态。
        viewpoint_coverage_warning=image_prompt_profiles.viewpoint_coverage_warning(
            scene_spec["profile"], scene_spec,
        ),
    )
    profile_negative = image_prompt_profiles.negative_prompt(
        ctx.get("prompt_profile") or "anima_tags", scene_spec,
    )
    if profile_negative:
        scene_spec["negative_prompt"] = profile_negative
    return scene_spec


def _wb_comfy_illustrate_env(ctx: dict, plan, sig: dict, target: dict,
                             scene_spec: dict, *, motion: int) -> dict:
    """comfy 路径⑤：组装 illustrate_req 外壳（含视频配置与 anchor 兜底开关）。

    Profile：同轮成稿（模型仍自发带块时）优先；否则由携带防拦截预设的独立
    LLM 链（_profile_llm_fallback）编译，仍失败才走本地事实兜底。
    逐字自 `_agency_writeback` 搬移。
    """
    from app.services import image_prompt_extract

    request_prompt = image_prompt_extract.format_comfy_prompt(target["request_prompt"])
    # 改造前 request_prompt 是同一个局部变量：格式化后既写进 illustrate_req["prompt"]，
    # 也被 ⑦ 的 trace 计量（prompt_chars）。此处写回 target 以复原「单变量」语义，
    # 否则 trace 会读到格式化前的长度，求值口径漂移（见 _wb_emit_illustration_trace）。
    target["request_prompt"] = request_prompt
    return (
        {"prompt": request_prompt, "motion": motion, "actors": target["request_actors"],
         "anchor": target["requested_anchor"], "scene_spec": scene_spec,
         # V1.5 默认开放：视频配置随事件透传，供 dry-run 组装「上交视频模型的参数」
         # （测试视频参数有没有正确上传；无视频工作流/节点也不影响出图）
         "video_config": {
             "base_url": str(ctx.get("vid_base") or ""),
             "model": str(ctx.get("vid_model") or ""),
             "size": "1280x720",
             "proxy": str(ctx.get("vid_proxy") or ""),
         },
         "allow_anchor_fallback": (
             bool(sig["visible_story"]) and (
                 sig["local_scene_fallback"] or sig["missing_plan_fallback"]
                 or sig["first_story_reply"] or sig["character_encounter"]
             )
         ) and not plan}
        if sig["at_climax"] and (request_prompt or scene_spec["narrative"]) else {}
    )


def _wb_frames_and_video(ctx: dict, illustrate_req: dict, scene_spec: dict,
                         clean: str, transition_decision, motion: int,
                         *, compile_video: bool = True) -> str:
    """comfy 路径⑥：首尾帧描述/复用决策 + 视频（含转场）请求编译。

    返回 `video_prompt_text`（未开启/失败时为空串）。逐字自 `_agency_writeback` 搬移。

    `compile_video=False`（第 57 刀 · 多批派发的第 2..N 张）：只补帧描述，**不编译视频请求** ——
    多批视频的合并是**后续步骤**，本步先只做多批生图，避免产出「N 段各自独立、未合并」的视频。
    默认 True ⇒ 主链路逐字节不变（零回归锁）。
    """
    from app.services import story_frames

    # V1.5/W2：首帧复用决策合并（坑B/坑I）——L0 确定 → L0；L0 ambiguous → 消费 L1 <transition>。
    # N 尾帧从历史最近角色回复提取（方案 B，零 wire），N+1 首帧从当前正文提取；合并结果
    # 三态（reuse/regenerate/ambiguous）随出图请求透传，前端叠加坑C「有图前提」裁决。
    if illustrate_req:
        # 2026-08-29 验收「帧提示词编译失败」实锤：scene_spec 只随 climax 请求下发，
        # first_story_reply 等非高潮路径的首尾帧请求拿不到 sceneSpec → 前端帧编译被
        # 整段跳过 → 触发「禁止中文原文直提」守卫 → 首帧必失败。首尾帧编译以帧描述
        # 为主输入、scene_spec 提供角色外貌等事实参照，必须恒定随请求下发。
        illustrate_req.setdefault("scene_spec", scene_spec)
        prev_tail_desc = _resolve_prev_tail_desc(ctx)
        frames = story_frames.extract_story_frames(clean)
        merged = story_frames.merge_frame_reuse(
            prev_tail_desc, frames.opening, transition_decision,
        )
        illustrate_req["transition"] = merged.decision
        # V1.6/W3：首尾帧描述 + 上尾帧描述随事件下发（firstlast 生图 + 转场编译的素材源）。
        # climax 也带（无害冗余，前端非 firstlast 忽略）；首帧复用决策用 opening 同源，不重复提取。
        illustrate_req["first_frame_desc"] = frames.opening[:500].strip()
        illustrate_req["last_frame_desc"] = frames.closing[:500].strip()
        illustrate_req["prev_tail_desc"] = (prev_tail_desc or "")[:500].strip()
    # 正常链路：comfy_video 开启时 produce 层编译视频提示词 + 参数，
    # 随事件下发；失败静默降级 None，不阻断出图。
    # 三模态开关：comfy_video 关=不调 _extract_video_action_plan（省 LLM 调用）、
    # 不编译 video_request/transition_video_request（省 token 干烧），完全对齐图/音链的关=零成本。
    video_prompt_text = ""
    if compile_video and illustrate_req and ctx.get("comfy_video"):
        try:
            from app.services import video_prompt as _vp_mod
            _merged_spec = dict(scene_spec)
            if "motion" not in _merged_spec:
                _merged_spec["motion"] = int(motion or 0)
            # V1.6/W3：视频模式先定（前端「首尾帧生成」选项推导，缺省 climax
            # 兼容旧预设）——提取协议按模式分支：climax 定格窗口无对白，
            # firstlast 从头到尾含全部对白。
            _video_mode = str(ctx.get("video_mode") or "climax")
            if _video_mode not in ("climax", "firstlast"):
                _video_mode = "climax"
            # 选 A：从剧情原文理解体态，补动作延伸 + 简化外貌/场景。
            # 失败静默回退（_vp_plan 为空），不阻断出图；非 retargeted 时主模型
            # 已给 action_sequence，本提取作为兜底优先补齐，避免动作段退化。
            _vp_plan = _extract_video_action_plan(ctx, _merged_spec, video_mode=_video_mode)
            if _vp_plan.get("action_sequence"):
                _merged_spec["action_sequence"] = _vp_plan["action_sequence"]
            if _vp_plan.get("subject_scene"):
                _merged_spec["video_subject_scene"] = _vp_plan["subject_scene"]
            if _vp_plan.get("audio_design"):
                _merged_spec["audio_design"] = _vp_plan["audio_design"]
            illustrate_req["video_mode"] = _video_mode
            _vcfg = illustrate_req.get("video_config") or {}
            if _video_mode == "firstlast":
                illustrate_req["video_request"] = _vp_mod.build_video_request(
                    mode="firstlast", spec=_merged_spec, video_config=_vcfg,
                    first_frame_desc=illustrate_req.get("first_frame_desc") or "",
                    last_frame_desc=illustrate_req.get("last_frame_desc") or "",
                    prev_tail_desc=illustrate_req.get("prev_tail_desc") or "",
                )
            else:
                illustrate_req["video_request"] = _vp_mod.build_video_request(
                    mode="climax", spec=_merged_spec, video_config=_vcfg,
                    # first_frame_desc 留空：图职责描述由 video_prompt 用画面级
                    # 动作瞬间（subjects/visual_facts/composition）兜底，与 [动作]
                    # 同源，避免把围绕锚点截取的可能陈旧 narrative 写进 [参考绑定]。
                )
            video_prompt_text = str(
                (illustrate_req["video_request"].get("submit") or {}).get("prompt") or ""
            )
            # W3 转场任务（坑F/坑G）：firstlast 且首帧需独立生成（transition≠reuse）→
            # 额外编译转场 video_request（图片1=上尾帧、图片2=当前首帧），随事件下发。
            _decision = str(illustrate_req.get("transition") or "")
            if _video_mode == "firstlast" and _decision not in ("reuse", ""):
                illustrate_req["transition_video_request"] = _vp_mod.build_video_request(
                    mode="transition", spec=_merged_spec, video_config=_vcfg,
                    # transition 分支：first_frame_desc=当前首帧描述（终点），
                    # prev_tail_desc=上尾帧描述（起点）；last_frame_desc 该分支不使用。
                    first_frame_desc=illustrate_req.get("first_frame_desc") or "",
                    prev_tail_desc=illustrate_req.get("prev_tail_desc") or "",
                )
        except Exception:
            illustrate_req["video_request"] = None
    return video_prompt_text


def _wb_emit_illustration_trace(ctx: dict, illustrate_req: dict, *, plan, sig: dict,
                                pool: dict, target: dict, scene: str, local_scene: str,
                                video_prompt_text: str) -> None:
    """comfy 路径⑦：派发 `illustration.request` 事件（含 reason 阶梯与提示词计量）。

    逐字自 `_agency_writeback` 搬移；末尾三字段（`viewpoint` / `viewpoint_detail` /
    `frame_subjects`）为 2026-09-22「前置观测」新增（见下方注释）。
    """
    viewpoint_detail = target.get("viewpoint_detail") or {}
    run_trace.emit(
        ctx,
        "illustration.request",
        status="emitted" if illustrate_req else "skipped",
        reason=_wb_illustration_reason(
            illustrate_req=illustrate_req,
            plan_retargeted=target["plan_retargeted"],
            illustration_plan=plan,
            character_encounter=sig["character_encounter"],
            local_scene_fallback=sig["local_scene_fallback"],
            first_story_reply=sig["first_story_reply"],
            missing_plan_fallback=sig["missing_plan_fallback"],
            prompt_source=target["prompt_source"],
            at_climax=sig["at_climax"],
        ),
        scene=scene,
        inferred_scene=local_scene,
        actor_count=len(target["request_actors"]),
        actors=target["request_actors"],
        actor_candidates=pool["known"],
        status_actors=[name for name in pool["known"] if name in sig["present"]],
        plan_retargeted=target["plan_retargeted"],
        prompt_chars=len(target["request_prompt"]),
        # V1.5 默认开放：视频生成提示词记入 trace（测试模式核对提示词质量）
        video_prompt_chars=len(video_prompt_text),
        video_prompt=video_prompt_text,
        # 前置观测（2026-09-22 · 人称「单一真源」改造第一步）：补记人称判定与画面主体。
        # 动机＝全量 `agent-trace.jsonl` 里 `viewpoint` **0 命中**（D6）⇒ 「同一帧人称判两次」
        # （D1）与「判据主体收敛失败」都不可观测，只能靠真机成图反推。
        # ⛔ **纯记录**：不参与任何判定分支，出图请求与成稿逐字节不变。
        # `viewpoint_detail` 由 `frame_viewpoint_detail` 恒返回 dict（`viewpoint`/`auto`/
        # `source`/`conflict`）；缺失 ⇒ 记空值（防御 `.get`，不抛错）。
        viewpoint=str(target.get("viewpoint") or ""),
        viewpoint_detail=viewpoint_detail,
        frame_subjects=[str(name) for name in (target.get("frame_subjects") or [])],
        # ⚠ 2026-09-22 追加：叙事者姓名与其来路。诊断「手选第一人称却仍画出主人公」时，
        #   旧观测**看不到 narrator 到底有没有取到**（只能靠离线复现反推）⇒ 补记。
        #   同样**纯记录**、不参与任何判定分支。
        narrator=str(target.get("narrator") or ""),
        narrator_source=str(target.get("narrator_source") or ""),
    )


def _wb_comfy_extra_requests(ctx: dict, repo_id: str, plan, clean: str, sig: dict,
                             pool: dict, *, primary_anchor: str, primary_motion: int,
                             prompt_override: str, transition_decision,
                             user_text: str, primary_prompt: str = "") -> list[dict]:
    """第 57 刀 · 多批派发：给**除主锚点外**的其余锚点各编译一条出图请求（2026-09-24）。

    用户口径（逐字）：「如果用户输入的生图数量 **≥** 高潮点数目，则**是高潮点的片段全部生图**
    并插入合适的位置；如果 **<** 高潮点数目，则**按照打分制度，从高到低**，选择高潮点位置生图」。

    返回**各请求自带 `anchor`**（下游 `_illustration_anchor_offset` 据此逐条定位插入点）
    ⇒ 每张图插到**自己的正文位置**（前端 `insertSlotAtOffset` 已支持多槽、零改动）。

    三条短路（任一命中 ⇒ `[]`，主链路逐字节不变）：
      ① 批量数目 < 2（0/缺省 = 单张）；② `ctx` 里没有多锚点列表（判据档没跑 / 旧链路）；
      ③ 锚点数 < 2。
    另：某锚点算出来是空请求（`at_climax` 不成立等）⇒ **跳过该条**，不影响其余。

    ⚠ **逐点各过一遍既有仲裁机**（在 `_wb_comfy_scene_target` 内）—— 红线 74／76 的修法
      必须对每一张都生效。⚠ **不重复调判据 LLM**（见 `anchor_override` 的说明）。
    ⚠ 第 59 刀（视频连续化 · 用户定案「前后衔接按首尾帧生成，衔接不上就是高潮片段延续」）：
      firstlast 模式下 extras **不再整跳过** —— 每张 extra = **段桥**：
        首帧 = 上一张的画面（正文序链，首桥 = 主图画面）；尾帧 = 本锚点画面（槽位自己的图）；
        桥视频复用现有 `mode="transition"` 编译（起点=prev_tail、终点=first_frame）。
      衔接不上（上一张帧图失败）⇒ 前端退**高潮片段延续**（该槽按普通图出、视频从定格延续）。
    """
    from app.services import scene_illustration

    count = _anchor_batch_count(ctx)
    if count < 2:
        return []
    items = ctx.get("illustration_anchor_intents") or []
    if len(items) < 2:
        return []
    # 主路径用的是「分最高」那条（＝ `select_anchor_intents(items, 1)`），已经在主循环里出过图 ⇒ 跳过。
    # 同时按**仲裁后**的锚点去重：仲裁若把主图挪到别的段，那一段也不能再出一张。
    primary_raw = scene_illustration.select_anchor_intents(items, 1)
    seen = {
        str(primary_anchor or "").strip(),
        str(primary_raw[0].get("anchor") or "").strip() if primary_raw else "",
    }
    # 第 57 刀 A（2026-09-24 用户拍板）：**仲裁后**再按「插入点」比对一次 —— 两个不同的
    # 判据锚点可能被定位收敛到**同一处**；同位 ⇒ 跳过（防两图叠放）。
    # 主图的插入点用同一口径（clean 文本）预置进集合；偏移统一吸附到段落末尾（第 58 刀），
    # 否则同段内两个不同句尾会吸附到同一段落尾 ⇒ 仍叠放。
    used_offsets: set[int] = set()

    def _para_off(anchor: str) -> int | None:
        raw = scene_illustration.illustration_anchor_offset(clean, anchor) if anchor else None
        return (scene_illustration.snap_offset_to_paragraph_end(clean, raw)
                if raw is not None else None)

    primary_off = _para_off(primary_anchor)
    if primary_off is not None:
        used_offsets.add(primary_off)
    # 第 59 刀（视频连续化）：段链起点 = 主图画面（首桥的「上一张」）。
    _prev_pic = str(primary_prompt or "")
    extras: list[dict] = []
    for item in scene_illustration.select_anchor_intents(items, count):
        anchor = str(item.get("anchor") or "").strip()
        if not anchor or anchor in seen:
            continue
        seen.add(anchor)
        target = _wb_comfy_scene_target(
            ctx, repo_id, plan, clean, sig, pool,
            prompt_override=prompt_override, motion=primary_motion, user_text=user_text,
            anchor_override=anchor, subjects_override=item.get("subjects"),
        )
        final_anchor = str(target.get("requested_anchor") or "").strip()
        final_off = _para_off(final_anchor)
        if final_off is not None and final_off in used_offsets:
            continue
        if final_off is not None:
            used_offsets.add(final_off)
        scene_spec = _wb_comfy_scene_spec(ctx, repo_id, plan, clean, sig, pool, target)
        # 第 57 刀 ③：多批的每一张都要过「画面事实筛分」——真机实锤（2026-09-24 · 2 张实测）：
        # 主图筛了、第 2 张没筛 ⇒ 第 2 张提示词仍带 `broad rounded hips`／`white silk stockings`。
        # ⚠ 必须在 `_wb_comfy_profile` 之前（同主图：字段账本会把装不下的部位写进稿子）。
        _wb_first_person_frame_filter(ctx, scene_spec)
        scene_spec = _wb_comfy_profile(ctx, plan, scene_spec, target["plan_retargeted"])
        request = _wb_comfy_illustrate_env(
            ctx, plan, sig, target, scene_spec, motion=target["motion"])
        if not request:
            continue
        _wb_frames_and_video(
            ctx, request, scene_spec, clean, transition_decision,
            target["motion"], compile_video=False)
        # 第 59 刀（视频连续化）：firstlast 模式下每张 extra = **段桥**——
        #   首帧 = 上一张的画面（正文序链，首桥 = 主图画面）；尾帧 = 本锚点画面（槽位自己的图）；
        #   桥视频复用现有 mode="transition" 编译（起点=prev_tail、终点=first_frame）。
        # 衔接不上（上一张帧图失败/前端拿不到桥）⇒ 前端退**高潮片段延续**（该槽按普通图出）。
        if str(ctx.get("video_mode") or "") == "firstlast" and ctx.get("comfy_video"):
            _own_pic = str(request.get("prompt") or "")
            request["video_mode"] = "firstlast"
            request["transition"] = "regenerate"     # 首帧（上一画面）需独立生成
            request["prev_tail_desc"] = _prev_pic[:500].strip()
            request["first_frame_desc"] = _prev_pic[:500].strip()   # 帧任务①：上一画面（桥首帧）
            request["last_frame_desc"] = _own_pic[:500].strip()     # 帧任务②：本画面（桥尾帧）
            try:
                from app.services import video_prompt as _vp_mod
                _merged_spec = dict(scene_spec)
                _merged_spec.setdefault("motion", int(target["motion"] or 0))
                request["transition_video_request"] = _vp_mod.build_video_request(
                    mode="transition", spec=_merged_spec,
                    video_config=request.get("video_config") or {},
                    first_frame_desc=request["first_frame_desc"],
                    prev_tail_desc=request["prev_tail_desc"],
                )
            except Exception:  # noqa: BLE001
                pass   # 桥编译失败 ⇒ 前端退高潮延续（该槽按普通图出）
            _prev_pic = _own_pic
        extras.append(request)
    return extras


def _agency_writeback(ctx: dict, deps, reply: str, turn: int, affinity,
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
            _wb_split_media_blocks(ctx, reply))
        # 阶段 C：状态写回 + 快照兜底 + 旧表格块清洗
        clean, raw, before, after, snapshot, st = _wb_apply_state(
            ctx, deps, clean, turn=turn, repo_id=repo_id, card_name=card_name)
        # 连续性 CI（永不阻断或改写正文）
        _wb_run_narrative_ci(ctx, clean, turn=turn, raw=raw, repo_id=repo_id)
        # 阶段 D 前置：可见正文 + 在场/衣着/所在 + 高潮触发信号
        scene = ctx.get("scene") or ""
        sig = _wb_scene_signals(
            ctx, deps, st, clean, snapshot, scene, user_text, illustration_plan, lost)
        wardrobe, locale = sig["wardrobe"], sig["locale"]
        # 音频对白配音请求：comfy_audio 时组装（含机械降级兜底），随 writeback 返回给前端逐角色提交。
        audio_req = _wb_build_audio_request(ctx, audio_plan, sig["visible_story"])
        # 初选出图提示词/动作/角色（comfy 与非 comfy 两条路径共用）
        prompt_override, motion, actors = _wb_initial_illustration_prompt(
            ctx, deps, illustration_plan, sig, user_text=user_text)
        # comfy_illustrate：不同步 render，把 prompt + motion + actors 作为出图请求返回，
        # 前端据 motion 智能选图/视频、据 actors 按角色选 LoRA/底图，走异步 ComfyUI 闭环。
        # 七段接缝依次组装（候选角色池 → 提示词/锚点 → scene_spec → Profile →
        # 请求外壳 → 首尾帧与视频 → 事件派发），判定逻辑与文案逐字未改。
        if ctx.get("comfy_illustrate"):
            pool = _wb_comfy_actor_pool(
                ctx, repo_id, card_name, illustration_plan, actors, sig,
                user_text=user_text)
            target = _wb_comfy_scene_target(
                ctx, repo_id, illustration_plan, clean, sig, pool,
                prompt_override=prompt_override, motion=motion, user_text=user_text)
            scene_spec = _wb_comfy_scene_spec(
                ctx, repo_id, illustration_plan, clean, sig, pool, target)
            # 第 57 刀 ③：第一人称「画面事实筛分」——必须在 Profile 编译**之前**
            # （`_wb_comfy_profile` 会按字段账本强制补齐 appearance/wardrobe，
            #   剔晚了就来不及：改完的稿子里那些装不下的部位已经写进去了）。
            _wb_first_person_frame_filter(ctx, scene_spec)
            scene_spec = _wb_comfy_profile(
                ctx, illustration_plan, scene_spec, target["plan_retargeted"])
            illustrate_req = _wb_comfy_illustrate_env(
                ctx, illustration_plan, sig, target, scene_spec, motion=target["motion"])
            video_prompt_text = _wb_frames_and_video(
                ctx, illustrate_req, scene_spec, clean, transition_decision,
                target["motion"])
            _wb_emit_illustration_trace(
                ctx, illustrate_req, plan=illustration_plan, sig=sig, pool=pool,
                target=target, scene=scene, local_scene=sig["local_scene"],
                video_prompt_text=video_prompt_text)
            # 第 57 刀 · 多批派发：为其余锚点各编译一条请求，随主请求一起交给 finalize_turn
            # 展开成 N 条 rec（各自 anchor_offset ⇒ 各自插到自己的正文位置）。
            # ⚠ `illustrate_req` 为空（非高潮点）时不挂键 ⇒ 行为与改造前一致。
            if illustrate_req:
                extras = _wb_comfy_extra_requests(
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


def _illustration_anchor_offset(reply: str, request: dict) -> int | None:
    """在最终显示正文中定位插画槽；本地兜底 anchor 被正则改写时重新选高潮段。

    第 58 刀（2026-09-24 用户口径）：插图插到**段落下方**，不切进段落中间 ——
    句尾偏移统一吸附到所在段落的末尾（主图／extras／首尾帧同走本口，一处吸附全覆盖）。
    """
    from app.services import scene_illustration

    snap = scene_illustration.snap_offset_to_paragraph_end
    # 首尾帧模式：主槽=首帧图（尾帧 :last 副槽由前端追加楼层末尾），首帧画面锚正文
    # 第一段——主图用的「高潮纠偏/末段兜底」会把开篇铺垫改判到中央/末尾
    # （2026-08-29 用户验收问题①），firstlast 不走那套纠偏。
    if str(request.get("video_mode") or "") == "firstlast":
        offset = scene_illustration.first_frame_anchor_offset(reply)
        return snap(reply, offset) if offset is not None else None
    offset = scene_illustration.illustration_anchor_offset(
        reply, str(request.get("anchor") or ""),
    )
    if offset is None and request.get("allow_anchor_fallback"):
        final_anchor = scene_illustration.fallback_illustration_anchor(reply)
        if final_anchor:
            offset = scene_illustration.illustration_anchor_offset(reply, final_anchor)
    return snap(reply, offset) if offset is not None else None


def _emit_roleplay_ready(ctx: dict, out: dict) -> bool:
    """正文最终化后立即发正文和媒体任务；返回是否已走即时通道。"""
    sink = ctx.get("stream_sink")
    if not callable(sink) or not out.get("result_text"):
        return False
    sink({"replace": out["result_text"]})
    for event in _streamed_illustration_events(out.get("illustrate_recs") or []):
        sink(event)
    for rec in out.get("image_recs") or []:
        sink({"image": rec.get("url"), "id": rec.get("id"),
              "regeneration": rec.get("regeneration")})
    # 音频对白配音与插画同属即时通道：漏发会导致 eager 分支跳过 audio_request，
    # 前端永远收不到台词（日志有 emit、SSE 无事件）。格式对齐 agent_graph 的 yield。
    for rec in out.get("audio_recs") or []:
        sink({"audio_request": {"lines": rec.get("lines") or []}, "id": rec.get("id")})
    return True


def _build_image_prompt(ctx: dict, *, paragraph: str, appearance: str,
                        wardrobe: str, locale: str) -> tuple[str, int, list[str]]:
    """零 LLM 组装自动插画提示词，并复用 IMAGE_PROMPT 清洗规则。"""
    from app.services import image_prompt_extract as ipe, scene_illustration
    from app.services.regex_engine import Placement

    card_name = (ctx.get("card_name") or "").strip()
    known = [str(name).strip() for name in (ctx.get("illustration_actor_names") or [])
             if str(name).strip()]
    actors = [name for name in known if name in (paragraph or "")]
    if card_name and card_name in (paragraph or "") and card_name not in actors:
        actors.append(card_name)
    if not actors and card_name:
        actors.append(card_name)
    request = scene_illustration.build_scene_request(
        paragraph=ipe.restore_jailbreak(paragraph),
        appearance=appearance,
        wardrobe=wardrobe,
        locale=locale,
        actors=actors,
    )
    prompt = _apply_regex(ctx, request.prompt, Placement.IMAGE_PROMPT, is_prompt=True)
    return prompt.strip(), ipe.infer_motion(paragraph), request.actors


def _build_renderer(ctx: dict):
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


def _agency_maintenance_async(ctx: dict, deps, clean: str, turn: int,
                              rag_events: list | None = None) -> None:
    """维护与 turn 完成解耦（2026-08-30 用户实锤：表格维护 LLM 单次 4m52s 挂住对话完成，
    生图早已完毕）。正文/插画发布后立即 turn.completed；维护转后台线程，
    同作品线用锁串行化先后两轮的写入。代价：本轮 rag_events 弹窗不再随结果下发。"""
    import threading

    repo_key = str(ctx.get("repo_id") or ctx.get("thread_id") or "home")
    lock = _maintenance_lock(repo_key)

    def run() -> None:
        with lock:
            try:
                _agency_maintenance(ctx, deps, clean, turn, rag_events)
            except Exception as exc:  # noqa: BLE001 后台维护失败只留痕
                run_trace.emit(ctx, "memory.maintenance", status="error", error=str(exc))

    thread = threading.Thread(target=run, name=f"maintenance-{repo_key[:8]}", daemon=True)
    with _MAINTENANCE_THREADS_LOCK:
        _MAINTENANCE_THREADS.setdefault(repo_key, []).append(thread)
    thread.start()


def _agency_maintenance(ctx: dict, deps, clean: str, turn: int,
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


def _belief_maintenance(ctx: dict, repo_id: str, clean: str, turn: int) -> None:
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


def _table_maintenance(ctx: dict, deps, repo_id: str, clean: str, turn: int) -> None:
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


_MAINTENANCE_THREADS: dict[str, list[threading.Thread]] = {}


_MAINTENANCE_LOCKS: dict[str, threading.Lock] = {}


_MAINTENANCE_THREADS_LOCK = threading.Lock()
