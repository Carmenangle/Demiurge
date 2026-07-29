"""Supervisor 多 Agent 系统（LangGraph 手写 StateGraph）。

范式：一个 supervisor 节点判用户意图 → 默认普通对话，明确执行时分派图片/视频/工具专家 →
专家执行完把结果写回 state。自由文本统一走该编排；遗留 ReAct 大脑只作为工具专家 Adapter。

分派原则：Supervisor 每轮结合上下文做唯一语义判断；代码只校验路由、附件和工具能力。
Supervisor 可使用独立快模型，专家使用主模型；单专家任务直连 END，不做二次判断。
"""
from __future__ import annotations

import json
import re
from typing import TypedDict, Iterator

from app.services import agent_context, generation_approval, generation_store, tool_agent_adapter
from app.services import llm as _llm
from app.services.agent_contracts import RunContext


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
    # 下方是执行上下文（构图时注入，专家节点用）
    _ctx: RunContext


# ── 路由：Supervisor 模型负责语义，代码只校验能力条件 ──


_SUPERVISOR_SYSTEM = (
    "你是多智能体系统中唯一负责理解用户语义和上下文的调度主管。"
    "结合最近对话、本轮文本、附件数量和本轮可用路由判断最终交付物。\n"
    "特别区分：\n"
    "- 审查已有提示词、解释生成结果为何漏画元素、评价或优化现有要求，属于 answer。\n"
    "- 根据图片产出新的提示词文本、从图片反推可复用提示词，才属于 analyze。\n"
    "- img2img 的最终交付物必须是基于附件生成或编辑后的新图片；附件本身不代表要生图。\n"
    "- 只提交一段可直接执行的完整成稿生图提示词，也可以选择 generate 或 img2img。\n"
    "- 用户说‘继续、按刚才、其他不变、就这样’时必须结合最近对话判断延续目标。\n"
    "只有一个路由明显成立时 confidence=high；两种以上理解都合理时 confidence=low，"
    "并在 alternatives 中按相关性给出最多3个合理路由，不得罗列无关工具。\n"
    "只输出 JSON，不要解释："
    "{\"route\":\"首选路由\",\"confidence\":\"high或low\",\"alternatives\":[\"其他合理路由\"]}。"
    "route 和 alternatives 只能使用下方本轮可用路由。"
)

def _supervisor_route(text: str, image_count: int, ctx: dict) -> tuple[str, bool, list[str]]:
    """每个普通用户轮次都由模型做唯一语义判断；代码只提供并复核能力清单。"""
    chat_fn = ctx.get("chat_fn") or _llm.chat
    try:
        model = ctx.get("route_model") or ctx["chat_model"]
        has_images = image_count > 0
        available = _available_routes(has_images, ctx)
        route_lines = "\n".join(
            f"- {route}：{_ROUTE_DESCRIPTIONS[route]}" for route in available
        )
        system = _SUPERVISOR_SYSTEM + "\n【本轮可用路由】\n" + route_lines
        user = (
            agent_context.history_text(ctx)
            + f"附件数量：{image_count}\n本轮用户：{text}"
        )
        reply = chat_fn(ctx["chat_base"], ctx["chat_key"], model,
                        system, user, temperature=0, proxy=ctx.get("proxy", ""))
        raw = (reply or "").strip()
        try:
            json_block = re.search(r"\{[\s\S]*\}", raw)
            payload = json.loads(json_block.group(0) if json_block else raw)
            route = str(payload.get("route") or "").strip().lower()
            confidence = str(payload.get("confidence") or "high").strip().lower()
            raw_alternatives = payload.get("alternatives") or []
            alternatives = [str(item).strip().lower() for item in raw_alternatives] \
                if isinstance(raw_alternatives, list) else []
            if route in available:
                return route, confidence != "low", alternatives
        except (TypeError, ValueError):
            pass
        r = raw.lower().strip("`'\".,:;，。")
        if r in available:
            return r, True, []
    except Exception:
        pass
    return "answer", True, []


# ── supervisor 节点：判路由，写 state.route + trace ──

# route → 对应工具开关键（自定义预设可关掉某能力，关掉则回退 answer）
_ROUTE_TOOL = {"generate": "generate_image", "img2img": "image_to_image",
               "analyze": "analyze_image", "inspire": "search_inspiration",
               "video": "generate_video"}
_ROUTE_LABELS = {
    "answer": "继续对话",
    "generate": "生成图片",
    "img2img": "参考图生图",
    "analyze": "反推提示词",
    "video": "生成视频",
    "inspire": "查找灵感",
    "tool_agent": "调用工具",
}
_ROUTE_DESCRIPTIONS = {
    "answer": "普通对话、问答，以及审查、解释、评价或优化已有内容",
    "generate": "根据文本生成新图片，或执行无参考图的完整成稿提示词",
    "img2img": "基于本轮图片附件生成、修改或续接新图片",
    "analyze": "从本轮图片附件反推并交付新的可复用提示词文本",
    "video": "生成视频、动画或动图",
    "inspire": "联网查找参考、灵感、流行款式或趋势",
    "tool_agent": "调用已接入的外部工具、接口、文件或数据库能力",
}


def _route_available(route: str, has_images: bool, ctx: dict) -> bool:
    if route not in _ROUTE_LABELS:
        return False
    if route == "answer":
        return True
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
    text = state.get("user_text", "")
    has_images = bool(state.get("images"))
    forced_route = str(ctx.get("forced_route") or "").strip().lower()
    if forced_route:
        route = forced_route if _route_available(forced_route, has_images, ctx) else "answer"
    else:
        route, confident, alternatives = _supervisor_route(text, len(state.get("images") or []), ctx)
        if not confident:
            options = _route_choice_options(route, alternatives, has_images, ctx)
            if len(options) >= 2:
                trace = state.get("trace", []) + ["🧭 主管无法确定分派，等待用户选择"]
                return {
                    "route": "clarify",
                    "route_choice": _route_choice_payload(ctx, options),
                    "trace": trace,
                }
            route = "answer"
    if not _route_available(route, has_images, ctx):
        route = "answer"
    label = {"generate": "生图专家", "img2img": "图生图专家", "analyze": "反推专家",
             "inspire": "灵感专家", "tool_agent": "工具专家", "video": "视频专家",
             "answer": "对话"}.get(route, route)
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
                        system, prompt, temperature=0.5, proxy=ctx.get("proxy", ""))
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
                    system, prompt, temperature=0.3, proxy=ctx.get("proxy", ""))
    if not (out or "").strip():
        raise RuntimeError("提示词修饰模型未返回内容")
    return out.strip()


def generate_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
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
    imgs = state.get("images", [])
    trace = state.get("trace", []) + ["🔍 反推专家执行中…"]
    if not imgs:
        return {"result_text": "请先上传要反推的图片。", "trace": trace}
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        model = _llm.build_model(ctx["chat_base"], ctx["chat_key"], ctx["chat_model"], proxy=ctx.get("proxy", ""))
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
    query = state.get("user_text", "")
    trace = state.get("trace", []) + ["💡 灵感专家执行中…"]
    try:
        from app.services import inspiration as _insp
        data = _insp.search_and_refine(query, ctx["chat_base"], ctx["chat_key"], ctx["chat_model"], proxy=ctx.get("proxy", ""))
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
    text = state.get("user_text", "")
    imgs = state.get("images", [])
    trace = state.get("trace", []) + ["🛠️ 工具专家执行中…"]
    return tool_agent_adapter.run(ctx, text, imgs, trace)


def answer_node(state: AgentState) -> dict:
    ctx = state["_ctx"]
    text = state.get("user_text", "")
    trace = state.get("trace", []) + ["💬 对话中…"]
    try:
        system = _agent_system(
            ctx,
            "你是通用 AI 助手，默认进行普通对话。讨论、评审或优化提示词时只回答用户，"
            "不要声称已调用任何生成工具。必须衔接最近对话中的对象、代词、已确认约束和否定修改，"
            "以用户本轮最新要求为最高优先级，不得恢复已经被否决的旧方案。请用简洁中文回答。",
        )
        user = agent_context.history_text(ctx) + text
        reply = _llm.chat(ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
                         system, user, temperature=_temperature(ctx, 0.5), proxy=ctx.get("proxy", ""))
        return {"result_text": reply or "（无回复）", "trace": trace}
    except Exception as e:  # noqa: BLE001
        return {"result_text": f"回答失败：{e}", "trace": trace}


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
    g.add_node("answer", answer_node)
    g.add_node("clarify", clarify_node)
    g.set_entry_point("supervisor")
    # 条件边：按 supervisor 判出的 route 跳到对应专家
    g.add_conditional_edges("supervisor", lambda s: s.get("route", "answer"),
                            {"generate": "generate", "video": "video", "img2img": "img2img",
                             "analyze": "analyze", "inspire": "inspire",
                             "tool_agent": "tool_agent", "answer": "answer",
                             "clarify": "clarify"})
    # 单专家任务：干完直接 END，不回 supervisor 二次判断（慢中转下省一次往返）
    for n in ("generate", "video", "img2img", "analyze", "inspire", "tool_agent", "answer", "clarify"):
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


def stream_multi_agent(context: RunContext) -> Iterator[dict]:
    """运行 supervisor 多 Agent 图；HTTP/SSE wire 由 runner/router 适配。"""
    context.agent_cfg = _resolve_agent_cfg(context.agent_id)
    context.has_mcp = _has_mcp(context.agent_cfg)
    context.history = agent_context.recent_history(
        context.thread_id,
        max_tokens=context.context_max_tokens,
    )
    context.skill_frags = _resolve_skills(context.agent_cfg)
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
                if upd.get("_interrupted"):
                    interrupted = True  # noqa: F841  语义标记，保留可读性
                    yield {"interrupted": True}
                for line in (upd.get("trace") or [])[seen_trace:]:
                    yield {"trace": line}
                seen_trace = len(upd.get("trace") or []) if upd.get("trace") else seen_trace
                for rec in upd.get("image_recs") or []:
                    if rec.get("id") not in emitted_imgs:
                        emitted_imgs.add(rec.get("id"))
                        yield {"image": rec.get("url"), "id": rec.get("id"),
                               "regeneration": rec.get("regeneration")}
                for rec in upd.get("video_recs") or []:
                    if rec.get("id") not in emitted_imgs:
                        emitted_imgs.add(rec.get("id"))
                        yield {"video": rec.get("url"), "id": rec.get("id")}
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
                    yield {"delta": upd["result_text"]}
    except Exception as e:  # noqa: BLE001
        yield {"error": str(e)}
    yield {"done": True}
