"""
角色认知自动抽取：正文维护调用中同步更新角色"知道/相信/怀疑/误解/隐瞒/未知"。

设计：
- 不增加主剧情调用：挂在 _agency_maintenance 的维护周期内（同 cadence）。
- 纯规则启发式（零 LLM 成本）为默认；提供 LLM 结构化抽取作为增强（可选开关）。
- 抽取结果写 character_beliefs.db；失败绝不阻断正文或维护流程。
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

from app.services import character_belief

# ── 认知触发模式 ─────────────────────────────────────────────────────────────
# (stance, 正则)。顺序即优先级：长模式/否定式优先，避免"不知道"被"知道"先匹配。
_STANCE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # 明确不知道（优先于"知道"）
    ("unknown", re.compile(r"(?:不知道|不了解|不清楚|从未听说|没听过|一无所知|并未知晓)", re.IGNORECASE)),
    # 误解/错误相信（优先于"相信/以为"）
    ("misbelieves", re.compile(r"(?:误以为|误解|错认为|误信|被误导|以为…其实)", re.IGNORECASE)),
    # 隐瞒
    ("conceals", re.compile(r"(?:隐瞒|藏着|隐瞒不报|秘而不宣|没说|故意不提|藏在心里|瞒着)", re.IGNORECASE)),
    # 怀疑
    ("suspects", re.compile(r"(?:怀疑|觉得不对劲|起疑|生疑|猜测|猜想|猜疑)", re.IGNORECASE)),
    # 明确知道/了解
    ("knows", re.compile(r"(?:知道|了解|记得|想起|明白|清楚|知晓|听说|被告知|得知)", re.IGNORECASE)),
    # 相信/认为（最后，作为兜底）
    ("believes", re.compile(r"(?:相信|认为|觉得|感觉|坚信|认定)", re.IGNORECASE)),
]

# 事实对象提取：认知动词后的名词短语（中文 2-12 字）
_CLAIM_RE = re.compile(r"(?:不知道|不了解|不清楚|从未听说|没听过|一无所知|知道|了解|记得|想起|明白|清楚|知晓|听说|被告知|得知|相信|认为|觉得|以为|感觉|坚信|怀疑|起疑|生疑|猜测|猜想|误以为|误解|错认为|误信|隐瞒|瞒着|藏着|没说)\s*[，。；]?\s*(?:并|都|也|还|始终|一直|从|压根|根本|完全)?\s*([^，。；！？\n]{2,16})", re.IGNORECASE)

# 主语提取：认知动词前的角色名（简单启发：取句首或"X 知道"）
_SUBJECT_RE = re.compile(r"([\u4e00-\u9fffA-Za-z]{2,6})(?:知道|了解|记得|相信|认为|觉得|以为|怀疑|误以为|误解|隐瞒|不知道|不清楚|从未听说)", re.IGNORECASE)

# 角色名字典缓存（由调用方传入，避免跨作品串扰）
_KNOWN_NAMES: set[str] = set()


def _known_names() -> set[str]:
    return _KNOWN_NAMES


def configure_known_names(names: Iterable[str]) -> None:
    """设置当前作品的角色名集合，用于主语识别。调用方（agent_graph）在抽取前设置。"""
    global _KNOWN_NAMES
    _KNOWN_NAMES = {str(name).strip() for name in names if str(name).strip()}


def _stance_for(sentence: str) -> str | None:
    """判断句子的认知状态。返回 stance 或 None（无认知表达）。"""
    for stance, pattern in _STANCE_PATTERNS:
        if pattern.search(sentence):
            return stance
    return None


def _extract_claims(text: str) -> list[dict]:
    """从正文提取 (character, stance, claim, evidence) 候选。"""
    candidates: list[dict] = []
    sentences = re.split(r"[。！？\n]+", text)
    known = _known_names()
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        stance = _stance_for(sent)
        if not stance:
            continue
        # 提取主语：按句中出现位置最早的已知角色名（保证确定性）
        subject = ""
        positions = []
        for name in known:
            if name:
                pos = sent.find(name)
                if 0 <= pos <= 40:
                    positions.append((pos, name))
        if positions:
            positions.sort(key=lambda pair: pair[0])
            subject = positions[0][1]
        if not subject:
            m = _SUBJECT_RE.search(sent[:60])
            if m:
                subject = m.group(1)
        if not subject:
            continue
        # 提取 claim：认知动词后的内容
        claims = _CLAIM_RE.findall(sent)
        if not claims:
            # 兜底：取整句
            claims = [sent[:16]]
        for claim in claims:
            claim = claim.strip().strip("，。！？")
            if not claim or len(claim) < 2:
                continue
            candidates.append({
                "character": subject,
                "stance": stance,
                "claim": claim,
                "evidence": sent[:120],
            })
    return candidates


def extract(text: str, *, known_names: Iterable[str] = ()) -> list[dict]:
    """纯规则抽取：返回候选认知（未落库）。"""
    configure_known_names(known_names)
    return _extract_claims(text)


def _fact_id_for(repo_id: str, claim: str) -> str:
    """为 claim 生成稳定的 fact_id（无对应世界事实时也允许引用）。"""
    raw = f"{repo_id}|belief|{claim}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ingest(base: str, repo_id: str, *, text: str, turn: int,
           known_names: Iterable[str] = (), source: str = "auto",
           max_items: int = 8) -> dict:
    """抽取并落库角色认知。

    返回 {"extracted": n, "recorded": m, "skipped": k, "errors": [...]}。
    幂等：同 turn 同 character 同 claim 同 stance 不重复写。
    """
    configure_known_names(known_names)
    candidates = _extract_claims(text)
    candidates = candidates[:max_items]
    recorded = 0
    skipped = 0
    errors: list[str] = []
    for cand in candidates:
        try:
            fact_id = _fact_id_for(repo_id, cand["claim"])
            # 幂等检查：同回合同角色同 claim 已存在则跳过
            existing = character_belief.active(
                base, repo_id, turn, characters=[cand["character"]],
            )
            already = any(
                item.get("claim") == cand["claim"] and item.get("stance") == cand["stance"]
                for item in existing
            )
            if already:
                skipped += 1
                continue
            confidence = _confidence_for(cand["stance"])
            character_belief.record(
                base, repo_id,
                character=cand["character"],
                fact_id=fact_id,
                claim=cand["claim"],
                stance=cand["stance"],
                confidence=confidence,
                witnessed_at=turn,
                evidence=cand["evidence"],
                source=source,
            )
            recorded += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    return {"extracted": len(candidates), "recorded": recorded,
            "skipped": skipped, "errors": errors}


def _confidence_for(stance: str) -> float:
    """按 stance 给默认确信度（可被显式 feedback 覆盖）。"""
    return {
        "knows": 0.95, "believes": 0.75, "suspects": 0.5,
        "misbelieves": 0.65, "conceals": 0.85, "unknown": 0.9,
    }.get(stance, 0.5)
