"""资产主表（SQLite）：元数据真相 + 回收站 + FTS5 结构化搜索（PLAN §P1-1/2/3）。

为什么要它：资产库此前的「元数据真相寄居在向量库」——列表全量 dump、标签统计全扫、
删除硬删无回收站。本模块把**元数据**搬进 SQLite（Chroma 只保留向量职责）：
- 列表 / keyset 分页 / 标签统计 / 结构化搜索（FTS5 + asset_tags 精确 AND）；
- 回收站：软删除（deleted_at）+ 恢复（返回行供重建向量）+ 彻底删除 / 清空。

双写纪律：`rag_store` 写 Chroma 后调 `upsert_from_generation`（失败只 warning，
不阻断入库；`scripts/backfill_asset_db.py` 可补）。读路径以本表为准；
语义向量检索仍走 Chroma hybrid（互不替代，PLAN P1-3「结构化 AND 语义可并存」）。
"""
from __future__ import annotations

import json
import re
import time

from app.db import get_connection, write_with_retry
from app.services import thumbnail_store

# 读侧 camelCase 契约与本表字段的映射见 _row_to_item（形状必须与
# rag_store.list_generations 逐键一致，前端 GenLike 解码依赖它）。


# ─────────────────────────── 工具 ───────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_list(raw) -> list:
    """tags_json / derived_from → list；兼容历史裸逗号串与损坏 JSON。"""
    if isinstance(raw, list):
        return raw
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
    except (ValueError, TypeError):
        pass
    return [t.strip() for t in text.split(",") if t.strip()]


def _parse_json_dict(raw) -> dict:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _fresh_thumb_url(stored: str, image_url: str) -> str:
    """缩略图 URL 取用（与 rag_store 同规则）：元数据指向已不存在文件 ⇒ 丢弃，
    回退「只查不生成」的派生发现。"""
    thumb = (stored or "").strip()
    if thumb and thumbnail_store.local_path_of_url(thumb) is None:
        thumb = ""
    return thumb or thumbnail_store.thumb_url_if_ready(image_url)


def _row_to_item(row) -> dict:
    """SQLite 行 → 前端 GenLike（键名/类型与 rag_store.list_generations 逐键一致）。"""
    tags = _json_list(row["tags_json"])
    return {
        "id": row["id"],
        "repo_id": row["repo_id"],
        "prompt": row["prompt"],
        "description": row["description"],
        "image_url": row["image_url"],
        "tags": tags,
        "templateName": row["template_name"],
        "modelName": row["model_name"],
        "loraNames": [s for s in (row["lora_names"] or "").split(",") if s.strip()],
        "mediaType": row["media_type"] or "image",
        "thumbUrl": _fresh_thumb_url(row["thumb_url"], row["image_url"]),
        "derivedFrom": _json_list(row["derived_from"]),
        "source": row["source"] or "",
        "sourceUrl": row["source_url"] or "",
        "sourceMeta": _parse_json_dict(row["source_meta"]),
        "created_at": int(row["created_at"] or 0),
    }


def _placeholders(values) -> str:
    return ",".join("?" for _ in values)


# ─────────────────────────── 写入 ───────────────────────────

def upsert_asset(item: dict) -> None:
    """幂等 upsert（id 冲突覆盖元数据；**不复活已软删行**——deleted_at 保留）。"""
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.split(",") if t.strip()]
    tags = [str(t).strip() for t in tags if str(t).strip()]
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                insert into assets (
                    id, repo_id, kind, media_type, source, image_url, thumb_url,
                    prompt, description, tags_json, template_name, model_name,
                    lora_names, created_at, deleted_at, source_url, source_meta, derived_from
                ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                on conflict(id) do update set
                    repo_id=excluded.repo_id, kind=excluded.kind,
                    media_type=excluded.media_type, source=excluded.source,
                    image_url=excluded.image_url, thumb_url=excluded.thumb_url,
                    prompt=excluded.prompt, description=excluded.description,
                    tags_json=excluded.tags_json,
                    template_name=excluded.template_name, model_name=excluded.model_name,
                    lora_names=excluded.lora_names, created_at=excluded.created_at,
                    source_url=excluded.source_url, source_meta=excluded.source_meta,
                    derived_from=excluded.derived_from
                    -- deleted_at 刻意不覆盖：回填/重建索引不得复活回收站里的记录
                """,
                (
                    item["id"], item.get("repo_id") or "", item.get("kind") or "generation",
                    item.get("media_type") or "image", item.get("source") or "",
                    item.get("image_url") or "", item.get("thumb_url") or "",
                    item.get("prompt") or "", item.get("description") or "",
                    json.dumps(tags, ensure_ascii=False),
                    item.get("template_name") or "", item.get("model_name") or "",
                    item.get("lora_names") or "", int(item.get("created_at") or 0),
                    item.get("deleted_at"),
                    item.get("source_url") or "", item.get("source_meta") or "",
                    item.get("derived_from") or "[]",
                ),
            )
            conn.execute("delete from asset_tags where asset_id = ?", (item["id"],))
            conn.executemany(
                "insert or ignore into asset_tags (asset_id, tag, repo_id) values (?,?,?)",
                [(item["id"], t, item.get("repo_id") or "") for t in tags],
            )
    finally:
        conn.close()


def upsert_from_generation(repo_id: str, metadata: dict, doc_id: str) -> None:
    """Chroma generation metadata → 主表（双写入口；键名沿用 metadata 真源）。"""
    meta = metadata or {}
    upsert_asset({
        "id": doc_id,
        "repo_id": meta.get("repo_id") or repo_id or "",
        "kind": meta.get("kind") or "generation",
        "media_type": meta.get("media_type") or "image",
        "source": meta.get("source") or "",
        "image_url": meta.get("image_url") or "",
        "thumb_url": meta.get("thumb_url") or "",
        "prompt": meta.get("prompt") or "",
        "description": meta.get("description") or "",
        "tags": [t for t in (meta.get("tags") or "").split(",") if t.strip()],
        "template_name": meta.get("template_name") or "",
        "model_name": meta.get("model_name") or "",
        "lora_names": meta.get("lora_names") or "",
        "created_at": int(meta.get("created_at") or 0),
        "source_url": meta.get("source_url") or "",
        "source_meta": meta.get("source_meta") or "",
        "derived_from": meta.get("derived_from") or "[]",
    })


def sync_tags(doc_id: str, tags: list[str]) -> bool:
    """标签改动同步（asset_tags 精确过滤 + FTS 重建由 upsert 触发）。"""
    conn = get_connection()
    try:
        with conn:
            row = conn.execute("select * from assets where id = ?", (doc_id,)).fetchone()
            if row is None:
                return False
            conn.execute(
                "update assets set tags_json = ? where id = ?",
                (json.dumps(tags, ensure_ascii=False), doc_id),
            )
            conn.execute("delete from asset_tags where asset_id = ?", (doc_id,))
            conn.executemany(
                "insert or ignore into asset_tags (asset_id, tag, repo_id) values (?,?,?)",
                [(doc_id, str(t).strip(), row["repo_id"]) for t in tags if str(t).strip()],
            )
        return True
    finally:
        conn.close()


def sync_description(doc_id: str, description: str) -> bool:
    """写描述到主表（批量描述后台任务并发写：走 write_with_retry 兜 WAL 竞态）。"""
    def _op(conn):
        try:
            with conn:
                cur = conn.execute(
                    "update assets set description = ? where id = ?",
                    (description or "", doc_id),
                )
            return cur.rowcount > 0
        finally:
            conn.close()
    return bool(write_with_retry(_op))


def delete_rows(ids: list[str]) -> int:
    """硬删（prune/dedup 对账用——破损记录不进回收站）。"""
    if not ids:
        return 0
    conn = get_connection()
    try:
        with conn:
            conn.execute(f"delete from assets where id in ({_placeholders(ids)})", ids)
            conn.execute(f"delete from asset_tags where asset_id in ({_placeholders(ids)})", ids)
        return len(ids)
    finally:
        conn.close()


# ─────────────────────────── 回收站（P1-2） ───────────────────────────

def soft_delete(doc_id: str) -> bool:
    """置 deleted_at（幂等：已软删保留原时间）。"""
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                "update assets set deleted_at = coalesce(deleted_at, ?) where id = ?",
                (_now_ms(), doc_id),
            )
        return cur.rowcount > 0
    finally:
        conn.close()


def restore(doc_id: str) -> dict | None:
    """清 deleted_at 并返回该行（供调用方重建 Chroma 向量）；不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute("select * from assets where id = ?", (doc_id,)).fetchone()
        if row is None:
            return None
        with conn:
            conn.execute("update assets set deleted_at = null where id = ?", (doc_id,))
        return dict(row)
    finally:
        conn.close()


def purge(doc_id: str) -> bool:
    """彻底删除单条（回收站内）。"""
    return delete_rows([doc_id]) > 0


def clear_trash(repo_ids: list[str]) -> int:
    """清空回收站（可按仓库范围；空列表＝全部）。"""
    conn = get_connection()
    try:
        where = "deleted_at is not null"
        args: list = []
        if repo_ids:
            where += f" and repo_id in ({_placeholders(repo_ids)})"
            args = list(repo_ids)
        ids = [r["id"] for r in conn.execute(f"select id from assets where {where}", args)]
        with conn:
            if ids:
                conn.execute(f"delete from assets where id in ({_placeholders(ids)})", ids)
                conn.execute(f"delete from asset_tags where asset_id in ({_placeholders(ids)})", ids)
        return len(ids)
    finally:
        conn.close()


# ─────────────────────────── 读取（P1-1 读路径） ───────────────────────────

def _scope_clause(repo_ids: list[str] | None, deleted: bool,
                  alias: str = "") -> tuple[str, list]:
    """作用域条件（存活/回收站 + 仓库范围）。alias 供 JOIN 查询限定列名防歧义。"""
    prefix = f"{alias}." if alias else ""
    state = "is not null" if deleted else "is null"
    clauses = [f"{prefix}deleted_at {state}"]
    args: list = []
    if repo_ids:
        clauses.append(f"{prefix}repo_id in ({_placeholders(repo_ids)})")
        args.extend(repo_ids)
    return " and ".join(clauses), args


def list_assets(repo_ids: list[str] | None, *, limit: int = 0, before_ts: int = 0,
                media_type: str = "", deleted: bool = False, offset: int = 0) -> list[dict]:
    """列表（created_at 倒序）。limit=0 ⇒ 全量。

    deleted=True ⇒ 回收站视图（按 deleted_at 倒序）。
    offset>0 ⇒ 按偏移取（2026-10-19：分页条要显示「共 N 页」并直达任意页，
    光有 keyset 游标做不到——前端不知道总页数，也只能顺序翻）。
    """
    where, args = _scope_clause(repo_ids, deleted)
    if media_type:
        where += " and media_type = ?"
        args.append(media_type)
    if before_ts > 0:
        where += " and created_at < ?"
        args.append(int(before_ts))
    order = "deleted_at desc" if deleted else "created_at desc"
    sql = f"select * from assets where {where} order by {order}"
    if limit > 0:
        sql += " limit ?"
        args.append(int(limit))
        if offset > 0:
            # SQLite 要求 offset 必须跟在 limit 后面（无 limit 的 offset 用 `limit -1`）
            sql += " offset ?"
            args.append(int(offset))
    conn = get_connection()
    try:
        return [_row_to_item(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def tag_stats(repo_ids: list[str] | None) -> list[dict]:
    """标签→数量（只统计存活记录），降序。"""
    where, args = _scope_clause(repo_ids, deleted=False, alias="a")
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            select t.tag as tag, count(*) as count
            from asset_tags t join assets a on a.id = t.asset_id
            where {where}
            group by t.tag order by count desc, t.tag asc
            """,
            args,
        ).fetchall()
        return [{"tag": r["tag"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


_FTS_TOKEN = re.compile(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]+")


def _fts_query(text: str) -> str:
    """把自由文本收拾成安全 FTS5 查询（词项加引号、空格＝AND）；无可用词返回空串。"""
    tokens = _FTS_TOKEN.findall(text or "")
    return " ".join(f'"{t}"' for t in tokens)


def search_assets(repo_ids: list[str] | None, *, q: str = "", tags: tuple[str, ...] | list[str] = (),
                  media_type: str = "", limit: int = 0, before_ts: int = 0,
                  deleted: bool = False) -> list[dict]:
    """结构化搜索（P1-3）：FTS5 全文 + LIKE 兜底 + asset_tags 精确 AND。

    - tags 多个 = AND（每张图都要带齐这些标签）；
    - q 同时命中的两种渠道：FTS5（英文标签/词，游标级快）OR LIKE（中文子串兜底）；
    - 语义检索不在这里——`/rag/search-generations` 仍走 Chroma hybrid，二者可并存。
    """
    where, args = _scope_clause(repo_ids, deleted)
    if media_type:
        where += " and media_type = ?"
        args.append(media_type)
    tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if tag_list:
        where += (
            f" and (select count(*) from asset_tags at"
            f" where at.asset_id = assets.id and at.tag in ({_placeholders(tag_list)})) = ?"
        )
        args.extend(tag_list)
        args.append(len(tag_list))
    text = (q or "").strip()
    if text:
        fts = _fts_query(text)
        like = f"%{text}%"
        if fts:
            where += (
                " and (rowid in (select rowid from assets_fts where assets_fts match ?)"
                " or prompt like ? or description like ? or tags_json like ?)"
            )
            args.extend([fts, like, like, like])
        else:
            where += " and (prompt like ? or description like ? or tags_json like ?)"
            args.extend([like, like, like])
    if before_ts > 0:
        where += " and created_at < ?"
        args.append(int(before_ts))
    order = "deleted_at desc" if deleted else "created_at desc"
    sql = f"select * from assets where {where} order by {order}"
    if limit > 0:
        sql += " limit ?"
        args.append(int(limit))
    conn = get_connection()
    try:
        return [_row_to_item(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def get_row(doc_id: str) -> dict | None:
    """取单行（含 deleted_at，供回收站端点判定）；不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute("select * from assets where id = ?", (doc_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def count_assets(repo_ids: list[str] | None = None, *, deleted: bool | None = None,
                 media_type: str = "") -> int:
    """计数（deleted=None ⇒ 全部，含回收站）。读路径回退判定用（0 = 未回填）。

    media_type 与 list_assets 同口径（2026-10-19：媒体筛选下推后，分页条的
    「共 N 页」必须按过滤后的数量算，否则会出现「共 10 页但内容只有一页」的假页）。
    """
    clauses: list[str] = []
    args: list = []
    if deleted is not None:
        clauses.append("deleted_at is not null" if deleted else "deleted_at is null")
    if repo_ids:
        clauses.append(f"repo_id in ({_placeholders(repo_ids)})")
        args.extend(repo_ids)
    if media_type:
        clauses.append("media_type = ?")
        args.append(media_type)
    where = (" where " + " and ".join(clauses)) if clauses else ""
    conn = get_connection()
    try:
        return int(conn.execute(f"select count(*) from assets{where}", args).fetchone()[0])
    finally:
        conn.close()
