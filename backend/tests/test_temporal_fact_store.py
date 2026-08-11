import pytest

from app.services import temporal_fact_store


def test_temporal_fact_requires_explicit_supersession_and_supports_as_of(tmp_path):
    first = temporal_fact_store.record(
        str(tmp_path), "repo", subject="王城", predicate="统治者", object_="旧王",
        valid_from_turn=1, evidence="第一章登基记录", source="chronicle",
    )
    second = temporal_fact_store.record(
        str(tmp_path), "repo", subject="王城", predicate="统治者", object_="新王",
        valid_from_turn=8, evidence="第八章政变成功", source="chronicle",
        supersedes_id=first["id"],
    )

    assert temporal_fact_store.as_of(str(tmp_path), "repo", 7)[0]["object"] == "旧王"
    assert temporal_fact_store.as_of(str(tmp_path), "repo", 8)[0]["id"] == second["id"]
    assert first["id"] != second["id"]


def test_temporal_fact_reports_unresolved_conflicts_without_guessing(tmp_path):
    for value, evidence in (("北境", "侦察兵甲报告"), ("东境", "侦察兵乙报告")):
        temporal_fact_store.record(
            str(tmp_path), "repo", subject="龙巢", predicate="位于", object_=value,
            valid_from_turn=3, evidence=evidence, source="chronicle",
        )

    conflicts = temporal_fact_store.conflicts(str(tmp_path), "repo", 3)
    assert {fact["object"] for fact in conflicts[0]["facts"]} == {"北境", "东境"}


def test_character_state_fields_cannot_become_a_second_truth(tmp_path):
    with pytest.raises(ValueError, match="character_state"):
        temporal_fact_store.record(
            str(tmp_path), "repo", subject="露娜", predicate="心情", object_="平静",
            valid_from_turn=2, evidence="她笑了", source="chronicle",
        )
