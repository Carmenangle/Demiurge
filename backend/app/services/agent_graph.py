"""Supervisor 多 Agent 系统（LangGraph 手写 StateGraph）。

范式：无卡或带附件请求由 supervisor 判用户意图；有卡纯文本直达 Roleplay，明确强执行命令
用零 LLM 规则分派。专家执行完把结果写回 state；遗留 ReAct 大脑只作为工具专家 Adapter。

分派原则：Supervisor 处理模糊/多能力请求；角色卡纯文本避免重复上传历史。
Supervisor 可使用独立快模型，专家使用主模型；单专家任务直连 END，不做二次判断。
"""
from __future__ import annotations

import json
import logging
import re
from typing import TypedDict, Iterator

from app.services import agent_context, builtin_agents, edit_agent, generation_approval, generation_store, roleplay_turn, run_trace, scene_classify, tool_agent_adapter
from app.services import llm as _llm
from app.services.agent_contracts import RunContext

# 探测日志：会话输入 / AI 思考(<think>) / RAG 召回，输出到 uvicorn 控制台（仅开发可见，不推前端）。
_probe = logging.getLogger("uvicorn.error")


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
    result_text: str               # 专家产出的文本回复
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


def _proxy_kw(ctx: dict, key: str = "chat_proxy") -> dict:
    value = (ctx.get(key, "") or "").strip()
    return {"proxy": value} if value else {}

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
        reply = chat_fn(ctx["chat_base"], ctx["chat_key"], model,
                        system, user, temperature=sup_temp,
                        **_proxy_kw(ctx),
                        **_builtin_sampling(ctx, "supervisor"))
        raw = (reply or "").strip()
        run_trace.emit(ctx, "model.response", agent="supervisor", content=raw)
        try:
            json_block = re.search(r"\{[\s\S]*\}", raw)
            payload = json.loads(json_block.group(0) if json_block else raw)
            route = str(payload.get("route") or "").strip().lower()
            confidence = str(payload.get("confidence") or "high").strip().lower()
            raw_alternatives = payload.get("alternatives") or []
            alternatives = [str(item).strip().lower() for item in raw_alternatives] \
                if isinstance(raw_alternatives, list) else []
            scene = scene_classify.normalize_scene(str(payload.get("scene") or ""))
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
_ROUTE_TOOL = {"generate": "generate_image", "img2img": "image_to_image",
               "analyze": "analyze_image", "inspire": "search_inspiration",
               "video": "generate_video"}
_ROUTE_LABELS = {
    "answer": "继续对话",
    "roleplay": "剧情扮演",
    "generate": "生成图片",
    "img2img": "参考图生图",
    "analyze": "反推提示词",
    "video": "生成视频",
    "inspire": "查找灵感",
    "tool_agent": "调用工具",
    "edit": "编辑作品文件",
}
_ROUTE_DESCRIPTIONS = {
    "answer": "普通对话、问答，以及审查、解释、评价或优化已有内容",
    "roleplay": "沉浸式角色扮演：推进剧情、以角色身份出演对白与叙事",
    "generate": "根据文本生成新图片，或执行无参考图的完整成稿提示词",
    "img2img": "基于本轮图片附件生成、修改或续接新图片",
    "analyze": "从本轮图片附件反推并交付新的可复用提示词文本",
    "video": "生成视频、动画或动图",
    "inspire": "联网查找参考、灵感、流行款式或趋势",
    "tool_agent": "调用已接入的外部工具、接口、文件或数据库能力",
    "edit": "创建角色卡、编写作品脚本、读取和修改当前作品文件并排错",
}


def _has_card(ctx: dict) -> bool:
    """本作品是否关联角色卡（有卡=剧情扮演可用，对话默认走 roleplay）。"""
    return bool((ctx.get("card_name") or "").strip() and (ctx.get("character_dir") or "").strip())


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
    if route not in _ROUTE_LABELS:
        return False
    if route == "answer":
        return True
    if route == "roleplay":
        return _has_card(ctx)
    if route == "generate" and has_images:
        return False
    if route in {"img2img", "analyze"} and not has_images:
        return False
    if route == "tool_agent" and not ctx.get("has_mcp"):
        return False
    tool_key = _ROUTE_TOOL.get(route)
    return not tool_key or _tool_on(ctx.get("agent_cfg"), tool_key)


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
    if ctx.get("workspace_mode") == "edit":
        run_trace.emit(ctx, "agent.completed", agent="supervisor", route="edit", forced=True)
        return {"route": "edit", "trace": state.get("trace", []) + ["📝 进入编辑模式"]}
    # 对话兜底：关联角色卡的作品默认走剧情扮演，否则通用对话。
    chat_default = "roleplay" if _has_card(ctx) else "answer"
    forced_route = str(ctx.get("forced_route") or "").strip().lower()
    if forced_route:
        route = forced_route if _route_available(forced_route, has_images, ctx) else chat_default
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
             "roleplay": "剧情扮演", "answer": "对话"}.get(route, route)
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
    trace = state.get("trace", []) + ["🎬 视频专家执行中…"]
    if (ctx.get("style_template") or "").strip():
        candidate = _styled_prompt(ctx, execution_prompt)
        result = generation_approval.save_prompt_review(ctx, "video", original, candidate, [], "style")
        result["trace"] = trace + result["trace"]
        return result
    return generation_approval.execute_generation(ctx, "video", original, execution_prompt, [], trace)


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
        # 选了自定义风格存档时附加写法指引（与单 agent analyze_image 的 style_hint 对齐）
        style_hint = ""
        if (ctx.get("style_template") or "").strip():
            try:
                from app.services.image_prompt_style import guidance_for
                style_hint = "\n" + guidance_for("", ctx.get("gen_model", ""), ctx["style_template"])
            except Exception:  # noqa: BLE001
                pass
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
        )
        if not data.get("prompt"):
            return {"result_text": "未能从搜索结果提炼出提示词。", "trace": trace}
        card = generation_store.persist_inspiration(ctx["thread_id"], data["query"], data["prompt"], data["tags"], data["sources"])
        return {"result_text": f"已生成灵感卡：{data['prompt'][:80]}…", "insp_cards": [card], "trace": trace}
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
        return {"result_text": reply, "trace": trace, "_streamed_result": streamed}
    except Exception as e:  # noqa: BLE001
        return {"result_text": f"回答失败：{e}", "trace": trace, "_streamed_result": streamed}


# 剧情扮演默认提示词由 builtin_agents 单一属主（③ 可覆盖）；别名保留旧引用不破坏。
_ROLEPLAY_BASE = builtin_agents.ROLEPLAY_BASE


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
        wb = _resolve_worldbook(ctx, text)
        if wb:
            # 世界信息正则（placement 5）：作用于世界书组装后的注入文本
            wb = _apply_regex(ctx, wb, Placement.WORLD_INFO, is_prompt=True, depth=0)
        run_trace.emit(
            ctx, "worldbook.resolved", injected=bool(wb), content=wb or "",
            selected_indices=ctx.get("_selected_worldbook_indices") or [],
            keyword_indices=ctx.get("_keyword_worldbook_indices") or [],
            character_names=ctx.get("_worldbook_character_names") or [],
        )
        first_story_reply = not any(
            item.get("role") == "user" and str(item.get("content") or "").strip()
            for item in (ctx.get("history") or [])
        )
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
        # 能动性子图·准备：算 turn/读好感度/state 注入块（无卡或无 output_dir → deps=None 静默跳过）
        deps, turn, affinity, st_block = _agency_prelude(ctx, text)
        # 阶段 A：世界提案 + 裁判（门控通常关 → directive 空，塌回单次 LLM 零额外成本）
        directive, lost = _agency_propose(ctx, deps, affinity, wb, text)
        # 有激活偏置预设 → 按预设 prompt_order 组装带 role 的多条消息（marker 填卡字段/世界书，
        # chatHistory 处原位插历史）；否则内置扮演提示。dialogue = 少样本片段 + 历史（真实多轮，不折叠）
        # scene 由 supervisor 那次调用分类后写入 ctx（P2）→ 驱动 scene 条件链；无则空串（只命中无条件链）
        preset_msgs, preset_temp, has_hist, chains_tail, chains_head = _resolve_preset(
            ctx, wb, scene=ctx.get("scene") or "", affinity=affinity, turn=turn)
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
                dialogue += _history_messages(ctx)
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
            dialogue = _history_messages(ctx)  # 历史作真实多轮，不再折叠进 user 串
        # 头部思维链随 system 头（框定推理框架）
        if chains_head:
            base += "\n\n" + "\n\n".join(chains_head)
        # 命运骰点规则：注入剧情推进提示词，让主模型在关键博弈点打可审计 <roll>（用户可在编辑器改/清空）
        roll_rule = _builtin(ctx, "roleplay", "rollInstruction", builtin_agents.ROLL_INSTRUCTION)
        if roll_rule and roll_rule.strip():
            base += roll_rule
        # 阶段 B：注入 state 块 + 记忆召回 + 已裁定自主行动 + 搭车状态指令（deps 存在才挂）
        if deps is not None:
            from app.services import roleplay_agency
            repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
            retrieval_query = (agent_context.history_text(ctx)[-600:] + text)
            # 表格+RAG 结合：先从 rag_store 召回知识库条目 + 检索表行（同库同通道）
            rag_text = _rag_recall_text(ctx, repo_id, retrieval_query)
            # Recall 只做检索，不独立调 LLM：往事纪要 + 知识库/检索表候选原样并入
            # GrayWill 主请求，由主模型结合世界书、历史与本轮输入一次判断并生成。
            recall = roleplay_agency.recall_chronicle(
                deps, repo_id=repo_id, query=retrieval_query, rag_text=rag_text,
                actors=[
                    name for name in (ctx.get("illustration_actor_names") or [])
                    if name and name in retrieval_query
                ] or ([ctx.get("card_name")] if ctx.get("card_name") else []),
            )
            if recall:
                base += "\n\n" + recall
                _probe.info("🔎[RAG召回] repo=%s 注入%d字:\n%s", repo_id, len(recall), recall[:800])
                run_trace.emit(ctx, "rag.injected", status="ok", content=recall,
                               char_count=len(recall))
            else:
                _probe.info("🔎[RAG召回] repo=%s 无命中（本轮未注入记忆）", repo_id)
                run_trace.emit(ctx, "rag.injected", status="empty", content="", char_count=0)
            base += st_block + directive + roleplay_agency.state_instruction()
            if getattr(deps, "renderer", None) is not None or ctx.get("comfy_illustrate"):
                from app.services import image_prompt_extract, worldbook_store
                visual_query = (agent_context.history_text(ctx)[-2000:] + "\n" + text).strip()
                visual_profiles = (
                    _card_visual_profiles(ctx, visual_query)
                    if ctx.get("appearance_source") == "character_card"
                    else worldbook_store.repo_visual_profiles(
                        ctx.get("output_dir") or "", repo_id, visual_query,
                    )
                )
                ctx["_illustration_visual_profiles"] = visual_profiles
                base += image_prompt_extract.build_inline_plan_instruction(
                    ctx.get("prompt_profile") or "krea2",
                    visual_profiles,
                )
            # 通用数据表只作只读剧情上下文；更新由正文发出后的独立维护调用完成，
            # 禁止再让主 Roleplay 在正文尾部生成 <表格更新>。
            try:
                from app.services import table_store, table_update
                _tables = table_store.load(ctx.get("output_dir") or "", repo_id)
                should_fill = bool(_tables) and _should_fill(ctx, repo_id, turn)
                turn_tables = table_store.tables_for_turn(_tables, should_fill)
                if turn_tables:
                    base += table_update.table_context(turn_tables)
                    run_trace.emit(
                        ctx, "table.prompt", status="read_only", turn=turn,
                        tables=[t.get("name", "") for t in turn_tables],
                    )
                else:
                    run_trace.emit(
                        ctx, "table.prompt", status="skipped", turn=turn,
                        reason="no_tables" if not _tables else "cadence_not_reached",
                    )
            except Exception as exc:  # noqa: BLE001
                run_trace.emit(ctx, "table.prompt", status="error", turn=turn, error=str(exc))
        # 收口：state 块/纪要召回/裁定指令等在 substitute 之后才拼进 base（且预设分支根本没替换 base），
        # 可能含 {{user}}/{{char}}（如状态字段名「对{{user}}态度」、快照）。统一在此对最终 base 再替换一次，
        # 缺省 user 回退「我」（用户没填人设时也不让字面 {{user}} 漏进 system→被模型照抄进正文）。
        from app.services import preset_store as _ps2
        base = _ps2.substitute_macros(base, {
            "char_name": (ctx.get("card_name") or "").strip(),
            "user_name": (ctx.get("user_name") or "").strip(),
        })
        system = _agent_system(ctx, base)
        # 尾部思维链作独立 system 消息落在历史之后、本轮 user 之前 → 离生成点最近，遵守最严
        tail_msgs = [{"role": "system", "content": c} for c in chains_tail]
        messages = [{"role": "system", "content": system}, *dialogue, *tail_msgs,
                    {"role": "user", "content": text}]
        wire_messages = _llm.prepare_messages(ctx["chat_model"], messages)
        run_trace.emit(ctx, "model.request", agent="roleplay", model=ctx["chat_model"],
                       messages=wire_messages, preset=ctx.get("preset_name") or "",
                       temperature=temp)
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
            maintain=lambda item, value, events: _agency_maintenance(
                item.ctx, item.deps, value, item.turn, events,
            ),
        )
        return roleplay_turn.execute_turn(
            roleplay_turn.TurnExecution(
                ctx=ctx, text=text, trace=trace, streamed=streamed,
                deps=deps, turn=turn, affinity=affinity, lost=lost,
            ),
            roleplay_turn.TurnExecutionHooks(
                generate=lambda: _chat_with_optional_stream(
                    ctx, wire_messages, temperature=temp,
                    **_builtin_sampling(ctx, "roleplay"),
                ),
                generated=_generated,
                finalization=finalization,
            ),
        )
    except Exception as e:  # noqa: BLE001
        return {"result_text": f"扮演失败：{e}", "trace": trace,
                "_streamed_result": streamed}


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
    """从完整会话快照计算下一剧情回合；无快照时才回退已裁剪上下文。"""
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
        return roleplay_agency.narrative_directive(verdicts, {}), roleplay_agency.agency_lost(verdicts)
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
            processed = table_store.tables_for_turn(tables, _should_fill(ctx, repo_id, turn))
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
        processed = table_store.tables_for_turn(
            tables, False if short_reply else _should_fill(ctx, repo_id, turn),
        )
        manual_table_fill.mark_processed(
            output_dir, repo_id, [str(table.get("uid") or "") for table in processed], turn,
        )
    run_trace.emit(ctx, "table.writeback", status="ok", ops=effective_ops, applied=applied)
    return clean


def _visible_roleplay_text(reply: str) -> str:
    """后处理失败时仍剥离内部控制块，禁止把状态、表格和生图提示词暴露给用户。"""
    clean = reply
    try:
        from app.services import image_prompt_extract

        clean, _ = image_prompt_extract.extract_illustration_plan(clean)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services import roleplay_agency

        clean, _ = roleplay_agency.parse_state_block(clean)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services import table_update

        clean, _ = table_update.parse_table_block(clean)
    except Exception:  # noqa: BLE001
        pass
    return clean.strip()


def _agency_writeback(ctx: dict, deps, reply: str, turn: int, affinity,
                      lost: bool, rag_events: list | None = None,
                      user_text: str = "") -> tuple[str, list, dict]:
    """阶段 C+D：剥离 <状态更新> 写回 → 判插画。返回（去块正文, image_recs, illustrate_req）。

    illustrate_req：comfy_illustrate 时高潮点产出的出图请求 {prompt}；前端据本地预设模板走异步 ComfyUI 闭环。
    非 comfy 路径为空 dict。rag_events：可选，收集 RAG 创建（纪要/知识库）状态供前端弹窗。"""
    try:
        from app.services import character_state, roleplay_agency
        from app.services import image_prompt_extract, scene_classify
        from app.services.regex_engine import Placement
        repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
        card_name = ctx.get("card_name") or ""
        clean, illustration_plan = image_prompt_extract.extract_illustration_plan(reply)
        # 抽 <status> 快照（不剥，留正文供前端正则渲染）+ 剥 <状态更新> 小数值 JSON
        snapshot = roleplay_agency.extract_status_snapshot(clean)
        clean, raw = roleplay_agency.parse_state_block(clean)
        raw = [*raw, *(ctx.pop("_agency_goal_deltas", []) or [])]
        before, after = roleplay_agency.writeback(
            deps, repo_id=repo_id, card_name=card_name, raw_deltas=raw, turn=turn,
            snapshot=snapshot)
        run_trace.emit(ctx, "state.writeback", raw_deltas=raw, snapshot=snapshot,
                       affinity_before=before, affinity_after=after)
        st = character_state.load_state(deps.state_base, repo_id, card_name)
        if not snapshot:
            previous_snapshot = getattr(getattr(st, "快照", None), "text", "")
            clean = roleplay_agency.ensure_status_snapshot(clean, previous_snapshot)
        # 旧版/异常模型仍可能输出表格块：这里只清洗，实际更新统一交给独立维护调用。
        from app.services import table_update
        had_legacy_table_block = table_update.has_table_block(clean)
        clean, _legacy_ops = table_update.parse_table_block(clean)
        if had_legacy_table_block:
            run_trace.emit(ctx, "table.writeback", status="legacy_ignored")
        # 阶段 D：插画（renderer=None 时 maybe_illustrate 直接返回 None）；用去块正文当段落
        scene = ctx.get("scene") or ""
        wardrobe = roleplay_agency._narr(st, "衣着")
        locale = roleplay_agency._narr(st, "所在")
        # 插画提示词直接由正文 + 已有视觉锚组装，不再额外调用一次聊天模型。
        visible_story = image_prompt_extract.visible_narrative_text(clean)
        local_scene = scene_classify.infer_scene(
            "\n".join((user_text, visible_story)),
        )
        from app.services import scene_illustration
        local_scene_fallback = (
            not illustration_plan and local_scene in ("nsfw", "climax")
        )
        (encounter_anchor, encounter_narrative, encounter_actors,
         encounter_facts) = scene_illustration.encounter_illustration_context(clean)
        character_encounter = bool(
            not illustration_plan and encounter_anchor
            and (deps.renderer is not None or ctx.get("comfy_illustrate"))
        )
        first_story_reply = not any(
            item.get("role") == "user" and str(item.get("content") or "").strip()
            for item in (ctx.get("history") or [])
        )
        at_climax = bool(illustration_plan) or (
            bool(visible_story) and (
                lost or scene in ("nsfw", "climax")
                or local_scene_fallback or first_story_reply or character_encounter
            )
        )
        prompt_override, profile_prompt, motion, actors = "", "", 0, []
        image_rating = (
            "nsfw" if scene in ("nsfw", "climax")
            or local_scene in ("nsfw", "climax") else "sfw"
        )
        if illustration_plan:
            prompt_override = _apply_regex(
                ctx, illustration_plan["prompt"], Placement.IMAGE_PROMPT, is_prompt=True).strip()
            from app.services import image_prompt_profiles
            profile_prompt = image_prompt_profiles.normalize_inline(
                ctx.get("prompt_profile") or "krea2",
                illustration_plan.get("profile_prompt", ""),
                {
                    "rating": image_rating,
                    "narrative": visible_story,
                    "draft_prompt": illustration_plan.get("prompt", ""),
                },
            )
            motion = illustration_plan["motion"]
            actors = illustration_plan["actors"]
        elif ctx.get("comfy_illustrate") and local_scene_fallback:
            prompt_override = image_prompt_extract.build_fallback_content_tags(
                "\n".join((user_text, visible_story)),
            )
            motion = image_prompt_extract.infer_motion(visible_story)
            actors = [card_name] if card_name else []
        elif (deps.renderer is not None or ctx.get("comfy_illustrate")) and at_climax:
            prompt_override, motion, actors = _build_image_prompt(
                ctx, paragraph=encounter_narrative if character_encounter else visible_story,
                appearance=_illustration_appearance(ctx),
                wardrobe=wardrobe, locale=locale)
            if character_encounter:
                actors = encounter_actors
        # comfy_illustrate：不同步 render，把 prompt + motion + actors 作为出图请求返回，
        # 前端据 motion 智能选图/视频、据 actors 按角色选 LoRA/底图，走异步 ComfyUI 闭环。
        if ctx.get("comfy_illustrate"):
            # 提取模型失败/拒答/返回坏 JSON 时仍要发请求：用既有纯逻辑组装器把正文、
            # 外观和动态状态拼成降级提示词。旧代码只在 prompt_override 非空时发事件，
            # 与上方“失败回退中文裸拼接”的设计相反，会让整条 ComfyUI 链静默消失。
            request_actors = actors or ([card_name] if card_name else [])
            request_prompt = prompt_override.strip()
            prompt_source = "extracted"
            if at_climax and not request_prompt:
                request_prompt = scene_illustration.build_scene_request(
                    paragraph=encounter_narrative if character_encounter else visible_story,
                    appearance=_illustration_appearance(ctx),
                    wardrobe=wardrobe,
                    locale=locale,
                    actors=request_actors,
                ).prompt
                prompt_source = "fallback"
            fallback_anchor = ""
            if character_encounter:
                fallback_anchor = encounter_anchor
            elif visible_story and (local_scene_fallback or first_story_reply) and not illustration_plan:
                fallback_anchor = scene_illustration.fallback_illustration_anchor(clean)
            planned_anchor = illustration_plan.get("anchor", "")
            requested_anchor = planned_anchor or fallback_anchor
            if illustration_plan:
                requested_anchor = scene_illustration.resolve_illustration_anchor(
                    clean, requested_anchor,
                )
                if requested_anchor != planned_anchor:
                    # 主模型把动作峰值退化成静态收束时，废弃同源的错误 Profile；
                    # 前端会用纠正后的 narrative 重新生成，不让肖像提示词继续提交。
                    profile_prompt = ""
            scene_spec = {
                "narrative": encounter_narrative if character_encounter else (
                    scene_illustration.illustration_scene_excerpt(
                        visible_story, requested_anchor,
                    )
                ),
                "draft_prompt": request_prompt,
                "appearance": _illustration_appearance(ctx),
                "wardrobe": wardrobe,
                "locale": locale,
                "actors": request_actors,
                "rating": image_rating,
                "aspect_ratio": illustration_plan.get("aspect_ratio") or (
                    "4:3" if character_encounter else "2:3"
                ),
            }
            if ctx.get("appearance_source") in {"worldbook", "character_card"}:
                scene_spec["appearance_source"] = ctx.get("appearance_source")
            if character_encounter:
                scene_spec["encounter"] = encounter_facts
            if illustration_plan.get("art_direction"):
                scene_spec["art_direction"] = illustration_plan["art_direction"]
            from app.services import image_prompt_profiles
            profile_negative = image_prompt_profiles.negative_prompt(
                ctx.get("prompt_profile") or "krea2", scene_spec,
            )
            if profile_negative:
                scene_spec["negative_prompt"] = profile_negative
            if profile_prompt:
                scene_spec.update({
                    "profile": ctx.get("prompt_profile") or "krea2",
                    "profile_prompt": profile_prompt,
                })
            request_prompt = profile_prompt or image_prompt_extract.format_comfy_prompt(request_prompt)
            illustrate_req = (
                {"prompt": request_prompt, "motion": motion, "actors": request_actors,
                 "anchor": requested_anchor, "scene_spec": scene_spec,
                 "allow_anchor_fallback": (
                     bool(visible_story) and (
                         local_scene_fallback or first_story_reply or character_encounter
                     )
                 ) and not illustration_plan}
                if at_climax and (request_prompt or scene_spec["narrative"]) else {}
            )
            run_trace.emit(
                ctx,
                "illustration.request",
                status="emitted" if illustrate_req else "skipped",
                reason=("main_profile" if profile_prompt and illustrate_req else
                        "main_plan" if illustration_plan and illustrate_req else
                        "character_encounter" if character_encounter and illustrate_req else
                        "local_scene_fallback" if local_scene_fallback and illustrate_req else
                        "first_story_reply" if first_story_reply and illustrate_req else
                        prompt_source if illustrate_req else
                        "scene_not_triggered" if not at_climax else "empty_prompt"),
                scene=scene,
                inferred_scene=local_scene,
                actor_count=len(request_actors),
                prompt_chars=len(request_prompt),
            )
            return clean, [], illustrate_req
        illo = roleplay_agency.maybe_illustrate(
            deps, paragraph=clean, appearance=_illustration_appearance(ctx),
            wardrobe=wardrobe, locale=locale,
            actors=actors or ([card_name] if card_name else []), before=before, after=after,
            turn=turn, cadence=0, explicit=bool(illustration_plan), lost=lost,
            scene=scene, prompt_override=prompt_override,
            character_encounter=character_encounter)
        if illo:
            rec = {"id": f"illo-{repo_id}-{turn}", "url": illo["url"], "caption": illo["caption"]}
            return clean, [rec], {}
        return clean, [], {}
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "illustration.pipeline", status="error", error=str(exc))
        return _visible_roleplay_text(reply), [], {}


def _illustration_anchor_offset(reply: str, request: dict) -> int | None:
    """在最终显示正文中定位插画槽；本地兜底 anchor 被正则改写时重新选高潮段。"""
    from app.services import scene_illustration

    offset = scene_illustration.illustration_anchor_offset(
        reply, str(request.get("anchor") or ""),
    )
    if offset is not None or not request.get("allow_anchor_fallback"):
        return offset
    final_anchor = scene_illustration.fallback_illustration_anchor(reply)
    if not final_anchor:
        return None
    return scene_illustration.illustration_anchor_offset(reply, final_anchor)


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
    return True


def _agency_maintenance(ctx: dict, deps, clean: str, turn: int,
                        rag_events: list | None = None) -> None:
    """正文/插画已发出后的记忆维护；失败不得改写已完成正文。"""
    try:
        from app.services import roleplay_agency
        repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
        card_name = ctx.get("card_name") or ""
        _table_maintenance(ctx, repo_id, clean, turn)
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
            deps, window_text=window,
            chat_base=ctx["chat_base"], chat_key=ctx["chat_key"], chat_model=ctx["chat_model"],
            events=rag_events, proxy=ctx.get("chat_proxy", ""))
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "memory.maintenance", status="error", error=str(exc))


def _table_maintenance(ctx: dict, repo_id: str, clean: str, turn: int) -> None:
    """正文发出后独立生成并写回表格 ops；响应只进 Trace，不进入对话。"""
    try:
        from app.services import table_store, table_update

        output_dir = ctx.get("output_dir") or ""
        tables = table_store.load(output_dir, repo_id)
        if not tables:
            run_trace.emit(ctx, "agent.skipped", agent="table_maintenance", reason="no_tables")
            return
        cfg = table_store.load_config(output_dir, repo_id)
        scheduled = _should_fill(ctx, repo_id, turn)
        if len(clean) < int(cfg.get("minReplyLen", 0)):
            scheduled = False
        selected = table_store.tables_for_turn(tables, scheduled)
        system = table_update.maintenance_instruction(selected)
        if not system:
            run_trace.emit(ctx, "agent.skipped", agent="table_maintenance", reason="cadence_not_reached")
            return
        user_input = str(ctx.get("message") or "").strip()
        user = f"【本轮用户输入】\n{user_input}\n\n【已生成剧情正文】\n{clean}".strip()
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
        ops = table_update.parse_maintenance_ops(raw)
        if ops is None:
            run_trace.emit(ctx, "agent.error", agent="table_maintenance",
                           error="invalid_or_truncated_json")
            return
        _apply_table_ops(ctx, repo_id, clean, ops, turn)
        run_trace.emit(ctx, "agent.completed", agent="table_maintenance", op_count=len(ops))
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "agent.error", agent="table_maintenance", error=str(exc))


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


def _embed_cfg(ctx: dict):
    """从 ctx 取嵌入配置 → EmbedConfig；缺 base/model → None（无法走语义检索/入库）。"""
    embed_base = (ctx.get("embed_base") or "").strip()
    embed_model = (ctx.get("embed_model") or "").strip()
    if not (embed_base and embed_model):
        return None
    from app.services.rag_backend import EmbedConfig
    return EmbedConfig(
        embed_base, ctx.get("embed_key", ""), embed_model,
        proxy=ctx.get("embed_proxy", ""),
    )


def _rag_recall_text(ctx: dict, repo_id: str, query: str, k: int = 6) -> str:
    """从 rag_store 召回本仓库知识库条目 + 检索表行（kind!=generation），拼成候选文本。

    这是"表格+RAG 结合"的读侧接缝：curator 知识与检索表行同库同通道，一起按 query 召回。
    缺嵌入配置/无命中/异常 → 空串（caller 回退纯纪要召回，不阻断）。
    """
    cfg = _embed_cfg(ctx)
    if cfg is None or not (repo_id and query.strip()):
        run_trace.emit(ctx, "rag.retrieve", status="skipped",
                       reason="missing_embedding_config" if cfg is None else "missing_query",
                       query=query)
        return ""
    try:
        from app.services import rag_store
        hits = rag_store.retrieve_with_trace(
            repo_id, cfg, query, k=k, include_system=False)
    except Exception as exc:  # noqa: BLE001  召回失败不阻断叙述
        run_trace.emit(ctx, "rag.retrieve", status="error", query=query, error=str(exc))
        return ""
    run_trace.emit(ctx, "rag.retrieve", status="ok", query=query, hit_count=len(hits), hits=hits)
    return "\n".join(
        f"- {hit.get('content', '')}" for hit in hits if (hit.get("content") or "").strip()
    )


def _reindex_retrieval_tables(ctx: dict, repo_id: str, tables: list) -> None:
    """把 mode=retrieval 的表的当前行重灌进 RAG（表格写回后调用）。缺嵌入配置则跳过。"""
    cfg = _embed_cfg(ctx)
    if cfg is None or not repo_id:
        return
    try:
        from app.services import rag_store, table_store
        for t in table_store.retrieval_tables(tables):
            texts = [table_store.row_text(t, r) for r in (t.get("rows") or [])]
            rag_store.index_table_rows(repo_id, cfg, t.get("uid", ""), t.get("name", ""), texts)
    except Exception:  # noqa: BLE001  索引失败不阻断叙述
        pass


def _curator_index_fn(ctx: dict, repo_id: str):
    """构造条目维护 Agent 的写库闭包 (text,title)→写入本仓库 RAG 知识库。缺 embed 配置 → None（不写）。"""
    cfg = _embed_cfg(ctx)
    if cfg is None or not repo_id:
        return None

    def _index(text: str, title: str):
        from app.services import rag_store
        result = rag_store.index_document(repo_id, cfg, text, title)
        run_trace.emit(ctx, "rag.write", source="curator", title=title, content=text, result=result)
        return result
    return _index


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
    g.set_entry_point("supervisor")
    # 条件边：按 supervisor 判出的 route 跳到对应专家
    g.add_conditional_edges("supervisor", lambda s: s.get("route", "answer"),
                            {"generate": "generate", "video": "video", "img2img": "img2img",
                             "analyze": "analyze", "inspire": "inspire",
                             "tool_agent": "tool_agent", "answer": "answer",
                             "edit": "edit", "roleplay": "roleplay", "clarify": "clarify"})
    # 单专家任务：干完直接 END，不回 supervisor 二次判断（慢中转下省一次往返）
    for n in ("generate", "video", "img2img", "analyze", "inspire", "tool_agent", "edit", "answer", "roleplay", "clarify"):
        g.add_edge(n, END)
    return g.compile()


_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def _resolve_agent_cfg(agent_id: str) -> dict | None:
    """读自定义 Agent 预设：空 agent_id / 查不到 → None（走内置默认，与单 agent 一致）。"""
    if not (agent_id or "").strip():
        return None
    try:
        from app.services import agent_store
        return agent_store.get_agent(agent_id)
    except Exception:  # noqa: BLE001
        return None


def _resolve_skills(agent_cfg: dict | None) -> list[str]:
    """技能提示词片段：有预设按其 skillIds（空=不用），无预设用全部已启用（与单 agent 一致）。"""
    try:
        from app.services import skills_store
        if agent_cfg is not None:
            return skills_store.fragments_by_ids(agent_cfg.get("skillIds") or [])
        return skills_store.enabled_prompt_fragments()
    except Exception:  # noqa: BLE001
        return []


def _render_user_persona(ctx: dict) -> str:
    """无预设扮演时组装用户人设片段（有预设走 personaDescription marker，不重复注入）。
    名+描述任一非空即渲染，让角色知道'用户是谁'。"""
    name = (ctx.get("user_name") or "").strip()
    desc = (ctx.get("user_persona") or "").strip()
    if not (name or desc):
        return ""
    head = f"【用户扮演（{name}）】" if name else "【用户扮演】"
    return head + ("\n" + desc if desc else "")


def _bound_card_names(ctx: dict) -> list[str]:
    names = [str(name).strip() for name in (ctx.get("card_names") or []) if str(name).strip()]
    opening = str(ctx.get("opening_card_name") or ctx.get("card_name") or "").strip()
    if opening and opening not in names:
        names.insert(0, opening)
    return list(dict.fromkeys(names))


_CHARACTER_DEPARTURE = re.compile(
    r"离开|离场|退出|告辞|走远|远去|消失|不在|已经走了|已走|返回(?:自己的|原来的)?(?:房间|住处|领地)",
)
_CHARACTER_RETURN = re.compile(r"回来|回到|返回现场|重新出现|进入|走进|来到|抵达|仍在|还在|留下")
_NEGATED_DEPARTURE = re.compile(r"没有离开|并未离开|未离开|不曾离开|没有走|并未走")


def _recent_character_context(ctx: dict) -> str:
    """角色回退只看最近一条 AI 剧情，避免更早楼层角色持续滞留。"""
    for item in reversed(ctx.get("history") or []):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            return content[-2000:]
    return ""


def _mentioned_bound_names(names: list[str], text: str) -> list[str]:
    """角色名有包含关系时优先最长实体；独立出现的短名仍保留。"""
    spans: list[tuple[int, int, str]] = []
    for name in names:
        start = 0
        while start < len(text):
            index = text.find(name, start)
            if index < 0:
                break
            spans.append((index, index + len(name), name))
            start = index + len(name)
    accepted: list[tuple[int, int, str]] = []
    for span in sorted(spans, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(span[0] < other[1] and other[0] < span[1] for other in accepted):
            continue
        accepted.append(span)
    matched = {name for _start, _end, name in accepted}
    return [name for name in names if name in matched]


def _active_fallback_names(names: list[str], text: str) -> list[str]:
    """从最近剧情按角色最后一次出现的分句排除明确离场者。"""
    selected: list[str] = []
    for name in _mentioned_bound_names(names, text):
        index = text.rfind(name)
        if index < 0:
            continue
        tail = text[index + len(name):]
        clause = re.split(r"[，,。！？!?；;\n]", tail, maxsplit=1)[0][:80]
        departure = _CHARACTER_DEPARTURE.search(clause)
        returned = _CHARACTER_RETURN.search(clause)
        negated = _NEGATED_DEPARTURE.search(clause)
        if departure and not negated and (not returned or returned.start() < departure.start()):
            continue
        selected.append(name)
    return selected


def _card_source(ctx: dict, selected_name: str = "") -> tuple[str, str]:
    """作品用卡/世界书/正则的读取 base：**快照优先**。

    新建作品时卡已快照进作品文件夹（<outputDir>/<卡名>/角色卡/）；命中则运行时读快照——
    改源库的卡不回灌已建作品（快照隔离）。无快照（存量作品/未快照）→ 回退源库 characterDir。
    返回 (base, card_name)；两值任一空表示无卡，调用方各自处理。
    """
    card_name = selected_name or ctx.get("opening_card_name") or ctx.get("card_name") or ""
    character_dir = ctx.get("character_dir") or ""
    if not card_name:
        return character_dir, card_name
    try:
        from app.services import character_store
        snap = character_store.repo_card_base(
            ctx.get("output_dir") or "", ctx.get("repo_id") or "", card_name,
        )
        if snap:
            return snap, card_name
    except Exception:  # noqa: BLE001
        pass
    return character_dir, card_name


def _apply_work_persona(ctx) -> None:
    """作品绑定的用户人设**快照优先**：命中 <outputDir>/<卡名>/persona.json 则覆盖 ctx 的
    user_name/user_persona（改设置里的人设不回灌已建作品）；无快照保留前端透传值。就地改写。"""
    # 仓库显式绑定了人设（前端标 persona_bound）→ 用前端透传值，不被作品快照覆盖
    if ctx.get("persona_bound"):
        return
    card_name = ctx.get("card_name") or ""
    output_dir = ctx.get("output_dir") or ""
    if not (card_name and output_dir):
        return
    try:
        from app.services import character_store
        snap = character_store.read_work_persona(output_dir, card_name)
    except Exception:  # noqa: BLE001
        return
    if not isinstance(snap, dict):
        return
    ctx.user_name = str(snap.get("name") or "")
    ctx.user_persona = str(snap.get("content") or "")


def _resolve_persona(character_dir: str, card_name: str) -> str:
    """按作品关联的角色卡组装 persona 系统片段。无卡/读不到 → 空串（回退通用对话）。

    character_dir 由调用方经 _card_source(ctx) 得到（快照优先，回退源库）。
    """
    if not ((character_dir or "").strip() and (card_name or "").strip()):
        return ""
    try:
        from app.services import character_card, character_store
        card = character_store.read_card(character_dir, card_name)
        return character_card.build_persona_system(card) if card else ""
    except Exception:  # noqa: BLE001
        return ""


def _resolve_personas(
    ctx: dict, query: str = "", *, opening_only: bool = False, fallback_query: str = "",
    worldbook_names: list[str] | None = None,
) -> str:
    """只注入本轮出场角色的非空 description；首轮固定为开场卡。"""
    names = _bound_card_names(ctx)
    if opening_only:
        opening = str(ctx.get("opening_card_name") or ctx.get("card_name") or "").strip()
        selected = [opening] if opening in names else []
    else:
        explicit = set(worldbook_names or [])
        direct = set(_mentioned_bound_names(names, query))
        selected = [name for name in names if name in direct or name in explicit]
        if not selected and fallback_query:
            selected = _active_fallback_names(names, fallback_query)

    profiles: list[str] = []
    injected_names: list[str] = []
    try:
        from app.services import character_store
        for name in selected:
            base, card_name = _card_source(ctx, name)
            card = character_store.read_card(base, card_name) if base and card_name else None
            description = str((card or {}).get("description") or "").strip()
            if not description:
                continue
            profiles.append(f"【角色：{card_name}】\n{description}")
            injected_names.append(card_name)
    except Exception:  # noqa: BLE001
        profiles = []
        injected_names = []
    ctx["_selected_persona_names"] = injected_names
    if not profiles:
        return ""
    selection = (
        "【本轮角色卡描述】只按角色名使用下列实际出场角色的描述；"
        "不得把一名角色的外貌、经历或行为特征转移给另一名角色。"
    )
    return selection + "\n\n" + "\n\n".join(profiles)


def _card_visual_profiles(ctx: dict, query: str) -> str:
    """角色卡模式的生图外貌真源；只读取本轮出现的绑定卡，未命中时回退开场卡。"""
    names = _bound_card_names(ctx)
    selected = [name for name in names if name in query]
    if not selected and names:
        selected = [str(ctx.get("opening_card_name") or ctx.get("card_name") or names[0])]
    profiles: list[str] = []
    try:
        from app.services import character_store
        for name in selected:
            base, card_name = _card_source(ctx, name)
            card = character_store.read_card(base, card_name) if base and card_name else None
            if not card:
                continue
            description = str(card.get("description") or "").strip()
            if description:
                profiles.append(f"{card_name}：{description}")
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(profiles)


def _illustration_appearance(ctx: dict) -> str:
    selected = str(ctx.get("_illustration_visual_profiles") or "").strip()
    if ctx.get("appearance_source") in {"worldbook", "character_card"}:
        return selected
    return selected or str(ctx.get("persona") or "").strip()


def _resolve_worldbook(ctx: dict, query: str) -> str:
    """卡内嵌世界书：constant 常驻 + 非常驻按当前上下文语义检索，组装注入文本。

    查询用「最近历史 + 本轮输入」以贴合当前剧情。无卡/无书/读不到 → 空串。
    """
    ctx["_selected_worldbook_indices"] = []
    ctx["_keyword_worldbook_indices"] = []
    ctx["_worldbook_character_names"] = []
    try:
        from app.services import worldbook
        from app.services.rag_backend import EmbedConfig
        book = _repo_worldbook(ctx)
        entries = worldbook.parse_entries(book)
        if not entries:
            return ""
        cfg = EmbedConfig(
            ctx.get("embed_base", ""), ctx.get("embed_key", ""),
            ctx.get("embed_model", "") or "text-embedding-3-small",
            proxy=ctx.get("embed_proxy", ""),
        )
        def notify_initial_index(count: int) -> None:
            run_trace.emit(
                ctx, "worldbook.index", status="started", initial=True, count=count,
            )
            sink = ctx.get("stream_sink")
            if callable(sink):
                sink({"rag_status": {
                    "state": "start", "kind": "worldbook", "count": count,
                }})

        worldbook.schedule_index(
            ctx.get("repo_id", ""), entries, cfg, on_initial=notify_initial_index,
        )
        scan = (agent_context.history_text(ctx) + "\n" + query).strip()
        selection = worldbook.assemble_selection(ctx.get("repo_id", ""), entries, scan, cfg)
        ctx["_selected_worldbook_indices"] = selection.indices
        current_keyword_indices = set(worldbook.keyword_match_indices(entries, query))
        selected_current_indices = current_keyword_indices.intersection(selection.indices)
        ctx["_keyword_worldbook_indices"] = [
            index for index in selection.indices if index in selected_current_indices
        ]
        bound_names = _bound_card_names(ctx)
        activated_text = "\n".join(
            entry.content + "\n" + entry.comment + "\n" + "\n".join(entry.keys)
            for position, entry in enumerate(entries)
            if (entry.source_index if entry.source_index >= 0 else position)
            in selected_current_indices
        )
        ctx["_worldbook_character_names"] = _mentioned_bound_names(bound_names, activated_text)
        return selection.text
    except Exception:  # noqa: BLE001
        return ""


def _worldbook_sources(ctx: dict) -> list[dict]:
    """读取卡快照/源卡与绑定独立书，仅供首次建立小仓库世界书快照。"""
    from app.services import character_store, worldbook_store
    books: list[dict] = []
    for name in _bound_card_names(ctx):
        character_dir, card_name = _card_source(ctx, name)
        if character_dir and card_name:
            embedded = character_store.read_worldbook(character_dir, card_name)
            if isinstance(embedded, dict):
                books.append(embedded)
    wb_dir = ctx.get("worldbook_dir") or ""
    wb_name = ctx.get("worldbook_name") or ""
    if wb_dir and not wb_name and card_name:
        wb_name = card_name
    if wb_dir and wb_name:
        standalone = worldbook_store.read_book(wb_dir, wb_name)
        if isinstance(standalone, dict):
            books.append(standalone)
    return books


def _repo_worldbook(ctx: dict) -> dict | None:
    """返回当前小仓库世界书；首次读取从绑定来源复制，之后只读隔离快照。"""
    from app.services import worldbook_store
    output_dir = ctx.get("output_dir") or ""
    repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
    if output_dir and repo_id:
        existing = worldbook_store.read_repo_snapshot(output_dir, repo_id)
        if existing is not None:
            return existing
        return worldbook_store.ensure_repo_snapshot(output_dir, repo_id, _worldbook_sources(ctx))
    sources = _worldbook_sources(ctx)
    if not sources:
        return None
    entries = []
    for source in sources:
        raw = source.get("entries")
        values = raw.values() if isinstance(raw, dict) else raw
        entries.extend(item for item in (values or []) if isinstance(item, dict))
    return {"entries": entries}


def _curator_worldbook_context(ctx: dict, repo_id: str) -> str:
    from app.services import worldbook_store
    if not _repo_worldbook(ctx):
        return ""
    return worldbook_store.repo_snapshot_context(ctx.get("output_dir") or "", repo_id)


def _curator_worldbook_context_fn(ctx: dict, repo_id: str):
    from app.services import worldbook_store
    base = ctx.get("output_dir") or ""
    if not (base and repo_id and _repo_worldbook(ctx)):
        return None
    allowed = frozenset(ctx.get("_selected_worldbook_indices") or [])
    return lambda _window_text: worldbook_store.repo_snapshot_context(
        base, repo_id, allowed_indices=allowed,
    )


def _curator_worldbook_fn(ctx: dict, repo_id: str):
    from app.services import worldbook_store
    base = ctx.get("output_dir") or ""
    if not (base and repo_id and _repo_worldbook(ctx)):
        return None
    allowed = frozenset(ctx.get("_selected_worldbook_indices") or [])

    def apply(ops):
        rejected = []
        for op in ops:
            if not isinstance(op, dict) or str(op.get("op") or "").strip() != "worldbook_update":
                continue
            try:
                index = int(op.get("index"))
            except (TypeError, ValueError):
                index = None
            if index not in allowed:
                rejected.append(index)
        run_trace.emit(
            ctx, "worldbook.update_scope", allowed_indices=sorted(allowed),
            rejected_indices=rejected,
        )
        return worldbook_store.apply_repo_ops(
            base, repo_id, ops, allowed_update_indices=allowed,
        )

    return apply


def _resolve_regex_scripts(ctx: dict) -> list:
    """合并全局正则 + 本作品卡内嵌正则 → RegexScript 列表。读一次缓存到 ctx。

    全局跨作品生效在前，卡内嵌在后（ST 顺序：GLOBAL→SCOPED）。读不到任一侧不影响另一侧。
    """
    cached = ctx.get("_regex_scripts")
    if cached is not None:
        return cached
    scripts: list = []
    try:
        from app.services import regex_engine, regex_store
        # ① 全局正则（跨作品，存 data/regex_scripts.json）
        for raw in regex_store.load_scripts():
            scripts.append(regex_engine.from_st_dict(raw))
        # ② 预设级正则（仅当前激活预设生效，存预设 JSON 的 regexScripts 键）
        preset_dir = ctx.get("preset_dir") or ""
        preset_name = ctx.get("preset_name") or ""
        if preset_dir and preset_name:
            from app.services import preset_store
            for raw in preset_store.read_regex(preset_dir, preset_name):
                scripts.append(regex_engine.from_st_dict(raw))
        # ③ 卡内嵌正则（随卡、仅该卡，快照优先回退源库）
        from app.services import character_store
        for name in _bound_card_names(ctx):
            character_dir, card_name = _card_source(ctx, name)
            if not (character_dir and card_name):
                continue
            for raw in character_store.read_regex(character_dir, card_name):
                scripts.append(regex_engine.from_st_dict(raw))
    except Exception:  # noqa: BLE001
        scripts = []
    ctx["_regex_scripts"] = scripts
    return scripts


def _apply_regex(ctx: dict, text: str, placement: int, *,
                 is_prompt: bool = False, depth: int | None = 0,
                 skip_depth_gated: bool = False) -> str:
    """在指定 placement 上跑后端侧正则（存储/发送档，不含 markdownOnly 显示档——那在前端）。

    skip_depth_gated：处理本轮实时输入时置真，跳过深度门控（历史楼层）脚本，避免刚输入的当前轮被
    「删 history 最后一条用户消息」等历史级删除正则误擦成空（本架构 live 输入尚未入历史）。
    """
    if not text:
        return text
    scripts = _resolve_regex_scripts(ctx)
    if not scripts:
        return text
    try:
        from app.services import regex_engine
        return regex_engine.run_scripts(
            text, placement, scripts,
            is_markdown=False, is_prompt=is_prompt, depth=depth,
            skip_depth_gated=skip_depth_gated,
        )
    except Exception:  # noqa: BLE001
        return text


def _history_messages(ctx: dict) -> list[dict]:
    """把 ctx.history 转成真实的多轮消息（保留 user/assistant role），供 chat_messages 用。
    替代旧的 history_text 折叠——历史作真实对话轮出现，模型更好衔接、role 不被抹平。"""
    out: list[dict] = []
    for h in (ctx.get("history") or []):
        content = (h.get("content") or "").strip()
        if content:
            out.append({"role": h.get("role") or "user", "content": content})
    return out


def _resolve_preset(
    ctx: dict, worldbook_text: str, *, scene: str = "", affinity: float | None = None, turn: int = 0,
) -> tuple[list[dict], float | None, bool, list[str], list[str]]:
    """有激活偏置预设 → 组装带 role 的多条消息 + 采样温度 + 是否含历史 marker + 命中的思维链(尾/头)。
    无预设/读不到 → ([], None, False, [], [])。

    保留每片段自身 role（system/user/assistant 少样本片段不再被折叠），chatHistory marker 处原位
    插入历史（ST 深度注入语义）。marker 填充：卡字段 + 世界书 + 用户人设。
    思维链按 scene/affinity/turn 真状态条件选（select_chains），尾部注入遵守最严、头部随 system。
    """
    preset_dir = ctx.get("preset_dir") or ""
    preset_name = ctx.get("preset_name") or ""
    if not (preset_dir and preset_name):
        return [], None, False, [], []
    try:
        from app.services import preset_store
        preset = preset_store.read_preset(preset_dir, preset_name)
        if not preset:
            return [], None, False, [], []
        selected_names = [
            str(name).strip() for name in (ctx.get("_selected_persona_names") or [])
            if str(name).strip()
        ]
        history = ctx.get("history") or []
        # ST 深度重注入范式：{{lastUserMessage}}=本轮实时输入（未入历史），{{lastCharMessage}}=历史里
        # 最后一条 AI 消息。配套「擦除历史最后一条用户消息 + 在指定深度重注入 {{lastUserMessage}}」越甲。
        last_char = ""
        for h in reversed(history):
            if (h.get("role") or "") == "assistant" and (h.get("content") or "").strip():
                last_char = (h.get("content") or "").strip()
                break
        markers = {
            "char_name": "、".join(selected_names),
            "char_description": (ctx.get("persona") or "").strip(),
            "char_personality": "",
            "scenario": "",
            "dialogue_examples": "",
            "worldbook": worldbook_text or "",
            "persona": (ctx.get("user_persona") or "").strip(),
            "user_name": (ctx.get("user_name") or "").strip(),
            "last_user_message": (ctx.get("message") or "").strip(),
            "last_char_message": last_char,
        }
        messages = preset_store.assemble_messages(preset, markers, history)
        has_hist = preset_store.has_history_marker(preset)
        chains_tail, chains_head = preset_store.select_chains(
            preset, scene=scene, affinity=affinity, turn=turn)
        # 思维链（含状态栏模板）与其它预设内容一致做宏替换，避免 {{user}}/{{char}} 字面漏进提示词→被模型照抄进正文
        chains_tail = [preset_store.substitute_macros(c, markers) for c in chains_tail]
        chains_head = [preset_store.substitute_macros(c, markers) for c in chains_head]
        params = preset_store.sampling_params(preset)
        temp = params.get("temperature")
        return (messages, (float(temp) if isinstance(temp, (int, float)) else None),
                has_hist, chains_tail, chains_head)
    except Exception:  # noqa: BLE001
        return [], None, False, [], []


def _tool_on(agent_cfg: dict | None, key: str) -> bool:
    """工具开关：无预设全开（原行为）；有预设按其 tools 配置，缺省 True。"""
    if agent_cfg is None:
        return True
    return ((agent_cfg.get("tools") or {}).get(key, True))


def _has_mcp(agent_cfg: dict | None) -> bool:
    """本轮是否有可用 MCP 外部工具：有预设看其 mcpServerIds 非空；无预设看全局已启用服务器。
    为真才在 supervisor 里放出 tool_agent 分派（无 MCP 时该 route 不激活，与原多 Agent 行为一致）。"""
    try:
        if agent_cfg is not None:
            return bool(agent_cfg.get("mcpServerIds"))
        from app.services import mcp_store
        return bool(mcp_store.enabled_servers())
    except Exception:  # noqa: BLE001
        return False


def _agent_system(ctx: dict, base: str) -> str:
    """按预设/风格/技能拼 system_prompt（与单 agent _build 对齐）：
    自定义预设的 systemPrompt 完全替换人设，memory 作长期记忆，风格模板+技能追加。"""
    cfg = ctx.get("agent_cfg")
    sp = (cfg.get("systemPrompt").strip() if cfg and (cfg.get("systemPrompt") or "").strip() else base)
    if cfg and (cfg.get("memory") or "").strip():
        sp += "\n\n【长期记忆（关于用户/偏好）】\n" + cfg["memory"].strip()
    st = (ctx.get("style_template") or "").strip()
    if st:
        try:
            from app.services.image_prompt_style import guidance_for
            sp += "\n\n【生图提示词写法】" + guidance_for("", ctx.get("gen_model", ""), st)
        except Exception:  # noqa: BLE001
            pass
    frags = ctx.get("skill_frags") or []
    if frags:
        sp += "\n\n【用户自定义技能】\n" + "\n".join(f"- {f}" for f in frags)
    return sp


def _temperature(ctx: dict, default: float) -> float:
    cfg = ctx.get("agent_cfg")
    if cfg and isinstance(cfg.get("temperature"), (int, float)):
        return cfg["temperature"]
    return default


def _stream_enabled(ctx: dict) -> bool:
    return bool(ctx.get("stream_output")) and callable(ctx.get("stream_sink"))


def _chat_with_optional_stream(ctx: dict, messages: list[dict], *, temperature: float,
                               top_p: float | None = None,
                               max_tokens: int | None = None) -> str:
    """按本轮设置选择整段或流式调用；流式增量直接送入 runner 队列。"""
    if not _stream_enabled(ctx):
        return _llm.chat_messages(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"], messages,
            temperature=temperature, **_proxy_kw(ctx), top_p=top_p, max_tokens=max_tokens,
        )

    from app.services.stream_text import VisibleTextStream

    sink = ctx.get("stream_sink")
    visible = VisibleTextStream()

    def on_delta(raw: str) -> None:
        text = visible.feed(raw)
        if text:
            sink({"delta": text})

    try:
        return _llm.chat_messages_stream(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"], messages,
            on_delta=on_delta, temperature=temperature, **_proxy_kw(ctx),
            top_p=top_p, max_tokens=max_tokens,
        )
    finally:
        tail = visible.finish()
        if tail:
            sink({"delta": tail})


def _ordered_illustration_events(result_text: str, recs: list[dict]) -> list[dict]:
    """把完整正文拆成有序 SSE 事件：文本前缀 → 插画槽 → 文本后缀。"""
    if not recs:
        return [{"delta": result_text}] if result_text else []
    ordered = sorted(recs, key=lambda rec: int(rec.get("anchor_offset") or len(result_text)))
    events: list[dict] = []
    cursor = 0
    for rec in ordered:
        anchor = max(cursor, min(len(result_text), int(rec.get("anchor_offset") or len(result_text))))
        if anchor > cursor:
            events.append({"delta": result_text[cursor:anchor]})
        request = {
            "prompt": rec.get("prompt") or "",
            "motion": rec.get("motion") or 0,
            "actors": rec.get("actors") or [],
        }
        if isinstance(rec.get("scene_spec"), dict) and rec["scene_spec"]:
            request["scene_spec"] = rec["scene_spec"]
        events.append({"illustrate_request": request, "id": rec.get("id")})
        cursor = anchor
    if cursor < len(result_text):
        events.append({"delta": result_text[cursor:]})
    return events


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
        if isinstance(rec.get("scene_spec"), dict) and rec["scene_spec"]:
            request["scene_spec"] = rec["scene_spec"]
        events.append({"illustrate_request": request, "id": rec.get("id")})
    return events


def stream_multi_agent(context: RunContext) -> Iterator[dict]:
    """运行 supervisor 多 Agent 图；HTTP/SSE wire 由 runner/router 适配。"""
    context.agent_cfg = _resolve_agent_cfg(context.agent_id)
    context.builtin = builtin_agents.resolved()  # ③ 内置 Agent 生效参数（默认+用户覆盖），供各节点取
    context.has_mcp = _has_mcp(context.agent_cfg)
    context.history = agent_context.recent_history(
        context.thread_id,
        max_tokens=context.context_max_tokens,
        per_role=context.history_per_role,
        history_override=context.history_override,
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
    from langchain_core.messages import HumanMessage
    content: list = [{"type": "text", "text": message}]
    for u in (images or []):
        content.append({"type": "image_url", "image_url": {"url": u}})
    init: AgentState = {
        "messages": [HumanMessage(content=content)], "user_text": message,
        "images": images or [], "trace": [], "_ctx": ctx,
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
