"""Supervisor 多 Agent 系统（LangGraph 手写 StateGraph）。

范式：无卡或带附件请求由 supervisor 判用户意图；有卡纯文本直达 Roleplay，明确强执行命令
用零 LLM 规则分派。专家执行完把结果写回 state；遗留 ReAct 大脑只作为工具专家 Adapter。

分派原则：Supervisor 处理模糊/多能力请求；角色卡纯文本避免重复上传历史。
Supervisor 可使用独立快模型，专家使用主模型；单专家任务直连 END，不做二次判断。
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
import re
import threading
import time
import traceback
from typing import Any, Callable, Iterator, TypedDict

from app.services import agent_context, agent_plugins as _agent_plugins, attachment_store, builtin_agents, edit_agent, generation_approval, generation_store, plan_compiler, prompt_compiler, roleplay_turn, run_trace, scene_classify, story_history, structured_output, tool_agent_adapter
from app.services.structured_contracts import SupervisorDecision
from app.services import llm as _llm
from app.services import prompt_clean
from app.services.agent_contracts import RunContext
from app.config import COMFYUI_BASE_URL

# 2026-09-01 用户定案：首发**不设总时长上限**——只要还在持续吐 token 就不允许截断；
# 只有 30s 无 token 的流式读超时（llm.build_model streaming）才允许中断。

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


def _proxy_kw(ctx: dict, key: str = "chat_proxy") -> dict:
    value = (ctx.get(key, "") or "").strip()
    return {"proxy": value} if value else {}


# 固化知识库（DATA_DIR/agent_knowledge/*.md）：流程规范/映射表。
# 目录与上限常量、列举/读取统一走 agent_knowledge 服务（唯一属主）。
# 2026-09-04：带 frontmatter「skill」的文档 = 固化技能（按触发装载，不常驻）；
# 无头的普通知识文档仍全量常驻注入。目录注入文本是本策略的唯一裁决。


def _knowledge_catalog_text() -> str:
    """拼接注入文本（第三类目录附件，与配方清单同机制）。

    - 注入模式取 agent_knowledge.injection_config()：always（全量常驻=老行为）或
      smart（默认）——技能文档（meta.skill 非空且不在 always_docs）只注入一行触发
      描述 + 指令：命中场景先调 knowledge.load_doc(name=文件名主名) 拉全文照执行；
    - 无头普通知识文档保持历史行为：全量注入（KNOWLEDGE_MAX_FILES 上限不变）。
    """
    from app.services import agent_knowledge
    cfg = agent_knowledge.injection_config()  # 缺省 {} → smart
    mode = str(cfg.get("mode") or agent_knowledge.MODE_SMART)
    force_always = {str(n) for n in (cfg.get("always_docs") or []) if str(n).strip()}
    skill_lines: list[str] = []
    legacy: list[dict] = []
    for meta in agent_knowledge.list_docs():
        is_skill = bool(meta.get("skill")) and mode == agent_knowledge.MODE_SMART \
            and meta.get("name") not in force_always
        (skill_lines if is_skill else legacy).append(meta)

    parts: list[str] = []
    if skill_lines:
        lines = []
        for meta in skill_lines:
            trigger = str(meta.get("whenToUse") or "").strip()
            name = str(meta.get("name") or meta.get("file") or "?")
            lines.append(f"- {meta.get('file', name)}（skill={meta['skill']}）"
                         + (f"：{trigger}" if trigger else ""))
        parts.append(
            "【固化技能库】以下流程技能按触发场景按需装载、不常驻注入：\n"
            + "\n".join(lines)
            + "\n接到与某个技能触发场景相符的任务时，必须先调用 "
              "knowledge.load_doc(name=文件名主名，如「固化02-小说转合集卡规范」) "
              "拉取全文并照其结构与质量标准执行；拿不准是否相关也应先加载核对，"
              "禁止凭目录一句话另搞一套。")

    legacy_texts: list[str] = []
    appended = 0
    for meta in legacy:
        if appended >= agent_knowledge.KNOWLEDGE_MAX_FILES:
            break
        try:
            doc = agent_knowledge.read_doc(meta["name"])
        except (FileNotFoundError, ValueError, OSError):
            continue
        text = (doc.get("content") or "").strip()
        if text:
            legacy_texts.append(f"【知识：{meta['name']}】\n{text}")
            appended += 1
    if legacy_texts:
        parts.append("【固化知识库】以下是本项目沉淀的流程规范与映射表，执行相关任务时必须遵守"
                     "其中的结构与质量标准，禁止凭印象另搞一套：\n\n" + "\n\n".join(legacy_texts))
    return "\n\n".join(parts)


def _recipe_catalog_text() -> str:
    """已保留（saved）固化流程预设清单：注入编译/自由循环上下文供复用匹配（草稿不出现）。"""
    try:
        from app.services import plan_tasks as _pt
        saved = [r for r in _pt.list_recipes().values()
                 if str(r.get("status") or "saved") == "saved"]
    except Exception:  # noqa: BLE001 - 清单不可用时不阻断对话
        return ""
    if not saved:
        return ""
    lines = []
    for r in sorted(saved, key=lambda x: str(x.get("created_at") or 0), reverse=True):
        plan = r.get("plan") or {}
        steps = plan.get("steps") or []
        ops = "、".join(str(s.get("operation") or "") for s in steps[:6])
        lines.append(f"- 《{r.get('name')}》 id={r.get('id')}"
                     f"（{len(steps)} 步：{ops}）意图：{r.get('intent') or ''}")
    return ("【固化流程预设】以下是用户已确认固化的可重放流程。用户目标与某条高度一致时，"
            "优先调用 plan.instantiate_recipe 整条重放（省 token，durable/expensive 步骤照常走审批）；"
            "不完全一致才逐步编排：\n" + "\n".join(lines))

def _live_trace(ctx: dict, agent: str):
    """把 run_trace 事件同时实时推给前端对话流（stream_sink.trace）。

    非流式节点（计划编译/自由循环）内部阻塞时，SSE 也要能看到过程：
    思考、重试、工具调用、步骤执行、失败——不再只有「生成中」黑盒。
    """
    def _line(event: str, data: dict) -> str:
        op = str(data.get("operation") or data.get("tool") or "")
        status = str(data.get("status") or "")
        if event == "model.request":
            return "🤔 模型正在思考…"
        if event == "structured.output":
            return "📐 解析模型输出…"
        if event == "plan.compiled":
            return f"✅ 计划编译完成（{data.get('steps', '?')} 步）" if status == "ok"                 else "⚠️ 计划校验未通过，正在重试…"
        if event == "plan.sections_filled":
            return f"🧩 回填 {data.get('count', '?')} 个套装提示词"
        if event == "plan.validated":
            return "✅ 计划校验通过，投递执行队列"
        if event == "plan.attachments":
            return f"📎 已预读文档：{data.get('path', '')}" if status == "ok"                 else f"⚠️ 文档预读失败：{data.get('path', '')}"
        if event == "tool.call":
            return f"🔧 调用工具：{op}"
        if event == "tool.result":
            return f"{'✅' if data.get('ok') else '❌'} 工具{'完成' if data.get('ok') else '失败'}：{op}"
        if event == "plan.step_started":
            return f"▶ 执行步骤：{op}"
        if event == "plan.step_done":
            return f"✅ 步骤完成：{op}"
        if event == "plan.step_failed":
            return f"❌ 步骤失败：{op}（{str(data.get('error') or '')[:60]}）"
        if event == "plan.step_blocked":
            return f"⛔ 步骤受阻：{op}（{str(data.get('reason') or '')}）"
        if event == "plan.terminal":
            return f"🏁 计划结束：{status}"
        return f"🔄 {event}"

    def _detail(event: str, data: dict) -> str | None:
        """trace 行详情（2026-09-06）：思考文本 / 工具参数 / 工具结果，
        前端执行过程面板点开查看。"""
        if event == "model.request":
            t = str(data.get("thinking") or "").strip()
            return t[:400] if t else None
        if event == "tool.call":
            p = data.get("params")
            if p is None:
                return None
            try:
                return json.dumps(p, ensure_ascii=False)[:400]
            except Exception:
                return str(p)[:400]
        if event == "tool.result":
            r = data.get("result")
            if r is None:
                return None
            try:
                return (json.dumps(r, ensure_ascii=False)[:500]
                        if not isinstance(r, str) else r[:500])
            except Exception:
                return str(r)[:500]
        return None

    def emit(event: str, **data):
        run_trace.emit(ctx, event, agent=agent, **data)
        sink = ctx.get("stream_sink")
        if not callable(sink):
            return
        text = _line(event, data or {})
        if text:
            try:
                detail = _detail(event, data or {})
                sink({"trace": text, **({"detail": detail} if detail else {})})
            except Exception:
                pass
    return emit


def _live_chat(ctx: dict):
    """包装 chat_fn：调用前/成功/失败/重试都实时 sink 到对话流。

    模型调用可能挂起数分钟——调用前先显示「思考中」，重试时逐次显示原因，
    否则前端只有一动不动「生成中」。
    """
    base_fn = ctx.get("chat_fn") or _llm.chat

    def _sink(text: str) -> None:
        sink = ctx.get("stream_sink")
        if callable(sink):
            try:
                sink({"trace": text})
            except Exception:
                pass

    def wrapped(base, key, model, system, user, **kw):
        _sink("🤔 模型思考中…")
        try:
            if base_fn is _llm.chat:
                kw["on_retry"] = lambda n, e: _sink(
                    f"🔄 第 {n} 次重试（{str(e)[:60]}）")
            result = base_fn(base, key, model, system, user, **kw)
            _sink("✅ 模型响应完成")
            return result
        except Exception as e:  # noqa: BLE001 - 原样抛出，只补可视化
            _sink(f"❌ 模型调用失败：{str(e)[:80]}")
            raise
    return wrapped


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
            or plan_compiler.is_doc_delegation_intent(intent_text)):
        # 路由界限·委派强命令层：高置信规模词+资产动作 / 显式计划语言 / 文档交付
        # 强命令 → 委派（零 LLM）。文档交付允许带图附件：参考图在此是素材
        # （看图反推→生成套装文档），不是图生图/反推的目标。
        # 误判方向：模糊表达不改剧情默认。
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


def _flow_doc_hint(ctx: Any, user_text: str) -> str:
    """current_flow_doc 续跑提示（固化链 §3 设计 A）：延续语 + 有句柄才注入，纯机械。"""
    try:
        from app.services import flow_context

        repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "")
        return flow_context.resume_hint(repo_id, user_text)
    except Exception:  # noqa: BLE001 - 句柄不可用不阻断编译/自由循环
        return ""


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


def _fabric_capabilities(intent_text: str, configured: set[str]) -> list[dict]:
    """自由循环能力清单：卡/书/文档交付任务（固化02/03/04）按白名单过滤；
    其余（批量生图等确定性任务走计划编译，不走自由循环）保持全量。

    白名单单一属主 = capability_registry.FABRIC_DOC_DELIVERY_OPS（2026-09-10，
    此前是本文件里的硬编码 frozenset，新增能力要改两处）；
    再按意图按需叠加两个可选通道（都要求用户显式说出口，默认关闭）：
    - FABRIC_TOOLING_OPS：写脚本/跑命令/排错（自建工具通道）；
    - FABRIC_WEB_OPS：联网检索 + 受控下载（链路①+②，内置安全链而非裸脚本联网）。
    """
    from app.services import capability_registry as _cr
    caps = _cr.with_availability(configured)
    if not plan_compiler.is_doc_delegation_intent(intent_text):
        return caps
    allowed = set(_cr.FABRIC_DOC_DELIVERY_OPS)
    if plan_compiler.wants_tooling_intent(intent_text):
        allowed |= _cr.FABRIC_TOOLING_OPS
    if plan_compiler.wants_web_material_intent(intent_text):
        allowed |= _cr.FABRIC_WEB_OPS
    return [c for c in caps if c.get("operation") in allowed]


def _fabric_discipline(ctx: Any) -> str:
    """自由循环 system 级执行纪律（2026-09-06）：大 txt 附件禁止整本 read_text。

    放 system（每步决策都可见）而非仅 history——实锤：history 里的纪律提示模型会无视，
    长文本进 messages 后决策输出被网关截断（第 5 步模型决策失败）。返回空串=无纪律。
    """
    parts = []
    # 2026-09-07 治本：纪律无条件注入（此前只在附件 .txt/.md 时注入——后续补写指令
    # 不带附件，模型完全看不到纪律，反复自我怀疑/不按规范执行实锤）。
    if True:
        parts.append(
            "【执行纪律·合集卡制作】本任务是小说→合集卡内容生成，必须遵守以下规范：\n"
                "1. 章节感知阅读（2026-09-07 用户定案）：先 novel.survey 拿章节清单（标题/offset），标题是线索——"
                "从标题识别角色/地区/情节，规划定向阅读，禁止通读全文、禁止按字数硬切。"
                "按章节读：单章 ≤2 万字多章合并一卷（offset 定位），单章 >2 万分次读。\n"
                "2. 角色素材：用 novel.charfacts 切素材段（_prep/charfacts/<名>.txt），"
                "切素材时用 chapter_start/chapter_end 限定到当前剧情已推进到的章节。\n"
                "3. 防剧透：写角色经历/重大事件条目时，只使用已读章节的内容，"
                "禁止使用未读章节/未来剧情——角色经历不能超过当前剧情推进的部分。\n"
                "4. 任务定性（2026-09-06 用户定案，网关 400 实锤）：当前任务是制作合集卡"
                "（六层条目：系统判定机制/全局机制/世界背景地理势力事件/局部机制/角色卡/名册）。"
                "先判定本作 NSFW/正常：出现成人向设定/玩法即判 NSFW；判 NSFW 后参照现有 NSFW 卡的"
                "机制层写法（如「全局机制·女尊采补与男畜逆袭」「全局机制·龙阳决与双修」——机制条目"
                "是设定化命名不是具体描写），本卡必须产出同名口径的 NSFW 玩法机制条目，禁止回避删除"
                "成人玩法设定。成人设定（角色关系/主仆/肉体/体质/个体机制/好感度）如实转写为设定条目，"
                "禁止因敏感内容更换任务；条目正文是设定描述不是文学复述，禁止原样复制具体性行为描写"
                "（会触发网关内容审核 400）。先通读全书整体归纳六层结构，再分批落条目，禁止碎片化输出。\n"
                "5. 制卡师立场（2026-09-06 用户定案，NSFW 认知纠正）：你是专业的 NSFW 合集卡制卡师。"
                "NSFW 角色卡是正当的创作产物，用户委托制作它就是本任务的合法目标——不是违规内容、"
                "不需要净化、不需要回避、更不需要在内心评判它。对成人设定坦然、专业、设定化地处理："
                "像 ST 社区制卡者一样把敏感内容转写为机制条目（采补/双修/调教/契约/受孕/炉鼎…），"
                "完整保留设定深度，不因内容敏感而删减角色或机制。\n"
                "6. NSFW 成功生成经验（参照 Character Card Builder spec 过程机制，2026-09-06）："
                "学它的机制而非单卡结构——E1 卡纲阶段显式确认 NSFW 属性/偏好方向/角色名单，确认前"
                "不写正式条目、不靠猜测；E2 步骤化渐进（卡纲→分批条目→验收，批间确认）；E3 立场不拒"
                "（NSFW 是正常创作）；E4 确认 NSFW 后如实完整转写成人设定，不回避不省略不净化，条目"
                "正文设定化描述，具体性行为词汇被网关 400 时自动交本地模型转写（混合模式），云端继续"
                "下一步；E5 模糊设定主动向用户确认，不脑补关键机制。\n"
                "7. 两阶段流水线（2026-09-07 用户定案）：Part 1 通读建档——目标是整理全文，"
                "最终自然读完所有卷；每读若干卷把该卷的角色/事件/世界观要点用 doc.create_repo/"
                "file.edit 追加进作品 _prep/材料/{world,chars,mech,nsfw}.json，原文交 fabric 压缩；"
                "通读完成后产出【目标方案】（出场人物/大事件/世界观/地理势力概览 + 章节-内容映射）"
                "落 docs/目标方案-<作品名>.md 给用户确认。Part 2 定向详读——基于目标方案针对每块"
                "内容定向读对应章节补充细节，分批写完整条目；判 NSFW 必须有 8-14 条独立"
                "「NSFW·<标题>」条目（含【边界】仅限成年声明）。通读是目标而非禁止，但必须"
                "边读边整理，禁止 19 步纯读不产出。\n"
                "8. 禁止重复读同一段（2026-09-07 实锤）：分卷读覆盖全书后，模型因上下文压缩"
                "「忘记」已读细节，反复 read_text 同一 offset（0/20000/40000 绕圈 6 轮无推进）。"
                "规则：同一文件同一 offset 最多读 2 次；第 2 次后仍缺细节，读 charfacts 素材段"
                "或基于已读归纳直接产出材料/卡纲——禁止第 3 次重复读同一段，读完即产出，"
                "不得停滞在确认细节。\n"
                "9. 卡纲前置概览（2026-09-07 用户定案）：请求写卡纲（doc.create_repo）时，"
                "卡纲 content 第一部分必须是【已读章节与材料概览】——已读章节范围（如「第 1-6 章」）、"
                "角色名单（主要出场角色）、机制要点、事件线索、地理/势力概览；让用户批准的是"
                "有依据的草案，禁止凭少量阅读匆忙给空结构。\n"
                "10. B 态单卡合集（2026-09-07 用户定案，240KB）：默认产出单卡——全部六层条目写完后，"
                "用 character.upsert_repo 落主卡，card.data.character_book.entries = 全部条目 list，"
                "card.json 含全部内容 ≈ 240KB（A 态主卡仅 2KB 骨架=没内嵌，不合格）；first_mes 非空。\n"
                "11. 密度（2026-09-09 用户定案）：角色条目 ≥1800 字、机制 ≥800、编号 ≥600、NSFW ≥400；"
                "NSFW 玩法 ≥400——旧 400/200/200 太低导致 2KB 骨架卡。\n"
                "12. 素材锚定扩写法（2026-09-07 用户定案，防偏差）：密度不达标时禁止凭记忆"
                "重写一个短版覆盖——那不是扩写是退化（22:08 实锤：戴茂 1945→949 倒退）。"
                "正确做法：(a) 先读该条目对应素材段 _prep/charfacts/<角色或主题名>.txt 的"
                "完整内容（分卷读，直到读完,不能只读前 2 万字符）；(b) 从素材原文提取关键词/"
                "具体细节——真实事件、对话、关系变化、外貌描写、机制运作、好感节点；(c) 在"
                "已有条目基础上逐字段扩充这些原文细节，每条目标 ≥1800/800/600/400 字；(d) "
                "扩写内容必须能追溯到素材原文，禁止凭空脑补、禁止重复堆砌同一细节。\n"
                "13. 扩写目标校准（2026-09-07）：一条不达标条目的扩写，期望一次到位（如角色"
                "从 900→1800+，机制 500→800+），写入后跑 check_density 确认；若仍不达标，"
                "继续读素材补细节，而不是反复重写同一短版。\n"
                "14. 环境信任与上下文压缩认知（2026-09-07 实锤）：(a) repo_id 是作品仓库"
                "UUID（如 8fed7f23-...），由执行环境归一注入，worldbook 快照就写在其目录下的"
                "worldbook.json——这是正确的，不要怀疑/改用它显示名（如「玫瑰与繁花」只是文件夹名）；"
                "(b) 对话历史里出现「UPSERT_ENTRIES 已写…」是上下文压缩标记：系统把旧步骤的"
                "条目参数压缩成摘要以控制上下文长度，**实际落盘的 worldbook.json 始终是完整条目**，"
                "不是摘要——不要误以为没写入而停下来问用户；(c) 数据完整性以 check_density + "
                "worldbook 快照为准，不要因为看到压缩标记或 repo_id 长相而自我怀疑、中断流程；"
                "继续按密度检查补写直至 passed=True。\n"
                "15. 主角归一（{{user}} 映射，2026-09-07 用户定案·通用规则）：主角（玩家扮演"
                "对象）在卡里对应 {{user}}，不是普通角色。(a) 主角自身条目不含【与{{user}}的"
                "关系】【好感分阶】（玩家不对自己有第三人称视角），改用【主角定位】；"
                "(b) 其他角色/机制/世界/NSFW 条目正文里提及主角一律替换为 {{user}}"
                "（如「戴茂哥哥」→「{{user}}哥哥」），确保玩家代入不穿帮；(c) 先识别谁是"
                "主角（通读主视角/被后宫围绕的核心），禁止把配角误当 {{user}}；"
                "(d) 此规则对任何作品的主角生效，不限于本作。\n"
                "15b. 主角条目保留与初始好感（2026-09-08 用户定案）：(e) 主角自身条目必须保留"
                "（comment 仍为「角色卡·主角名」），用【主角定位】替代【与{{user}}的关系】——"
                "主角是 {{user}} 的化身卡，不能被删除或覆盖，其他条目提及主角仍替换为 {{user}}；"
                "(f) 好感度系统机制条目（2026-09-08 用户定案）：初始好感不是写在每个角色条目里硬编码，"
                "而是独立做一条「系统判定机制·好感度初始值与动态变化」条目，由它统一规定：初始好感按剧情因果推导"
                "（不是默认 0）——亲缘/旧识/恩人出场即中高好感 55-90，{{user}} 操作损害过其利益或敌对/仇视出身"
                "则初次见面即负好感 -30 到 -10，陌生中立 20-55；同时规定好感度随剧情事件动态增减（被救+/被夺利-）。"
                "各角色条目里不必再写初始好感值，统一由这条机制条目管辖。\n"
                "16. 定向补写·不重读重写达标条目（2026-09-08 用户定案，防绕圈）：check_density 返回的"
                "below 清单是唯一补写范围——只补 below 里列出的不达标条目（读它对应的 _prep/charfacts 素材），"
                "已达标条目（在 below 之外的）禁止重读、禁止重写、禁止反复 read worldbook.json 全文核对差异"
                "（13:23 实锤：48 步反复 read 同一 worldbook.json off=0 原地绕圈）。补写流程："
                "check_density 拿 below 清单、逐条读对应素材补写、再 check_density 确认，达标条目不进循环。")
    return "\n\n".join(parts)


def _fabric_knowledge_history(ctx: Any, user_text: str) -> str:
    """自由循环历史装配（full/approval 共用）：分档历史 + 配方目录 + 固化知识目录 + 流程句柄。

    历史走 context_gate 按当前意图域分档注入（2026-09-06 实锤：生图历史全量平铺
    淹没「小说转合集卡」指令，模型把合集卡任务编排出生图步骤）；意图域未识别时
    退回原全量拼接，不误伤依赖上文的短指令。
    """
    from app.services import context_gate as _cg
    intent_source = str(ctx.get("message") or user_text or "").strip()
    # 实锤修复（2026-09-06 用户四步实锤）：装配回路历史不含本轮消息（前端 visibleHistory
    # 在 setMessages 前计算、后端 append_turn 回合结束才落），历史最后一条 user 是上一轮
    # 指令——必须显式传本轮指令全文作锚点，否则上一轮跨域指令被误标「当前指令」拿最高权重
    #（生图→合集卡场景被反向放大）。P3①：嵌入配置存在时叠加语义分档（失败退回词表）。
    history_text = _cg.history_text_gated(ctx, intent_source, anchor_text=intent_source,
                                          embed_fn=_history_embed_fn(ctx))
    recipe_catalog = _recipe_catalog_text()
    if recipe_catalog:
        history_text = (history_text + "\n\n" + recipe_catalog).strip()
    knowledge = _knowledge_catalog_text()
    if knowledge:
        history_text = (history_text + "\n\n" + knowledge).strip()
    flow_hint = _flow_doc_hint(ctx, user_text)
    if flow_hint:
        history_text = (history_text + "\n\n" + flow_hint).strip()
    attach_lines = []
    for _a in (ctx.get("attachments") or []):
        _name = str(_a.get("name") or "")
        _fid = str(_a.get("file_id") or "")
        _path = ""
        if _fid:
            try:
                from app.services import attachment_store as _attach_store
                _p = _attach_store.resolve(_fid)
                _path = str(_p) if _p is not None else ""
            except Exception:  # noqa: BLE001 - 路径解析失败不阻断
                _path = ""
        if _name or _path:
            attach_lines.append(f"- {_name}（真实路径：{_path or '未落盘'}）")
    if attach_lines:
        history_text = (history_text + "\n\n【用户上传的附件文件】\n"
                        + "\n".join(attach_lines)
                        + "\n直接使用上述真实路径读取/引用，禁止反复 list_dir 查找。").strip()
    # 固化02/03 内容生成纪律（2026-09-06 实锤：模型对小说附件整本 file.read_text
    # 分块翻读（40k 字符/次），长输入决策被网关拖死）。规范 §2.5 要求机械预处理：
    # 小说/txt 大附件先 novel.survey 拿候选名单 → novel.charfacts 切素材段 →
    # 模型只读素材段写条目，禁止整本 read_text。
    _txt_big = any(str(_a.get("name") or "").lower().endswith((".txt", ".md"))
                   for _a in (ctx.get("attachments") or []))
    if _txt_big:
        history_text += (
            "\n\n【执行纪律】用户上传的是长文本/小说类附件（可能数十万字）："
            "先 novel.survey 拿候选名单 → novel.charfacts 切素材段（用 chapter_start/end "
            "限定到当前剧情推进章节）→ 角色条目只读素材段；理解机制/背景/事件可 "
            "file.read_text 分卷读（每次 ≤20000 字符，按章节顺序），禁止一次硬塞超长文本。"
            "写角色经历/事件不得使用未读章节的未来剧情。")
    return history_text


def _fabric_run_args(ctx: Any, *, intent: str, history: str,
                     access_mode: str, lease_id: str, output_dir: str,
                     repo_id: str, configured: set[str],
                     images: list[str], resume: dict | None = None,
                     checkpoint_fn: Callable | None = None,
                     delivery_intent: str | None = None) -> dict:
    """自由循环公共调用参数（full/approval 共用）。

    2026-09-07 重发的资本：card 交付任务启动时自动扫描作品已有产出
    （卡纲/目标方案/素材/世界书快照/主卡），生成【前序产出档案】注入 system——
    模型开局就知道已有产出，在其上继续/修改/优化，不重读全文重建。

    delivery_intent（2026-09-10）：判定用文本 = 本轮原文拼上历史用户消息后的
    `_fabric_judge`。**只影响能力面与前序产出档案**，不改 intent（进度卡/断点仍用
    本轮原文）——解决「先对话积累上下文，再说一句『整理成设定总集』」这类短指令：
    若用本轮原文判定，能力面收窄与前序产出扫描都会落空（实锤场景）。
    """
    _delivery = delivery_intent if delivery_intent is not None else intent
    work_brief = ""
    try:
        if plan_compiler.is_doc_delegation_intent(_delivery):
            from app.services import fabric_work
            # 缓存不是无脑读取（2026-09-07 用户定案）：前序产出档案的注入量受
            # 上下文预算约束——取 context_max_tokens 的 20% 且封顶 4000 字符；
            # 无上限（0）用默认 4000；预算被调小时缓存自动缩量，不让档案挤占
            # 历史与本体注入，避免「缓存反客为主」。
            _ctx_budget = ctx.get("context_max_tokens")
            _brief_budget = 4000
            if isinstance(_ctx_budget, int) and _ctx_budget > 0:
                _brief_budget = max(800, min(4000, _ctx_budget // 5))
            work_brief = fabric_work.build_work_brief(
                output_dir, repo_id, max_chars=_brief_budget)
    except Exception:  # noqa: BLE001 - 档案生成失败不阻断 run
        work_brief = ""
    return dict(
        intent=intent, history=history,
        capabilities=_fabric_capabilities(_delivery, configured),
        access_mode=access_mode, lease_id=lease_id,
        output_dir=output_dir, repo_id=repo_id,
        configured_models=configured,
        chat_base=ctx["chat_base"], chat_key=ctx["chat_key"],
        chat_model=ctx.get("route_model") or ctx["chat_model"],
        chat_fn=_live_chat(ctx),
        structured_chat_fn=ctx.get("structured_chat_fn"),
        images=list(images or []),
        proxy_kwargs=_proxy_kw(ctx),
        # 联网检索代理（web.search_materials）：与聊天代理分开——本应用把「联网代理」
        # 单独配置（RunContext.proxy_url → ctx["proxy"]），走它访问外网搜索源。
        search_proxy=str(ctx.get("proxy") or ""),
        trace=_live_trace(ctx, "fabric"),
        resume=resume,
        checkpoint_fn=checkpoint_fn,
        system_extra=_fabric_discipline(ctx),
        work_brief=work_brief,
    )


def _collect_fabric_artifacts(ctx: Any, output_dir: str, since: float = 0.0) -> list[dict]:
    """收集**本轮实际写入**的交付物（主卡/世界书），供产物卡展示；失败返回空列表不阻断结果。

    2026-09-09：since = 本 run 开始时间，只收 mtime ≥ since 的产物——否则「目录里最新的
    产物」会把上一轮/历史作品的产物当成本轮产物（实锤：御仙 ST 卡任务显示玫瑰与繁花）。
    since=0 时退回旧行为（全目录最新组），供历史消息补卡等无 run 时间的场景使用。
    2026-09-10 B3/A：repo_id 现在是**真参与域解析**（域 = 作品域 ∪ 本作品拥有的卡目录），
    不再只是软提示——否则收产区是作品库根全域，会把别作品刚写的东西当成本轮产物。
    """
    try:
        from app.services import collection_artifacts
        repo_id = str((ctx or {}).get("repo_id") or "")
        return collection_artifacts.collect_artifacts(output_dir, repo_id=repo_id, since=since)
    except Exception:  # noqa: BLE001 - 产物卡只是展示增强
        return []


def _fabric_finalize(outcome: Any, text: str, trace: list[str],
                     ctx: Any, output_dir: str) -> dict:
    """自由循环终局统一处理（full/approval 共用）：done 带固化询问；error 如实返回。

    2026-09-08：产物卡「随时可见」——done 与未完成（step_limit/error/awaiting_approval）
    都收集当前已落盘的交付物（主卡/世界书）随结果下发；未完成时展示的是**已落盘的部分**
    （如只写了 worldbook.json、还没内嵌主卡）。收集失败不影响结果。
    """
    # 2026-09-09 收紧：仅 done 才下发产物卡（中间「需要批准/执行未完成」不再有卡，
    # 否则前端会把同一批「当前最新产物」挂在所有智能编造消息上）。done 同时存一版快照。
    if outcome.status == "done":
        artifacts: list[dict] = _collect_fabric_artifacts(
            ctx, output_dir, since=float(getattr(outcome, "_started_at", 0.0) or 0.0))
        recipe_marker = ""
        # 事前固化询问的「直接跑」分支（2026-09-10）：用户已经在询问里明确表示不要为
        # 这次任务固化流程。若这里照旧产出草稿卡，等于问完照样弹「保留/不保留」，
        # 那次询问就是纯噪声——所以必须抑制。
        if bool((ctx or {}).get("_suppress_recipe")):
            run_trace.emit(ctx, "fabric.solidified", status="suppressed",
                           reason="用户在事前询问里选择了「直接跑」")
        else:
            try:
                from app.services import plan_tasks as _pt
                recipe = _pt.solidify_steps(
                    intent=text, steps=outcome.steps, output_dir=output_dir, name=text[:40])
                recipe_marker = f"\n\n[[recipe:{recipe['id']}|{recipe['name']}]]"
                run_trace.emit(ctx, "fabric.solidified", status="draft",
                               recipe_id=recipe["id"], steps=len(outcome.steps))
            except ValueError as exc:
                run_trace.emit(ctx, "fabric.solidified", status="skipped", reason=str(exc))
            except Exception as exc:  # noqa: BLE001 - 固化失败不影响本轮结果
                run_trace.emit(ctx, "fabric.solidified", status="error", error=str(exc))
        try:
            from app.services import artifact_versions as _av
            # B3/A：快照存储域 = 作品域（仓库/小仓库文件夹），由 repo_id 解析——
            # 此前落作品库根下的全局 `_versions/`，所有作品的版本混放、回档列表互相串味。
            _av.snapshot_current(output_dir, trigger="done",
                                 summary=outcome.reply[:200] or text[:200],
                                 repo_id=str((ctx or {}).get("repo_id") or ""))
        except Exception:  # noqa: BLE001 - 版本快照失败不影响交付
            pass
        return {"result_text": outcome.reply + recipe_marker,
                "trace": trace + ["🧵 智能编造自由循环完成"],
                "artifacts": artifacts}
    return {"result_text": f"智能编造执行未完成（{outcome.status}）：{outcome.error}",
            "trace": trace + [f"🧵 智能编造循环中断：{outcome.status}"]}


# ── 事前固化询问的答复词（2026-09-10）──────────────────────────────────────
# 刻意与审批词表**不重叠**：审批侧是「批准/同意/执行/确认/好/取消」，其中「好」最容易
# 被审批直通车先吃掉，所以这里一个都不用。默认选项是「直接跑」——用户不回答、或回答
# 别的，都不在这里命中，由闸门按默认处理（不打断为主）。
_RECIPE_OFFER_YES = ("要", "固化", "固化它", "要固化", "固化流程")
_RECIPE_OFFER_NO = ("直接跑", "不用", "跳过", "不固化", "直接处理")
_RECIPE_OFFER_RE = re.compile(
    r"^\s*(" + "|".join(_RECIPE_OFFER_YES + _RECIPE_OFFER_NO) + r")\s*$",
    flags=re.IGNORECASE)


def _recipe_offer_pending(ctx: Any, output_dir: str) -> dict | None:
    """取本会话待确认的事前固化询问（无则 None）。"""
    from app.services import recipe_offer_store
    try:
        return recipe_offer_store.pending(
            output_dir=output_dir, thread_id=str((ctx or {}).get("thread_id") or ""))
    except Exception:  # noqa: BLE001 - 询问不可用时不拦对话
        return None


def _recipe_offer_answer(text: str, ctx: Any, output_dir: str) -> dict | None:
    """消费事前固化询问的答复（2026-09-10）。

    返回 None = 没有待确认询问、或这句话不是在回答询问（调用方继续正常流程）；
    返回 dict = 已消费并**删掉**该询问，调用方按其继续：
    - `answer="solidify"`：照常跑，跑完正常固化（done 时出草稿卡）；
    - `answer="skip"`：照常跑，但抑制本次草稿卡。

    `intent` / `delivery_intent` 回放**询问时存下的原始任务文本**——用户这一句只是
    「要」/「直接跑」，不是任务本身；若拿它当 intent，真正的任务会整个丢失。
    """
    match = _RECIPE_OFFER_RE.match(text or "")
    if match is None:
        return None
    offer = _recipe_offer_pending(ctx, output_dir)
    if offer is None:
        return None
    from app.services import recipe_offer_store
    try:
        recipe_offer_store.delete(str(offer.get("id") or ""))
    except Exception:  # noqa: BLE001 - 删除失败也不阻断本次执行
        pass
    return {
        "answer": "solidify" if match.group(1) in _RECIPE_OFFER_YES else "skip",
        "intent": str(offer.get("intent") or ""),
        "delivery_intent": str(offer.get("delivery_intent") or ""),
    }


def _recipe_hard_replay(hit: dict, score: float, ctx: Any, output_dir: str,
                        trace: list[str]) -> dict | None:
    """命中同类配方时尝试**硬重放**（2026-09-10）。返回 dict=已投递并给出明示；None=不重放。

    只对「内容无关」的配方硬重放：固化把步骤参数原样存下，内容型参数（如
    `doc.create_repo` 的 content）也在里面——硬重放那样的配方只会把当时生成的内容原样
    再写一遍，本轮素材一变就写错了。这类配方改走**骨架复用**（不重放，交给自由循环按
    本次素材重新生成），由既有的配方清单提示词承担，这里只留一条 trace 便于排障。

    「明示 + 可反悔」：投递后如实告知命中了哪条、相似度多少，并指路下方计划卡的「取消」。
    """
    from app.services import recipe_match
    if recipe_match.replay_safety(hit) != "hard":
        run_trace.emit(ctx, "recipe.replay", status="skeleton",
                       recipe_id=hit.get("id"), score=round(score, 3))
        return None
    try:
        from app.services import plan_tasks as _pt
        task = _pt.instantiate_recipe(
            str(hit.get("id") or ""), output_dir=output_dir,
            repo_id=str(ctx.get("repo_id") or ""))
    except Exception as exc:  # noqa: BLE001 - 重放失败就退回自由循环正常编排
        run_trace.emit(ctx, "recipe.replay", status="error", error=str(exc))
        return None
    run_trace.emit(ctx, "recipe.replay", status="hit",
                   recipe_id=hit.get("id"), score=round(score, 3))
    return {
        "result_text": (
            f"检测到与已固化流程《{hit.get('name')}》同类"
            f"（相似度 {round(score * 100)}%），已整条重放，省去重新编排。\n"
            f"计划已投递：{str(task.get('intent') or '')[:120]}\n"
            "审批与配额照常生效，进度见后台活动面板。\n"
            "如果这不是你要的，就在下方计划卡点「取消」，再重发原指令即可。"),
        "trace": trace + [f"🧩 命中固化流程《{hit.get('name')}》，整条重放"]}


def _fabric_approval_word(word: str, ctx: Any, output_dir: str,
                          trace: list[str]) -> dict | None:
    """对话审批词处理 approval 自由循环 checkpoint（2026-09-06）。

    返回 dict=已处理（批准后续跑/取消）；None=没有待审批的自由循环 checkpoint。
    """
    from app.services import capability_sandbox as _sb, fabric_checkpoint, fabric_loop

    pending = fabric_checkpoint.pending(output_dir=output_dir)
    if not pending:
        return None
    cp = pending[0]
    if word in ("取消", "cancel", "撤销"):
        try:
            _sb.revoke(str(cp.get("lease_id") or ""))
        except Exception:  # noqa: BLE001
            pass
        fabric_checkpoint.delete(cp["id"])
        return {"result_text": f"已取消智能编造：{str(cp.get('intent'))[:120]}", "trace": trace}
    # 批准:追加租约授权 → 从断点续跑。
    # running 断点（step_limit/error 后）无 pending_tool，跳过授权直接续跑。
    _pending_tool = str(cp.get("pending_tool") or "")
    if _pending_tool:
        try:
            _sb.grant_operation(str(cp.get("lease_id") or ""), _pending_tool, path=output_dir)
        except Exception as exc:  # noqa: BLE001 - 租约失效则中止
            fabric_checkpoint.delete(cp["id"])
            return {"result_text": f"审批失败：{exc}", "trace": trace}
    configured = {key for key, flag in (("chat", True), ("image", ctx.get("gen_base")),
                                        ("video", ctx.get("vid_base")), ("embed", ctx.get("embed_base")))
                  if flag}
    # 批准后续跑：追加一条「已批准」提示，让模型重新发起被拦操作（否则模型误以为
    # 已执行直接 done——2026-09-06 实锤）。已完成步骤保留不重跑。
    import json as _json
    resume_messages = list(cp.get("messages") or [])
    resume_messages.append({"role": "user", "content": _json.dumps(
        {"approval_granted": f"用户已批准操作「{cp.get('pending_tool') or ''}」。"
                             "请重新发起该操作执行（不要重复其他已完成步骤）。"},
        ensure_ascii=False)})
    # 2026-09-07 治本：续跑也要每步保存断点（重试的资本）——批准后进入新一轮
    # 自由循环，若不带 checkpoint_fn，期间 step_limit/error 时断点停在旧状态，
    # 甚至被误删，用户无法再接续（21:54 实锤：step_limit 后 checkpoint 被清空）。
    cp_id = str(cp.get("id") or "")
    cp_repo = str(cp.get("repo_id") or ctx.get("repo_id") or ctx.get("thread_id") or "")
    cp_intent = str(cp.get("intent") or "")
    cp_history = str(cp.get("history") or "")
    cp_lease = str(cp.get("lease_id") or "")
    # 续跑沿用启动时的判定文本（旧断点无此字段 → 回退 intent），保证能力面/前序产出
    # 档案与首次启动一致（2026-09-10）。
    cp_delivery = str(cp.get("delivery_intent") or cp_intent)

    def _cp_fn(step: int, msgs: list, steps: list, status: str) -> None:
        fabric_checkpoint.save({
            "id": cp_id, "status": status,
            "intent": cp_intent, "history": cp_history,
            "delivery_intent": cp_delivery,
            "messages": msgs, "steps": steps,
            "lease_id": cp_lease, "pending_tool": "",
            "output_dir": output_dir, "repo_id": cp_repo,
            "thread_id": str(ctx.get("thread_id") or ""),
        })

    args = _fabric_run_args(
        ctx, intent=cp_intent, history=cp_history,
        access_mode=_sb.ACCESS_APPROVAL, lease_id=cp_lease,
        output_dir=output_dir,
        repo_id=cp_repo,
        configured=configured, images=[],
        resume={"messages": resume_messages, "steps": cp.get("steps") or []},
        checkpoint_fn=_cp_fn,
        delivery_intent=cp_delivery,
    )
    outcome = fabric_loop.run_loop(**args)
    if outcome.status == "awaiting_approval":
        cp.update({
            "pending_tool": outcome.pending_tool,
            "messages": outcome.messages, "steps": outcome.steps,
            "status": "awaiting_approval",
        })
        fabric_checkpoint.save(cp)
        return {"result_text": f"智能编造需要批准：下一步将「{outcome.pending_tool}」。回复「批准」继续、「取消」中止。",
                "trace": trace + ["🧵 智能编造等待审批"]}
    if outcome.status == "done":
        # 仅完成清理断点；step_limit/error 保留 running 断点供用户再接续
        # （2026-09-07 实锤修复：此前无条件 delete，step_limit 后无法续跑）。
        fabric_checkpoint.delete(cp_id)
    else:
        fabric_checkpoint.save({
            "id": cp_id, "status": "running",
            "intent": cp_intent, "history": cp_history,
            "messages": outcome.messages, "steps": outcome.steps,
            "lease_id": cp_lease, "pending_tool": "",
            "output_dir": output_dir, "repo_id": cp_repo,
            "thread_id": str(ctx.get("thread_id") or ""),
        })
    return _fabric_finalize(outcome, cp_intent, trace, ctx, output_dir)


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
    configured = {
        key for key, flag in (("chat", True), ("image", ctx.get("gen_base")),
                              ("video", ctx.get("vid_base")), ("embed", ctx.get("embed_base")))
        if flag
    }
    # 审批词直通车：对话里发「批准/同意/执行/取消」不再编译新计划，
    # 直接审批/取消最近一个 awaiting_approval 计划（修复 2026-09-02 实锤：
    # 用户发「批准」被当成新任务编译，上下文断档）。
    _approve_word = re.match(r"^\s*(批准|同意|执行|确认|approve|ok|好|取消|cancel|撤销)\s*$",
                             text, flags=re.IGNORECASE)
    if _approve_word:
        from app.services import plan_tasks as _pt
        _word = _approve_word.group(1).lower()
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
    _fabric_judge = intent_text
    if not plan_compiler.is_doc_delegation_intent(_fabric_judge):
        for _h in (ctx.get("history") or []):
            if isinstance(_h, dict) and _h.get("role") == "user":
                _fabric_judge = str(_h.get("content") or "") + "\n" + _fabric_judge
        _fabric_judge = _fabric_judge.strip()
    # ── 事前固化询问（2026-09-10）────────────────────────────────────────────
    # 本轮是「值得固化」的交付类任务、且已保留的固化流程清单里**没有同类**时，先问一句
    # 要不要为它固化一条流程——之后同类请求就能整条重放，省 token。
    # 判定零 LLM（纯本地 shingle/Jaccard），取向是**宁可漏问，不可多问**。
    # 只在「新任务」上问：答复轮已在上面消费掉，不重复问。
    if _offer_recovered is None:
        from app.services import recipe_match, recipe_offer_store
        if recipe_match.is_solidifiable(_fabric_judge):
            _offer_hit, _offer_score = recipe_match.similar_recipe(_fabric_judge)
            if _offer_hit is not None:
                # 清单里已有同类 → 不问，改判能不能硬重放。
                _replay_reply = _recipe_hard_replay(
                    _offer_hit, _offer_score, ctx, output_dir, trace)
                if _replay_reply is not None:
                    return _replay_reply
            else:
                _offer_fp = recipe_match.fingerprint(_fabric_judge)
                _prev_offer = _recipe_offer_pending(ctx, output_dir)
                # 同一意图只打扰一次：用户没理会、又原样重发同一条指令时直接放行去跑。
                if not (_prev_offer and _prev_offer.get("fingerprint") == _offer_fp):
                    recipe_offer_store.save({
                        "id": f"offer:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}",
                        "fingerprint": _offer_fp,
                        "intent": intent_text,
                        "delivery_intent": _fabric_judge,
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
    from app.services import capability_sandbox, fabric_loop, plan_tasks
    if plan_tasks._agent_access_mode() == capability_sandbox.ACCESS_FULL:
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
                "delivery_intent": _fabric_judge,
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
                delivery_intent=_fabric_judge,
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
    if plan_compiler.is_doc_delegation_intent(_fabric_judge):
        # approval 内容生成型：自由循环断点审批（读文件/写条目/落盘逐个确认）
        from app.services import fabric_checkpoint
        lease = capability_sandbox.grant(
            subject=f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}",
            capabilities=[], ttl_seconds=86400, approved_by="pending",
            mode=capability_sandbox.ACCESS_APPROVAL)
        history_text = _fabric_knowledge_history(ctx, text)
        cp_id = f"fabric:{ctx.get('thread_id') or ctx.get('message_id') or 'sess'}"
        repo_id = str(ctx.get("repo_id") or ctx.get("thread_id") or "")
        thread_id = str(ctx.get("thread_id") or "")
        resume: dict | None = None
        # 2026-09-07 重试的资本：启动前检查未完成断点（running/awaiting_approval），
        # 有则自动恢复，不从头重跑（中断/step_limit/error 后不再白跑）。
        # 匹配放宽：同会话（thread_id）有未完成断点即续跑——重发指令时文本略变
        # （加标点/换措辞）也能恢复，不被「intent 不一致」误判从头开始。
        _cp = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
        if _cp is not None:
            resume = {"messages": list(_cp.get("messages") or []),
                      "steps": list(_cp.get("steps") or [])}
        def _cp_fn(step: int, msgs: list, steps: list, status: str) -> None:
            fabric_checkpoint.save({
                "id": cp_id, "status": status,
                "intent": intent_text, "history": history_text,
                "delivery_intent": _fabric_judge,
                "messages": msgs, "steps": steps,
                "lease_id": lease["id"], "pending_tool": "",
                "output_dir": output_dir, "repo_id": repo_id, "thread_id": thread_id,
            })
        # 2026-09-09 harness 自动化：step_limit/error 中断后自动续跑（上限 2 次），
        # 用户发一次指令即可跑完；approval 审批点（awaiting_approval）照常停下等批准。
        outcome: Any = None
        _auto = 0
        while True:
            outcome = fabric_loop.run_loop(**_fabric_run_args(
                ctx, intent=intent_text, history=history_text,
                access_mode=capability_sandbox.ACCESS_APPROVAL,
                lease_id=lease["id"], output_dir=output_dir,
                repo_id=repo_id, configured=configured,
                images=list(state.get("images") or []),
                resume=resume, checkpoint_fn=_cp_fn,
                delivery_intent=_fabric_judge,
            ))
            if outcome.status in ("step_limit", "error") and _auto < 2:
                _cp2 = fabric_checkpoint.resumable(output_dir=output_dir, thread_id=thread_id)
                if _cp2 is None:
                    break
                _auto += 1
                resume = {"messages": list(_cp2.get("messages") or []),
                          "steps": list(_cp2.get("steps") or [])}
                run_trace.emit(ctx, "fabric.auto_resume", round=_auto,
                               status=outcome.status, steps=len(outcome.steps))
                continue
            break
        if outcome.status == "awaiting_approval":
            fabric_checkpoint.save({
                "id": cp_id, "status": "awaiting_approval",
                "intent": intent_text, "history": history_text,
                "delivery_intent": _fabric_judge,
                "messages": outcome.messages, "steps": outcome.steps,
                "lease_id": lease["id"], "pending_tool": outcome.pending_tool,
                "output_dir": output_dir, "repo_id": repo_id, "thread_id": thread_id,
            })
            return {"result_text": f"智能编造需要批准：下一步将「{outcome.pending_tool}」。"
                                   f"回复「批准」继续、「取消」中止。",
                    "trace": trace + ["🧵 智能编造等待审批"]}
        # 2026-09-07 重试资本：仅「完成」清理断点；step_limit/error 保留 running 断点，
        # 用户点「批准继续」可从断点续跑（不再需要重新发完整指令）。
        if outcome.status == "done":
            fabric_checkpoint.delete(cp_id)
        return _fabric_finalize(outcome, intent_text, trace, ctx, output_dir)

    # 编译期预读：本轮消息 + 历史用户消息里显式写出的本地文本文件（仅用户明示的
    # 路径，容量封顶）。历史用户消息兜底「根据上一轮的批量生成进行修改」这类不带
    # 路径的指代——预读必须在编译期完成，执行器无 LLM，file.read_text 读出的内容
    # 填不进 variants（2026-09-03 实锤：模型编了「套装1..10」占位）。
    attachments: list[dict] = []
    prev_batch: dict | None = None
    try:
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
            attachments = attachments[:3]
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
                except Exception:  # noqa: BLE001 - 路径解析失败仍可编译
                    pass
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
        except Exception:  # noqa: BLE001 - 目录不可用时跳过
            pass
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
        except Exception:  # noqa: BLE001 - 模板库不可用时跳过
            pass
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
        # 批量延续（方案 A，2026-09-05 实锤）：续轮编译上下文没有原文档（附件只随
        # 当条消息注入、历史只带可见文本），模型凭记忆重写提示词=幻觉。命中延续
        # 意图时把上一批计划的真实变体注入编译上下文，prompt 由编译期按 name 回填。
        # 判定必须用剥离【文件参考】块后的指令文本（2026-09-05 实锤：100k 字小说
        # 全文会误命中批量词+动作词，把生图上一批注入合集卡任务）。
        compile_text = re.sub(r"【文件参考：.*?【文件参考结束：[^】]*】", "", text, flags=re.S).strip()
        prev_batch = None
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
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "plan.attachments", status="error", error=str(exc))
    # compile_text 已在上方 prev_batch 判定处提前计算（剥离【文件参考】块）；
    # 附件正文已进 attachments，避免大文档全文在 user intent 里再送一遍拖慢编译。
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
        except Exception:  # noqa: BLE001 - 记忆注入永不阻断对话
            pass
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
            scan_chars=int(ctx.get("_worldbook_scan_chars") or 0),
        )
        first_story_reply = not any(
            item.get("role") == "user" and str(item.get("content") or "").strip()
            for item in (ctx.get("history") or [])
        )
        # 上下文合同·历史：剧情模式只进上一次剧情轮，且过滤到只剩正文
        # （剥 think/status/encounter/骰点/插画音频转场 JSON 块）；更早轮次不进上下文。
        story_round = story_history.last_story_round(ctx.get("history") or [])
        run_trace.emit(ctx, "history.slimmed",
                       full_messages=len(ctx.get("history") or []),
                       injected_messages=len(story_round),
                       injected_chars=sum(len(m["content"]) for m in story_round))
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
        # 文风（去AI味）配置：每轮读一次用户态文件，失败回退内置默认（enabled+零增删）。
        try:
            from app.services import prose_style
            ctx["_style_config"] = prose_style.load_config()
        except Exception as exc:  # noqa: BLE001 文风配置损坏不能阻断正文
            run_trace.emit(ctx, "prose_style.config", status="error", error=str(exc))
        # 阶段 A：世界提案 + 裁判（默认每轮判断一次：judge.gateBaseRate=1.0 / gateFloor=-100，
        # 设 0 才显式关闭；gate 未命中/失败时 directive 空，塌回单次 LLM 零额外成本）
        directive, lost = _agency_propose(ctx, deps, affinity, wb, text)
        # 有激活偏置预设 → 按预设 prompt_order 组装带 role 的多条消息（marker 填卡字段/世界书，
        # chatHistory 处原位插历史）；否则内置扮演提示。dialogue = 少样本片段 + 历史（真实多轮，不折叠）
        # scene 由 supervisor 那次调用分类后写入 ctx（P2）→ 驱动 scene 条件链；无则空串（只命中无条件链）
        preset_msgs, preset_temp, has_hist, chains_tail, chains_head = _resolve_preset(
            ctx, wb, scene=ctx.get("scene") or "", affinity=affinity, turn=turn,
            history=story_round)
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
            table_recall = _table_recall_text(ctx, repo_id, retrieval_query)
            # Recall 只做检索，不独立调 LLM：往事纪要（人物相关优先，简要 Top-10）
            # + 知识库/检索表候选原样并入 GrayWill 主请求，由主模型结合世界书、历史与本轮输入一次判断并生成。
            recall = roleplay_agency.recall_chronicle(
                deps, repo_id=repo_id, query=retrieval_query, rag_text=rag_text,
                actors=[
                    name for name in (ctx.get("illustration_actor_names") or [])
                    if name and name in retrieval_query
                ] or ([ctx.get("card_name")] if ctx.get("card_name") else []),
            )
            if recall:
                _probe.info("🔎[RAG召回] repo=%s 注入%d字:\n%s", repo_id, len(recall), recall[:800])
                run_trace.emit(ctx, "rag.injected", status="ok", content=recall,
                               char_count=len(recall))
            else:
                _probe.info("🔎[RAG召回] repo=%s 无命中（本轮未注入记忆）", repo_id)
                run_trace.emit(ctx, "rag.injected", status="empty", content="", char_count=0)
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
                base += "\n\n" + compiled.text
            if table_recall:
                base += "\n\n【相关数据表行（独立配额）】\n" + table_recall
            run_trace.emit(
                ctx, "continuity.compiled", tokens=compiled.tokens,
                sources=list(compiled.included), fact_count=len(active_facts),
            )
            base += directive + roleplay_agency.state_instruction()
            # S1 生成侧预防：从同一词表编译文风约束段（enabled=False → 空串，system 逐字节不变）。
            _style_cfg = ctx.get("_style_config") or {}
            if _style_cfg.get("enabled", True):
                from app.services import prose_style as _prose_style
                _style_seg = _prose_style.style_prompt_segment(_style_cfg)
                if _style_seg:
                    base += _style_seg
                    run_trace.emit(ctx, "prose_style.injected",
                                   words=len(_prose_style.effective_phrases(_style_cfg)))
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
                from app.services import table_store, table_update
                _tables = table_store.load(ctx.get("output_dir") or "", repo_id)
                turn_tables = table_store.tables_for_read(_tables)
                if turn_tables:
                    base += table_update.table_context(turn_tables)
                    run_trace.emit(
                        ctx, "table.prompt", status="read_only", turn=turn,
                        tables=[t.get("name", "") for t in turn_tables],
                    )
                else:
                    run_trace.emit(
                        ctx, "table.prompt", status="skipped", turn=turn,
                        reason="no_tables",
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
        # 思考纪律（2026-08-29 验收「正文内容极其少」：模型反复推翻已定结论、多次宣称动笔又回退，
        # think 烧光输出额度正文 0 字）。软引导而非硬限制——只禁「回退重确认」这类浪费模式，
        # 不限制正常推演深度；正文预算永远优先（max_tokens 已放宽，见 _roleplay_sampling）。
        tail_msgs.append({
            "role": "system",
            "content": (
                "[输出纪律] 思考是决策摘要，不是草稿纸。思考只允许包含三类内容：对用户输入的"
                "一次性解读、本轮关键抉择（骰点/立场/结构）各定一次的结论、不可省略的因果依据。\n"
                "硬性禁区（逐条对照，违反任意一条=白烧正文额度）：\n"
                "一、禁止在思考里试写正文、台词或任何成段草稿——正文全文只写一遍，"
                "直接写进最终输出，写进思考的那份纯属双倍付费；\n"
                "二、禁止复述设定、人物档案、好感分阶、输出规则等上下文已有内容——"
                "它们已经注入在场，思考里引用结论即可，不得朗读原文；\n"
                "三、骰点/意图解读/叙事结构各只许判定一轮：禁止「不对，让我重新看看」式的推翻"
                "重来，禁止规划两版结构再二选一，禁止「现在真的开始写了」式的假开场；\n"
                "四、禁止输出 <status>/<encounter>/<roll>/<content>/<状态更新> 等任何标签块的"
                "草稿或片段（包括只开未闭的标签）——这些块只允许在全部正文之后正式输出一次，"
                "在思考里预写会让解析层错位配对，吞掉正文与状态栏；"
                "五、思考总长度硬上限：**不得超过 300 字**。思考是决策摘要，不是草稿纸；"
                "超长思考会烧光正文输出额度，导致正文为零——宁可少想，把篇幅全部留给正文。"
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


def _agency_writeback(ctx: dict, deps, reply: str, turn: int, affinity,
                      lost: bool, rag_events: list | None = None,
                      user_text: str = "") -> tuple[str, list, dict, dict]:
    """阶段 C+D：剥离 <状态更新> 写回 → 判插画 → 提取对白配音。返回（去块正文, image_recs, illustrate_req, audio_req）。

    illustrate_req：comfy_illustrate 时高潮点产出的出图请求 {prompt}；前端据本地预设模板走异步 ComfyUI 闭环。
    audio_req：comfy_audio 时产出的对白配音请求 {lines:[{speaker,text,emotion}]}；前端逐角色提交 IndexTTS。
    非 comfy 路径为空 dict。rag_events：可选，收集 RAG 创建（纪要/知识库）状态供前端弹窗。"""
    try:
        from app.services import character_state, roleplay_agency
        from app.services import image_prompt_extract, scene_classify
        from app.services import story_frames, transition_extract
        from app.services.regex_engine import Placement
        repo_id = ctx.get("repo_id") or ctx.get("thread_id") or ""
        card_name = ctx.get("card_name") or ""
        # 根因修复（2026-08-31 实锤 3389/11977：幻影协议标签跨界匹配吞正文）：think 是
        # 输出协议的第一块，块提取链只应作用于其后的正文。think 内复述协议清单产生的
        # 幻影开标签会让各提取器的懒匹配从 think 内一路吃到真块闭合，把 </think> 与
        # 全部正文整段当块剥掉。拆出 think 前缀原样保留（前端正则折叠为思考过程），
        # 提取链只看正文，发布时拼回前缀。
        think_head, body = roleplay_agency.split_think_prefix(reply)
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
        try:
            from app.services import narrative_ci

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
        # 阶段 D：插画（renderer=None 时 maybe_illustrate 直接返回 None）；用去块正文当段落
        scene = ctx.get("scene") or ""
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
        # 插画提示词直接由正文 + 已有视觉锚组装，不再额外调用一次聊天模型。
        visible_story = image_prompt_extract.visible_narrative_text(clean)
        # 音频对白配音请求：comfy_audio 时组装（含机械降级兜底），随 writeback 返回给前端逐角色提交。
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
        local_scene = scene_classify.infer_scene(
            "\n".join((user_text, visible_story)),
        )
        from app.services import scene_illustration
        local_scene_fallback = (
            not illustration_plan and local_scene in ("nsfw", "climax")
        )
        # ComfyUI 自动插画开启后，主模型即使没带内联计划，也不能让整条请求静默消失。
        # 普通 dialogue/action 直接复用本轮正文最强视觉段落；完整 Profile 由独立 LLM 链
        # （_profile_llm_fallback）或本地事实编译提供，前端无需再补调文本模型。
        missing_plan_fallback = bool(
            ctx.get("comfy_illustrate") and not illustration_plan and visible_story
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
                or local_scene_fallback or missing_plan_fallback
                or first_story_reply or character_encounter
            )
        )
        prompt_override, motion, actors = "", 0, []
        image_rating = (
            "nsfw" if scene in ("nsfw", "climax")
            or local_scene in ("nsfw", "climax") else "sfw"
        )
        if illustration_plan:
            prompt_override = _apply_regex(
                ctx, illustration_plan["prompt"], Placement.IMAGE_PROMPT, is_prompt=True).strip()
            motion = illustration_plan["motion"]
            actors = illustration_plan["actors"]
        elif ctx.get("comfy_illustrate") and local_scene_fallback:
            prompt_override = image_prompt_extract.build_fallback_content_tags(
                "\n".join((user_text, visible_story)),
            )
            motion = image_prompt_extract.infer_motion(visible_story)
            actors = []
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
            # actors 只表示画面真实在场角色；配置全集仅用于正文精确补漏，不能整体塞入，
            # 否则前端会给未出场角色加载 LoRA。
            _known = [
                str(name).strip() for name in (ctx.get("illustration_actor_names") or [])
                if str(name).strip()
            ]
            _actor_values = list(actors)
            if ctx.get("appearance_source") == "worldbook" and card_name:
                # 世界书作品的 card_name 是作品/父仓库名，不是角色名。旧前端曾把它
                # 混进候选全集，导致本地降级把作品名精确命中并回退风格 LoRA。
                _known = [name for name in _known if name != card_name]
                _actor_values = [name for name in _actor_values if name != card_name]
            scene_actor_text = encounter_narrative if character_encounter else visible_story
            _scene_text = "\n".join(filter(None, (scene_actor_text, user_text, present)))
            _climax_paragraph = (
                "" if illustration_plan else scene_illustration.fallback_illustration_anchor(
                    scene_actor_text)
            )
            _absent = _repo_table_absent_actors(ctx, repo_id, _known)
            request_actors = _resolve_illustration_request_actors(
                _known,
                planned=_actor_values if illustration_plan else [],
                user_text=user_text,
                narrative=scene_actor_text,
                present=present,
                encounter=encounter_actors if character_encounter else [],
                priority_text=_climax_paragraph,
                absent=_absent,
            )
            if not request_actors:
                request_actors = list(dict.fromkeys(
                    [name for name in _actor_values
                     if (not _known or name in _known) and name not in _absent] + (
                        [card_name]
                        if card_name and ctx.get("appearance_source") != "worldbook" else []
                    ),
                ))
            request_appearance = _filter_illustration_appearance(
                _illustration_appearance(ctx), request_actors, _known,
            )
            request_prompt = prompt_override.strip()
            prompt_source = "extracted"
            if at_climax and not request_prompt:
                request_prompt = scene_illustration.build_scene_request(
                    paragraph=encounter_narrative if character_encounter else visible_story,
                    appearance=request_appearance,
                    wardrobe=wardrobe,
                    locale=locale,
                    actors=request_actors,
                ).prompt
                prompt_source = "fallback"
            fallback_anchor = ""
            if character_encounter:
                fallback_anchor = encounter_anchor
            elif visible_story and (
                local_scene_fallback or missing_plan_fallback or first_story_reply
            ) and not illustration_plan:
                fallback_anchor = scene_illustration.fallback_illustration_anchor(clean)
            planned_anchor = illustration_plan.get("anchor", "")
            requested_anchor = planned_anchor or fallback_anchor
            if illustration_plan:
                requested_anchor = scene_illustration.resolve_illustration_anchor(
                    clean, requested_anchor,
                )
            plan_retargeted = bool(
                illustration_plan and planned_anchor
                and image_prompt_extract.restore_jailbreak(planned_anchor).strip()
                != image_prompt_extract.restore_jailbreak(requested_anchor).strip()
            )
            if plan_retargeted:
                corrected_scene = scene_illustration.illustration_scene_excerpt(
                    visible_story, requested_anchor,
                )
                # 重定向只替换错误高潮动作，不能把 Krea2 的英文动作底座也清空；
                # 否则独立 Profile 一旦拒答，只能退回没有角色和剧情事实的通用模板。
                request_prompt = image_prompt_extract.build_fallback_content_tags(corrected_scene)
                motion = image_prompt_extract.infer_motion(corrected_scene)
                # 高潮重定向只纠正动作与锚点；主计划 subjects 已通过配置角色全集
                # 精确校验，是角色身份真源。代词化正文不应把这些角色覆盖为空。
                retarget_actors = [
                    name for name in _known
                    if name in corrected_scene or name in present
                ]
                request_actors = list(dict.fromkeys(request_actors + retarget_actors))
            scene_narrative = encounter_narrative if character_encounter else (
                    scene_illustration.illustration_scene_excerpt(
                        visible_story, requested_anchor,
                    )
                )
            final_actors = _resolve_illustration_request_actors(
                _known,
                planned=_actor_values if illustration_plan else [],
                user_text=user_text,
                narrative=scene_narrative,
                present=present,
                encounter=encounter_actors if character_encounter else [],
                priority_text=_climax_paragraph,
                absent=_absent,
            )
            if final_actors:
                request_actors = final_actors
            request_appearance = _filter_illustration_appearance(
                _illustration_appearance(ctx), request_actors, _known,
            )
            request_appearance = scene_illustration.supplement_actor_table_appearance(
                request_appearance, request_actors, _repo_tables_for_facts(ctx, repo_id),
            )
            profile_draft_prompt = request_prompt
            if not illustration_plan:
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
                "wardrobe": wardrobe,
                "locale": locale,
                "actors": request_actors,
                "rating": image_rating,
                "aspect_ratio": (
                    "" if plan_retargeted else illustration_plan.get("aspect_ratio")
                ) or (
                    "4:3" if character_encounter else scene_illustration.infer_aspect_ratio(
                        _scene_text, request_actors,
                    )
                ),
                "profile": ctx.get("prompt_profile") or "anima_tags",
            }
            if ctx.get("appearance_source") in {"worldbook", "character_card"}:
                scene_spec["appearance_source"] = ctx.get("appearance_source")
            if illustration_plan.get("subjects"):
                # 主计划的英文主体描述是拒答降级时仍可用的身份真源；即使高潮锚点
                # 被纠正，稳定外貌不会随动作重定向而失效。
                selected_subjects = [
                    subject for subject in illustration_plan["subjects"]
                    if str(subject.get("name") or "").strip() in request_actors
                ]
                if selected_subjects:
                    scene_spec["subjects"] = selected_subjects
            if illustration_plan.get("visual_facts"):
                visual_facts = illustration_plan["visual_facts"]
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
                scene_spec["encounter"] = encounter_facts
            if not plan_retargeted and illustration_plan.get("art_direction"):
                scene_spec["art_direction"] = illustration_plan["art_direction"]
            if not plan_retargeted and illustration_plan.get("camera"):
                scene_spec["camera"] = illustration_plan["camera"]
            if not plan_retargeted and illustration_plan.get("composition"):
                scene_spec["composition"] = illustration_plan["composition"]
            if not plan_retargeted and illustration_plan.get("action_sequence"):
                scene_spec["action_sequence"] = illustration_plan["action_sequence"]
            from app.services import image_prompt_profiles
            # illustration JSON 已在解析前复用正文的 AI_OUTPUT 正则；成稿再叠加
            # IMAGE_PROMPT 专用清洗。
            # 高潮锚点被纠正时，旧成稿描述的是错误桥段，必须丢弃并从纠正后的事实本地编译。
            inline_profile = ""
            if illustration_plan and (
                not plan_retargeted or bool(scene_spec.get("visual_facts"))
            ):
                # 锚点被纠正但计划中仍有逐字证据落在纠正后窗口时，保留 Agent 已完成的
                # 具体英文画面；normalize/字段账本仍会淘汰格式错误或事实不覆盖的成稿。
                inline_profile = str(illustration_plan.get("profile_prompt") or "")
                inline_profile = _apply_regex(
                    ctx, inline_profile, Placement.IMAGE_PROMPT, is_prompt=True, depth=0,
                )
            compiled_profile = image_prompt_profiles.normalize_inline(
                scene_spec["profile"], inline_profile, scene_spec,
            )
            profile_strategy = "same_turn" if illustration_plan else "independent_chain"
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
            ) and "Required visible facts:" in compiled_profile:
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
            )
            profile_negative = image_prompt_profiles.negative_prompt(
                ctx.get("prompt_profile") or "anima_tags", scene_spec,
            )
            if profile_negative:
                scene_spec["negative_prompt"] = profile_negative
            # Profile：同轮成稿（模型仍自发带块时）优先；否则由携带防拦截预设的独立
            # LLM 链（_profile_llm_fallback）编译，仍失败才走本地事实兜底。
            request_prompt = image_prompt_extract.format_comfy_prompt(request_prompt)
            illustrate_req = (
                {"prompt": request_prompt, "motion": motion, "actors": request_actors,
                 "anchor": requested_anchor, "scene_spec": scene_spec,
                 # V1.5 默认开放：视频配置随事件透传，供 dry-run 组装「上交视频模型的参数」
                 # （测试视频参数有没有正确上传；无视频工作流/节点也不影响出图）
                 "video_config": {
                     "base_url": str(ctx.get("vid_base") or ""),
                     "model": str(ctx.get("vid_model") or ""),
                     "size": "1280x720",
                     "proxy": str(ctx.get("vid_proxy") or ""),
                 },
                 "allow_anchor_fallback": (
                     bool(visible_story) and (
                         local_scene_fallback or missing_plan_fallback
                         or first_story_reply or character_encounter
                     )
                 ) and not illustration_plan}
                if at_climax and (request_prompt or scene_spec["narrative"]) else {}
            )
            # V1.5/W2：首帧复用决策合并（坑B/坑I）——L0 确定 → L0；L0 ambiguous → 消费 L1 <transition>。
            # N 尾帧从历史最近角色回复提取（方案 B，零 wire），N+1 首帧从当前正文提取；合并结果
            # 三态（reuse/regenerate/ambiguous）随出图请求透传，前端叠加坑C「有图前提」裁决。
            if illustrate_req:
                # 2026-08-29 验收「帧提示词编译失败」实锤：scene_spec 只随 climax 请求下发，
                # first_story_reply 等非高潮路径的首尾帧请求拿不到 sceneSpec → 前端帧编译被
                # 整段跳过 → 触发「禁止中文原文直提」守卫 → 首帧必失败。首尾帧编译以帧描述
                # 为主输入、scene_spec 提供角色外貌等事实参照，必须恒定随请求下发。
                illustrate_req.setdefault("scene_spec", scene_spec)
                _prev_tail_desc = _resolve_prev_tail_desc(ctx)
                _frames = story_frames.extract_story_frames(clean)
                _merged = story_frames.merge_frame_reuse(
                    _prev_tail_desc, _frames.opening, transition_decision,
                )
                illustrate_req["transition"] = _merged.decision
                # V1.6/W3：首尾帧描述 + 上尾帧描述随事件下发（firstlast 生图 + 转场编译的素材源）。
                # climax 也带（无害冗余，前端非 firstlast 忽略）；首帧复用决策用 opening 同源，不重复提取。
                illustrate_req["first_frame_desc"] = _frames.opening[:500].strip()
                illustrate_req["last_frame_desc"] = _frames.closing[:500].strip()
                illustrate_req["prev_tail_desc"] = (_prev_tail_desc or "")[:500].strip()
            # 正常链路：comfy_video 开启时 produce 层编译视频提示词 + 参数，
            # 随事件下发；失败静默降级 None，不阻断出图。
            # 三模态开关：comfy_video 关=不调 _extract_video_action_plan（省 LLM 调用）、
            # 不编译 video_request/transition_video_request（省 token 干烧），完全对齐图/音链的关=零成本。
            _video_prompt_text = ""
            if illustrate_req and ctx.get("comfy_video"):
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
                    _video_prompt_text = str(
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
            run_trace.emit(
                ctx,
                "illustration.request",
                status="emitted" if illustrate_req else "skipped",
                reason=("main_plan_retargeted" if plan_retargeted and illustrate_req else
                        "main_plan" if illustration_plan and illustrate_req else
                        "character_encounter" if character_encounter and illustrate_req else
                        "local_scene_fallback" if local_scene_fallback and illustrate_req else
                        "first_story_reply" if first_story_reply and illustrate_req else
                        "missing_plan_fallback" if missing_plan_fallback and illustrate_req else
                        prompt_source if illustrate_req else
                        "scene_not_triggered" if not at_climax else "empty_prompt"),
                scene=scene,
                inferred_scene=local_scene,
                actor_count=len(request_actors),
                actors=request_actors,
                actor_candidates=_known,
                status_actors=[name for name in _known if name in present],
                plan_retargeted=plan_retargeted,
                prompt_chars=len(request_prompt),
                # V1.5 默认开放：视频生成提示词记入 trace（测试模式核对提示词质量）
                video_prompt_chars=len(_video_prompt_text),
                video_prompt=_video_prompt_text,
            )
            return think_head + clean, [], illustrate_req, audio_req
        illo = roleplay_agency.maybe_illustrate(
            deps, paragraph=clean, appearance=_illustration_appearance(ctx),
            wardrobe=wardrobe, locale=locale,
            actors=actors or ([card_name] if card_name else []), before=before, after=after,
            turn=turn, cadence=0, explicit=bool(illustration_plan), lost=lost,
            scene=scene, prompt_override=prompt_override,
            character_encounter=character_encounter)
        if illo:
            rec = {"id": f"illo-{repo_id}-{turn}", "url": illo["url"], "caption": illo["caption"]}
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
    """在最终显示正文中定位插画槽；本地兜底 anchor 被正则改写时重新选高潮段。"""
    from app.services import scene_illustration

    # 首尾帧模式：主槽=首帧图（尾帧 :last 副槽由前端追加楼层末尾），首帧画面锚正文
    # 第一段——主图用的「高潮纠偏/末段兜底」会把开篇铺垫改判到中央/末尾
    # （2026-08-29 用户验收问题①），firstlast 不走那套纠偏。
    if str(request.get("video_mode") or "") == "firstlast":
        return scene_illustration.first_frame_anchor_offset(reply)
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
    # 音频对白配音与插画同属即时通道：漏发会导致 eager 分支跳过 audio_request，
    # 前端永远收不到台词（日志有 emit、SSE 无事件）。格式对齐 agent_graph 的 yield。
    for rec in out.get("audio_recs") or []:
        sink({"audio_request": {"lines": rec.get("lines") or []}, "id": rec.get("id")})
    return True


_MAINTENANCE_THREADS: dict[str, list[threading.Thread]] = {}
_MAINTENANCE_LOCKS: dict[str, threading.Lock] = {}
_MAINTENANCE_THREADS_LOCK = threading.Lock()


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
        _table_maintenance(ctx, repo_id, clean, turn)
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
        selected = table_store.tables_for_maintenance(tables, scheduled)
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


# M1 审计 #7（2026-09-06）：文本→向量进程内 LRU 缓存——fabric 每轮装配对同一批
# 历史重复嵌入（本地 ollama 逐条 HTTP，实测长历史 1-3s/轮），缓存后稳态零网络调用。
# 键含模型名（换嵌入模型不串向量）；容量 256 条 ≈ 2MB，超限丢最旧。
_history_embed_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_history_embed_lock = threading.Lock()
_HISTORY_EMBED_CACHE_CAP = 256


def _history_embed_fn(ctx: dict):
    """P3①（2026-09-06）：历史语义分档的批量嵌入函数（context_gate 可选增强）。

    缺嵌入配置 → None（context_gate 纯词表分档）；返回 callable(list[str]) →
    list[list[float]]。嵌入失败由 context_gate 静默吞掉退回词表，此处不兜异常——
    保证「语义是增强不是依赖」的边界只在一个地方实现。
    """
    cfg = _embed_cfg(ctx)
    if cfg is None:
        return None
    from app.services import rag_backend

    prefix = f"{cfg.mode}|{cfg.embed_model}|"

    def _embed(texts: list[str]) -> list[list[float]]:
        with _history_embed_lock:
            missing = [t for t in texts if prefix + t not in _history_embed_cache]
        if missing:
            vecs = rag_backend.embeddings(cfg).embed_documents(missing)
            with _history_embed_lock:
                for t, v in zip(missing, vecs):
                    _history_embed_cache[prefix + t] = v
                    _history_embed_cache.move_to_end(prefix + t)
                while len(_history_embed_cache) > _HISTORY_EMBED_CACHE_CAP:
                    _history_embed_cache.popitem(last=False)
        with _history_embed_lock:
            return [list(_history_embed_cache[prefix + t]) for t in texts]

    return _embed


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
        candidates = rag_store.retrieve_with_trace(
            repo_id, cfg, query, k=max(k * 2, 12), include_system=False)
        hits = [hit for hit in candidates if hit.get("kind") != "table_row"][:k]
    except Exception as exc:  # noqa: BLE001  召回失败不阻断叙述
        run_trace.emit(ctx, "rag.retrieve", status="error", query=query, error=str(exc))
        return ""
    run_trace.emit(ctx, "rag.retrieve", status="ok", query=query, hit_count=len(hits), hits=hits)
    return "\n".join(
        f"- {hit.get('content', '')}" for hit in hits if (hit.get("content") or "").strip()
    )


def _table_recall_text(ctx: dict, repo_id: str, query: str, k: int = 5) -> str:
    """检索表专属读通道；与普通知识 RAG 分池、分配额。"""
    try:
        from app.services import table_store
        tables = table_store.load(ctx.get("output_dir") or "", repo_id)
        rows = table_store.recall_retrieval_rows(tables, query, k=k)
    except Exception as exc:  # noqa: BLE001
        run_trace.emit(ctx, "table.retrieve", status="error", query=query, error=str(exc))
        return ""
    run_trace.emit(ctx, "table.retrieve", status="ok", query=query, hit_count=len(rows), hits=rows)
    return "\n".join(f"- {row}" for row in rows)


def _repo_tables_for_facts(ctx: dict, repo_id: str) -> list:
    """插画视觉事实兜底读通道：整读 通用表（含 重要角色表），不检索、不分配额。

    世界书视觉锚按条目名匹配，剧情常用名/道号可能连不上（舞姬恋↔角色卡·舞柔）；
    表格按剧情进度维护、姓名与正文一致，作为未命中角色的【外貌】/【穿着】真源。
    读失败静默空表——事实兜底缺失不阻断出图，由 illustration.profile 的
    appearance_missing_actors 告警暴露。
    """
    if not repo_id:
        return []
    try:
        from app.services import table_store
        return table_store.load(ctx.get("output_dir") or "", repo_id)
    except Exception:  # noqa: BLE001
        return []


def _repo_table_absent_actors(ctx: dict, repo_id: str, known: list[str]) -> set:
    """表格在场状态里明确「不在场」的绑定角色——点名提及不等于画面在场。

    2026-09-01 用户实锤：高潮段只有凌若冰（浮花泉封域内），舞姬恋在封域外石廊
    （表格在场状态=不在场），但正文/状态栏都点名了她 → request_actors 把两人
    都列入，提示词写成 2girls。姓名+在场状态列是剧情进度维护的真源，优先于点名。
    """
    if not repo_id or not known:
        return set()
    try:
        tables = _repo_tables_for_facts(ctx, repo_id)
        absent: set = set()
        known_set = set(known)
        for table in tables:
            if not isinstance(table, dict):
                continue
            cols = table.get("columns") or []
            if "姓名" not in cols or "在场状态" not in cols:
                continue
            name_idx = cols.index("姓名")
            state_idx = cols.index("在场状态")
            for row in (table.get("rows") or []):
                if not isinstance(row, list) or len(row) <= max(name_idx, state_idx):
                    continue
                name = str(row[name_idx]).strip()
                state = str(row[state_idx]).strip()
                if name in known_set and state.startswith("不在场"):
                    absent.add(name)
        return absent
    except Exception:  # noqa: BLE001 - 表格在场状态是兜底约束，读失败不阻断出图
        return set()


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
    personalities: list[str] = []
    scenarios: list[str] = []
    dialogue_examples: list[str] = []
    injected_names: list[str] = []
    try:
        from app.services import character_store, instruction_provenance
        for name in selected:
            base, card_name = _card_source(ctx, name)
            card = character_store.read_card(base, card_name) if base and card_name else None
            description = str((card or {}).get("description") or "").strip()
            if not description:
                continue
            profiles.append(instruction_provenance.wrap(
                f"角色卡：{card_name}",
                f"【角色：{card_name}】\n{description}",
            ))
            for key, target in (
                ("personality", personalities),
                ("scenario", scenarios),
                ("mes_example", dialogue_examples),
            ):
                value = str((card or {}).get(key) or "").strip()
                if value:
                    target.append(instruction_provenance.wrap(
                        f"角色卡：{card_name}:{key}",
                        f"【角色：{card_name}】\n{value}",
                    ))
            injected_names.append(card_name)
    except Exception:  # noqa: BLE001
        profiles = []
        personalities = []
        scenarios = []
        dialogue_examples = []
        injected_names = []
    ctx["_selected_persona_names"] = injected_names
    ctx["_selected_persona_personality"] = "\n\n".join(personalities)
    ctx["_selected_persona_scenario"] = "\n\n".join(scenarios)
    ctx["_selected_persona_examples"] = "\n\n".join(dialogue_examples)
    if not profiles:
        return ""
    selection = (
        "【本轮角色卡描述】只按角色名使用下列实际出场角色的描述；"
        "不得把一名角色的外貌、经历或行为特征转移给另一名角色。"
    )
    return selection + "\n\n" + "\n\n".join(profiles)


def _profile_llm_fallback(ctx: dict, scene_spec: dict[str, Any]) -> tuple[str, str]:
    """同轮成稿被清空（锚点重定向且无存活视觉事实）时，补一次携带当前防拦截预设的
    LLM 调用，按纠正后正文重写图像 Profile（Krea2 英文画面）。

    这是「防拦截生效」的兜底：旧实现在此直接掉本地模板、完全没有 LLM 参与，
    防拦截预设自然无从谈起。这里复用 image_prompt_profiles.generate 的
    system/校验/重写链，并用 system_with_preset 把当前防拦截预设接到独立调用上；
    scene_spec 里的 protected_narrative（防拦截原文）经 _scene_for_model 作为模型输入，
    本地校验则用 _scene_for_facts 的还原事实，两层各司其职。
    返回 (compiled_profile, strategy)；失败返回 ("", "")，调用方回退本地事实兜底。
    """
    profile = str(scene_spec.get("profile") or "anima_tags")
    if not (ctx.get("chat_base") and ctx.get("chat_key") and ctx.get("chat_model")):
        return "", ""
    if not str(scene_spec.get("narrative") or "").strip():
        return "", ""
    from app.services import image_prompt_profiles

    def _generate(system: str, user: str) -> str:
        # 2026-09-01 用户定案：插画兜底 LLM 也走流式，实时读 token 输入输出，
        # 30s 无 token 即断（llm.build_model streaming 读超时），不再用 60s 总超时。
        guarded = image_prompt_profiles.system_with_preset(
            system, scene_spec,
            preset_dir=str(ctx.get("preset_dir") or ""),
            preset_name=str(ctx.get("preset_name") or ""),
            user_name=str(ctx.get("user_name") or ""),
            # 2026-09-01 用户定案：生图提示词要像剧情一样稳定——带完整预设防拦截。
            head_only=False,
        )
        started = time.monotonic()
        chars = [0]
        last_token = [started]
        last_heartbeat = [started]

        def _on_delta(delta: str) -> None:
            now = time.monotonic()
            last_token[0] = now
            chars[0] += len(delta)
            if now - last_heartbeat[0] >= 20:
                last_heartbeat[0] = now
                try:
                    run_trace.emit(
                        ctx, "model.stream_progress", agent="illustration_fallback",
                        elapsed_seconds=round(now - started, 1),
                        visible_chars=chars[0],
                    )
                except Exception:
                    pass

        return _llm.chat_messages_stream(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"],
            [{"role": "system", "content": guarded}, {"role": "user", "content": user}],
            on_delta=_on_delta, temperature=0.4, retries=2, **_proxy_kw(ctx),
            provider_profile=ctx.get("provider_profile") or "openai_compatible",
        )

    diagnostics: dict[str, object] = {}
    try:
        compiled = image_prompt_profiles.generate(
            profile, scene_spec, _generate, diagnostics,
        )
    except Exception:  # noqa: BLE001
        run_trace.emit(ctx, "illustration.profile_llm_fallback", status="error")
        return "", ""
    if not compiled or not str(compiled).strip():
        return "", ""
    strategy = str(diagnostics.get("strategy") or "llm_retargeted")
    strategy_map = {
        "direct": "llm_retargeted",
        "repaired": "llm_retargeted+repair",
        "fallback": "llm_retargeted_fallback",
    }
    final_strategy = strategy_map.get(strategy, strategy)
    run_trace.emit(
        ctx, "illustration.profile_llm_fallback",
        status="ok", strategy=final_strategy, output_chars=len(compiled),
        field_ledger=diagnostics.get("field_ledger"),
        first_errors=diagnostics.get("first_errors") or [],
        repair_errors=diagnostics.get("repair_errors") or [],
    )
    return compiled, final_strategy


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
    except Exception:  # noqa: BLE001
        pass
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
        scan = _worldbook_scan_text(ctx, query)
        ctx["_worldbook_scan_chars"] = len(scan)
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
        # 提取世界规则条目（含约束词的 entry）供 Narrative CI 的世界规则诊断
        _RULE_HINT_RE = re.compile(
            r"(?:不可|禁止|不得|必须|应当|务必|严禁|绝不|永远不要|只有|唯一)", re.IGNORECASE
        )
        ctx["_world_rules"] = [
            entry.content.strip()
            for entry in entries
            if _RULE_HINT_RE.search(entry.content or "")
        ][:20]
        return selection.text
    except Exception:  # noqa: BLE001
        return ""


def _worldbook_scan_text(ctx: dict, query: str, *, history_chars: int = 1800) -> str:
    """世界书激活窗口：本轮输入 + 最近一组对话，不扫描整段旧历史。"""
    recent: list[str] = []
    for message in reversed(ctx.get("history") or []):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        recent.append(content)
        if len(recent) >= 2:
            break
    history = "\n".join(reversed(recent))[-max(0, history_chars):]
    return "\n".join(part for part in (history, (query or "").strip()) if part).strip()


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

    def context(window_text: str) -> str:
        # 2026-08-30 成本实锤：整本世界书快照 ≈2万字符/轮（JSON 转义后实测 4.4 万字符）。
        # curator 的世界书改写范围本就限定在本轮相关条目（allowed），视图只给这些条目、
        # 按与正文的命中排序并截断到片段预算——改写能力保留，全书参照撤销。
        return worldbook_store.repo_snapshot_context(
            base, repo_id, query=window_text or "",
            max_chars=6_000, allowed_indices=allowed,
        )

    return context


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
        rejections: list[dict[str, object]] = []
        applied = worldbook_store.apply_repo_ops(
            base, repo_id, ops, allowed_update_indices=allowed,
            rejections=rejections,
        )
        if rejections:
            run_trace.emit(
                ctx, "worldbook.ops_rejected", repo_id=repo_id, rejections=rejections,
            )
        return applied

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
    history: list[dict] | None = None,
) -> tuple[list[dict], float | None, bool, list[str], list[str]]:
    """有激活偏置预设 → 组装带 role 的多条消息 + 采样温度 + 是否含历史 marker + 命中的思维链(尾/头)。
    无预设/读不到 → ([], None, False, [], [])。

    保留每片段自身 role（system/user/assistant 少样本片段不再被折叠），chatHistory marker 处原位
    插入历史（ST 深度注入语义）。marker 填充：卡字段 + 世界书 + 用户人设。
    history=None 读 ctx 全量历史；剧情模式传入瘦身后的上一次剧情轮（story_history.last_story_round）。
    思维链按 scene/affinity/turn 真状态条件选（select_chains），尾部注入遵守最严、头部随 system。
    """
    ctx["_preset_sampling"] = {}
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
        history = (ctx.get("history") or []) if history is None else history
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
            "char_personality": str(ctx.get("_selected_persona_personality") or "").strip(),
            "scenario": str(ctx.get("_selected_persona_scenario") or "").strip(),
            "dialogue_examples": str(ctx.get("_selected_persona_examples") or "").strip(),
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
        ctx["_preset_sampling"] = params
        # 2026-09-04 设置整合：trace 里的 temperature 取 settings 全局（权威源），
        # preset 顶层 sampling 字段保留但仅作旧 settings 迁移期兼容——新链路已迁移完毕。
        # 若 settings 缺该字段，回退 preset 字段。
        _ps = ctx.get("preset_sampling") if isinstance(ctx.get("preset_sampling"), dict) else {}
        _t = _ps.get("temperature")
        if not isinstance(_t, (int, float)) or isinstance(_t, bool):
            _t = params.get("temperature")
        temp = _t
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


def _notify_stream_trace(ctx: dict, message: str) -> None:
    """把自愈进度提示推入流式通道（仅流式模式）。

    截断自愈重试期间思考阶段不进流式通道，气泡会静默冻结数分钟（2026-08-31 用户
    反馈「正文生成完毕一会儿报错」像卡死）。提示走 trace 事件追加进气泡，最终被
    replace 覆盖不残留；非流式模式无此问题，走 trace 列表照常展示。
    """
    if not _stream_enabled(ctx):
        return
    sink = ctx.get("stream_sink")
    try:
        sink({"trace": message})
    except Exception:  # noqa: BLE001 - 提示失败不阻断生成主流程
        pass


def _resume_interrupted_messages(wire_messages: list[dict]) -> list[dict]:
    """中断续写（2026-09-01 用户需求）：上一轮被打断的半成品正文不要从头再来。

    chat_memory.append_turn / generation_store.persist_text(interrupted=True) 会给半成品
    assistant 正文追加「（已打断）」后缀（chat_snapshot.INTERRUPT_MARKER 唯一属主）。这里把
    后缀剥掉、原样保留为最后一条 assistant（预填语义），再追加续写指令：从断点继续、
    不输出 think、闭合 </content>。识别依赖文本后缀（快照/上传链只带 role+content，
    2026-09-04 收口：persist_text 落盘已统一拼后缀，前端停止时亦补同标记）。

    指令落位（2026-09-04 文件级冒烟实锤）：roleplay 的 wire 总是以**本轮 user 结尾**
    （[system, *dialogue, *tail_msgs, user(text)]），不能简单 append——否则形成
    [assistant 半成品, user 本轮输入, user 续写指令] 双 user：本轮真实输入被夹成历史、
    续写指令语义稀释。命中时若尾部恰为本轮 user，把该 user **替换**为「续写指令 + 原输入
    拼接」，让模型对同一条 user 作答（先紧接半句续写、再自然衔接用户最新输入）。
    """
    from app.services import chat_snapshot
    marker = chat_snapshot.INTERRUPT_MARKER
    for idx in range(len(wire_messages) - 1, -1, -1):
        message = wire_messages[idx]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "")
        if not content.endswith(marker):
            return wire_messages
        partial = content[:-len(marker)].rstrip()
        if len(partial) < 200:
            # 半成品太短没有续写价值：保留原样（新轮重写比续写更稳）——但要把「（已打断）」
            # 标记剥掉再返回，防止标记字面混进模型输入、被照抄进正文（2026-09-04 收口）。
            resumed = [*wire_messages]
            resumed[idx] = {**message, "content": partial}
            return resumed
        instruction = (
            "你上一条消息在正文中间被打断，正文没有写完。现在从断点直接继续：\n"
            "一、第一个字必须紧接已写出的最后半句，直接续写正文剩余部分——"
            "禁止重复已有内容、禁止改写或重新开始；\n"
            "二、本次不要输出 <think> 思考块，直接续写；\n"
            "三、正文写完后闭合 </content>，再按协议正常输出后续块。"
        )
        resumed = [*wire_messages]
        resumed[idx] = {**message, "content": partial}
        if (idx < len(wire_messages) - 1 and isinstance(wire_messages[-1], dict)
                and wire_messages[-1].get("role") == "user"):
            # wire 以本轮 user 结尾（roleplay 主链形态）：替换尾部 user 为续写指令 + 原输入，
            # 避免双 user 让本轮输入被夹成历史（2026-09-04 收口补充）。
            tail_user = str(wire_messages[-1].get("content") or "").strip()
            resumed[-1] = {
                "role": "user",
                "content": instruction
                + (f"\n四、半句接上、正文写完后，再自然衔接用户本轮输入：{tail_user}"
                   if tail_user else ""),
            }
        else:
            # 历史以 assistant 结尾（无独立尾部 user，如精简/单测形态）：直接追加指令。
            resumed.append({"role": "user", "content": instruction})
        return resumed
    return wire_messages


def _roleplay_continuation_messages(wire_messages: list[dict], partial: str,
                                    *, think_truncated: bool = False) -> list[dict]:
    """截断续写的消息序列：残缺输出回喂为最后一条 assistant + 断点续写指令（2026-08-31）。

    残缺输出全文回喂（含 think 与 @逐字@ 段落），保证续写的格式与文风接得上；
    指令要求从断点直接续写、不重复不重开。残缺输出保持 assistant 身份追加，
    不破坏 system/对话/尾部合同的既有顺序。

    think_truncated=True（思考阶段截断，2026-08-31 晚定案）：残缺输出是**同一条
    输出流的前缀**（think 开而未闭），指令改为从断点闭合 </think> 后再按协议
    输出正文——角色层（roleplay_turn._try_think_continuation）用原文直连拼接，
    不做 think 剥离。
    """
    if think_truncated:
        instruction = (
            "你上一条消息在思考阶段被截断，<think> 标签没有闭合，正文还没有开始。"
            "现在从断点直接继续（你写的内容会与已有部分无缝拼接为一个完整回复）：\n"
            "一、第一个字必须紧接已写出的思考内容的最后一个字，先把剩余思考写完，"
            "并尽快收敛、闭合 </think>——禁止重复或改写已有思考内容、禁止重新开始；"
            "**剩余思考最多再写 200 字，必须立即闭合 </think>**；\n"
            "二、</think> 之后严格按既有协议继续输出：<content>正文</content> 与后续块，"
            "格式与之前保持完全一致（包括逐字间隔标记等既有格式）；\n"
            "三、把剩余篇幅全部留给正文，正文必须写完整；再超长思考等于白烧付费额度。"
        )
    else:
        instruction = (
            "你上一条消息在正文中间被截断，<content> 没有闭合。现在从断点直接继续：\n"
            "一、第一个字必须紧接你已写出的最后半句，直接续写正文剩余部分——"
            "禁止重复已有内容、禁止改写或重新开始；\n"
            "二、格式与已写正文保持完全一致（包括逐字间隔标记等既有格式）；\n"
            "三、正文写完后闭合 </content>，再按协议正常输出后续块；\n"
            "四、本次不要输出 <think> 思考块，直接续写。"
        )
    return [*wire_messages,
            {"role": "assistant", "content": partial},
            {"role": "user", "content": instruction}]


def _chat_with_optional_stream(ctx: dict, messages: list[dict], *, temperature: float,
                               top_p: float | None = None,
                               max_tokens: int | None = None,
                               max_duration_seconds: float | None = None) -> str:
    """按本轮设置选择整段或流式调用；流式增量直接送入 runner 队列。

    成功后把模型 usage（prompt/completion/cached token 等）以 model.usage trace 事件
    落盘，供 Provider 缓存命中率与成本观测。
    """
    agent_name = str(ctx.get("current_agent") or "roleplay")
    model_name = str(ctx.get("chat_model") or "")

    def _emit_usage(stats: dict) -> None:
        try:
            run_trace.emit(
                ctx, "model.usage", agent=agent_name, model=model_name, usage=stats,
            )
        except Exception:
            pass

    def _emit_finish(meta: dict) -> None:
        # 结束原因观测：finish_reason=length → 输出上限被掐；空 → 流被中途中断
        #（中转掐流不给结束原因）。2026-08-31 正文截断诊断用。
        try:
            run_trace.emit(ctx, "model.finish", agent=agent_name, model=model_name, **meta)
        except Exception:
            pass

    if not _stream_enabled(ctx):
        # 非流式也走可取消通道（2026-09-04 A 方案）：整段 invoke 期间零取消检查点，
        # 用户点停止只能等整段模型跑完（_tmp/smoke_pause_real.py 实锤复现）。关闭流式
        # 输出只表示不逐字推给前端，不等于放弃停止权——这里改走底层流式请求 + 本地
        # 聚合（不推 sink），外部语义仍是「一次性返回完整文本」，但每个 delta 到达都
        # 检查 cancel_event，置位立即抛 RoleplayInterrupted 中止本次调用。
        # 超时沿用原非流式宽限（代理 120s / 公网 200s），避免首 token 慢的模型被
        # 流式默认 30s 读超时误掐（2026-08-31 定案：正常吐字 1s 不到，30s 静默=卡死，
        # 但那是针对真流式的；非流式聚合场景首 token 冷启动可能远超 30s）。
        _chat_proxy = (ctx.get("chat_proxy") or "").strip()

        def _plain_collect_check(_raw: str) -> None:
            # 每个 delta 到达即查用户停止（流式层取消检查点，见 _check_deadline 注释）
            if roleplay_turn._user_interrupted(ctx):
                raise roleplay_turn.RoleplayInterrupted("用户已停止生成")

        return _llm.chat_messages_stream(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"], messages,
            on_delta=_plain_collect_check, temperature=temperature, **_proxy_kw(ctx),
            top_p=top_p, max_tokens=max_tokens,
            provider_profile=ctx.get("provider_profile") or "openai_compatible",
            timeout_override=120.0 if _chat_proxy else 200.0,
            on_usage=_emit_usage,
        )

    from app.services.stream_text import VisibleTextStream

    sink = ctx.get("stream_sink")
    deadline = ctx.get("_selfheal_deadline")
    started = time.monotonic()
    # 实时 token 流监测（2026-08-31 深夜用户要求）：正常吐字 1 秒都不到，30s 无任何
    # token 由 llm 层读超时掐断；这里每 20s 发一次心跳 trace，结束发摘要（时长/字数/
    # 最大静默间隔），trace 里一眼能看流是否还活着、卡了多久。
    last_token_at = [started]
    last_heartbeat = [started]
    visible_chars = [0]
    thinking_chars = [0]
    max_gap = [0.0]

    def _touch_stream(visible: int, thinking: int) -> None:
        now = time.monotonic()
        gap = now - last_token_at[0]
        if gap > max_gap[0]:
            max_gap[0] = gap
        last_token_at[0] = now
        visible_chars[0] += visible
        thinking_chars[0] += thinking
        if now - last_heartbeat[0] >= 20:
            last_heartbeat[0] = now
            try:
                run_trace.emit(
                    ctx, "model.stream_progress", agent=agent_name,
                    elapsed_seconds=round(now - started, 1),
                    visible_chars=visible_chars[0],
                    thinking_chars=thinking_chars[0],
                )
            except Exception:
                pass

    def _check_deadline() -> None:
        # 用户停止（2026-09-04 事故实锤）：正文流此前只查超时不查 cancel_event——
        # 前端「停止」置位后单次流照常跑完、selfheal 还整段 reroll 从头，白烧 token。
        # 每收到一个 token 即查，置位立即中止本次流（复用超时抛错中止的既有模式）。
        if roleplay_turn._user_interrupted(ctx):
            raise roleplay_turn.RoleplayInterrupted("用户已停止生成")
        # 自愈调用限时（2026-08-31 晚实锤：续写流式 10 分钟不返回）。只在仍有增量时
        # 检查；增量长时间不来由 httpx 超时兜底。
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("自愈调用超时，中止本次生成")
        # 初始生成总时长上限（2026-08-31 深夜实锤：首发流式卡 4.5 分钟不结束；
        # 思考面板折叠时用户看到的就是「卡住不继续」）。到点中止，交由自愈循环处理。
        if max_duration_seconds is not None and time.monotonic() - started > max_duration_seconds:
            raise TimeoutError("生成总时长超时，中止本次生成")

    def on_hidden(name: str, text: str) -> None:
        _check_deadline()
        if name == "think" and text:
            _touch_stream(0, len(text))
            # 思考全公开（2026-08-31 晚用户定调）：think 块内容实时送前端思考面板，
            # 正文 delta 仍只含可见文本，两不污染；其余隐藏块（状态更新等）不送。
            sink({"thinking": text})

    visible = VisibleTextStream(on_hidden=on_hidden)

    def on_delta(raw: str) -> None:
        _check_deadline()
        text = visible.feed(raw)
        if text:
            _touch_stream(len(text), 0)
            sink({"delta": text})

    try:
        return _llm.chat_messages_stream(
            ctx["chat_base"], ctx["chat_key"], ctx["chat_model"], messages,
            on_delta=on_delta, temperature=temperature, **_proxy_kw(ctx),
            top_p=top_p, max_tokens=max_tokens,
            provider_profile=ctx.get("provider_profile") or "openai_compatible",
            on_usage=_emit_usage, on_finish=_emit_finish,
        )
    finally:
        tail = visible.finish()
        if tail:
            sink({"delta": tail})
        try:
            run_trace.emit(
                ctx, "model.stream", agent=agent_name,
                duration_seconds=round(time.monotonic() - started, 1),
                visible_chars=visible_chars[0],
                thinking_chars=thinking_chars[0],
                max_gap_seconds=round(max_gap[0], 1),
            )
        except Exception:
            pass


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


def stream_multi_agent(context: RunContext) -> Iterator[dict]:
    """运行 supervisor 多 Agent 图；HTTP/SSE wire 由 runner/router 适配。"""
    context.agent_cfg = _resolve_agent_cfg(context.agent_id)
    context.builtin = builtin_agents.resolved()  # ③ 内置 Agent 生效参数（默认+用户覆盖），供各节点取
    context.has_mcp = _has_mcp(context.agent_cfg)
    context.history = agent_context.recent_history(
        context.thread_id,
        max_tokens=context.context_max_tokens,
        per_role=context.context_count,
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
    except Exception:  # noqa: BLE001 - 路径提取失败不影响主流程
        pass
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
