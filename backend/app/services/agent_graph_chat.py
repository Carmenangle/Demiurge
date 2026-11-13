"""聊天室图节点：显式 `chat` / `group_chat` 模式的唯一出口。

从 `agent_graph.py` 拆出，避免把聊天轮转塞进剧情节点；向下只依赖 `chat_room` 与
`agent_graph_state`，不反向 import agent_graph（与 supervisor/roleplay 域同构）。
"""

from __future__ import annotations

from app.services import chat_room, run_trace
from app.services.agent_contracts import CtxLike
from app.services.agent_graph_state import AgentState


def chat_node(state: AgentState) -> dict:
    ctx: CtxLike = state.get("_ctx", {})
    run_trace.emit(ctx, "agent.started", agent="chat")
    text = state.get("user_text", "") or ""
    try:
        messages = chat_room.run_turn(ctx, text)
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 聊天失败要给用户可见原因，不能静默空转
        run_trace.emit(ctx, "agent.error", agent="chat", error=str(exc))
        return {
            "route": "chat",
            "result_text": f"聊天失败：{exc}",
            "trace": state.get("trace", []) + [f"💬 聊天室失败：{exc}"],
        }
    speakers = [str(item.get("speaker") or "") for item in messages]
    trace = state.get("trace", []) + [f"💬 聊天室 → {'、'.join(speakers)}"]
    payload: dict = {"route": "chat", "trace": trace}
    # 生产路径 chat_room 已逐条走 stream_sink 即时下发；节点再回同一批会重复。
    if not callable(ctx.get("stream_sink")):
        payload["chat_messages"] = messages
    return payload
