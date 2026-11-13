"""
Narrative CI：内容中立、非阻断的剧情一致性诊断与处置记录。
扩展版本：增加时间/空间/关系/认知越权/世界规则诊断。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from typing import Any, Iterable

from app.services import prose_style, repo_meta

_LOG = logging.getLogger("uvicorn.error")


DB_NAME = "narrative_ci.db"
RESOLUTIONS = {"open", "fixed", "foreshadow", "retcon", "accepted"}

# ─── 诊断代码常量 ────────────────────────────────────────────────────────────
CODE_ACTIVE_CONFLICT     = "active_fact_conflict"
CODE_CONTRADICTION       = "fact_contradiction"
CODE_RELATIONSHIP_JUMP   = "relationship_jump"
CODE_LOCATION_NO_TRANSIT  = "location_without_transition"
CODE_KNOWLEDGE_OVERREACH = "knowledge_overreach"
# 新增
CODE_TEMPORAL_PARADOX    = "temporal_paradox"       # 时间顺序矛盾
CODE_SPATIAL_INCONSIST   = "spatial_inconsistency"  # 空间位置矛盾
CODE_RELATION_CHANGE     = "relationship_change"     # 关系单轮剧变
CODE_WORLD_RULE_BREAK    = "world_rule_break"       # 违反世界规则/设定
CODE_BELIEF_CONFLICT     = "character_belief_conflict"  # 角色认知与事实矛盾
CODE_NPC_VERDICT_BREAK   = "npc_verdict_break"      # 判失败的自主尝试没写成未遂/受挫
# 文风诊断码属主是 prose_style（词表单一属主），此处并流复用
CODE_STYLE_BANNED_PHRASE = prose_style.CODE_STYLE_BANNED_PHRASE
CODE_STYLE_PUNCT_DENSITY = prose_style.CODE_STYLE_PUNCT_DENSITY
CODE_STYLE_RHYTHM_METRONOME = prose_style.CODE_STYLE_RHYTHM_METRONOME
CODE_STYLE_SELF_QA = prose_style.CODE_STYLE_SELF_QA
CODE_STYLE_PATTERN_REPEAT = prose_style.CODE_STYLE_PATTERN_REPEAT
CODE_STYLE_OPENING_CUE = prose_style.CODE_STYLE_OPENING_CUE
CODE_STYLE_TERM_RATCHET = prose_style.CODE_STYLE_TERM_RATCHET


def diagnostic_entry(turn: int, code: str, message: str, evidence: str,
                     source: str, severity: str = "warning") -> dict:
    raw = f"{turn}|{code}|{message}|{evidence}|{source}"
    return {
        "id": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "turn": turn, "code": code, "severity": severity,
        "message": message, "evidence": evidence, "source": source,
        "status": "open",
    }


# ─── 臂参与台账（清单 #3／#4：没书所以不判 ⇒ 报告要写明「这一臂本轮没参与」）────

ARMS_META_KEY = "arms"
ARMS_WORLD_RULE = "world_rule"
ARMS_RATCHET_STOCK = "term_ratchet_stock"
ARMS_AGENCY_VERDICT = "agency_verdict"

NO_WORLDBOOK = "no_worldbook"
EMPTY_WORLDBOOK = "empty_worldbook"
SNAPSHOT_UNREADABLE = "snapshot_unreadable"
NO_RULE_LINES = "no_rule_lines"
NO_STATIC_BASE = "no_static_base"
# 第三条臂（缺陷 4）的三种「本轮没有可核对的裁定」，与**世界书无关**，不能套上面的阶梯
WORLD_NOT_CONSULTED = "world_not_consulted"
NO_PROPOSAL = "no_proposal"
NO_ROLLED_ATTEMPT = "no_rolled_attempt"


def arm_participation(*, world_rules: Iterable[str] = (),
                      stock_sources: Iterable[str] = (),
                      has_worldbook: bool = False,
                      worldbook_entries: int = 0,
                      readable: bool = True,
                      agency_claims: list | None = None) -> dict[str, dict]:
    """三条「靠上游喂输入」的诊断臂本轮到底有没有被评估——**只观测，不改判据**。

    `CODE_WORLD_RULE_BREAK` 是 `for rule in world_rules:`，空列表 ⇒ 循环一次不跑；
    术语棘轮的存量闸门是 `if set_texts:`，空 ⇒ 整道闸关闭。两种情形下 diagnostics 里
    **一行都不会多**，与「查过了、没查出问题」在存储层逐字同形 ⇒ 绿是空转出来的。
    这里把「没参与」与原因显式算出来，交给 trace 与面板；判定语句本身一个字不动。

    `has_worldbook`＝快照文件在册，`readable`＝它真被读成了 dict。两者都要传：读失败时
    说「这个作品没有世界书」同样是句假话，而「说错原因」正是这一刀要消灭的东西。

    `agency_claims`＝本轮 `agency.judge` 产出的裁定投影（`_agency_propose` 写入），
    三种形状对应三句话：`None`＝这条链根本没跑出裁定（deps 缺失／World 抛错／非剧情链路，
    究竟哪种由 `agent.skipped`/`agent.error`  trace 说）；`[]`＝问了 World 但一条提案也没有；
    全 `roll==0`＝有提案但都被前置门槛拦下，`narrative_directive` 对它们**一个字都不写进
    正文**，于是「没有要求」与「要求了没核对」在面板上分开。计数口径与 directive 同为
    `roll > 0`（由 `test_臂台账_裁定计数与叙事指令同源` 锁住），这条臂不读世界书阶梯。
    """
    rules = [str(rule).strip() for rule in world_rules if str(rule).strip()]
    stock = [str(text).strip() for text in stock_sources if str(text).strip()]

    def idle(arm_specific: str) -> str:
        if not has_worldbook:
            return NO_WORLDBOOK
        if not readable:
            return SNAPSHOT_UNREADABLE
        if worldbook_entries <= 0:
            return EMPTY_WORLDBOOK
        return arm_specific

    def arm(count: int, arm_specific: str) -> dict:
        return {"participated": count > 0, "count": count,
                "reason": "" if count > 0 else idle(arm_specific)}

    def verdict_arm() -> dict:
        if agency_claims is None:
            return {"participated": False, "count": 0, "reason": WORLD_NOT_CONSULTED}
        rolled = [v for v in agency_claims if int(v.get("roll") or 0) > 0]
        reason = "" if rolled else (NO_PROPOSAL if not agency_claims else NO_ROLLED_ATTEMPT)
        return {"participated": bool(rolled), "count": len(rolled), "reason": reason}

    return {
        ARMS_WORLD_RULE: arm(len(rules), NO_RULE_LINES),
        ARMS_RATCHET_STOCK: arm(len(stock), NO_STATIC_BASE),
        ARMS_AGENCY_VERDICT: verdict_arm(),
    }


# ─── 时间/空间模式 ───────────────────────────────────────────────────────────
_TIME_ORDER = re.compile(
    r"(?:之前?|以后?|后来?|先前?|随后?|接着|于是|那么|结果|最终|终于|最后|起初|当初)",
    re.IGNORECASE,
)
_SPACIAL_TRANSITION = re.compile(
    r"(?:走向|来到|回到|抵达|到达|穿越|穿过|离开|进入|爬上|跃入|冲进|走进|回到|返回|起身|跳下|掉进|坠落|跃起|起身|站起|跪下|倒下)",
    re.IGNORECASE,
)
_LOCATION_TOKENS = re.compile(
    r"[\u4e00-\u9fff]{1,8}(?:里|中|外|内|上|下|前|后|旁|侧|顶|底|边)?",
)
# 关系动词（好感度/敌意等变化）
_RELATION_VERBS = re.compile(
    r"(?:喜欢|讨厌|憎恨|信任|怀疑|依赖|服从|反抗|亲吻|拥抱|牵手|拥抱|攻击|拯救|背叛|欺骗|利用|原谅|怨恨|羡慕|嫉妒|怜悯|愤怒|恐惧|惊讶|感动)",
    re.IGNORECASE,
)


def evaluate(text: str, *, turn: int, facts: Iterable[dict] = (),
             raw_deltas: Iterable[dict] = (), beliefs: Iterable[dict] = (),
             world_rules: Iterable[str] = (),
             recent_openings: Iterable[str] = (),
             style_config: dict | None = None,
             style_injected: Iterable[str] = (),
             style_bodies: Iterable[str] = (),
             style_exclude: Iterable[str] = (),
             style_setting: Iterable[str] = (),
             agency_claims: Iterable[Any] = (),
             scene_snapshot: str = "") -> list[dict]:
    """只返回带证据诊断；不分类内容、不阻断、不重写正文。

    新增参数：
        world_rules：迭代器，每项为一条世界设定/规则文本（如"药王谷的药材不可带出谷外"）。
        recent_openings：最近数层 assistant 楼层的开头文本（供跨轮开场趋同检测）。
        style_config：prose_style.load_config() 的结果；None=内置默认，
            enabled=False 时跳过全部文风检测（文风功能总开关）。
        style_injected / style_bodies：跨来源术语棘轮（CODE_STYLE_TERM_RATCHET）的两组输入，
            前者＝注入层（数据表块、世界书召回/动态段），后者＝最近数轮正文。
            **两组都要非空才会出该码**（默认空 ⇒ 与改造前同行为）。
        style_exclude：上述检测要豁免的合法高频词（角色名/称呼等）。默认空。
        style_setting：设定存量来源（世界书设定条目 + 角色卡静态底座）。传了就按
            「用量/存量」重排并把两个数打进 message，用来把「设定支撑的高频」
            （如 霜月宫）与「无锚点膨胀」（如 经脉）分开。默认空 ⇒ message 逐字不变。
        agency_claims：本轮 `agency.judge` 的裁定投影（`ctx["_agency_npc_claims"]`）。
            非空时多一道「未遂对账」臂（`CODE_NPC_VERDICT_BREAK`，只出 warning）。
            默认空 ⇒ 与改造前逐字同行为（手动 CI 端点不带裁定，也就不出这码）。
        scene_snapshot：本轮状态栏快照文本，仅供未遂对账的在场闸读 `[在场]`。默认空。
    """
    body = text or ""
    diagnostics: list[dict] = []
    fact_list = list(facts)

    # ── 1. 同一 subject/predicate 多有效事实 ─────────────────────────────────
    grouped: dict[tuple[str, str], list[dict]] = {}
    for fact in fact_list:
        grouped.setdefault((str(fact.get("subject") or ""),
                            str(fact.get("predicate") or "")), []).append(fact)
    for (subject, predicate), items in grouped.items():
        objects = {str(item.get("object") or "") for item in items}
        if subject and predicate and len(objects) > 1:
            diagnostics.append(diagnostic_entry(
                turn, CODE_ACTIVE_CONFLICT,
                f"{subject} 的「{predicate}」同时存在多个有效事实。",
                "；".join(sorted(objects)), "temporal_fact_store", "error",
            ))

    # ── 2. 正文否定有效事实 ──────────────────────────────────────────────────
    for fact in fact_list:
        subject = str(fact.get("subject") or "").strip()
        object_ = str(fact.get("object") or "").strip()
        if subject and object_ and (
            f"{subject}不是{object_}" in body
            or f"{subject}并非{object_}" in body
            or f"{subject}不在{object_}" in body
            or f"并非{subject}" in body and object_ in body
        ):
            diagnostics.append(diagnostic_entry(
                turn, CODE_CONTRADICTION,
                f"正文可能否定当前有效事实：{subject}／{object_}。",
                str(fact.get("evidence") or object_), "temporal_fact_store", "error",
            ))

    # ── 3. 数值字段单轮剧变（原有）──────────────────────────────────────────
    for delta in raw_deltas:
        if not isinstance(delta, dict):
            continue
        field = str(delta.get("field") or "")
        evidence_str = str(delta.get("evidence") or "").strip()
        value = delta.get("value")
        if field.startswith("数值/"):
            try:
                jump = abs(float(value)) if isinstance(value, (str, int, float)) else 0
            except (TypeError, ValueError):
                jump = 0
            if jump > 30:
                diagnostics.append(diagnostic_entry(
                    turn, CODE_RELATIONSHIP_JUMP,
                    f"{field} 单回合变化 {value}，需要明确剧情依据。",
                    evidence_str or "状态更新未提供证据", "character_state",
                ))
        if field.endswith("所在") and not evidence_str:
            diagnostics.append(diagnostic_entry(
                turn, CODE_LOCATION_NO_TRANSIT,
                f"{field} 已改变但没有过渡证据。",
                str(value or ""), "character_state",
            ))

    # ── 4. 角色认知越权（原有）───────────────────────────────────────────────
    known = {(str(item.get("character") or ""), str(item.get("fact_id") or ""))
             for item in beliefs if str(item.get("stance") or "") == "knows"}
    for item in beliefs:
        character = str(item.get("character") or "").strip()
        fact_id = str(item.get("fact_id") or "").strip()
        if str(item.get("stance") or "") == "unknown" and (character, fact_id) not in known:
            claim = str(item.get("claim") or "").strip()
            if character and claim and character in body and claim in body:
                diagnostics.append(diagnostic_entry(
                    turn, CODE_KNOWLEDGE_OVERREACH,
                    f"{character} 在正文中使用了尚未知晓的事实。",
                    claim, "character_belief", "error",
                ))

    # ── 5. 角色认知与正文事实矛盾（新增）─────────────────────────────────────
    for item in beliefs:
        stance = str(item.get("stance") or "")
        if stance not in ("knows", "believes", "suspects", "misbelieves"):
            continue
        character = str(item.get("character") or "").strip()
        claim = str(item.get("claim") or "").strip()
        if not character or not claim:
            continue
        # 如果角色"相信 X"或"知道 X"，但正文明确否定了该事实
        if stance in ("believes", "knows"):
            neg_words = ["并未", "并没有", "不是", "并不", "没有", "不再", "否认",
                         "推翻", "违背", "违反", "破坏了", "打破了", "并不认为"]
            cores = _core_nouns(claim)
            for core in cores:
                if core in body:
                    idx_c = body.index(core)
                    window = body[max(0, idx_c - 60): idx_c + 60]
                    if any(w in window for w in neg_words):
                        diagnostics.append(diagnostic_entry(
                            turn, CODE_BELIEF_CONFLICT,
                            f"{character} 相信「{claim}」，但正文否定了这一认知。",
                            f"stance={stance}，claim={claim}", "character_belief", "warning",
                        ))
                        break

    # ── 6. 时间顺序矛盾（新增）：正文出现反向时间词 ─────────────────────────
    # 简单启发：同一句/段内出现"然后回到"或同一角色先"结果"再"于是"等多步冲突
    temporal_conflicts = _detect_temporal_paradox(body)
    for msg, evd in temporal_conflicts:
        diagnostics.append(diagnostic_entry(
            turn, CODE_TEMPORAL_PARADOX, msg, evd, "narrative_ci", "warning",
        ))

    # ── 7. 空间位置矛盾（新增）：角色出现在两个不相连空间但无过渡 ───────────
    spatial_conflicts = _detect_spatial_inconsistency(body)
    for msg, evd in spatial_conflicts:
        diagnostics.append(diagnostic_entry(
            turn, CODE_SPATIAL_INCONSIST, msg, evd, "narrative_ci", "warning",
        ))

    # ── 8. 关系单轮剧变（新增）───────────────────────────────────────────────
    relation_changes = _detect_relation_change(body)
    for msg, evd in relation_changes:
        diagnostics.append(diagnostic_entry(
            turn, CODE_RELATION_CHANGE, msg, evd, "narrative_ci", "warning",
        ))

    # ── 9. 违反世界规则（新增）───────────────────────────────────────────────
    for rule in world_rules:
        rule_text = str(rule).strip()
        if not rule_text:
            continue
        violated = _check_world_rule_violation(body, rule_text)
        if violated:
            diagnostics.append(diagnostic_entry(
                turn, CODE_WORLD_RULE_BREAK,
                f"正文可能违反世界设定：{rule_text}",
                violated, "worldbook", "warning",
            ))

    # ── 10. 文风 lint（去 AI 味，prose_style 属主并流；只检测不涂改）──────────
    if style_config is None or style_config.get("enabled", True):
        try:
            phrases = (None if style_config is None
                       else prose_style.effective_phrases(style_config))
            for item in prose_style.lint(body, recent_openings=list(recent_openings),
                                         banned_phrases=phrases,
                                         ratchet_injected=tuple(style_injected or ()),
                                         ratchet_bodies=tuple(style_bodies or ()),
                                         ratchet_exclude=tuple(style_exclude or ()),
                                         ratchet_setting=tuple(style_setting or ())):
                diagnostics.append(diagnostic_entry(
                    turn, item["code"], item["message"], item["evidence"],
                    "prose_style", item.get("severity", "warning"),
                ))
        except Exception as exc:  # noqa: BLE001 [EXC-留痕] 文风检测失败不阻断诊断流（本轮少 prose_style 诊断）
            _LOG.warning("文风检测失败（本轮少 prose_style 诊断）: %s", exc)

    # ── 11. 未遂对账（缺陷 4 第二段：裁判判失败 → 指令要求未遂 → 回看正文有没有照做）──
    diagnostics.extend(check_verdict_attempts(
        body, agency_claims, turn=turn, scene_snapshot=scene_snapshot,
    ))

    unique = {item["id"]: item for item in diagnostics}
    return list(unique.values())


# ─── 未遂对账（词表档，2026-11-06 S3 真语料定档）───────────────────────────────
#
# 稳定合同（`docs/memory/npc-agency-goals.md` 第 5 条）：过了 core/好感前置但掷骰失败的尝试
# **必须写成未遂或受挫**。`narrative_directive` 每轮都把这句话打进主叙事指令，但过去没有
# 任何一道闸回看正文 ⇒ 「指令有没有被听见」只能人肉盯。本档是**提示级**诊断（warning、
# 不阻断、不改写），不是定罪：实测精确率 82%／召回率 100%（16 条已定标签语料：
# 9 真违规／2 假阳／0 漏报），剩下的假阳全部来自「合规但正文一个失败词都没用」的写法
# （靠对白让步、动作收回演出拒绝），那是词表档的**地板**而不是没调好——唯一压低它的
# 办法是加词，而加词按实测会洗掉真违规（`退回来` 一词就洗掉 1 条，占真违规 11%）。
VERDICT_ATTEMPT_MARKERS = (
    "未遂", "受挫", "没能", "未能", "没等到", "没等来", "没做成", "被驳回", "驳回",
    "搁置", "落空", "告吹", "被拒", "无从对证", "作罢", "未成", "咽回去",
)
# 实测**不收**的词及原因（改本表前先跑 `docs/memory/npc-agency-goals.md` 里登记的定档口径）：
# 失败／退开／只好／被迫／无从——前两个只出现在 `<roll>` 块与泛指句（「计划失败」「他退开」
# 与本轮裁定无关），后三个是让步连词，命中即把真违规洗成合规。


def verdict_attempt_claims(claims: Iterable[Any]) -> list[dict]:
    """本轮被要求「写成未遂或受挫」的裁定人群。

    判据必须与 `narrative_directive` 的 NPC 段**逐字同源**（`kind == "npc"`、`roll > 0`、
    判失败、`intent` 非空）——由 `test_未遂对账_人群与叙事指令的未遂句逐字同源` 在
    kind × outcome × roll 网格上钉住。event 段写的是「只以预兆/余波出现」、user_action 段写的是
    「禁止写成变相成功」，两者都不是失败措辞能核对的，故本档一律不判。
    """
    from app.services import agency

    out: list[dict] = []
    for claim in claims or ():
        if not isinstance(claim, dict):
            continue
        if (str(claim.get("kind") or "") == "npc"
                and int(claim.get("roll") or 0) > 0
                and str(claim.get("outcome") or "") == agency.OUTCOME_REJECT
                and str(claim.get("intent") or "").strip()):
            out.append(claim)
    return out


def _actor_excerpt(prose: str, actor: str) -> str:
    """取正文里该角色的第一个含名句做证据（逐字，供面板核对，不做摘要）。"""
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；\n])", prose) if s.strip()]
    for sentence in sentences:
        if actor in sentence:
            return sentence[:120]
    return prose[:120].strip()


def violating_verdict_claims(text: str, claims: Iterable[Any], *,
                             scene_snapshot: str = "") -> list[dict]:
    """判失败的 NPC 尝试在正文里没有落成未遂/受挫 ⇒ 返回这些裁定（单一判据）。

    只报告臂（`check_verdict_attempts`）与回合闸（`roleplay_turn` 的生成侧修正）共用本函数：
    「报的红」与「修的东西」必须逐字同源，否则两侧会静默漂移。
    两个口径都来自实测，不是随手定的：
    - **命中面 = `story_history.prose_only(text)`**（清洗属主，`prompt_clean.CONTEXT_SLIM_TAGS`
      剥净 `<roll>/<status>/<fate>` 等）。实测与「全 `<content>` 块拼接」分类零差异；
      而**含 `<roll>` 的原文面**会把骰点块自写的「失败／驳回」当成正文照做 ⇒ 系统性洗白。
    - **在场闸 = 角色全名出现在正文或状态栏快照**。实测 18 条里 4 条真违规的角色名
      只在 `[在场]` 行、不在正文；只查正文会让召回从 100% 掉到 56%。反过来**不做节译别名**
      （分段／去衔称／取前两字）：别名口径把 1 条合规推成假阳（82%→75%），宁可少指控一条。
    """
    from app.services import story_history

    pending = verdict_attempt_claims(claims)
    if not pending:
        return []
    prose = story_history.prose_only(text or "")
    surface = f"{prose}\n{scene_snapshot or ''}"
    hits: list[dict] = []
    for claim in pending:
        actor = str(claim.get("actor") or "").strip()
        if not actor or actor not in surface:
            continue
        if any(marker in prose for marker in VERDICT_ATTEMPT_MARKERS):
            continue
        hits.append(claim)
    return hits


def check_verdict_attempts(text: str, claims: Iterable[Any], *, turn: int,
                           scene_snapshot: str = "") -> list[dict]:
    """判失败的 NPC 尝试在正文里没有落成未遂/受挫措辞 ⇒ 出一条 warning（只报告）。"""
    from app.services import agency, story_history

    hits = violating_verdict_claims(text, claims, scene_snapshot=scene_snapshot)
    if not hits:
        return []
    prose = story_history.prose_only(text or "")
    items: list[dict] = []
    for claim in hits:
        actor = str(claim.get("actor") or "").strip()
        intent = str(claim.get("intent") or "").strip()
        degree = str(claim.get("degree") or "")
        label = agency.DEGREE_LABEL.get(degree, degree or "失败")
        roll = int(claim.get("roll") or 0)
        chance = int(claim.get("chance") or 0)
        items.append(diagnostic_entry(
            turn, CODE_NPC_VERDICT_BREAK,
            f"World 已裁 {actor} 的「{intent}」为{label}（D100={roll}／阈值 {chance}），"
            "但正文没按指令把它写成未遂或受挫。",
            _actor_excerpt(prose, actor), "agency_verdict", "warning",
        ))
    return items


# ─── 新增辅助检测函数 ────────────────────────────────────────────────────────

def _detect_temporal_paradox(text: str) -> list[tuple[str, str]]:
    """检测时间顺序矛盾。启发：同一段中先说'然后/于是/结果'，又出现'起初/当初/之前'等回溯词。"""
    issues: list[tuple[str, str]] = []
    paragraphs = text.split("\n")
    for para in paragraphs:
        if not para.strip():
            continue
        has_result = bool(_TIME_ORDER.search(para))
        has_backtrack = bool(re.search(r"(?:起初|当初|此前|在此之前|在此之前|回忆|想起|追溯)", para))
        # 简单矛盾：段落同时包含"结果/于是"和"起初/当初"
        if has_result and has_backtrack:
            issues.append(
                ("段落同时出现结果导向词与回溯词，时间顺序可能矛盾。",
                 f"段落摘要：{para[:80]}...")
            )
    return issues


def _detect_spatial_inconsistency(text: str) -> list[tuple[str, str]]:
    """检测空间位置矛盾：角色出现在多个互斥位置但无过渡证据。"""
    issues: list[tuple[str, str]] = []
    LOC_WORDS = re.compile(
        r"(?:寝殿|牢房|长廊|山门|药房|药架|内殿|大殿|后山|谷外|谷内|密室|禁地|"
        r"森林|湖泊|河流|悬崖|殿外|殿内|门外|门内|院中|院外|室内|室外|楼阁|塔顶)",
        re.IGNORECASE,
    )
    sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if s.strip()]
    location_sentences: list[tuple[str, str]] = []  # (sentence, location)
    for sent in sentences:
        locs = LOC_WORDS.findall(sent)
        if locs:
            location_sentences.append((sent, locs[0]))
    # 检测"回到/返回"空间回溯：前文有不同地点且无过渡词
    for i, (sent, loc) in enumerate(location_sentences):
        if "回到" in sent or "返回" in sent or "退到" in sent:
            for j in range(i - 1, -1, -1):
                prev_loc = location_sentences[j][1]
                if prev_loc != loc:
                    has_transition = any(
                        w in sent for w in ("穿过", "沿着", "经过", "走出", "走回", "绕", "跋涉", "赶路", "行走")
                    )
                    denies_transition = any(
                        w in sent for w in ("没有穿过", "没穿过", "未穿过", "没有经过", "未经过", "没有走", "没走")
                    )
                    if not has_transition or denies_transition:
                        issues.append(
                            (f"角色从「{prev_loc}」回到「{loc}」但缺少过渡路径。",
                             f"「{loc}」所在句：{sent[:80]}")
                        )
                    break
    return issues


def _detect_relation_change(text: str) -> list[tuple[str, str]]:
    """检测关系单轮剧变：角色间情感/关系突然转变无充分铺垫。"""
    issues: list[tuple[str, str]] = []
    paragraphs = text.split("\n")
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        matches = list(_RELATION_VERBS.finditer(para))
        if len(matches) >= 2:
            # 同一段内出现 2+ 关系动词
            verbs = [m.group() for m in matches]
            # 检测极端对立：喜欢↔憎恨、拥抱↔攻击 等
            hostile = {"憎恨", "攻击", "背叛", "欺骗"}
            friendly = {"喜欢", "亲吻", "拥抱", "牵手", "信任"}
            v_set = set(verbs)
            if v_set & hostile and v_set & friendly:
                issues.append(
                    ("同一段落内出现友好与敌对关系词并置，可能缺乏情感铺垫。",
                     f"关系词：{'/'.join(verbs)}；段落：{para[:80]}...")
                )
    return issues




def _core_nouns(phrase: str) -> list[str]:
    """从约束/claim 中提取 2-4 字候选核心片段（中文名词通常 2-4 字）。"""
    phrase = (phrase or "").strip().strip("。，,！？")
    if not phrase:
        return []
    # 过滤掉情态/动作/虚词
    stop_words = {"不可", "不得", "必须", "应当", "禁止", "严禁", "务必", "绝不",
                  "永远", "不要", "只有", "唯一", "带出", "带离", "带走", "拿出",
                  "取出", "离开", "进入", "触碰", "打开", "使用", "说出", "告诉",
                  "透露", "展示", "携带", "私自", "把", "将", "要", "会", "能",
                  "可以", "应该", "就是", "还是", "以及", "并且"}
    candidates = []
    for length in (4, 3, 2):
        for i in range(len(phrase) - length + 1):
            piece = phrase[i:i + length]
            if piece in stop_words or piece in candidates:
                continue
            # 排除含动作前缀的片段（如"带出谷外"→取"谷外"）
            if any(piece.startswith(w) for w in ("带出", "带离", "带走", "拿出", "取出", "离开")):
                continue
            candidates.append(piece)
    # 按长度降序（最具体的优先）
    candidates.sort(key=len, reverse=True)
    return candidates


_PROHIBITION_CONSTRAINT_RES = (
    re.compile(r"不可([^。，,！？]+)"),
    re.compile(r"禁止([^。，,！？]+)"),
    re.compile(r"不得([^。，,！？]+)"),
)
# 正向义务（必须/应当/务必）的「没做到」无法用词窗可靠判定；旧实现把它当禁令核对，
# 正文照做反而被指控（2026-11-06 D1 实测）。本臂宁可漏，不可诬告。
_POSITIVE_OBLIGATION_RE = re.compile(r"必须[^。，,！？]+|应当[^。，,！？]+|务必[^。，,！？]+")


def _rule_constraints(rule: str) -> list[str]:
    """规则里全部可判定的禁止类约束；正向义务⇒空表（有意不判）；无约束词才退回整句前 20 字。"""
    constraints = [
        match.group(1).strip()
        for pattern in _PROHIBITION_CONSTRAINT_RES
        for match in pattern.finditer(rule)
        if match.group(1).strip()
    ]
    if constraints:
        return constraints
    if _POSITIVE_OBLIGATION_RE.search(rule):
        return []
    return [rule[:20].strip()]


def _check_world_rule_violation(text: str, rule: str) -> str:
    """检测正文是否违反单条世界规则。返回空字符串表示未违反或无法判定，否则返回矛盾证据。"""
    rule = rule.strip()
    if not rule or len(rule) < 4:
        return ""

    violation_hints = ["偏要", "竟然", "依然", "坚持", "故意", "违反", "无视",
                       "不管不顾", "还是", "仍", "却", "偷偷", "瞒着", "胆敢"]
    action_verbs = ["带出", "离开", "带走", "偷走", "取走", "搬走", "带离", "拿出",
                    "携带", "带着", "闯", "私闯", "破坏", "违背", "打破了"]
    for constraint in _rule_constraints(rule):
        # 找正文中出现的候选核心片段
        for core in _core_nouns(constraint):
            if core in text:
                idx_c = text.index(core)
                window = text[max(0, idx_c - 50): idx_c + 60]
                if any(h in window for h in violation_hints) or any(v in window for v in action_verbs):
                    for v in action_verbs:
                        if v in window:
                            return f"规则禁止「{constraint}」（核心「{core}」），但正文出现「{v}」动作"
                    return f"规则禁止「{constraint}」（核心「{core}」），且正文含违规倾向词"
    return ""

def _connect(base: str, repo_id: str) -> sqlite3.Connection:
    path = repo_meta.engine_repo_dir(base, repo_id) / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS diagnostics ("
        "id TEXT PRIMARY KEY, turn INTEGER NOT NULL, code TEXT NOT NULL, "
        "severity TEXT NOT NULL, message TEXT NOT NULL, evidence TEXT NOT NULL, "
        "source TEXT NOT NULL, status TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


def save(base: str, repo_id: str, diagnostics: Iterable[dict]) -> int:
    if not (base and repo_id):
        return 0
    count = 0
    with _connect(base, repo_id) as conn:
        for item in diagnostics:
            result = conn.execute(
                "INSERT OR IGNORE INTO diagnostics VALUES(?,?,?,?,?,?,?,?)",
                (item["id"], item["turn"], item["code"], item["severity"],
                 item["message"], item["evidence"], item["source"], "open"),
            )
            count += max(0, result.rowcount)
    return count


def list_diagnostics(base: str, repo_id: str, *, status: str = "",
                     code: str = "") -> list[dict]:
    with _connect(base, repo_id) as conn:
        query = "SELECT * FROM diagnostics"
        params: list[Any] = []
        where: list[str] = []
        if status:
            where.append("status=?")
            params.append(status)
        if code:
            where.append("code=?")
            params.append(code)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY turn DESC,id"
        rows = conn.execute(query, params)
        return [dict(row) for row in rows]


def resolve(base: str, repo_id: str, diagnostic_id: str, status: str) -> bool:
    if status not in RESOLUTIONS - {"open"}:
        raise ValueError("未知 Narrative CI 处置状态")
    with _connect(base, repo_id) as conn:
        result = conn.execute(
            "UPDATE diagnostics SET status=? WHERE id=?", (status, diagnostic_id),
        )
    return bool(result.rowcount)


def save_arms(base: str, repo_id: str, arms: dict[str, dict], *, turn: int = 0) -> None:
    """按仓库覆盖保存「最近一轮哪些诊断臂参与了」。空转的绿要有地方看得见。"""
    if not (base and repo_id):
        return
    payload = json.dumps({"turn": turn, "arms": arms}, ensure_ascii=False)
    with _connect(base, repo_id) as conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (ARMS_META_KEY, payload))


def load_arms(base: str, repo_id: str) -> dict:
    """读回臂参与台账；读不到（含从未跑过剧情轮）退回空 dict。"""
    if not (base and repo_id):
        return {}
    try:
        with _connect(base, repo_id) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (ARMS_META_KEY,)).fetchone()
        data = json.loads(row["value"]) if row else {}
    except (OSError, ValueError, sqlite3.Error):
        return {}
    return data if isinstance(data, dict) else {}
