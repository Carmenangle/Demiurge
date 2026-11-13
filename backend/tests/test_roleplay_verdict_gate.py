"""未遂对账回合闸（`roleplay_turn._repair_verdict_attempts`，2026-11-06 用户定案）。

用户口径：把「掷骰失败却写成得手」从**只报告**（`narrative_ci` 的 `npc_verdict_break`
诊断臂）升级为**生成侧补救**——命中即在同一份正文上续写，把该尝试写成未遂/受挫；
补不动按原样发布（非致命、不整段重掷、每轮最多一次调用）。

覆盖：判据（`narrative_ci.violating_verdict_claims`）／续写指令
（`roleplay_agency.verdict_fix_directive`）／闸（`execute_turn`）／自愈路径接续顺序／
续写消息分支／接线源码锁。
"""
import inspect
import threading

import pytest

from app.services import (
    agent_graph_roleplay,
    agent_stream,
    narrative_ci,
    roleplay_agency,
    roleplay_turn,
)

_TITLE = "<think>\n灰魂吐槽：先看裁定。\n</think>"
_STATUS = "<status>\n[在场] 沈青梧、谢若冰\n[时间] 暮色\n</status>"
# 结构合格、收束完整，但正文把「送汤守夜被拒」写成了已办成
_VIOLATING = (
    f"{_TITLE}\n\n{_STATUS}\n\n<content>\n"
    "沈青梧把汤盅搁在书案角上，轻声告退，退到门边立着。\n</content>"
)
# 正文自带失败措辞 ⇒ 合规
_COMPLIANT = _VIOLATING.replace("退到门边立着。", "退到门边立着，终究没能把汤送到案前。")
# 一次成功的裁定续写：接在原句尾之后，写明未遂并收束
_FIXED = (
    "\n\n她在廊下等到更漏将尽，终究没能等到书房里的人应声，只得把汤盅交回小厮，"
    "独自沿着长街走回去。\n</content>"
)
# 续写补上来了，但正文仍没写失败 ⇒ 只留一条汇总，不进入第二轮
_STILL_BAD = (
    "\n\n她把窗子掩上，随后回到廊下站着，夜色一层一层沉下来，"
    "长街上再没有半个人影经过。\n</content>"
)


def _claim(**over):
    base = {
        "kind": "npc", "actor": "沈青梧", "intent": "把汤送进书房并留下守夜",
        "roll": 95, "chance": 46, "outcome": "reject", "degree": "none",
        "reason": "骰点高于技能", "goal": "",
    }
    base.update(over)
    return base


# ── 判据（单一属主：CI 报告与回合闸共用）────────────────────────────────────

def test_判据_违规裁定被检出():
    hits = narrative_ci.violating_verdict_claims(_VIOLATING, [_claim()])
    assert [claim["actor"] for claim in hits] == ["沈青梧"]
    assert hits[0]["intent"] == "把汤送进书房并留下守夜"


def test_判据_正文写了失败措辞就不算违规():
    assert narrative_ci.violating_verdict_claims(_COMPLIANT, [_claim()]) == []


def test_判据_失败措辞只出现在状态栏不算():
    reply = (
        f"{_TITLE}\n\n<status>\n[在场] 沈青梧\n备注：没能赴约\n</status>\n\n"
        "<content>\n沈青梧把汤盅搁在书案角上，轻声告退。\n</content>"
    )
    assert len(narrative_ci.violating_verdict_claims(reply, [_claim()])) == 1


def test_判据_正文用代称时靠在场行认人():
    reply = (
        f"{_TITLE}\n\n{_STATUS}\n\n<content>\n"
        "她把汤盅搁在书案角上，轻声告退，退到门边立着。\n</content>"
    )
    snapshot = "[在场] 沈青梧、谢若冰\n[时间] 暮色"
    assert len(narrative_ci.violating_verdict_claims(
        reply, [_claim()], scene_snapshot=snapshot)) == 1


def test_判据_名字既不在正文也不在状态栏就不判():
    reply = (
        f"{_TITLE}\n\n<status>\n[在场] 谢若冰\n</status>\n\n<content>\n"
        "谢若冰把汤盅搁在书案角上，轻声告退。\n</content>"
    )
    assert narrative_ci.violating_verdict_claims(reply, [_claim()]) == []


@pytest.mark.parametrize("over", [
    {"kind": "event"},
    {"kind": "user_action"},
    {"roll": 0},
    {"intent": ""},
])
def test_判据_人群口径与叙事指令同源(over):
    # event 写「只以预兆/余波出现」、user_action 写「禁止变相成功」、未掷骰没失败可言、
    # 空 intent 无从核对——四种都不进本闸（与 `verdict_attempt_claims` 同源）。
    assert narrative_ci.violating_verdict_claims(_VIOLATING, [_claim(**over)]) == []


def test_判据_报告臂与闸共用同一判据():
    # 只报告那一臂必须复用同一个函数：两侧口径漂移会让「报的红」和「修的东西」对不上
    src = inspect.getsource(narrative_ci.check_verdict_attempts)
    assert "violating_verdict_claims(" in src


# ── 续写指令 ────────────────────────────────────────────────────────────────

def test_指令_逐条列出裁定与骰点():
    text = roleplay_agency.verdict_fix_directive([_claim()])
    assert "- 沈青梧：「把汤送进书房并留下守夜」" in text
    assert "D100=95" in text and "阈值 46" in text and "已裁为失败" in text


def test_指令_大失败用统一档位措辞():
    text = roleplay_agency.verdict_fix_directive([_claim(roll=99, degree="fumble")])
    assert "已裁为大失败" in text


def test_指令_空人群返回空串():
    assert roleplay_agency.verdict_fix_directive([]) == ""
    assert roleplay_agency.verdict_fix_directive([_claim(kind="event")]) == ""


# ── 闸（execute_turn）──────────────────────────────────────────────────────

def _finalization_hooks():
    return roleplay_turn.TurnFinalizationHooks(
        writeback=lambda item, events: (item.reply, [], {}, {}),
        apply_output=lambda value: value,
        anchor_offset=lambda _reply, _request: None,
        emit_ready=lambda _ctx, _result: False,
        maintain=lambda item, reply, events: None,
    )


def _gate_result(reply, *, continuations=(), attempts=3, ctx=None, wire=True):
    """未遂对账整链脚手架：continuations 为**逐次**裁定续写返回值（空串＝补不动）。"""
    calls: list = []
    queue = list(continuations)

    def continue_verdict(partial, claims):
        calls.append(("verdict", partial, list(claims)))
        return queue.pop(0) if queue else ""

    hooks = roleplay_turn.TurnExecutionHooks(
        generate=lambda: reply,
        generated=lambda _reply: None,
        continue_verdict=continue_verdict if wire else None,
        finalization=_finalization_hooks(),
    )
    turn = roleplay_turn.TurnExecution(
        ctx=ctx if ctx is not None else {}, text="输入", trace=[], streamed=False,
        deps=None, turn=1, affinity=None, lost=False, selfheal_attempts=attempts,
    )
    return turn, roleplay_turn.execute_turn(turn, hooks), calls


def test_闸_命中后恰好一次续写并发布合并正文():
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_FIXED], ctx={"_agency_npc_claims": [_claim()]})
    assert len(calls) == 1
    _kind, prefill, claims = calls[0]
    assert claims[0]["actor"] == "沈青梧"
    assert prefill.endswith("退到门边立着。")      # 预填保留原句尾，模型才接得上
    assert "</content>" not in prefill            # 让模型接着在同一 content 里写
    published = result["result_text"]
    assert "没能等到书房里的人应声" in published    # 续写正文接在后面
    assert "退到门边立着。" in published           # 已付费正文被保住
    assert published.count("<content>") == 1 and published.count("</content>") == 1
    assert any("🔧 未遂对账" in line for line in result["trace"])
    assert not any("正在续写纠正" in line for line in result["trace"])   # 进度提示不留痕


def test_闸_只续写不整段重掷():
    # 用户红线：结构已合格的正文绝不作废——补救只能走续写，generate 全程只调 1 次
    generates: list = []

    def generate():
        generates.append("generate")
        return _VIOLATING

    hooks = roleplay_turn.TurnExecutionHooks(
        generate=generate,
        generated=lambda _reply: None,
        continue_verdict=lambda _partial, _claims: _FIXED,
        finalization=_finalization_hooks(),
    )
    turn = roleplay_turn.TurnExecution(
        ctx={"_agency_npc_claims": [_claim()]}, text="输入", trace=[],
        streamed=False, deps=None, turn=1, affinity=None, lost=False,
        selfheal_attempts=3,
    )
    result = roleplay_turn.execute_turn(turn, hooks)
    assert generates == ["generate"]
    assert "没能等到书房里的人应声" in result["result_text"]


def test_闸_正文用代称时靠状态栏认人也算命中():
    reply = (
        f"{_TITLE}\n\n{_STATUS}\n\n<content>\n"
        "她把汤盅搁在书案角上，轻声告退，退到门边立着。\n</content>"
    )
    _turn, result, calls = _gate_result(
        reply, continuations=[_FIXED], ctx={"_agency_npc_claims": [_claim()]})
    assert len(calls) == 1
    assert "没能等到书房里的人应声" in result["result_text"]


def test_闸_合格正文不多花一次调用():
    _turn, result, calls = _gate_result(
        _COMPLIANT, continuations=[_FIXED], ctx={"_agency_npc_claims": [_claim()]})
    assert calls == []
    assert result["result_text"] == _COMPLIANT


def test_闸_未接线时逐字节等于旧行为():
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_FIXED],
        ctx={"_agency_npc_claims": [_claim()]}, wire=False)
    assert calls == []
    assert result["result_text"] == _VIOLATING


def test_闸_自愈次数为0时关闭():
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_FIXED],
        ctx={"_agency_npc_claims": [_claim()]}, attempts=0)
    assert calls == []
    assert result["result_text"] == _VIOLATING


def test_闸_没有裁定投影不猜():
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_FIXED], ctx={})
    assert calls == []
    assert result["result_text"] == _VIOLATING


def test_闸_非NPC投影不触发():
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_FIXED],
        ctx={"_agency_npc_claims": [_claim(kind="event")]})
    assert calls == []
    assert result["result_text"] == _VIOLATING


def test_闸_续写补不动按当前正文发布():
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=["（补不动）"], ctx={"_agency_npc_claims": [_claim()]})
    assert len(calls) == 1
    assert result["result_text"] == _VIOLATING           # 已合格正文绝不作废
    assert any("续写未生效（按当前正文发布）" in line for line in result["trace"])


def test_闸_用户停止后不再续写():
    stop = threading.Event()
    stop.set()
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_FIXED],
        ctx={"_agency_npc_claims": [_claim()], "cancel_event": stop})
    assert calls == []
    assert result["result_text"] == _VIOLATING
    assert any("裁定续写已中止" in line for line in result["trace"])


def test_闸_续写后仍没写成未遂只留一条汇总不循环():
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_STILL_BAD], ctx={"_agency_npc_claims": [_claim()]})
    assert len(calls) == 1                                # 每轮最多一次，不追打
    assert "夜色一层一层沉下来" in result["result_text"]
    assert any("仍有 1 条未检出" in line for line in result["trace"])


def test_闸_单轮多条裁定合并为一次调用():
    other = _claim(actor="谢若冰", intent="把药箱取来并当众验方", roll=89, chance=65)
    _turn, result, calls = _gate_result(
        _VIOLATING, continuations=[_FIXED],
        ctx={"_agency_npc_claims": [_claim(), other]})
    assert len(calls) == 1
    assert [claim["actor"] for claim in calls[0][2]] == ["沈青梧", "谢若冰"]


# ── 自愈路径接续（skill 坑 4：两条出口都要接上）────────────────────────────

_TRUNC_PARTIAL = f"{_TITLE}\n\n{_STATUS}\n\n<content>\n" + "沈青梧" * 110 + "，话还没说完"
_TRUNC_DONE = (
    "\n\n她终于把这句话说完，随后退到门边立着，手指在袖口里攥得发白，再也没有抬头。\n</content>"
)


def test_自愈路径_截断续写完仍违规时接着续写纠正():
    calls: list = []

    def generate():
        calls.append("generate")
        return _TRUNC_PARTIAL

    def continue_generate(_partial):
        calls.append("trunc")
        return _TRUNC_DONE

    def continue_verdict(_partial, _claims):
        calls.append("verdict")
        return _FIXED

    hooks = roleplay_turn.TurnExecutionHooks(
        generate=generate,
        generated=lambda _reply: None,
        continue_generate=continue_generate,
        continue_verdict=continue_verdict,
        finalization=_finalization_hooks(),
    )
    turn = roleplay_turn.TurnExecution(
        ctx={"_agency_npc_claims": [_claim()]}, text="输入", trace=[],
        streamed=False, deps=None, turn=1, affinity=None, lost=False,
        selfheal_attempts=3,
    )
    result = roleplay_turn.execute_turn(turn, hooks)
    # 调用顺序＝首发 → 截断续写 → 未遂对账续写；全程没有整段重掷
    assert calls == ["generate", "trunc", "verdict"]
    assert "没能等到书房里的人应声" in result["result_text"]


def test_接线_未遂对账排在剧情完整度闸之前():
    # 先修裁定、再判剧情：裁定续写本身也在往下写剧情，倒序会白烧一次调用
    for fn in (roleplay_turn.execute_turn, roleplay_turn._selfheal_loop):
        src = inspect.getsource(fn)
        assert src.index("_repair_verdict_attempts") < src.index("_complete_story"), fn.__name__


# ── 续写消息分支 ────────────────────────────────────────────────────────────

def test_续写指令_裁定分支回灌未遂要求():
    wire = [{"role": "system", "content": "sys"}, {"role": "user", "content": "继续"}]
    messages = agent_stream._roleplay_continuation_messages(
        wire, "预填正文",
        verdict_fix="- 沈青梧：「把汤送进书房并留下守夜」（D100=95／阈值 46，已裁为失败）")
    assert messages[:len(wire)] == wire
    assert messages[-2] == {"role": "assistant", "content": "预填正文"}
    instruction = messages[-1]["content"]
    assert "- 沈青梧" in instruction
    assert "未遂" in instruction
    assert "禁止重复已有内容" in instruction
    assert "不要重新输出 <content>" in instruction


def test_接线_裁定续写消息走专用指令分支():
    wire = [{"role": "user", "content": "继续"}]
    messages = agent_graph_roleplay._verdict_fix_messages(wire, "预填正文", [_claim()])
    assert messages[-2] == {"role": "assistant", "content": "预填正文"}
    instruction = messages[-1]["content"]
    assert "沈青梧" in instruction and "D100=95" in instruction


def test_接线_roleplay_node装配了未遂对账钩子():
    hooks_src = inspect.getsource(agent_graph_roleplay._rp_continuation_hooks)
    assert '"continue_verdict"' in hooks_src
    assert "_verdict_fix_messages(" in hooks_src
    node_src = inspect.getsource(agent_graph_roleplay.roleplay_node)
    assert "_rp_continuation_hooks(" in node_src
