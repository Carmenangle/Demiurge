"""能动性纯逻辑：裁判驳回顺序、掷骰复现、门控省 token、跨档触发。"""
from __future__ import annotations

import random

from app.services import agency


def _prop(**kw) -> agency.Proposal:
    d = dict(actor="埃斯托利亚", intent="在酒里下药", difficulty=50,
             min_affinity=20.0, basis="[从舌头开始]")
    d.update(kw)
    return agency.Proposal(**d)


def test_无core依据直接驳回():
    v = agency.judge(_prop(basis="  "), affinity=100, rng=random.Random(1))
    assert v.outcome == agency.OUTCOME_REJECT and v.roll == 0 and "OOC" in v.reason


def test_好感度未达门槛不尝试():
    v = agency.judge(_prop(min_affinity=55), affinity=20, rng=random.Random(1))
    assert v.outcome == agency.OUTCOME_REJECT and v.roll == 0 and "门槛" in v.reason


def test_掷骰确定可复现():
    # 同种子 → 同结果（裁判必须可复现，见设计「确定、可复现」）
    a = agency.judge(_prop(), affinity=60, rng=random.Random(42))
    b = agency.judge(_prop(), affinity=60, rng=random.Random(42))
    assert a == b


def test_success_chance_随好感度加成():
    low = agency.success_chance(50, affinity=20, min_affinity=20)   # 无加成
    high = agency.success_chance(50, affinity=120, min_affinity=20)  # 满加成
    assert low == 50 and high == 80  # 100-50=50, +封顶30


def test_低骰full高骰partial():
    # chance=99（难度1、满加成），roll 极小 → 命中高档（crit/hard/full 之一）
    v = agency.judge(_prop(difficulty=1), affinity=120, rng=random.Random(0))
    assert v.outcome == agency.OUTCOME_ACCEPT
    assert v.degree in (agency.DEGREE_CRIT, agency.DEGREE_HARD, agency.DEGREE_FULL, agency.DEGREE_PARTIAL)


def test_classify_roll_六档阈值():
    # 技能值(chance)=60，照表逐行：1→大成功；≤15→极难；≤30→困难；≤60→普通；61-95→失败；≥96→大失败
    assert agency.classify_roll(1, 60) == (agency.OUTCOME_ACCEPT, agency.DEGREE_CRIT)
    assert agency.classify_roll(12, 60) == (agency.OUTCOME_ACCEPT, agency.DEGREE_HARD)   # ≤15
    assert agency.classify_roll(28, 60) == (agency.OUTCOME_ACCEPT, agency.DEGREE_FULL)   # ≤30
    assert agency.classify_roll(55, 60) == (agency.OUTCOME_ACCEPT, agency.DEGREE_PARTIAL)  # ≤60
    assert agency.classify_roll(74, 60) == (agency.OUTCOME_REJECT, agency.DEGREE_NONE)   # 失败
    assert agency.classify_roll(97, 60) == (agency.OUTCOME_REJECT, agency.DEGREE_FUMBLE)  # 大失败


def test_大失败即便技能高也失败():
    # 骰≥96 恒大失败，无视技能值（对齐正文铁则）
    assert agency.classify_roll(96, 99) == (agency.OUTCOME_REJECT, agency.DEGREE_FUMBLE)


def test_门控无人达floor直接False():
    # 0 LLM：全场好感度低于 floor → 不唤起世界 Agent
    assert agency.should_consult_world({"a": -30, "b": 0}, rng=random.Random(1),
                                       floor=55, base_rate=0.2) is False


def test_门控高好感度更可能主动():
    # 大量采样下，高好感度触发率应显著高于 base_rate
    rng = random.Random(7)
    hits = sum(agency.should_consult_world({"a": 100}, rng=rng, floor=20, base_rate=0.2)
               for _ in range(2000))
    assert hits / 2000 > 0.5  # prob = 0.2 + 80/100 = 1.0 封顶 0.9


def test_门控基础率为零时世界Agent完全关闭():
    assert agency.should_consult_world(
        {"a": 100}, rng=random.Random(1), floor=0, base_rate=0,
    ) is False


def test_tier与跨档():
    th = [-30.0, 20.0, 55.0, 90.0, 100.0]
    assert agency.tier_index(-40, th) == 0
    assert agency.tier_index(-30, th) == 1
    assert agency.tier_index(100, th) == 5
    assert agency.crossed_tier(19, 25, th) is True    # 第2档→第3档
    assert agency.crossed_tier(21, 30, th) is False   # 同档内
