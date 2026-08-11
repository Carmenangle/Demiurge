"""叙事纪要落盘+召回：SQLite FTS5（trigram）单一属主。Phase C「表格记忆」的 I/O 层。

按 repo_id 物理隔离：`<base>/<safe repo_id>/chronicle.db`（与 state.json 同目录，同一作品线）。
纪要**只增不改**（append-only）——`append` 落一条，`recall` 按 trigram 相关性取 top-k，
`recent` 取最近若干条兜底，`rebuild` 从已存正文清空重建 FTS5 索引（RAG 重建口）。

依赖方向：import `narrative_memory` 取 `ChronicleEntry` 类型与查询构造（纯逻辑）；
`narrative_memory` 不反向 import 本模块。base 由调用方注入，不读 config（同 character_state）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services import narrative_memory as nm
from app.services.narrative_memory import ChronicleEntry
from app.services.pathnames import safe_seg

CHRONICLE_DB = "chronicle.db"


def db_path(base: str, repo_id: str) -> Path:
    return Path(base) / safe_seg(repo_id, strip=False) / CHRONICLE_DB


def _connect(base: str, repo_id: str) -> sqlite3.Connection:
    """打开（必要时建）某作品线的纪要库并保证表结构存在。"""
    p = db_path(base, repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # 每卡抽取进度（last_summarized_turn），门控用，不进 FTS。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (card TEXT PRIMARY KEY, last_turn INTEGER)"
    )
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chronicle'"
    ).fetchone()
    if not existing:
        _create_chronicle(conn)
    else:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(chronicle)").fetchall()}
        if "overview" not in columns:
            rows = conn.execute(
                "SELECT rowid, text, turn_start, turn_end, layer, keywords FROM chronicle "
                "ORDER BY rowid"
            ).fetchall()
            conn.execute("DROP TABLE chronicle")
            _create_chronicle(conn)
            for rowid, text, turn_start, turn_end, layer, keywords in rows:
                overview = (text or "")[:120]
                conn.execute(
                    "INSERT INTO chronicle(rowid, body, overview, text, dialogue, characters, "
                    "turn_start, turn_end, layer, keywords) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rowid, text or "", overview, text or "", "", "", turn_start,
                     turn_end, layer, keywords or ""),
                )
    conn.commit()


def _create_chronicle(conn: sqlite3.Connection) -> None:
    # body 走 trigram 分词；展示字段均 UNINDEXED，body 由 entry.body() 汇总。
    conn.execute(
        "CREATE VIRTUAL TABLE chronicle USING fts5("
        "body, overview UNINDEXED, text UNINDEXED, dialogue UNINDEXED, characters UNINDEXED, "
        "turn_start UNINDEXED, turn_end UNINDEXED, layer UNINDEXED, keywords UNINDEXED, "
        "tokenize='trigram')"
    )


def _row_to_entry(row: sqlite3.Row | tuple) -> ChronicleEntry:
    rowid, overview, text, dialogue, characters, ts, te, layer, kws = row
    return ChronicleEntry(
        text=text or "", turn_start=int(ts or 0), turn_end=int(te or 0),
        layer=int(layer or 0),
        keywords=[k for k in (kws or "").split("\n") if k],
        rowid=int(rowid or 0),
        overview=overview or "", dialogue=dialogue or "",
        characters=[name for name in (characters or "").split("\n") if name],
    )


def append(base: str, repo_id: str, entry: ChronicleEntry) -> int:
    """追加一条纪要，返回其 rowid。base/repo_id 空 → 跳过返回 0。"""
    if not (base and repo_id) or not entry.text.strip():
        return 0
    conn = _connect(base, repo_id)
    try:
        rowid = _insert_entry(conn, entry)
        conn.commit()
        return rowid
    finally:
        conn.close()


def _insert_entry(conn: sqlite3.Connection, entry: ChronicleEntry) -> int:
    cur = conn.execute(
        "INSERT INTO chronicle(body, overview, text, dialogue, characters, "
        "turn_start, turn_end, layer, keywords) VALUES (?,?,?,?,?,?,?,?,?)",
        (entry.body(), entry.short_overview(), entry.text, entry.dialogue,
         "\n".join(entry.characters), entry.turn_start, entry.turn_end,
         entry.layer, "\n".join(entry.keywords)),
    )
    return int(cur.lastrowid or 0)


def import_entries(
    base: str,
    repo_id: str,
    entries: list[ChronicleEntry],
    *,
    replace: bool = False,
) -> int:
    """在单个 SQLite 事务内追加或替换纪要；任一条失败则完整回滚。"""
    valid = [entry for entry in entries if entry.text.strip()]
    if not (base and repo_id):
        return 0
    conn = _connect(base, repo_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if replace:
            conn.execute("DELETE FROM chronicle")
        imported = sum(1 for entry in valid if _insert_entry(conn, entry))
        conn.commit()
        return imported
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recall(base: str, repo_id: str, query: str, *, k: int = 4,
           layer: int | None = None) -> list[ChronicleEntry]:
    """按 trigram 相关性召回 top-k 纪要。查询过短/空 → []（上层跳过注入）。

    layer 非空则只召回该层；否则全层混召（细纪要通常更相关，bm25 自然靠前）。
    """
    match = nm.to_trigram_query(query)
    if not match or not (base and repo_id):
        return []
    if not db_path(base, repo_id).is_file():
        return []
    conn = _connect(base, repo_id)
    try:
        sql = ("SELECT rowid, overview, text, dialogue, characters, "
               "turn_start, turn_end, layer, keywords "
               "FROM chronicle WHERE chronicle MATCH ?")
        params: list[object] = [match]
        if layer is not None:
            sql += " AND layer = ?"
            params.append(layer)
        sql += " ORDER BY bm25(chronicle) LIMIT ?"
        params.append(k)
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [_row_to_entry(r) for r in rows]


def recent(base: str, repo_id: str, *, k: int = 20,
           layer: int | None = None) -> list[ChronicleEntry]:
    """取最近 k 条纪要（按 rowid 降序=时间倒序）。供列表展示 / 压缩取旧条 / 召回兜底。"""
    if not (base and repo_id) or not db_path(base, repo_id).is_file():
        return []
    conn = _connect(base, repo_id)
    try:
        sql = ("SELECT rowid, overview, text, dialogue, characters, "
               "turn_start, turn_end, layer, keywords FROM chronicle")
        params: list[object] = []
        if layer is not None:
            sql += " WHERE layer = ?"
            params.append(layer)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(k)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_to_entry(r) for r in rows]


def oldest(base: str, repo_id: str, *, k: int, layer: int) -> list[ChronicleEntry]:
    """取某层最旧的 k 条（按 rowid 升序），供分层压缩吃掉。"""
    if not (base and repo_id) or not db_path(base, repo_id).is_file():
        return []
    conn = _connect(base, repo_id)
    try:
        rows = conn.execute(
            "SELECT rowid, overview, text, dialogue, characters, "
            "turn_start, turn_end, layer, keywords FROM chronicle "
            "WHERE layer = ? ORDER BY rowid ASC LIMIT ?", (layer, k),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_entry(r) for r in rows]


def count(base: str, repo_id: str, *, layer: int) -> int:
    """某层现有条数（分层压缩门控用）。"""
    if not (base and repo_id) or not db_path(base, repo_id).is_file():
        return 0
    conn = _connect(base, repo_id)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM chronicle WHERE layer = ?", (layer,)).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def delete_rows(base: str, repo_id: str, rowids: list[int]) -> int:
    """删除指定 rowid 的纪要（分层压缩吃掉旧条后清除）。返回删除条数。"""
    if not (base and repo_id) or not rowids or not db_path(base, repo_id).is_file():
        return 0
    conn = _connect(base, repo_id)
    try:
        before = conn.execute("SELECT COUNT(*) FROM chronicle").fetchone()[0]
        conn.executemany("DELETE FROM chronicle WHERE rowid = ?", [(r,) for r in rowids])
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM chronicle").fetchone()[0]
        return int(before - after)
    finally:
        conn.close()


def get_by_rowid(base: str, repo_id: str, rowid: int) -> ChronicleEntry | None:
    """读单条纪要（供更新前取原值/校验存在）。无则 None。"""
    if not (base and repo_id) or not db_path(base, repo_id).is_file():
        return None
    conn = _connect(base, repo_id)
    try:
        row = conn.execute(
            "SELECT rowid, overview, text, dialogue, characters, "
            "turn_start, turn_end, layer, keywords FROM chronicle "
            "WHERE rowid = ?", (rowid,)).fetchone()
    finally:
        conn.close()
    return _row_to_entry(row) if row else None


def update_entry(base: str, repo_id: str, rowid: int, entry: ChronicleEntry) -> bool:
    """就地更新一条纪要（正文/区间/层/关键词），同步重算 trigram 索引 body。返回是否命中更新。

    人工编辑往事用（浏览器 UI）。与自动流程「只增不改」不冲突：这是用户显式改写。
    """
    if not (base and repo_id) or not entry.text.strip() or not db_path(base, repo_id).is_file():
        return False
    conn = _connect(base, repo_id)
    try:
        cur = conn.execute(
            "UPDATE chronicle SET body=?, overview=?, text=?, dialogue=?, characters=?, "
            "turn_start=?, turn_end=?, layer=?, keywords=? "
            "WHERE rowid=?",
            (entry.body(), entry.short_overview(), entry.text, entry.dialogue,
             "\n".join(entry.characters), entry.turn_start, entry.turn_end,
             entry.layer, "\n".join(entry.keywords), rowid),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def all_entries(base: str, repo_id: str) -> list[ChronicleEntry]:
    """导出用：全部纪要（rowid 升序）。无库 → []。"""
    if not (base and repo_id) or not db_path(base, repo_id).is_file():
        return []
    conn = _connect(base, repo_id)
    try:
        rows = conn.execute(
            "SELECT rowid, overview, text, dialogue, characters, "
            "turn_start, turn_end, layer, keywords FROM chronicle "
            "ORDER BY rowid ASC").fetchall()
    finally:
        conn.close()
    return [_row_to_entry(r) for r in rows]


def get_last_turn(base: str, repo_id: str, card: str) -> int:
    """读某卡的抽取进度 last_summarized_turn。无记录 → 0。"""
    if not (base and repo_id) or not db_path(base, repo_id).is_file():
        return 0
    conn = _connect(base, repo_id)
    try:
        row = conn.execute("SELECT last_turn FROM meta WHERE card = ?", (card,)).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else 0


def set_last_turn(base: str, repo_id: str, card: str, turn: int) -> None:
    """写某卡的抽取进度。"""
    if not (base and repo_id):
        return
    conn = _connect(base, repo_id)
    try:
        conn.execute(
            "INSERT INTO meta(card, last_turn) VALUES(?,?) "
            "ON CONFLICT(card) DO UPDATE SET last_turn=excluded.last_turn", (card, turn))
        conn.commit()
    finally:
        conn.close()


def rebuild(base: str, repo_id: str) -> int:
    """RAG 重建口：从已存正文清空并重建 FTS5 索引（应对索引损坏/分词器变更/数据成型后重嵌）。

    读出全部纪要 → 重建虚拟表 → 按 body() 重算 trigram 索引重插。返回重建的条数。
    正文/元数据不丢（存在 UNINDEXED 列，只是重算索引）。base/repo_id 空或无库 → 0。
    """
    if not (base and repo_id) or not db_path(base, repo_id).is_file():
        return 0
    conn = _connect(base, repo_id)
    try:
        rows = conn.execute(
            "SELECT rowid, overview, text, dialogue, characters, "
            "turn_start, turn_end, layer, keywords FROM chronicle "
            "ORDER BY rowid ASC").fetchall()
        entries = [_row_to_entry(r) for r in rows]
        conn.execute("DROP TABLE chronicle")
        _ensure_schema(conn)
        for e in entries:
            conn.execute(
                "INSERT INTO chronicle(body, overview, text, dialogue, characters, "
                "turn_start, turn_end, layer, keywords) VALUES (?,?,?,?,?,?,?,?,?)",
                (e.body(), e.short_overview(), e.text, e.dialogue,
                 "\n".join(e.characters), e.turn_start, e.turn_end, e.layer,
                 "\n".join(e.keywords)))
        conn.commit()
        return len(entries)
    finally:
        conn.close()


def delete_overlapping(base: str, repo_id: str, start: int, end: int) -> int:
    """只删除与指定回合范围相交的纪要，供手动重填局部覆盖。"""
    entries = all_entries(base, repo_id)
    rowids = [
        entry.rowid for entry in entries
        if entry.turn_start <= end and entry.turn_end >= start
    ]
    return delete_rows(base, repo_id, rowids)
