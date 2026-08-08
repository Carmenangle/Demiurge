"""Roleplay turn finalization transaction.

Visible text is finalized and published before maintenance. This module owns that ordering;
agent_graph owns routing and prompt assembly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


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
    writeback: Callable[[TurnFinalization, list], tuple[str, list, dict]]
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
    rag_events: list = []

    if draft.deps is not None:
        reply, image_recs, illustrate_request = hooks.writeback(draft, rag_events)

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

    if hooks.emit_ready(draft.ctx, result):
        result["_eager_result"] = True

    if draft.deps is not None:
        hooks.maintain(draft, reply, rag_events)
    if rag_events:
        repo_id = draft.ctx.get("repo_id") or draft.ctx.get("thread_id") or "?"
        result["rag_recs"] = [
            {"id": f"rag-{repo_id}-{draft.turn}-{index}", **event}
            for index, event in enumerate(rag_events)
        ]
    return result
