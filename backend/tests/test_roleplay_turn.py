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

        generate=lambda: order.append("generate") or "<content>raw</content>",

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



    # 截断自愈（2026-08-30 起）：每次生成（含被截断的首次与重掷）都留痕，自愈上限

    # 3 次耗尽才上抛（2026-08-31 晚 1 → 3）

    assert order == ["generated"] * 4





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





def test_execute_turn_rejects_unclosed_think_with_literals():

    """2026-08-30 改约：截断发生在 think 内（think 未闭合）= 思考阶段掐断，

    必须判截断走重试——旧放行逻辑会让 think-only 残缺回复覆盖流式正文。"""

    reply = "<think>推理到一半提到<content>标签就被上游掐断"

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):

        roleplay_turn.ensure_complete_visible_content(reply)





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





def test_生成截断自动重试一次再截断才失败():

    """2026-08-30 用户实锤：max_tokens 充裕仍被提供商中途掐断。重试在 generate 钩子内完成。"""

    calls = []



    def generate():

        calls.append(1)

        if len(calls) == 1:

            return "<content>\n正文只有前半句，没有闭合"  # 首次：截断

        return "<content>\n完整正文。</content>\n<status>状态</status>"



    result = roleplay_turn.execute_turn(

        roleplay_turn.TurnExecution(

            ctx={}, text="输入", trace=[], streamed=False,

            deps=None, turn=1, affinity=None, lost=False,

        ),

        roleplay_turn.TurnExecutionHooks(

            generate=generate,

            generated=lambda _reply: None,

            finalization=roleplay_turn.TurnFinalizationHooks(

                writeback=lambda item, events: (item.reply, [], {}, {}),

                apply_output=lambda value: value,

                anchor_offset=lambda reply, request: None,

                emit_ready=lambda ctx, result: False,

                maintain=lambda item, reply, events: None,

            ),

        ),

    )



    assert len(calls) == 2  # 截断后自动重试了一次

    assert "完整正文。" in result["result_text"]





def test_重试后仍截断按失败上抛():

    calls = []



    def generate():

        calls.append(1)

        return "<content>\n两次都在正文中途被掐断"



    try:

        roleplay_turn.execute_turn(

            roleplay_turn.TurnExecution(

                ctx={}, text="输入", trace=[], streamed=False,

                deps=None, turn=1, affinity=None, lost=False,

            ),

            roleplay_turn.TurnExecutionHooks(

                generate=generate,

                generated=lambda _reply: None,

                finalization=roleplay_turn.TurnFinalizationHooks(

                    writeback=lambda item, events: (item.reply, [], {}, {}),

                    apply_output=lambda value: value,

                    anchor_offset=lambda reply, request: None,

                    emit_ready=lambda ctx, result: False,

                    maintain=lambda item, reply, events: None,

                ),

            ),

        )

        raise AssertionError("应上抛 TruncatedRoleplayOutput")

    except roleplay_turn.TruncatedRoleplayOutput:

        pass

    assert len(calls) == 4  # 原始 1 次 + 自愈上限 3 次（2026-08-31 晚 1 → 3），不无限循环





def test_未闭合think判截断触发重试而非放行覆盖正文():

    """2026-08-30 实锤：think-only 残缺回复通过旧放行逻辑，replace 覆盖流式正文、

    生图锚进思考块。未闭合 think 与 think-only 必须判截断走重试。"""

    calls = []



    def generate():

        calls.append(1)

        if len(calls) == 1:

            return "<think>思考只写了一半就被提供商掐断，没有正文"  # 未闭合 think

        return "<content>完整正文。</content>"



    result = roleplay_turn.execute_turn(

        roleplay_turn.TurnExecution(

            ctx={}, text="输入", trace=[], streamed=False,

            deps=None, turn=1, affinity=None, lost=False,

        ),

        roleplay_turn.TurnExecutionHooks(

            generate=generate,

            generated=lambda _reply: None,

            finalization=roleplay_turn.TurnFinalizationHooks(

                writeback=lambda item, events: (item.reply, [], {}, {}),

                apply_output=lambda value: value,

                anchor_offset=lambda reply, request: None,

                emit_ready=lambda ctx, result: False,

                maintain=lambda item, reply, events: None,

            ),

        ),

    )



    assert len(calls) == 2  # 截断自动重试

    assert "完整正文。" in result["result_text"]





def test_think_only无正文回复判截断():

    import pytest



    def generate():

        return "<think>完整的思考但模型忘了写正文</think>"



    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):

        roleplay_turn.execute_turn(

            roleplay_turn.TurnExecution(

                ctx={}, text="输入", trace=[], streamed=False,

                deps=None, turn=1, affinity=None, lost=False,

            ),

            roleplay_turn.TurnExecutionHooks(

                generate=generate,

                generated=lambda _reply: None,

                finalization=roleplay_turn.TurnFinalizationHooks(

                    writeback=lambda item, events: (item.reply, [], {}, {}),

                    apply_output=lambda value: value,

                    anchor_offset=lambda reply, request: None,

                    emit_ready=lambda ctx, result: False,

                    maintain=lambda item, reply, events: None,

                ),

            ),

        )



def test_重试仍失败时绝不发布replace正文绝不被顶替():
    """2026-08-30 用户要求：正文一旦生成完成，绝不会被残缺内容顶替。
    两次生成都被截断 → 抛错终止，发布/落库/维护钩子一次都不许跑。"""
    import pytest

    calls = []
    order: list[str] = []

    def generate():
        calls.append(1)
        return "<think>两次都在思考阶段被提供商掐断，始终没有正文"

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):
        roleplay_turn.execute_turn(
            roleplay_turn.TurnExecution(
                ctx={}, text="输入", trace=[], streamed=True,
                deps=object(), turn=1, affinity=None, lost=False,
            ),
            roleplay_turn.TurnExecutionHooks(
                generate=generate,
                generated=lambda _reply: order.append("generated"),
                finalization=roleplay_turn.TurnFinalizationHooks(
                    writeback=lambda item, events: order.append("writeback") or (item.reply, [], {}, {}),
                    apply_output=lambda value: value,
                    anchor_offset=lambda reply, request: None,
                    emit_ready=lambda ctx, result: order.append("publish") or True,
                    maintain=lambda item, reply, events: order.append("maintain"),
                ),
            ),
        )

    assert len(calls) == 4            # 原始 1 次 + 自愈上限 3 次（2026-08-31 晚 1 → 3）
    assert order == ["generated"] * 4  # writeback/publish/maintain 零调用：无 replace 发出


def test_截断自愈重试前推送用户可见提示并留痕():

    """2026-08-31 用户反馈：重试全程静默（思考阶段不进流式通道），气泡冻结数分钟

    看起来像卡死，然后突然报错。重试前必须推送进度提示并落 trace 留痕。"""

    calls: list[str] = []

    notices: list[str] = []



    def generate():

        calls.append("generate")

        if len(calls) == 1:

            return "<content>第一次写到一半被提供商掐断"

        return "<content>完整正文。</content>"



    def generated(_reply):

        calls.append("generated")



    trace: list = []

    result = roleplay_turn.execute_turn(

        roleplay_turn.TurnExecution(

            ctx={}, text="输入", trace=trace, streamed=True,

            deps=None, turn=1, affinity=None, lost=False,

        ),

        roleplay_turn.TurnExecutionHooks(

            generate=generate,

            generated=generated,

            notify=notices.append,

            finalization=roleplay_turn.TurnFinalizationHooks(

                writeback=lambda item, events: (item.reply, [], {}, {}),

                apply_output=lambda value: value,

                anchor_offset=lambda reply, request: None,

                emit_ready=lambda ctx, result: False,

                maintain=lambda item, reply, events: None,

            ),

        ),

    )



    assert calls == ["generate", "generated", "generate", "generated"]

    assert len(notices) == 1 and notices[0].startswith("⚠️")

    assert "被截断" in notices[0] and "自动重新生成" in notices[0]

    # 2026-08-31 晚用户反馈：正文完整生成后进度提示必须清除（流式通道已被 replace

    # 覆盖；trace 里的自愈提示按登记原文精确清除，不误伤其他 ⚠️ 告警）。

    assert trace == []

    assert "完整正文。" in result["result_text"]





def test_二次截断错误信息带自动重试上下文():

    """最终报错必须说明已自动重试过：用户看到「正文生成完毕一会儿报错」时，

    能分辨这是重试后仍失败，而不是第一次就放弃。"""

    import pytest



    def generate():

        return "<content>两次都在正文中途被掐断"



    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput) as exc_info:

        roleplay_turn.execute_turn(

            roleplay_turn.TurnExecution(

                ctx={}, text="输入", trace=[], streamed=False,

                deps=None, turn=1, affinity=None, lost=False,

            ),

            roleplay_turn.TurnExecutionHooks(

                generate=generate,

                generated=lambda _reply: None,

                finalization=roleplay_turn.TurnFinalizationHooks(

                    writeback=lambda item, events: (item.reply, [], {}, {}),

                    apply_output=lambda value: value,

                    anchor_offset=lambda reply, request: None,

                    emit_ready=lambda ctx, result: False,

                    maintain=lambda item, reply, events: None,

                ),

            ),

        )

    message = str(exc_info.value)

    assert "模型输出在正文结束前被截断" in message

    assert "本轮共 4 次模型调用" in message and "tokens 已消耗" in message

    assert "已自动自愈 3 次" in message  # 2026-08-31 晚：上限 1 → 3

    assert "请重新生成" in message



def _continuation_hooks(calls, partial_body, continuation_raw, *, reroll_reply=None):

    """续写测试公共装配：首次生成返回 partial_body，之后返回 reroll_reply（整段重掷）。"""

    def generate():

        calls.append("generate")

        if calls.count("generate") == 1:

            return partial_body

        return reroll_reply



    def continue_generate(partial):

        calls.append(("continue", partial))

        if isinstance(continuation_raw, Exception):

            raise continuation_raw

        return continuation_raw



    def generated(_reply):

        calls.append("generated")



    return generate, continue_generate, generated





def _run_turn(hooks_factory):

    finalization = roleplay_turn.TurnFinalizationHooks(

        writeback=lambda item, events: (item.reply, [], {}, {}),

        apply_output=lambda value: value,

        anchor_offset=lambda reply, request: None,

        emit_ready=lambda ctx, result: False,

        maintain=lambda item, reply, events: None,

    )

    return roleplay_turn.execute_turn(

        roleplay_turn.TurnExecution(

            ctx={}, text="输入", trace=[], streamed=False,

            deps=None, turn=1, affinity=None, lost=False,

        ),

        roleplay_turn.TurnExecutionHooks(

            generate=hooks_factory.generate,

            generated=hooks_factory.generated,

            continue_generate=hooks_factory.continue_generate,

            finalization=finalization,

        ),

    )





def test_正文截断优先续写保住断点前正文():

    """2026-08-31 用户确认方案：正文阶段截断且断点前有足量正文 → 续写而非整段重掷。

    续写调用自己的推演剥掉、只落 trace，绝不插进正文中间（思考过程仍在正文前）。"""

    calls: list = []

    long_body = "她" * 320 + "，写到一半被掐断"

    partial = f"<think>决策摘要：本轮走向。</think>\\n<content>{long_body}"

    continuation = (

        "<think>续写推演：从她的表情接下去。</think>\\n"

        + "续" * 40

        + "。</content>\\n<状态更新>[时间]夜</状态更新>"

    )

    generate, continue_generate, generated = _continuation_hooks(

        calls, partial, continuation)

    result = _run_turn(_Hooks(generate, generated, continue_generate))



    assert calls == ["generate", "generated", ("continue", partial), "generated"]

    assert "续写推演" not in result["result_text"]  # 续写推演不进消息

    assert "决策摘要：本轮走向。" in result["result_text"]  # 首次思考保留在正文前

    # 2026-09-01 新规：续写前回退到最近标点（含逗号），半句「写到一半被掐断」被裁掉
    assert "她" * 320 + "，" in result["result_text"]
    assert "写到一半被掐断" not in result["result_text"]

    assert "<状态更新>" in result["result_text"]





def test_续写只差闭合标签自动补闭合保存正文():
    """2026-08-31 深夜 turn14 实锤：续写写了一大段正文却忘了闭合 </content>，
    旧逻辑把已生成正文整段扔掉去重掷——现在只差闭合标签时自动补上，正文不被删。"""
    calls: list = []
    partial = "<think>决策。</think>\\n<content>" + "她" * 320 + "，写到一半"
    continuation = "又续写了一大段但没有闭合标签仍然被掐断" * 2
    generate, continue_generate, generated = _continuation_hooks(
        calls, partial, continuation,
        reroll_reply="<think>决策。</think>\\n<content>完整正文。</content>")
    result = _run_turn(_Hooks(generate, generated, continue_generate))

    assert calls == ["generate", "generated", ("continue", partial), "generated"]
    assert "她" * 320 in result["result_text"]
    assert continuation in result["result_text"]
    assert result["result_text"].rstrip().endswith("</content>")
    assert "' + BSN + '" not in result["result_text"]  # 补丁占位符不得泄漏进正文
    assert "完整正文。" not in result["result_text"]
def test_续写调用抛异常回退整段重掷不判死():

    calls: list = []

    partial = "<think>决策。</think>\\n<content>" + "她" * 320 + "，写到一半"

    generate, continue_generate, generated = _continuation_hooks(

        calls, partial, RuntimeError("Connection error"),

        reroll_reply="<content>完整正文。</content>")

    result = _run_turn(_Hooks(generate, generated, continue_generate))



    # 续写调用抛异常 → 没有对应 generated 留痕，直接回退重掷

    assert calls == ["generate", "generated", ("continue", partial),

                     "generate", "generated"]

    assert "完整正文。" in result["result_text"]





def test_懒闭合无新增正文回退重掷():

    """续写直接闭合标签（偷懒不加正文）→ 无进展，回退重掷，防止断点半句被存进历史。"""

    calls: list = []

    partial = "<think>决策。</think>\\n<content>" + "她" * 320 + "，写到一半"

    generate, continue_generate, generated = _continuation_hooks(

        calls, partial, "</content>\\n<状态更新>[时间]夜</状态更新>",

        reroll_reply="<content>完整正文。</content>")

    result = _run_turn(_Hooks(generate, generated, continue_generate))



    assert calls == ["generate", "generated", ("continue", partial), "generated",

                     "generate", "generated"]

    assert "完整正文。" in result["result_text"]





def test_正文过短不走续写直接重掷():

    """断点前正文过短（<300 字）→ 续写不划算，直接整段重掷。"""

    calls: list = []

    generate, continue_generate, generated = _continuation_hooks(

        calls, "<content>短正文", "<content>不该被调用</content>",

        reroll_reply="<content>完整正文。</content>")

    result = _run_turn(_Hooks(generate, generated, continue_generate))

    assert calls == ["generate", "generated", "generate", "generated"]

    assert "完整正文。" in result["result_text"]





def test_思考阶段截断走预填续写保住已付费思考():
    """2026-08-31 晚实锤（turn 12：两次全额重掷零产出、tokens 全废）——think 截断
    改预填续写：残缺输出原样回馈、原文直连拼接，已付费思考不作废，不再整段重掷。
    2026-09-01 补：只有成型思考（≥800 字）才预填续写，短碎片直接重掷。"""
    calls: list = []
    partial = "<think>" + "长思考" * 300  # 900 字成型思考
    continuation = (
        "写母亲的逼近。</think>\n<content>"
        + "正文" * 160
        + "。</content>")
    generate, continue_generate, generated = _continuation_hooks(
        calls, partial, continuation)
    result = _run_turn(_Hooks(generate, generated, continue_generate))

    assert calls == ["generate", "generated", ("continue", partial), "generated"]
    assert partial in result["result_text"]  # 已付费思考保留
    assert "写母亲的逼近。" in result["result_text"]  # 续写从断点接上并闭合
    assert "正文" * 160 in result["result_text"]  # 正文产出（≥300 字质量门）


def test_思考碎片过短直接整段重掷():
    """2026-09-01 实锤：模型连续输出 100-400 字思考碎片就停，预填续写这些碎片接不上
    也闭不上。短碎片（<800 字）跳过预填续写，直接整段重掷。"""
    calls: list = []
    partial = "<think>思考只写了一半"
    generate, continue_generate, generated = _continuation_hooks(
        calls, partial, "不该被调用的续写",
        reroll_reply="<content>重掷完整正文。</content>")
    result = _run_turn(_Hooks(generate, generated, continue_generate))

    assert calls == ["generate", "generated", "generate", "generated"]
    assert "重掷完整正文。" in result["result_text"]


def test_自愈次数预算从设置透传_预算1次耗尽即报错():
    """设置→AI 模型「截断自愈次数」透传为 selfheal_attempts：预算 1 → 原始 1 次
    + 自愈 1 次 = 共 2 次模型调用，耗尽即报错。2026-09-01：短思考碎片直接重掷。"""
    import pytest
    calls: list = []
    generate, continue_generate, generated = _continuation_hooks(
        calls, "<think>思考只写了一半", "不该被调用",
        reroll_reply="<think>重掷仍截断")
    turn = roleplay_turn.TurnExecution(
        ctx={}, text="输入", trace=[], streamed=False,
        deps=None, turn=1, affinity=None, lost=False, selfheal_attempts=1)
    finalization = roleplay_turn.TurnFinalizationHooks(
        writeback=lambda item, events: (item.reply, [], {}, {}),
        apply_output=lambda value: value,
        anchor_offset=lambda reply, request: None,
        emit_ready=lambda ctx, result: False,
        maintain=lambda item, reply, events: None,
    )
    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput) as excinfo:
        roleplay_turn.execute_turn(turn, roleplay_turn.TurnExecutionHooks(
            generate=generate, generated=generated,
            continue_generate=continue_generate, finalization=finalization))
    assert "本轮共 2 次模型调用" in str(excinfo.value)  # 原始 1 次 + 预算内重掷 1 次
    assert calls == ["generate", "generated", "generate", "generated"]


def test_自愈次数为0时不自愈直接报错():

    """selfheal_attempts=0（设置选「不自愈」）→ 截断直接上抛，只花 1 次调用。"""

    import pytest

    calls: list = []

    generate, continue_generate, generated = _continuation_hooks(

        calls, "<think>思考只写了一半", "不该被调用",

        reroll_reply="<content>不该重掷</content>")

    turn = roleplay_turn.TurnExecution(

        ctx={}, text="输入", trace=[], streamed=False,

        deps=None, turn=1, affinity=None, lost=False, selfheal_attempts=0)

    finalization = roleplay_turn.TurnFinalizationHooks(

        writeback=lambda item, events: (item.reply, [], {}, {}),

        apply_output=lambda value: value,

        anchor_offset=lambda reply, request: None,

        emit_ready=lambda ctx, result: False,

        maintain=lambda item, reply, events: None,

    )

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput) as excinfo:

        roleplay_turn.execute_turn(turn, roleplay_turn.TurnExecutionHooks(

            generate=generate, generated=generated,

            continue_generate=continue_generate, finalization=finalization))

    message = str(excinfo.value)

    assert "未启用自动自愈" in message and "本轮共 1 次模型调用" in message

    assert calls == ["generate", "generated"]  # 零续写零重掷





def test_首次生成异常转入自愈重掷成功():
    """2026-08-31 深夜实锤：首发流式卡 4.5 分钟不结束。首发超时/连接失败不再直接
    判死，转入自愈循环整段重掷——付费调用换来的是重试机会而不是一条 error。"""
    calls: list = []

    def generate():
        calls.append("generate")
        if calls.count("generate") == 1:
            raise TimeoutError("生成总时长超时，中止本次生成")
        return "<content>完整正文。</content>"

    def generated(_reply):
        calls.append("generated")

    result = _run_turn(_Hooks(generate, generated, lambda partial: "不该被调用"))

    assert result["result_text"] == "<content>完整正文。</content>"
    assert calls == ["generate", "generate", "generated"]  # 首发失败 → 重掷成功


def test_自愈总时长预算用尽即报错(monkeypatch):
    """2026-08-31 晚 turn13 实锤：整轮自愈 15 分钟才报错——总时长预算到点立即停止。"""
    import pytest
    clock = [0.0, roleplay_turn.SELFHEAL_TOTAL_BUDGET_SECONDS + 9999.0]
    monkeypatch.setattr(
        roleplay_turn.time, "monotonic",
        lambda: clock.pop(0) if len(clock) > 1 else clock[0])
    calls: list = []
    generate, continue_generate, generated = _continuation_hooks(
        calls, "<think>思考只写了一半", "不该被调用",
        reroll_reply="<content>不该重掷</content>")
    turn = roleplay_turn.TurnExecution(
        ctx={}, text="输入", trace=[], streamed=False,
        deps=None, turn=1, affinity=None, lost=False)
    finalization = roleplay_turn.TurnFinalizationHooks(
        writeback=lambda item, events: (item.reply, [], {}, {}),
        apply_output=lambda value: value,
        anchor_offset=lambda reply, request: None,
        emit_ready=lambda ctx, result: False,
        maintain=lambda item, reply, events: None,
    )
    # 时钟序列：第 1 次读取（selfheal_started）=0；第 2 次读取（预算检查）已超总预算
    # → 立刻 break，不再花任何自愈调用
    def monkey_generate():
        calls.append("generate")
        return "<think>思考只写了一半"

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput) as excinfo:
        roleplay_turn.execute_turn(turn, roleplay_turn.TurnExecutionHooks(
            generate=monkey_generate, generated=generated,
            continue_generate=continue_generate, finalization=finalization))
    message = str(excinfo.value)
    assert "自愈总时长预算已用尽" in message
    assert "本轮共 1 次模型调用" in message  # 原始生成后直接到点，零自愈


def test_自愈deadline透传兼容dict与RunContext形态():
    ctx_dict: dict = {}
    roleplay_turn._set_selfheal_deadline(ctx_dict, 123.0)
    assert ctx_dict.get("_selfheal_deadline") == 123.0
    roleplay_turn._set_selfheal_deadline(ctx_dict, None)
    assert "_selfheal_deadline" not in ctx_dict

    class CtxLike:
        def __init__(self):
            self.extras = {}

    ctx_like = CtxLike()
    roleplay_turn._set_selfheal_deadline(ctx_like, 456.0)
    assert ctx_like.extras.get("_selfheal_deadline") == 456.0
    roleplay_turn._set_selfheal_deadline(ctx_like, None)
    assert "_selfheal_deadline" not in ctx_like.extras


def test_重掷截断输出下一轮预填续写不作废():
    """2026-08-31 晚定案：重掷仍截断时，重掷产出保留为下一轮预填续写素材——
    已付费的 token 一律不作废，而不是再花一次全新生成。"""
    calls: list = []
    reroll_partial = "<think>" + "重" * 900  # 成型思考，值得预填续写

    def generate():
        calls.append("generate")
        if len([c for c in calls if c == "generate"]) == 1:
            return "<content>短"  # 正文过短：不可续 → 整段重掷
        return reroll_partial  # 重掷仍卡在思考阶段

    def generated(_reply):
        calls.append("generated")

    def continue_generate(partial):
        calls.append(("continue", partial))
        return "？然后她开口。</think>\n<content>" + "正文" * 160 + "。</content>"

    result = _run_turn(_Hooks(generate, generated, continue_generate))

    assert calls == ["generate", "generated", "generate", "generated",
                     ("continue", reroll_partial), "generated"]
    assert reroll_partial in result["result_text"]  # 重掷思考保留
    assert "正文" * 160 in result["result_text"]  # 正文接上


def test_自愈全部失败报错带费用核算():
    """付费零产出的透明度底线：全部自愈失败时，报错必须交代调用次数与 token 消耗。"""
    import pytest
    calls: list = []
    generate, continue_generate, generated = _continuation_hooks(
        calls, "<think>思考只写了一半", "不该被调用",
        reroll_reply="<think>又只写了一半")  # 重掷再次思考截断（短碎片）
    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput) as excinfo:
        _run_turn(_Hooks(generate, generated, continue_generate))
    message = str(excinfo.value)
    assert "本轮共 4 次模型调用" in message  # 原始 1 次 + 自愈上限 3 次
    assert "已自动自愈 3 次（整段重掷、整段重掷、整段重掷）" in message
    assert "tokens 已消耗" in message
    assert calls == ["generate", "generated"] * 4


class _Hooks:
    """轻量命名空间：把三个钩子凑成 _run_turn 需要的形状。"""

    def __init__(self, generate, generated, continue_generate):
        self.generate = generate
        self.generated = generated
        self.continue_generate = continue_generate



def test_正文截断优先续写保住断点前正文_留痕():

    """自愈进度落 trace：续写提示与完成留痕（非流式气泡靠它展示）。"""

    calls: list = []
    notices: list = []
    partial = "<think>决策。</think>\\n<content>" + "她" * 320 + "，写到一半"

    continuation = "续" * 40 + "。</content>"

    generate, continue_generate, generated = _continuation_hooks(

        calls, partial, continuation)

    trace: list = []

    finalization = roleplay_turn.TurnFinalizationHooks(

        writeback=lambda item, events: (item.reply, [], {}, {}),

        apply_output=lambda value: value,

        anchor_offset=lambda reply, request: None,

        emit_ready=lambda ctx, result: False,

        maintain=lambda item, reply, events: None,

    )

    roleplay_turn.execute_turn(

        roleplay_turn.TurnExecution(

            ctx={}, text="输入", trace=trace, streamed=False,

            deps=None, turn=1, affinity=None, lost=False,

        ),

        roleplay_turn.TurnExecutionHooks(

            generate=generate, generated=generated,
            notify=notices.append,
            continue_generate=continue_generate,

            finalization=finalization,

        ),

    )

    assert any("正在从断点续写" in line for line in notices)
    assert any("续写完成" in line for line in notices)
    assert not [line for line in trace if "续写" in line]  # 成功后提示已从 trace 清除

def test_后处理丢失正文时回退剥think原文绝不发布空壳():
    """2026-08-31 实锤：writeback 链跨界误剥 → replace 推出 think 残片，气泡
    「正文被思考过程覆盖」。发布级安全网：已通过结构校验的回复，其可见正文在任何
    后处理环节全部丢失 → 回退剥 think 原文并留痕，绝不发布空壳。"""
    raw = "<think>决策摘要。</think>\n<content>正文核心段落，必须保留。</content>"
    trace: list = []

    def writeback(_draft, _events):
        # 模拟提取链误剥：只剩 think 残片（未闭合），正文全丢
        return "<think>残片：格式确认 - <content> 正文", [], {}, {}

    result = roleplay_turn.finalize_turn(
        roleplay_turn.TurnFinalization(
            ctx={}, text="输入", trace=trace, streamed=True,
            reply=raw, deps=object(), turn=1, affinity=None, lost=False,
        ),
        roleplay_turn.TurnFinalizationHooks(
            writeback=writeback,
            apply_output=lambda value: value,
            anchor_offset=lambda reply, request: None,
            emit_ready=lambda ctx, result: False,
            maintain=lambda item, reply, events: None,
        ),
    )

    assert "正文核心段落，必须保留。" in result["result_text"]
    assert "回退剥 think 原文" in "".join(trace)


def test_后处理正常时不触发回退():
    """安全网只在可见正文全部丢失时触发；正常后处理（think 折叠保留、正文在）不干预。"""
    raw = "<think>决策摘要。</think>\n<content>正文核心段落。</content>"
    trace: list = []
    result = roleplay_turn.finalize_turn(
        roleplay_turn.TurnFinalization(
            ctx={}, text="输入", trace=trace, streamed=True,
            reply=raw, deps=object(), turn=1, affinity=None, lost=False,
        ),
        roleplay_turn.TurnFinalizationHooks(
            writeback=lambda _draft, _events: (raw, [], {}, {}),
            apply_output=lambda value: value,
            anchor_offset=lambda reply, request: None,
            emit_ready=lambda ctx, result: False,
            maintain=lambda item, reply, events: None,
        ),
    )

    assert result["result_text"] == raw
    assert trace == []


def test_续写拼接_句中断句回退到上一断句符号():
    """2026-09-01 用户建议：句中断掉很难接——回退到上一句结尾再续写。"""
    partial = (
        "<think>决策。</think>\n<content>"
        + "前" * 250
        + "她走到门前，轻轻推开。门后是长廊，她刚迈出一步，裙摆"
    )
    truncated = roleplay_turn.truncate_to_sentence_boundary(partial)
    # 回退到最近逗号：逗号前内容全部保留
    assert truncated.endswith("她刚迈出一步，")
    assert "门后是长廊" in truncated
    # 模拟续写拼接
    continuation = "被风吹起，她继续向前走去。</content>"
    assembled = truncated + continuation
    roleplay_turn.ensure_complete_visible_content(assembled)  # 拼接后结构完整
    assert "门后是长廊" in assembled  # 逗号前内容保留


def test_续写拼接_防拦截词结构不被切开():
    """防拦截 @字@(偏旁)@ 结构内不含断句符号，按符号截断天然不会切开它。"""
    partial = (
        "<think>决策。</think>\n<content>"
        + "前" * 250
        + "她伸手握住@阳@(具)@，掌心收紧。他闷哼一声，腰腹"
    )
    truncated = roleplay_turn.truncate_to_sentence_boundary(partial)
    # 回退到「掌心收紧。」之后；@阳@(具)@ 完整保留在截断点之前
    assert truncated.endswith("他闷哼一声，")  # 回退到最近的逗号
    assert "@阳@(具)@" in truncated
    assert "腰腹" not in truncated  # 只有逗号后的半句被裁掉


def test_首次生成4xx确定性错误判死不重掷():
    """2026-09-04 实锤：max_tokens 超限等 4xx 是确定性错误——直接判死不整段重掷，
    不再白烧自愈预算（deepseek 604000→网关 400 曾白烧 4 次调用）。"""
    calls: list = []

    def generate():
        calls.append("generate")
        raise RuntimeError("调用对话模型失败：Error code: 400 - "
                           "{'code': 'LITELLM_ERROR', 'message': "
                           "'max_tokens should be less or equal to 393216'}")

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput) as excinfo:
        _run_turn(_Hooks(generate, lambda _reply: None, lambda partial: None))
    message = str(excinfo.value)
    assert "首次生成失败" in message
    assert "请求/配置类错误（4xx）" in message
    assert "本轮仅 1 次模型调用" in message
    assert calls == ["generate"]  # 无任何自愈重掷


def test_自愈重掷触发4xx确定性错误立即判死():
    """首发截断入自愈后，重掷若遇 4xx 也应立即停止，不把剩余自愈预算烧完。"""
    calls: list = []

    def generate():
        calls.append("generate")
        if len([c for c in calls if c == "generate"]) == 1:
            return "<content>正文截断到一半"  # 首发截断（过短不可续）→ 入自愈整段重掷
        raise RuntimeError("调用对话模型失败：Error code: 400 - bad request")

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput) as excinfo:
        _run_turn(_Hooks(generate, lambda _reply: None, lambda partial: None))
    message = str(excinfo.value)
    assert "自愈重掷失败" in message
    assert "请求/配置类错误（4xx）" in message
    assert calls == ["generate", "generate"]  # 首发 + 1 次重掷即停，预算未烧完


def _bare_turn_executor(generate, attempts=0):
    """execute_turn 裸正文兜底测试脚手架：selfheal_attempts 可控。"""
    return roleplay_turn.execute_turn(
        roleplay_turn.TurnExecution(
            ctx={}, text="输入", trace=[], streamed=False,
            deps=None, turn=1, affinity=None, lost=False,
            selfheal_attempts=attempts,
        ),
        roleplay_turn.TurnExecutionHooks(
            generate=generate,
            generated=lambda _reply: None,
            finalization=roleplay_turn.TurnFinalizationHooks(
                writeback=lambda item, events: (item.reply, [], {}, {}),
                apply_output=lambda value: value,
                anchor_offset=lambda reply, request: None,
                emit_ready=lambda ctx, result: False,
                maintain=lambda item, reply, events: None,
            ),
        ),
    )
def test_整段裸正文首发一次调用即包裹发布():
    """2026-09-04 事故修复：deepseek 通道输出无 <content> 标签的整段裸正文 → 自动包裹发布，
    不再 3 次无谓整段重掷白烧 token。"""
    calls = []
    body = '夜色一寸寸沉进廊檐，烛火把两个人的影子拉长又揉碎。' * 16  # >300 字

    def generate():
        calls.append(1)
        return body

    result = _bare_turn_executor(generate, attempts=5)
    assert len(calls) == 1  # 一次调用即发布，不进自愈
    assert body in result['result_text']
    assert '自动包裹发布' in result['trace'][-1]


def _bare_turn_executor(generate, attempts=0):
    """execute_turn 裸正文兜底测试脚手架：selfheal_attempts 可控。"""
    return roleplay_turn.execute_turn(
        roleplay_turn.TurnExecution(
            ctx={}, text='输入', trace=[], streamed=False,
            deps=None, turn=1, affinity=None, lost=False,
            selfheal_attempts=attempts,
        ),
        roleplay_turn.TurnExecutionHooks(
            generate=generate,
            generated=lambda _reply: None,
            finalization=roleplay_turn.TurnFinalizationHooks(
                writeback=lambda item, events: (item.reply, [], {}, {}),
                apply_output=lambda value: value,
                anchor_offset=lambda reply, request: None,
                emit_ready=lambda ctx, result: False,
                maintain=lambda item, reply, events: None,
            ),
        ),
    )


def test_裸正文含思考混排不兜底():
    """思考文字（灰魂吐槽/md 样式）不得发布成正文 → 不包裹，交回既有判定。"""
    calls = []
    think_body = "* 灰魂吐槽\n   - 主人这波进攻节奏不错。\n" + "夜色漫过回廊。" * 10

    def generate():
        calls.append(1)
        return think_body

    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):
        _bare_turn_executor(generate, attempts=0)


def test_裸正文过短不兜底():
    body = '太短了。'
    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):
        _bare_turn_executor(lambda: body, attempts=0)


def test_已有content标签不触发裸正文兜底():
    """带完整标签的正常输出不受兜底影响（防止误包已合规输出）。"""
    calls = []

    def generate():
        calls.append(1)
        return "<think>简短思考</think>\n<content>\n这是合规正文，超过三百字……" + "合规正文内容。" * 20 + "\n</content>"

    result = _bare_turn_executor(generate, attempts=5)
    assert len(calls) == 1
    assert '合规正文' in result['result_text']


def test_重掷输出整段裸正文时包裹发布():
    """自愈循环里重掷又出裸正文 → 本轮包裹发布，不再烧剩余预算。"""
    calls = []
    body = "重掷裸正文段落。" * 40  # >300 字

    def generate():
        calls.append(1)
        if len(calls) == 1:
            return "<content>\n首发截断正文没有闭合标签"  # 截断 → 进自愈
        return body  # 重掷：整段裸正文 → 兜底包裹

    result = _bare_turn_executor(generate, attempts=2)
    assert len(calls) == 2
    assert body in result["result_text"]


# ── 用户停止（cancel_event）中断语义（2026-09-04 事故修复：正文链此前不感知停止，
#    selfheal 无视中断连续整段 reroll 从头生成白烧 token）──

def _noop_finalization():
    return roleplay_turn.TurnFinalizationHooks(
        writeback=lambda _draft, _events: ("visible", [], {}, {}),
        apply_output=lambda reply: reply,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: True,
        maintain=lambda _draft, _reply, _events: None,
    )


def test_用户停止时首生成异常直接抛中断不进自愈():
    """F2（2026-09-04）：cancel_event 置位 + 首生成异常 → RoleplayInterrupted，
    不再当「首次生成失败」进 selfheal 整段重掷（此前每重掷一次=又一次从头生成烧钱）。"""
    order: list = []
    stop = threading.Event()
    stop.set()
    turn = roleplay_turn.TurnExecution(
        ctx={"repo_id": "work", "cancel_event": stop}, text="继续", trace=[],
        streamed=True, deps=object(), turn=2, affinity=0, lost=False,
    )
    hooks = roleplay_turn.TurnExecutionHooks(
        generate=lambda: (_ for _ in ()).throw(TimeoutError("用户中断引发的流异常")),
        generated=lambda _reply: order.append("generated"),
        finalization=_noop_finalization(),
    )
    with pytest.raises(roleplay_turn.RoleplayInterrupted):
        roleplay_turn.execute_turn(turn, hooks)
    assert order == []  # 未进自愈、零额外模型调用


def test_自愈重掷期间用户停止立即中断不继续重掷():
    """F3（2026-09-04）：首发截断进自愈 → 重掷调用中用户点停止 → 立即中断，
    不再进入下一轮自愈（此前唯一中止条件是 6 分钟总时长预算，可连续 reroll N 次）。"""
    stop = threading.Event()
    calls: list = []

    def generate() -> str:
        calls.append(1)
        if len(calls) == 1:
            return "<think>ok</think>\n<content>断点正文"  # content 开未闭 → 截断
        stop.set()  # 重掷进行中用户点停止（等价流式层检测到 cancel_event）
        raise TimeoutError("重掷时用户中断流")

    turn = roleplay_turn.TurnExecution(
        ctx={"repo_id": "work", "cancel_event": stop}, text="继续", trace=[],
        streamed=True, deps=object(), turn=2, affinity=0, lost=False,
    )
    hooks = roleplay_turn.TurnExecutionHooks(
        generate=generate,
        generated=lambda _reply: None,
        finalization=_noop_finalization(),
    )
    with pytest.raises(roleplay_turn.RoleplayInterrupted):
        roleplay_turn.execute_turn(turn, hooks)
    assert len(calls) == 2  # 首发 1 次 + 重掷 1 次即停，绝无第 3 次


def test_候选截断且用户已停止不进自愈直接抛中断():
    """F2 校验失败路径：候选不完整 + cancel_event 置位 → 中断（不因截断再 reroll）。"""
    stop = threading.Event()
    calls: list = []

    def generate() -> str:
        calls.append(1)
        stop.set()  # 生成结束前用户点了停止
        return "<content>正文写到一半没闭合"  # 校验必然判截断

    turn = roleplay_turn.TurnExecution(
        ctx={"repo_id": "work", "cancel_event": stop}, text="继续", trace=[],
        streamed=True, deps=object(), turn=2, affinity=0, lost=False,
    )
    hooks = roleplay_turn.TurnExecutionHooks(
        generate=generate,
        generated=lambda _reply: None,
        finalization=_noop_finalization(),
    )
    with pytest.raises(roleplay_turn.RoleplayInterrupted):
        roleplay_turn.execute_turn(turn, hooks)
    assert len(calls) == 1


def test_wrap_bare_body_preserves_latest_status_block():
    """裸输出 <status>新栏</status>+正文时，包装必须保留状态栏（2026-09-12 实锤）。

    旧逻辑把 status 当残留剥掉 → extract 为空 → ensure_status_snapshot 前置旧快照，
    状态卡/数据表被冻结在旧值。修复后取最后一块放回 <content> 之前。
    """
    raw = (
        "<status>\n[时间] 旧栏\n</status>\n"
        "<status>\n[时间] 大比前三日·入夜\n[好感] 凌若冰=38\n</status>\n"
        + "剧情正文。" + "字" * 320
    )
    wrapped = roleplay_turn._wrap_bare_body(raw)
    assert wrapped, "完整裸正文应被包装发布"
    assert wrapped.index("[时间] 大比前三日·入夜") < wrapped.index("<content>")
    assert "[时间] 旧栏" not in wrapped, "应只保留最后一块状态栏"
    assert "<content>" in wrapped and "</content>" in wrapped
    # 与 extract 口径闭环：抽出的快照 = 新栏
    from app.services import roleplay_agency
    assert roleplay_agency.extract_status_snapshot(wrapped) == "[时间] 大比前三日·入夜\n[好感] 凌若冰=38"


def test_wrap_bare_body_still_strips_event_blocks():
    """roll/encounter 等事件块照旧剥掉，状态栏以外的防御语义不变。"""
    raw = "<roll>1d100=77</roll>" + "剧情正文。" + "字" * 320
    wrapped = roleplay_turn._wrap_bare_body(raw)
    assert wrapped and "<roll>" not in wrapped and "</roll>" not in wrapped


def test_wrap_bare_body_wraps_leading_bare_cot_into_think():
    """裸 COT（漏 <think> 标签）+ 完整正文：补包 think 保留思考，不判死重掷。

    用户设计口径：思考发布后由前端「小cot折叠」正则折叠为「推演内思考过程」下拉
    （2026-09-12 澄清）。旧逻辑见 marker 词就整段拒绝 → 白烧一次重掷且思考归零。
    """
    cot = (
        "灰魂吐槽：主人这是要当着娘亲的面直接抓恋娘胸口揉奶了……\n\n"
        "输入分析：主人选择继续抱着凌若冰，腾出一只手去抓舞姬恋的胸口。\n\n"
        "认知情境：当前浮花泉→回廊，凌渊赤身抱着凌若冰。\n\n"
        "关键NPC分析：舞姬恋——乳柔媚体的母性慈爱与骚浪雌媚并存。\n\n"
        "文风规划：短句为主，动作密。"
    )
    body_text = "凌渊没有放开凌若冰。他一只手掌从她膝弯底下穿过。" + "字" * 320
    wrapped = roleplay_turn._wrap_bare_body(cot + "\n\n" + body_text)
    assert wrapped, "裸 COT+完整正文应包装发布而非判死"
    assert wrapped.startswith("<think>")
    assert "</think>" in wrapped
    assert "灰魂吐槽" in wrapped and wrapped.index("灰魂吐槽") < wrapped.index("<content>")
    assert "<content>" in wrapped and "凌渊没有放开凌若冰" in wrapped
    # think 在 content 之外，结构校验通过（包装内部已过 ensure）
    roleplay_turn.ensure_complete_visible_content(wrapped)


def test_wrap_bare_body_rejects_cot_interleaved_in_body():
    """思考混在正文中间（开头不是 COT 段）：切不干净 → 照旧拒绝兜底。"""
    raw = "正文开头。" + "字" * 320 + "\n\n灰魂吐槽：这一段思考出现在正文中间。"
    assert roleplay_turn._wrap_bare_body(raw) == ""


def test_wrap_bare_body_cot_prefix_and_body_mentioning_tuoyan():
    """「思考（推演内过程）：」前缀 COT + 正文含「推演」宽词：必须包装成功不误杀。

    09-12 21:11 实锤：宽词口径把含「推演」的正文误判为思考混排 → 重掷丢思考丢正文。
    """
    cot = (
        "思考（推演内过程）：\n"
        "灰魂吐槽：好家伙，主人这是彻底放飞了。\n\n"
        "输入分析：主人明确指令有三步，核心是竞争。\n\n"
        "认知情境：社交规则已经被彻底改写。\n\n"
        "文风规划：短句为主。"
    )
    body_text = "他忽然明白这场推演的规则早已改写。" + "字" * 320
    wrapped = roleplay_turn._wrap_bare_body(cot + "\n\n" + body_text)
    assert wrapped, "正文合理引用「推演」不应被误杀"
    assert wrapped.startswith("<think>")
    assert "灰魂吐槽" in wrapped
    roleplay_turn.ensure_complete_visible_content(wrapped)


def test_wrap_bare_body_strips_status_update_block_with_content():
    """<状态更新> 增量 JSON 块必须整块含内容剥除，不得漏进正文（09-12 21:11 截图实锤）。"""
    body_text = "凌渊垂眼看去，两个女人并排跪着。" + "字" * 320
    raw = (
        body_text
        + "\n\n<状态更新>[{\"field\":\"数值/凌若冰·好感度\",\"op\":\"add\",\"value\":3}]</状态更新>"
    )
    wrapped = roleplay_turn._wrap_bare_body(raw)
    assert wrapped and "好感度" not in wrapped and "field" not in wrapped
    assert "<状态更新>" not in wrapped
    assert "凌渊垂眼看去" in wrapped


def test_wrap_bare_body_rejects_cot_paragraph_in_middle_of_body():
    """思考段混在正文中间（行首标题）：切不干净 → 照旧拒绝兜底。"""
    raw = "正文开头。" + "字" * 320 + "\n\n认知情境：这一段思考出现在正文中间。"
    assert roleplay_turn._wrap_bare_body(raw) == ""


def test_wrap_bare_body_repairs_orphan_think_close_with_meta_note():
    """孤立 </think>（漏写开标签）+ 规划注释：注释补包 think，正文区间不得混入（09-12 23:15 实锤）。

    模型输出结构=正文A+（正文到此，继续往后续写一点：…）</think>+正文B，旧逻辑把
    注释和闭标签随裸正文包装发布，正文区间出现非正文内容。
    """
    body_a = "凌若冰含着他的龟头僵了一瞬，随即喉头一滚，缓缓吞回。" + "字" * 320
    note = "（正文到此，继续往后续写一点：凌渊的应对，以及本轮的收束和下一轮的钩子）"
    body_b = "凌渊的笑音还挂在喉间未散，那截低哑已经变成一段压不住的闷哼。" + "字" * 320
    wrapped = roleplay_turn._wrap_bare_body(body_a + "\n\n" + note + "</think>" + body_b)
    assert wrapped, "孤立闭标签情形应修复后包装发布，不得判死重掷"
    # 注释只出现在 think 块内，不出现在正文区间
    think_end = wrapped.index("</think>")
    assert wrapped.startswith("<think>") and note in wrapped[:think_end]
    content_start = wrapped.index("<content>")
    assert note not in wrapped[content_start:], "规划注释不得塞入正文区间"
    assert "</think>" not in wrapped[think_end + len("</think>"):content_start], "孤立闭标签必须剥除"
    # 正文 A、B 都保留
    assert "凌若冰含着他的龟头" in wrapped[content_start:]
    assert "凌渊的笑音还挂在喉间" in wrapped[content_start:]
    # 成对完整：全文恰一个 </think>（补包 think 的闭合）
    assert wrapped.count("</think>") == 1


def test_wrap_bare_body_orphan_close_without_note_still_publishes():
    """孤立 </think> 但前方无括号规划注释：只剥闭标签，正文照常包装。"""
    body_a = "正文开头。" + "字" * 320
    body_b = "正文收尾。" + "字" * 320
    wrapped = roleplay_turn._wrap_bare_body(body_a + "</think>" + body_b)
    assert wrapped and wrapped.count("</think>") == 0
    assert "<think>" not in wrapped, "无注释时不补包空 think"
    assert "正文开头" in wrapped and "正文收尾" in wrapped


# ── 正文元话语泄漏清洗（2026-09-13 用户实锤）──────────────────────────
# 模型把「输出纪律」清单抄进 <content>：首行「质量检查：满足1000-8000字；状态栏更新…」、
# 末行「【系统提示：以下为推演输出尾部标注，非正文内容】」。清洗**只作用于 <content>**——
# <think> 里的「质量检查」是预设思维链9 的设计产物，不是泄漏。

_LEAK_NARR = (
    "*她穿一身压暗纹的玄色窄袍，乌黑齐肩短发被风掀起几缕，灰紫色的瞳仁里映着雪光，冷得像刀。*\n\n"
    "「就是你？」*她开口，声音清凌凌的，带着这个年纪少有的沉。*\n\n"
    "*她转身就走，玄色的衣角在雪地上扫出长长的痕。你跟上她，两个孩子追到门口，怯怯地喊小哥哥。*\n\n"
    "「进了那个门，你就是幽影的人了。」*她说，声音裹在风里，辨不出是冷还是软。*\n"
)


def test_正文元话语清洗只动content不动think与status():
    reply = (
        "<think>\n质量检查：\n- 平滑过渡：衔接自然\n- 拒绝无关细节\n</think>\n\n"
        "<status>\n[时间] 初冬日昃\n</status>\n\n"
        "<content>\n质量检查：满足1000-8000字；状态栏更新收养线与目的地；\n\n"
        + _LEAK_NARR
        + "\n【系统提示：以下为推演输出尾部标注，非正文内容】\n</content>"
    )
    cleaned, removed = roleplay_turn.strip_meta_leak_in_content(reply)
    assert len(removed) == 2
    think_part, content_part = cleaned.split("<content>", 1)
    assert "质量检查" in think_part, "think 内的思维链检查项必须保留"
    assert "[时间] 初冬日昃" in cleaned, "status 字段不得被清洗"
    assert "质量检查" not in content_part
    assert "系统提示" not in content_part
    assert "玄色窄袍" in content_part


def test_正文元话语清洗无残留时原样返回():
    reply = "<content>\n" + _LEAK_NARR + "\n</content>"
    cleaned, removed = roleplay_turn.strip_meta_leak_in_content(reply)
    assert cleaned == reply
    assert removed == []


def test_execute_turn发布前清洗正文元话语():
    """整链验证：脏回复经 execute_turn 后，落库/发布的正文不带元话语。"""
    reply = (
        "<think>\n灰魂吐槽：开写。\n</think>\n\n"
        "<status>\n[时间] 暮色\n</status>\n\n"
        "<content>\n质量检查：满足1000-8000字；状态栏更新收养线与目的地；\n\n"
        + _LEAK_NARR
        + "\n【系统提示：以下为推演输出尾部标注，非正文内容】\n</content>"
    )
    published: list[str] = []
    turn = roleplay_turn.TurnExecution(
        ctx={"repo_id": "work"}, text="继续", trace=[], streamed=True,
        deps=None, turn=1, affinity=0, lost=False,
    )
    hooks = roleplay_turn.TurnExecutionHooks(
        generate=lambda: reply,
        generated=lambda _r: None,
        finalization=roleplay_turn.TurnFinalizationHooks(
            writeback=lambda d, _e: (d.reply, [], {}, {}),
            apply_output=lambda r: r,
            anchor_offset=lambda _r, _q: None,
            emit_ready=lambda _c, res: published.append(res["result_text"]) or False,
            maintain=lambda *_a: None,
        ),
    )
    roleplay_turn.execute_turn(turn, hooks)
    assert published, "必须发布"
    assert "质量检查" not in published[0].split("<content>", 1)[1]
    assert "系统提示" not in published[0]
    assert "玄色窄袍" in published[0]
