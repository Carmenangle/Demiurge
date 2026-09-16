"""table 路由的行级端点：接线契约（直测路由函数，无需起 TestClient）。

2026-09-14 审计 C5：`rows`/`update`/`delete` 三个端点此前各自手写
「load → apply_ops → if n: save」；收口后一律走 `table_store.mutate`。
本文件按**路由出口**锁两件事：
1. 三个端点行为不变（增/改/删都生效且落盘、都回吐更新后的表）；
2. 三个端点**确实调了**唯一事务接缝 `mutate`（防止有人改回路由层内联编排——
   那正是 C5 要治的分层漂移，且不会让任何数据面用例变红）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import table as table_router
from app.services import table_store as ts


def _tpl() -> dict:
    return {
        "sheet_a": {
            "name": "背包物品表",
            "sourceData": {"note": "记录物品"},
            "content": [["row_id", "物品名称", "数量", "类别"]],
        },
    }


def _seed(tmp_path) -> tuple[str, str]:
    base, rid = str(tmp_path), "r"
    ts.import_template(base, rid, _tpl(), replace=True)
    return base, rid


def test_rows_端点新增行并落盘(tmp_path):
    base, rid = _seed(tmp_path)
    got = table_router.add_row(table_router.RowRequest(
        output_dir=base, repo_id=rid, table="背包物品表",
        values={"物品名称": "符纸", "数量": "10"}))
    assert got["ok"] is True
    assert got["tables"][0]["rows"][0][0] == "符纸"
    # 回吐的表就是落盘内容（不是一个「内存里的表」）
    assert ts.load(base, rid)[0]["rows"][0][0] == "符纸"


def test_update_端点改单元格(tmp_path):
    base, rid = _seed(tmp_path)
    table_router.add_row(table_router.RowRequest(
        output_dir=base, repo_id=rid, table="背包物品表",
        values={"物品名称": "符纸", "数量": "10"}))
    got = table_router.update_row(table_router.UpdateRequest(
        output_dir=base, repo_id=rid, table="背包物品表", row=0, values={"数量": "3"}))
    assert got["tables"][0]["rows"][0][1] == "3"
    assert ts.load(base, rid)[0]["rows"][0][1] == "3"


def test_delete_端点删行(tmp_path):
    base, rid = _seed(tmp_path)
    for name in ("符纸", "丹药"):
        table_router.add_row(table_router.RowRequest(
            output_dir=base, repo_id=rid, table="背包物品表", values={"物品名称": name}))
    got = table_router.delete_row(table_router.DeleteRequest(
        output_dir=base, repo_id=rid, table="背包物品表", row=0))
    assert [r[0] for r in got["tables"][0]["rows"]] == ["丹药"]
    assert [r[0] for r in ts.load(base, rid)[0]["rows"]] == ["丹药"]


def test_三个端点都走唯一事务接缝(tmp_path, monkeypatch):
    """C5 接线锁：ops 形状与调用次数都从 `mutate` 上断言，而不是从落盘结果反推。"""
    base, rid = _seed(tmp_path)
    seen: list[dict] = []
    real = ts.mutate

    def spy(b, r, ops):
        seen.append({"base": b, "repo_id": r, "ops": ops})
        return real(b, r, ops)

    monkeypatch.setattr(table_router.table_store, "mutate", spy)

    table_router.add_row(table_router.RowRequest(
        output_dir=base, repo_id=rid, table="背包物品表", values={"物品名称": "符纸"}))
    table_router.update_row(table_router.UpdateRequest(
        output_dir=base, repo_id=rid, table="背包物品表", row=0, values={"数量": "3"}))
    table_router.delete_row(table_router.DeleteRequest(
        output_dir=base, repo_id=rid, table="背包物品表", row=0))

    assert len(seen) == 3
    assert all(c["base"] == base and c["repo_id"] == rid for c in seen)
    assert [c["ops"][0]["op"] for c in seen] == ["insert", "update", "delete"]
    assert seen[1]["ops"][0]["row"] == 0 and seen[1]["ops"][0]["values"] == {"数量": "3"}


def test_三端点缺repo_id仍400(tmp_path):
    """校验仍在路由层（mutate 不做 400）；没库也不能静默写。"""
    for call in (
        lambda: table_router.add_row(table_router.RowRequest(
            output_dir=str(tmp_path), repo_id="", table="背包物品表", values={})),
        lambda: table_router.update_row(table_router.UpdateRequest(
            output_dir=str(tmp_path), repo_id="", table="背包物品表", row=0, values={})),
        lambda: table_router.delete_row(table_router.DeleteRequest(
            output_dir=str(tmp_path), repo_id="", table="背包物品表", row=0)),
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 400


# ── 结构端点（create / drop / set-meta）的特征测试 + 唯一结构接缝（P6） ──────
#
# 2026-09-15 审计 P6：这三个端点此前各自手写「load → 改 → (if 有变化) save」，
# 与行级接缝 `mutate` 并存 ⇒「无变化不落盘」有两个属主。收进 `table_store.mutate_structure` 后，
# 路由只剩「拼 op + 回吐表」。
#
# **no-op 口径是刻意不同的产品策略（2026-09-15 用户定案「保留现状」）**：
#   /create    无变化 → **400**「表名重复或没有有效列」（前端 TableModal 会显示「建表失败：…」）
#   /drop      无变化 → **静默 200**（表不存在 → 什么都不做，不报错）
#   /set-meta  无变化 → **静默 200**（同上）
# 三者共同的不变量是 **无变化一律不落盘**。下面两组用例把这两件事分别钉住：
# 「行为」组（no-op 口径 + 不写盘）与「接线」组（确实走了唯一结构接缝）。


def _file_text(base: str, rid: str) -> str | None:
    """直接读盘：判「有没有真的写盘」用文件文本比对（比 mtime 稳）。"""
    p = ts.tables_path(base, rid)
    return p.read_text(encoding="utf-8") if p.is_file() else None


def test_create_成功建表并落盘(tmp_path):
    base, rid = _seed(tmp_path)
    got = table_router.create_table(table_router.CreateRequest(
        output_dir=base, repo_id=rid, name="自建表", columns=["甲", "乙"], note="说明"))
    assert got["ok"] is True
    assert [t["name"] for t in got["tables"]][-1] == "自建表"
    assert [t["name"] for t in ts.load(base, rid)][-1] == "自建表"


def test_create_重名或无列时400且不写盘(tmp_path):
    """no-op 口径：create 用 **400** 表达 no-op；且**一次都不写盘**。"""
    base, rid = _seed(tmp_path)
    before = _file_text(base, rid)
    for req in (
        table_router.CreateRequest(output_dir=base, repo_id=rid, name="背包物品表", columns=["甲"]),
        table_router.CreateRequest(output_dir=base, repo_id=rid, name="新表", columns=[]),
    ):
        with pytest.raises(HTTPException) as ei:
            table_router.create_table(req)
        assert ei.value.status_code == 400
        assert ei.value.detail == "表名重复或没有有效列"
    assert _file_text(base, rid) == before      # 未写盘


def test_drop_存在的表生效并落盘(tmp_path):
    base, rid = _seed(tmp_path)
    got = table_router.drop_table(table_router.DropRequest(
        output_dir=base, repo_id=rid, table="背包物品表"))
    assert got["ok"] is True
    assert "背包物品表" not in [t["name"] for t in got["tables"]]
    assert "背包物品表" not in [t["name"] for t in ts.load(base, rid)]


def test_drop_表不存在时静默200且不写盘(tmp_path):
    """no-op 口径：drop 用 **静默 200**（不报错、不写盘）表达 no-op。"""
    base, rid = _seed(tmp_path)
    before = _file_text(base, rid)
    got = table_router.drop_table(table_router.DropRequest(
        output_dir=base, repo_id=rid, table="不存在的表"))
    assert got["ok"] is True
    assert _file_text(base, rid) == before


def test_set_meta_存在的表生效并落盘(tmp_path):
    base, rid = _seed(tmp_path)
    got = table_router.set_meta(table_router.MetaRequest(
        output_dir=base, repo_id=rid, table="背包物品表", note="改过的说明", mode="retrieval"))
    tbl = next(t for t in got["tables"] if t["name"] == "背包物品表")
    assert tbl["note"] == "改过的说明" and tbl["mode"] == "retrieval"
    assert next(t for t in ts.load(base, rid) if t["name"] == "背包物品表")["note"] == "改过的说明"


def test_set_meta_表不存在时静默200且不写盘(tmp_path):
    """no-op 口径：set-meta 用 **静默 200**（不报错、不写盘）表达 no-op。"""
    base, rid = _seed(tmp_path)
    before = _file_text(base, rid)
    got = table_router.set_meta(table_router.MetaRequest(
        output_dir=base, repo_id=rid, table="不存在的表", note="x"))
    assert got["ok"] is True
    assert _file_text(base, rid) == before


def test_三个结构端点都走唯一结构接缝(tmp_path, monkeypatch):
    """P6 接线锁：三个结构端点的「load → 改 → 有变化才落盘」必须走 `mutate_structure`。

    同时覆盖**有变化**与**no-op**两条分支（create 成功 / drop 命中 / set-meta 命中 / drop 落空），
    防止有人改回路由层内联编排——那正是 P6 要治的分层漂移，且**不会让任何行为用例变红**。
    """
    base, rid = _seed(tmp_path)
    seen: list[dict] = []
    real = ts.mutate_structure

    def spy(b, r, apply_fn):
        seen.append({"base": b, "repo_id": r})
        return real(b, r, apply_fn)

    monkeypatch.setattr(table_router.table_store, "mutate_structure", spy)

    table_router.create_table(table_router.CreateRequest(
        output_dir=base, repo_id=rid, name="自建表", columns=["甲"]))
    table_router.drop_table(table_router.DropRequest(
        output_dir=base, repo_id=rid, table="自建表"))
    table_router.set_meta(table_router.MetaRequest(
        output_dir=base, repo_id=rid, table="背包物品表", note="x"))
    table_router.drop_table(table_router.DropRequest(          # no-op 分支也要过接缝
        output_dir=base, repo_id=rid, table="不存在的表"))

    assert len(seen) == 4
    assert all(c["base"] == base and c["repo_id"] == rid for c in seen)
