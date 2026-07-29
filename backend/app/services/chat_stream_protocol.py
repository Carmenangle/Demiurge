"""对话流事件协议 v1：把内部领域事件编码成稳定的 SSE wire 结构。"""
from __future__ import annotations

from typing import Literal, Mapping, TypedDict


PROTOCOL = "laf-chat-stream"
VERSION = 1


class ChatStreamEvent(TypedDict):
    protocol: Literal["laf-chat-stream"]
    version: Literal[1]
    type: str
    data: dict


def _wire(event_type: str, data: dict) -> ChatStreamEvent:
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "type": event_type,
        "data": data,
    }


def error_event(message: str) -> ChatStreamEvent:
    return _wire("error", {"message": message})


def encode_event(event: Mapping[str, object]) -> ChatStreamEvent | None:
    """编码一个内部事件；完成信号由 SSE 传输层收尾，不重复进入 payload。"""
    signals: list[str] = []
    for key in ("trace", "delta", "image", "video", "insp", "approval", "route_choice", "error"):
        if key in event and event[key] is not None:
            signals.append(key)
    if event.get("interrupted") is True:
        signals.append("interrupted")
    if event.get("done") is True:
        signals.append("done")

    if signals == ["done"]:
        return None
    if len(signals) != 1:
        raise ValueError(f"对话流内部事件必须且只能包含一种事件类型：{signals or list(event)}")

    kind = signals[0]
    if kind in ("trace", "delta"):
        return _wire(kind, {"text": str(event[kind])})
    if kind in ("image", "video"):
        data = {"url": str(event[kind])}
        event_id = event.get("id") or event.get("image_id")
        if event_id:
            data["id"] = str(event_id)
        if kind == "image" and event.get("regeneration") is not None:
            data["regeneration"] = event["regeneration"]
        return _wire(kind, data)
    if kind == "insp":
        return _wire("inspiration", {"card": event["insp"]})
    if kind == "approval":
        return _wire("approval", {"approval": event["approval"]})
    if kind == "route_choice":
        return _wire("route_choice", {"choice": event["route_choice"]})
    if kind == "interrupted":
        return _wire("interrupted", {})
    return error_event(str(event["error"]))
