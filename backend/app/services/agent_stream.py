"""流式/事件域助手（原 agent_graph F 簇流式部分，逐字节搬移）。

逐字节自 agent_graph.py 搬移（方案 B 第二步之 F，见 docs/memory/agent-graph-seam-plan-2026-09-16.md）；
行为等价由既有测试网 + 搬移块 sha1 校验保证。
"""

from __future__ import annotations
import time
from app.services import roleplay_turn, run_trace
from app.services import llm as _llm
from app.services.agent_support import (  # noqa: F401  跨簇底座
    _proxy_kw,
)
from app.services.agent_agency import (  # noqa: F401  跨簇底座
    _video_params_payload,
    _video_request_for,
)

def _stream_enabled(ctx: dict) -> bool:
    return bool(ctx.get("stream_output")) and callable(ctx.get("stream_sink"))


def _notify_stream_trace(ctx: dict, message: str, detail: str = "") -> None:
    """把自愈进度提示推入流式通道（仅流式模式）。

    截断自愈重试期间思考阶段不进流式通道，气泡会静默冻结数分钟（2026-08-31 用户
    反馈「正文生成完毕一会儿报错」像卡死）。提示走 trace 事件追加进气泡，最终被
    replace 覆盖不残留；非流式模式无此问题，走 trace 列表照常展示。
    ② 纪要引用透出（2026-09-18）：可选 detail 随行下发（前端执行面板折叠卡明细）；
    无 detail 时线格式与改动前逐字节一致（旧前端宽松兼容）。
    """
    if not _stream_enabled(ctx):
        return
    sink = ctx.get("stream_sink")
    try:
        payload: dict = {"trace": message}
        if detail:
            payload["detail"] = detail
        sink(payload)
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
            if roleplay_turn.user_interrupted(ctx):
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
        if roleplay_turn.user_interrupted(ctx):
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
