"""Narrative CI 扩展诊断 + 角色认知自动抽取测试。"""
from app.services import belief_extractor, character_belief, narrative_ci


def test_world_rule_violation_detected():
    """正文违反世界规则应产生 world_rule_break 诊断。"""
    body = "他偏要带出谷外，即便知道这是禁忌。"
    diags = narrative_ci.evaluate(
        body, turn=1, world_rules=["药王谷的药材不可带出谷外。"],
    )
    codes = {item["code"] for item in diags}
    assert "world_rule_break" in codes


def test_no_world_rule_violation_when_compliant():
    """正文遵守世界规则时不应产生 world_rule_break。"""
    body = "他小心翼翼地把药材放回药架，转身离开药房。"
    diags = narrative_ci.evaluate(
        body, turn=1, world_rules=["药王谷的药材不可带出谷外。"],
    )
    codes = {item["code"] for item in diags}
    assert "world_rule_break" not in codes


def test_world_rule_positive_obligation_not_misjudged_as_prohibition():
    """「必须 X」是义务不是禁令：正文照做时不得报「规则禁止 X」。

    2026-11-06 实测（D1）：旧提取器把「必须」也当禁令约束，正文合规却报
    「规则禁止「每日饮下苦水」，但正文出现「带出」动作」——warning 级判据宁可沉默，
    也不能指控照做的正文。
    """
    body = "他每日把苦水从井里带出，仰头饮下，一句怨言也没有。"
    diags = narrative_ci.evaluate(body, turn=1, world_rules=["凡人必须每日饮下苦水。"])
    assert "world_rule_break" not in {item["code"] for item in diags}


def test_world_rule_positive_obligation_absence_not_guessed():
    """正向义务「没做到」无法用词窗可靠判定 ⇒ 本臂有意不判（宁可漏，不可诬告）。"""
    body = "他从不饮下苦水，只顾自己快活。"
    diags = narrative_ci.evaluate(body, turn=1, world_rules=["凡人必须每日饮下苦水。"])
    assert "world_rule_break" not in {item["code"] for item in diags}


def test_world_rule_all_prohibitions_checked_not_only_first():
    """一条规则含多条禁令时每条都要核对——旧实现按模式顺序命中第一条即 break。

    「药材不可带出谷外，也不得私自买卖。」＋正文只违反后半句 ⇒ 必须报红。
    """
    body = "他偷偷做起了药材买卖。"
    diags = narrative_ci.evaluate(
        body, turn=1, world_rules=["药材不可带出谷外，也不得私自买卖。"],
    )
    evidence = [item["evidence"] for item in diags if item["code"] == "world_rule_break"]
    assert evidence and "私自买卖" in evidence[0]


def test_temporal_paradox_detected():
    """同一段落同时出现结果词与回溯词应产生 temporal_paradox。"""
    body = "于是她决定离开。起初她还想着留下来，但最终还是走了。"
    diags = narrative_ci.evaluate(body, turn=1)
    codes = {item["code"] for item in diags}
    assert "temporal_paradox" in codes


def test_spatial_inconsistency_detected():
    """角色返回另一位置但无过渡路径应产生 spatial_inconsistency。"""
    body = "她站在寝殿里。\n她回到了牢房，却没有穿过任何走廊。"
    diags = narrative_ci.evaluate(body, turn=1)
    codes = {item["code"] for item in diags}
    assert "spatial_inconsistency" in codes


def test_relation_change_detected():
    """同段出现敌对与友好关系词并置应产生 relationship_change。"""
    body = "他上前拥抱了她，随即又憎恨地推开。"
    diags = narrative_ci.evaluate(body, turn=1)
    codes = {item["code"] for item in diags}
    assert "relationship_change" in codes


def test_belief_conflict_detected():
    """角色知道/相信的事实被正文否定应产生 character_belief_conflict。"""
    beliefs = [{
        "character": "顾凝霜", "fact_id": "f1", "claim": "药王谷的药材不可带出",
        "stance": "knows", "confidence": 0.95, "witnessed_at": 1,
        "evidence": "她一直遵守门规", "source": "auto",
    }]
    body = "顾凝霜并没有遵守门规，她把药材带出了谷外。"
    diags = narrative_ci.evaluate(body, turn=2, beliefs=beliefs)
    codes = {item["code"] for item in diags}
    assert "character_belief_conflict" in codes


def test_belief_extractor_knows():
    """"知道"句应抽取为 knows。"""
    items = belief_extractor.extract(
        "顾凝霜知道药王谷的药材不可带出。",
        known_names=["顾凝霜"],
    )
    assert items and items[0]["stance"] == "knows"
    assert items[0]["character"] == "顾凝霜"


def test_belief_extractor_misbelieves():
    """"误以为"句应抽取为 misbelieves。"""
    items = belief_extractor.extract(
        "苏映晚误以为顾凝霜背叛了师门。",
        known_names=["苏映晚", "顾凝霜"],
    )
    assert items and items[0]["stance"] == "misbelieves"
    assert items[0]["character"] == "苏映晚"


def test_belief_extractor_unknown():
    """"不知道"句应抽取为 unknown。"""
    items = belief_extractor.extract(
        "顾凝霜并不知道谷外的阴谋。",
        known_names=["顾凝霜"],
    )
    assert items and items[0]["stance"] == "unknown"


def test_belief_extractor_ingest_roundtrip(tmp_path):
    """ingest 应落库且幂等（同 turn 同 claim 不重复写）。"""
    text = "顾凝霜知道药王谷的药材不可带出。苏映晚怀疑她另有隐情。"
    result1 = belief_extractor.ingest(
        str(tmp_path), "repo", text=text, turn=1, known_names=["顾凝霜", "苏映晚"],
    )
    assert result1["recorded"] >= 2
    result2 = belief_extractor.ingest(
        str(tmp_path), "repo", text=text, turn=1, known_names=["顾凝霜", "苏映晚"],
    )
    assert result2["recorded"] == 0
    # active 应包含已抽取的认知
    active = character_belief.active(str(tmp_path), "repo", 1)
    stances = {item["stance"] for item in active}
    assert {"knows", "suspects"} <= stances


def test_ci_extended_codes_present():
    """新增代码常量应被识别。"""
    assert narrative_ci.CODE_TEMPORAL_PARADOX == "temporal_paradox"
    assert narrative_ci.CODE_WORLD_RULE_BREAK == "world_rule_break"
    assert narrative_ci.CODE_SPATIAL_INCONSIST == "spatial_inconsistency"
    assert narrative_ci.CODE_RELATION_CHANGE == "relationship_change"
    assert narrative_ci.CODE_BELIEF_CONFLICT == "character_belief_conflict"


# ── 臂参与台账（清单 #3／#4：没书所以不判 ⇒ 报告要写明「这一臂本轮没参与」）──────
#
# 判据本体一个字没动（`for rule in world_rules:` / `if set_texts:`），这里只锁「观测口径」：
# 空转的臂必须在报告里看得见，且**原因要说对**——说错原因同样是假绿。
# 2026-11-06（缺陷 4）补第三条臂：本轮到底有没有 `agency.judge` 的裁定可核对，同样要说清。


def test_臂台账_没喂输入时三条臂都记未参与():
    assert narrative_ci.arm_participation() == {
        "world_rule": {"participated": False, "count": 0, "reason": "no_worldbook"},
        "term_ratchet_stock": {"participated": False, "count": 0, "reason": "no_worldbook"},
        "agency_verdict": {"participated": False, "count": 0, "reason": "world_not_consulted"},
    }


def test_臂台账_没参与的四种原因分得开():
    """没书／书读不出来／书是空的／有书但这一臂没喂到东西——面板上是四句话。"""
    def why(**kw) -> str:
        return narrative_ci.arm_participation(**kw)["world_rule"]["reason"]

    assert why() == "no_worldbook"
    assert why(has_worldbook=True, readable=False) == "snapshot_unreadable"
    assert why(has_worldbook=True, worldbook_entries=0) == "empty_worldbook"
    assert why(has_worldbook=True, worldbook_entries=3) == "no_rule_lines"


def test_臂台账_世界规则臂与存量臂各自独立():
    """有书、只喂到规则句：世界规则臂参与，棘轮存量臂仍须报「没有静态底座」。"""
    arms = narrative_ci.arm_participation(
        world_rules=["药王谷的药材不可带出谷外。"], has_worldbook=True, worldbook_entries=4)
    assert arms["world_rule"] == {"participated": True, "count": 1, "reason": ""}
    assert arms["term_ratchet_stock"] == {
        "participated": False, "count": 0, "reason": "no_static_base"}


def test_臂台账_裁定臂三种未参与原因分得开():
    """没问过 World／问了但一条提案也没有／有提案但全被前置门槛拦下没掷骰——
    面板上是三句话。第三种尤其要说清：`roll==0` 的裁定 `narrative_directive` 一个字
    都不写进正文，所以「没东西要求正文」与「要求了没核对」不能都写成 `no_proposal`。"""
    def why(claims) -> str:
        return narrative_ci.arm_participation(agency_claims=claims)["agency_verdict"]["reason"]

    assert why(None) == "world_not_consulted"
    assert why([]) == "no_proposal"
    assert why([{"roll": 0}]) == "no_rolled_attempt"
    assert why([{"roll": 0}, {"roll": 81}]) == ""


def test_臂台账_裁定臂不走世界书阶梯():
    """裁定臂的输入来自 World Agent，**不看世界书**。世界书阶梯（没书／读不出来／空条目）
    一旦套到它头上，面板就把「本轮没问过 World」引成「去补世界书」——动作方向是错的。"""
    arms = narrative_ci.arm_participation(has_worldbook=True, worldbook_entries=9)
    assert arms["agency_verdict"] == {
        "participated": False, "count": 0, "reason": "world_not_consulted"}


def test_臂台账_裁定计数与叙事指令同源():
    """台账数的是「必须在本轮正文体现」的裁定条数，与 `narrative_directive` 同一个
    `roll > 0` 口径；一边数错就会出现「正文被要求写未遂、台账说本轮没裁定可核对」。"""
    from app.services import agency, roleplay_agency

    掷过 = agency.Verdict("埃吉尔", agency.OUTCOME_REJECT, agency.DEGREE_NONE, 81, 70, "困难")
    未掷 = agency.Verdict("露娜", agency.OUTCOME_ACCEPT, agency.DEGREE_FULL, 0, 50, "好感不足")

    got = narrative_ci.arm_participation(
        agency_claims=[vars(掷过), vars(未掷)])["agency_verdict"]
    assert got == {"participated": True, "count": 1, "reason": ""}

    assert roleplay_agency.narrative_directive([未掷]) == ""
    assert not narrative_ci.arm_participation(agency_claims=[vars(未掷)])["agency_verdict"][
        "participated"]


def test_臂台账_全空白输入不算参与():
    """判据里有 `if not rule_text: continue` ⇒ 全是空白的列表照样一行不跑；
    台账不能因为「列表非空」就报参与，否则就是把空转渲染成干净。"""
    arms = narrative_ci.arm_participation(
        world_rules=["", "   "], stock_sources=["\n"], has_worldbook=True, worldbook_entries=2)
    assert not arms["world_rule"]["participated"]
    assert not arms["term_ratchet_stock"]["participated"]


def test_臂台账与判据同源():
    """台账说「参与」时判据才可能出码；说「未参与」时确实出不了码。两边读同一份入参。"""
    body = "他偏要带出谷外，即便知道这是禁忌。"
    rules = ["药王谷的药材不可带出谷外。"]
    fired = {"world_rule_break"} & {i["code"] for i in
                                    narrative_ci.evaluate(body, turn=1, world_rules=rules)}
    assert fired and narrative_ci.arm_participation(
        world_rules=rules, has_worldbook=True, worldbook_entries=1)["world_rule"]["participated"]

    idle = {i["code"] for i in narrative_ci.evaluate(body, turn=1, world_rules=rules[:0])}
    assert "world_rule_break" not in idle


def test_臂台账不反过来参与判定():
    """观测层一旦被判据读回去，「没书」就会变成一条新诊断——这条耦合必须锁死。"""
    import inspect

    assert "arm_participation" not in inspect.getsource(narrative_ci.evaluate)
    assert "arm_participation" not in inspect.getsource(narrative_ci.diagnostic_entry)


def test_臂台账读写往返(tmp_path):
    base = str(tmp_path)
    assert narrative_ci.load_arms(base, "r1") == {}      # 从没跑过剧情轮 ⇒ 面板不留块

    arms = narrative_ci.arm_participation(
        world_rules=["药材不可带出谷外。"], has_worldbook=True, worldbook_entries=2)
    narrative_ci.save_arms(base, "r1", arms, turn=7)
    assert narrative_ci.load_arms(base, "r1") == {"turn": 7, "arms": arms}


def test_臂台账按仓库隔离且只留最近一轮(tmp_path):
    base = str(tmp_path)
    narrative_ci.save_arms(base, "r1", narrative_ci.arm_participation(), turn=1)
    narrative_ci.save_arms(base, "r2", narrative_ci.arm_participation(
        stock_sources=["药王谷的规矩。"], has_worldbook=True, worldbook_entries=1), turn=5)

    assert narrative_ci.load_arms(base, "r1")["arms"]["term_ratchet_stock"]["reason"] \
        == "no_worldbook"
    got = narrative_ci.load_arms(base, "r2")
    assert got["turn"] == 5 and got["arms"]["term_ratchet_stock"]["participated"]

    narrative_ci.save_arms(base, "r1", narrative_ci.arm_participation(), turn=2)
    assert narrative_ci.load_arms(base, "r1")["turn"] == 2   # 覆盖，不追加历史


def test_臂台账缺参静默():
    narrative_ci.save_arms("", "r1", {}, turn=1)
    narrative_ci.save_arms("x", "", {}, turn=1)
    assert narrative_ci.load_arms("", "") == {}


def test_旧库无meta表时台账读写自动补表(tmp_path):
    """老库只有 diagnostics：第一次 save_arms 必须建出 meta 而不是抛，且读侧不能被新表搞坏。"""
    import sqlite3

    from app.services import repo_meta

    base = str(tmp_path)
    db = repo_meta.engine_repo_dir(base, "r1") / narrative_ci.DB_NAME
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE diagnostics (id TEXT PRIMARY KEY, turn INTEGER NOT NULL, "
            "code TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL, "
            "evidence TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL)")
        conn.execute("INSERT INTO diagnostics VALUES('x1',1,'temporal_paradox',"
                     "'warning','m','e','s','open')")

    assert [i["id"] for i in narrative_ci.list_diagnostics(base, "r1")] == ["x1"]
    narrative_ci.save_arms(base, "r1", narrative_ci.arm_participation(), turn=3)
    assert narrative_ci.load_arms(base, "r1")["turn"] == 3


def test_台账值坏了退回空而不是抛(tmp_path):
    """面板读不到台账时应当「不显示判据块」，而不是把整条 GET /narrative/ci 带崩。"""
    base = str(tmp_path)
    narrative_ci.save_arms(base, "r1", narrative_ci.arm_participation(), turn=1)
    with narrative_ci._connect(base, "r1") as conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                     (narrative_ci.ARMS_META_KEY, "{坏"))
    assert narrative_ci.load_arms(base, "r1") == {}


# ─── 未遂对账（缺陷 4 的第二段：裁定 → 叙事指令 → CI 核对）────────────────────
#
# 稳定合同（`docs/memory/npc-agency-goals.md` 第 5 条）：裁判判失败的尝试**必须写成未遂或
# 受挫**。`narrative_directive` 每轮都把这句话写进主叙事指令，但过去没有任何一道闸回看
# 正文有没有照做 ⇒ 指令是否被听见只能靠人肉盯。以下用例的输入形态取自 2026-11-06 的
# 真语料定档（`backend/data/logs/agent-trace*`，18 条已掷骰失败裁定：9 违规 / 7 合规 / 2 无从判定）。

_V_押送令 = (
    "<content>我回到值房时，押送令已经签好了，朱印压在纸角上。\n"
    "埃吉尔把笔搁回砚边，没有提方才那一场交涉。</content>\n"
    "<status>\n[在场] 埃吉尔、沈青梧\n</status>\n"
    "<roll>\n[检定] 埃吉尔 取回押送令 D100=81 技能=70\n[RESULT] 失败\n</roll>"
)
_ROSTER = "[在场] 埃吉尔、沈青梧"


def _失败裁定(actor: str = "埃吉尔", intent: str = "取回押送令", **kw) -> list[dict]:
    from app.services import agency

    verdict = agency.Verdict(actor, agency.OUTCOME_REJECT, agency.DEGREE_NONE, 81, 70,
                             "骰点高于技能", intent=intent, **kw)
    return [vars(verdict)]


def test_未遂对账_判失败但正文没写未遂时报warning():
    from app.services import story_history

    items = narrative_ci.evaluate(_V_押送令, turn=4, agency_claims=_失败裁定())
    fired = [i for i in items if i["code"] == narrative_ci.CODE_NPC_VERDICT_BREAK]
    assert len(fired) == 1
    assert fired[0]["severity"] == "warning"
    assert "埃吉尔" in fired[0]["message"] and "取回押送令" in fired[0]["message"]
    assert fired[0]["evidence"] in story_history.prose_only(_V_押送令)


def test_未遂对账_正文出现定档措辞时不报():
    text = _V_押送令.replace("押送令已经签好了", "押送令没能签下来，被驳回")
    codes = {i["code"] for i in narrative_ci.evaluate(text, turn=4, agency_claims=_失败裁定())}
    assert narrative_ci.CODE_NPC_VERDICT_BREAK not in codes


def test_未遂对账_roll块里的失败字样不洗白正文():
    """清洗面只认正文：骰点块自己会写「未遂／驳回」，那不算正文照做（实测「失败」在
    `<roll>` 内出现 2 次 ⇒ 不剥就是把违规判成合规）。"""
    text = _V_押送令.replace("[RESULT] 失败", "[RESULT] 未遂，命令被驳回")
    codes = {i["code"] for i in narrative_ci.evaluate(text, turn=4, agency_claims=_失败裁定())}
    assert narrative_ci.CODE_NPC_VERDICT_BREAK in codes


def test_未遂对账_人群与叙事指令的未遂句逐字同源():
    """CI 核对的人群必须**恰好**等于 `narrative_directive` 写出「必须写成未遂或受挫」的人群：
    多核 ⇒ 报没要求过的事；少核 ⇒ 要求过的事漏掉。两边必须同进同退。"""
    from app.services import agency, roleplay_agency

    for kind in ("npc", "event", "user_action"):
        for outcome in (agency.OUTCOME_ACCEPT, agency.OUTCOME_REJECT):
            for roll in (0, 81):
                verdict = agency.Verdict("埃吉尔", outcome, agency.DEGREE_NONE, roll, 70, "骰点",
                                         intent="取回押送令", kind=kind, who="埃吉尔")
                asked = "必须写成未遂或受挫" in roleplay_agency.narrative_directive([verdict])
                assert bool(narrative_ci.verdict_attempt_claims([vars(verdict)])) is asked, \
                    (kind, outcome, roll)


def test_未遂对账_没有可核对裁定时一条不出():
    """`agency_claims` 默认空 ⇒ 手动 CI（`POST /narrative/ci/check`）与旧调用行为逐字不变。"""
    assert narrative_ci.evaluate(_V_押送令, turn=4) == narrative_ci.evaluate(
        _V_押送令, turn=4, agency_claims=[])


def test_未遂对账_人名既不在正文也不在场名单时不报():
    """actor 缺席 ⇒ 词表档看不见「有没有写未遂」，此时报红等于指控「没承接」，是另一种判据。
    实测 18 条里 npc 类**零命中**这种形态（只有 1 条 user_action），故本臂选择沉默不判。"""
    items = narrative_ci.evaluate(
        "<content>雨停了，廊下没有人。</content>", turn=4,
        agency_claims=_失败裁定(actor="奥古斯特·冯·帕塞瓦尔"), scene_snapshot=_ROSTER)
    assert narrative_ci.CODE_NPC_VERDICT_BREAK not in {i["code"] for i in items}


def test_未遂对账_在场名单能补上正文里没点名的actor():
    """实测：同一角色 5 条违规里 4 条的名字**只出现在 `[在场]`**，不在正文。
    在场闸少了 `scene_snapshot` 这一条腿，召回率从 100% 掉到 56%。"""
    items = narrative_ci.evaluate(
        "<content>我推门进去，令已经签好了。</content>\n<status>\n" + _ROSTER + "\n</status>",
        turn=4, agency_claims=_失败裁定(), scene_snapshot=_ROSTER)
    assert narrative_ci.CODE_NPC_VERDICT_BREAK in {i["code"] for i in items}


def test_定档词表_退回来与失败都不在表内():
    """逐词贡献实测（16 条）：`退回来` 是唯一一次洗白真违规的词（9bb03d04），
    `失败` 只出现在 `<roll>` 块与泛指句 ⇒ 两个都不收。
    `咽回去`（保住 2 条合规、零洗白）与 `驳回`/`没等到` 保留。"""
    markers = set(narrative_ci.VERDICT_ATTEMPT_MARKERS)
    assert {"退回来", "退开", "失败", "只好", "被迫", "无从"} & markers == set()
    assert {"未遂", "受挫", "咽回去", "驳回", "没等到"} <= markers


def test_在场闸只认全名_不做节译别名():
    """实测：加别名腿（分段／去衔称／取前两字）把 1 条合规推成假阳——c0faace3 正文没提名，
    但快照里的短称被别名匹配当成「actor 在场」。全名口径 82%／100%，别名口径 75%／100%
    ⇒ 宁可少指控一条，不为「看起来更聪明」去猜节译名。"""
    text = ("<content>天没亮，窗帘缝里的光是灰的，谁也没提昨夜那纸调令。</content>\n"
            "<status>\n[在场] IB031前任指挥官\n</status>")
    items = narrative_ci.evaluate(text, turn=4,
                                  agency_claims=_失败裁定(actor="IB031前任指挥官"),
                                  scene_snapshot="[在场] 指挥官")
    assert narrative_ci.CODE_NPC_VERDICT_BREAK not in {i["code"] for i in items}


def test_未遂对账_已知天花板_合规靠对白让步呈现时仍报红():
    """精确率 75%（9 真 / 3 假 / n=12 报红）是**词表档的地板**，不是没调好：
    实测 bf099121 那类合规正文用「侧室空着吧」「那就别让他站在走廊上」把拒绝演出来，
    一个失败词都不出现。唯一能把这 3 条按下去的办法是加词，而加词按实测会洗掉真违规
    ⇒ 这条用例把「别拿召回换精确」钉住，也提醒面板这码是提示级、不是定罪。"""
    text = ("<content>她说完那句，拇指在戒圈上按了一下，把撑在栏杆上的手收回来。\n"
            "「侧室空着吧。」「空着。」</content>\n<status>\n" + _ROSTER + "\n</status>")
    items = narrative_ci.evaluate(text, turn=4, agency_claims=_失败裁定(intent="安排侧室入院"),
                                  scene_snapshot=_ROSTER)
    assert narrative_ci.CODE_NPC_VERDICT_BREAK in {i["code"] for i in items}
