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
        "character": "冷倾雪", "fact_id": "f1", "claim": "药王谷的药材不可带出",
        "stance": "knows", "confidence": 0.95, "witnessed_at": 1,
        "evidence": "她一直遵守门规", "source": "auto",
    }]
    body = "冷倾雪并没有遵守门规，她把药材带出了谷外。"
    diags = narrative_ci.evaluate(body, turn=2, beliefs=beliefs)
    codes = {item["code"] for item in diags}
    assert "character_belief_conflict" in codes


def test_belief_extractor_knows():
    """"知道"句应抽取为 knows。"""
    items = belief_extractor.extract(
        "冷倾雪知道药王谷的药材不可带出。",
        known_names=["冷倾雪"],
    )
    assert items and items[0]["stance"] == "knows"
    assert items[0]["character"] == "冷倾雪"


def test_belief_extractor_misbelieves():
    """"误以为"句应抽取为 misbelieves。"""
    items = belief_extractor.extract(
        "虞妙玥误以为冷倾雪背叛了师门。",
        known_names=["虞妙玥", "冷倾雪"],
    )
    assert items and items[0]["stance"] == "misbelieves"
    assert items[0]["character"] == "虞妙玥"


def test_belief_extractor_unknown():
    """"不知道"句应抽取为 unknown。"""
    items = belief_extractor.extract(
        "冷倾雪并不知道谷外的阴谋。",
        known_names=["冷倾雪"],
    )
    assert items and items[0]["stance"] == "unknown"


def test_belief_extractor_ingest_roundtrip(tmp_path):
    """ingest 应落库且幂等（同 turn 同 claim 不重复写）。"""
    text = "冷倾雪知道药王谷的药材不可带出。虞妙玥怀疑她另有隐情。"
    result1 = belief_extractor.ingest(
        str(tmp_path), "repo", text=text, turn=1, known_names=["冷倾雪", "虞妙玥"],
    )
    assert result1["recorded"] >= 2
    result2 = belief_extractor.ingest(
        str(tmp_path), "repo", text=text, turn=1, known_names=["冷倾雪", "虞妙玥"],
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
