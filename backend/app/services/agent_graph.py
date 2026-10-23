"""Supervisor 多 Agent 系统（LangGraph 手写 StateGraph）——图装配与驱动器。

范式：无卡或带附件请求由 supervisor 判用户意图；有卡纯文本直达 Roleplay，明确强执行命令
用零 LLM 规则分派。专家执行完把结果写回 state；遗留 ReAct 大脑只作为工具专家 Adapter。

分派原则：Supervisor 处理模糊/多能力请求；角色卡纯文本避免重复上传历史。
Supervisor 可使用独立快模型，专家使用主模型；单专家任务直连 END，不做二次判断。

2026-10-23 方案 B 再拆（2271 → 590 行）：五个域已迁出为独立属主，本模块只留图装配
（_build_graph/_graph/_GRAPH）、驱动器 stream_multi_agent 与两个留驻助手
（_rewrite_for_compatibility / _handle_pending_approval——消费方都在本模块）。
旧名经下方 re-export 门面零破坏（方法论见 docs/memory/agent-graph-seam-plan-2026-09-16.md）：
- agent_graph_state       AgentState 底座（TypedDict，新增键必须在此声明）
- agent_graph_supervisor  Supervisor 路由域
- agent_graph_nodes       专家节点域（generate/video/img2img/analyze/inspire/tool_agent/edit/answer/clarify）
- agent_graph_plan        计划编译节点域（plan_compiler_node + 11 个 _pc_* 接缝）
- agent_graph_roleplay   Roleplay 剧情回合域（roleplay_node + 22 个 _rp_* 接缝 + 采样助手）
"""

from __future__ import annotations

import logging
from typing import Iterator
from app.services import agent_context, attachment_store, builtin_agents, generation_approval, run_trace
from app.services import llm as _llm
from app.services.agent_contracts import RunContext

from app.services.agent_support import (  # noqa: F401  拆分 re-export
    _proxy_kw,
)
from app.services.agent_retrieval import (  # noqa: F401  拆分 re-export
    _embed_cfg,
    _history_embed_fn,
    _rag_recall_text,
    _table_recall_text,
    _repo_tables_for_facts,
    _repo_table_absent_actors,
    _reindex_retrieval_tables,
    _curator_index_fn,
    _history_embed_cache,
    _history_embed_lock,
    _HISTORY_EMBED_CACHE_CAP,
)
from app.services.agent_context_assembly import (  # noqa: F401  拆分 re-export
    _resolve_agent_cfg,
    _resolve_skills,
    _render_user_persona,
    _bound_card_names,
    _recent_character_context,
    _mentioned_bound_names,
    _active_fallback_names,
    _card_source,
    _apply_work_persona,
    _resolve_persona,
    _resolve_personas,
    _profile_llm_fallback,
    _card_visual_profiles,
    _illustration_appearance,
    _worldbook_session_state_path,
    _worldbook_session_key,
    _resolve_worldbook,
    _worldbook_scan_text,
    _worldbook_sources,
    _repo_worldbook,
    _curator_worldbook_context,
    _curator_worldbook_context_fn,
    _curator_worldbook_fn,
    _resolve_regex_scripts,
    _apply_regex,
    _history_messages,
    _resolve_preset,
    _tool_on,
    _has_mcp,
    _agent_system,
    _temperature,
    _CHARACTER_DEPARTURE,
    _CHARACTER_RETURN,
    _NEGATED_DEPARTURE,
)
from app.services.agent_fabric import (  # noqa: F401  拆分 re-export
    _flow_doc_hint,
    _knowledge_catalog_text,
    _recipe_catalog_text,
    _live_trace,
    _live_chat,
    _history_summarizer,
    _fabric_capabilities,
    _fabric_discipline,
    _recent_inspiration_blocks,
    _task_session_continuation,
    _recent_user_texts,
    _recent_attachment_lines,
    _fabric_knowledge_history,
    _fabric_run_args,
    _collect_fabric_artifacts,
    _fabric_finalize,
    _recipe_offer_pending,
    _recipe_offer_answer,
    _recipe_hard_replay,
    _fabric_approval_word,
    _run_fabric_content_loop,
    _RECIPE_OFFER_YES,
    _RECIPE_OFFER_NO,
    _RECIPE_OFFER_RE,
)
from app.services.agent_agency import (  # noqa: F401  拆分 re-export
    _builtin,
    _builtin_sampling,
    _has_card,
    _extract_video_action_plan,
    _streamed_illustration_events,
    _video_request_for,
    _video_params_payload,
    _agency_prelude,
    _next_story_turn,
    _agency_propose,
    _agency_core_context,
    _should_fill,
    _apply_table_ops,
    _visible_roleplay_text,
    _status_snapshot_value,
    _illustration_visual_query,
    _ordered_illustration_names,
    _resolve_illustration_request_actors,
    _filter_illustration_appearance,
    _resolve_prev_tail_desc,
    _agency_writeback,
    _illustration_anchor_offset,
    _emit_roleplay_ready,
    _build_image_prompt,
    _build_renderer,
    _maintenance_lock,
    join_maintenance_threads,
    _agency_maintenance_async,
    _agency_maintenance,
    _belief_maintenance,
    _table_maintenance,
    _MAINTENANCE_THREADS,
    _MAINTENANCE_LOCKS,
    _MAINTENANCE_THREADS_LOCK,
)

# ── 方案 B 第二步之 F：流式助手迁出，旧名 re-export 保持调用方/测试/脚本零改动 ──
from app.services.agent_stream import (  # noqa: F401  拆分 re-export
    _stream_enabled,
    _notify_stream_trace,
    _roleplay_failure_result,
    _resume_interrupted_messages,
    _roleplay_continuation_messages,
    _chat_with_optional_stream,
    _ordered_illustration_events,
)
from app.services.agent_graph_state import (  # noqa: F401  兼容 re-export
    AgentState,
)
from app.services.agent_graph_supervisor import (  # noqa: F401  兼容 re-export
    _SUPERVISOR_SYSTEM,
    _supervisor_route,
    _ROUTE_TOOL,
    _ROUTE_LABELS,
    _ROUTE_DESCRIPTIONS,
    _explicit_card_route,
    _route_available,
    _available_routes,
    _route_choice_options,
    _route_choice_payload,
    supervisor_node,
)
from app.services.agent_graph_nodes import (  # noqa: F401  兼容 re-export
    _gen_ctx,
    _styled_prompt,
    generate_node,
    video_node,
    img2img_node,
    analyze_node,
    inspire_node,
    tool_agent_node,
    edit_node,
    answer_node,
    clarify_node,
)
from app.services.agent_graph_plan import (  # noqa: F401  兼容 re-export
    _pc_configured_models,
    _pc_approval_word,
    _pc_judge_text,
    _pc_collect_attachments,
    _pc_prev_batch_and_flow_hint,
    _pc_compile_and_finalize,
    _pc_design_discussion_reply,
    _pc_register_delegation,
    _pc_run_full_loop,
    _pc_ask_recipe_offer,
    _pc_approval_word_reply,
    plan_compiler_node,
)
from app.services.agent_graph_roleplay import (  # noqa: F401  兼容 re-export
    _probe_think,
    _roleplay_sampling,
    _roleplay_sampling_kwargs,
    _stream_sampling_keys,
    _ROLEPLAY_BASE,
    _rp_prepare_worldbook,
    _rp_emit_worldbook_resolved,
    _rp_is_first_story_reply,
    _rp_last_story_round,
    _rp_resolve_persona_block,
    _rp_load_style_config,
    _rp_build_prompt_base,
    _rp_seed_dynamic_parts,
    _rp_append_static_blocks,
    _rp_substitute_macros,
    _rp_prefix_output_structure,
    _rp_user_priority_block,
    _rp_macro_substitute_text,
    _rp_pop_agency_directive,
    _rp_build_tail_messages,
    _rp_recall_memory,
    _rp_flush_citation_trace,
    _rp_compile_continuity,
    _rp_append_state_and_style,
    _rp_prepare_visual_and_tables,
    _rp_inject_agency_context,
    roleplay_node,
)

# 2026-09-01 用户定案：首发**不设总时长上限**——只要还在持续吐 token 就不允许截断；
# 只有 30s 无 token 的流式读超时（llm.build_model streaming）才允许中断。

# T3.3（2026-09-17）：业务数据路径兜底降级留痕（探针日志 _probe 随 roleplay 域迁出）。
_LOG = logging.getLogger("uvicorn.error")






# ── 路由：Supervisor 模型负责语义，代码只校验能力条件 ──












# 固化知识库（DATA_DIR/agent_knowledge/*.md）：流程规范/映射表。
# 目录与上限常量、列举/读取统一走 agent_knowledge 服务（唯一属主）。
# 2026-09-04：带 frontmatter「skill」的文档 = 固化技能（按触发装载，不常驻）；
# 无头的普通知识文档仍全量常驻注入。目录注入文本是本策略的唯一裁决。




# ── supervisor 节点：判路由，写 state.route + trace ──















# ── 专家节点：直接调底层服务（不复用 image_agent 闭包工具，零耦合）──





def _rewrite_for_compatibility(ctx: dict, prompt: str) -> str:
    """在用户授权后生成更兼容上游表达的候选稿；只改措辞，不改画面或视频细节。"""
    system = (
        "你是提示词措辞编辑。上游生成服务没有接受这段提示词。请在遵守上游规则的前提下，"
        "改写成更中性、专业、艺术化的表达。必须完整保留原提示词中的主体、数量、身份、外观、"
        "构图、动作、姿势、视角、场景、服装、材质、光照、色彩及其他可保留细节；"
        "不得擅自删除、弱化、替换、概括、增加或改变细节。只输出完整候选提示词，不要解释。"
    )
    out = _llm.chat(ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
                    system, prompt, temperature=0.3, **_proxy_kw(ctx))
    if not (out or "").strip():
        raise RuntimeError("提示词修饰模型未返回内容")
    return out.strip()
















# 固化02/03（卡/书/文档交付）任务的工具白名单：模型只能摸到这些能力，
# 物理上摸不到 shell/生图/LoRA/媒体——防自由循环跑偏（2026-09-06 驱动测试实锤：
# 模型用 project.run_shell 读小说、load 固化01 编排生图）。
# 2026-09-10 收口：清单单一属主迁到 capability_registry（FABRIC_DOC_DELIVERY_OPS /
# FABRIC_TOOLING_OPS / FABRIC_WEB_OPS），本文件只做装配。防跑偏语义保留：
#   - 默认不给 shell/写脚本，仅当用户在对话里显式要求「写脚本/跑命令/排错/调试/自动化」
#     时才按需解锁（plan_compiler.wants_tooling_intent），既满足「让 harness 自己生成
#     工具、自己排错」的诉求，又不重演跑偏实锤。
#   - 默认也不给联网，仅当用户显式要「联网/搜索/找参考图」时解锁
#     （plan_compiler.wants_web_material_intent）——多数交付任务只在本地资料上作业。












































































def _handle_pending_approval(context: RunContext) -> list[dict] | None:
    return generation_approval.handle_pending(context, _rewrite_for_compatibility)


# ── 组装 StateGraph：supervisor 判路由 → 条件边分派专家 → 专家 END（单专家直连不回交，省往返）──

def _build_graph():
    from langgraph.graph import StateGraph, END
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("generate", generate_node)
    g.add_node("video", video_node)
    g.add_node("img2img", img2img_node)
    g.add_node("analyze", analyze_node)
    g.add_node("inspire", inspire_node)
    g.add_node("tool_agent", tool_agent_node)
    g.add_node("edit", edit_node)
    g.add_node("answer", answer_node)
    g.add_node("roleplay", roleplay_node)
    g.add_node("clarify", clarify_node)
    g.add_node("plan", plan_compiler_node)
    g.set_entry_point("supervisor")
    # 条件边：按 supervisor 判出的 route 跳到对应专家
    g.add_conditional_edges("supervisor", lambda s: s.get("route", "answer"),
                            {"generate": "generate", "video": "video", "img2img": "img2img",
                             "analyze": "analyze", "inspire": "inspire",
                             "tool_agent": "tool_agent", "answer": "answer",
                             "edit": "edit", "roleplay": "roleplay", "clarify": "clarify", "plan": "plan"})
    # 单专家任务：干完直接 END，不回 supervisor 二次判断（慢中转下省一次往返）
    for n in ("generate", "video", "img2img", "analyze", "inspire", "tool_agent", "edit", "answer", "roleplay", "clarify", "plan"):
        g.add_edge(n, END)
    return g.compile()


_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def stream_multi_agent(context: RunContext) -> Iterator[dict]:
    """运行 supervisor 多 Agent 图；HTTP/SSE wire 由 runner/router 适配。"""
    context.agent_cfg = _resolve_agent_cfg(context.agent_id)
    context.builtin = builtin_agents.resolved()  # ③ 内置 Agent 生效参数（默认+用户覆盖），供各节点取
    context.has_mcp = _has_mcp(context.agent_cfg)
    # 2026-09-11 上下文 token 化：有效预算 = min(设置上限, 模型窗口−输出预留)，
    # 条数约束废弃（剧情模式历史走 last_story_round，不受影响）。达到触发
    # 百分比自动压缩（LLM 摘要旧段，失败退机械保头保尾），对齐市面 /compact。
    _hist_budget = agent_context.resolve_history_budget(
        context.context_max_tokens, context.preset_sampling)
    context.history = agent_context.recent_history(
        context.thread_id,
        max_tokens=_hist_budget,
        per_role=0,
        history_override=context.history_override,
        compact_trigger_pct=context.compact_trigger_pct,
        summarize_fn=_history_summarizer(context),
    )
    context.skill_frags = _resolve_skills(context.agent_cfg)
    context.persona = ""
    _apply_work_persona(context)  # 作品绑定人设快照优先，回退前端透传
    run_trace.emit(context, "turn.context_ready", history=context.history,
                   history_count=len(context.history), card_name=context.card_name,
                   card_names=context.card_names,
                   preset_name=context.preset_name, has_mcp=context.has_mcp)
    pending_events = _handle_pending_approval(context)
    if pending_events is not None:
        for event in pending_events:
            yield event
        yield {"done": True}
        return
    ctx = context
    message = context.message
    images = context.input_images()
    # 对话附件（file_id 元信息）→「文件参考」段落，追加进本轮 user_text：
    # 文本类全文（100k 封顶）、docx/xlsx 零依赖提取、PDF 可选提取（失败降级）、二进制只元信息。
    # 追加在用户文本之后、消息 content 之前，下游所有节点（supervisor/专家）天然可见。
    attachment_blocks = attachment_store.file_reference_blocks(context.attachments)
    if attachment_blocks:
        message = message + "\n\n" + "\n\n".join(attachment_blocks)
    # 2026-09-09 治本（实锤：用户上传 ST 卡 PNG 后模型说「未给出附件 path」→ 排不出
    # import_source/migrate_scan 计划，只编译出空壳）：随文图片的 URL 多是
    # comfyui local-view?path=<本地绝对路径>，把本地路径显式写进上下文，模型直接用它
    # 调 file.read_text / character.import_source / character.migrate_scan，不再向用户索要路径。
    try:
        import os as _os
        import re as _re_img
        import urllib.parse as _up
        _img_paths: list[str] = []
        for _u in (images or []):
            _m = _re_img.search(r"[?&]path=([^&]+)", str(_u))
            if not _m:
                continue
            _p = _up.unquote(_m.group(1))
            if _os.path.isfile(_p) and _p not in _img_paths:
                _img_paths.append(_p)
        if _img_paths:
            message = message + (
                "\n\n【附图本地路径】" + "、".join(_img_paths)
                + "\n（这是随文附图的真实本地文件路径——需要读取/迁移/转写该文件时"
                  "直接用它调用 file.read_text / character.import_source / character.migrate_scan，"
                  "禁止向用户索要路径。）")
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 路径提取失败不影响主流程，但附图本地路径缺失
        _LOG.warning("附图本地路径提取失败（主流程继续，但用户无法直接引用该文件）: %s", exc)
    from langchain_core.messages import HumanMessage
    content: list = [{"type": "text", "text": message}]
    for u in (images or []):
        content.append({"type": "image_url", "image_url": {"url": u}})
    init: AgentState = {
        "messages": [HumanMessage(content=content)], "user_text": message,
        "images": images or [], "attachments": context.attachments, "trace": [], "_ctx": ctx,
    }
    seen_trace = 0
    emitted_imgs: set = set()
    emitted_cards: set = set()
    final_text: list[str] = []
    interrupted = False
    try:
        for chunk in _graph().stream(init, {"configurable": {"thread_id": context.thread_id}}):
            # 协作式取消：节点间检查（LangGraph 不支持节点内打断，故粒度到节点边界）
            if context.cancel_event.is_set():
                interrupted = True
                yield {"interrupted": True}
                break
            for _node, upd in chunk.items():
                if not isinstance(upd, dict):
                    continue
                run_trace.emit(context, "agent.node_completed", agent=_node,
                               output_keys=sorted(str(k) for k in upd.keys() if k != "_ctx"),
                               result_text=upd.get("result_text") or "")
                if upd.get("route"):
                    yield {"route": upd["route"]}
                if upd.get("_interrupted"):
                    interrupted = True  # noqa: F841  语义标记，保留可读性
                    yield {"interrupted": True}
                streamed_result = bool(upd.get("_streamed_result"))
                eager_result = bool(upd.get("_eager_result"))
                if not streamed_result:
                    for line in (upd.get("trace") or [])[seen_trace:]:
                        yield {"trace": line}
                seen_trace = len(upd.get("trace") or []) if upd.get("trace") else seen_trace
                for rec in [] if eager_result else (upd.get("image_recs") or []):
                    if rec.get("id") not in emitted_imgs:
                        emitted_imgs.add(rec.get("id"))
                        yield {"image": rec.get("url"), "id": rec.get("id"),
                               "regeneration": rec.get("regeneration")}
                for rec in upd.get("video_recs") or []:
                    if rec.get("id") not in emitted_imgs:
                        emitted_imgs.add(rec.get("id"))
                        yield {"video": rec.get("url"), "id": rec.get("id")}
                illustrate_recs = [] if eager_result else [
                    rec for rec in (upd.get("illustrate_recs") or [])
                    if rec.get("id") not in emitted_imgs
                ]
                for rec in illustrate_recs:
                    emitted_imgs.add(rec.get("id"))
                # 音频对白配音：独立于插画锚点（配音覆盖整段楼层，不插回正文）。
                for rec in [] if eager_result else (upd.get("audio_recs") or []):
                    if rec.get("id") in emitted_imgs:
                        continue
                    emitted_imgs.add(rec.get("id"))
                    yield {"audio_request": {"lines": rec.get("lines") or []},
                           "id": rec.get("id")}
                for rec in upd.get("rag_recs") or []:
                    if rec.get("id") not in emitted_cards:
                        emitted_cards.add(rec.get("id"))
                        yield {"rag_status": {"state": rec.get("state") or "",
                                              "kind": rec.get("kind") or "",
                                              "count": rec.get("count")}}
                for card in upd.get("insp_cards") or []:
                    cid = card.get("id")
                    if cid not in emitted_cards:
                        emitted_cards.add(cid)
                        yield {"insp": card}
                if upd.get("approval"):
                    yield {"approval": upd["approval"]}
                if upd.get("route_choice"):
                    yield {"route_choice": upd["route_choice"]}
                if upd.get("result_text"):
                    final_text.append(upd["result_text"])
                    if upd.get("artifacts"):
                        yield {"artifacts": upd["artifacts"]}
                    if eager_result:
                        continue
                    events = (
                        [{"replace": upd["result_text"]}, *_streamed_illustration_events(illustrate_recs)]
                        if streamed_result
                        else _ordered_illustration_events(upd["result_text"], illustrate_recs)
                    )
                    for event in events:
                        yield event
                elif illustrate_recs:
                    for event in _ordered_illustration_events("", illustrate_recs):
                        yield event
    except Exception as e:  # noqa: BLE001
        yield {"error": str(e)}
    yield {"done": True}
