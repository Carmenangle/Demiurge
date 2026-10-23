"""条目↔章节覆盖台账 + 修订旁路 + 条目删除（固化02 §3.9 增量链第三/四步）。

锁定四件事：
1. `worldbook.upsert_repo(source_chapters=…)` 写入成功后机械记台账（不进条目五字段）；
2. `worldbook.coverage` 反查：哪些条目被本次变更章节影响、哪些章节没人覆盖；
3. 修订旁路 `allow_shrink` 必须带原因，且只放行「删减修订」不放开其它保护；
4. `worldbook.delete_entries` 删除条目并同步清台账（不留「已删条目仍算受影响」）。
"""
from __future__ import annotations

import json

import pytest

from app.services import capability_handlers as ch
from app.services import repo_meta, worldbook_coverage, worldbook_store


def _entry(comment: str, keys: list[str], chars: int = 1900) -> dict:
    return {"comment": comment, "keys": keys, "constant": False,
            "content": f"【身份】{comment}的设定正文。" + "填充段落用于满足密度下限。" * max(1, chars // 15)}


def _setup(tmp_path):
    base = str(tmp_path)
    return base, "work"


def _ledger(base: str, repo_id: str) -> dict:
    p = worldbook_coverage.ledger_path(str(repo_meta.engine_repo_dir(base, repo_id)))
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ── 记账 ────────────────────────────────────────────────────────────────────


def test_upsert带source_chapters写台账(tmp_path):
    base, repo_id = _setup(tmp_path)
    res = ch.upsert_repo_worldbook(base, repo_id,
                                   [_entry("角色卡·苏晚晚", ["苏晚晚"])],
                                   source_chapters=[61, 62])
    assert res["applied"] == 1
    data = _ledger(base, repo_id)
    got = data["entries"]["角色卡·苏晚晚"]
    assert got["chapters"] == [61, 62]
    assert got["chars"] > 1000


def test_台账章节并集累积_重写不丢旧来源(tmp_path):
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [_entry("角色卡·苏晚晚", ["苏晚晚"])],
                             source_chapters=[1, 2])
    ch.upsert_repo_worldbook(base, repo_id, [_entry("角色卡·苏晚晚", ["苏晚晚"])],
                             source_chapters=[63])
    assert _ledger(base, repo_id)["entries"]["角色卡·苏晚晚"]["chapters"] == [1, 2, 63]


def test_条目五字段不被污染(tmp_path):
    """台账是外置文件：快照条目里不能出现 source_chapters 之类新字段。"""
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [_entry("角色卡·苏晚晚", ["苏晚晚"])],
                             source_chapters=[1])
    snap = worldbook_store.read_repo_snapshot(base, repo_id) or {}
    entry = (snap.get("entries") or [])[0]
    assert set(entry) <= {"content", "comment", "keys", "constant", "enabled", "index"}


# ── 反查报表 ────────────────────────────────────────────────────────────────


def test_coverage_受影响条目与未覆盖章节(tmp_path):
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [
        _entry("角色卡·苏晚晚", ["苏晚晚"]),
        _entry("世界背景·1. 南境", ["南境"]),
    ], source_chapters=[1, 2, 3])

    rep = ch.coverage_report(base, repo_id, chapters=[2])
    assert {a["comment"] for a in rep["affected"]} == {"角色卡·苏晚晚", "世界背景·1. 南境"}
    assert rep["uncovered_chapters"] == []
    assert rep["has_ledger"] is True

    rep2 = ch.coverage_report(base, repo_id, chapters=[9])
    assert rep2["affected"] == []
    assert rep2["uncovered_chapters"] == [9]


def test_coverage_台账上线前条目进unregistered(tmp_path):
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [_entry("角色卡·苏晚晚", ["苏晚晚"])])  # 不带章节
    rep = ch.coverage_report(base, repo_id, chapters=[5])
    assert rep["has_ledger"] is False
    assert "角色卡·苏晚晚" in rep["unregistered"]
    assert rep["note"]  # 空台账必须给可操作说明


def test_coverage_已删条目进stale(tmp_path):
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [
        _entry("角色卡·苏晚晚", ["苏晚晚"]), _entry("角色卡·沈糯", ["沈糯"]),
    ], source_chapters=[1])
    ch.delete_repo_entries(base, repo_id, comments=["角色卡·沈糯"])
    rep = ch.coverage_report(base, repo_id)
    assert "角色卡·沈糯" not in [a["comment"] for a in rep["affected"]]
    assert rep["stale"] == []


# ── 修订旁路 allow_shrink ───────────────────────────────────────────────────


def test_allow_shrink_必须给原因(tmp_path):
    base, repo_id = _setup(tmp_path)
    with pytest.raises(ValueError, match="revision_reason"):
        ch.upsert_repo_worldbook(base, repo_id, [_entry("角色卡·苏晚晚", ["苏晚晚"])],
                                 allow_shrink=True)


def test_覆盖保护默认仍拦短版_allow_shrink才放行(tmp_path):
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [_entry("角色卡·苏晚晚", ["苏晚晚"], 3000)])
    short = _entry("角色卡·苏晚晚", ["苏晚晚"], 200)  # 200 字 << 1800 且短于已有

    blocked = ch.upsert_repo_worldbook(base, repo_id, [short])
    assert blocked["applied"] == 0 and blocked["skipped"] == 1
    assert any("覆盖保护拒绝写入" in w for w in blocked["warnings"])

    relaxed = ch.upsert_repo_worldbook(base, repo_id, [short],
                                       allow_shrink=True,
                                       revision_reason="新版第 12 章删掉了她的整段往事")
    assert relaxed["applied"] == 1
    assert any("删减修订放行" in w for w in relaxed["warnings"])


# ── 删除 ────────────────────────────────────────────────────────────────────


def test_delete_entries_按comment删并清台账(tmp_path):
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [_entry("角色卡·苏晚晚", ["苏晚晚"])],
                             source_chapters=[1])
    res = ch.delete_repo_entries(base, repo_id, comments=["角色卡·苏晚晚"])
    assert res["deleted"] == 1
    snap = worldbook_store.read_repo_snapshot(base, repo_id) or {}
    assert (snap.get("entries") or []) == []
    assert _ledger(base, repo_id)["entries"] == {}


def test_delete_entries_按keys删(tmp_path):
    base, repo_id = _setup(tmp_path)
    ch.upsert_repo_worldbook(base, repo_id, [
        _entry("世界背景·1. 南境", ["南境", "王都"]),
        _entry("角色卡·沈糯", ["沈糯"]),
    ])
    res = ch.delete_repo_entries(base, repo_id, keys=["南境"])
    assert res["deleted"] == 1
    snap = worldbook_store.read_repo_snapshot(base, repo_id) or {}
    comments = [e.get("comment") for e in snap.get("entries") or []]
    assert comments == ["角色卡·沈糯"]


def test_delete_entries_无参数报错(tmp_path):
    base, repo_id = _setup(tmp_path)
    with pytest.raises(ValueError, match="comments 与 keys"):
        ch.delete_repo_entries(base, repo_id)


def test_delete_entries_快照不存在报错(tmp_path):
    base, repo_id = _setup(tmp_path)
    with pytest.raises(ValueError, match="快照不存在"):
        ch.delete_repo_entries(base, repo_id, comments=["角色卡·苏晚晚"])
