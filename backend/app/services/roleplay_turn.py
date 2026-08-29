"""Roleplay turn finalization transaction.

Visible text and its illustration request are published before maintenance. The Agent turn only
finishes after maintenance, while ComfyUI continues independently.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable


class TruncatedRoleplayOutput(RuntimeError):
    """The provider ended a response after opening, but before closing, visible content."""


# think 剥离：闭合块 + 未闭合直达结尾的尾部（截断发生在思考阶段时正文尚未开始）。
_THINK_CLOSED = re.compile(r"<think\b.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_THINK_UNCLOSED = re.compile(r"<think\b.*\Z", re.IGNORECASE | re.DOTALL)


def ensure_complete_visible_content(reply: str) -> None:
    # 计数前剥离 think 段：模型推理常复述协议字面量（「检查 <content> 标签」），
    # 这些引用不是输出结构——2026-08-29 trace 实证 think 内 2 次字面量 <content>
    # 导致真实正文完好却被误判截断。未闭合 think 时正文尚未开始，剥离后自然放行。
    visible = _THINK_CLOSED.sub("", reply or "")
    visible = _THINK_UNCLOSED.sub("", visible)
    opened = len(re.findall(r"<content\b[^>]*>", visible, flags=re.I))
    closed = len(re.findall(r"</content\s*>", visible, flags=re.I))
    if opened > closed:
        raise TruncatedRoleplayOutput("模型输出在正文结束前被截断，请重新生成")


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


@dataclass
class TurnExecutionHooks:
    generate: Callable[[], str]
    generated: Callable[[str], None]
    finalization: TurnFinalizationHooks


def execute_turn(turn: TurnExecution, hooks: TurnExecutionHooks) -> dict:
    """Generate and finalize one roleplay turn through the public transaction interface."""
    reply = hooks.generate() or "（无回复）"
    hooks.generated(reply)
    ensure_complete_visible_content(reply)
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
