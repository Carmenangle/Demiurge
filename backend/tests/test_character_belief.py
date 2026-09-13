import pytest

from app.services import character_belief


def test_同一事实可被不同角色以不同方式理解(tmp_path):
    base = str(tmp_path)
    character_belief.record(
        base, "repo", character="艾丽丝", fact_id="door", claim="密道存在",
        stance="knows", confidence=1, witnessed_at=5, evidence="亲眼看见",
    )
    character_belief.record(
        base, "repo", character="莉亚", fact_id="door", claim="密道存在",
        stance="suspects", confidence=.4, witnessed_at=6, evidence="听见风声",
    )
    items = character_belief.active(base, "repo", 6)
    assert {(item["character"], item["stance"]) for item in items} == {
        ("艾丽丝", "knows"), ("莉亚", "suspects"),
    }


def test_认知变化必须显式替代同角色同事实(tmp_path):
    base = str(tmp_path)
    old = character_belief.record(
        base, "repo", character="莉亚", fact_id="door", claim="密道不存在",
        stance="misbelieves", confidence=.8, witnessed_at=2, evidence="伪造证词",
    )
    new = character_belief.record(
        base, "repo", character="莉亚", fact_id="door", claim="密道存在",
        stance="knows", confidence=1, witnessed_at=8, evidence="亲眼发现",
        supersedes_id=old["id"],
    )
    assert character_belief.active(base, "repo", 8) == [new]


def test_禁止用一个角色的认知替代另一个角色(tmp_path):
    old = character_belief.record(
        str(tmp_path), "repo", character="甲", fact_id="x", claim="事实",
        stance="knows", confidence=1, witnessed_at=1, evidence="目击",
    )
    with pytest.raises(ValueError, match="同一角色"):
        character_belief.record(
            str(tmp_path), "repo", character="乙", fact_id="x", claim="事实",
            stance="knows", confidence=1, witnessed_at=2, evidence="转述",
            supersedes_id=old["id"],
        )


def test_认知上下文明确标出不知道和隐瞒():
    text = character_belief.render_context([
        {"character": "甲", "stance": "unknown", "claim": "王冠被盗", "confidence": 1,
         "evidence": "不在现场"},
        {"character": "乙", "stance": "conceals", "claim": "王冠被盗", "confidence": 1,
         "evidence": "亲手藏匿"},
    ])
    assert "明确不知道" in text and "知道但隐瞒" in text
