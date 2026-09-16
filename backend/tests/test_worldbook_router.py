"""世界书条目级 CRUD 路由：行为特征 + 接线契约（直测路由函数，无需起 TestClient）。

2026-09-15 审计 P7：同一形状「读 book → `worldbook_edit` 改一条 → 写回」此前有**两套写法**：
- `routers/worldbook.py`：`_read_book`/`_write_book` helper（独立世界书 / 卡内嵌两定位）；
- `routers/narrative.py`：三个 repo 快照端点**内联**同一形状，落盘条件与 404 各自手写。
收口后两处都走唯一事务接缝 `worldbook_store.mutate_entries(load, save, apply)`。

**刻意不统一、必须逐字保留的东西**（两套端点的失败文案本就不同，收口不得抹平）：
- 定位缺失 → 400「缺少世界书定位（base+name 或 character_dir+card_name）」
- 目标不存在 → 404「世界书不存在」/「该卡没有内嵌世界书」
  / 快照 404「快照不存在，请先绑定世界书并运行至少一次剧情」（add）/「快照不存在」（update·delete）
- 条目越界 → 404「条目不存在」（worldbook）/「条目索引越界」（narrative 快照）

本文件按**路由出口**锁两件事：
1. 七个端点行为不变（三定位都能读写、越界 404 且**一个字节都不落盘**、文案逐字）；
2. 六个变更端点**确实调了**唯一条目接缝（防止有人改回路由层内联编排——
   那正是 P7 要治的分层漂移，且不会让任何数据面用例变红）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers import narrative as narrative_router
from app.routers import worldbook as worldbook_router
from app.services import character_store, worldbook_store
from app.services.pathnames import safe_dir

CARD = "塞西莉亚"
BOOK = "测试书"


# ── 夹具与读盘辅助 ──────────────────────────────────────────────────────────


def _book(entries: list[dict] | None = None) -> dict:
    return {"entries": entries if entries is not None else [
        {"content": "常驻设定", "comment": "世界观", "keys": ["世界"], "constant": True, "uid": 0},
        {"content": "魔法体系", "comment": "魔法", "keys": ["魔法"], "uid": 1},
    ]}


def _seed_standalone(tmp_path, entries: list[dict] | None = None) -> str:
    base = str(tmp_path)
    worldbook_store.save(base, BOOK, _book(entries))
    return base


def _seed_card(tmp_path) -> str:
    base = str(tmp_path)
    character_store.write_worldbook(base, CARD, _book())
    return base


def _standalone_file(base: str) -> Path:
    return Path(base) / f"{safe_dir(BOOK)}.json"


def _card_file(base: str) -> Path:
    return character_store.card_dir(base, CARD) / character_store.WORLDBOOK_FILE


def _text(p: Path) -> str:
    """判「有没有真的写盘」用**文件文本前后比对**（比 mtime 稳）。"""
    return p.read_text(encoding="utf-8")


def _raw_entries(p: Path) -> list[dict]:
    return json.loads(p.read_text(encoding="utf-8"))["entries"]


def _standalone_loc(base: str) -> "worldbook_router.EntryLocation":
    return worldbook_router.EntryLocation(base=base, name=BOOK)


def _card_loc(base: str) -> "worldbook_router.EntryLocation":
    return worldbook_router.EntryLocation(character_dir=base, card_name=CARD)


def _entry_payload(content: str = "新条目", **over) -> "worldbook_router.EntryFields":
    fields = {"content": content, "comment": "新", "keys": ["新"], "constant": False, "enabled": True}
    fields.update(over)
    return worldbook_router.EntryFields(**fields)


def _snapshot_file(base: str, rid: str = "r") -> Path:
    return worldbook_store.repo_snapshot_path(base, rid)


def _seed_snapshot(tmp_path, rid: str = "r") -> str:
    base = str(tmp_path)
    worldbook_store.ensure_repo_snapshot(base, rid, [_book()])
    return base


# ── /worldbook/entries 列出（只读，不落盘）──────────────────────────────────


def test_entries_独立世界书列出条目(tmp_path):
    base = _seed_standalone(tmp_path)
    got = worldbook_router.list_entries(_standalone_loc(base))
    assert [e["index"] for e in got["entries"]] == [0, 1]
    assert got["entries"][0]["content"] == "常驻设定"
    assert got["entries"][0]["constant"] is True


def test_entries_卡内嵌世界书列出条目(tmp_path):
    base = _seed_card(tmp_path)
    got = worldbook_router.list_entries(_card_loc(base))
    assert [e["content"] for e in got["entries"]] == ["常驻设定", "魔法体系"]


def test_entries_缺定位报400且文案逐字(tmp_path):
    with pytest.raises(HTTPException) as ei:
        worldbook_router.list_entries(worldbook_router.EntryLocation())
    assert ei.value.status_code == 400
    assert ei.value.detail == "缺少世界书定位（base+name 或 character_dir+card_name）"


def test_entries_独立世界书不存在报404且文案逐字(tmp_path):
    with pytest.raises(HTTPException) as ei:
        worldbook_router.list_entries(_standalone_loc(str(tmp_path)))
    assert ei.value.status_code == 404
    assert ei.value.detail == "世界书不存在"


def test_entries_卡无内嵌世界书报404且文案逐字(tmp_path):
    with pytest.raises(HTTPException) as ei:
        worldbook_router.list_entries(_card_loc(str(tmp_path)))
    assert ei.value.status_code == 404
    assert ei.value.detail == "该卡没有内嵌世界书"


# ── /worldbook/entry/add ────────────────────────────────────────────────────


def test_add_entry_独立世界书返回index并落盘(tmp_path):
    base = _seed_standalone(tmp_path)
    got = worldbook_router.add_entry(worldbook_router.EntryAddRequest(
        **_standalone_loc(base).model_dump(), entry=_entry_payload("第三")))
    assert got == {"ok": True, "index": 2}
    assert _raw_entries(_standalone_file(base))[2]["content"] == "第三"


def test_add_entry_空书首条index为0也必须落盘(tmp_path):
    """falsy 陷阱：首条新增的 index 就是 0，落盘判定**不能**用 `if index`。"""
    base = _seed_standalone(tmp_path, entries=[])
    got = worldbook_router.add_entry(worldbook_router.EntryAddRequest(
        **_standalone_loc(base).model_dump(), entry=_entry_payload("首条")))
    assert got == {"ok": True, "index": 0}
    assert [e["content"] for e in _raw_entries(_standalone_file(base))] == ["首条"]


def test_add_entry_卡内嵌写回卡文件(tmp_path):
    base = _seed_card(tmp_path)
    got = worldbook_router.add_entry(worldbook_router.EntryAddRequest(
        **_card_loc(base).model_dump(), entry=_entry_payload("卡内新增")))
    assert got["index"] == 2
    assert _raw_entries(_card_file(base))[2]["content"] == "卡内新增"


# ── /worldbook/entry/update ────────────────────────────────────────────────


def test_update_entry_改字段并落盘(tmp_path):
    base = _seed_standalone(tmp_path)
    got = worldbook_router.update_entry(worldbook_router.EntryUpdateRequest(
        **_standalone_loc(base).model_dump(), index=1, entry=_entry_payload("改后的魔法", enabled=False)))
    assert got == {"ok": True}
    entry = _raw_entries(_standalone_file(base))[1]
    assert entry["content"] == "改后的魔法"
    assert entry["enabled"] is False and entry["disable"] is True   # 双写兼容


def test_update_entry_越界404且文案逐字且不落盘(tmp_path):
    base = _seed_standalone(tmp_path)
    before = _text(_standalone_file(base))
    with pytest.raises(HTTPException) as ei:
        worldbook_router.update_entry(worldbook_router.EntryUpdateRequest(
            **_standalone_loc(base).model_dump(), index=9, entry=_entry_payload("越界")))
    assert ei.value.status_code == 404
    assert ei.value.detail == "条目不存在"
    assert _text(_standalone_file(base)) == before                  # 越界不落盘


# ── /worldbook/entry/delete ────────────────────────────────────────────────


def test_delete_entry_删条目并落盘(tmp_path):
    base = _seed_standalone(tmp_path)
    got = worldbook_router.delete_entry(worldbook_router.EntryDeleteRequest(
        **_standalone_loc(base).model_dump(), index=0))
    assert got == {"ok": True}
    assert [e["content"] for e in _raw_entries(_standalone_file(base))] == ["魔法体系"]


def test_delete_entry_越界404且文案逐字且不落盘(tmp_path):
    base = _seed_card(tmp_path)
    before = _text(_card_file(base))
    with pytest.raises(HTTPException) as ei:
        worldbook_router.delete_entry(worldbook_router.EntryDeleteRequest(
            **_card_loc(base).model_dump(), index=9))
    assert ei.value.status_code == 404
    assert ei.value.detail == "条目不存在"
    assert _text(_card_file(base)) == before


# ── repo 快照三端点（narrative.py，内联同一形状）────────────────────────────


def _add_payload(content: str = "新条目") -> "narrative_router.RepoWorldbookEntryPayload":
    return narrative_router.RepoWorldbookEntryPayload(
        content=content, comment="新", keys=["新"], constant=False, enabled=True)


def test_repo_entry_add_新增并落盘(tmp_path):
    base = _seed_snapshot(tmp_path)
    got = narrative_router.repo_worldbook_entry_add(narrative_router.RepoWorldbookEntryAdd(
        output_dir=base, repo_id="r", entry=_add_payload("快照新增")))
    assert got == {"ok": True, "index": 2}
    assert _raw_entries(_snapshot_file(base))[2]["content"] == "快照新增"


def test_repo_entry_add_快照不存在404且文案逐字(tmp_path):
    with pytest.raises(HTTPException) as ei:
        narrative_router.repo_worldbook_entry_add(narrative_router.RepoWorldbookEntryAdd(
            output_dir=str(tmp_path), repo_id="r", entry=_add_payload()))
    assert ei.value.status_code == 404
    assert ei.value.detail == "快照不存在，请先绑定世界书并运行至少一次剧情"


def test_repo_entry_update_改字段并落盘(tmp_path):
    base = _seed_snapshot(tmp_path)
    got = narrative_router.repo_worldbook_entry_update(narrative_router.RepoWorldbookEntryUpdate(
        output_dir=base, repo_id="r", index=1, entry=_add_payload("快照改后")))
    assert got == {"ok": True}
    assert _raw_entries(_snapshot_file(base))[1]["content"] == "快照改后"


def test_repo_entry_越界404且文案逐字且不落盘(tmp_path):
    """越界文案与 worldbook 端点**刻意不同**（「条目索引越界」≠「条目不存在」），且不落盘。"""
    base = _seed_snapshot(tmp_path)
    before = _text(_snapshot_file(base))
    for call in (
        lambda: narrative_router.repo_worldbook_entry_update(
            narrative_router.RepoWorldbookEntryUpdate(
                output_dir=base, repo_id="r", index=9, entry=_add_payload("越界"))),
        lambda: narrative_router.repo_worldbook_entry_delete(
            narrative_router.RepoWorldbookEntryDelete(output_dir=base, repo_id="r", index=9)),
    ):
        with pytest.raises(HTTPException) as ei:
            call()
        assert ei.value.status_code == 404
        assert ei.value.detail == "条目索引越界"
    assert _text(_snapshot_file(base)) == before


def test_repo_entry_delete_删条目并落盘(tmp_path):
    base = _seed_snapshot(tmp_path)
    got = narrative_router.repo_worldbook_entry_delete(narrative_router.RepoWorldbookEntryDelete(
        output_dir=base, repo_id="r", index=0))
    assert got == {"ok": True}
    assert [e["content"] for e in _raw_entries(_snapshot_file(base))] == ["魔法体系"]


def test_repo_entry_update_delete_快照不存在404且文案逐字(tmp_path):
    for call in (
        lambda: narrative_router.repo_worldbook_entry_update(
            narrative_router.RepoWorldbookEntryUpdate(
                output_dir=str(tmp_path), repo_id="r", index=0, entry=_add_payload())),
        lambda: narrative_router.repo_worldbook_entry_delete(
            narrative_router.RepoWorldbookEntryDelete(output_dir=str(tmp_path), repo_id="r", index=0)),
    ):
        with pytest.raises(HTTPException) as ei:
            call()
        assert ei.value.status_code == 404
        assert ei.value.detail == "快照不存在"


# ── 唯一事务接缝接线锁（P7）────────────────────────────────────────────────


def test_六个变更端点都走唯一条目接缝(tmp_path, monkeypatch):
    """P7 接线锁：add/update/delete 都要过 `worldbook_store.mutate_entries`。

    三定位（独立世界书 / 卡内嵌 / repo 快照）全在锁内，且覆盖**有变化**与
    **no-op（越界）**两条分支——两条都必须经过接缝，否则「越界不落盘」又会退回
    路由层手写。`/entries` 是只读端点，**不**进本锁。
    """
    base = _seed_standalone(tmp_path)
    card_base = _seed_card(tmp_path / "card")
    snap_base = _seed_snapshot(tmp_path / "snap")

    seen: list[dict] = []
    real = worldbook_store.mutate_entries

    def spy(load, save, apply):
        seen.append({"load": load, "save": save, "apply": apply})
        return real(load, save, apply)

    monkeypatch.setattr(worldbook_store, "mutate_entries", spy)

    worldbook_router.add_entry(worldbook_router.EntryAddRequest(
        **_standalone_loc(base).model_dump(), entry=_entry_payload("甲")))
    worldbook_router.update_entry(worldbook_router.EntryUpdateRequest(
        **_standalone_loc(base).model_dump(), index=0, entry=_entry_payload("乙")))
    worldbook_router.delete_entry(worldbook_router.EntryDeleteRequest(
        **_standalone_loc(base).model_dump(), index=0))
    worldbook_router.add_entry(worldbook_router.EntryAddRequest(
        **_card_loc(card_base).model_dump(), entry=_entry_payload("卡内")))
    narrative_router.repo_worldbook_entry_add(narrative_router.RepoWorldbookEntryAdd(
        output_dir=snap_base, repo_id="r", entry=_add_payload("快照")))
    narrative_router.repo_worldbook_entry_update(narrative_router.RepoWorldbookEntryUpdate(
        output_dir=snap_base, repo_id="r", index=0, entry=_add_payload("快照改")))

    assert len(seen) == 6
    # no-op 分支也要过接缝：越界 update 仍调用接缝（由接缝的 changed=False 决定不写盘）
    n_before = len(seen)
    with pytest.raises(HTTPException):
        narrative_router.repo_worldbook_entry_delete(narrative_router.RepoWorldbookEntryDelete(
            output_dir=snap_base, repo_id="r", index=9))
    assert len(seen) == n_before + 1
