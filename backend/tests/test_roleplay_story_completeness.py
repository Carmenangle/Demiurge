"""剧情完整度闸（`roleplay_turn._complete_story`，2026-09-21 用户定案）。

用户口径：「现有的重试机制已经相当完善，但是还差判定剧情结束的机制——只要认为剧情还未
完善，重试就可以继续续写，最起码保证段落是完整的。」

覆盖三层：判据（`incomplete_story_reason`）／续写预填（`open_content_for_continuation`）／
整链（`execute_turn`：续写补全 · 补不动按原样发布 · 未接线逐字节等于旧行为）。
"""
import threading

import pytest

from app.services import agent_stream, roleplay_turn

_TITLE = "<think>\n灰魂吐槽：开写。\n</think>"
_STATUS = "<status>\n[时间] 暮色\n</status>"

# 结构合格（标签都闭合）且段落收束
_COMPLETE = f"{_TITLE}\n\n{_STATUS}\n\n<content>\n他推开门，风灌进来。\n\n她抬起头，什么也没说。\n</content>"
# 结构合格但末尾停在句中——正是本闸要抓的形态
_OPEN_TAIL = f"{_TITLE}\n\n{_STATUS}\n\n<content>\n他推开门，风灌进来。\n\n她抬起头，正要说\n</content>"
# 正文尾巴上残留模型写给自己看的续写注记
_NOTE_TAIL = (
    f"{_TITLE}\n\n<content>\n他推开门，风灌进来。\n\n"
    "（正文到此，继续往后续写一点：她抬起头要说什么）\n</content>"
)
# 一次成功的剧情续写（结尾收束，篇幅足够）
_CONTINUED = '什么，却被门外的脚步声打断。\n\n"谁？"他压低声音，手按在刀柄上。\n</content>'


# ── 判据 ────────────────────────────────────────────────────────────────────

def test_判据_完整正文判为写完():
    assert roleplay_turn.incomplete_story_reason(_COMPLETE) == ""


@pytest.mark.parametrize("tail", ["他推开门，", "他推开门", "他推开门：", "他伸出了手"])
def test_判据_句中断掉判为没写完(tail):
    reply = f"{_TITLE}\n\n<content>\n风灌进来。\n\n{tail}\n</content>"
    assert "段落未写完" in roleplay_turn.incomplete_story_reason(reply)


@pytest.mark.parametrize(
    "tail", ["他推开门。", "他说：「谁？」", "他的嘴角动了动——", "他张了张嘴……", "他点了点头」"])
def test_判据_句末标点判为写完(tail):
    reply = f"{_TITLE}\n\n<content>\n风灌进来。\n\n{tail}\n</content>"
    assert roleplay_turn.incomplete_story_reason(reply) == ""


def test_判据_markdown尾巴不算没写完():
    reply = f"{_TITLE}\n\n<content>\n风灌进来。\n\n**他推开门。**\n</content>"
    assert roleplay_turn.incomplete_story_reason(reply) == ""


def test_判据_think里的规划注记不算():
    # 模型在思考里写「正文到此，继续往后续写」是设计产物，正文本身是收束的
    reply = (
        "<think>\n（正文到此，继续往后续写一点：她抬起头）\n</think>\n\n"
        "<content>\n他推开门，风灌进来。\n</content>"
    )
    assert roleplay_turn.incomplete_story_reason(reply) == ""


def test_判据_正文末尾自留注记判为没写完():
    # 注记以「）」结尾——若先判标点会误判成写完，故硬信号必须排在段落判据之前
    reason = roleplay_turn.incomplete_story_reason(_NOTE_TAIL)
    assert "续写注记" in reason


def test_判据_只看content内层不论status位置():
    # status 位于 content 之后且以句号结尾——不得把正文的「停在半句」洗成「写完」
    reply = (
        f"{_TITLE}\n\n<content>\n他推开门，风灌进来。\n\n她抬起头，正要说\n</content>\n"
        "<status>\n[时间] 夜色\n</status>"
    )
    assert "段落未写完" in roleplay_turn.incomplete_story_reason(reply)


def test_判据_篇幅下限默认关闭():
    short = f"{_TITLE}\n\n<content>\n他推开门。\n</content>"
    assert roleplay_turn.incomplete_story_reason(short) == ""
    assert "篇幅下限" in roleplay_turn.incomplete_story_reason(short, min_body_chars=800)


# ── 续写预填 ────────────────────────────────────────────────────────────────

def test_续写预填_摘掉闭合标签与尾部块():
    reply = f"{_TITLE}\n\n<content>\n他推开门，风灌进来。\n</content>\n<roll>\n1d100=42\n</roll>"
    prefill = roleplay_turn.open_content_for_continuation(reply)
    assert prefill.startswith(_TITLE)        # think 前缀保留：文风/格式接得上
    assert prefill.endswith("风灌进来。")
    assert "<content>" in prefill
    assert "</content>" not in prefill       # 让模型接着在同一 content 里往下写
    assert "<roll>" not in prefill           # 尾部块交给续写重新产出


def test_续写预填_think内协议字面量不误认():
    reply = (
        "<think>\n按协议检查 <content> 标签是否闭合。\n</think>\n\n"
        "<content>\n他推开门，风灌进来。\n</content>"
    )
    prefill = roleplay_turn.open_content_for_continuation(reply)
    assert prefill.endswith("风灌进来。")
    assert "</content>" not in prefill


def test_续写预填_没有content块返回空串():
    assert roleplay_turn.open_content_for_continuation("裸正文没有标签。") == ""


# ── 整链 ────────────────────────────────────────────────────────────────────

def _story_result(reply, continuations, *, attempts=3, ctx=None, wire_story=True):
    """剧情续写整链脚手架：continuations 为**逐次**续写调用的返回值（空串＝这次补不动）。"""
    calls: list = []
    queue = list(continuations)

    def continue_story(partial):
        calls.append(("story", partial))
        return queue.pop(0) if queue else ""

    finalization = roleplay_turn.TurnFinalizationHooks(
        writeback=lambda item, events: (item.reply, [], {}, {}),
        apply_output=lambda value: value,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: False,
        maintain=lambda item, reply, events: None,
    )
    hooks = roleplay_turn.TurnExecutionHooks(
        generate=lambda: reply,
        generated=lambda _reply: None,
        continue_story=continue_story if wire_story else None,
        finalization=finalization,
    )
    turn = roleplay_turn.TurnExecution(
        ctx=ctx if ctx is not None else {}, text="输入", trace=[], streamed=False,
        deps=None, turn=1, affinity=None, lost=False, selfheal_attempts=attempts,
    )
    return turn, roleplay_turn.execute_turn(turn, hooks), calls


def test_整链_完整正文不多花一次调用():
    _turn, result, calls = _story_result(_COMPLETE, [_CONTINUED])
    assert calls == []
    assert result["result_text"] == _COMPLETE


def test_整链_剧情没写完会续写补全并发布合并正文():
    _turn, result, calls = _story_result(_OPEN_TAIL, [_CONTINUED])
    assert len(calls) == 1 and calls[0][0] == "story"
    # 预填＝摘掉闭合标签的正文：原句尾必须在场，模型才接得上
    assert "她抬起头，正要说" in calls[0][1]
    assert "</content>" not in calls[0][1]
    published = result["result_text"]
    assert "却被门外的脚步声打断" in published
    assert published.count("<content>") == 1
    assert published.count("</content>") == 1
    # 成功的续写提示不留痕（与截断自愈同口径：只有一条 merged 正文发给用户）
    assert not any("正在续写补全剧情" in line for line in result["trace"])


def test_整链_续写漏掉闭合标签时自动补上():
    _turn, result, calls = _story_result(
        _OPEN_TAIL, ["什么，却被门外的脚步声硬生生打断，她猛地回过头，手按在刀柄上。"])
    assert len(calls) == 1
    assert result["result_text"].rstrip().endswith("</content>")
    assert "她猛地回过头" in result["result_text"]


def test_整链_续写补不动时按当前正文发布():
    _turn, result, calls = _story_result(_OPEN_TAIL, ["（没有任何新增）"])
    assert len(calls) == 1
    assert result["result_text"] == _OPEN_TAIL          # 已合格正文绝不作废
    assert any("按当前正文发布" in line for line in result["trace"])


def test_整链_预算用尽按当前正文发布():
    # 每次都续写、但每次都仍然停在半句 ⇒ 耗尽预算后发布最后一版，并留一条汇总
    halved = [
        "什么，却被门外的脚步声硬生生打断。她猛地回过头，手已经按在了刀柄上，正要开口\n</content>",
        "说些什么，门却在这时被人从外面猛地推开了，风卷着雪灌进来，她的话音\n</content>",
    ]
    _turn, result, calls = _story_result(_OPEN_TAIL, halved, attempts=2)
    assert len(calls) == 2                              # 预算＝2，第三次不再调用
    assert "风卷着雪灌进来" in result["result_text"]
    assert any("剧情续写 2 次后仍未写完整" in line for line in result["trace"])


def test_整链_续写重复输出content标签被拒():
    duplication = (
        "他接着把门外发生的事从头到尾一一说给她听，一直说到天快亮了，最后才"
        '<content>"谁？"</content>，然后闭上了嘴。\n</content>'
    )
    _turn, result, calls = _story_result(_OPEN_TAIL, [duplication])
    assert len(calls) == 1
    assert result["result_text"] == _OPEN_TAIL
    assert any("按当前正文发布" in line for line in result["trace"])


def test_整链_未接线剧情闸关闭逐字节等于旧行为():
    _turn, result, calls = _story_result(_OPEN_TAIL, [_CONTINUED], wire_story=False)
    assert calls == []
    assert result["result_text"] == _OPEN_TAIL


def test_整链_自愈次数为0时剧情闸关闭():
    _turn, result, calls = _story_result(_OPEN_TAIL, [_CONTINUED], attempts=0)
    assert calls == []
    assert result["result_text"] == _OPEN_TAIL


def test_整链_用户停止后不再续写():
    stop = threading.Event()
    stop.set()
    _turn, result, calls = _story_result(
        _OPEN_TAIL, [_CONTINUED], ctx={"cancel_event": stop})
    assert calls == []
    assert result["result_text"] == _OPEN_TAIL
    assert any("剧情续写已中止" in line for line in result["trace"])


# ── 续写指令 ────────────────────────────────────────────────────────────────

def test_续写指令_剧情分支要求接着往下写():
    wire = [{"role": "system", "content": "sys"}, {"role": "user", "content": "继续"}]
    messages = agent_stream._roleplay_continuation_messages(
        wire, "预填正文", story_incomplete=True)
    assert messages[:len(wire)] == wire
    assert messages[-2] == {"role": "assistant", "content": "预填正文"}
    instruction = messages[-1]["content"]
    assert "没有写完" in instruction
    assert "不要重新输出 <content>" in instruction
    assert "禁止重复已有内容" in instruction


def test_续写指令_缺省仍走截断分支():
    wire = [{"role": "user", "content": "继续"}]
    messages = agent_stream._roleplay_continuation_messages(wire, "半截正文")
    assert messages[-2] == {"role": "assistant", "content": "半截正文"}
    assert "没有闭合" in messages[-1]["content"]


# ── 截断自愈 × 剧情闸（2026-09-21 用户定案「要接上」）──────────────────────
#
# 用户口径：「要接上（但别忘记了之前的重试按照我的设定还是拼接而不是从头到尾重新生成）」。
# 截断自愈只保证**标签闭合**，续写/重掷出来的正文可能仍停在半段剧情 ⇒ 自愈成功后必须
# 再跑一次剧情完整度闸；且续写语义与截断自愈一致——**在既有正文上接着写，不整段重掷**。

# 首次生成：正文阶段截断（content 开而未闭，断点前正文足量）
_TRUNC_PARTIAL = f"{_TITLE}\n\n<content>\n" + "她" * 320 + "，写到一半被掐断"
# 截断续写：闭合了标签，但正文**仍停在半句**——正是「自愈成功但剧情没写完」
_TRUNC_CONTINUED = (
    "\n\n她终于开了口，声音却轻得像落在积雪上的灰，风一吹就散在了夜色里，谁也没听清她\n</content>"
)
# 剧情续写：把那个半句接完并收束（拼接，不是重写）
_STORY_CONTINUED = "\n\n说的那句「我一直在等你回来」，直到很多年以后，他才明白那句话的意思。\n</content>"


def _finalization_hooks():
    return roleplay_turn.TurnFinalizationHooks(
        writeback=lambda item, events: (item.reply, [], {}, {}),
        apply_output=lambda value: value,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: False,
        maintain=lambda item, reply, events: None,
    )


def _heal_then_story(*, story_continuations=(), wire_story=True, attempts=3, ctx=None):
    """截断自愈 ＋ 剧情闸整链：首次生成必然截断，截断续写必然成功但剧情没写完。

    逐次续写返回值由 `story_continuations` 依次给出（空串＝这次补不动）。
    """
    calls: list = []
    queue = list(story_continuations)

    def generate():
        calls.append("generate")
        return _TRUNC_PARTIAL

    def generated(_reply):
        calls.append("generated")

    def continue_generate(partial):
        calls.append(("trunc", partial))
        return _TRUNC_CONTINUED

    def continue_story(prefill):
        calls.append(("story", prefill))
        return queue.pop(0) if queue else ""

    hooks = roleplay_turn.TurnExecutionHooks(
        generate=generate,
        generated=generated,
        continue_generate=continue_generate,
        continue_story=continue_story if wire_story else None,
        finalization=_finalization_hooks(),
    )
    turn = roleplay_turn.TurnExecution(
        ctx=ctx if ctx is not None else {}, text="输入", trace=[], streamed=False,
        deps=None, turn=1, affinity=None, lost=False, selfheal_attempts=attempts,
    )
    return turn, roleplay_turn.execute_turn(turn, hooks), calls


def test_接上_截断自愈后仍会跑剧情闸并接着写():
    _turn, result, calls = _heal_then_story(story_continuations=[_STORY_CONTINUED])
    # 调用顺序＝首发 → 截断续写 → 剧情续写：剧情闸**排在自愈成功之后**
    assert [c[0] if isinstance(c, tuple) else c for c in calls] == [
        "generate", "generated", "trunc", "generated", "story", "generated"]
    assert next(c[1] for c in calls if isinstance(c, tuple) and c[0] == "story") \
        .rstrip().endswith("谁也没听清她")            # 预填＝已自愈正文（原句尾在场才接得上）
    published = result["result_text"]
    assert "谁也没听清她" in published                # 截断续写的正文被保住
    assert "直到很多年以后" in published              # 剧情续写的正文接在后面
    assert published.count("<content>") == 1 and published.count("</content>") == 1
    assert calls.count("generate") == 1               # ⚠ 拼接：全程没有整段重掷


def test_接上_自愈后剧情已写完不再多花一次调用():
    # 截断续写正好停在句末标点上（如「…被掐断。」）⇒ 剧情闸判为写完，零附加调用
    calls: list = []

    def generate():
        calls.append("generate")
        return _TRUNC_PARTIAL

    def generated(_reply):
        calls.append("generated")

    def continue_generate(partial):
        calls.append(("trunc", partial))
        return "\n\n她终于开了口，声音却轻得像落在积雪上的灰，谁也没有听清她说了些什么。\n</content>"

    hooks = roleplay_turn.TurnExecutionHooks(
        generate=generate, generated=generated,
        continue_generate=continue_generate,
        continue_story=lambda prefill: calls.append(("story", prefill)) or _STORY_CONTINUED,
        finalization=_finalization_hooks(),
    )
    turn = roleplay_turn.TurnExecution(
        ctx={}, text="输入", trace=[], streamed=False, deps=None, turn=1,
        affinity=None, lost=False, selfheal_attempts=3)
    result = roleplay_turn.execute_turn(turn, hooks)
    assert not any(isinstance(c, tuple) and c[0] == "story" for c in calls)
    assert "直到很多年以后" not in result["result_text"]


def test_接上_自愈成功后补不动剧情按当前正文发布():
    _turn, result, calls = _heal_then_story(story_continuations=["（补不动）"])
    assert len([c for c in calls if isinstance(c, tuple) and c[0] == "story"]) == 1
    assert "谁也没听清她" in result["result_text"]     # 已付费正文绝不作废
    assert any("按当前正文发布" in line for line in result["trace"])


def test_接上_未接线剧情闸时自愈路径逐字节不变():
    _turn, result, calls = _heal_then_story(wire_story=False)
    assert not any(isinstance(c, tuple) and c[0] == "story" for c in calls)
    # 与旧行为一致：拼接后的自愈正文原样发布（含那个没写完的尾巴）
    assert result["result_text"].rstrip().endswith("谁也没听清她\n</content>".rstrip())


def test_接上_自愈成功后先剥元话语再判剧情():
    # 自愈正文尾巴上抄了「输出纪律」行（不以句末标点结尾）⇒ 必须先剥再进闸：
    # 否则纪律行会被当正文发布，还会让闸误判「没写完」白烧一次续写。
    calls: list = []

    def generate():
        calls.append("generate")
        return _TRUNC_PARTIAL

    def generated(_reply):
        calls.append("generated")

    def continue_generate(partial):
        calls.append(("trunc", partial))
        return ("\n\n她终于开了口，声音却轻得像落在积雪上的灰，风一吹就散在了夜色里，"
                "谁也没听清了，那扇门再也没有打开过。\n字数要求：正文不少于1000字\n</content>")

    hooks = roleplay_turn.TurnExecutionHooks(
        generate=generate, generated=generated,
        continue_generate=continue_generate,
        continue_story=lambda prefill: calls.append(("story", prefill)) or _STORY_CONTINUED,
        finalization=_finalization_hooks(),
    )
    turn = roleplay_turn.TurnExecution(
        ctx={}, text="输入", trace=[], streamed=False, deps=None, turn=1,
        affinity=None, lost=False, selfheal_attempts=3)
    result = roleplay_turn.execute_turn(turn, hooks)
    assert not any(isinstance(c, tuple) and c[0] == "story" for c in calls)
    assert "字数要求" not in result["result_text"]
    assert any("正文元话语残留已剥离" in line for line in result["trace"])


def test_接上_自愈次数为0时两段闸都关闭():
    # 预算 0 ⇒ 截断自愈循环一次不跑（不重试）、剧情闸也拿不到预算 ⇒ 直接判死上抛
    with pytest.raises(roleplay_turn.TruncatedRoleplayOutput):
        _heal_then_story(attempts=0)


def test_接上_用户停止后自愈与剧情闸都不再调用():
    # 中断≠失败：停止信号先于一切重试被看到（截断自愈循环第一轮就上抛中断）
    stop = threading.Event()
    stop.set()
    with pytest.raises(roleplay_turn.RoleplayInterrupted):
        _heal_then_story(story_continuations=[_STORY_CONTINUED], ctx={"cancel_event": stop})



def test_生成字数下限开启后判未写完_关闭时不判():
    """2026-09-22：`min_body_chars` 是「生成字数下限」的判据本体，默认 0＝关。"""
    from app.services import roleplay_turn as rt
    short = "<content>" + "他推开门。" * 5 + "</content>"
    # 关闭（默认）：末尾是句末标点 ⇒ 判「写完」，零回归
    assert rt.incomplete_story_reason(short) == ""
    # 开启：低于下限 ⇒ 判「没写完」，理由带字数
    reason = rt.incomplete_story_reason(short, min_body_chars=3000)
    assert "低于篇幅下限 3000 字" in reason
    # 达标 ⇒ 判「写完」
    assert rt.incomplete_story_reason(short, min_body_chars=10) == ""
