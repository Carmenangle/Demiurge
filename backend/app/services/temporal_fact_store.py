"""时序事实账本：任意世界/实体事实的有效区间与显式替代关系。"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from app.services.pathnames import safe_seg

DB_NAME = "temporal_facts.db"
_CHARACTER_STATE_PREDICATES = {
    "好感度", "态度", "心情", "所在", "身体状态", "精神状态", "生理状态",
    "心理状态", "外观状态", "伤势状态", "衣着状态",
}


def _connect(base: str, repo_id: str) -> sqlite3.Connection:
    path = Path(base) / safe_seg(repo_id, strip=False) / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS facts ("
        "id TEXT PRIMARY KEY, subject TEXT NOT NULL, predicate TEXT NOT NULL, "
        "object TEXT NOT NULL, valid_from_turn INTEGER NOT NULL, valid_to_turn INTEGER, "
        "supersedes_id TEXT, evidence TEXT NOT NULL, evidence_hash TEXT NOT NULL, "
        "source TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS fact_time ON facts(subject,predicate,valid_from_turn)")
    return conn


def _dump(row: sqlite3.Row) -> dict:
    return dict(row)


def resolve_supersedes(base: str, repo_id: str, reference: str) -> str | None:
    """P3② 指认式替代（2026-09-06 用户裁决）：把模型引用的 id 前缀解析成完整事实 id。

    精确命中或唯一前缀命中（≥8 位，短前缀不猜）→ 全 id；找不到/歧义 → None
    （调用方降级为普通 ADD，矛盾交 conflicts 诊断——语义增强不是依赖）。
    """
    ref = (reference or "").strip().lstrip("#")
    if len(ref) < 8 or not (base and repo_id):
        return None
    with _connect(base, repo_id) as conn:
        if conn.execute("SELECT 1 FROM facts WHERE id=?", (ref,)).fetchone():
            return ref
        rows = conn.execute(
            "SELECT DISTINCT id FROM facts WHERE id LIKE ?", (ref + "%",)).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def record(base: str, repo_id: str, *, subject: str, predicate: str, object_: str,
           valid_from_turn: int, evidence: str, source: str,
           supersedes_id: str | None = None) -> dict:
    """追加事实；只有显式 supersedes_id 才关闭旧事实，禁止模型隐式覆盖。"""
    subject, predicate, object_ = subject.strip(), predicate.strip(), object_.strip()
    evidence, source = evidence.strip(), source.strip()
    if not all((base, repo_id, subject, predicate, object_, evidence, source)):
        raise ValueError("事实字段、证据与来源不能为空")
    if predicate in _CHARACTER_STATE_PREDICATES:
        raise ValueError(f"{predicate} 由 character_state 持有，不得写入时序事实账本")
    if valid_from_turn < 0:
        raise ValueError("valid_from_turn 不能为负数")
    evidence_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    raw = f"{repo_id}|{subject}|{predicate}|{object_}|{valid_from_turn}|{evidence_hash}"
    fact_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with _connect(base, repo_id) as conn:
        if supersedes_id:
            old = conn.execute("SELECT * FROM facts WHERE id=?", (supersedes_id,)).fetchone()
            if old is None:
                raise ValueError("supersedes_id 不存在")
            if old["subject"] != subject or old["predicate"] != predicate:
                raise ValueError("只能替代同一 subject/predicate 的事实")
            if int(old["valid_from_turn"]) > valid_from_turn:
                raise ValueError("新事实不能早于被替代事实")
            conn.execute(
                "UPDATE facts SET valid_to_turn=? WHERE id=?",
                (max(int(old["valid_from_turn"]), valid_from_turn - 1), supersedes_id),
            )
        conn.execute(
            "INSERT OR IGNORE INTO facts(id,subject,predicate,object,valid_from_turn,"
            "valid_to_turn,supersedes_id,evidence,evidence_hash,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fact_id, subject, predicate, object_, valid_from_turn, None,
             supersedes_id, evidence, evidence_hash, source),
        )
        row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        # M1 E2E / P3②（2026-09-06）：同事实被重复主张 → 重新生效。两个真实场景：
        # ① 封口后同回合重生成重记录（同 id 被 INSERT OR IGNORE 挡住，重生成规律不生效）；
        # ② 指认式替代事实随回合被封口后重生成重录。重新生效后，本次调用若带 supersedes_id
        # 其对旧事实的关闭 UPDATE 幂等重跑；「重复主张与在替事实并存」由 conflicts 诊断兜底。
        if row is not None and row["valid_to_turn"] is not None:
            conn.execute("UPDATE facts SET valid_to_turn=NULL WHERE id=?", (fact_id,))
            row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
    return _dump(row)


def seal_from_turn(base: str, repo_id: str, *, turn: int) -> int:
    """封口自 turn 起写入且仍有效的事实（M1 记忆封口，2026-09-06）：
    valid_to_turn 收口到 turn-1——不再被 as_of 注入（等效记忆清除），
    但 timeline 完整保留（可审计可回放）。返回封口条数（幂等）。

    早于封口回合的事实不受影响：它们的写入与被删回合无关。
    """
    if turn < 0:
        raise ValueError("封口回合不能为负")
    if not (base and repo_id):
        return 0
    with _connect(base, repo_id) as conn:
        cur = conn.execute(
            "UPDATE facts SET valid_to_turn=? "
            "WHERE valid_from_turn>=? AND (valid_to_turn IS NULL OR valid_to_turn>=?)",
            (turn - 1, turn, turn),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def as_of(base: str, repo_id: str, turn: int, *, subject: str = "",
          recency_first: bool = False) -> list[dict]:
    """当前回合有效事实。recency_first=True（M2 2026-09-06）：按新近度降序——
    供注入面使用：预算裁剪保留块内前几行，时效优先让最新事实在压力下存活
    （默认按 subject/predicate/turn 分组升序，供 conflicts/timeline 可读性）。"""
    with _connect(base, repo_id) as conn:
        sql = ("SELECT * FROM facts WHERE valid_from_turn<=? AND "
               "(valid_to_turn IS NULL OR valid_to_turn>=?)")
        args: list[object] = [turn, turn]
        if subject.strip():
            sql += " AND subject=?"
            args.append(subject.strip())
        sql += (" ORDER BY valid_from_turn DESC, id DESC" if recency_first
                else " ORDER BY subject,predicate,valid_from_turn,id")
        return [_dump(row) for row in conn.execute(sql, args)]


def timeline(base: str, repo_id: str, *, subject: str = "", predicate: str = "") -> list[dict]:
    with _connect(base, repo_id) as conn:
        clauses, args = [], []
        if subject.strip():
            clauses.append("subject=?")
            args.append(subject.strip())
        if predicate.strip():
            clauses.append("predicate=?")
            args.append(predicate.strip())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [_dump(row) for row in conn.execute(
            "SELECT * FROM facts" + where + " ORDER BY valid_from_turn,id", args,
        )]


def conflicts(base: str, repo_id: str, turn: int) -> list[dict]:
    active = as_of(base, repo_id, turn)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for fact in active:
        grouped.setdefault((fact["subject"], fact["predicate"]), []).append(fact)
    return [{"subject": key[0], "predicate": key[1], "facts": facts}
            for key, facts in grouped.items()
            if len({fact["object"] for fact in facts}) > 1]
