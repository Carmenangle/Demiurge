"""对话 checkpoint 存储：LangGraph + SqliteSaver。

2026-11-12 P6-D 后，正常 Agent 回合以 `chat_snapshot` 为唯一真源（`agent_context`
快照优先）；本模块只服务：legacy `/ai/chat` 流式端点、手动压缩/恢复、显式追加
（`/chat/append`、工作流产物）。system 提示词每轮临时注入，不写进持久化消息列表。
"""
import sqlite3
import threading
from typing import Iterator

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, MessagesState, StateGraph

from app.config import CHECKPOINT_DB
from app.services import llm as _llm
from app.services.chat_snapshot import INTERRUPT_MARKER

import logging

_LOG = logging.getLogger("uvicorn.error")

#: 每个线程只保留最新 N 个 checkpoint：最新一个就是恢复点，旧版本只是时间线。
#: 不保留会让每次整段 state 的历史快照无限堆积（P6-C 实测单线程 3.16 GB）。
CHECKPOINT_KEEP = 5
_PRUNE_LOCK = threading.Lock()

_saver: SqliteSaver | None = None


def _get_saver() -> SqliteSaver:
    """单例 SqliteSaver。check_same_thread=False 以适配 FastAPI 多线程。"""
    global _saver
    if _saver is None:
        CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
        _saver = SqliteSaver(conn)
        _saver.setup()
    return _saver


def _build_graph(llm, system_text: str):
    """每次请求用动态模型现编图（开销极小）；checkpointer 复用单例。

    @deprecated 2026-11-12 P6：legacy `/ai/chat` 已改为预算裁剪 + 直接流式调用，
    不再经由编译图。保留此函数仅为兼容可能的外部调用/测试。
    """

    def call_model(state: MessagesState):
        # system + 历史(来自 checkpoint) + 本轮输入；system 不进 state，故不落盘
        msgs = [SystemMessage(content=system_text)] + state["messages"]
        resp = llm.invoke(msgs)
        return {"messages": [resp]}

    g = StateGraph(MessagesState)
    g.add_node("model", call_model)
    g.add_edge(START, "model")
    return g.compile(checkpointer=_get_saver())


# 仅挂 checkpointer 的空图，用于直接往 thread 追加消息（不调模型）
_blank_graph = None


def _get_blank_graph():
    global _blank_graph
    if _blank_graph is None:
        g = StateGraph(MessagesState)
        g.add_node("noop", lambda s: s)
        g.add_edge(START, "noop")
        _blank_graph = g.compile(checkpointer=_get_saver())
    return _blank_graph


def append_message(thread_id: str, role: str, text: str,
                   images: list[str] | None = None) -> None:
    """把一条已生成的消息直接写进 thread 的 checkpoint（不调模型）。

    用于 /p 提示词、/s 出图等有价值产物落盘，刷新后可由 get_history 带出。
    role: user | assistant。
    2026-11-12 P6-C：写入前把 data URI 大图外置成文件 + local-view 地址，
    避免整段 checkpoint 每轮重复膨胀；落盘后只保留最新 `CHECKPOINT_KEEP` 个版本。
    """
    from langchain_core.messages import AIMessage

    from app.services import chat_checkpoint_media

    text = chat_checkpoint_media.externalize_text(text or "", thread_id)
    images = chat_checkpoint_media.externalize_images(thread_id, images)

    if images:
        content: list = []
        if text:
            content.append({"type": "text", "text": text})
        content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        msg = AIMessage(content=content) if role == "assistant" else HumanMessage(content=content)
    else:
        msg = AIMessage(content=text) if role == "assistant" else HumanMessage(content=text)
    graph = _get_blank_graph()
    config = {"configurable": {"thread_id": thread_id}}
    graph.update_state(config, {"messages": [msg]})
    try:
        prune_thread(thread_id)
    except sqlite3.Error as exc:  # 裁剪失败只影响磁盘，不阻断本轮落盘
        _LOG.warning("checkpoint 裁剪失败 thread=%s: %s", thread_id, exc)


def prune_thread(thread_id: str, keep: int | None = None) -> int:
    """删除该线程除最新 keep 个以外的 checkpoint 与 writes；返回删除条数。

    最新一个 checkpoint 已包含完整消息列表，旧版本只用于 LangGraph 时间线回溯，
    本项目没有消费方；保留少量版本是为了异常时可人工回退。
    """
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return 0
    limit = CHECKPOINT_KEEP if keep is None else max(1, int(keep))
    conn = _get_saver().conn
    tail = ("DELETE FROM {table} WHERE thread_id=? AND checkpoint_id NOT IN ("
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id=? "
            "ORDER BY rowid DESC LIMIT ?)")
    with _PRUNE_LOCK, conn:
        conn.execute(tail.format(table="writes"), (thread_id, thread_id, limit))
        cur = conn.execute(tail.format(table="checkpoints"),
                           (thread_id, thread_id, limit))
    return int(cur.rowcount or 0)


def append_turn(thread_id: str, user_text: str, images: list[str] | None,
                assistant_text: str, interrupted: bool = False) -> None:
    """提交一个完整 Agent turn；调用方只调用一次，Module 统一 user/assistant 顺序。"""
    if user_text or images:
        append_message(thread_id, "user", user_text or "（见图）", images or None)
    text = (assistant_text or "").strip()
    if text:
        suffix = INTERRUPT_MARKER if interrupted else ""
        append_message(thread_id, "assistant", text + suffix)


def mark_interrupted(thread_id: str, user_text: str, images: list[str] | None,
                     partial_text: str) -> None:
    """打断时把「本轮 user 消息 + 半成品 AI 文本」补进 checkpoint，供下一轮续写=合并。

    去重：LangGraph 在图启动时通常已把 user 消息写入 state，故先查当前历史，
    末条已是相同 user 则不重复补；半成品 AI 文本非空才补（标注为打断续写素材）。
    """
    hist = get_history(thread_id)
    last = hist[-1] if hist else None
    same_user = bool(last and last.get("role") == "user"
                     and (last.get("content") or "") == (user_text or ""))
    if not same_user:
        append_message(thread_id, "user", user_text or "（见图）", images or None)
    if (partial_text or "").strip():
        append_message(thread_id, "assistant",
                       "（上一轮被用户打断，已生成部分：）" + partial_text.strip())


def stream_chat(llm, thread_id: str, system_text: str, user_text: str,
                images: list[str] | None = None,
                max_tokens: int = 20_000) -> Iterator[str]:
    """流式产出增量文本；历史按预算裁剪后送模型，成功后整轮写回 checkpoint。

    2026-11-12 P6：旧实现挂 checkpointer 让图自动载入整条历史（无预算），长会话会
    撑爆模型窗口。现在与主链同口径，取 `agent_context.recent_history` 预算内的尾部
    （快照优先、checkpoint 回退），本轮完整回复成功后才 `append_turn` 落盘。
    """
    from app.services import agent_context

    history = agent_context.recent_history(
        thread_id, max_tokens=max_tokens, per_role=0,
    )
    from langchain_core.messages import AIMessage

    messages: list = [SystemMessage(content=system_text)]
    for item in history:
        text = str(item.get("content") or "")
        if not text:
            continue
        cls = HumanMessage if item.get("role") == "user" else AIMessage
        messages.append(cls(content=text))
    if images:
        parts: list = [{"type": "text", "text": user_text}]
        parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        human = HumanMessage(content=parts)
    else:
        human = HumanMessage(content=user_text)
    messages.append(human)

    reply: list[str] = []
    for chunk in llm.stream(messages):
        delta = _llm.flatten_content(getattr(chunk, "content", ""))
        if delta:
            reply.append(delta)
            yield delta
    append_turn(thread_id, user_text, images, "".join(reply).strip())


def get_history(thread_id: str) -> list[dict]:
    """取某仓库已落盘的对话历史（刷新后回填前端）。

    返回 [{role, content, images}]，images 为该条消息的图片 URL/dataURI 列表。
    """
    saver = _get_saver()
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    cp = saver.get(config)
    if not cp:
        return []
    msgs = cp.get("channel_values", {}).get("messages", []) or []
    out: list[dict] = []
    for m in msgs:
        role = getattr(m, "type", "")  # human / ai / system
        role = {"human": "user", "ai": "assistant"}.get(role, role)
        if role not in ("user", "assistant"):
            continue
        content = m.content
        text = ""
        images: list[str] = []
        if isinstance(content, list):  # 多模态：拆出文本块与图片块
            for p in content:
                if not isinstance(p, dict):
                    text += str(p)
                elif p.get("type") == "text":
                    text += p.get("text", "")
                elif p.get("type") == "image_url":
                    url = p.get("image_url", {})
                    images.append(url.get("url", "") if isinstance(url, dict) else str(url))
        else:
            text = content or ""
        out.append({"role": role, "content": text, "images": images})
    return out


def clear_history(thread_id: str) -> None:
    """清空某仓库的对话线；底层失败直接上浮，避免维护操作假成功。"""
    _get_saver().delete_thread(thread_id)
    from app.services import prompt_approval_store
    prompt_approval_store.clear(thread_id)


_COMPACT_SYSTEM = (
    "你是对话归档助手。把一段绘画助手的多轮对话历史压缩成一段简洁摘要，供后续对话作背景。"
    "必须按时间顺序覆盖从第一条到最后一条消息，说明目标如何变化、哪些方案被修改或否决、最终得到什么成果。"
    "摘要必须覆盖（有则写、无则跳过，别编造）：\n"
    "- 本仓库累计出了几张图；\n"
    "- 最后一次/最常用的生图提示词（原样保留关键提示词）；\n"
    "- 用得最多的工作流模板名；\n"
    "- 用到的生图/AI 模型名；\n"
    "- 若用户用过某个提示词风格模板，写明其要点；\n"
    "- 当前角色/主体的固定身份、外貌、服装、配色、构图和画风约束；\n"
    "- 用户最新确认的修改、明确要求保持不变的部分、已经否决且不能恢复的方案；\n"
    "- 当前待完成的下一步和仍未解决的问题。\n"
    "用中文分点写，信息完整但不重复，不超过 1000 字。只输出摘要正文，不要客套或解释。"
)


_COMPACT_SYSTEM_CHAT = (
    "你是角色聊天的长期记忆归档助手。把一段角色私聊/群聊历史压缩成给角色自己看的中文摘要，"
    "供后续聊天当背景。"
    "必须保留：角色与用户的关系、称呼、亲密程度；用户明确说过的重要偏好、禁忌与拒绝；"
    "角色做过的承诺、约定、未完成话题；重要事件（纪念日/婚礼/装扮/第一次/重大冲突）；"
    "当前情绪与未解决的矛盾。"
    "不要保留：临时寒暄、重复撒娇、技术或系统说明、生图提示词、图片数量、工作流或模型名、"
    "与角色关系无关的琐事。"
    "用中文分点写，信息完整但不重复，不超过 1000 字。只输出摘要正文，不要客套或解释。"
)


def summarize_history(
    history: list[dict], llm, generations: list[dict] | None = None,
    *, chat_mode: bool = False,
) -> dict:
    """只生成摘要，不修改 checkpoint。破坏性提交由会话维护 Module 统一负责。

    `chat_mode=True`：私聊/群聊用聊天专用提示词，保留关系/称呼/承诺/重要事件，
    不把生图提示词、工作流、模型名带进角色记忆。
    """
    hist = history
    gens = generations or []
    if not hist and not gens:
        return {"ok": False, "summary": "", "image_count": 0}

    # 拼历史文本（图片只记「[图片]」占位，省 token）
    lines = []
    for m in hist:
        tag = "用户" if m["role"] == "user" else "助手"
        txt = (m.get("content") or "").strip()
        if m.get("images"):
            txt = (txt + " ").strip() + f"[{len(m['images'])}张图片]"
        if txt:
            lines.append(f"{tag}：{txt}")
    hist_text = "\n".join(lines) or "（无文字对话）"

    # 生成记录：图数 + 各图提示词（去重、限量，防超长）。聊天模式不带这些技术细节。
    if chat_mode:
        user_content = f"【对话历史】\n{hist_text}"
        system = _COMPACT_SYSTEM_CHAT
    else:
        prompts = [g.get("prompt", "").strip() for g in gens if g.get("prompt", "").strip()]
        uniq_prompts = list(dict.fromkeys(prompts))[:20]
        gen_text = (
            f"本仓库累计生成图片 {len(gens)} 张。"
            + (("\n出现过的提示词：\n" + "\n".join(f"- {p}" for p in uniq_prompts))
               if uniq_prompts else "")
        )
        user_content = f"【对话历史】\n{hist_text}\n\n【生成记录（权威）】\n{gen_text}"
        system = _COMPACT_SYSTEM
    try:
        resp = llm.invoke([SystemMessage(content=system),
                           HumanMessage(content=user_content)])
        summary = _llm.flatten_content(getattr(resp, "content", "")) or ""
    except Exception as e:
        _LOG.debug("chat_memory::summarize_history 兜底异常: %s", e)
        return {"ok": False, "summary": "", "image_count": len(gens), "error": str(e)}
    if not summary.strip():
        return {"ok": False, "summary": "", "image_count": len(gens)}

    return {"ok": True, "summary": summary.strip(), "image_count": len(gens)}


def replace_history(thread_id: str, messages: list[dict]) -> None:
    """严格替换对话线为给定标准历史；用于维护提交和有限补偿。"""
    clear_history(thread_id)
    for message in messages:
        append_message(
            thread_id,
            message.get("role", "assistant"),
            message.get("content", ""),
            message.get("images") or None,
        )


def rewrite_history(thread_id: str, messages: list[dict]) -> None:
    """只重写 checkpoint 历史（P6-C 存量瘦身用），不动 prompt 审批等旁路状态。

    与 replace_history 的差异：不调用 `clear_history`（它会连带清空
    `prompt_approval_store`，而瘦身迁移不该让用户待审的审阅卡消失）。
    """
    _get_saver().delete_thread(thread_id)
    for message in messages:
        append_message(
            thread_id,
            message.get("role", "assistant"),
            message.get("content", ""),
            message.get("images") or None,
        )


def compact(thread_id: str, llm, generations: list[dict] | None = None) -> dict:
    """兼容旧调用：生成摘要后替换 checkpoint。新维护路径不调用此 Interface。"""
    result = summarize_history(get_history(thread_id), llm, generations)
    if result.get("ok"):
        replace_history(thread_id, [{
            "role": "assistant",
            "content": "【历史摘要】\n" + result["summary"],
            "images": [],
        }])
    return result


def get_saver() -> SqliteSaver:
    """对外暴露单例 checkpointer，供图像智能体复用同一记忆库。"""
    return _get_saver()
