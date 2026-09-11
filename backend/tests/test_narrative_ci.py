from app.services import narrative_ci


def test_ci发现有效事实冲突和正文直接否定():
    facts = [
        {"subject": "塞西莉亚", "predicate": "身份", "object": "院长", "evidence": "任命书"},
        {"subject": "塞西莉亚", "predicate": "身份", "object": "商人", "evidence": "伪造文件"},
    ]
    found = narrative_ci.evaluate("塞西莉亚并非院长。", turn=8, facts=facts)
    codes = {item["code"] for item in found}
    assert {"active_fact_conflict", "fact_contradiction"} <= codes


def test_ci发现无证据关系跳变和地点跳变但不改写正文():
    text = "她在王宫停下脚步。"
    found = narrative_ci.evaluate(text, turn=9, raw_deltas=[
        {"field": "数值/莉亚·好感度", "op": "add", "value": 50, "evidence": ""},
        {"field": "叙事/莉亚·所在", "op": "set", "value": "王宫", "evidence": ""},
    ])
    assert {item["code"] for item in found} == {
        "relationship_jump", "location_without_transition",
    }
    assert text == "她在王宫停下脚步。"


def test_ci诊断可持久化并由用户处置(tmp_path):
    items = narrative_ci.evaluate("甲不是守卫。", turn=2, facts=[
        {"subject": "甲", "predicate": "身份", "object": "守卫", "evidence": "名册"},
    ])
    assert narrative_ci.save(str(tmp_path), "repo", items) == 1
    saved = narrative_ci.list_diagnostics(str(tmp_path), "repo")
    assert saved[0]["status"] == "open"
    assert narrative_ci.resolve(str(tmp_path), "repo", saved[0]["id"], "foreshadow")
    assert narrative_ci.list_diagnostics(str(tmp_path), "repo", status="foreshadow")


def test_ci不按内容分级也不把成人词当错误():
    assert narrative_ci.evaluate("adult explicit intimate scene", turn=1) == []
