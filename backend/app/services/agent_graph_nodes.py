"""专家节点域：generate/video/img2img/analyze/inspire/tool_agent/edit/answer/clarify。

从 agent_graph.py 按域迁出（方案 B，2026-10-23）：搬移块 sha1 逐字节一致，
旧名经 agent_graph re-export 零破坏。向下 import agent_graph_state；
_build_graph 留在 agent_graph（图装配是编排职责）。"""

from __future__ import annotations

from app.services.agent_contracts import CtxLike

import logging

from app.services import llm as _llm, agent_context, generation_approval, run_trace, generation_store, tool_agent_adapter, edit_agent, builtin_agents, roleplay_turn
from app.services.agent_agency import _builtin, _builtin_sampling, _next_story_turn
from app.services.agent_context_assembly import _agent_system, _apply_regex, _temperature
from app.services.agent_graph_state import AgentState
from app.services.agent_stream import _chat_with_optional_stream, _stream_enabled
from app.services.agent_support import _proxy_kw

_LOG = logging.getLogger("uvicorn.error")


def _gen_ctx(ctx: dict):
    return (ctx["gen_base"], ctx["gen_key"], ctx["gen_model"], ctx["thread_id"],
            ctx["repo_id"], ctx["output_dir"], ctx["embed_base"], ctx["embed_key"], ctx["embed_model"])

def _styled_prompt(ctx: CtxLike, prompt: str) -> str:
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

def clarify_node(state: AgentState) -> dict:
    return {"result_text": "本次意图有多种合理理解，请选择要执行的功能。"}

