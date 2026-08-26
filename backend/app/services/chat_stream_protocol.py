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
    for key in ("trace", "delta", "replace", "route", "image", "video", "illustrate_request", "audio_request",
                "insp", "rag_status", "approval", "route_choice", "error"):
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
    if kind in ("trace", "delta", "replace"):
        return _wire(kind, {"text": str(event[kind])})
    if kind == "route":
        return _wire("route", {"route": str(event["route"])})
    if kind in ("image", "video"):
        data = {"url": str(event[kind])}
        event_id = event.get("id") or event.get("image_id")
        if event_id:
            data["id"] = str(event_id)
        if kind == "image" and event.get("regeneration") is not None:
            data["regeneration"] = event["regeneration"]
        return _wire(kind, data)
    if kind == "illustrate_request":
        req = event["illustrate_request"]
        prompt = req.get("prompt", "") if isinstance(req, Mapping) else ""
        motion = req.get("motion", 0) if isinstance(req, Mapping) else 0
        actors = req.get("actors", []) if isinstance(req, Mapping) else []
        data: dict = {"prompt": str(prompt), "motion": int(motion) if isinstance(motion, (int, float)) else 0,
                      "actors": [str(a) for a in actors] if isinstance(actors, list) else []}
        if isinstance(req, Mapping) and isinstance(req.get("scene_spec"), Mapping):
            data["scene_spec"] = dict(req["scene_spec"])
        if isinstance(req, Mapping) and isinstance(req.get("offset"), (int, float)):
            data["offset"] = max(0, int(req["offset"]))
        if isinstance(req, Mapping) and req.get("turn_id"):
            data["turn_id"] = str(req["turn_id"])
        # V1.5/B1：透传视频协议可选字段（字符串且非空才带，旧数据/旧前端宽松忽略）
        if isinstance(req, Mapping):
            for _field in ("video_mode", "first_frame_desc", "last_frame_desc",
                           "prev_tail_desc", "last_frame_url", "video_prompt",
                           "transition"):
                _value = req.get(_field)
                if isinstance(_value, str) and _value:
                    data[_field] = _value
        # V1.5 默认开放：透传结构化视频参数（dry-run 组装结果，供测试核对参数是否上传）
        if isinstance(req, Mapping) and isinstance(req.get("video_params"), Mapping):
            data["video_params"] = dict(req["video_params"])
        if event.get("id"):
            data["id"] = str(event["id"])
        return _wire("illustrate_request", data)
    if kind == "audio_request":
        req = event["audio_request"]
        lines: list[dict] = []
        if isinstance(req, Mapping) and isinstance(req.get("lines"), list):
            for item in req["lines"]:
                if not isinstance(item, Mapping):
                    continue
                speaker = str(item.get("speaker") or "").strip()
                text = str(item.get("text") or "").strip()
                if not speaker or not text:
                    continue
                emotion = item.get("emotion")
                line: dict = {"speaker": speaker, "text": text}
                if isinstance(emotion, Mapping):
                    line["emotion"] = {
                        key: max(0.0, min(1.0, float(emotion[key])))
                        for key in ("happy", "angry", "sad", "fear", "hate", "low", "surprise", "neutral")
                        if isinstance(emotion.get(key), (int, float))
                    }
                lines.append(line)
        data: dict = {"lines": lines}
        if event.get("id"):
            data["id"] = str(event["id"])
        return _wire("audio_request", data)
    if kind == "insp":
        return _wire("inspiration", {"card": event["insp"]})
    if kind == "rag_status":
        rs = event["rag_status"]
        rs = rs if isinstance(rs, Mapping) else {}
        data = {"state": str(rs.get("state", "")), "kind": str(rs.get("kind", ""))}
        if rs.get("count") is not None:
            data["count"] = int(rs["count"]) if isinstance(rs["count"], (int, float)) else 0
        return _wire("rag_status", data)
    if kind == "approval":
        return _wire("approval", {"approval": event["approval"]})
    if kind == "route_choice":
        return _wire("route_choice", {"choice": event["route_choice"]})
    if kind == "interrupted":
        return _wire("interrupted", {})
    return error_event(str(event["error"]))
