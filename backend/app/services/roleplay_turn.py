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


def ensure_complete_visible_content(reply: str) -> None:
    opened = len(re.findall(r"<content\b[^>]*>", reply or "", flags=re.I))
    closed = len(re.findall(r"</content\s*>", reply or "", flags=re.I))
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
            result["illustrate_recs"] = [{
                "id": f"illo-req-{repo_id}-{draft.turn}",
                "prompt": illustrate_request.get("prompt", ""),
                "motion": illustrate_request.get("motion", 0),
                "actors": illustrate_request.get("actors", []),
                "scene_spec": illustrate_request.get("scene_spec", {}),
                "anchor_offset": anchor_offset,
                "turn_id": draft.ctx.get("turn_id", ""),
            }]
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
