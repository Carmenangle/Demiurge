"""narrative.read_chronicle 能力回归（固化04 纪读取数口，2026-09-09 用户定案 B）。

覆盖：注册字段（narrative 分类/readonly/env 注入登记）/ 缺库不建库不报错 /
按 rowid 倒序取最近 N 条且字段完整 / limit 夹取 / 参数域校验（base 缺失拒绝）。
"""
from __future__ import annotations

import pytest

from app.services import capability_handlers as ch
from app.services import capability_registry as cr
from app.services import narrative_store as ns
from app.services.narrative_memory import ChronicleEntry


def test_已注册且分级readonly_环境注入登记():
    cap = cr.get("narrative.read_chronicle")
    assert cap is not None
    assert cap.category == "narrative"
    assert cap.side_effect_level == cr.SIDE_EFFECT_READONLY
    assert cap.channel == cr.CHANNEL_SYNC
    assert cap.needs_model is None
    assert "narrative" in cr.CATEGORIES
    assert cr.env_injected_params("narrative.read_chronicle") == ("base", "repo_id")
    assert cap.handler == "app.services.capability_handlers:read_chronicle"


def _seed(base, repo_id: str = "sim-repo", n: int = 3) -> None:
    for i in range(1, n + 1):
        entry = ChronicleEntry(
            text=f"第 {i} 段纪要：主角推进事件 {i}。",
            turn_start=i * 3, turn_end=i * 3 + 2, layer=0,
            overview=f"事件{i}", characters=["沈栖", "Helia"], keywords=["事件"])
        ns.append(str(base), repo_id, entry)


def test_按rowid倒序取最近limit条(tmp_path):
    _seed(tmp_path, n=5)
    out = ch.read_chronicle(base=str(tmp_path), repo_id="sim-repo", limit=2)
    assert out["found"] is True and out["count"] == 2 and out["limit"] == 2
    # recent = rowid 倒序 → 最新两条
    assert [e["rowid"] for e in out["entries"]] == [5, 4]
    first = out["entries"][0]
    assert first["text"].startswith("第 5 段") and first["turn_end"] == 17
    assert first["characters"] == ["沈栖", "Helia"] and first["layer"] == 0
    assert first["overview"] and first["keywords"]


def test_纪要不存在的作品found_false_不建库不报错(tmp_path):
    out = ch.read_chronicle(base=str(tmp_path / "no-such"), repo_id="x")
    assert out["found"] is False and out["count"] == 0 and out["entries"] == []
    assert not ns.db_path(str(tmp_path / "no-such"), "x").exists()  # 只读：不建库


def test_limit夹取1到100(tmp_path):
    _seed(tmp_path)
    assert ch.read_chronicle(base=str(tmp_path), repo_id="sim-repo", limit=0)["limit"] == 1
    assert ch.read_chronicle(base=str(tmp_path), repo_id="sim-repo",
                             limit=999)["limit"] == 100
    assert ch.read_chronicle(base=str(tmp_path), repo_id="sim-repo",
                             limit=None)["limit"] == 30


def test_缺base或repo_id拒绝(tmp_path):
    with pytest.raises(ValueError, match="环境归一注入"):
        ch.read_chronicle(repo_id="sim-repo")
    with pytest.raises(ValueError, match="环境归一注入"):
        ch.read_chronicle(base=str(tmp_path))
