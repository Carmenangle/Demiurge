"""Supervisor 多 Agent 系统（LangGraph 手写 StateGraph）。

范式：无卡或带附件请求由 supervisor 判用户意图；有卡纯文本直达 Roleplay，明确强执行命令
用零 LLM 规则分派。专家执行完把结果写回 state；遗留 ReAct 大脑只作为工具专家 Adapter。

分派原则：Supervisor 处理模糊/多能力请求；角色卡纯文本避免重复上传历史。
Supervisor 可使用独立快模型，专家使用主模型；单专家任务直连 END，不做二次判断。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator, TypedDict
from app.services import agent_context, agent_plugins as _agent_plugins, attachment_store, builtin_agents, edit_agent, generation_approval, generation_store, plan_compiler, prompt_compiler, roleplay_turn, run_trace, scene_classify, story_history, structured_output, tool_agent_adapter
from app.services.structured_contracts import SupervisorDecision
from app.services import llm as _llm
from app.services.agent_contracts import RunContext
from app.config import COMFYUI_BASE_URL

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
    _resume_interrupted_messages,
    _roleplay_continuation_messages,
    _chat_with_optional_stream,
    _ordered_illustration_events,
)

# 2026-09-01 用户定案：首发**不设总时长上限**——只要还在持续吐 token 就不允许截断；
# 只有 30s 无 token 的流式读超时（llm.build_model streaming）才允许中断。

# 探测日志：会话输入 / AI 思考(<think>) / RAG 召回，输出到 uvicorn 控制台（仅开发可见，不推前端）。
_probe = logging.getLogger("uvicorn.error")

# T3.3（2026-09-17）：业务数据路径兜底降级留痕（探针日志见上，用途不同）。
_LOG = logging.getLogger("uvicorn.error")


def _probe_think(reply: str) -> str:
    """从回复里抽 <think>…</think> 思考段（GrayWill 等预设的 CoT）。无则空串。"""
    m = re.search(r"<think>([\s\S]*?)</think>", reply or "", re.IGNORECASE)
    return m.group(1).strip() if m else ""


class AgentState(TypedDict, total=False):
    """图的共享状态。messages 累积对话；route 是 supervisor 判出的下一站；产出写各字段。"""
    messages: list                 # 对话消息（含用户输入、图片）
    route: str                     # supervisor 分派结果：各专家/answer/clarify
    user_text: str                 # 本轮用户文本
    images: list                   # 本轮上传图片 url
    attachments: list              # 本轮对话附件元信息 [{file_id,name,mime,size}]（已并入 user_text 参考段）
    result_text: str               # 专家产出的文本回复
    artifacts: list                # 2026-09-08 产物卡：fabric 终局下发的交付物元数据列表
                                   # （必须声明，否则 LangGraph TypedDict schema 静默丢弃该键）
    image_recs: list               # 生图产出 [{id,url}]
    video_recs: list               # 生视频产出 [{id,url}]
    insp_cards: list               # 灵感卡
    approval: dict                 # 结构化提示词审批卡
    route_choice: dict             # Supervisor 低置信时的最小候选选择卡
    trace: list                    # 节点流转轨迹（供 SSE 透出多 agent 协作过程）
    _interrupted: bool
    _streamed_result: bool         # 节点正文已实时发送；完成时只发最终替换，不重复 delta
    # 下方是执行上下文（构图时注入，专家节点用）
    _ctx: RunContext


# ── 路由：Supervisor 模型负责语义，代码只校验能力条件 ──


# 内置 Agent 默认提示词由 builtin_agents 单一属主（③ 可被用户覆盖）；别名保留旧引用不破坏。
_SUPERVISOR_SYSTEM = builtin_agents.SUPERVISOR_SYSTEM


def _roleplay_sampling(ctx: dict) -> dict:
    """正文额度优先取「智能体参数预设」（settings 全局，2026-09-04 设置整合后权威来源）；
    preset 顶层不再管理采样/输入参数。分析/状态/骰点/插画均在正文额度之外。

    preset_sampling 来自 ctx.preset_sampling（前端 settings payload 注入），逐字段覆盖
    builtin sampling；缺省字段保留 builtin 默认（向后兼容老 settings）。
    """
    sampling = _builtin_sampling(ctx, "roleplay")
    # 2026-09-04：ctx.preset_sampling 取代 ctx["_preset_sampling"]（旧字段来自 preset_store.sampling_params，
    # 已废弃）。settings 缺字段时只覆盖提供的键，未提供的仍用 builtin default。
    preset_sampling = ctx.get("preset_sampling") if isinstance(ctx.get("preset_sampling"), dict) else {}
    for key in ("temperature", "top_p", "top_k", "frequency_penalty", "presence_penalty"):
        v = preset_sampling.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            sampling[key] = float(v) if key != "top_k" else int(v)
    # 兼容 preset 标准字段名（openai_max_tokens=settings 权威 / max_tokens=旧 sampling_params 简写）。
    preset_max = None
    for key in ("openai_max_tokens", "max_tokens"):
        v = preset_sampling.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            preset_max = v
            break
    if preset_max is not None:
        sampling["max_tokens"] = preset_max
    if "max_tokens" in sampling:
        # GrayWill 的 think、状态和骰点先于正文输出，必须在正文上限之外独立预留。
        sampling["max_tokens"] += 4000
    # 同轮成稿已剥离（上下文合同）：不再为内联 profile_prompt 预留输出预算，
    # 画像成稿改由插画链独立调用编译，正文额度全额归正文。
    if "max_tokens" not in sampling:
        # 2026-08-29 验收「正文内容极其少」实锤：未配置 max_tokens 时依赖供应商默认
        # （常见仅 4k-8k），长思考模型在 <think> 阶段就烧光额度，正文 0 字截断。
        # 正文输出额度按用户设置「历史上下文上限 tokens」走（user 指定语义）：
        #   >0 → 直接作为正文 max_tokens；0=无限 → 不传（额度交给供应商）；
        #   字段缺失（防御）→ 32768 安全上限。不加 +4000 预留——用户设多少就是多少。
        _ctx_limit = ctx.get("context_max_tokens")
        if isinstance(_ctx_limit, int) and not isinstance(_ctx_limit, bool) and _ctx_limit > 0:
            sampling["max_tokens"] = _ctx_limit
        elif _ctx_limit == 0:
            pass
        else:
            sampling["max_tokens"] = 32768
    # 2026-09-04 模型输出上限收敛：GrayWill 预设 openai_max_tokens=600000 → +4000=604000
    # 直发被网关 400 拒（deepseek-v4-flash 上限 393216，trace 实锤）。用户定案：min 语义——
    # 请求上限 > 模型上限按模型来；未登记模型不钳。此处收敛让 trace 与 wire 一致，
    # llm.transport 层仍有同款兜底（幂等）。
    if "max_tokens" in sampling:
        sampling["max_tokens"] = _llm.cap_max_tokens(
            str(ctx.get("chat_model") or ""), sampling["max_tokens"])
    return sampling


# 固化知识库（DATA_DIR/agent_knowledge/*.md）：流程规范/映射表。
# 目录与上限常量、列举/读取统一走 agent_knowledge 服务（唯一属主）。
# 2026-09-04：带 frontmatter「skill」的文档 = 固化技能（按触发装载，不常驻）；
# 无头的普通知识文档仍全量常驻注入。目录注入文本是本策略的唯一裁决。


def _supervisor_route(text: str, image_count: int, ctx: dict) -> tuple[str, bool, list[str], str]:
    """每个普通用户轮次都由模型做唯一语义判断；代码只提供并复核能力清单。
    返回 (route, confident, alternatives, scene)。scene 复用同一次调用产出，零额外往返。"""
    chat_fn = ctx.get("chat_fn") or _llm.chat
    try:
        model = ctx.get("route_model") or ctx["chat_model"]
        has_images = image_count > 0
        available = _available_routes(has_images, ctx)
        route_lines = "\n".join(
            f"- {route}：{_ROUTE_DESCRIPTIONS[route]}" for route in available
        )
        sup_system = _builtin(ctx, "supervisor", "systemPrompt", builtin_agents.SUPERVISOR_SYSTEM)
        sup_temp = _builtin(ctx, "supervisor", "temperature", builtin_agents.SUPERVISOR_TEMPERATURE)
        system = sup_system + "\n【本轮可用路由】\n" + route_lines
        user = (
            agent_context.history_text(ctx)
            + f"附件数量：{image_count}\n本轮用户：{text}"
        )
        run_trace.emit(ctx, "model.request", agent="supervisor", model=model,
                       messages=[{"role": "system", "content": system},
                                 {"role": "user", "content": user}])
        call_args = (
            ctx["chat_base"], ctx["chat_key"], model, system, user,
        )
        call_kwargs = {
            "temperature": sup_temp,
            **_proxy_kw(ctx),
            **_builtin_sampling(ctx, "supervisor"),
        }
        structured_fn = ctx.get("structured_chat_fn")
        result = structured_output.invoke(
            SupervisorDecision,
            native=(lambda: structured_fn(*call_args, schema=SupervisorDecision, **call_kwargs))
            if callable(structured_fn) else None,
            legacy=lambda: chat_fn(*call_args, **call_kwargs),
            trace=lambda event, **data: run_trace.emit(ctx, event, agent="supervisor", **data),
        )
        raw = result.raw.strip() if result.raw else result.value.model_dump_json()
        run_trace.emit(ctx, "model.response", agent="supervisor", content=raw)
        try:
            payload = result.value
            route = payload.route.strip().lower()
            confidence = payload.confidence.strip().lower()
            alternatives = [str(item).strip().lower() for item in payload.alternatives]
            scene = scene_classify.normalize_scene(payload.scene)
            if route in available:
                return route, confidence != "low", alternatives, scene
        except (TypeError, ValueError):
            pass
        r = raw.lower().strip("`'\".,:;，。")
        if r in available:
            return r, True, [], ""
    except Exception as exc:
        run_trace.emit(ctx, "agent.error", agent="supervisor", error=str(exc))
    return "answer", True, [], ""


# ── supervisor 节点：判路由，写 state.route + trace ──

# route → 对应工具开关键（自定义预设可关掉某能力，关掉则回退 answer）
# Agent 插件注册表（P6）：路由标签/描述/工具开关从 agent_plugins 生成，不再硬编码。
_ROUTE_TOOL = {p.route: p.tool_key for p in _agent_plugins.all_plugins() if p.tool_key}
_ROUTE_LABELS = {p.route: p.label for p in _agent_plugins.all_plugins()}
_ROUTE_DESCRIPTIONS = {p.route: p.description for p in _agent_plugins.all_plugins()}


def _explicit_card_route(text: str, ctx: dict) -> str:
    """角色卡纯文本中的强执行命令走零 LLM 分派；模糊表达仍按剧情处理。"""
    source = (text or "").strip().lower()
    if any(mark in source for mark in ("为什么", "失败", "问题", "检查", "审查", "分析", "？", "?")):
        return ""
    source = re.sub(r"^(?:请帮我|麻烦你|帮我|麻烦|请)\s*", "", source)
    patterns = (
        ("generate", ("画一张", "生成图片", "生成一张图", "出一张图", "出图")),
        ("video", ("生成视频", "做成视频", "做一个视频", "制作视频")),
        ("inspire", ("找灵感", "搜索参考", "联网查找", "查找灵感")),
    )
    for route, words in patterns:
        if any(source.startswith(word) for word in words) and _route_available(route, False, ctx):
            return route
    return ""


def _route_available(route: str, has_images: bool, ctx: dict) -> bool:
    return _agent_plugins.route_available(
        route, has_images=has_images, has_card=_has_card(ctx),
        has_mcp=bool(ctx.get("has_mcp")), agent_cfg=ctx.get("agent_cfg"),
        tool_on=_tool_on)


def _available_routes(has_images: bool, ctx: dict) -> list[str]:
    return [route for route in _ROUTE_LABELS if _route_available(route, has_images, ctx)]


def _route_choice_options(
    route: str, alternatives: list[str], has_images: bool, ctx: dict,
) -> list[dict]:
    routes = []
    for candidate in [route, *alternatives]:
        if candidate not in routes and _route_available(candidate, has_images, ctx):
            routes.append(candidate)
        if len(routes) == 3:
            break
    return [
        {"route": route, "label": _ROUTE_LABELS[route]}
        for route in routes
    ]


def _route_choice_payload(ctx: dict, options: list[dict]) -> dict:
    message_id = str(ctx.get("message_id") or "")
    return {
        "id": f"route-choice-{message_id}" if message_id else "route-choice",
        "messageId": message_id,
        "userMessageId": str(ctx.get("user_message_id") or ""),
        "status": "pending",
        "options": options,
    }


def supervisor_node(state: AgentState) -> dict:
    ctx = state.get("_ctx", {})
    run_trace.emit(ctx, "agent.started", agent="supervisor")
    text = state.get("user_text", "")
    has_images = bool(state.get("images"))
    # 委派强命令只判原始用户指令：附件参考块（file_reference_blocks，文本类可长达
    # 100k 字）已追加进 user_text，小说正文必然命中疑问词（为什么/？…）导致
    # 一票否决误杀「制作合集卡」类强命令（2026-09-05 实锤）。ctx.message 是剥离
    # 附件后的本轮指令原文，素材内容不参与疑问/规模词判定。
    intent_text = str(ctx.get("message") or text or "")
    # 事前固化询问的答复直通车（2026-09-10）：待确认询问存在时，「要 / 直接跑」这类短词
    # **必须**回到 plan 节点去消费——否则会被 LLM 主管判成普通对话（answer/roleplay），
    # 询问永远悬空、用户只能重发整条原指令。与审批词同理，这是零 LLM 的强制路由。
    # （审批按钮发的也是普通聊天消息，见 ChatView.tsx 的 onFabricApprove，同一类问题。）
    # 放在 edit 模式分支之前：edit 模式也能经文档交付强命令进 plan 节点，同样要能答复。
    if _RECIPE_OFFER_RE.match(intent_text or "") and _recipe_offer_pending(
            ctx, str(ctx.get("output_dir") or "")):
        run_trace.emit(ctx, "agent.completed", agent="supervisor", route="plan",
                       forced=True, recipe_offer=True)
        trace = state.get("trace", []) + ["🧭 主管分派 → 智能编造计划（固化询问答复）"]
        return {"route": "plan", "trace": trace}
    if ctx.get("workspace_mode") == "edit":
        # 老项目遗留边界修复（2026-09-05）：编辑模式原强制直达受限编辑 Agent，
        # 导致「制作合集卡/角色卡/世界书」等委派强命令被只读拦截。制作类强命令
        # 优先走智能编造（plan，固化02/03 知识驱动），其余请求仍走受限编辑 Agent
        # （文件排错用途）。
        if _route_available("plan", has_images, ctx) and (
                (not has_images and plan_compiler.is_delegation_intent(intent_text))
                or plan_compiler.is_doc_delegation_intent(intent_text)):
            route = "plan"
            ctx["scene"] = scene_classify.infer_scene(text)
            run_trace.emit(ctx, "agent.completed", agent="supervisor", route="plan",
                           forced=False, scene=ctx.get("scene") or "", edit_override=True)
            trace = state.get("trace", []) + ["🧭 主管分派 → 智能编造计划（编辑模式放行委派强命令）"]
            return {"route": "plan", "trace": trace}
        run_trace.emit(ctx, "agent.completed", agent="supervisor", route="edit", forced=True)
        return {"route": "edit", "trace": state.get("trace", []) + ["📝 进入编辑模式"]}
    # 对话兜底：关联角色卡的作品默认走剧情扮演，否则通用对话。
    chat_default = "roleplay" if _has_card(ctx) else "answer"
    forced_route = str(ctx.get("forced_route") or "").strip().lower()
    if forced_route:
        route = forced_route if _route_available(forced_route, has_images, ctx) else chat_default
    elif _route_available("plan", has_images, ctx) and (
            (not has_images and plan_compiler.is_delegation_intent(intent_text))
            or plan_compiler.is_doc_delegation_intent(intent_text)
            or plan_compiler.is_design_discussion_intent(intent_text)
            or _task_session_continuation(ctx, intent_text)
            or plan_compiler.is_design_discussion_continuation(
                intent_text, _recent_user_texts(ctx))):
        # 路由界限·委派强命令层：高置信规模词+资产动作 / 显式计划语言 / 文档交付
        # 强命令 → 委派（零 LLM）。文档交付允许带图附件：参考图在此是素材
        # （看图反推→生成套装文档），不是图生图/反推的目标。
        # 2026-09-11 加设计讨论型：绑卡作品里「参考文档讨论各服装的生图提示词」
        # 此前掉进 chat_default=roleplay → 剧情楼层（实锤）；讨论的正道是智能编造
        # 自由循环。误判方向：纯剧情讨论不带设计话题词，不命中。
        # 2026-09-11 晚加讨论粘性：接续轮（「不用ComfyUI的提示词,我要适配
        # gpt/banana 的文字描述」）无任何讨论动词 → 三个判定全不命中 → 掉回
        # roleplay、灵感卡/规范/附件注入整段失效（隔一轮就失忆）。近 3 条用户
        # 消息里有设计讨论 + 本轮带设计话题词 + 非其他明确意图 → 仍走讨论循环。
        route = "plan"
        ctx["scene"] = scene_classify.infer_scene(text)
        run_trace.emit(ctx, "agent.completed", agent="supervisor", route="plan",
                       forced=False, scene=ctx.get("scene") or "")
        trace = state.get("trace", []) + ["🧭 主管分派 → 智能编造计划"]
        return {"route": "plan", "trace": trace}
    elif _has_card(ctx) and not has_images:
        # 作品剧情纯文本最终本就会并入 roleplay；无需先把历史再提交给 Supervisor。
        # 图片附件仍交 Supervisor，避免把图生图/反推误判为剧情。
        route = _explicit_card_route(text, ctx) or "roleplay"
        ctx["scene"] = scene_classify.infer_scene(text)
    else:
        route, confident, alternatives, scene = _supervisor_route(
            text, len(state.get("images") or []), ctx)
        # 场景标签写回 ctx（roleplay_node 从 ctx 读，驱动条件选链/配图）；空则不写，保持缺省
        if scene:
            ctx["scene"] = scene
        if not confident:
            options = _route_choice_options(route, alternatives, has_images, ctx)
            if len(options) >= 2:
                run_trace.emit(ctx, "agent.completed", agent="supervisor", route="clarify",
                               confidence="low", alternatives=options, scene=scene)
                trace = state.get("trace", []) + ["🧭 主管无法确定分派，等待用户选择"]
                return {
                    "route": "clarify",
                    "route_choice": _route_choice_payload(ctx, options),
                    "trace": trace,
                }
            route = chat_default
    if not _route_available(route, has_images, ctx):
        route = chat_default
    # 有卡作品里的通用对话统一并入剧情扮演（保持人设不掉线）；非扮演路由（生图等）不受影响。
    if route == "answer" and _has_card(ctx):
        route = "roleplay"
    label = {"generate": "生图专家", "img2img": "图生图专家", "analyze": "反推专家",
             "inspire": "灵感专家", "tool_agent": "工具专家", "video": "视频专家",
             "roleplay": "剧情扮演", "answer": "对话", "plan": "智能编造计划"}.get(route, route)
    run_trace.emit(ctx, "agent.completed", agent="supervisor", route=route,
                   forced=bool(forced_route), scene=ctx.get("scene") or "")
    trace = state.get("trace", []) + [f"🧭 主管分派 → {label}"]
    return {"route": route, "trace": trace}


# ── 专家节点：直接调底层服务（不复用 image_agent 闭包工具，零耦合）──

def _gen_ctx(ctx: dict):
    return (ctx["gen_base"], ctx["gen_key"], ctx["gen_model"], ctx["thread_id"],
            ctx["repo_id"], ctx["output_dir"], ctx["embed_base"], ctx["embed_key"], ctx["embed_model"])


def _styled_prompt(ctx: dict, prompt: str) -> str:
    """按风格模板的结构组织提示词；模板是结构参考，原提示词细节必须完整保留。"""
    tpl = (ctx.get("style_template") or "").strip()
    if not tpl:
        return prompt
    try:
        from app.services.image_prompt_style import guidance_for
        system = (
            "你是提示词结构整理助手。下面的风格模板只用于参考组织结构、语序和表达形式。"
            "必须逐项保留原提示词中的全部主体、数量、身份、外观、构图、动作、姿势、视角、"
            "场景、服装、材质、光照、色彩及其他细节；不得删除、弱化、替换、概括、增加或改变任何细节。\n"
            "不得改变原提示词表达的画面事实。\n"
            + guidance_for("", ctx.get("gen_model", ""), tpl)
            + "\n只输出整理后的完整提示词本身，不要解释、不要引号。"
        )
        out = _llm.chat(ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
                        system, prompt, temperature=0.5, **_proxy_kw(ctx))
        return out.strip() or prompt
    except Exception:  # noqa: BLE001
        return prompt


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


def generate_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="generate")
    original = state.get("user_text", "")
    execution_prompt = agent_context.standalone_execution_prompt(ctx, original)
    trace = state.get("trace", []) + ["🎨 生图专家执行中…"]
    if (ctx.get("style_template") or "").strip():
        candidate = _styled_prompt(ctx, execution_prompt)
        result = generation_approval.save_prompt_review(ctx, "image", original, candidate, [], "style")
        result["trace"] = trace + result["trace"]
        return result
    return generation_approval.execute_generation(ctx, "image", original, execution_prompt, [], trace)


def video_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="video")
    original = state.get("user_text", "")
    execution_prompt = agent_context.standalone_execution_prompt(ctx, original)
    imgs = state.get("images", [])  # V1.4：用户消息带图 → 首帧图生视频
    trace = state.get("trace", []) + ["🎬 视频专家执行中…"]
    if (ctx.get("style_template") or "").strip():
        candidate = _styled_prompt(ctx, execution_prompt)
        result = generation_approval.save_prompt_review(ctx, "video", original, candidate, imgs, "style")
        result["trace"] = trace + result["trace"]
        return result
    return generation_approval.execute_generation(ctx, "video", original, execution_prompt, imgs, trace)


def img2img_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="img2img")
    original = state.get("user_text", "")
    execution_prompt = agent_context.standalone_execution_prompt(ctx, original)
    imgs = state.get("images", [])
    trace = state.get("trace", []) + ["🖼️ 图生图专家执行中…"]
    if not imgs:
        return {"result_text": "未找到参考图，无法图生图。", "trace": trace}
    if (ctx.get("style_template") or "").strip():
        candidate = _styled_prompt(ctx, execution_prompt)
        result = generation_approval.save_prompt_review(
            ctx, "img2img", original, candidate, imgs, "style", ctx.get("image_mask"),
        )
        result["trace"] = trace + result["trace"]
        return result
    return generation_approval.execute_generation(
        ctx, "img2img", original, execution_prompt, imgs, trace,
        image_mask=ctx.get("image_mask"),
    )


def analyze_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="analyze")
    imgs = state.get("images", [])
    trace = state.get("trace", []) + ["🔍 反推专家执行中…"]
    if not imgs:
        return {"result_text": "请先上传要反推的图片。", "trace": trace}
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        model = _llm.build_model(ctx["chat_base"], ctx["chat_key"], ctx["chat_model"])
        # 选了自定义风格存档时附加写法指引（反推能力现仅存于此 supervisor 路由）
        style_hint = ""
        if (ctx.get("style_template") or "").strip():
            try:
                from app.services.image_prompt_style import guidance_for
                style_hint = "\n" + guidance_for("", ctx.get("gen_model", ""), ctx["style_template"])
            except Exception as exc:  # noqa: BLE001 [EXC-留痕] 风格指引缺失不阻断，但提示词弱化
                _LOG.warning("自定义风格写法指引附加失败（本次分析提示词缺该段）: %s", exc)
        resp = model.invoke([
            SystemMessage(content="如实完整描述这张图用于再次生成：主体/人物/服饰/动作/背景/光影/构图/画风/画质。"
                          + style_hint + "\n只输出提示词本身。"),
            HumanMessage(content=[{"type": "image_url", "image_url": {"url": imgs[0]}}]),
        ])
        return {"result_text": _llm.flatten_content(resp.content) or "反推无结果。", "trace": trace}
    except Exception as e:  # noqa: BLE001
        return {"result_text": f"反推失败：{e}", "trace": trace}


def inspire_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="inspire")
    query = state.get("user_text", "")
    trace = state.get("trace", []) + ["💡 灵感专家执行中…"]
    try:
        from app.services import inspiration as _insp
        data = _insp.search_and_refine(
            query, ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            proxy=ctx.get("proxy", ""), chat_proxy=ctx.get("chat_proxy", ""),
            search_provider=ctx.get("search_provider") or None,
        )
        if not data.get("content"):
            return {"result_text": "未能从搜索结果整理出内容。", "trace": trace}
        card = generation_store.persist_inspiration(ctx["thread_id"], data["title"], data["content"], data["sources"], data.get("images"))
        return {"result_text": f"已生成灵感卡「{data['title']}」：{data['content'][:80]}…", "insp_cards": [card], "trace": trace}
    except Exception as e:  # noqa: BLE001
        return {"result_text": f"找灵感失败：{e}", "trace": trace}


def tool_agent_node(state: AgentState) -> dict:
    """通用工具专家：直接跑单 agent 的完整 ReAct 大脑(内置生图/反推/灵感 + MCP 工具 + 自主串联)。
    吸收单 agent 唯一独占的 MCP 能力，是淘汰单 agent 的承接节点。走 image_agent.stream_agent，
    其 checkpointer 已自动记本轮对话进 chat_memory → 本节点被走时置 _used_tool_agent，末尾跳过 _persist_turn 防双写。"""
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="tool_agent")
    text = state.get("user_text", "")
    imgs = state.get("images", [])
    trace = state.get("trace", []) + ["🛠️ 工具专家执行中…"]
    return tool_agent_adapter.run(ctx, text, imgs, trace)


def edit_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    text = state.get("user_text", "")
    images = state.get("images", [])
    trace = state.get("trace", []) + ["📝 编辑 Agent 执行中…"]
    return edit_agent.run(ctx, text, images, trace)


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


def _pc_configured_models(ctx: dict) -> set:
    """已配置模型集合：chat 恒在，image/video/embed 按 base_url 真值判定。

    逐字自 `plan_compiler_node` 搬移（判定逻辑与字面值零改动）。
    """
    return {
        key for key, flag in (("chat", True), ("image", ctx.get("gen_base")),
                              ("video", ctx.get("vid_base")), ("embed", ctx.get("embed_base")))
        if flag
    }


def _pc_approval_word(text: str) -> str | None:
    """审批词直通车判定：整条消息恰为审批/取消词之一时返回归一化小写词，否则 None。

    逐字自 `plan_compiler_node` 搬移（含 `flags=re.IGNORECASE` 与 `group(1).lower()`）。
    """
    m = re.match(r"^\s*(批准|同意|执行|确认|approve|ok|好|取消|cancel|撤销)\s*$",
                 text, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower()


def _pc_judge_text(intent_text: str, history) -> str:
    """判定用文本：本轮意图 + 历史用户消息（**逐条前插**）。

    短指令（「继续补写」）零 LLM 判定会丢语义，故不命中 doc 委派时把历史用户消息
    前插再判一次。⚠ 是逐条前插（后出现的用户消息更靠前），不是追加——这是改造前
    既有语义，不得"顺手修正"。

    逐字自 `plan_compiler_node` 搬移（判定逻辑与字面值零改动）。
    """
    judge = intent_text
    if not plan_compiler.is_doc_delegation_intent(judge):
        for h in (history or []):
            if isinstance(h, dict) and h.get("role") == "user":
                judge = str(h.get("content") or "") + "\n" + judge
        judge = judge.strip()
    return judge


def _pc_collect_attachments(ctx: dict, text: str, attachments: list[dict]) -> None:
    """编译期预读：把编译上下文附件**原地追加**进 `attachments`。

    覆盖：消息/历史里显式写出的本地文本路径（容量封顶 3 条）、正文里的【文件参考】块
    全文（并按 name 解析真实落盘路径）、LoRA 目录、模板目录（含暴露字段清单）、固化
    流程清单、固化知识库。各子块自带 try/except 降级，单块不可用不影响其余块。

    ⚠ **原地改 `attachments` 而非返回新列表**：改造前它就是 `plan_compiler_node` 的局部
    变量，异常路径下「已收集到的部分附件」仍会被下游 `compile_plan` 使用——返回新列表
    会让调用方在异常时拿到空列表，属行为差异。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    _scan_text = text
    for _hitem in (ctx.get("history") or []):
        if isinstance(_hitem, dict) and _hitem.get("role") == "user":
            _scan_text += "\n" + str(_hitem.get("content") or "")
    for raw_path in set(re.findall(
            r"[A-Za-z]:\\[^\s<>|？?」』]*\.(?:md|txt|json|csv|log|ya?ml|xml|html)",
            _scan_text)):
        try:
            read = plan_compiler.read_user_file(raw_path)
        except Exception as exc:  # noqa: BLE001 - 预读失败如实留痕，不阻断编译
            run_trace.emit(ctx, "plan.attachments", status="error",
                           path=raw_path, error=str(exc))
            continue
        attachments.append({"name": raw_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1],
                            "text": read["text"]})
        run_trace.emit(ctx, "plan.attachments", status="ok", path=raw_path,
                       chars=len(read["text"]))
    if len(attachments) > 3:
        attachments[:] = attachments[:3]
    # 用户把文档作为附件发送时，正文里带【文件参考：name】…【文件参考结束：name】块；
    # 这些全文也必须进 attachments，fill_prompt_sections 才能机械回填 prompt_section 引用
    # （路径预读只覆盖「消息里写绝对路径」的情况）。
    # 同时按 name 匹配 ctx.attachments 的 file_id 取真实落盘路径（attachment_store.resolve），
    # 让模型在 plan 步骤里写真实路径而非编造（2026-09-05 实锤：编出 /Users/momo/... 越域路径）。
    _attach_by_name = {str(a.get("name") or ""): str(a.get("file_id") or "")
                       for a in (ctx.get("attachments") or []) if a.get("file_id")}
    for fm in re.finditer(r"【文件参考：([^】]+)】[^\n]*\n(.*?)\n【文件参考结束：\1】", text, re.S):
        _name = fm.group(1).strip()
        _item = {"name": _name, "text": fm.group(2)}
        _fid = _attach_by_name.get(_name)
        if _fid:
            try:
                from app.services import attachment_store as _attach_store
                _p = _attach_store.resolve(_fid)
                if _p is not None:
                    _item["path"] = str(_p)
                    run_trace.emit(ctx, "plan.attachment_path", name=_name, path=str(_p))
            except Exception as exc:  # noqa: BLE001 [EXC-留痕] 路径解析失败仍可编译，但附件无本地路径
                _LOG.warning("附件路径解析失败（仍可编译，但该附件无本地路径）: %s", exc)
        attachments.append(_item)
    try:
        from app.services import capability_handlers as _ch
        catalog = _ch.lora_list()
        if catalog.get("loras"):
            lines = "\n".join(
                f"- {item['file']}" + (f"（触发词:{'/'.join(item['triggers'])}，建议权重:{item['suggested_weight']}）"
                                       if item["triggers"] else "")
                for item in catalog["loras"])
            attachments.append({"name": plan_compiler.LORA_CATALOG_NAME, "text":
                                f"共 {catalog['count']} 个：\n{lines}\n"
                                "用户提到近似名称时优先用上面的真实文件名；"
                                "宽泛指向（如「用 krea2 的」）存在多个候选时，"
                                "在计划卡里列出候选让用户选择，禁止替用户猜。"})
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 目录不可用时跳过，但编译上下文缺 LoRA 清单
        _LOG.warning("LoRA 目录读取失败（编译上下文缺 LoRA 清单，近似名无法对号）: %s", exc)
    try:
        from app.services import template_store
        templates = template_store.list_templates()
        if templates:
            lines = "\n".join(
                f"- {t.get('id')} {t.get('name') or ''}".rstrip()
                for t in templates)
            # 通用机制：把每个模板的暴露字段清单注入编译上下文，模型按 field
            # 名自由往 variants 写值（width/height/seed/任意节点参数），执行层按
            # binding 注入——换任何模板都适用，不硬编码特定参数名。
            field_lines = []
            for t in templates:
                exposed = t.get("exposed") or []
                if not isinstance(exposed, list):
                    continue
                brief = "、".join(
                    f"{f.get('field')}" + (f"/{f.get('binding')}" if f.get('binding') else "")
                    for f in exposed[:24]
                    if isinstance(f, dict) and f.get('field'))
                if brief:
                    field_lines.append(f"- {t.get('id')} {t.get('name') or ''} 暴露字段：{brief}")
            catalog_text = f"共 {len(templates)} 个：\n{lines}\n"
            if field_lines:
                catalog_text += ("暴露字段（variants 里按 field 名写值；binding 标注语义落点——"
                                 "如 width 的 binding 是 latent_width 即潜在尺寸节点；用户提到 "
                                 "latent/像素尺寸时，把数值写进对应语义字段，未暴露的节点"
                                 "（放大链路等）不在可写范围也不应去猜）：\n") \
                                + "\n".join(field_lines) + "\n"
            catalog_text += "用户指定模板时优先用上面的真实 id（或完整 name）；" \
                            "禁止写 TO_BE_RESOLVED、{{...}} 或任何占位符。"
            attachments.append({"name": plan_compiler.TEMPLATE_CATALOG_NAME,
                                "text": catalog_text})
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 模板库不可用时跳过，但编译上下文缺模板清单
        _LOG.warning("模板库读取失败（编译上下文缺模板清单）: %s", exc)
    recipe_catalog = _recipe_catalog_text()
    if recipe_catalog:
        # 固化流程清单进编译上下文：意图高度一致时编译器可编排 plan.instantiate_recipe 整条重放
        attachments.append({"name": plan_compiler.RECIPE_CATALOG_NAME,
                            "text": recipe_catalog})
    knowledge = _knowledge_catalog_text()
    if knowledge:
        # 固化知识库进编译上下文：流程规范/映射表对智能编造永远在线
        attachments.append({"name": plan_compiler.KNOWLEDGE_CATALOG_NAME,
                            "text": knowledge})


def _pc_prev_batch_and_flow_hint(ctx: dict, text: str, compile_text: str,
                                 output_dir: str, attachments: list[dict]) -> dict | None:
    """批量延续注入：命中延续意图时把上一批计划变体与上次流程文档句柄**原地追加**。

    返回 `prev_batch`（未命中为 None）。同样原地改 `attachments`（理由见
    `_pc_collect_attachments`）。逐字自 `plan_compiler_node` 搬移。
    """
    prev_batch: dict | None = None
    try:
        prev_batch = plan_compiler.prev_batch_for(output_dir, compile_text)
        if prev_batch:
            attachments.append(plan_compiler.prev_batch_attachment(prev_batch))
            run_trace.emit(ctx, "plan.prev_batch", status="ok",
                           variants=len(prev_batch.get("variants") or []),
                           path=prev_batch.get("plan_path"))
    except Exception as exc:  # noqa: BLE001 - 延续注入失败不阻断编译
        run_trace.emit(ctx, "plan.prev_batch", status="error", error=str(exc))
        prev_batch = None
    flow_hint = _flow_doc_hint(ctx, text)
    if flow_hint:
        # current_flow_doc（固化链 §3 设计 A）：延续语命中时把上次流程文档句柄
        # 送进编译上下文，模型先读文档再编排，避免「接着做」断指代
        attachments.append({"name": "doc.flow_resume", "text": flow_hint})
    return prev_batch


def _pc_compile_and_finalize(ctx: dict, state: AgentState, *, text: str,
                             intent_text: str, judge_text: str, trace: list,
                             output_dir: str, configured: set,
                             attachments: list[dict], compile_text: str,
                             prev_batch: dict | None) -> dict:
    """编译机械计划 → 校验兜底 → 回填段落引用 → 落盘 → 投递执行队列 → 渲染计划卡。

    ⚠ 与改造前逐字同形，含一处「既有形态」不得顺手修：投递 `submit_task` 抛
    `ValueError` 时只改 `queue_note`，`submitted` 仍**未绑定** ⇒ 末行
    `submitted.get(...)` 会抛 NameError（改造前既有行为，本步不修、不改语义）。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    try:
        outcome = plan_compiler.compile_plan(
            intent=compile_text, history=agent_context.history_text(ctx),
            attachments=attachments,
            repo_id=str(ctx.get("repo_id") or ctx.get("thread_id") or ""),
            comfyui_url=COMFYUI_BASE_URL,
            output_dir=output_dir, configured_models=configured,
            chat_base=ctx["chat_base"], chat_key=ctx["chat_key"],
            chat_model=ctx.get("route_model") or ctx["chat_model"],
            chat_fn=_live_chat(ctx),
            structured_chat_fn=ctx.get("structured_chat_fn"),
            proxy_kwargs=_proxy_kw(ctx),
            prev_batch=prev_batch,
            trace=_live_trace(ctx, "plan_compiler"),
        )
    except Exception as exc:  # noqa: BLE001 - 编译异常如实回复，不编造计划
        run_trace.emit(ctx, "agent.error", agent="plan_compiler", error=str(exc))
        return {"result_text": f"计划编译失败：{exc}", "trace": trace}
    if outcome.plan is None:
        if any("没有任何 submit/collect" in e for e in outcome.errors):
            # 2026-09-11 智能兜底：讨论/咨询型请求（「跟我讨论各服装的生图提示词」）
            # 会被规模词+生成词误带进机械计划——讨论本就不该有 submit 步骤，
            # 重试只会死循环撞同一堵墙。不再报错死路，降级自由循环（断点审批），
            # 让 LLM 直接读文档与用户讨论；真要出图时循环内照样能编排 submit。
            run_trace.emit(ctx, "plan.compile", status="fallback_fabric",
                           errors="; ".join(outcome.errors)[:300])
            trace = trace + ["🧭 意图与计划形态不符（疑似讨论型请求）→ 降级自由循环"]
            return _run_fabric_content_loop(ctx, state, intent_text=intent_text,
                                            judge_text=judge_text, text=text,
                                            trace=trace, output_dir=output_dir,
                                            configured=configured)
        return {"result_text": "计划编译未通过校验：\n- " + "\n- ".join(outcome.errors),
                "trace": trace}
    # 段落引用回填：模型为省输出上限会把变体写成 prompt_section 引用，
    # 编译期按预读附件机械抽取完整提示词（执行器无 LLM，必须在此回填）。
    try:
        filled = plan_compiler.fill_prompt_sections(outcome.plan, attachments)
        if filled:
            run_trace.emit(ctx, "plan.sections_filled", count=filled)
    except Exception as exc:  # noqa: BLE001 - 回填失败如实留痕
        run_trace.emit(ctx, "plan.sections_filled", status="error", error=str(exc))
    json_path = plan_compiler.save_plan(output_dir, outcome.plan.repo_id, outcome.plan)
    run_trace.emit(ctx, "plan.validated", status="ok", steps=len(outcome.plan.steps),
                   path=json_path)
    # P2：自动投递执行队列；durable/expensive 步骤由 P3 审批闸门拦到 awaiting_approval
    queue_note = ""
    try:
        from app.services import plan_tasks
        submitted = plan_tasks.submit_task(
            outcome.plan, output_dir=output_dir,
            repo_id=str(ctx.get("repo_id") or ctx.get("thread_id") or ""),
            configured_models=configured)
        queue_note = ("已投递执行队列" if not submitted["deduped"]
                      else f"与已有任务重复，复用 {submitted['task_id'][:8]}")
    except ValueError as exc:
        queue_note = f"投递被拒：{exc}"
    card = plan_compiler.render_plan_card(outcome.plan, json_path)
    approval_marker = f"\n\n[[plan:{submitted['task_id']}]]" if submitted.get("task_id") else ""
    return {"result_text": card + "\n" + queue_note + approval_marker,
            "trace": trace + ["📋 计划已落盘并投递执行队列（durable/expensive 需审批）"]}


def _pc_design_discussion_reply(ctx: dict, state: AgentState, *, intent_text: str,
                                judge_text: str, text: str, trace: list,
                                output_dir: str, configured: set) -> dict | None:
    """设计讨论型：登记任务会话后降级自由循环（不编机械计划）。

    命中条件 = `is_design_discussion_intent(judge_text)` 或
    `_task_session_continuation(ctx, intent_text)`；未命中返回 None 交给下游分派。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    if not (plan_compiler.is_design_discussion_intent(judge_text)
            or _task_session_continuation(ctx, intent_text)):
        return None
    # 设计讨论型（2026-09-11）：讨论没有 submit/产出步骤，编机械计划必然撞
    # 「意图与计划不一致」；正道是自由循环——LLM 读文档直接讨论，要产出时
    # 循环内照样能编排。按合同模板注入灵感卡/历史附件/提示词规范。
    # 上下文合同 P1：任务会话登记——首链存原始任务句与材料，接续轮判定
    # 优先查登记（词表只兜底），登记缺失全部 fail-open 退回现行判定。
    trace = trace + ["💬 设计讨论 → 自由循环（不编机械计划）"]
    try:
        from app.services import task_session_store
        task_session_store.save({
            "task_type": "design_discussion",
            "intent": intent_text,
            "thread_id": str(ctx.get("thread_id") or ""),
            "repo_id": str(ctx.get("repo_id") or ""),
            "output_dir": output_dir,
            "materials": [
                {"file_id": str(_a.get("file_id") or ""),
                 "name": str(_a.get("name") or "")}
                for _a in (ctx.get("attachments") or [])
                if isinstance(_a, dict) and _a.get("file_id")
            ],
        })
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 登记失败不阻断讨论，但任务会话缺本轮登记
        _LOG.warning("设计讨论任务登记失败（讨论继续，但任务会话缺该登记）: %s", exc)
    return _run_fabric_content_loop(ctx, state, intent_text=intent_text,
                                    judge_text=judge_text, text=text,
                                    trace=trace, output_dir=output_dir,
                                    configured=configured,
                                    task_type="design_discussion")


def _pc_register_delegation(ctx: dict, *, intent_text: str, judge_text: str,
                            output_dir: str) -> None:
    """上下文合同 P2b：委派型任务登记（materials 并集 / 首链意图保留）。

    一线程一活跃任务，后一个任务自然覆盖前一个（设计讨论接续判定随之失效，词表粘性兜底）。
    路由不消费委派登记（委派意图本身判定可靠），仅作任务卡 ①目标/②材料/⑤产物的登记源；
    登记失败不阻断。逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    if not (plan_compiler.is_delegation_intent(intent_text)
            or plan_compiler.is_doc_delegation_intent(judge_text)):
        return
    try:
        from app.services import task_session_store
        task_session_store.save({
            "task_type": "delegation",
            "intent": intent_text,
            "thread_id": str(ctx.get("thread_id") or ""),
            "repo_id": str(ctx.get("repo_id") or ""),
            "output_dir": output_dir,
            "materials": [
                {"file_id": str(_a.get("file_id") or ""),
                 "name": str(_a.get("name") or "")}
                for _a in (ctx.get("attachments") or [])
                if isinstance(_a, dict) and _a.get("file_id")
            ],
        })
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 登记失败不阻断委派，但任务会话缺该登记
        _LOG.warning("委派任务登记失败（委派继续，但任务会话缺该登记）: %s", exc)


def _pc_run_full_loop(ctx: dict, state: AgentState, *, text: str, judge_text: str,
                      output_dir: str, configured: set, trace: list) -> dict | None:
    """full 访问模式：跑自由循环（ReAct），step_limit/error 中断后自动续跑（上限 2 次）。

    仅在 `_agent_access_mode() == ACCESS_FULL` 时生效，否则返回 None 交给下游分派。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    from app.services import capability_sandbox, fabric_loop, plan_tasks
    if plan_tasks._agent_access_mode() != capability_sandbox.ACCESS_FULL:
        return None
    from app.services import fabric_checkpoint
    lease = capability_sandbox.grant(
        subject=f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}",
        capabilities=[], ttl_seconds=86400, approved_by="full_mode",
        mode=capability_sandbox.ACCESS_FULL)
    history_text = _fabric_knowledge_history(ctx, text)
    cp_id = f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}"
    thread_id = str(ctx.get("thread_id") or "")
    repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "")

    def _cp_fn_full(step: int, msgs: list, steps: list, status: str) -> None:
        fabric_checkpoint.save({
            "id": cp_id, "status": status,
            "intent": text, "history": history_text,
            "delivery_intent": judge_text,
            "messages": msgs, "steps": steps,
            "lease_id": lease["id"], "pending_tool": "",
            "output_dir": output_dir, "repo_id": repo_id, "thread_id": thread_id,
        })

    _resume_full: dict | None = None
    _cpf = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
    if _cpf is not None:
        _resume_full = {"messages": list(_cpf.get("messages") or []),
                        "steps": list(_cpf.get("steps") or [])}
    # 2026-09-09 harness 自动化：step_limit/error 中断后自动续跑（上限 2 次），
    # 用户发一次指令即可跑完（条目数闸门在 fabric_loop 内保证未达标不 done）。
    outcome: Any = None
    _auto = 0
    while True:
        outcome = fabric_loop.run_loop(**_fabric_run_args(
            ctx, intent=text, history=history_text,
            access_mode=capability_sandbox.ACCESS_FULL,
            lease_id=lease["id"], output_dir=output_dir,
            repo_id=repo_id,
            configured=configured, images=list(state.get("images") or []),
            resume=_resume_full, checkpoint_fn=_cp_fn_full,
            delivery_intent=judge_text,
        ))
        if outcome.status in ("step_limit", "error") and _auto < 2:
            _cp2 = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
            if _cp2 is None:
                break
            _auto += 1
            _resume_full = {"messages": list(_cp2.get("messages") or []),
                            "steps": list(_cp2.get("steps") or [])}
            run_trace.emit(ctx, "fabric.auto_resume", round=_auto,
                           status=outcome.status, steps=len(outcome.steps))
            continue
        break
    if outcome.status == "done":
        fabric_checkpoint.delete(cp_id)
    return _fabric_finalize(outcome, text, trace, ctx, output_dir)


def _pc_ask_recipe_offer(ctx: dict, *, output_dir: str, trace: list, intent_text: str,
                         judge_text: str, recovered) -> dict | None:
    """事前固化询问：值得固化的交付类任务、且已保留流程清单里没有同类时，先问一句要不要固化。

    ⚠ 只在「新任务」上问（答复轮已被 `recovered` 消费掉）；清单里已有同类时不问，
    改判能否硬重放。返回节点结果 dict；不问或不必问则返回 None。
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    if recovered is not None:
        return None
    from app.services import recipe_match, recipe_offer_store
    if recipe_match.is_solidifiable(judge_text):
        _offer_hit, _offer_score = recipe_match.similar_recipe(judge_text)
        if _offer_hit is not None:
            # 清单里已有同类 → 不问，改判能不能硬重放。
            _replay_reply = _recipe_hard_replay(
                _offer_hit, _offer_score, ctx, output_dir, trace)
            if _replay_reply is not None:
                return _replay_reply
        else:
            _offer_fp = recipe_match.fingerprint(judge_text)
            _prev_offer = _recipe_offer_pending(ctx, output_dir)
            # 同一意图只打扰一次：用户没理会、又原样重发同一条指令时直接放行去跑。
            if not (_prev_offer and _prev_offer.get("fingerprint") == _offer_fp):
                recipe_offer_store.save({
                    "id": f"offer:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}",
                    "fingerprint": _offer_fp,
                    "intent": intent_text,
                    "delivery_intent": judge_text,
                    "output_dir": output_dir,
                    "repo_id": str(ctx.get("repo_id") or ""),
                    "thread_id": str(ctx.get("thread_id") or ""),
                })
                run_trace.emit(ctx, "recipe.offer", status="asked", fingerprint=_offer_fp)
                return {
                    "result_text": (
                        "这次任务在已固化的流程清单里没有同类。要不要为它固化成一条"
                        "可复用流程？以后同类请求可以整条重放、省 token。\n\n"
                        "- 回复「要」→ 现在照常执行，跑完把这次的步骤固化成流程；\n"
                        "- 回复「直接跑」→ 照常执行、不固化（默认）。"),
                    "trace": trace + ["🧩 询问是否固化流程"]}
    return None


def _pc_approval_word_reply(ctx: dict, text: str, output_dir: str, trace: list) -> dict | None:
    """审批词直通车：消息恰为审批/取消词时直接审批或取消最近的 awaiting_approval 计划。

    返回节点结果 dict；不是审批词则返回 None（调用方继续后续分派）。
    ⚠ 无待审批计划时先探 approval 自由循环 checkpoint（2026-09-06 断点续跑），
    再回「当前没有待审批的计划。」
    逐字自 `plan_compiler_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    _word = _pc_approval_word(text)
    if _word:
        from app.services import plan_tasks as _pt
        _pending = [t for t in _pt.list_tasks(output_dir=output_dir, limit=10)
                    if t["status"] == "awaiting_approval"]
        if not _pending:
            # 无待审批计划时，看 approval 自由循环 checkpoint（2026-09-06 断点续跑）
            _fabric = _fabric_approval_word(_word, ctx, output_dir, trace)
            if _fabric is not None:
                return _fabric
            return {"result_text": "当前没有待审批的计划。", "trace": trace}
        _task = _pending[0]
        if _word in ("取消", "cancel", "撤销"):
            _pt.cancel_task(_task["id"])
            return {"result_text": f"已取消计划：{_task['intent'][:120]}", "trace": trace}
        _pt.approve_task(_task["id"])
        return {"result_text": f"已批准计划：{_task['intent'][:120]}\n执行器继续执行，"
                               f"进度见后台活动面板。", "trace": trace}
    return None


def plan_compiler_node(state: AgentState) -> dict:
    """智能编造节点：full 模式跑自由循环（ReAct）；approval 模式内容生成型任务
    （卡/书/文档交付，固化02/03）走自由循环断点审批，批量生图等确定性任务走计划编译。"""
    ctx = state["_ctx"]
    text = state.get("user_text", "")
    trace = state.get("trace", []) + ["📋 智能编造计划编译中…"]
    run_trace.emit(ctx, "agent.started", agent="plan_compiler")
    output_dir = str(ctx.get("output_dir") or "").strip()
    if not output_dir:
        return {"result_text": "请先选择作品（计划文档需要落盘到作品文件夹）。", "trace": trace}
    configured = _pc_configured_models(ctx)
    # 审批词直通车：对话里发「批准/同意/执行/取消」不再编译新计划，
    # 直接审批/取消最近一个 awaiting_approval 计划（修复 2026-09-02 实锤：
    # 用户发「批准」被当成新任务编译，上下文断档）。
    _approval_reply = _pc_approval_word_reply(ctx, text, output_dir, trace)
    if _approval_reply is not None:
        return _approval_reply
    # 事前固化询问的答复（2026-09-10）：回「要」→ 照常跑并固化；回「直接跑」→ 照常跑
    # 但不固化。两条分支都必须用**询问时存下的原始任务文本**继续——本轮消息只是这两个
    # 词，拿它当 intent 会把真正的任务整个丢掉（supervisor 已零 LLM 强制路由到这里）。
    _offer_recovered = _recipe_offer_answer(text, ctx, output_dir)
    if _offer_recovered is not None:
        trace = trace + ["🧩 已按固化询问答复继续"]
        if _offer_recovered["answer"] == "skip":
            # 用户已明确不要为这次固化流程 → 抑制 done 时的草稿卡。否则跑完照样弹
            # 「保留/不保留」，那次询问就成了纯噪声（问与不问结果一样）。
            ctx["_suppress_recipe"] = True
        else:
            ctx.pop("_suppress_recipe", None)
    # 访问模式分派：
    # - full：自由循环（ReAct），模型逐步决定调用哪个能力，观察结果后自行修正；
    # - approval 内容生成型任务（卡/书/文档交付，固化02/03 场景）：走自由循环断点审批——
    #   机械计划执行器不调 LLM，承载不了「读素材→LLM 写条目」的内容生成
    #   （2026-09-06 实锤占位条目/生图误注入），自由循环 + 逐能力审批才是正解；
    # - approval 其余（批量生图等确定性任务）：继续走下方计划编译+机械执行。
    #
    # 判定用文本（full/approval 共用，2026-09-07 治本 + 2026-09-10 扩展）：
    # 短指令（「继续补写」「进行补写内容」）零 LLM 判定丢语义——supervisor 有完整历史
    # 能判对 plan，本节点只看本轮短句却漏判 → 掉进机械 compile_plan（实锤：1 步
    # check_density 计划 partial 卡死）。修法：本轮不命中时拼历史用户消息再判一次。
    # 2026-09-10：该判定文本还作为 **delivery_intent** 决定能力面与前序产出档案
    # （不改 intent）——「先对话积累上下文，再说一句『整理成设定总集』」时，若用本轮
    # 原文判定，白名单与前序产出扫描都会落空。
    intent_text = str(ctx.get("message") or text or "").strip()
    if _offer_recovered is not None and _offer_recovered["intent"]:
        # 答复轮：本轮原文只是「要 / 直接跑」，真正的任务文本在询问记录里。
        intent_text = _offer_recovered["intent"]
        text = intent_text
    _fabric_judge = _pc_judge_text(intent_text, ctx.get("history"))
    # ── 事前固化询问（2026-09-10）：值得固化且清单无同类时先问一句（详见接缝 docstring）
    _offer_reply = _pc_ask_recipe_offer(
        ctx, output_dir=output_dir, trace=trace, intent_text=intent_text,
        judge_text=_fabric_judge, recovered=_offer_recovered)
    if _offer_reply is not None:
        return _offer_reply
    _full_reply = _pc_run_full_loop(ctx, state, text=text, judge_text=_fabric_judge,
                                    output_dir=output_dir, configured=configured,
                                    trace=trace)
    if _full_reply is not None:
        return _full_reply
    _pc_register_delegation(ctx, intent_text=intent_text, judge_text=_fabric_judge,
                            output_dir=output_dir)
    if plan_compiler.is_doc_delegation_intent(_fabric_judge):
        # approval 内容生成型：自由循环断点审批（读文件/写条目/落盘逐个确认）
        return _run_fabric_content_loop(ctx, state, intent_text=intent_text,
                                        judge_text=_fabric_judge, text=text,
                                        trace=trace, output_dir=output_dir,
                                        configured=configured)
    _discussion_reply = _pc_design_discussion_reply(
        ctx, state, intent_text=intent_text, judge_text=_fabric_judge, text=text,
        trace=trace, output_dir=output_dir, configured=configured)
    if _discussion_reply is not None:
        return _discussion_reply

    # 编译期预读：本轮消息 + 历史用户消息里显式写出的本地文本文件（仅用户明示的
    # 路径，容量封顶）。历史用户消息兜底「根据上一轮的批量生成进行修改」这类不带
    # 路径的指代——预读必须在编译期完成，执行器无 LLM，file.read_text 读出的内容
    # 填不进 variants（2026-09-03 实锤：模型编了「套装1..10」占位）。
    attachments: list[dict] = []
    prev_batch: dict | None = None
    try:
        _pc_collect_attachments(ctx, text, attachments)
        # 批量延续（方案 A，2026-09-05 实锤）：续轮编译上下文没有原文档（附件只随
        # 当条消息注入、历史只带可见文本），模型凭记忆重写提示词=幻觉。命中延续
        # 意图时把上一批计划的真实变体注入编译上下文，prompt 由编译期按 name 回填。
        # 判定必须用剥离【文件参考】块后的指令文本（2026-09-05 实锤：100k 字小说
        # 全文会误命中批量词+动作词，把生图上一批注入合集卡任务）。
        compile_text = re.sub(r"【文件参考：.*?【文件参考结束：[^】]*】", "", text, flags=re.S).strip()
        prev_batch = _pc_prev_batch_and_flow_hint(
            ctx, text, compile_text, output_dir, attachments)
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "plan.attachments", status="error", error=str(exc))
    # compile_text 已在上方 prev_batch 判定处提前计算（剥离【文件参考】块）；
    # 附件正文已进 attachments，避免大文档全文在 user intent 里再送一遍拖慢编译。
    return _pc_compile_and_finalize(
        ctx, state, text=text, intent_text=intent_text, judge_text=_fabric_judge,
        trace=trace, output_dir=output_dir, configured=configured,
        attachments=attachments, compile_text=compile_text, prev_batch=prev_batch)


def answer_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="answer")
    text = state.get("user_text", "")
    trace = state.get("trace", []) + ["💬 对话中…"]
    streamed = _stream_enabled(ctx)
    try:
        from app.services.regex_engine import Placement
        text = _apply_regex(ctx, text, Placement.USER_INPUT, is_prompt=True, depth=0,
                            skip_depth_gated=True)
        run_trace.emit(ctx, "input.processed", agent="answer", processed_input=text)
        system = _agent_system(
            ctx, _builtin(ctx, "answer", "systemPrompt", builtin_agents.ANSWER_SYSTEM),
        )
        # M3（2026-09-06）：正常对话长期事实注入（recency-first，同 M2；缺失不阻断）
        try:
            from app.services import chat_facts as _chat_facts
            _thread = ctx.get("repo_id") or ctx.get("thread_id") or ""
            _turn = _next_story_turn(ctx)
            _facts_block = _chat_facts.render_facts_block(_thread, _turn)
            if _facts_block:
                system += "\n\n" + _facts_block
        except Exception as exc:  # noqa: BLE001 [EXC-留痕] 记忆注入永不阻断对话，但本轮缺事实记忆
            _LOG.warning("事实记忆注入失败（对话继续，但本轮回答缺 chat_facts 块）: %s", exc)
        user = agent_context.history_text(ctx) + text
        ans_temp = _builtin(ctx, "answer", "temperature", builtin_agents.ANSWER_TEMPERATURE)
        run_trace.emit(ctx, "model.request", agent="answer", model=ctx["chat_model"],
                       messages=[{"role": "system", "content": system},
                                 {"role": "user", "content": user}])
        reply = _chat_with_optional_stream(
            ctx,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=_temperature(ctx, ans_temp),
            **_builtin_sampling(ctx, "answer"),
        )
        reply = _apply_regex(ctx, reply or "（无回复）", Placement.AI_OUTPUT, is_prompt=False, depth=0)
        run_trace.emit(ctx, "model.response", agent="answer", content=reply)
        # M3（2026-09-06）：门控抽取长期事实（每 CADENCE 回合一次，搭本线程模型；
        # 失败静默跳过不阻断回复交付——抽取是增强不是依赖）
        try:
            from app.services import chat_facts as _chat_facts
            _thread = ctx.get("repo_id") or ctx.get("thread_id") or ""
            _turn = _next_story_turn(ctx)
            _window = agent_context.history_text(ctx) + "\n\n用户：" + text + "\n助手：" + reply
            _written = _chat_facts.maybe_extract(
                _llm.chat, _thread, window_text=_window, turn=_turn,
                chat_base=ctx.get("chat_base") or "", chat_key=ctx.get("chat_key") or "",
                chat_model=ctx.get("chat_model") or "", proxy=ctx.get("chat_proxy") or "",
            )
            if _written:
                run_trace.emit(ctx, "chat_facts.written", thread=_thread, turn=_turn, count=1)
        except Exception as exc:  # noqa: BLE001 - 抽取失败不阻断回复
            run_trace.emit(ctx, "chat_facts.error", error=str(exc))
        return {"result_text": reply, "trace": trace, "_streamed_result": streamed}
    except roleplay_turn.RoleplayInterrupted as exc:
        # 用户主动停止（2026-09-04 与 roleplay_node 对齐）：中断≠失败。
        # 此前 answer_node 只捕 Exception，用户点停止会显示成「回答失败：用户已停止生成…」。
        run_trace.emit(ctx, "agent.interrupted", agent="answer", reason=str(exc))
        return {"result_text": "", "trace": trace + [f"⏹ {exc}"],
                "_streamed_result": streamed, "_interrupted": True}
    except Exception as e:  # noqa: BLE001
        return {"result_text": f"回答失败：{e}", "trace": trace, "_streamed_result": streamed}


# 剧情扮演默认提示词由 builtin_agents 单一属主（③ 可覆盖）；别名保留旧引用不破坏。
_ROLEPLAY_BASE = builtin_agents.ROLEPLAY_BASE


def _rp_prepare_worldbook(ctx: dict, text: str) -> tuple[str, str]:
    """世界书组装 + 三段世界信息正则（锚点段 / 召回段 / 动态段）。

    召回段与动态段改完**写回 ctx**（`_worldbook_recall_text` / `_worldbook_dynamic_text`），
    调用方后续仍从 ctx 读（与改造前同一份绑定语义）。返回 (锚点段 wb, 召回段 wb_recall)。
    逐字自 `roleplay_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    from app.services.regex_engine import Placement
    wb = _resolve_worldbook(ctx, text)
    if wb:
        # 世界信息正则（placement 5）：作用于世界书组装后的注入文本
        wb = _apply_regex(ctx, wb, Placement.WORLD_INFO, is_prompt=True, depth=0)
    # 世界书召回段（每轮按相关度重排）：同一套世界信息正则，但**落尾部动态块**
    # （history 之后，与数据表召回同位置）。若与锚点段拼在一起塞进世界书块（history 之前），
    # 它每轮一变就把其后的历史与全部预设片段打掉——真机实测相邻轮字节 LCP 78% → 62%。
    wb_recall = str(ctx.get("_worldbook_recall_text") or "")
    if wb_recall:
        wb_recall = _apply_regex(ctx, wb_recall, Placement.WORLD_INFO, is_prompt=True, depth=0)
        ctx["_worldbook_recall_text"] = wb_recall
    # 世界书动态段（2026-09-13 动态区外置）：锚点条目的【剧情进展·动态】区 + 会话中途
    # 新激活的锚点条目。与召回段同一落点（尾部动态块）——留在锚点段会把分叉点拉回
    # 世界书块内，真机三次 cached=0 的分叉点全在那里。
    wb_dynamic = str(ctx.get("_worldbook_dynamic_text") or "")
    if wb_dynamic:
        wb_dynamic = _apply_regex(ctx, wb_dynamic, Placement.WORLD_INFO, is_prompt=True, depth=0)
        ctx["_worldbook_dynamic_text"] = wb_dynamic
    return wb, wb_recall


def _rp_emit_worldbook_resolved(ctx: dict, wb: str, wb_recall: str) -> None:
    """派发 `worldbook.resolved` 观测事件（纯留痕，不改任何状态）。

    逐字自 `roleplay_node` 搬移。
    """
    run_trace.emit(
        ctx, "worldbook.resolved", injected=bool(wb), content=wb or "",
        selected_indices=ctx.get("_selected_worldbook_indices") or [],
        keyword_indices=ctx.get("_keyword_worldbook_indices") or [],
        character_names=ctx.get("_worldbook_character_names") or [],
        scan_chars=int(ctx.get("_worldbook_scan_chars") or 0),
        dropped=int(ctx.get("_worldbook_dropped") or 0),
        recall_chars=len(wb_recall),
    )


def _rp_is_first_story_reply(ctx: dict) -> bool:
    """**纯判定**：本会话是否还没有任何非空用户发言（= 开场首轮）。

    逐字自 `roleplay_node` 搬移（唯一改动＝首行 `first_story_reply = ` → `return `）。
    """
    return not any(
        item.get("role") == "user" and str(item.get("content") or "").strip()
        for item in (ctx.get("history") or [])
    )


def _rp_last_story_round(ctx: dict) -> list:
    """上下文合同·历史：取上一次剧情轮并**瘦到只剩正文**，再派发 `history.slimmed`。

    逐字自 `roleplay_node` 搬移。
    """
    story_round = story_history.last_story_round(ctx.get("history") or [])
    run_trace.emit(ctx, "history.slimmed",
                   full_messages=len(ctx.get("history") or []),
                   injected_messages=len(story_round),
                   injected_chars=sum(len(m["content"]) for m in story_round))
    return story_round


def _rp_resolve_persona_block(ctx: dict, text: str, first_story_reply: bool) -> None:
    """解析并写回 `ctx["persona"]`（角色卡 persona）+ 派发 `character_cards.resolved`。

    ⚠ 就地覆写 ctx 键（清查项）：下游从 `ctx.get("persona")` 读 ⇒ 必须写回 ctx，不能只返回。
    逐字自 `roleplay_node` 搬移。
    """
    ctx["persona"] = _resolve_personas(
        ctx, text, opening_only=first_story_reply,
        worldbook_names=ctx.get("_worldbook_character_names") or [],
        fallback_query=_recent_character_context(ctx),
    )
    run_trace.emit(
        ctx, "character_cards.resolved",
        opening_only=first_story_reply,
        selected=ctx.get("_selected_persona_names") or [],
        injected=bool(ctx.get("persona")),
    )


def _rp_load_style_config(ctx: dict) -> None:
    """每轮读一次文风（去 AI 味）用户态配置，写回 `ctx["_style_config"]`；

    损坏不能阻断正文 ⇒ 只留痕。逐字自 `roleplay_node` 搬移。
    """
    try:
        from app.services import prose_style
        ctx["_style_config"] = prose_style.load_config()
    except Exception as exc:  # noqa: BLE001 文风配置损坏不能阻断正文
        run_trace.emit(ctx, "prose_style.config", status="error", error=str(exc))


def _rp_build_prompt_base(ctx: dict, *, wb: str, preset_msgs: list, preset_temp,
                         has_hist: bool, story_round: list) -> tuple[str, float, list]:
    """两条分支（激活预设 / 内置扮演）各自的 system 头 base、temperature 与对话轮。

    - 预设分支：起始连续 system 段 → head（进 system 头），其余保持原位当对话轮；
      预设无 chatHistory marker 时把历史补在对话末尾。
    - 内置分支：内置扮演提示 + persona + 用户人设 + 世界书 + 宏替换。
    返回 (base, temp, dialogue)。逐字自 `roleplay_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    if preset_msgs:
        # 拆：起始连续 system 段 → 进 system 头（受 _agent_system 包裹/替换）；其余(user/assistant/
        # 历史/尾部 system=PHI)保持原位当对话轮。这样 role 不被抹平，PHI 仍在历史之后。
        head_parts: list[str] = []
        dialogue: list[dict] = []
        for m in preset_msgs:
            if not dialogue and m["role"] == "system":
                head_parts.append(m["content"])
            else:
                dialogue.append(m)
        base = "\n\n".join(head_parts)
        rp_temp = _builtin(ctx, "roleplay", "temperature", builtin_agents.ROLEPLAY_TEMPERATURE)
        temp = preset_temp if preset_temp is not None else _temperature(ctx, rp_temp)
        if not has_hist:  # 预设无 chatHistory marker → 历史补在对话末尾
            dialogue += story_round
    else:
        persona = ctx.get("persona") or ""
        rp_base = _builtin(ctx, "roleplay", "systemPrompt", builtin_agents.ROLEPLAY_BASE)
        base = rp_base + (f"\n\n{persona}" if persona else "")
        up = _render_user_persona(ctx)
        if up:
            base += f"\n\n{up}"
        if wb:
            base += f"\n\n{wb}"
        # 卡字段/世界书可能含 {{char}}/{{user}} 宏 → 替换（缺省 user 回退「我」），避免字面漏进提示词
        from app.services import preset_store as _ps
        base = _ps.substitute_macros(base, {
            "char_name": (ctx.get("card_name") or "").strip(),
            "user_name": (ctx.get("user_name") or "").strip(),
        })
        temp = _temperature(ctx, _builtin(ctx, "roleplay", "temperature", builtin_agents.ROLEPLAY_TEMPERATURE))
        dialogue = story_round  # 历史作真实多轮，只进上一次剧情轮的纯正文（上下文合同）
    return base, temp, dialogue


def _rp_seed_dynamic_parts(ctx: dict, wb_recall: str, dynamic_parts: list) -> None:
    """把每轮变化的两个世界书段（召回段 / 动态段）推入**尾部动态块**。

    ⚠ 就地 mutate 传入的 `dynamic_parts`（与改造前同一列表对象 ⇒ 调用方拿到同一份）。
    逐字自 `roleplay_node` 搬移。
    """
    if wb_recall:
        dynamic_parts.append(wb_recall)
    # 世界书动态段（动态区+中途新锚点）：同样只落尾部动态块，理由同上。
    wb_dynamic = str(ctx.get("_worldbook_dynamic_text") or "")
    if wb_dynamic:
        dynamic_parts.append(wb_dynamic)


def _rp_append_static_blocks(ctx: dict, base: str, chains_head: list) -> str:
    """把**跨轮逐字节稳定**的静态块追加进 system 头：头部思维链 + 命运骰点规则。

    ⚠ 与 `state_instruction()`（同样静态）分处两支：本函数只做「随 system 头」的那两段。
    逐字自 `roleplay_node` 搬移（骰点规则文案字面值零改动）。
    """
    if chains_head:
        base += "\n\n" + "\n\n".join(chains_head)
    # 命运骰点规则：注入剧情推进提示词，让主模型在关键博弈点打可审计 <roll>（用户可在编辑器改/清空）
    roll_rule = _builtin(ctx, "roleplay", "rollInstruction", builtin_agents.ROLL_INSTRUCTION)
    if roll_rule and roll_rule.strip():
        base += roll_rule
    return base


def _rp_substitute_macros(ctx: dict, base: str, dynamic_parts: list) -> tuple[str, str]:
    """对最终**两段**各替换一次 `{{char}}`/`{{user}}`（缺省 user 回退「我」）。

    收口原因：状态块/纪要召回/裁定指令等在 substitute 之后才生成，且预设分支根本没替换 base。
    返回 (base, dynamic_text)。逐字自 `roleplay_node` 搬移。
    """
    from app.services import preset_store as _ps2
    _macros = {
        "char_name": (ctx.get("card_name") or "").strip(),
        "user_name": (ctx.get("user_name") or "").strip(),
    }
    base = _ps2.substitute_macros(base, _macros)
    dynamic_text = _ps2.substitute_macros(
        "\n\n".join(part for part in dynamic_parts if part.strip()), _macros,
    )
    return base, dynamic_text


def _rp_prefix_output_structure(system: str) -> str:
    """把【输出结构】模板前置到 system 最开头（首因位置守约率最高）。

    ⚠ 模板文案字面值零改动（`<roll>/<encounter>/<fate>` 三块由 `test_output_blocks` 源码锁点名）。
    逐字自 `roleplay_node` 搬移。
    """
    system = (
        "【输出结构（每轮固定，顺序不可换）】\n"
        "第一步 <think>完整推演思考</think>——按预设思考框架展开推演；\n"
        "第二步 <status>状态栏</status>——延续上轮格式逐字段更新；\n"
        "第三步 <content>正文</content>——按预设 <length> 字数要求足量输出；"
        "<roll>/<encounter>/<fate> 等事件块按剧情需要穿插在正文中间。\n"
        "所有标签必须成对完整闭合，各类块只输出一次，禁止在思考里预写任何标签草稿。\n\n"
        + system
    )
    return system


def _rp_user_priority_block(user_text: str) -> str:
    """「本轮用户安排·最高优先级」声明 + **逐字复述**用户本轮输入（空输入 → 空串）。

    2026-09-23 用户定案：**不改预设/世界书/前情/表格的任何内容**，改为在**离生成点最近**处
    抬升用户输入的权重——本块是尾部 system 消息的**最后一条**，紧跟 [输出纪律]、紧邻本轮 `user`。
    与随 system 头的 `ROLL_INSTRUCTION` 分处首尾两端，构成「近端锚」。

    纯函数、零副作用、不读 ctx；空输入逐字返回空串 ⇒ 既有消息组一字不变（等价矩阵不破）。
    """
    text = (user_text or "").strip()
    if not text:
        return ""
    return f"{builtin_agents.USER_PRIORITY_INSTRUCTION}\n{text}"


def _rp_build_tail_messages(ctx: dict, dynamic_text: str, chains_tail: list,
                            user_text: str = "") -> list[dict]:
    """组装 system **头之后**的尾部 system 消息，顺序＝[动态块][思维链 tail][输出纪律][音频][用户安排]。

    动态块是每轮变化的块 → 落在历史之后，静态头（system）才能逐字节跨轮稳定（P4-roleplay 前缀复用）。
    逐字自 `roleplay_node` 搬移（输出纪律提示词文案字面值零改动）＋ 末尾追加「用户安排最高优先级」
    块（2026-09-23：抬升用户输入权重；空 `user_text` ⇒ 不追加，既有消息组逐字不变）。
    """
    tail_msgs: list[dict] = []
    if dynamic_text:
        tail_msgs.append({"role": "system", "content": dynamic_text})
    # 尾部思维链作独立 system 消息落在历史之后、本轮 user 之前 → 离生成点最近，遵守最严
    tail_msgs.extend({"role": "system", "content": c} for c in chains_tail)
    # 思考纪律（2026-09-12 用户拍板修订）：思考定位为**完整推演思考**——发布后由前端
    # 「小cot折叠」正则折叠为「推演内思考过程」下拉（用户设计，思考本就该发布而非丢弃）。
    # 旧版「三类内容 + 300 字硬上限」与预设「默认COT组装」框架直接矛盾，模型面对矛盾
    # 指令干脆不写思考（09-12 20:32 trace 实证思考=0）。保留防烧钱核心禁令
    # （试写正文/复述设定/推翻重来/标签草稿——2026-08-29/30 事故教训），删思考长度压制；
    # 正文长度以预设 <length> 字数区间为准（预设已启用 1000-8000），不在后端另设硬指标。
    # 思考纪律（2026-09-12 三次修订定稿）：思考定位为**完整推演思考**（发布后由前端
    # 「小cot折叠」折叠为「推演内思考过程」下拉）。历次实验：300 字硬上限 → 模型干脆
    # 不写思考（deepseek）；点名禁令清单 → 冗余根因其实在预设思考框架本身（用户裁定
    # 禁令去掉，去预设里找）。定稿=只保留**思考预算原则**：思考总篇幅 ≤ 正文一半
    # （软比例，用户认可），预算外的冗余治理落在预设思考框架的瘦身（另行处理）。
    # 泄漏修订（2026-09-13）：09-12 版把「前端折叠」「1000-8000 字」等实现细节写进了
    # 模型可见文本，模型照抄进正文（神权大陆 msg[4] 实证：质量检查/状态栏更新/
    # 【系统提示…尾部标注】三处泄漏）。净提示词=删全部实现细节 + 追加元话语硬禁令；
    # 出口兜底见 roleplay_turn.strip_meta_leak_in_content（只动 <content>）。
    tail_msgs.append({
        "role": "system",
        "content": (
            "[输出纪律] 严格按 system 开头的【输出结构】执行：①<think>完整推演思考</think>"
            "——按预设思考框架展开推演，写成连贯的多段推演，禁止写成一句话摘要糊弄；\n"
            "②<status>状态栏</status>——在思考之后、正文之前，延续上轮格式逐字段更新；\n"
            "③正文——按预设 <length> 的字数区间足量交付，"
            "不因思考延长而缩水正文；<roll>/<encounter>/<fate> 等事件块穿插正文中间。\n"
            "思考预算原则：思考的每个字都按输出计费——总篇幅**不超过正文的一半**。"
            "推演的价值在决策质量不在铺陈，把输出预算留给正文、状态栏与事件块。\n"
            "硬禁令：只输出剧情本体。禁止在任何输出区写面向系统的说明文字——包括但不限于"
            "质量检查、字数统计/字数要求、对状态栏的更新说明、【系统提示】/【系统说明】等"
            "标注块。这类文字不是剧情，一个字都不许出现。"
        ),
    })
    # 同轮成稿已剥离：不再向正文轮下发 <illustration> JSON 义务及其 near_generation_contract
    # 尾部合同；视觉锚与画像成稿全部由插画链在 produce 阶段独立编译（_profile_llm_fallback）。
    if ctx.get("comfy_audio"):
        from app.services import audio_dialogue_extract
        tail_msgs.append({
            "role": "system",
            "content": audio_dialogue_extract.build_inline_audio_instruction(),
        })
    # 用户安排最高优先级：落在**最后一条** system ⇒ 紧邻本轮 user，近端权重最高（2026-09-23 用户定案）
    priority_block = _rp_user_priority_block(user_text)
    if priority_block:
        tail_msgs.append({"role": "system", "content": priority_block})
    return tail_msgs


def _rp_recall_memory(ctx: dict, *, deps, text: str) -> tuple[str, str, str]:
    """① RAG/检索表召回 + 往事纪要（`recall_chronicle`，只检索不独立调 LLM）。

    返回 (repo_id, table_recall, recall)；`retrieval_query` / `rag_text` 只在本段内用，不外传。
    逐字自 `_rp_inject_agency_context` 搬移。
    ② 纪要引用透出（2026-09-18）：命中时把「本轮引用」行+明细暂存 `ctx["_rp_citations"]`。
    """
    from app.services import roleplay_agency
    repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
    retrieval_query = (agent_context.history_text(ctx)[-600:] + text)
    # 表格+RAG 结合：先从 rag_store 召回知识库条目 + 检索表行（同库同通道）
    rag_text = _rag_recall_text(ctx, repo_id, retrieval_query)
    table_recall = _table_recall_text(ctx, repo_id, retrieval_query)
    # Recall 只做检索，不独立调 LLM：往事纪要（人物相关优先，简要 Top-10）
    # + 知识库/检索表候选原样并入 GrayWill 主请求，由主模型结合世界书、历史与本轮输入一次判断并生成。
    citations: list = []
    recall = roleplay_agency.recall_chronicle(
        deps, repo_id=repo_id, query=retrieval_query, rag_text=rag_text,
        actors=[
            name for name in (ctx.get("illustration_actor_names") or [])
            if name and name in retrieval_query
        ] or ([ctx.get("card_name")] if ctx.get("card_name") else []),
        citations_out=citations,
    )
    if recall:
        _probe.info("🔎[RAG召回] repo=%s 注入%d字:\n%s", repo_id, len(recall), recall[:800])
        run_trace.emit(ctx, "rag.injected", status="ok", content=recall,
                       char_count=len(recall))
        # ② 纪要引用透出（2026-09-18）：本函数拿不到 trace 列表引用 → 行+明细暂存 ctx，
        # 由 roleplay_node 统一经 _rp_flush_citation_trace 并入 trace/流式通道。
        rag_hits = sum(
            1 for line in rag_text.splitlines() if line.strip().startswith("-"))
        ctx["_rp_citations"] = {
            "text": f"📖 本轮引用：往事纪要 ×{len(citations)} · 知识条目 ×{rag_hits}",
            "detail": "\n".join(
                [f"[{c['turn']}] {c['overview']}" for c in citations]
                + ([f"【相关知识/检索表】{rag_hits} 条"] if rag_hits else [])),
        }
    else:
        _probe.info("🔎[RAG召回] repo=%s 无命中（本轮未注入记忆）", repo_id)
        run_trace.emit(ctx, "rag.injected", status="empty", content="", char_count=0)
    return repo_id, table_recall, recall


def _rp_flush_citation_trace(ctx: dict, trace: list) -> None:
    """② 纪要引用透出（2026-09-18）：把召回阶段暂存的「本轮引用」行并入 trace 列表，
    并推流式通道（带 detail，前端执行面板折叠卡展开明细）。

    流式模式两路都有（streamed_result 抑制 trace 重放，行不重复下发）；非流式模式
    只落 trace 列表（节点完成时随 trace 下发，无明细）。无暂存/空行 → 无操作。
    """
    cite = ctx.pop("_rp_citations", None)
    if not cite:
        return
    line = str(cite.get("text") or "")
    if not line:
        return
    trace.append(line)
    _notify_stream_trace(ctx, line, str(cite.get("detail") or ""))


def _rp_compile_continuity(
    ctx: dict, *, repo_id: str, turn: int, st_block: str, recall: str,
    table_recall: str, dynamic_parts: list,
) -> None:
    """②③④ 时效事实账本 + 角色认知库 + 连续性编译（预算 900 token）。

    ⚠ 就地 mutate 传入的 `dynamic_parts`；并写回 ctx 的 _continuity_facts 键 /
    `ctx["_continuity_beliefs"]`（下游从 ctx 读）。逐字自 `_rp_inject_agency_context` 搬移。
    """
    from app.services import character_belief, continuity_compiler, temporal_fact_store
    active_facts: list[dict] = []
    active_beliefs: list[dict] = []
    try:
        if ctx.get("output_dir") and repo_id:
            # M2（2026-09-06）：注入面时效优先——编译预算裁剪保留块内前几行，
            # recency-first 让最新事实在压力下存活（旧行为是最老的存活，方向反了）
            active_facts = temporal_fact_store.as_of(
                ctx.get("output_dir") or "", repo_id, turn, recency_first=True,
            )
    except Exception as exc:  # noqa: BLE001 账本损坏不能阻断正文
        run_trace.emit(ctx, "temporal.recall", status="error", error=str(exc))
    else:
        run_trace.emit(
            ctx, "temporal.recall", status="ok", fact_count=len(active_facts),
        )
    ctx["_continuity_facts"] = active_facts
    belief_characters = [
        str(name) for name in (ctx.get("_selected_persona_names") or []) if str(name)
    ]
    try:
        if ctx.get("output_dir") and repo_id and belief_characters:
            active_beliefs = character_belief.active(
                ctx.get("output_dir") or "", repo_id, turn,
                characters=belief_characters,
            )
    except Exception as exc:  # noqa: BLE001 认知库损坏不能阻断正文
        run_trace.emit(ctx, "belief.recall", status="error", error=str(exc))
    else:
        run_trace.emit(
            ctx, "belief.recall", status="ok", count=len(active_beliefs),
            characters=belief_characters,
        )
    ctx["_continuity_beliefs"] = active_beliefs
    compiled = continuity_compiler.compile_context([
        continuity_compiler.ContextSource("CURRENT_STATE", st_block, True, 100),
        continuity_compiler.ContextSource(
            "ACTIVE_FACTS", continuity_compiler.temporal_fact_text(active_facts), True, 90,
        ),
        continuity_compiler.ContextSource(
            "CHARACTER_BELIEFS", character_belief.render_context(active_beliefs), True, 80,
        ),
        continuity_compiler.ContextSource("RAG_MEMORY", recall, False, 20),
    ], token_budget=900)
    if compiled.text:
        dynamic_parts.append(compiled.text)
    if table_recall:
        dynamic_parts.append("【相关数据表行（独立配额）】\n" + table_recall)
    run_trace.emit(
        ctx, "continuity.compiled", tokens=compiled.tokens,
        sources=list(compiled.included), fact_count=len(active_facts),
    )


def _rp_append_state_and_style(ctx: dict, base: str, directive: str, dynamic_parts: list) -> str:
    """⑤⑥ 搭车状态指令（静态、随 system 头）+ directive（每轮变 → 后置）+ 文风约束段。

    ⚠ 就地 mutate 传入的 `dynamic_parts`；`base` 片段内被重绑定 ⇒ 以返回值回传。
    逐字自 `_rp_inject_agency_context` 搬移。
    """
    from app.services import roleplay_agency   # 原由 ① 段局部 import，拆开后本段自带（幂等）
    # 搭车指令是固定文本（跨轮逐字节相同）→ 留静态头；本轮自主行动裁定 directive
    # 每轮变 → 后置（P4-roleplay）。
    base += roleplay_agency.state_instruction()
    if directive:
        dynamic_parts.append(directive)
    # S1 生成侧预防：从同一词表编译文风约束段（enabled=False → 空串，system 逐字节不变）。
    _style_cfg = ctx.get("_style_config") or {}
    if _style_cfg.get("enabled", True):
        from app.services import prose_style as _prose_style
        _style_seg = _prose_style.style_prompt_segment(_style_cfg)
        if _style_seg:
            base += _style_seg
            run_trace.emit(ctx, "prose_style.injected",
                           words=len(_prose_style.effective_phrases(_style_cfg)))
    return base


def _rp_prepare_visual_and_tables(
    ctx: dict, *, deps, repo_id: str, text: str, turn: int, dynamic_parts: list,
) -> None:
    """⑦ 插画视觉锚（写 `ctx["_illustration_visual_profiles"]`）+ 通用数据表只读上下文。

    ⚠ 就地 mutate 传入的 `dynamic_parts`。逐字自 `_rp_inject_agency_context` 搬移。
    """
    if getattr(deps, "renderer", None) is not None or ctx.get("comfy_illustrate"):
        from app.services import worldbook_store
        visual_query = _illustration_visual_query(ctx, text)
        visual_profiles = (
            _card_visual_profiles(ctx, visual_query)
            if ctx.get("appearance_source") == "character_card"
            else worldbook_store.repo_visual_profiles(
                ctx.get("output_dir") or "", repo_id, visual_query,
            )
        )
        ctx["_illustration_visual_profiles"] = visual_profiles
        # 上下文合同·同轮成稿剥离：不再向正文轮下发内联 <illustration> JSON 义务
        # （原 build_inline_plan_instruction+格式说明 ≈ 数千字符尾部合同）。
        # 视觉锚与成稿全部交给画像链独立编译（produce 阶段 _profile_llm_fallback）。
    # 通用数据表只作只读剧情上下文；更新由正文发出后的独立维护调用完成，
    # 禁止再让主 Roleplay 在正文尾部生成 <表格更新>。
    try:
        from app.services import character_state, table_store, table_update
        _tables = table_store.load(ctx.get("output_dir") or "", repo_id)
        turn_tables = table_store.tables_for_read(_tables)
        if turn_tables:
            dynamic_parts.append(table_update.table_context(turn_tables))
            run_trace.emit(
                ctx, "table.prompt", status="read_only", turn=turn,
                tables=[t.get("name", "") for t in turn_tables],
            )
        else:
            run_trace.emit(
                ctx, "table.prompt", status="skipped", turn=turn,
                reason="no_tables",
            )
        # 待建好感度字段（2026-09-23 用户口径）：播种规则只说「状态表里还没有字段时要
        # set 播种」，而**模型看不到"谁缺字段"**（它只看到【当前状态】块里已有的字段）
        # ⇒ 已登场却始终没被建过好感度的角色永远轮不到（真机实锤：骆姑姑在「重要角色表」
        # 有行、状态表里有 4 条叙事字段，好感度字段一直不存在）。
        # 名单 =「重要角色表」姓名列 − 状态表已有好感度属主；进动态区（不占 continuity
        # 的 900 token 预算，也不动跨轮逐字节稳定的静态头）。无 state（无卡/无 output_dir）
        # 时不注入，既有文案逐字不变。
        st = ctx.get("_character_state")
        pending = character_state.missing_affinity_owners(
            st, table_store.column_values(turn_tables, table_store.CHARACTERS_TABLE, "姓名"),
        ) if st is not None else []
        if pending:
            dynamic_parts.append(character_state.render_missing_affinity_block(pending))
            run_trace.emit(ctx, "affinity.seed_pending", roles=pending, count=len(pending))
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "table.prompt", status="error", turn=turn, error=str(exc))


def _rp_inject_agency_context(
    ctx: dict, *, deps, turn: int, st_block: str, text: str, base: str,
    directive: str, dynamic_parts: list,
) -> str:
    """阶段 B：`deps` 存在时注入 state 块 + 记忆召回 + 已裁定自主行动 + 搭车状态指令。

    七段（顺序即落点）：
    ① RAG/检索表召回 + 往事纪要（`recall_chronicle`）；
    ② 时效事实账本（`temporal_fact_store.as_of`）→ `ctx["_continuity_facts"]`；
    ③ 角色认知库（`character_belief.active`）→ `ctx["_continuity_beliefs"]`；
    ④ 连续性编译（`continuity_compiler`，预算 900 token）→ 追加动态块；
    ⑤ 搭车状态指令（静态、随 system 头）+ 本轮自主行动 directive（每轮变 → 后置）；
    ⑥ 文风约束段（enabled=False → 空串，system 逐字节不变）；
    ⑦ 插画视觉锚（写 `ctx["_illustration_visual_profiles"]`）+ 数据表只读上下文。

    ⚠ 就地 mutate 传入的 `dynamic_parts`（保持同一列表对象）；`base` 片段内被重绑定 ⇒ 以返回值回传。
    逐字自 `roleplay_node` 搬移（判定逻辑与文案字面值零改动）。
    """
    repo_id, table_recall, recall = _rp_recall_memory(ctx, deps=deps, text=text)
    _rp_compile_continuity(
        ctx, repo_id=repo_id, turn=turn, st_block=st_block, recall=recall,
        table_recall=table_recall, dynamic_parts=dynamic_parts,
    )
    base = _rp_append_state_and_style(ctx, base, directive, dynamic_parts)
    _rp_prepare_visual_and_tables(
        ctx, deps=deps, repo_id=repo_id, text=text, turn=turn, dynamic_parts=dynamic_parts,
    )
    return base


def roleplay_node(state: AgentState) -> dict:
    """剧情扮演节点：吃角色卡 persona（+后续世界书/表格记忆），沉浸式出演。"""
    ctx = state["_ctx"]
    run_trace.emit(ctx, "agent.started", agent="roleplay")
    text = state.get("user_text", "")
    trace = state.get("trace", []) + ["🎭 剧情扮演中…"]
    _probe.info("💬[会话输入] repo=%s:\n%s", ctx.get("repo_id") or ctx.get("thread_id") or "?", text)
    streamed = _stream_enabled(ctx)
    try:
        from app.services.regex_engine import Placement
        # 用户输入正则（placement 1）：本轮输入 depth 0，改发给模型的文本。
        # skip_depth_gated：跳过深度门控（历史级）删除/改写脚本，避免刚输入的当前轮被「删 history
        # 最后一条用户消息」等误擦空（本架构 live 输入尚未入历史，深度语义只该作用于历史楼层）。
        text = _apply_regex(ctx, text, Placement.USER_INPUT, is_prompt=True, depth=0,
                            skip_depth_gated=True)
        run_trace.emit(ctx, "input.processed", agent="roleplay", processed_input=text)
        wb, wb_recall = _rp_prepare_worldbook(ctx, text)
        _rp_emit_worldbook_resolved(ctx, wb, wb_recall)
        first_story_reply = _rp_is_first_story_reply(ctx)
        # 上下文合同·历史：剧情模式只进上一次剧情轮，且过滤到只剩正文
        # （剥 think/status/encounter/骰点/插画音频转场 JSON 块）；更早轮次不进上下文。
        story_round = _rp_last_story_round(ctx)
        _rp_resolve_persona_block(ctx, text, first_story_reply)
        # 能动性子图·准备：算 turn/读好感度/state 注入块（无卡或无 output_dir → deps=None 静默跳过）
        deps, turn, affinity, st_block = _agency_prelude(ctx, text)
        # 文风（去AI味）配置：每轮读一次用户态文件，失败回退内置默认（enabled+零增删）。
        _rp_load_style_config(ctx)
        # 阶段 A：世界提案 + 裁判（默认每轮判断一次：judge.gateBaseRate=1.0 / gateFloor=-100，
        # 设 0 才显式关闭；gate 未命中/失败时 directive 空，塌回单次 LLM 零额外成本）
        directive, lost = _agency_propose(ctx, deps, affinity, wb, text)
        # 有激活偏置预设 → 按预设 prompt_order 组装带 role 的多条消息（marker 填卡字段/世界书，
        # chatHistory 处原位插历史）；否则内置扮演提示。dialogue = 少样本片段 + 历史（真实多轮，不折叠）
        # scene 由 supervisor 那次调用分类后写入 ctx（P2）→ 驱动 scene 条件链；无则空串（只命中无条件链）
        preset_msgs, preset_temp, has_hist, chains_tail, chains_head = _resolve_preset(
            ctx, wb, scene=ctx.get("scene") or "", affinity=affinity, turn=turn,
            history=story_round)
        # P4-roleplay（2026-09-12）：跨轮前缀复用的总前提——把「每轮变化」的块从 system 头
        # 移到尾部 system 消息。静态头（人设/角色卡/世界书/骰点规则/状态更新规则/文风）跨轮
        # 逐字节稳定；动态块（连续性状态/NPC 目标/RAG 召回/表格/directive）后置。
        # 两分支（内置 / 预设）同步拆；内容一字不删，只换位置（红线：位置可动、内容不可动）。
        dynamic_parts: list[str] = []
        base, temp, dialogue = _rp_build_prompt_base(
            ctx, wb=wb, preset_msgs=preset_msgs, preset_temp=preset_temp,
            has_hist=has_hist, story_round=story_round)
        # 世界书**召回段**（每轮重排）落这里：与数据表召回/连续性状态同层，都在 history 之后。
        # 锚点段（constant/keyword）留在世界书块内（history 之前）跨轮稳定 ⇒ 前缀不被召回打断。
        _rp_seed_dynamic_parts(ctx, wb_recall, dynamic_parts)
        # 头部思维链随 system 头（框定推理框架）
        base = _rp_append_static_blocks(ctx, base, chains_head)
        # 阶段 B：注入 state 块 + 记忆召回 + 已裁定自主行动 + 搭车状态指令（deps 存在才挂）
        if deps is not None:
            base = _rp_inject_agency_context(
                ctx, deps=deps, turn=turn, st_block=st_block, text=text, base=base,
                directive=directive, dynamic_parts=dynamic_parts,
            )
            _rp_flush_citation_trace(ctx, trace)  # ② 纪要引用透出（2026-09-18）：引用行进 trace/流式
        # 收口：状态块/纪要召回/裁定指令等在 substitute 之后才生成（且预设分支根本没替换
        # base），可能含 {{user}}/{{char}}（如状态字段名「对{{user}}态度」、快照）。统一在此对
        # 最终**两段**各替换一次，缺省 user 回退「我」（用户没填人设时也不让字面 {{user}} 漏进
        # system→被模型照抄进正文）。P4-roleplay：静态头与动态尾段分别替换，两段都不得漏。
        base, dynamic_text = _rp_substitute_macros(ctx, base, dynamic_parts)
        system = _agent_system(ctx, base)
        # 输出结构模板前置（2026-09-12 用户拍板方案 A + 顺序定稿）：首因位置守约率最高——
        # 模型守约率波动（21:11 写 2726 字思考 / 21:26 零思考）靠尾部纪律压不住，模板放
        # system 最开头给最强 few-shot。顺序=思考 → 状态栏 → 正文（用户定稿）；
        # 命定/登场/检定事件块按剧情穿插正文中间。解析层全部位置无关
        # （extract_status_snapshot 取末块、事件块就地解析），模板是行为引导不是解析契约。
        system = _rp_prefix_output_structure(system)
        # 尾部顺序（P4-roleplay）：[动态块][思维链 tail][输出纪律][…][用户安排][本轮 user]。
        # 动态块是每轮变化的块，落在历史之后 → 静态头（system）逐字节跨轮稳定。
        tail_msgs = _rp_build_tail_messages(ctx, dynamic_text, chains_tail, text)
        messages = [{"role": "system", "content": system}, *dialogue, *tail_msgs,
                    {"role": "user", "content": text}]
        compiled_prompt = prompt_compiler.compile_messages(
            messages,
            provider_profile=ctx.get("provider_profile") or "openai_compatible",
        )
        wire_messages = compiled_prompt.messages
        wire_messages = _resume_interrupted_messages(wire_messages)
        roleplay_sampling = _roleplay_sampling(ctx)
        run_trace.emit(ctx, "model.request", agent="roleplay", model=ctx["chat_model"],
                       messages=wire_messages, preset=ctx.get("preset_name") or "",
                       temperature=temp, provider_profile=compiled_prompt.provider_profile,
                       prompt_manifest=compiled_prompt.manifest, **roleplay_sampling)
        def _generated(value: str) -> None:
            run_trace.emit(ctx, "model.response", agent="roleplay", content=value)
            think = _probe_think(value)
            if think:
                repo = ctx.get("repo_id") or ctx.get("thread_id") or "?"
                _probe.info("🧠[AI思考] repo=%s:\n%s", repo, think)

        finalization = roleplay_turn.TurnFinalizationHooks(
            writeback=lambda item, events: _agency_writeback(
                item.ctx, item.deps, item.reply, item.turn, item.affinity,
                item.lost, events, item.text,
            ),
            apply_output=lambda value: _apply_regex(
                ctx, value, Placement.AI_OUTPUT, is_prompt=False, depth=0,
            ),
            anchor_offset=_illustration_anchor_offset,
            emit_ready=_emit_roleplay_ready,
            maintain=lambda item, value, events: _agency_maintenance_async(
                item.ctx, item.deps, value, item.turn, events,
            ),
        )
        return roleplay_turn.execute_turn(
            roleplay_turn.TurnExecution(
                ctx=ctx, text=text, trace=trace, streamed=streamed,
                deps=deps, turn=turn, affinity=affinity, lost=lost,
                selfheal_attempts=ctx.get("selfheal_attempts", roleplay_turn.SELFHEAL_MAX_ATTEMPTS),
                # 生成字数下限（智能体参数预设→剧情推进）：0=关闭；>0 时正文低于该字数
                # 触发**轮内续写**（同一条消息接着写，非多轮）——对齐 SillyTavern auto-continue。
                story_min_chars=_builtin(ctx, "roleplay", "storyMinChars", 0),
            ),
            roleplay_turn.TurnExecutionHooks(
                generate=lambda: _chat_with_optional_stream(
                    ctx, wire_messages, temperature=temp,
                    **roleplay_sampling,
                ),
                generated=_generated,
                notify=lambda message: _notify_stream_trace(ctx, message),
                continue_generate=lambda partial: _chat_with_optional_stream(
                    ctx, _roleplay_continuation_messages(
                        wire_messages, partial,
                        think_truncated=roleplay_turn.think_truncated(partial),
                    ),
                    temperature=temp, **roleplay_sampling,
                ),
                # 剧情续写（2026-09-21）：结构合格但剧情没写完时的补写通道。与截断续写
                # 分开接线——两者的指令语义不同（截断＝闭合标签；剧情＝接着往下写）。
                continue_story=lambda partial: _chat_with_optional_stream(
                    ctx, _roleplay_continuation_messages(
                        wire_messages, partial, story_incomplete=True,
                    ),
                    temperature=temp, **roleplay_sampling,
                ),
                finalization=finalization,
            ),
        )
    except roleplay_turn.RoleplayInterrupted as e:
        # 用户主动停止（2026-09-04 事故修复）：中断≠失败。已流式的半成品保留在气泡
        # （streamed=True 时 graph 层不覆盖正文），这里只打 interrupted 标记不报「失败」。
        run_trace.emit(ctx, "agent.interrupted", agent="roleplay", reason=str(e))
        return {"result_text": "", "trace": trace + [f"⏹ {e}"],
                "_streamed_result": streamed, "_interrupted": True}
    except Exception as e:  # noqa: BLE001
        run_trace.emit(ctx, "agent.error", agent="roleplay", error=str(e))
        return {"result_text": f"扮演失败：{e}", "trace": trace,
                "_streamed_result": streamed}


def clarify_node(state: AgentState) -> dict:
    return {"result_text": "本次意图有多种合理理解，请选择要执行的功能。"}


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
