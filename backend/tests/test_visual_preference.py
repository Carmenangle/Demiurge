from app.services import visual_preference


def test_显式二选一更新双方分数并保持作品隔离(tmp_path):
    result = visual_preference.record(
        str(tmp_path), "repo-a", winner_id="good", loser_id="bad", reason="character",
    )
    assert result["winner_score"] > 1000 > result["loser_score"]
    assert visual_preference.score_map(str(tmp_path), "repo-a")["good"] > 1000
    assert visual_preference.score_map(str(tmp_path), "repo-b") == {}


def test_偏好排序只重排不删除资产(tmp_path):
    visual_preference.record(str(tmp_path), "repo", winner_id="b", loser_id="a")
    items = [{"id": "a", "repo_id": "repo"}, {"id": "b", "repo_id": "repo"},
             {"id": "c", "repo_id": "repo"}]
    ranked = visual_preference.rank(str(tmp_path), items)
    assert {item["id"] for item in ranked} == {"a", "b", "c"}
    assert ranked[0]["id"] == "b"


def test_偏好摘要可清除且不包含自动LoRA修改(tmp_path):
    visual_preference.record(
        str(tmp_path), "repo", winner_id="b", loser_id="a", reason="composition",
    )
    summary = visual_preference.summary(str(tmp_path), "repo")
    assert summary["reasons"] == {"composition": 1}
    assert "lora" not in str(summary).lower()
    visual_preference.clear(str(tmp_path), "repo")
    assert visual_preference.summary(str(tmp_path), "repo")["comparisons"] == 0
