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


# ── M1 记忆封口（2026-09-06 用户定案）：删除/重生成回合起的事实关区间，时间线保留 ──


def test_seal_from_turn关闭该回合起仍有效的事实(tmp_path):
    base, repo = str(tmp_path), "repo"
    temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="旧城主",
                               valid_from_turn=1, evidence="旧证据", source="chronicle")
    temporal_fact_store.record(base, repo, subject="龙巢", predicate="位于", object_="北境",
                               valid_from_turn=6, evidence="侦察报告", source="chronicle")

    assert temporal_fact_store.seal_from_turn(base, repo, turn=6) == 1

    # 被封口事实不再注入（as_of 回不到），早于封口回合的事实不受影响
    assert [f["object"] for f in temporal_fact_store.as_of(base, repo, 9)] == ["旧城主"]
    assert [f["object"] for f in temporal_fact_store.as_of(base, repo, 5)] == ["旧城主"]
    # 时间线完整保留（可审计可回放），重复封口幂等
    assert len(temporal_fact_store.timeline(base, repo)) == 2
    assert temporal_fact_store.seal_from_turn(base, repo, turn=6) == 0


def test_封口后同内容重记录重新生效_显式替代关闭的不复活(tmp_path):
    base, repo = str(tmp_path), "repo"
    temporal_fact_store.record(base, repo, subject="神秘女子", predicate="阵营", object_="北门守军",
                               valid_from_turn=6, evidence="低声说北门的钥匙", source="chronicle")
    temporal_fact_store.seal_from_turn(base, repo, turn=6)
    assert temporal_fact_store.as_of(base, repo, 9) == []

    # 删除/重生成后同回合重记录同一事实 → 重新生效（否则重生成的规律永不生效）
    temporal_fact_store.record(base, repo, subject="神秘女子", predicate="阵营", object_="北门守军",
                               valid_from_turn=6, evidence="低声说北门的钥匙", source="chronicle")
    assert [f["object"] for f in temporal_fact_store.as_of(base, repo, 9)] == ["北门守军"]

    # 被显式 supersedes 关闭的事实：同内容重记录产生的是新事实（turn 不同 id 不同），
    # 旧行不复活——合同「只有显式 supersedes 才关闭」不受影响


# ── P3② 指认式替代（2026-09-06 用户裁决）──


def test_前缀解析_精确_唯一前缀_短前缀_歧义(tmp_path):
    base, repo = str(tmp_path), "repo"
    full = temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="旧城主",
                                      valid_from_turn=1, evidence="e", source="chronicle")["id"]
    assert temporal_fact_store.resolve_supersedes(base, repo, full) == full
    assert temporal_fact_store.resolve_supersedes(base, repo, "#" + full[:12]) == full
    assert temporal_fact_store.resolve_supersedes(base, repo, full[:7]) is None      # 太短不猜
    assert temporal_fact_store.resolve_supersedes(base, repo, "ffffffffffff") is None  # 不存在


def test_指认式替代_旧事实收口新事实生效_时间线完整(tmp_path):
    base, repo = str(tmp_path), "repo"
    first = temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="旧城主",
                                       valid_from_turn=1, evidence="登基旧典", source="chronicle")
    temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="新城主",
                               valid_from_turn=6, evidence="登基大典", source="chronicle",
                               supersedes_id=temporal_fact_store.resolve_supersedes(base, repo, first["id"]))
    # 新事实生效、旧事实收口；时间线完整可回放；无冲突残留
    assert [f["object"] for f in temporal_fact_store.as_of(base, repo, 9)] == ["新城主"]
    tl = temporal_fact_store.timeline(base, repo)
    assert (tl[0]["object"], tl[0]["valid_to_turn"]) == ("旧城主", 5)
    assert (tl[1]["object"], tl[1]["valid_to_turn"]) == ("新城主", None)
    assert temporal_fact_store.conflicts(base, repo, 9) == []


def test_指认替代事实被封口后重生成重录_重新生效且旧事实重新关闭(tmp_path):
    base, repo = str(tmp_path), "repo"
    first = temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="旧城主",
                                       valid_from_turn=1, evidence="登基旧典", source="chronicle")
    ref = temporal_fact_store.resolve_supersedes(base, repo, first["id"])
    temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="新城主",
                                        valid_from_turn=6, evidence="登基大典", source="chronicle",
                                        supersedes_id=ref)
    temporal_fact_store.seal_from_turn(base, repo, turn=6)  # 删除回合 6：新城主被收口
    assert temporal_fact_store.as_of(base, repo, 9) == []

    # 重生成重录同事实（同 id）→ 重新生效 + 对旧事实的替代关闭幂等重跑
    temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="新城主",
                               valid_from_turn=6, evidence="登基大典", source="chronicle",
                               supersedes_id=ref)
    assert [f["object"] for f in temporal_fact_store.as_of(base, repo, 9)] == ["新城主"]
    old = [f for f in temporal_fact_store.timeline(base, repo) if f["object"] == "旧城主"][0]
    assert old["valid_to_turn"] == 5  # 旧事实保持「被替代收口」状态，不复活


# ── M2：注入面时效优先（预算裁剪保前几行 → 最新事实存活） ──


def test_as_of_recency_first按新近度降序(tmp_path):
    base, repo = str(tmp_path), "repo"
    temporal_fact_store.record(base, repo, subject="城主", predicate="担任", object_="旧城主",
                               valid_from_turn=1, evidence="旧", source="chronicle")
    temporal_fact_store.record(base, repo, subject="龙巢", predicate="位于", object_="北境",
                               valid_from_turn=2, evidence="侦察", source="chronicle")
    temporal_fact_store.record(base, repo, subject="盟约", predicate="状态", object_="已缔结",
                               valid_from_turn=9, evidence="会盟", source="chronicle")
    got = temporal_fact_store.as_of(base, repo, 10, recency_first=True)
    assert [f["valid_from_turn"] for f in got] == [9, 2, 1]  # 新→旧
    default = temporal_fact_store.as_of(base, repo, 10)
    assert [f["subject"] for f in default] == ["城主", "盟约", "龙巢"]  # 默认分组序不变
