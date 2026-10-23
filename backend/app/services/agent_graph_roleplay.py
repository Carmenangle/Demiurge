"""Roleplay 剧情回合节点域：roleplay_node + 22 个 _rp_* 接缝 + 采样助手 + _ROLEPLAY_BASE。

从 agent_graph.py 按域迁出（方案 B，2026-10-23）：搬移块 sha1 逐字节一致，
旧名经 agent_graph re-export 零破坏。向下 import agent_graph_state。
⚠ monkeypatch 接缝：本域函数的补丁（_resolve_worldbook/_resolve_preset/_rag_recall_text/
_table_recall_text/_agency_*/_builtin 等）必须打**本模块**——消费方在 _rp_*/roleplay_node。"""

from __future__ import annotations

from app.services.agent_contracts import CtxLike

import logging
import re

from app.services import llm as _llm, run_trace, builtin_agents, story_history, agent_context, prompt_compiler, roleplay_turn
from app.services.agent_agency import _builtin_sampling, _builtin, _illustration_visual_query, _agency_maintenance_async, _agency_prelude, _agency_propose, _agency_writeback, _emit_roleplay_ready, _illustration_anchor_offset
from app.services.agent_context_assembly import _apply_regex, _resolve_worldbook, _recent_character_context, _resolve_personas, _render_user_persona, _temperature, _card_visual_profiles, _agent_system, _resolve_preset
from app.services.agent_graph_state import AgentState
from app.services.agent_retrieval import _rag_recall_text, _table_recall_text
from app.services.agent_stream import _notify_stream_trace, _chat_with_optional_stream, _resume_interrupted_messages, _roleplay_continuation_messages, _roleplay_failure_result, _stream_enabled

_probe = logging.getLogger("uvicorn.error")


def _probe_think(reply: str) -> str:
    """从回复里抽 <think>…</think> 思考段（GrayWill 等预设的 CoT）。无则空串。"""
    m = re.search(r"<think>([\s\S]*?)</think>", reply or "", re.IGNORECASE)
    return m.group(1).strip() if m else ""

def _roleplay_sampling(ctx: CtxLike) -> dict:
    """正文额度优先取「智能体参数预设」（settings 全局，2026-09-04 设置整合后权威来源）；
    preset 顶层不再管理采样/输入参数。分析/状态/骰点/插画均在正文额度之外。

    preset_sampling 来自 ctx.preset_sampling（前端 settings payload 注入），逐字段覆盖
    builtin sampling；缺省字段保留 builtin 默认（向后兼容老 settings）。
    """
    sampling = _builtin_sampling(ctx, "roleplay")
    # 2026-09-04：ctx.preset_sampling 取代 ctx["_preset_sampling"]（旧字段来自 preset_store.sampling_params，
    # 已废弃）。settings 缺字段时只覆盖提供的键，未提供的仍用 builtin default。
    preset_sampling = ctx.get("preset_sampling")
    if not isinstance(preset_sampling, dict):
        preset_sampling = {}
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
    # 2026-10-20：真机 `max_tokens=4000` 导致每轮长生成在 4000 tokens 处
    # `finish_reason=length` 截断（18:15/18:16 两次实锤），随后自愈连环重掷。
    # 但三个来源（预设 openai_max_tokens / 内置 agent maxTokens / context_max_tokens）
    # 在旧 trace 里分不清 ⇒ 落一条「实际值 + 来源」。
    try:
        run_trace.emit(
            ctx, "sampling.resolved", agent="roleplay",
            max_tokens=sampling.get("max_tokens"),
            source=("preset.openai_max_tokens" if preset_max is not None
                    else "context_max_tokens" if "max_tokens" in sampling
                    else "builtin.maxTokens" if "max_tokens" in _builtin_sampling(ctx, "roleplay")
                    else "none"),
            preset_max=preset_max,
            preset_sampling_keys=sorted(str(k) for k in preset_sampling.keys()),
            context_max_tokens=ctx.get("context_max_tokens"),
            model=str(ctx.get("chat_model") or ""),
        )
    except Exception:  # noqa: BLE001 [EXC-保留] 观测失败不得打断采样裁决
        pass
    return sampling

def _roleplay_sampling_kwargs(ctx: CtxLike) -> dict:
    """正文采样参数（**已按接收方签名过滤**）——`roleplay_node` 四处调用点共用。

    2026-10-20 两次实锤（用户「明明没点停止却显示已停止生成」）：
    ① `RunContext._legacy()` 漏 `preset_sampling` 键 ⇒ sampling 恒为空 ⇒ 下游
       `temperature=temp, **roleplay_sampling` 从不冲突（潜伏）；
    ② 修好 `_legacy()` 后 sampling 第一次带值，先炸 `temperature` 重复传参，
       再炸 `top_k` / `frequency_penalty` / `presence_penalty` 是
       `_chat_with_optional_stream` 根本不认识的参数。

    ⇒ **黑名单（逐个剔）永远追不上预设新增字段**。这里改为按接收方
    `_chat_with_optional_stream` 的**真实签名**做白名单过滤：签名自省、不手写名单，
    日后给该函数加参数会自动放开，给它删参数也不会再炸。
    （`temperature` 也在表外：一律以调用点的 `temp` 为准。）
    """
    sampling = _roleplay_sampling(ctx)
    return {key: value for key, value in sampling.items() if key in _stream_sampling_keys()}

# `_chat_with_optional_stream` 接受的采样参数名（懒加载缓存，2026-10-23 随消费方整体迁入）。
_STREAM_SAMPLING_KEYS: frozenset[str] | None = None


def _stream_sampling_keys() -> frozenset[str]:
    """`_chat_with_optional_stream` 真实接受的采样参数名（签名自省，懒加载缓存）。

    刻意运行时取而不是 import 时取：`agent_stream` 与本模块相互引用，
    顶层 import 会成环。
    """
    global _STREAM_SAMPLING_KEYS
    if _STREAM_SAMPLING_KEYS is None:
        import inspect as _inspect

        from app.services import agent_stream as _agent_stream

        _STREAM_SAMPLING_KEYS = frozenset(
            _inspect.signature(_agent_stream._chat_with_optional_stream).parameters
        ) - {"ctx", "messages", "temperature"}
    return _STREAM_SAMPLING_KEYS

# 剧情扮演默认提示词由 builtin_agents 单一属主（③ 可覆盖）；别名保留旧引用不破坏。
_ROLEPLAY_BASE = builtin_agents.ROLEPLAY_BASE

def _rp_prepare_worldbook(ctx: CtxLike, text: str) -> tuple[str, str]:
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

def _rp_emit_worldbook_resolved(ctx: CtxLike, wb: str, wb_recall: str) -> None:
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

def _rp_is_first_story_reply(ctx: CtxLike) -> bool:
    """**纯判定**：本会话是否还没有任何非空用户发言（= 开场首轮）。

    逐字自 `roleplay_node` 搬移（唯一改动＝首行 `first_story_reply = ` → `return `）。
    """
    return not any(
        item.get("role") == "user" and str(item.get("content") or "").strip()
        for item in (ctx.get("history") or [])
    )

def _rp_last_story_round(ctx: CtxLike) -> list:
    """上下文合同·历史：取上一次剧情轮并**瘦到只剩正文**，再派发 `history.slimmed`。

    逐字自 `roleplay_node` 搬移。
    """
    story_round = story_history.last_story_round(ctx.get("history") or [])
    run_trace.emit(ctx, "history.slimmed",
                   full_messages=len(ctx.get("history") or []),
                   injected_messages=len(story_round),
                   injected_chars=sum(len(m["content"]) for m in story_round))
    return story_round

def _rp_resolve_persona_block(ctx: CtxLike, text: str, first_story_reply: bool) -> None:
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

def _rp_load_style_config(ctx: CtxLike) -> None:
    """每轮读一次文风（去 AI 味）用户态配置，写回 `ctx["_style_config"]`；

    损坏不能阻断正文 ⇒ 只留痕。逐字自 `roleplay_node` 搬移。
    """
    try:
        from app.services import prose_style
        ctx["_style_config"] = prose_style.load_config()
    except Exception as exc:  # noqa: BLE001 文风配置损坏不能阻断正文
        run_trace.emit(ctx, "prose_style.config", status="error", error=str(exc))

def _rp_build_prompt_base(ctx: CtxLike, *, wb: str, preset_msgs: list, preset_temp,
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

def _rp_seed_dynamic_parts(ctx: CtxLike, wb_recall: str, dynamic_parts: list) -> None:
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

def _rp_append_static_blocks(ctx: CtxLike, base: str, chains_head: list) -> str:
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

def _rp_substitute_macros(ctx: CtxLike, base: str, dynamic_parts: list) -> tuple[str, str]:
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

def _rp_macro_substitute_text(ctx: CtxLike, text: str) -> str:
    """对单段文本替换 `{{char}}`/`{{user}}`（缺省 user 回退「我」）。

    directive 从动态块移到尾部最后一条 system 后不再经过 `_rp_substitute_macros`，
    必须在此再替换一次，避免字面宏漏进 system 被模型照抄进正文。
    """
    from app.services import preset_store as _ps2
    if not (text or "").strip():
        return ""
    return _ps2.substitute_macros(text, {
        "char_name": (ctx.get("card_name") or "").strip(),
        "user_name": (ctx.get("user_name") or "").strip(),
    })

def _rp_pop_agency_directive(ctx: CtxLike) -> str:
    """取出本轮的 World 裁定 directive（含宏替换），供尾部最后一条 system 注入。

    空 directive → 空串 ⇒ 既有消息组逐字不变（等价矩阵不破）。
    """
    return _rp_macro_substitute_text(ctx, str(ctx.pop("_rp_agency_directive", "") or ""))

def _rp_build_tail_messages(ctx: CtxLike, dynamic_text: str, chains_tail: list,
                            user_text: str = "") -> list[dict]:
    """组装 system **头之后**的尾部 system 消息，
    顺序＝[动态块][思维链 tail][输出纪律][音频][用户安排][World 裁定]。

    World 裁定（2026-10-21）落最后一条 system：紧邻本轮 user，近端权重最高，
    避免「判定失败/事件登门」被用户安排块覆盖；空 directive ⇒ 不追加。

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
    # 2026-10-21：World 裁定落**最后一条** system（紧邻本轮 user，近端权重最高），
    # 让「判定失败/事件登门」不被用户安排块覆盖。空 directive 不追加（消息组逐字不变）。
    directive = _rp_pop_agency_directive(ctx)
    if directive:
        tail_msgs.append({"role": "system", "content": directive})
    return tail_msgs

def _rp_recall_memory(ctx: CtxLike, *, deps, text: str) -> tuple[str, str, str]:
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
            str(name) for name in (ctx.get("illustration_actor_names") or [])
            if name and name in retrieval_query
        ] or ([str(ctx.get("card_name"))] if ctx.get("card_name") else []),
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

def _rp_flush_citation_trace(ctx: CtxLike, trace: list) -> None:
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
    ctx: CtxLike, *, repo_id: str, turn: int, st_block: str, recall: str,
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

def _rp_append_state_and_style(ctx: CtxLike, base: str, directive: str, dynamic_parts: list) -> str:
    """⑤⑥ 搭车状态指令（静态、随 system 头）+ directive（每轮变 → 后置）+ 文风约束段。

    ⚠ 就地 mutate 传入的 `dynamic_parts`；`base` 片段内被重绑定 ⇒ 以返回值回传。
    逐字自 `_rp_inject_agency_context` 搬移。
    """
    from app.services import roleplay_agency   # 原由 ① 段局部 import，拆开后本段自带（幂等）
    # 搭车指令是固定文本（跨轮逐字节相同）→ 留静态头；本轮自主行动裁定 directive
    # 每轮变 → 后置（P4-roleplay）。2026-10-21 世界事件司门：directive 不再留在动态块
    # （会排在尾部消息前段、被末位用户安排压过），改由 `_rp_build_tail_messages` 落到
    # **最后一条 system**（紧邻本轮 user），确保裁定结果不被用户安排覆盖。
    base += roleplay_agency.state_instruction()
    if directive:
        ctx["_rp_agency_directive"] = directive
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
    ctx: CtxLike, *, deps, repo_id: str, text: str, turn: int, dynamic_parts: list,
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
        # 2026-10-20 观测补口：外貌资料的取用情况此前**完全不可见**。
        # 真机线索：世界书注入了 25 条（含「角色卡·腓特烈大帝」），可提示词只有 245 字、
        # `actor_count=0` ⇒ 无法判断是「外貌没查到」还是「查到了但被演员过滤抹掉」。
        # 这里落：来源（世界书/角色卡）、查询串长度、取回字符数、段落头名单。
        try:
            import re as _re

            _heads = _re.findall(r"(?m)^\s*([^\s：:]{1,20})\s*[：:]", visual_profiles or "")
            run_trace.emit(
                ctx, "illustration.visual_profiles",
                source=("character_card" if ctx.get("appearance_source") == "character_card"
                        else "worldbook"),
                query_chars=len(visual_query or ""),
                profiles_chars=len(visual_profiles or ""),
                profile_heads=_heads[:8],
            )
        except Exception:  # noqa: BLE001 [EXC-保留] 观测失败不得打断插画链
            pass
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
    ctx: CtxLike, *, deps, turn: int, st_block: str, text: str, base: str,
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
        roleplay_sampling_kwargs = _roleplay_sampling_kwargs(ctx)
        run_trace.emit(ctx, "model.request", agent="roleplay", model=ctx["chat_model"],
                       messages=wire_messages, preset=ctx.get("preset_name") or "",
                       temperature=temp, provider_profile=compiled_prompt.provider_profile,
                       prompt_manifest=compiled_prompt.manifest, **roleplay_sampling_kwargs)
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
                    **roleplay_sampling_kwargs,
                ),
                generated=_generated,
                notify=lambda message: _notify_stream_trace(ctx, message),
                continue_generate=lambda partial: _chat_with_optional_stream(
                    ctx, _roleplay_continuation_messages(
                        wire_messages, partial,
                        think_truncated=roleplay_turn.think_truncated(partial),
                    ),
                    temperature=temp, **roleplay_sampling_kwargs,
                ),
                # 剧情续写（2026-09-21）：结构合格但剧情没写完时的补写通道。与截断续写
                # 分开接线——两者的指令语义不同（截断＝闭合标签；剧情＝接着往下写）。
                continue_story=lambda partial: _chat_with_optional_stream(
                    ctx, _roleplay_continuation_messages(
                        wire_messages, partial, story_incomplete=True,
                    ),
                    temperature=temp, **roleplay_sampling_kwargs,
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
        # 失败收尾下沉接缝（2026-10-20）：错误留痕 + 已收半成品按中断语义落库，
        # 让下一轮 `_resume_interrupted_messages` 能接着写（不是从头再来）。
        return _roleplay_failure_result(ctx, trace, streamed, e)

