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


def test_finalize_turn透传video_config进illustrate_recs():
    # V1.5 默认开放：video_config 白名单透传进 rec，供后端 dry-run 组装视频参数
    draft = roleplay_turn.TurnFinalization(
        ctx={"repo_id": "work", "turn_id": "t1"}, text="继续", trace=[], streamed=True,
        reply="raw", deps=object(), turn=3, affinity=0, lost=False,
    )
    vcfg = {"base_url": "", "model": "h3-mini", "size": "1280x720", "proxy": ""}

    def writeback(_draft, rag_events):
        return "visible", [], {"prompt": "tags", "motion": 2, "actors": ["A"],
                               "scene_spec": {"narrative": "动作"}, "video_config": vcfg,
                               "video_request": {"mode": "climax", "submit": {"prompt": "vp"}}}, {}

    hooks = roleplay_turn.TurnFinalizationHooks(
        writeback=writeback,
        apply_output=lambda reply: reply,
        anchor_offset=lambda _reply, _request: 7,
        emit_ready=lambda _ctx, _result: False,
        maintain=lambda _draft, _reply, _events: None,
    )
    result = roleplay_turn.finalize_turn(draft, hooks)
    rec = result["illustrate_recs"][0]
    assert rec["video_config"]["model"] == "h3-mini"
    assert rec["scene_spec"]["narrative"] == "动作"
    assert rec["video_request"]["submit"]["prompt"] == "vp"


def test_finalize_turn透传视频协议字段进illustrate_recs():
    # B1/P5/W3：video_mode/首尾帧描述/上尾帧描述/尾帧图地址/转场视频请求必须透传进 rec，
    # 否则 _ordered_illustration_events 读 rec 时拿不到，首尾帧生图/首帧复用/转场视频
    # 在真实链路上静默失效。空值字段不携带（有值才带契约）。
    draft = roleplay_turn.TurnFinalization(
        ctx={"repo_id": "work", "turn_id": "t1"}, text="继续", trace=[], streamed=True,
        reply="raw", deps=object(), turn=3, affinity=0, lost=False,
    )

    def writeback(_draft, rag_events):
        return "visible", [], {
            "prompt": "tags", "motion": 2, "actors": ["A"],
            "video_mode": "firstlast",
            "first_frame_desc": "当前首帧：暖光下一人",
            "last_frame_desc": "当前尾帧：举杯同框",
            "prev_tail_desc": "上尾帧：雨夜收伞",
            "last_frame_url": "local://prev-tail.png",
            "transition": "regenerate",
            "transition_video_request": {"mode": "transition",
                                         "submit": {"prompt": "转场分镜"}},
        }, {}

    hooks = roleplay_turn.TurnFinalizationHooks(
        writeback=writeback,
        apply_output=lambda reply: reply,
        anchor_offset=lambda _reply, _request: 7,
        emit_ready=lambda _ctx, _result: False,
        maintain=lambda _draft, _reply, _events: None,
    )
    rec = roleplay_turn.finalize_turn(draft, hooks)["illustrate_recs"][0]
    assert rec["video_mode"] == "firstlast"
    assert rec["first_frame_desc"] == "当前首帧：暖光下一人"
    assert rec["last_frame_desc"] == "当前尾帧：举杯同框"
    assert rec["prev_tail_desc"] == "上尾帧：雨夜收伞"
    assert rec["last_frame_url"] == "local://prev-tail.png"
    assert rec["transition"] == "regenerate"
    assert rec["transition_video_request"]["submit"]["prompt"] == "转场分镜"


def test_finalize_turn视频空值字段不携带进rec():
    # 有值才带：全空/缺失的视频字段不污染 rec（旧前端/旧数据宽松忽略）
    draft = roleplay_turn.TurnFinalization(
        ctx={"repo_id": "work", "turn_id": "t1"}, text="继续", trace=[], streamed=True,
        reply="raw", deps=object(), turn=3, affinity=0, lost=False,
    )

    def writeback(_draft, rag_events):
        return "visible", [], {
            "prompt": "tags", "motion": 1, "actors": [],
            "video_mode": "", "first_frame_desc": "", "last_frame_desc": "",
            "prev_tail_desc": "", "last_frame_url": "", "transition": "",
        }, {}

    hooks = roleplay_turn.TurnFinalizationHooks(
        writeback=writeback,
        apply_output=lambda reply: reply,
        anchor_offset=lambda _reply, _request: 7,
        emit_ready=lambda _ctx, _result: False,
        maintain=lambda _draft, _reply, _events: None,
    )
    rec = roleplay_turn.finalize_turn(draft, hooks)["illustrate_recs"][0]
    for _key in ("video_mode", "first_frame_desc", "last_frame_desc",
                 "prev_tail_desc", "last_frame_url", "transition",
                 "transition_video_request"):
        assert _key not in rec


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


def test_execute_turn_tolerates_protocol_literals_quoted_inside_think():
    """think 段复述协议字面量（「检查 <content> 标签」）不得计入结构判定。

    2026-08-29 trace 实证：模型思考里出现 2 次字面量 <content>，真实正文块
    <content>正文</content> 完好闭合，却被判「正文结束前被截断」。
    """
    reply = (
        "<think>先检查<content>标签中。再检查一下，我需要在正文之后输出：\n"
        "1. 状态更新（<状态更新>块）\n2. <illustration>块\n"
        "好的，开始写。<content></content>自检复述。</think>\n"
        "<content>她踏前一步，指尖挑起他的下颌。</content>\n"
        "<illustration>{\"prompt\": \"x\"}</illustration>"
    )
    roleplay_turn.ensure_complete_visible_content(reply)  # 不抛即通过


def test_execute_turn_still_rejects_truncation_after_think_with_literals():
    """剥离 think 后正文仍未闭合 → 依旧判截断（真实截断不被误放行）。"""
    reply = (
        "<think>提到<content>标签的复述</think>\n"
        "<content>正文开始，写到一半被上游掐断"
    )
    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):
        roleplay_turn.ensure_complete_visible_content(reply)


def test_execute_turn_tolerates_unclosed_think_with_literals():
    """截断发生在 think 内（think 未闭合、正文未开始）→ 不按 content 截断报错。"""
    reply = "<think>推理到一半提到<content>标签就被上游掐断"
    roleplay_turn.ensure_complete_visible_content(reply)  # 不抛即通过


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
