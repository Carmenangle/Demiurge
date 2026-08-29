"""
Narrative CI：内容中立、非阻断的剧情一致性诊断与处置记录。
扩展版本：增加时间/空间/关系/认知越权/世界规则诊断。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from app.services import prose_style
from app.services.pathnames import safe_seg

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
# 文风诊断码属主是 prose_style（词表单一属主），此处并流复用
CODE_STYLE_BANNED_PHRASE = prose_style.CODE_STYLE_BANNED_PHRASE
CODE_STYLE_PUNCT_DENSITY = prose_style.CODE_STYLE_PUNCT_DENSITY
CODE_STYLE_RHYTHM_METRONOME = prose_style.CODE_STYLE_RHYTHM_METRONOME
CODE_STYLE_SELF_QA = prose_style.CODE_STYLE_SELF_QA
CODE_STYLE_PATTERN_REPEAT = prose_style.CODE_STYLE_PATTERN_REPEAT
CODE_STYLE_OPENING_CUE = prose_style.CODE_STYLE_OPENING_CUE


def _diagnostic(turn: int, code: str, message: str, evidence: str,
                source: str, severity: str = "warning") -> dict:
    raw = f"{turn}|{code}|{message}|{evidence}|{source}"
    return {
        "id": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "turn": turn, "code": code, "severity": severity,
        "message": message, "evidence": evidence, "source": source,
        "status": "open",
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
             style_config: dict | None = None) -> list[dict]:
    """只返回带证据诊断；不分类内容、不阻断、不重写正文。

    新增参数：
        world_rules：迭代器，每项为一条世界设定/规则文本（如"药王谷的药材不可带出谷外"）。
        recent_openings：最近数层 assistant 楼层的开头文本（供跨轮开场趋同检测）。
        style_config：prose_style.load_config() 的结果；None=内置默认，
            enabled=False 时跳过全部文风检测（文风功能总开关）。
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
            diagnostics.append(_diagnostic(
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
            diagnostics.append(_diagnostic(
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
                diagnostics.append(_diagnostic(
                    turn, CODE_RELATIONSHIP_JUMP,
                    f"{field} 单回合变化 {value}，需要明确剧情依据。",
                    evidence_str or "状态更新未提供证据", "character_state",
                ))
        if field.endswith("所在") and not evidence_str:
            diagnostics.append(_diagnostic(
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
                diagnostics.append(_diagnostic(
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
                        diagnostics.append(_diagnostic(
                            turn, CODE_BELIEF_CONFLICT,
                            f"{character} 相信「{claim}」，但正文否定了这一认知。",
                            f"stance={stance}，claim={claim}", "character_belief", "warning",
                        ))
                        break

    # ── 6. 时间顺序矛盾（新增）：正文出现反向时间词 ─────────────────────────
    # 简单启发：同一句/段内出现"然后回到"或同一角色先"结果"再"于是"等多步冲突
    temporal_conflicts = _detect_temporal_paradox(body)
    for msg, evd in temporal_conflicts:
        diagnostics.append(_diagnostic(
            turn, CODE_TEMPORAL_PARADOX, msg, evd, "narrative_ci", "warning",
        ))

    # ── 7. 空间位置矛盾（新增）：角色出现在两个不相连空间但无过渡 ───────────
    spatial_conflicts = _detect_spatial_inconsistency(body)
    for msg, evd in spatial_conflicts:
        diagnostics.append(_diagnostic(
            turn, CODE_SPATIAL_INCONSIST, msg, evd, "narrative_ci", "warning",
        ))

    # ── 8. 关系单轮剧变（新增）───────────────────────────────────────────────
    relation_changes = _detect_relation_change(body)
    for msg, evd in relation_changes:
        diagnostics.append(_diagnostic(
            turn, CODE_RELATION_CHANGE, msg, evd, "narrative_ci", "warning",
        ))

    # ── 9. 违反世界规则（新增）───────────────────────────────────────────────
    for rule in world_rules:
        rule_text = str(rule).strip()
        if not rule_text:
            continue
        violated = _check_world_rule_violation(body, rule_text)
        if violated:
            diagnostics.append(_diagnostic(
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
                                         banned_phrases=phrases):
                diagnostics.append(_diagnostic(
                    turn, item["code"], item["message"], item["evidence"],
                    "prose_style", item.get("severity", "warning"),
                ))
        except Exception:  # noqa: BLE001 文风检测永不阻断诊断流
            pass

    unique = {item["id"]: item for item in diagnostics}
    return list(unique.values())


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


def _check_world_rule_violation(text: str, rule: str) -> str:
    """检测正文是否违反单条世界规则。返回空字符串表示未违反，否则返回矛盾证据。"""
    rule = rule.strip()
    if not rule or len(rule) < 4:
        return ""
    constraint = ""
    for pat in [
        r"不可([^。，,！？]+)", r"禁止([^。，,！？]+)", r"不得([^。，,！？]+)",
        r"必须([^。，,！？]+)", r"应当([^。，,！？]+)",
    ]:
        m = re.search(pat, rule)
        if m:
            constraint = m.group(1).strip()
            break
    if not constraint:
        constraint = rule[:20]

    violation_hints = ["偏要", "竟然", "依然", "坚持", "故意", "违反", "无视",
                       "不管不顾", "还是", "仍", "却", "偷偷", "瞒着", "胆敢"]
    action_verbs = ["带出", "离开", "带走", "偷走", "取走", "搬走", "带离", "拿出",
                    "携带", "带着", "闯", "私闯", "破坏", "违背", "打破了"]
    # 找正文中出现的候选核心片段
    cores = _core_nouns(constraint)
    for core in cores:
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
    path = Path(base) / safe_seg(repo_id, strip=False) / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS diagnostics ("
        "id TEXT PRIMARY KEY, turn INTEGER NOT NULL, code TEXT NOT NULL, "
        "severity TEXT NOT NULL, message TEXT NOT NULL, evidence TEXT NOT NULL, "
        "source TEXT NOT NULL, status TEXT NOT NULL)"
    )
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
