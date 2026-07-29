"""对话多轮记忆：LangGraph + SqliteSaver。

thread_id = repoId（首页用 "home"），每个仓库一条独立对话线，历史自动落盘到
checkpoints.db。调用方只需传「本轮用户输入」，历史由 checkpoint 载入；system 提示词
（含 RAG 检索上下文）每轮临时注入，不写进持久化的消息列表。
"""
import sqlite3
from typing import Iterator

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, MessagesState, StateGraph

from app.config import CHECKPOINT_DB
from app.services import llm as _llm

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
    """每次请求用动态模型现编图（开销极小）；checkpointer 复用单例。"""

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
    """
    from langchain_core.messages import AIMessage

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


def append_turn(thread_id: str, user_text: str, images: list[str] | None,
                assistant_text: str, interrupted: bool = False) -> None:
    """提交一个完整 Agent turn；调用方只调用一次，Module 统一 user/assistant 顺序。"""
    if user_text or images:
        append_message(thread_id, "user", user_text or "（见图）", images or None)
    text = (assistant_text or "").strip()
    if text:
        suffix = "（已打断）" if interrupted else ""
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
                images: list[str] | None = None) -> Iterator[str]:
    """流式产出增量文本。历史按 thread_id 自动载入并续写、落盘。

    images 为图片 data URI（或可访问 URL）列表，非空时组成多模态 content 送 VLM。
    """
    graph = _build_graph(llm, system_text)
    config = {"configurable": {"thread_id": thread_id}}
    if images:
        content: list = [{"type": "text", "text": user_text}]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        human = HumanMessage(content=content)
    else:
        human = HumanMessage(content=user_text)
    for chunk, _meta in graph.stream(
        {"messages": [human]},
        config,
        stream_mode="messages",
    ):
        delta = getattr(chunk, "content", "")
        delta = _llm.flatten_content(delta)  # 某些 provider 分段返回
        if delta:
            yield delta


def get_history(thread_id: str) -> list[dict]:
    """取某仓库已落盘的对话历史（刷新后回填前端）。

    返回 [{role, content, images}]，images 为该条消息的图片 URL/dataURI 列表。
    """
    saver = _get_saver()
    config = {"configurable": {"thread_id": thread_id}}
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


def summarize_history(history: list[dict], llm, generations: list[dict] | None = None) -> dict:
    """只生成摘要，不修改 checkpoint。破坏性提交由会话维护 Module 统一负责。"""
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

    # 生成记录：图数 + 各图提示词（去重、限量，防超长）
    prompts = [g.get("prompt", "").strip() for g in gens if g.get("prompt", "").strip()]
    uniq_prompts = list(dict.fromkeys(prompts))[:20]
    gen_text = (
        f"本仓库累计生成图片 {len(gens)} 张。"
        + (("\n出现过的提示词：\n" + "\n".join(f"- {p}" for p in uniq_prompts)) if uniq_prompts else "")
    )

    user_content = f"【对话历史】\n{hist_text}\n\n【生成记录（权威）】\n{gen_text}"
    try:
        resp = llm.invoke([SystemMessage(content=_COMPACT_SYSTEM),
                           HumanMessage(content=user_content)])
        summary = _llm.flatten_content(getattr(resp, "content", "")) or ""
    except Exception as e:
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
