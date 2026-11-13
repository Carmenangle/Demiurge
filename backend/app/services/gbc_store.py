"""GBC 场景图持久化（app.db 侧表 `asset_gbc`）。

图不塞 Chroma metadata：一张图的 graph + 三份 MOSS JSON 是几十 KB 的正文，
塞进向量库元数据会让列表/检索的读放大；本表只按 asset_id 主键取。
"""
from __future__ import annotations

import json
import time

from app.db import get_connection, write_with_retry


def _loads(raw: str) -> object:
    try:
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def save(asset_id: str, repo_id: str, *, graph: dict, moss: dict,
         moss_draft: dict, engine: dict, moss_layout: dict | None = None) -> None:
    """整行覆盖。不传 `moss_layout` 视为「这张图没有官方布局」——重跑 GBC 后
    旧布局必须跟着图一起作废，否则详情面板会拿旧文字配新框。"""
    payload = (
        asset_id,
        repo_id or "",
        json.dumps(graph, ensure_ascii=False),
        json.dumps(moss, ensure_ascii=False),
        json.dumps(moss_draft, ensure_ascii=False),
        json.dumps(moss_layout or {}, ensure_ascii=False),
        json.dumps(engine, ensure_ascii=False),
        int(time.time() * 1000),
    )

    def _op(conn):
        try:
            with conn:
                conn.execute(
                    """insert into asset_gbc
                       (asset_id, repo_id, graph_json, moss_json, moss_draft_json,
                        moss_layout_json, engine_json, created_at)
                       values (?,?,?,?,?,?,?,?)
                       on conflict(asset_id) do update set
                         repo_id = excluded.repo_id,
                         graph_json = excluded.graph_json,
                         moss_json = excluded.moss_json,
                         moss_draft_json = excluded.moss_draft_json,
                         moss_layout_json = excluded.moss_layout_json,
                         engine_json = excluded.engine_json,
                         created_at = excluded.created_at""",
                    payload,
                )
        finally:
            conn.close()

    write_with_retry(_op)


def save_layout(asset_id: str, payload: dict) -> bool:
    """只更新官方布局（GBC 图不动）。行不存在返回 False（布局依赖已有的图）。"""
    def _op(conn):
        try:
            with conn:
                cur = conn.execute(
                    "update asset_gbc set moss_layout_json = ? where asset_id = ?",
                    (json.dumps(payload, ensure_ascii=False), asset_id),
                )
                changed = cur.rowcount > 0
        finally:
            conn.close()
        return changed

    return bool(write_with_retry(_op))


def get(asset_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "select * from asset_gbc where asset_id = ?", (asset_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "asset_id": row["asset_id"],
        "repo_id": row["repo_id"],
        "graph": _loads(row["graph_json"]),
        "moss": _loads(row["moss_json"]),
        "moss_draft": _loads(row["moss_draft_json"]),
        "moss_layout": _loads(row["moss_layout_json"]),
        "engine": _loads(row["engine_json"]),
        "created_at": row["created_at"],
    }


def drop(asset_id: str) -> None:
    def _op(conn):
        try:
            with conn:
                conn.execute("delete from asset_gbc where asset_id = ?", (asset_id,))
        finally:
            conn.close()

    write_with_retry(_op)
