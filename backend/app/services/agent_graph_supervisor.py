"""Supervisor 路由域：语义分派（有卡纯文本零 LLM 直达 / 强命令直达 / 模糊表达走 LLM）。

从 agent_graph.py 按域迁出（方案 B，2026-10-23）：搬移块 sha1 逐字节一致，
旧名经 agent_graph re-export 零破坏。向下 import agent_graph_state（AgentState 底座）。"""

from __future__ import annotations

from app.services.agent_contracts import CtxLike

import re

from app.services import builtin_agents, llm as _llm, agent_context, run_trace, scene_classify, structured_output, agent_plugins as _agent_plugins, plan_compiler
from app.services.agent_agency import _builtin, _builtin_sampling, _has_card
from app.services.agent_context_assembly import _tool_on
from app.services.agent_fabric import _RECIPE_OFFER_RE, _recent_user_texts, _recipe_offer_pending, _task_session_continuation
from app.services.agent_graph_state import AgentState
from app.services.agent_support import _proxy_kw
from app.services.structured_contracts import SupervisorDecision

import logging

_LOG = logging.getLogger("uvicorn.error")



# 内置 Agent 默认提示词由 builtin_agents 单一属主（③ 可被用户覆盖）；别名保留旧引用不破坏。
_SUPERVISOR_SYSTEM = builtin_agents.SUPERVISOR_SYSTEM


def _supervisor_route(text: str, image_count: int, ctx: CtxLike) -> tuple[str, bool, list[str], str]:
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
        _LOG.debug("agent_graph_supervisor::_supervisor_route 兜底异常: %s", exc)
        run_trace.emit(ctx, "agent.error", agent="supervisor", error=str(exc))
    return "answer", True, [], ""

# route → 对应工具开关键（自定义预设可关掉某能力，关掉则回退 answer）
# Agent 插件注册表（P6）：路由标签/描述/工具开关从 agent_plugins 生成，不再硬编码。
_ROUTE_TOOL = {p.route: p.tool_key for p in _agent_plugins.all_plugins() if p.tool_key}

_ROUTE_LABELS = {p.route: p.label for p in _agent_plugins.all_plugins()}

_ROUTE_DESCRIPTIONS = {p.route: p.description for p in _agent_plugins.all_plugins()}

def _explicit_card_route(text: str, ctx: CtxLike) -> str:
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

def _route_available(route: str, has_images: bool, ctx: CtxLike) -> bool:
    return _agent_plugins.route_available(
        route, has_images=has_images, has_card=_has_card(ctx),
        has_mcp=bool(ctx.get("has_mcp")), agent_cfg=ctx.get("agent_cfg"),
        tool_on=_tool_on)

def _available_routes(has_images: bool, ctx: CtxLike) -> list[str]:
    return [route for route in _ROUTE_LABELS if _route_available(route, has_images, ctx)]

def _route_choice_options(
    route: str, alternatives: list[str], has_images: bool, ctx: CtxLike,
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

def _route_choice_payload(ctx: CtxLike, options: list[dict]) -> dict:
    message_id = str(ctx.get("message_id") or "")
    return {
        "id": f"route-choice-{message_id}" if message_id else "route-choice",
        "messageId": message_id,
        "userMessageId": str(ctx.get("user_message_id") or ""),
        "status": "pending",
        "options": options,
    }

def supervisor_node(state: AgentState) -> dict:
    ctx: CtxLike = state.get("_ctx", {})
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
