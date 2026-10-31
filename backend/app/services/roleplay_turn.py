"""Roleplay turn finalization transaction.

Visible text and its illustration request are published before maintenance. The Agent turn only
finishes after maintenance, while ComfyUI continues independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable

from app.services.agent_contracts import CtxLike
from app.services import llm as _llm
from app.services import prompt_clean as _prompt_clean


class TruncatedRoleplayOutput(RuntimeError):
    """The provider ended a response after opening, but before closing, visible content."""


class RoleplayInterrupted(RuntimeError):
    """用户主动停止本轮生成（cancel_event 置位）。中断≠失败：

    - 不进自愈循环、不整段重掷——停止后不再花任何模型调用（2026-09-04 事故实锤：
      此前正文链不感知 cancel_event，用户点停止后 selfheal 无视中断连续整段 reroll
      从头生成，单轮 3+ 次 33KB 上下文重发白烧 token）；
    - 已流式推给前端的半成品保留在气泡（graph 层以 _interrupted 标记收尾，不覆盖正文）。
    """


def user_interrupted(ctx: Any) -> bool:
    """用户停止信号（协作式取消事件置位）：RunContext 字段 / dict 键两种 ctx 都兼容。

    流式热路径每 token 调用，不走 ctx.get（RunContext.get 会重建 _legacy dict）。
    """
    try:
        if isinstance(ctx, dict):
            event = ctx.get("cancel_event")
        else:
            event = getattr(ctx, "cancel_event", None)
        return bool(event is not None and getattr(event, "is_set", lambda: False)())
    except Exception:  # noqa: BLE001 - 取消探测尽力而为，探测失败按未中断处理
        return False


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
# 才报错——付费用户等不起）。
# ⚠ 2026-10-20 按真机重新标定：中转缓冲式下发，单次调用实测 **181.3s / 241.7s**
#    （第二次甚至超出当时的 180s 上限——截止检查只在收到 delta 时触发，模型停住时没人触发它）。
#    原值 180s/360s 对 3 轮只够 1.5 轮 ⇒ 14:08 那轮两次重掷后就报「总时长预算已用尽」。
# ⇒ 单次上限 300s（覆盖实测 241.7s＋余量）；总预算不再写死，改为
#   `_selfheal_loop` 内按**运行期轮次**× 单次上限现算（见下），设置里改次数自动跟着缩放。
SELFHEAL_CALL_TIMEOUT_SECONDS = 300.0
# 兼容旧引用的兜底值（`_complete_story` 剧情续写闸用）；`_selfheal_loop` 已改为现算。
SELFHEAL_TOTAL_BUDGET_SECONDS = SELFHEAL_MAX_ATTEMPTS * SELFHEAL_CALL_TIMEOUT_SECONDS
# 自愈全败时「按当前正文发布」的最低可见正文字数（2026-10-20 用户定案非致命发布）。
# 与 `_resume_interrupted_messages` / `_persist_interrupted_partial` 的 200 字门槛同源：
# 低于此值的残句没有发布价值，仍按判死处理（用户重点「重新生成」更划算）。
PARTIAL_PUBLISH_MIN_CHARS = 200


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
    except Exception:  # noqa: BLE001 [EXC-保留] 限时尽力而为：设不上就退回 httpx 超时
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


# 开头连续裸 COT 段的强标记（段落级，行首标题式）：2026-09-12 用户设计口径澄清——
# 思考过程**应该发布**（保留在正文前），前端「小cot折叠」正则把 <think>…</think>
# 折叠为「推演内思考过程」下拉。模型漏写 <think> 标签裸输出 COT 时，正确处理是补包
# 标签保留思考，而不是整段判死重掷（旧逻辑白烧一次调用且思考归零）。
# 只认强标记（行首标题式）：灰魂吐槽/输入分析/认知情境/关键NPC/深度求索/思辨脉络/
# 文风规划/欲望链/「思考（推演内过程）」前缀。**不用宽词**（「推演」「输出格式」正文会
# 合理引用——09-12 21:11 实锤：正文含「推演」被宽口径误杀重掷，思考+正文双丢）。
_BARE_COT_PARA_RE = re.compile(
    r"^\s*\**\s*(?:灰魂吐槽|输入分析|认知情境|关键NPC(?:分析)?|深度求索|思辨脉络|文风规划|欲望链|思考（推演内过程）)",
    re.MULTILINE,
)
# 剥壳时清掉的独立事件块：**整块含内容**剥（09-12 21:11 实锤：<状态更新> 包裹的增量
# JSON 只剥标签不剥内容时，JSON 原样漏进正文；这些块本就由解析层消费，发布文本不该有）。
# 清单属主 = `prompt_clean.BARE_STRIP_TAGS`（2026-09-15 P16 收口：prompt 承诺的
# roll/encounter/**fate** 三块必须都在这里，否则 <fate> 会漏进发布正文）。
# 注意 <status> 状态栏不在此列：它是显示层要保留的真身（见 _wrap_bare_body）。
_BARE_EVENT_BLOCK_RES = [
    re.compile(rf"<{tag}>[\s\S]*?(?:</{tag}\s*>|\Z)", re.IGNORECASE)
    for tag in _prompt_clean.BARE_STRIP_TAGS
]
# 残余单标签防御（整块剥之后的孤儿标签）
_BARE_BLOCK_TAGS = re.compile(
    rf"</?\s*(?:{_prompt_clean.tags_alternation(_prompt_clean.BARE_STRIP_TAGS)})\b[^>]*>",
    re.IGNORECASE,
)
# 裸输出里的状态栏块：先抽出、包装后放回 <content> 之前（2026-09-12 19:27 trace 实锤：
# 模型老实裸输出 <status>新栏</status>+正文时，旧逻辑把新栏当残留剥掉 → 快照回退旧值 →
# 状态卡/数据表被冻结在旧状态，且 ensure_status_snapshot 每轮前置旧栏造成双显）。
_BARE_STATUS_BLOCK_RE = re.compile(r"<status>[\s\S]*?</status>", re.IGNORECASE)


def _strip_event_blocks(text: str) -> str:
    """整块剥除事件块（含内容），再清孤儿标签。"""
    for pat in _BARE_EVENT_BLOCK_RES:
        text = pat.sub("", text)
    return _BARE_BLOCK_TAGS.sub("", text)


# <content> 正文块（保留开闭标签，只清洗块内文本）
_CONTENT_BLOCK_RE = re.compile(r"(<content\b[^>]*>)([\s\S]*?)(</content\s*>)", re.IGNORECASE)


def strip_meta_leak_in_content(reply: str) -> tuple[str, list[str]]:
    """剥掉 **<content> 正文**里被模型抄进来的「输出纪律」元话语整行。

    2026-09-13 用户实锤：deepseek-v4-flash 把尾部纪律清单抄进正文——
    首行「质量检查：满足1000-8000字；状态栏更新收养线与目的地；」、
    末行「【系统提示：以下为推演输出尾部标注，非正文内容】」，两条都会当正文显示。

    **只作用于 <content>**：<think> 里的「质量检查」是预设思维链9 的设计产物（不是泄漏），
    <status> 是前端正则渲染要用的真身——两者一律不动。清洗规则属主 prompt_clean。
    返回（清洗后全文, 被删的行）。
    """
    removed: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        cleaned, gone = _prompt_clean.strip_meta_leak(match.group(2))
        removed.extend(gone)
        return f"{match.group(1)}{cleaned}{match.group(3)}"

    if not reply:
        return reply, []
    return _CONTENT_BLOCK_RE.sub(_sub, reply), removed


def _split_leading_bare_cot(visible: str) -> tuple[str, str]:
    """从输出开头切出**连续**的裸 COT 段（强标记段落），返回（COT, 其余全部）。

    只认开头：一旦遇到首个非 COT 段即停止收集——正文中间引用「推演」等字样的段落
    不会被误收（思考混在正文中间时切不干净，由宽口径 marker 拒绝兜底）。
    """
    paras = re.split(r"\n{2,}", visible)
    cot: list[str] = []
    cut = 0
    for i, para in enumerate(paras):
        if _BARE_COT_PARA_RE.search(para):
            cot.append(para)
            cut = i + 1
        else:
            break
    if not cot:
        return "", visible
    return "\n\n".join(cot), "\n\n".join(paras[cut:])


def _wrap_bare_body(reply: str) -> str:
    """整段裸输出兜底：无任何 XML 标签的完整正文 → 自动包 <content> 通过结构校验发布。

    2026-09-04 事故实锤：deepseek-v4-flash + GrayWill「ds启用」模板（setvar 定义块字面泄漏，
    把思维链格式带成 ds 原生裸格式）输出**整段裸正文**（第一句即正文，无 <think>/<content>），
    ensure opened=0 判死 → 3 次整段重掷全裸 → 白烧 token 后整轮失败。正文完整时一次包装即
    发布，不进自愈。    适用判定（全部满足才包）：
    - 全文无 <content>/<think> 开标签（有 = 是别的截断/结构问题，不走兜底）；
    - 孤立 </think>（漏写开标签）先修复：剥闭标签、紧邻规划注释拆出补包 <think>
      （2026-09-12 实锤：claude-opus-4-6「（正文到此，继续往后续写一点：…）</think>」）；
    - 开头连续的裸 COT 段（灰魂吐槽等强标记）补包 <think> 保留（用户设计：思考发布后由
      前端「小cot折叠」正则折叠为「推演内思考过程」下拉；2026-09-12 澄清）；
    - 剥掉开头 COT 后的剩余正文若仍有**行首 COT 标题段** = 思考正文交织 → 拒绝
      （防思考发成正文；不用宽词——「推演」等正文会合理引用，09-12 21:11 实锤误杀）；
    - 剥 think/事件块（含内容）后正文 ≥ CONTINUATION_MIN_BODY_CHARS（防短残片被包）；
    - 裸 <status> 状态栏保留（取最后一块放回 <content> 之前，供快照持久化与前端正则渲染）；
    - 包装后过 ensure 结构校验。
    返回包装串；不适用返回空串。
    """
    raw = reply or ""
    if not raw.strip():
        return ""
    # 孤立 </think> 修复（2026-09-12 实锤）：模型漏写 <think> 开标签只吐闭标签时，
    # 规划注释「（正文到此，继续往后续写一点：…）」会随裸正文包装发布。先剥孤立
    # 闭标签、拆出紧邻规划注释（稍后补包 <think> 保留），再走原有判定。
    from app.services import roleplay_agency
    orphan_repair = roleplay_agency.repair_orphan_think_close(raw)
    orphan_note = ""
    if orphan_repair is not None:
        orphan_note, raw = orphan_repair
    if re.search(r"<content\b|<think\b", raw, re.IGNORECASE):
        return ""
    visible = _strip_think(raw).strip()
    if not visible:
        return ""
    # 开头连续裸 COT 段切出（补 <think> 保留）；剩余正文若仍有行首 COT 标题段则拒绝
    cot_text, rest = _split_leading_bare_cot(visible)
    if _BARE_COT_PARA_RE.search(rest):
        return ""
    # 状态栏保留：取最后一块（叙述可能先复述旧栏再给新栏，末块为最新，与
    # extract_status_snapshot 口径一致）；事件块整块含内容剥除（<状态更新> JSON 等）。
    status_blocks = _BARE_STATUS_BLOCK_RE.findall(rest)
    body = _BARE_STATUS_BLOCK_RE.sub("", rest)
    body = _strip_event_blocks(body).strip()
    if len(body) < CONTINUATION_MIN_BODY_CHARS:
        return ""
    wrapped = f"<content>\n{body}\n</content>"
    if status_blocks:
        # findall 返回含标签整块，直接放回，不再包一层（防 <status><status> 嵌套）
        wrapped = f"{status_blocks[-1].strip()}\n\n{wrapped}"
    if cot_text:
        wrapped = f"<think>\n{cot_text.strip()}\n</think>\n\n{wrapped}"
    if orphan_note:
        wrapped = f"<think>\n{orphan_note}\n</think>\n\n{wrapped}"
    try:
        ensure_complete_visible_content(wrapped)
    except TruncatedRoleplayOutput:
        return ""
    return wrapped


@dataclass
class TurnFinalization:
    ctx: CtxLike
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
    emit_ready: Callable[[CtxLike, dict], bool]
    maintain: Callable[[TurnFinalization, str, list], None]


@dataclass
class TurnExecution:
    ctx: CtxLike
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
    # 生成字数下限（智能体参数预设→剧情推进「生成字数下限」，0=关闭）。
    # 产出正文低于该字数 ⇒ `incomplete_story_reason` 判「没写完」⇒ **轮内续写**（同一条
    # 消息接着写，非多轮），直到达标或预算用尽。对齐 SillyTavern 的 auto_continue.target_length。
    # ⚠ 默认 0＝关闭：短轮次是合法产物（用户就是要一小段/时光飞逝），开下限会误判并白烧一次调用。
    story_min_chars: int = 0


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
    # 剧情续写（可选，2026-09-21）：入参=**结构已合格但剧情没写完**的续写预填串，
    # 返回=续写调用的原始输出；缺省（None）＝剧情完整度闸关闭，行为逐字节等于旧版。
    # 与 continue_generate 分开是**刻意**的：截断续写给模型的指令是「闭合 </content>」，
    # 剧情续写给的是「接着往下写」，两者语义不同，必须由接线侧显式选（不靠猜）。
    continue_story: Callable[[str], str] | None = None




def _stream_partial(exc: Exception) -> str:
    """取流式异常携带的已收增量（`llm.StreamInterrupted.partial`）；无则空串。

    2026-10-20 真机（turn f77e68b1）：首发流中途 ReadTimeout，已收的 1304 字思考跟着
    异常一起丢 ⇒ 自愈循环只能整段重掷。这里把那份残缺输出原样交给循环，让它走
    「思考断点续写 / 正文断点续写」——与「正常返回但被截断」共用同一套拼接契约。
    **不 strip**：think 预填续写靠原文直连，剥空白会破坏拼接锚点。
    """
    partial = getattr(exc, "partial", "")
    return partial if isinstance(partial, str) else ""


def visible_body_of(reply: str) -> str:
    """剥 think 与 <content> 标签，返回**纯可见正文**（落库 / 展示 / 拼接共用一份口径）。

    2026-10-20：失败落库与续写预填都要「只拿正文」，此前各模块手写正则会漂移。
    """
    visible = _strip_think(reply or "")
    visible = re.sub(r"</?\s*content\b[^>]*>", "", visible, flags=re.I)
    return visible.strip()


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
        except Exception:  # noqa: BLE001 [EXC-保留] 流式提示 best-effort：失败不阻断自愈
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


def _prepare_partial_for_publish(partial: str) -> str:
    """把自愈全败时挑出的发布候选整理成**可发布**的正文（2026-10-20 用户定案）。

    两步，顺序不能反：
    1. **裁掉末尾半句**（`truncate_to_sentence_boundary`，与重试拼接前的裁剪同款）——
       模型句中被掐会留下「他说到一半…」这种残句；原样进历史，下一轮模型会当真续写，
       插画锚点也可能落在半截动作上 ⇒ 语义误解。回退到上一个断句符号之后。
    2. **补闭合标签**——`<content>` 开而未闭时 `ensure_complete_visible_content` 必败，
       用 `_autoclose_content` 补 `</content>`（补不上返回空串 ⇒ 调用方按无正文处理）。

    ⚠ 只在**自愈全败**这条路上用；成功路径的正文是校验过的，不走这里。
    ⚠ 裁剪后再闭合：若先闭合再裁剪，截断点可能又把 `</content>` 前的正文切出新的半句。
    """
    trimmed = truncate_to_sentence_boundary(partial or "")
    if not trimmed.strip():
        return ""
    if _content_open_unclosed(trimmed):
        return _autoclose_content(trimmed)
    return trimmed


# ── 剧情完整度闸（2026-09-21 用户定案）───────────────────────────────────────────
#
# 结构校验（ensure_complete_visible_content）只判「标签闭没闭」——模型完全可以**规规矩矩
# 闭合 </content> 却只写半段剧情**（末尾停在句中，或在正文尾巴上留下写给自己看的
# 「（正文到此，继续往后续写一点：…）」注记）。用户口径：**只要认为剧情还未完善，重试就该
# 继续续写，最起码保证段落是完整的**。故这里补一条**内容层**判据，命中即再续写一轮；
# 与结构截断的关键差异是**非致命**——补不动／预算用尽就按当前正文发布，绝不作废已付费正文。
#
# 「段落完整」（用户强制底线）＝正文末尾（去尾随空白与 markdown 强调符后）落在句末标点上。
# 标点集**刻意收窄**：不含 `，,`（句中断掉）与 `：:`（后面还要接内容）；含 `；;`／`——`／`…`
# ——这几种在中文叙事里可以是自觉收束，宁可漏判不误判（漏判＝少一次续写，误判＝白烧一次调用）。
_STORY_TERMINAL_CHARS = frozenset("。．.！？!?…～~—；;”\"’'』」》〉》）)】]}")
# 尾巴修剪：空白 ＋ markdown 强调/删除线/转义符（`**他推开门。**` 结尾不该算没收束）
_STORY_TAIL_TRIM_RE = re.compile(r"[\s*_~\\]+$")
_CONTENT_CLOSE_RE = re.compile(r"</content\s*>", re.IGNORECASE)
_CONTENT_OPEN_LEAD_RE = re.compile(r"^\s*<content\b[^>]*>\s*", re.IGNORECASE)


def story_body(reply: str) -> str:
    """取回复里的**纯剧情正文**（剥 think／status／事件块，只留 <content> 内层）。

    与上下文瘦身同源：直接复用 `story_history.prose_only`，不另立一套剥离清单——
    `prompt_clean` 的 tag 登记一改，这里自动跟上。
    """
    from app.services import story_history
    return story_history.prose_only(reply or "")


def _trailing_plan_note(body: str) -> str:
    """正文末尾的自留注记（识别清单属主＝`roleplay_agency.trailing_plan_note`）。"""
    from app.services import roleplay_agency
    return roleplay_agency.trailing_plan_note(body)


def _ends_on_sentence_boundary(body: str) -> bool:
    """末尾（去尾随空白／markdown 符后）是否落在句末标点上。"""
    tail = _STORY_TAIL_TRIM_RE.sub("", body or "")
    return bool(tail) and tail[-1] in _STORY_TERMINAL_CHARS


def incomplete_story_reason(reply: str, *, min_body_chars: int = 0) -> str:
    """剧情完整度判据：空串＝「这一轮写完了」；非空＝「未完善」并给出原因。

    只判**可见剧情正文**：模型在 `<think>` 里写「本轮的收束和下一轮的钩子」是设计产物，
    不算正文没收束。两条触发，按置信度降序：
    ① 正文末尾残留模型自留的续写注记——模型自己宣告没写完，属硬信号；
    ② 段落未写完：末尾不是句末标点（用户口径的**强制底线**）。
    `min_body_chars > 0` 时追加篇幅下限判据（默认 0＝关：短轮次是合法产物，把下限默认
    打开会把「用户就是要一小段」误判成没写完，白烧一次调用）。
    """
    body = story_body(reply)
    if not body:
        return ""
    note = _trailing_plan_note(body)
    if note:
        return f"正文末尾残留模型自留的续写注记：{note}"
    if min_body_chars > 0 and len(body) < min_body_chars:
        return f"正文仅 {len(body)} 字，低于篇幅下限 {min_body_chars} 字"
    if not _ends_on_sentence_boundary(body):
        return "正文末尾段落未写完（末尾不是句末标点）"
    return ""


def open_content_for_continuation(reply: str) -> str:
    """把「结构合格但剧情没写完」的回复改写成**续写预填**：摘掉最后一个 `</content>`
    及其后的尾部块，让模型接着在同一个 `<content>` 里往下写。

    与截断续写**共用同一拼接契约**（模型补正文 ＋ 自己闭合 `</content>` ＋ 自己补尾部块），
    所以接缝处理（think 剥离、只取新增正文）照旧适用。只在 think **之外**找闭合标签——
    think 里复述协议字面量（「检查 <content> 标签」）不是真结构（2026-08-29 trace 实锤）。
    找不到闭合标签返回空串（调用方按「改不动」处理）。
    """
    from app.services import roleplay_agency
    head, rest = roleplay_agency.split_think_prefix(reply or "")
    matches = list(_CONTENT_CLOSE_RE.finditer(rest))
    if not matches:
        return ""
    return head + rest[:matches[-1].start()].rstrip("\r\n")


def _try_story_continuation(turn: TurnExecution, hooks: TurnExecutionHooks,
                            prefill: str, reason: str) -> str:
    """剧情没写完时的续写（**非致命**）：契约与截断续写一致，差异有三——

    ① 半句**不回退**：`truncate_to_sentence_boundary` 会把「没写完的尾巴」切掉，而这条
       尾巴正是要接上的东西；
    ② 模型画蛇添足重开 `<content>` 时只摘掉**开头**那个开标签（防拼出嵌套 content）；
    ③ 失败一律返回空串——调用方按「照原样发布」处理，不判死、不整段重掷（正文已合格）。
    """
    _selfheal_notice(turn, hooks, f"⚠️ {reason}，正在续写补全剧情…")
    continue_story = hooks.continue_story
    if continue_story is None:  # 防御：调用方已判定接线后才进来
        return ""
    try:
        continuation = continue_story(prefill) or ""
    except Exception as exc:  # noqa: BLE001 - 续写失败不影响已合格的正文
        _selfheal_notice(turn, hooks, f"⚠️ 剧情续写调用失败（{exc}），按已生成正文发布")
        return ""
    hooks.generated(continuation)  # 每次生成（含续写）都留痕
    body = _CONTENT_OPEN_LEAD_RE.sub("", _strip_think(continuation).lstrip(), count=1)
    if re.search(r"<content\b", body, re.I):
        # 续写里又插了一个 <content> 开标签（**开头**那个已被摘掉）⇒ 正文会留下字面标签，
        # 宁可不要这段续写。判定只作用于续写输出：预填里那个 <content> 本就在，不算重复。
        _selfheal_notice(turn, hooks, "⚠️ 剧情续写重复输出了 <content> 标签，按已生成正文发布")
        return ""
    added = re.split(r"</content\s*>", body, maxsplit=1, flags=re.I)[0]
    added = re.sub(r"<content\b[^>]*>", "", added, flags=re.I)
    if len(added.strip()) < CONTINUATION_MIN_NEW_CHARS:
        _selfheal_notice(turn, hooks, "⚠️ 剧情续写无有效新增正文（空回/懒闭合），按已生成正文发布")
        return ""
    assembled = prefill + body
    try:
        ensure_complete_visible_content(assembled)
    except TruncatedRoleplayOutput as exc:
        if _content_open_unclosed(assembled):
            closed = _autoclose_content(assembled)
            if closed:
                _selfheal_notice(turn, hooks, "⚠️ 剧情续写只差闭合标签，已补 </content>")
                return closed
        _selfheal_notice(turn, hooks, f"⚠️ {exc}（剧情续写后结构不完整），按已生成正文发布")
        return ""
    _selfheal_notice(turn, hooks, f"⚠️ 剧情续写完（补写 {len(added.strip())} 字）")
    return assembled


def _complete_story(turn: TurnExecution, hooks: TurnExecutionHooks, reply: str,
                    *, now: Callable[[], float] = time.monotonic) -> str:
    """剧情完整度闸（2026-09-21 用户定案）：结构已合格但内容判为「没写完」⇒ 续写补全。

    与截断自愈的三点差异：① 触发条件是**内容层**判据而不是标签闭合；② **非致命**——
    补不动／预算用尽一律按当前正文发布（用户已付费的正文绝不作废）；③ 只在
    `hooks.continue_story` 装配时才生效（未接线＝逐字节等于旧行为）。
    预算复用「截断自愈次数」（0＝关闭）：每轮一次模型调用，判为写完即止。
    """
    if hooks.continue_story is None:
        return reply
    budget = max(0, min(5, int(turn.selfheal_attempts)))
    reason = incomplete_story_reason(reply, min_body_chars=turn.story_min_chars) if budget > 0 else ""
    if not reason:
        return reply
    started = now()
    spent = 0
    while spent < budget:
        # 用户停止：不再花任何模型调用，已合格正文照常发布（中断≠失败）。
        if user_interrupted(turn.ctx):
            turn.trace.append("⏹ 剧情续写已中止（用户停止，按已生成正文发布）")
            break
        if now() - started > SELFHEAL_TOTAL_BUDGET_SECONDS:
            turn.trace.append("⚠️ 剧情续写总时长预算已用尽（按已生成正文发布）")
            break
        prefill = open_content_for_continuation(reply)
        if not prefill:
            break
        _set_selfheal_deadline(turn.ctx, now() + SELFHEAL_CALL_TIMEOUT_SECONDS)
        try:
            candidate = _try_story_continuation(turn, hooks, prefill, reason)
        finally:
            _set_selfheal_deadline(turn.ctx, None)
        spent += 1
        if not candidate:
            break
        reply = candidate
        reason = incomplete_story_reason(reply, min_body_chars=turn.story_min_chars)
        if not reason:
            break
    # 进度提示（正在续写…）已完成使命：与截断自愈同口径，正文完整后从 trace 清除；
    # 仍未写完时留**一条**汇总，说明为什么最终按当前正文发布（费用已花在续写上）。
    turn.trace[:] = [line for line in turn.trace if line not in turn.selfheal_notices]
    turn.selfheal_notices[:] = []
    if reason:
        turn.trace.append(
            f"⚠️ 剧情续写 {spent} 次后仍未写完整（{reason}），已按当前正文发布")
    return reply



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
                   tried_continuations: set,
                   now: Callable[[], float] = time.monotonic) -> dict:
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
    selfheal_started = now()
    healed = False
    # 总预算按**运行期轮次**× 单次上限现算：曾写死 360s，而单次实测 181.3s/241.7s
    # ⇒ 第 2 轮就被掐（2026-10-20 14:08 实锤：两次重掷后报「总时长预算已用尽」）。
    # 设置里把「截断自愈次数」调到 5，这里自动变 5×300s，不用再改常量。
    total_budget = budget * SELFHEAL_CALL_TIMEOUT_SECONDS
    # 失败时的**发布候选**：这一轮里产生过最多可见正文的那一版。
    # 与续写种子 `reply` **分道**——`reply` 每轮被候选替换（「接续之前的动作」，语义不变），
    # 但整段重掷是**从头生成**，完全可能比首轮更差：2026-10-20 实锤（turn 14:08）
    # 首轮 4867 字正文 → 重掷1 只剩 6385 字思考（visible=0）→ 重掷2 空。
    # 若跟着 `reply` 走，首轮那 4867 字会被最后那版抹掉 ⇒ 「内容都没了」。
    # 用户口径（2026-09-21 剧情完整度闸定案）：「已付费正文绝不作废，补不动按当前正文发布」。
    publish_partial = reply
    for _ in range(budget):
        # 用户停止（2026-09-04 事故实锤）：中断不是失败，不得继续自愈 reroll 烧 token。
        if user_interrupted(turn.ctx):
            raise RoleplayInterrupted("用户已停止生成") from final_exc
        if now() - selfheal_started > total_budget:
            final_exc = TruncatedRoleplayOutput("自愈总时长预算已用尽")
            break
        _set_selfheal_deadline(
            turn.ctx, now() + SELFHEAL_CALL_TIMEOUT_SECONDS)
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
                    # 2026-09-04：重掷触发 4xx 确定性错误（如 max_tokens 超限）时继续
                    # 烧自愈预算毫无意义——直接判死停止，避免重复计费。
                    if user_interrupted(turn.ctx):
                        # 用户已点停止：流异常/连接断开不再自愈，立即上抛中断
                        raise RoleplayInterrupted("用户已停止生成") from exc3
                    if _llm.is_client_rejected(exc3):
                        raise TruncatedRoleplayOutput(
                            f"自愈重掷失败：{exc3}；该错误为请求/配置类错误（4xx），"
                            f"自动重试无法恢复，已停止（不再重复计费）。") from exc3
                    turn.trace.append(f"⚠️ 重掷调用失败（{exc3}），继续下一轮自愈")
                    # 重掷断流时同样保留已收增量：下一轮拿它做续写素材（付费 token 不作废）
                    candidate = _stream_partial(exc3)
                method = "整段重掷"
            attempts += 1
            methods.append(method)
            # 候选的可见正文比现有发布候选更多 ⇒ 它才是失败时该发布的那一版。
            # ⚠ 不看总长度看**可见正文**：重掷常产出几千字纯思考（visible=0），
            # 按总长度挑会选中它、把首轮真正文挤掉（14:08 那轮正是这个坑）。
            if candidate and len(visible_body_of(candidate)) > len(visible_body_of(publish_partial)):
                publish_partial = candidate
        finally:
            _set_selfheal_deadline(turn.ctx, None)
        if candidate:
            try:
                ensure_complete_visible_content(candidate)
            except TruncatedRoleplayOutput as exc2:
                # 2026-09-04 事故修复：重掷又出整段裸正文 → 包裹发布，不重复烧预算
                wrapped = _wrap_bare_body(candidate)
                if wrapped:
                    turn.trace.append("⚠️ 重掷输出为完整裸正文（无 <content> 标签），已自动包裹发布")
                    reply = wrapped
                    healed = True
                    break
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
        failure = TruncatedRoleplayOutput(
            f"{str(final_exc).rstrip('。')}；{detail}仍未恢复——本轮共 {attempts} 次模型调用，"
            f"tokens 已消耗但未能产出可用正文，本次失败不重复计费。"
            f"请重新生成（重新生成将重新计费）。")
        # 把**失败时的发布候选**挂到异常上：自愈循环的语义是「接续之前的动作」——
        # 每轮候选替换 `reply` 往下写，所以 `reply` 是「当前续写种子」；
        # 但整段重掷是**从头生成**，`reply` 末版可能比首轮差得多（实锤：4867 字正文
        # → 6385 字纯思考 → 空）。故发布候选单独按「可见正文最多」挑，与 `reply` 分道。
        # `_persist_interrupted_partial` 靠 `getattr(exc, "partial")` 把它按中断语义落库，
        # 下一轮 `_resume_interrupted_messages` 才能接着写，而不是从头再来。
        # 2026-10-20 前这里什么都不挂 ⇒ 首轮 4867 字正文直接蒸发（用户实锤「内容都没了」）。
        # 口径同 2026-09-21 剧情完整度闸定案：**已付费正文绝不作废，补不动按当前正文发布**。
        setattr(failure, "partial", publish_partial)
        # 流式模式下 graph 不转发 trace 行（`agent_graph:2092`）⇒ 最终失败原因必须走
        # notify 通道，否则用户只看到半截正文、不知道为什么停了。
        _selfheal_notice(turn, hooks, f"⚠️ {failure}", push_stream=True)
        # 2026-10-20 用户定案（非致命）：自愈全败**不再上抛判死**，按当前已生成正文发布。
        # 用户口径：「用户的产出是要保留的」——三次调用的钱已经花了，判死等于字和图
        # 都没有（整轮失败时插画链路根本不跑）。同 2026-09-21 剧情完整度闸
        # 「补不动 ⇒ 按当前正文发布」的口径。
        # 用户同时明确三点：
        #   ① **不跳过插画**；
        #   ② 发布前裁掉末尾半句（与重试拼接前的裁剪同款），
        #      避免「话说一半」的残句进历史/插画锚点造成语义误解；
        #   ③ **状态表照常更新**——半段正文会作为下一轮上下文发出，
        #      状态表不更新会造成信息差。
        # ⚠ **有可见正文才走非致命**：`visible_body_of(publish_partial)` 为空
        #   （think-only / 全空 / 纯思考）说明这一轮**没有任何可保留的产出**，
        #   仍然上抛判死——否则等于发布空壳，也正是 2026-08-30 用户定的
        #   「正文一旦生成完成，绝不会被残缺内容顶替」要防的那件事（见
        #   `test_重试仍失败时绝不发布replace正文绝不被顶替`：零正文时 writeback/publish
        #   一次都不许跑）。
        # ⚠ 判据是**可见正文**不是原文长度：think-only 原文几千字但可见正文为 0，
        #   按原文长度判会把纯思考当正文发布（2026-10-20 探针实锤过这个坑）。
        # ⚠ 还要过**发布价值门槛**（与 `_resume_interrupted_messages` /
        #   `_persist_interrupted_partial` 的 200 字门槛同源）：太短的残句没有发布价值，
        #   发布出去用户只看到一个几乎空的气泡，不如判死让用户重点「重新生成」。
        # ⚠ **只有真跑过自愈才非致命**：`budget == 0`（设置里「截断自愈次数＝0」）
        #   时循环一次都不跑，「首发截断」按旧契约判死上抛——用户显式关掉重试，
        #   不该被非致命发布改掉语义（见 `test_接上_自愈次数为0时两段闸都关闭`）。
        if (budget <= 0
                or len(visible_body_of(publish_partial).strip()) < PARTIAL_PUBLISH_MIN_CHARS):
            raise failure from final_exc
        reply = _prepare_partial_for_publish(publish_partial)
        turn.trace.append(
            f"⚠️ {detail}仍未恢复，已按当前正文发布（末尾半句已裁剪，剧情可能不完整）")
        # 注意：**不清** selfheal_notices（成功路径才清），失败发布的诊断链要留着。
    else:
        # 自愈成功：进度提示已完成使命，正文完整后必须清除（2026-08-31 晚用户反馈）；
        # 按登记原文精确清除，不误伤其他模块的 ⚠️ 告警。
        turn.trace[:] = [line for line in turn.trace if line not in turn.selfheal_notices]
    # 正文元话语清洗（2026-09-13 用户实锤）在此补齐：自愈路径此前**直接 finalize**，
    # 与首发路径口径不一致——「输出纪律」清单会当正文发布，且会污染下面剧情闸的尾巴判据
    # （纪律行不是句末标点 ⇒ 误判「没写完」白烧一次续写）。两段闸必须看到同一份清洗后文本。
    reply, meta_leaks = strip_meta_leak_in_content(reply)
    if meta_leaks:
        turn.trace.append(
            f"⚠️ 正文元话语残留已剥离 {len(meta_leaks)} 行：{meta_leaks[0][:40]}")
    # 剧情完整度闸接续（2026-09-21 用户定案「要接上」）：截断自愈只保证**标签闭合**，
    # 续写／重掷出来的正文完全可能仍停在半段剧情（结构合格 ≠ 剧情写完）——按用户口径
    # 「只要认为剧情还未完善，重试就该继续续写」，对**已自愈成功**的正文再跑一次内容层判据。
    # ⚠ 拼接语义与截断自愈**完全一致**（用户明令）：仍在既有正文上续写
    # （`open_content_for_continuation` ＋ `_try_story_continuation`），**不回退句尾、
    # 不整段重掷**；非致命——补不动／预算用尽一律按当前正文发布，绝不作废已付费正文。
    # 预算＝`selfheal_attempts`（与截断自愈同源设置，0＝关闭）；两段各自计费上界
    # ⇒ 单轮附加调用 ≤ 2 × selfheal_attempts（截断自愈 ≤ N ＋ 剧情续写 ≤ N）。
    reply = _complete_story(turn, hooks, reply, now=now)
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


def execute_turn(turn: TurnExecution, hooks: TurnExecutionHooks,
                 *, now: Callable[[], float] = time.monotonic) -> dict:
    """Generate and finalize one roleplay turn through the public transaction interface.

    now 可注入：单测用假钟驱动自愈总时长预算判定，不必 monkeypatch time.monotonic。
    """
    try:
        reply = hooks.generate() or "（无回复）"
        hooks.generated(reply)
    except Exception as exc:  # noqa: BLE001 - 首发超时/连接失败转入自愈（重掷有预算）
        # 用户停止（2026-09-04 事故实锤）：中断≠失败——不上自愈、不整段重掷。
        # 此前取消信号只到节点边界，selfheal 无视停止连续 reroll 从头生成白烧 token。
        if user_interrupted(turn.ctx):
            raise RoleplayInterrupted("用户已停止生成") from exc
        turn.trace.append(f"⚠️ 首次生成失败（{exc}），转入自愈循环")
        # 2026-09-04 实锤：max_tokens 超限等 4xx 是确定性错误，整段重掷必然同错，
        # 只会重复计费（deepseek 604000→网关 400 白烧 4 次调用）——直接判死不重试。
        if _llm.is_client_rejected(exc):
            raise TruncatedRoleplayOutput(
                f"首次生成失败：{exc}；该错误为请求/配置类错误（4xx），"
                f"自动重试无法恢复，已停止（本轮仅 1 次模型调用，未重复计费）。") from exc
        final_exc = TruncatedRoleplayOutput(f"首次生成失败：{exc}")
        # 2026-10-20 修：异常若带着已收增量（StreamInterrupted.partial），必须交给自愈
        # 循环当续写素材——此前一律传 ""，导致「思考已写 1304 字、正文 0 字」时也每轮
        # 整段重掷（真机 turn f77e68b1：thinking 745→972→1184→1304 反复重写）。
        return _selfheal_loop(
            turn, hooks, _stream_partial(exc), final_exc,
            attempts=1, methods=[], tried_continuations=set(), now=now)
    try:
        ensure_complete_visible_content(reply)
    except TruncatedRoleplayOutput as exc:
        # 用户停止：候选虽不完整也不再进自愈（自愈=又一次从头生成，违背停止意图）。
        if user_interrupted(turn.ctx):
            raise RoleplayInterrupted("用户已停止生成") from exc
        # 2026-09-04 事故修复：模型输出整段裸正文（无 <content> 标签）时一次包装即发布，
        # 不再进自愈循环（此前 3 次整段重掷全裸白烧 token 后整轮失败）。
        wrapped = _wrap_bare_body(reply)
        if wrapped:
            turn.trace.append("⚠️ 模型输出为完整裸正文（无 <content> 标签），已自动包裹发布")
            reply = wrapped
        else:
            return _selfheal_loop(
                turn, hooks, reply, exc,
                attempts=1, methods=[], tried_continuations=set(), now=now)
    # 正文元话语清洗（2026-09-13 用户实锤）：模型把「输出纪律」清单抄进 <content>
    # ⇒ 发布/落库前剥掉；否则它会作为历史被后台维护 agent 当内容吃进去（few-shot 污染，
    # 当日实锤 table_maintenance / chronicle 的 messages[1] 已带上这段文本）。
    reply, meta_leaks = strip_meta_leak_in_content(reply)
    if meta_leaks:
        turn.trace.append(
            f"⚠️ 正文元话语残留已剥离 {len(meta_leaks)} 行：{meta_leaks[0][:40]}")
    # 剧情完整度闸（2026-09-21 用户定案）：结构合格 ≠ 剧情写完。判为「没写完」就再续写一轮
    # （复用截断续写的拼接契约），补不动/预算用尽按当前正文发布——非致命、绝不判死。
    reply = _complete_story(turn, hooks, reply, now=now)
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


def _illustrate_rec(request: dict, *, repo_id, turn: int, turn_id: str,
                    anchor_offset: int, index: int) -> dict:
    """单条出图请求 → 一条 rec（第 57 刀：主请求与多批请求**共用**本函数，口径不分叉）。

    `index == 0` ⇒ **主图**：id 逐字沿用改造前的 `illo-req-{repo_id}-{turn}`（零回归锁）。
    `index > 0` ⇒ 多批的后续张：id 加 `-{index}` 后缀 —— 前端按 slotId 去重
    （`appendMediaSlot` 里 `existing.some(part => part.slotId === slotId)`），**必须唯一**，
    否则第 2 张起会被当成重复而静默丢弃。

    每张自带 `anchor_offset`（由调用方逐条按该请求的 `anchor` 定位）⇒ 前端
    `insertSlotAtOffset` 把它们各插到自己的正文位置（多槽能力早已具备）。
    """
    suffix = "" if index == 0 else f"-{index}"
    rec = {
        "id": f"illo-req-{repo_id}-{turn}{suffix}",
        "prompt": request.get("prompt", ""),
        "motion": request.get("motion", 0),
        "actors": request.get("actors", []),
        "scene_spec": request.get("scene_spec", {}),
        "video_config": request.get("video_config", {}),
        "video_request": request.get("video_request") or {},
        "anchor_offset": anchor_offset,
        "turn_id": turn_id,
    }
    # V1.5/W1：首帧复用判定（L1 原值）随 rec 透传（有值才带）
    transition_value = request.get("transition")
    if isinstance(transition_value, str) and transition_value:
        rec["transition"] = transition_value
    # V1.5/B1/P5/W3：视频协议字段透传（有值才带，旧前端/旧数据宽松忽略）。
    # 这些字段由 produce 层编译进 illustrate_request，若在此漏透传，
    # _ordered_illustration_events/_streamed_illustration_events 读 rec 时永远拿不到，
    # 首尾帧生图/首帧复用/转场视频在真实链路上全部静默失效。
    for _key in ("video_mode", "first_frame_desc", "last_frame_desc",
                 "prev_tail_desc", "last_frame_url"):
        _value = request.get(_key)
        if isinstance(_value, str) and _value:
            rec[_key] = _value
    if isinstance(request.get("transition_video_request"), dict):
        rec["transition_video_request"] = request["transition_video_request"]
    # 第 74 刀：多批的**第几张**（index>0 才带 ⇒ 单张 rec 与改造前逐字相同，零回归锁）。
    # 前端据此判「本条是批次图」：批次图落自己的 `anchor_offset`，不再吃首尾帧的第 34 刀
    # 固定落点（否则 N 张批次图全叠在首段末／末段末）。
    if index > 0:
        rec["batch_index"] = index
    return rec


def _is_firstlast_bridge(rec: dict) -> bool:
    """段桥 = firstlast + 有转场请求 + **无正片请求**（与前端 `isSegmentBridgeEvent` 同判据）。"""
    return (rec.get("video_mode") == "firstlast"
            and isinstance(rec.get("transition_video_request"), dict)
            and bool(rec.get("transition_video_request"))
            and not rec.get("video_request"))


def _link_firstlast_bridges(recs: list[dict]) -> None:
    """第 74 刀（2026-10-31 用户口径「前面的图片放首帧、后面的图片放尾帧生成视频」）：
    给多批的段桥 rec 就地标注 `bridge_prev_slot`＝**正文序**里离它最近的前一张画面槽 id。

    为什么后端来标：「前一张」只有 rec 全集 + `anchor_offset` 才排得出来（派发序＝打分序，
    展示序＝正文序），前端各槽只看得到自己。链序与 `_wb_bridge_chain`（agency_scene_comfy）
    同源 ⇒ 「起点描述」与前端真正取到的「上一张成图」必指同一画面。前端据此把那张**成图**
    当图片1，而不是像旧口径那样按文字再生成一张「上一画面」（画面对不上＝用户说的衔接不上）。

    前一张落哪个槽：
      · 前一张是**另一张桥** ⇒ 它的主槽就是它的成图（段桥主槽出普通图）。
      · 前一张是**主图**（index 0）⇒ 首尾帧模式下主槽出的是整楼层正片**视频**，不能当图片1，
        取它的尾帧图槽 `${主槽id}:last`（本楼层收束那一格的图，剧情上正对着下一张）。
        槽不存在（正片走了首帧复用、没生成尾帧图）⇒ 前端有界等待后自行降级。
      · 桥排在主图**之前**（正文序）⇒ 本轮批次里没有更早的成图 ⇒ **不标注**，前端退到
        上一楼层的尾帧成图（与 `_wb_bridge_chain` 的起点回退 `_resolve_prev_tail_desc` 同源），
        连上楼层尾帧图都没有才退「高潮片段延续」（第 59 刀定案）——绝不拿更晚的画面倒回去当起点。
    """
    bridges = [rec for rec in recs[1:] if _is_firstlast_bridge(rec)]
    if not bridges:
        return
    primary_id = str(recs[0].get("id") or "")
    chain: list[tuple[int, bool, dict]] = [
        (int(recs[0].get("anchor_offset") or 0), True, recs[0]),
        *((int(rec.get("anchor_offset") or 0), False, rec) for rec in bridges),
    ]
    chain.sort(key=lambda item: item[0])
    prev_slot = ""
    for _offset, is_primary, rec in chain:
        if is_primary:
            prev_slot = f"{primary_id}:last"
            continue
        if prev_slot:
            rec["bridge_prev_slot"] = prev_slot
        prev_slot = str(rec.get("id") or "")


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
        # 第 57 刀 · 多批派发（2026-09-24）：主请求 + 其余锚点的请求，各自按自己的 `anchor`
        # 定位 anchor_offset ⇒ N 张图各插到自己的正文位置（前端多槽能力早已具备，零 wire 改动）。
        # 旧链路 / 批量数目 < 2 ⇒ 只有主请求 ⇒ **逐字节等于改造前**（零回归锁）。
        requests = [illustrate_request]
        _extra = illustrate_request.get("extra_requests")
        if isinstance(_extra, list):
            requests += [item for item in _extra if isinstance(item, dict)]
        repo_id = draft.ctx.get("repo_id") or draft.ctx.get("thread_id")
        turn_id = draft.ctx.get("turn_id", "")
        illustrate_recs: list[dict] = []
        for index, request in enumerate(requests):
            anchor_offset = hooks.anchor_offset(reply, request)
            if anchor_offset is None:
                continue
            illustrate_recs.append(_illustrate_rec(
                request, repo_id=repo_id, turn=draft.turn, turn_id=turn_id,
                anchor_offset=anchor_offset, index=index,
            ))
            # 第 57 刀 · 观测补口（2026-09-24 · 2 张实测暴露）：多批派发后「两张图挤在一起」
            # 无法归因 —— extra 的 anchor／anchor_offset 此前**完全不进 trace**
            # （`illustration.request` 只为主图发、`illustration.slot` 此前不存在）。
            # 有了这条才能离线复算「每个槽实际插在哪」。纯观测，不参与任何判定。
            try:
                from app.services import run_trace as _slot_trace

                _slot_trace.emit(
                    draft.ctx, "illustration.slot",
                    slot_id=illustrate_recs[-1]["id"], index=index,
                    anchor=str(request.get("anchor") or ""),
                    anchor_offset=anchor_offset,
                )
            except Exception:  # noqa: BLE001 [EXC-保留] 观测失败不得打断出图链；无日志价值（trace 端点自身不可用）
                pass
        if illustrate_recs:
            _link_firstlast_bridges(illustrate_recs)
            result["illustrate_recs"] = illustrate_recs
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
