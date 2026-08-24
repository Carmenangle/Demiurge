import threading

import pytest

from app.services import roleplay_turn


def test_finalize_turn_publishes_visible_text_before_maintenance():
    order: list[str] = []
    maintained = threading.Event()
    draft = roleplay_turn.TurnFinalization(
        ctx={"repo_id": "work"}, text="继续", trace=["roleplay"], streamed=True,
        reply="raw", deps=object(), turn=3, affinity=0, lost=False,
    )

    def writeback(_draft, rag_events):
        order.append("writeback")
        rag_events.append({"state": "saved", "kind": "worldbook"})
        return "visible", [], {"prompt": "tags", "motion": 1, "actors": ["A"]}, {}

    hooks = roleplay_turn.TurnFinalizationHooks(
        writeback=writeback,
        apply_output=lambda reply: order.append("regex") or f"{reply}!",
        anchor_offset=lambda _reply, _request: 7,
        emit_ready=lambda _ctx, _result: order.append("publish") or True,
        maintain=lambda _draft, _reply, _events: (order.append("maintain"), maintained.set()),
    )

    result = roleplay_turn.finalize_turn(draft, hooks)

    assert order[:3] == ["writeback", "regex", "publish"]
    assert maintained.wait(timeout=1)
    assert order[-1] == "maintain"
    assert result["result_text"] == "visible!"
    assert result["_eager_result"] is True
    assert result["illustrate_recs"][0]["anchor_offset"] == 7
    assert result["rag_recs"][0]["kind"] == "worldbook"


def test_finalize_turn_without_agency_still_applies_output_and_publishes():
    order: list[str] = []
    draft = roleplay_turn.TurnFinalization(
        ctx={"thread_id": "home"}, text="hello", trace=[], streamed=False,
        reply="raw", deps=None, turn=1, affinity=0, lost=False,
    )
    hooks = roleplay_turn.TurnFinalizationHooks(
        writeback=lambda _draft, _events: (_ for _ in ()).throw(AssertionError()),
        apply_output=lambda reply: order.append("regex") or reply,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: order.append("publish") or False,
        maintain=lambda _draft, _reply, _events: (_ for _ in ()).throw(AssertionError()),
    )

    result = roleplay_turn.finalize_turn(draft, hooks)

    assert order == ["regex", "publish"]
    assert result == {"result_text": "raw", "trace": [], "_streamed_result": False}


def test_execute_turn_owns_generation_through_maintenance_order():
    order: list[str] = []
    maintained = threading.Event()
    turn = roleplay_turn.TurnExecution(
        ctx={"repo_id": "work"}, text="继续", trace=[], streamed=False,
        deps=object(), turn=2, affinity=0, lost=False,
    )
    finalization = roleplay_turn.TurnFinalizationHooks(
        writeback=lambda _draft, _events: order.append("writeback") or ("visible", [], {}, {}),
        apply_output=lambda reply: order.append("regex") or reply,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: order.append("publish") or True,
        maintain=lambda _draft, _reply, _events: (order.append("maintain"), maintained.set()),
    )

    result = roleplay_turn.execute_turn(turn, roleplay_turn.TurnExecutionHooks(
        generate=lambda: order.append("generate") or "raw",
        generated=lambda _reply: order.append("generated"),
        finalization=finalization,
    ))

    assert order[:5] == ["generate", "generated", "writeback", "regex", "publish"]
    assert maintained.wait(timeout=1)
    assert order[-1] == "maintain"
    assert result["result_text"] == "visible"


def test_execute_turn_rejects_unclosed_visible_content_before_writeback():
    order: list[str] = []
    turn = roleplay_turn.TurnExecution(
        ctx={"repo_id": "work"}, text="继续", trace=[], streamed=True,
        deps=object(), turn=2, affinity=0, lost=False,
    )
    finalization = roleplay_turn.TurnFinalizationHooks(
        writeback=lambda _draft, _events: order.append("writeback") or ("visible", [], {}, {}),
        apply_output=lambda reply: reply,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: order.append("publish") or True,
        maintain=lambda _draft, _reply, _events: order.append("maintain"),
    )

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):
        roleplay_turn.execute_turn(turn, roleplay_turn.TurnExecutionHooks(
            generate=lambda: "<think>分析</think><content>第一段。\n\n第二段中途",
            generated=lambda _reply: order.append("generated"),
            finalization=finalization,
        ))

    assert order == ["generated"]


def test_agent_turn_finishes_only_after_published_maintenance():
    published = threading.Event()
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    finalized = threading.Event()
    result: dict = {}

    draft = roleplay_turn.TurnFinalization(
        ctx={"repo_id": "work"}, text="继续", trace=[], streamed=True,
        reply="visible", deps=object(), turn=4, affinity=0, lost=False,
    )

    def maintain(_draft, _reply, _events):
        maintenance_started.set()
        release_maintenance.wait(timeout=2)

    hooks = roleplay_turn.TurnFinalizationHooks(
        writeback=lambda item, events: (item.reply, [], {}, {}),
        apply_output=lambda reply: reply,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: published.set() or True,
        maintain=maintain,
    )

    def finalize():
        result.update(roleplay_turn.finalize_turn(draft, hooks))
        finalized.set()

    thread = threading.Thread(target=finalize)
    thread.start()
    assert published.wait(timeout=1)
    assert maintenance_started.wait(timeout=1)
    try:
        assert not finalized.wait(timeout=0.1), "维护未完成时 Agent 不应提前释放下一轮"
    finally:
        release_maintenance.set()
        thread.join(timeout=1)
    assert finalized.is_set()
    assert result["result_text"] == "visible"
