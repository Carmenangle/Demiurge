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


def test_red_assets_excluded_from_rank(tmp_path):
    """Elo 与 Visual CI 打通：verdict='red' 的资产从 rank 结果中剔除，但可显式关闭过滤。"""
    import sqlite3

    ci_db = tmp_path / "repo" / "visual_ci.db"
    ci_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ci_db))
    con.execute(
        "CREATE TABLE IF NOT EXISTS diagnostics ("
        "id TEXT PRIMARY KEY,generation_id TEXT NOT NULL,turn_id TEXT DEFAULT '',"
        "created_at TEXT NOT NULL,status TEXT DEFAULT 'pending',verdict TEXT DEFAULT '',"
        "mechanical TEXT DEFAULT '{}',vlm_assessment TEXT DEFAULT '{}',similarity REAL DEFAULT 0.0,"
        "field_ledger TEXT DEFAULT '{}',retry_count INTEGER DEFAULT 0,retry_of TEXT DEFAULT '',"
        "evidence TEXT DEFAULT '{}')"
    )
    con.execute(
        "INSERT INTO diagnostics(id,generation_id,created_at,status,verdict)"
        " VALUES('d1','red-gen','2026-01-01','ok','red')"
    )
    con.commit()
    con.close()

    items = [
        {"id": "red-gen", "repo_id": "repo"},
        {"id": "ok-gen", "repo_id": "repo"},
    ]
    ranked = visual_preference.rank(str(tmp_path), items)
    assert {item["id"] for item in ranked} == {"ok-gen"}
    all_items = visual_preference.rank(str(tmp_path), items, exclude_red=False)
    assert {item["id"] for item in all_items} == {"red-gen", "ok-gen"}


def test_rank_without_ci_db_unchanged(tmp_path):
    """没有 visual_ci.db 时 rank 保持原行为。"""
    items = [{"id": "a", "repo_id": "repo"}, {"id": "b", "repo_id": "repo"}]
    ranked = visual_preference.rank(str(tmp_path), items)
    assert {item["id"] for item in ranked} == {"a", "b"}
