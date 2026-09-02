"""Roleplay turn finalization transaction.

Visible text and its illustration request are published before maintenance. The Agent turn only
finishes after maintenance, while ComfyUI continues independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable


class TruncatedRoleplayOutput(RuntimeError):
    """The provider ended a response after opening, but before closing, visible content."""


# think 剥离：闭合块 + 未闭合直达结尾的尾部（截断发生在思考阶段时正文尚未开始）。
_THINK_CLOSED = re.compile(r"<think\b.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_THINK_UNCLOSED = re.compile(r"<think\b.*\Z", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think\b[^>]*>", re.IGNORECASE)

# 续写自愈门槛：断点前可见正文达到该字数才值得续写（太短时整段重掷更省更稳）。
CONTINUATION_MIN_BODY_CHARS = 300
# 续写新增正文的最低字数：低于此视为懒闭合/无进展（模型直接闭合了标签），回退整段重掷。
CONTINUATION_MIN_NEW_CHARS = 30
# 截断自愈次数默认值（每次=一次模型调用：续写或重掷）。2026-08-31 晚用户定调：
# 重试次数太少是「付费零产出」的帮凶（turn 12 重试 1 次仍截断即弃），上限 1 → 3。
# 运行值可在 设置→AI 模型「截断自愈次数」调整（selfheal_attempts，0=不自愈，钳位 0–5）。
SELFHEAL_MAX_ATTEMPTS = 3
# 自愈限时（2026-08-31 晚实锤 turn13：思考续写单次流式拖了 10 分钟，整轮自愈 15 分钟
# 才报错——付费用户等不起）。单次自愈调用最长 3 分钟；整轮自愈总预算 6 分钟。
SELFHEAL_CALL_TIMEOUT_SECONDS = 180.0
SELFHEAL_TOTAL_BUDGET_SECONDS = 360.0


def _set_selfheal_deadline(ctx: Any, deadline: float | None) -> None:
    """把自愈调用截止时间透传给流式层（RunContext 与 dict 两种 ctx 都兼容）。

    流式层在收到下一个 delta/thinking 时检查，超时立即抛 TimeoutError 中止本次调用；
    非流式走 httpx 自身超时，不强依赖这里。
    """
    if isinstance(ctx, dict):
        if deadline is None:
            ctx.pop("_selfheal_deadline", None)
        else:
            ctx["_selfheal_deadline"] = deadline
        return
    try:
        extras = getattr(ctx, "extras", None)
        if isinstance(extras, dict):
            if deadline is None:
                extras.pop("_selfheal_deadline", None)
            else:
                extras["_selfheal_deadline"] = deadline
    except Exception:  # noqa: BLE001 - 限时是尽力而为，设不上就退回 httpx 超时
        pass


def ensure_complete_visible_content(reply: str) -> None:
    # 计数前剥离 think 段：模型推理常复述协议字面量（「检查 <content> 标签」），
    # 这些引用不是输出结构——2026-08-29 trace 实证 think 内 2 次字面量 <content>
    # 导致真实正文完好却被误判截断。
    visible = _THINK_CLOSED.sub("", reply or "")
    # 未闭合 think = 提供商在思考阶段掐断（2026-08-30 实锤：conn 抖动下 think-only
    # 残缺回复通过旧放行逻辑，replace 用它覆盖流式正文、生图锚进思考块）。必须判截断。
    if _THINK_OPEN.search(visible):
        raise TruncatedRoleplayOutput("模型输出在思考阶段被截断")
    visible = _THINK_UNCLOSED.sub("", visible)
    opened = len(re.findall(r"<content\b[^>]*>", visible, flags=re.I))
    closed = len(re.findall(r"</content\s*>", visible, flags=re.I))
    if opened > closed:
        raise TruncatedRoleplayOutput("模型输出在正文结束前被截断")
    # 2026-08-31 深夜 turn14 实锤：模型只输出 <status>/<roll> 却一个 <content> 都没开，
    # 旧校验放行 → 发布后正文消失（用户：正文被删掉）。剧情轮必须有 <content> 正文块。
    if opened == 0:
        raise TruncatedRoleplayOutput("模型输出缺少正文块（没有 <content>）")
    # 剥完思考后没有可见正文：think-only 回复同样不替换流式正文（剧情轮必须有正文）
    if not visible.strip():
        raise TruncatedRoleplayOutput("模型只返回了思考没有正文")
    blocks = re.findall(r"<content\b[^>]*>(.*?)</content\s*>", visible, flags=re.I | re.S)
    if not blocks or not any(str(block).strip() for block in blocks):
        raise TruncatedRoleplayOutput("模型输出正文为空")


@dataclass
class TurnFinalization:
    ctx: dict
    text: str
    trace: list
    streamed: bool
    reply: str
    deps: Any
    turn: int
    affinity: Any
    lost: bool


@dataclass
class TurnFinalizationHooks:
    writeback: Callable[[TurnFinalization, list], tuple[str, list, dict, dict]]
    apply_output: Callable[[str], str]
    anchor_offset: Callable[[str, dict], int | None]
    emit_ready: Callable[[dict, dict], bool]
    maintain: Callable[[TurnFinalization, str, list], None]


@dataclass
class TurnExecution:
    ctx: dict
    text: str
    trace: list
    streamed: bool
    deps: Any
    turn: int
    affinity: Any
    lost: bool
    # 本轮自愈进度提示原文（用于成功后从 trace 精确清除，不误伤其他 ⚠️ 告警）。
    selfheal_notices: list = field(default_factory=list)
    # 截断自愈次数上限（设置→AI 模型「截断自愈次数」，0=不自愈；默认取 SELFHEAL_MAX_ATTEMPTS）。
    selfheal_attempts: int = SELFHEAL_MAX_ATTEMPTS


@dataclass
class TurnExecutionHooks:
    generate: Callable[[], str]
    generated: Callable[[str], None]
    finalization: TurnFinalizationHooks
    # 截断自愈进度提示（可选）：推入流式通道，重试期间用户可见而非静默冻结。
    notify: Callable[[str], None] | None = None
    # 续写式自愈（可选）：入参=被截断的原始输出，返回=续写调用的原始输出；
    # 缺省（None）或不可续时退回整段重掷。
    continue_generate: Callable[[str], str] | None = None




def _strip_think(reply: str) -> str:
    """剥掉 think（闭合块 + 未闭合尾部）；截断分类与续写组装共用。"""
    visible = _THINK_CLOSED.sub("", reply or "")
    return _THINK_UNCLOSED.sub("", visible)


def _continuation_worthwhile(reply: str) -> bool:
    """正文阶段截断（content 开而未闭）且断点前有足量正文 → 值得续写。

    思考阶段截断（未闭合 think）另有预填续写（_try_think_continuation），
    不再走整段重掷——2026-08-31 晚实锤（turn 12）：think 截断重掷再截断 =
    两次全额计费零产出，付费用户不可接受。
    """
    visible = _THINK_CLOSED.sub("", reply or "")
    if _THINK_OPEN.search(visible):
        return False
    visible = _THINK_UNCLOSED.sub("", visible)
    opened = len(re.findall(r"<content\b[^>]*>", visible, flags=re.I))
    closed = len(re.findall(r"</content\s*>", visible, flags=re.I))
    if opened <= closed:
        return False
    prose = re.sub(r"</?\s*content\b[^>]*>", "", visible, flags=re.I)
    return len(prose.strip()) >= CONTINUATION_MIN_BODY_CHARS


def _selfheal_notice(turn: TurnExecution, hooks: TurnExecutionHooks, message: str,
                     push_stream: bool = True) -> None:
    """自愈进度留痕：trace 列表（非流式气泡展示 + 事后诊断）+ 可选流式通道提示。"""
    turn.trace.append(message)
    turn.selfheal_notices.append(message)
    if push_stream and hooks.notify is not None:
        try:
            hooks.notify(message)
        except Exception:  # noqa: BLE001 - 提示失败不阻断自愈
            pass


def think_truncated(reply: str) -> bool:
    """思考阶段截断：think 已开未闭、正文尚未开始（预填续写的适用判定）。

    与 ensure_complete_visible_content 的判定同序：先剥闭合块，再看是否残留
    think 开标签。供 agent_graph 续写钩子复用（按模式组装续写指令）。
    """
    visible = _THINK_CLOSED.sub("", reply or "")
    return bool(_THINK_OPEN.search(visible))


def _try_think_continuation(turn: TurnExecution, hooks: TurnExecutionHooks,
                            partial: str, reason: str) -> str:
    """思考阶段截断的预填续写（2026-08-31 晚用户定案：付费 token 不得零产出）。

    与正文续写（_try_continuation）的本质差异：残缺输出以 assistant 预填身份
    回喂，模型从断点**接着同一输出流**写——先闭合 </think> 再输出正文，所以
    拼接用原文直连（partial + 续写原文），不做 think 剥离；拼接全文过同一套
    结构校验（ensure_complete_visible_content），过不了回退整段重掷。
    费用封顶与正文路径一致：原始生成 + 1 次自愈（续写→重掷）。
    """
    _selfheal_notice(turn, hooks, f"⚠️ {reason}，正在从思考断点续写（保住已生成的思考篇幅）…")
    continue_generate = hooks.continue_generate
    if continue_generate is None:  # 防御：调用方已判过才进来
        return ""
    try:
        continuation = continue_generate(partial) or ""
    except Exception as exc:  # noqa: BLE001 - 续写失败回退整段重掷，不直接判死
        _selfheal_notice(turn, hooks, f"⚠️ 思考续写调用失败（{exc}），回退整段重掷")
        return ""
    hooks.generated(continuation)  # 每次生成（含续写）都留痕
    if len(continuation.strip()) < CONTINUATION_MIN_NEW_CHARS:
        _selfheal_notice(turn, hooks, "⚠️ 思考续写无有效新增内容（空回/懒闭合），回退整段重掷")
        return ""
    assembled = partial + continuation.lstrip()
    try:
        ensure_complete_visible_content(assembled)
    except TruncatedRoleplayOutput as exc:
        if _content_open_unclosed(assembled):
            closed = _autoclose_content(assembled)
            if closed:
                _selfheal_notice(turn, hooks, "⚠️ 思考续写只差闭合标签，已补 </content> 保存正文")
                return closed
        _selfheal_notice(turn, hooks, f"⚠️ {exc}（思考续写后仍不完整），回退整段重掷")
        return ""
    # 正文质量门：_THINK_CLOSED 非贪婪剥离会把「未闭合首段+续写闭合块」整体吞掉，
    # 结构校验对懒续写不设防（实锤：1 字正文过检）——续写产出的整段正文必须达到
    # 与正文路径同级的篇幅下限，否则宁可重掷。
    body = _strip_think(assembled).strip()
    if len(body) < CONTINUATION_MIN_BODY_CHARS:
        _selfheal_notice(
            turn, hooks,
            f"⚠️ 思考续写正文仅 {len(body)} 字（不足 {CONTINUATION_MIN_BODY_CHARS}），回退整段重掷")
        return ""
    _selfheal_notice(turn, hooks, "⚠️ 思考续写完成（思考已闭合，正文已接上，已消耗 token 未作废）")
    return assembled


def _try_continuation(turn: TurnExecution, hooks: TurnExecutionHooks,
                      partial: str, reason: str) -> str:
    """续写式自愈（2026-08-31 用户确认）：残缺输出回喂模型从断点续写，保住已生成正文。

    组装契约（用户确认的接缝处理）：
    - 第一次的 think 保持在顶部不动；续写调用自己的推演剥掉、只落 trace
      （hooks.generated 留痕原始输出），绝不插进正文中间——否则「思考过程跑到正文后」；
    - 续写正文增量直接接在断点后（lstrip 消除续写输出起头的换行），对拼接全文跑同一套
      结构校验，过不了返回空串交回整段重掷。
    续写调用异常/空回/懒闭合（新增正文 < CONTINUATION_MIN_NEW_CHARS）一律回退重掷。
    """
    _selfheal_notice(turn, hooks, f"⚠️ {reason}，正在从断点续写…")
    continue_generate = hooks.continue_generate
    if continue_generate is None:  # 防御：调用方已判过可续才进来
        return ""
    try:
        continuation = continue_generate(partial) or ""
    except Exception as exc:  # noqa: BLE001 - 续写失败回退整段重掷，不直接判死
        _selfheal_notice(turn, hooks, f"⚠️ 续写调用失败（{exc}），回退整段重掷")
        return ""
    hooks.generated(continuation)  # 每次生成（含续写）都留痕，便于事后诊断
    body = _strip_think(continuation).lstrip()
    added = re.split(r"</content\s*>", body, maxsplit=1, flags=re.I)[0]
    added = re.sub(r"<content\b[^>]*>", "", added, flags=re.I)
    if len(added.strip()) < CONTINUATION_MIN_NEW_CHARS:
        _selfheal_notice(turn, hooks, "⚠️ 续写无有效新增正文（懒闭合/空回），回退整段重掷")
        return ""
    # 2026-09-01 用户建议：句中断掉很难接，续写前把半句回退到上一断句符号。
    partial = truncate_to_sentence_boundary(partial)
    assembled = partial + body
    try:
        ensure_complete_visible_content(assembled)
    except TruncatedRoleplayOutput as exc:
        if _content_open_unclosed(assembled):
            closed = _autoclose_content(assembled)
            if closed:
                _selfheal_notice(turn, hooks, "⚠️ 续写只差闭合标签，已补 </content> 保存正文")
                return closed
        _selfheal_notice(turn, hooks, f"⚠️ {exc}（续写后仍不完整），回退整段重掷")
        return ""
    # 完成通知只落 trace 不推流式：气泡里续写正文即将无缝接上，无需多一行提示。
    _selfheal_notice(turn, hooks, f"⚠️ 续写完成（补写 {len(added.strip())} 字，断点前正文已保住）")
    return assembled


_SENTENCE_BOUNDARY_RE = re.compile(r"[。！？!?；;，,\n]")


def truncate_to_sentence_boundary(text: str, *, max_cut_back: int = 200,
                                  min_keep: int = 200) -> str:
    """把句中断掉的半句回退到上一个断句符号（。！？!?；;
）之后。

    2026-09-01 用户建议：续写时模型句中断掉很难接——回退到上一句结尾，让模型从
    干净句边界续写。防拦截 @字@(偏旁)@ 结构内不含断句符号，按符号截断天然不会
    把 @ 结构切开；截断点落在符号之后，保留前缀里的防拦截词完整。
    """
    source = text or ""
    if not source or _SENTENCE_BOUNDARY_RE.search(source[-1:]):
        return source  # 已停在断句符号后，无需回退
    window_start = max(0, len(source) - max_cut_back)
    window = source[window_start:]
    matches = list(_SENTENCE_BOUNDARY_RE.finditer(window))
    if not matches:
        return source  # 近 max_cut_back 字内没有断句符号，保持原样
    cut = window_start + matches[-1].end()
    if cut < min_keep:
        return source  # 回退后剩余正文太短，宁可保持原样
    return source[:cut]


def _content_open_unclosed(reply: str) -> bool:
    """content 标签开而未闭（think 已剥）——续写差一个闭合标签的判定。"""
    visible = _THINK_CLOSED.sub("", reply or "")
    visible = _THINK_UNCLOSED.sub("", visible)
    opened = len(re.findall(r"<content\b[^>]*>", visible, flags=re.I))
    closed = len(re.findall(r"</content\s*>", visible, flags=re.I))
    return opened > closed


def _autoclose_content(reply: str) -> str:
    """自动补 </content> 并通过校验；补不上返回空串（绝不发布没通过校验的东西）。"""
    closed = f"{reply.rstrip()}\n</content>"
    try:
        ensure_complete_visible_content(closed)
    except TruncatedRoleplayOutput:
        return ""
    return closed



def _reroll_once(turn: TurnExecution, hooks: TurnExecutionHooks, reason: str) -> str:
    """整段重掷一次：原始输出原样返回（可能仍截断），截断与否由自愈循环统一校验。

    2026-08-31 晚改版：重掷的截断输出不再原地丢弃，由循环保留为下一轮预填续写
    的素材——已付费的 token 一律不作废。
    """
    _selfheal_notice(turn, hooks, f"⚠️ {reason}，正在自动重新生成…")
    reply = hooks.generate() or "（无回复）"
    hooks.generated(reply)
    return reply


def _selfheal_loop(turn: TurnExecution, hooks: TurnExecutionHooks, reply: str,
                   exc: Exception, *, attempts: int, methods: list[str],
                   tried_continuations: set) -> dict:
    """截断自愈循环（2026-08-31 晚定案）：每轮只花一次模型调用，按残缺形态选动作——
    正文截断且断点前足量 → 正文续写；think 截断 → 预填续写（turn 12 实锤：两次全额
    重掷零产出，预填续写复用已付费思考篇幅）；否则 → 整段重掷。同一 partial 续写失败
    不再重复续写（改重掷换样本）；重掷的截断输出保留为下一轮续写素材。
    2026-08-31 晚 turn13 实锤：自愈没有时间上限，思考续写单次流式 10 分钟、整轮
    15 分钟才报错——补单次/整轮限时，并把单次重掷异常兜进下一轮。
    成功返回 finalize_turn 的结果；全部失败上抛带费用核算的 TruncatedRoleplayOutput。
    """
    final_exc: Exception = exc
    budget = max(0, min(5, int(turn.selfheal_attempts)))
    selfheal_started = time.monotonic()
    healed = False
    for _ in range(budget):
        if time.monotonic() - selfheal_started > SELFHEAL_TOTAL_BUDGET_SECONDS:
            final_exc = TruncatedRoleplayOutput("自愈总时长预算已用尽")
            break
        _set_selfheal_deadline(
            turn.ctx, time.monotonic() + SELFHEAL_CALL_TIMEOUT_SECONDS)
        try:
            if (hooks.continue_generate is not None and _continuation_worthwhile(reply)
                    and reply not in tried_continuations):
                tried_continuations.add(reply)
                candidate = _try_continuation(turn, hooks, reply, str(final_exc))
                method = "正文续写"
            elif (hooks.continue_generate is not None and think_truncated(reply)
                  and reply not in tried_continuations
                  and len(reply.strip()) >= 800):
                # 2026-09-01 实锤：短思考碎片（<800 字）预填续写接不上也闭不上，
                # 直接落 else 整段重掷，不白耗一次自愈预算。
                candidate = _try_think_continuation(turn, hooks, reply, str(final_exc))
                method = "思考续写"
            else:
                try:
                    candidate = _reroll_once(turn, hooks, str(final_exc))
                except Exception as exc3:  # noqa: BLE001 - 单次重掷失败不判死，继续下一轮
                    turn.trace.append(f"⚠️ 重掷调用失败（{exc3}），继续下一轮自愈")
                    candidate = ""
                method = "整段重掷"
            attempts += 1
            methods.append(method)
        finally:
            _set_selfheal_deadline(turn.ctx, None)
        if candidate:
            try:
                ensure_complete_visible_content(candidate)
            except TruncatedRoleplayOutput as exc2:
                final_exc = exc2
                reply = candidate  # 截断的重掷输出不丢弃，下一轮预填续写接正文
                continue
            reply = candidate
            healed = True
            break
    if not healed:
        if methods:
            detail = f"已自动自愈 {len(methods)} 次（{'、'.join(methods)}）"
        else:
            detail = "未启用自动自愈（截断自愈次数为 0）"
        raise TruncatedRoleplayOutput(
            f"{str(final_exc).rstrip('。')}；{detail}仍未恢复——本轮共 {attempts} 次模型调用，"
            f"tokens 已消耗但未能产出可用正文，本次失败不重复计费。"
            f"请重新生成（重新生成将重新计费）。") from final_exc
    # 自愈成功：进度提示已完成使命，正文完整后必须清除（2026-08-31 晚用户反馈）；
    # 按登记原文精确清除，不误伤其他模块的 ⚠️ 告警。失败路径保留提示供诊断。
    turn.trace[:] = [line for line in turn.trace if line not in turn.selfheal_notices]
    return finalize_turn(TurnFinalization(
        ctx=turn.ctx,
        text=turn.text,
        trace=turn.trace,
        streamed=turn.streamed,
        reply=reply,
        deps=turn.deps,
        turn=turn.turn,
        affinity=turn.affinity,
        lost=turn.lost,
    ), hooks.finalization)


def execute_turn(turn: TurnExecution, hooks: TurnExecutionHooks) -> dict:
    """Generate and finalize one roleplay turn through the public transaction interface."""
    try:
        reply = hooks.generate() or "（无回复）"
        hooks.generated(reply)
    except Exception as exc:  # noqa: BLE001 - 首发超时/连接失败转入自愈（重掷有预算）
        turn.trace.append(f"⚠️ 首次生成失败（{exc}），转入自愈循环")
        final_exc = TruncatedRoleplayOutput(f"首次生成失败：{exc}")
        return _selfheal_loop(
            turn, hooks, "", final_exc,
            attempts=1, methods=[], tried_continuations=set())
    try:
        ensure_complete_visible_content(reply)
    except TruncatedRoleplayOutput as exc:
        return _selfheal_loop(
            turn, hooks, reply, exc,
            attempts=1, methods=[], tried_continuations=set())
    return finalize_turn(TurnFinalization(
        ctx=turn.ctx,
        text=turn.text,
        trace=turn.trace,
        streamed=turn.streamed,
        reply=reply,
        deps=turn.deps,
        turn=turn.turn,
        affinity=turn.affinity,
        lost=turn.lost,
    ), hooks.finalization)


def finalize_turn(draft: TurnFinalization, hooks: TurnFinalizationHooks) -> dict:
    """Finalize one generated roleplay reply while preserving publish-before-maintenance."""
    reply = draft.reply
    image_recs: list = []
    illustrate_request: dict = {}
    audio_request: dict = {}
    rag_events: list = []

    if draft.deps is not None:
        reply, image_recs, illustrate_request, audio_request = hooks.writeback(draft, rag_events)

    reply = hooks.apply_output(reply)
    # 发布级安全网（2026-08-31 实锤：writeback 链跨界误剥 → replace 推出 think 残片，
    # 气泡「正文被思考过程覆盖」）：已通过结构校验的回复，其可见正文不得在任何后处理
    # 环节丢失。全部丢失即回退剥 think 原文并留痕——宁可降级显示，绝不发布空壳。
    if not _strip_think(reply).strip() and _strip_think(draft.reply).strip():
        draft.trace.append("⚠️ 后处理丢失可见正文，已回退剥 think 原文")
        reply = _strip_think(draft.reply).strip()
    result: dict = {
        "result_text": reply,
        "trace": draft.trace,
        "_streamed_result": draft.streamed,
    }
    if image_recs:
        result["image_recs"] = image_recs
    if illustrate_request:
        anchor_offset = hooks.anchor_offset(reply, illustrate_request)
        if anchor_offset is not None:
            repo_id = draft.ctx.get("repo_id") or draft.ctx.get("thread_id")
            rec = {
                "id": f"illo-req-{repo_id}-{draft.turn}",
                "prompt": illustrate_request.get("prompt", ""),
                "motion": illustrate_request.get("motion", 0),
                "actors": illustrate_request.get("actors", []),
                "scene_spec": illustrate_request.get("scene_spec", {}),
                "video_config": illustrate_request.get("video_config", {}),
                "video_request": illustrate_request.get("video_request") or {},
                "anchor_offset": anchor_offset,
                "turn_id": draft.ctx.get("turn_id", ""),
            }
            # V1.5/W1：首帧复用判定（L1 原值）随 rec 透传（有值才带）
            transition_value = illustrate_request.get("transition")
            if isinstance(transition_value, str) and transition_value:
                rec["transition"] = transition_value
            # V1.5/B1/P5/W3：视频协议字段透传（有值才带，旧前端/旧数据宽松忽略）。
            # 这些字段由 produce 层编译进 illustrate_request，若在此漏透传，
            # _ordered_illustration_events/_streamed_illustration_events 读 rec 时永远拿不到，
            # 首尾帧生图/首帧复用/转场视频在真实链路上全部静默失效。
            for _key in ("video_mode", "first_frame_desc", "last_frame_desc",
                         "prev_tail_desc", "last_frame_url"):
                _value = illustrate_request.get(_key)
                if isinstance(_value, str) and _value:
                    rec[_key] = _value
            if isinstance(illustrate_request.get("transition_video_request"), dict):
                rec["transition_video_request"] = illustrate_request["transition_video_request"]
            result["illustrate_recs"] = [rec]
    if audio_request:
        repo_id = draft.ctx.get("repo_id") or draft.ctx.get("thread_id")
        result["audio_recs"] = [{
            "id": f"audio-req-{repo_id}-{draft.turn}",
            "lines": audio_request.get("lines", []),
            "turn_id": draft.ctx.get("turn_id", ""),
        }]

    published = hooks.emit_ready(draft.ctx, result)
    if published:
        result["_eager_result"] = True

    if draft.deps is not None:
        # 正文和插画请求已先发给前端；维护属于本轮 Agent 完成边界，避免下一轮读取旧表格/纪要。
        # ComfyUI 由独立通道执行，不等待这里返回。
        hooks.maintain(draft, reply, rag_events)
    if rag_events:
        repo_id = draft.ctx.get("repo_id") or draft.ctx.get("thread_id") or "?"
        result["rag_recs"] = [
            {"id": f"rag-{repo_id}-{draft.turn}-{index}", **event}
            for index, event in enumerate(rag_events)
        ]
    return result
