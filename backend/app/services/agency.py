"""剧情能动性纯逻辑：世界提案门控 + 裁判仲裁（0 LLM、0 I/O，全单测）。

设计见 ARCHITECTURE.md「剧情能动性引擎」支柱 2 + 「明确不做的」。核心立场：
**裁判是确定性规则引擎，不是模型**——好感度档位阈值 + 掷骰 + core 一致性驳回。
创造力只留给上层「世界 Agent 生成动作」（LLM），本模块只做可复现的机械仲裁。

依赖方向（importlinter agency-purity 合同将强制）：本模块吃传入的状态**快照**（好感度数值等
标量），**不 import character_state / agent_graph**，因此可独立单测、可被 roleplay 子图安全调用。
掷骰用调用方注入的 random.Random，测试传固定种子即可复现。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

OUTCOME_ACCEPT = "accept"
OUTCOME_REJECT = "reject"
# 六档（对齐正文 <roll> 语汇；accept 的 full/partial 保留供旧调用判"完全得手"）：
DEGREE_CRIT = "crit"        # 大成功（骰=1）
DEGREE_HARD = "hard"        # 极难成功（骰≤技能÷4，最佳常规结果）
DEGREE_FULL = "full"        # 困难成功（骰≤技能÷2）
DEGREE_PARTIAL = "partial"  # 普通成功（骰≤技能，仅达成基本目标）
DEGREE_NONE = "none"        # 失败（技能<骰≤95）
DEGREE_FUMBLE = "fumble"    # 大失败（骰≥96）

# 档位中文措辞（trace/directive 用），键即 degree。
DEGREE_LABEL = {
    DEGREE_CRIT: "大成功", DEGREE_HARD: "极难成功", DEGREE_FULL: "困难成功",
    DEGREE_PARTIAL: "普通成功", DEGREE_NONE: "失败", DEGREE_FUMBLE: "大失败",
}


@dataclass
class Proposal:
    """世界 Agent 对某角色的一次自主行动提案（由 LLM 产出后归一为本结构）。"""
    actor: str            # 角色名
    intent: str           # 想做什么（叙事描述，供后续叙述节点用）
    difficulty: int       # 1–100，越高越难成功
    min_affinity: float   # 低于此好感度该角色根本不会尝试（档位门槛）
    basis: str            # 依据的 core 机制（世界 Agent 必须声明；空=无依据→驳回，防 OOC）
    goal: str = ""        # 当前持续目标；供主叙事保持行动因果，不参与机械裁判


@dataclass
class Verdict:
    """裁判裁决。degree 供叙述节点决定「完全得手 / 半途 / 未遂」。"""
    actor: str
    outcome: str          # accept | reject
    degree: str           # full | partial | none
    roll: int             # d100 点数（0=未掷，被前置门槛拦下）
    chance: int           # 实际成功阈值（供 trace 可观测）
    reason: str
    intent: str = ""      # 原始行动提案，不能在裁判后丢失
    goal: str = ""        # 行动服务的持续目标


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def success_chance(difficulty: int, affinity: float, min_affinity: float) -> int:
    """成功阈值(0–100)：基础 = 100-难度；好感度超出门槛越多加成越高（每 10 点 +5，封顶 +30）。

    纯函数：同输入同输出，便于单测与复现。
    """
    base = 100 - _clamp(difficulty, 1, 100)
    bonus = _clamp((affinity - min_affinity) / 10.0 * 5.0, 0.0, 30.0)
    return int(_clamp(base + bonus, 1, 99))


def classify_roll(roll: int, chance: int) -> tuple[str, str]:
    """D100 骰点分六档，返回 (outcome, degree)。chance = 成功阈值（技能值）。

    照表逐行、命中第一条即结果（对齐正文 <roll> 铁则，骰>技能即失败）：
      骰=1→大成功；骰≤技能÷4→极难；骰≤技能÷2→困难；骰≤技能→普通成功；技能<骰≤95→失败；骰≥96→大失败。
    纯函数，可复现。
    """
    if roll >= 96:
        return OUTCOME_REJECT, DEGREE_FUMBLE
    if roll == 1:
        return OUTCOME_ACCEPT, DEGREE_CRIT
    if roll <= chance // 4:
        return OUTCOME_ACCEPT, DEGREE_HARD
    if roll <= chance // 2:
        return OUTCOME_ACCEPT, DEGREE_FULL
    if roll <= chance:
        return OUTCOME_ACCEPT, DEGREE_PARTIAL
    return OUTCOME_REJECT, DEGREE_NONE


def judge(proposal: Proposal, affinity: float, *, rng: random.Random) -> Verdict:
    """机械仲裁一条提案。顺序：core 依据 → 好感度门槛 → 掷骰分六档。任一前置不过直接驳回（roll=0）。"""
    if not proposal.basis.strip():
        return Verdict(proposal.actor, OUTCOME_REJECT, DEGREE_NONE, 0, 0,
                       "无 core 依据，驳回（防 OOC）", proposal.intent, proposal.goal)
    if affinity < proposal.min_affinity:
        return Verdict(proposal.actor, OUTCOME_REJECT, DEGREE_NONE, 0, 0,
                       f"好感度 {_fmt(affinity)} < 门槛 {_fmt(proposal.min_affinity)}，不会尝试",
                       proposal.intent, proposal.goal)
    chance = success_chance(proposal.difficulty, affinity, proposal.min_affinity)
    roll = rng.randint(1, 100)
    outcome, degree = classify_roll(roll, chance)
    label = DEGREE_LABEL[degree]
    if outcome == OUTCOME_ACCEPT:
        return Verdict(proposal.actor, OUTCOME_ACCEPT, degree, roll, chance,
                       f"掷骰 {roll}/{chance}，{label}", proposal.intent, proposal.goal)
    return Verdict(proposal.actor, OUTCOME_REJECT, degree, roll, chance,
                   f"掷骰 {roll}/{chance}，{label}", proposal.intent, proposal.goal)


def should_consult_world(affinities: dict[str, float], *, rng: random.Random,
                         floor: float, base_rate: float) -> bool:
    """可配置门控：base_rate=1 每轮判断，0 明确关闭，中间值用于用户主动降频。

    逻辑：任一在场角色好感度 ≥ floor 才有资格；越高越可能主动。概率 = base_rate + 超出量/100，
    封顶 1.0。全场无人达 floor → 直接 False（0 LLM）。纯逻辑，rng 注入可复现。
    """
    if base_rate <= 0:
        return False
    eligible = [a for a in affinities.values() if a >= floor]
    if not eligible:
        return False
    top = max(eligible)
    prob = _clamp(base_rate + (top - floor) / 100.0, 0.0, 1.0)
    return rng.random() < prob


def tier_index(value: float, thresholds: list[float]) -> int:
    """好感度落在第几档（thresholds 升序，返回 0..len）。供插画触发判「跨档」用。

    thresholds=[-30,20,55,90,100] → value=-30→1, 25→2, 100→5。纯函数。
    """
    idx = 0
    for t in sorted(thresholds):
        if value >= t:
            idx += 1
        else:
            break
    return idx


def crossed_tier(before: float, after: float, thresholds: list[float]) -> bool:
    """好感度是否跨过一个档位边界（升或降）。scene_illustration 用它当高潮点触发。"""
    return tier_index(before, thresholds) != tier_index(after, thresholds)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)
