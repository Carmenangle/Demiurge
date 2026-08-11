"""角色独立认知：世界真相之外，记录每个角色知道、相信、怀疑或隐瞒什么。"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from app.services.pathnames import safe_seg

DB_NAME = "character_beliefs.db"
STANCES = {"knows", "believes", "suspects", "misbelieves", "conceals", "unknown"}


def _connect(base: str, repo_id: str) -> sqlite3.Connection:
    path = Path(base) / safe_seg(repo_id, strip=False) / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS beliefs ("
        "id TEXT PRIMARY KEY,character TEXT NOT NULL,fact_id TEXT NOT NULL,claim TEXT NOT NULL,"
        "stance TEXT NOT NULL,confidence REAL NOT NULL,witnessed_at INTEGER NOT NULL,"
        "evidence TEXT NOT NULL,source TEXT NOT NULL,supersedes_id TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS belief_actor_turn ON beliefs(character,witnessed_at)")
    return conn


def record(base: str, repo_id: str, *, character: str, fact_id: str, claim: str,
           stance: str, confidence: float, witnessed_at: int, evidence: str,
           source: str = "user", supersedes_id: str | None = None) -> dict:
    character, fact_id, claim = character.strip(), fact_id.strip(), claim.strip()
    evidence, source = evidence.strip(), source.strip()
    if not all((base, repo_id, character, fact_id, claim, evidence, source)):
        raise ValueError("角色认知、事实引用、证据与来源不能为空")
    if stance not in STANCES:
        raise ValueError("未知角色认知状态")
    if witnessed_at < 0:
        raise ValueError("witnessed_at 不能为负数")
    confidence = max(0.0, min(1.0, float(confidence)))
    raw = f"{repo_id}|{character}|{fact_id}|{stance}|{witnessed_at}|{evidence}"
    item_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with _connect(base, repo_id) as conn:
        if supersedes_id:
            old = conn.execute("SELECT * FROM beliefs WHERE id=?", (supersedes_id,)).fetchone()
            if old is None:
                raise ValueError("supersedes_id 不存在")
            if old["character"] != character or old["fact_id"] != fact_id:
                raise ValueError("只能替代同一角色对同一事实的认知")
        conn.execute(
            "INSERT OR IGNORE INTO beliefs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (item_id, character, fact_id, claim, stance, confidence, witnessed_at,
             evidence, source, supersedes_id),
        )
        row = conn.execute("SELECT * FROM beliefs WHERE id=?", (item_id,)).fetchone()
    return dict(row)


def active(base: str, repo_id: str, turn: int, *, characters: list[str] | None = None) -> list[dict]:
    with _connect(base, repo_id) as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM beliefs WHERE witnessed_at<=? ORDER BY witnessed_at,id", (turn,),
        )]
    allowed = set(characters or [])
    if allowed:
        rows = [row for row in rows if row["character"] in allowed]
    superseded = {str(row.get("supersedes_id") or "") for row in rows if row.get("supersedes_id")}
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        if row["id"] in superseded:
            continue
        latest[(row["character"], row["fact_id"])] = row
    return sorted(latest.values(), key=lambda item: (item["character"], item["fact_id"]))


def render_context(items: list[dict]) -> str:
    labels = {
        "knows": "确认知道", "believes": "主观相信", "suspects": "怀疑",
        "misbelieves": "错误相信", "conceals": "知道但隐瞒", "unknown": "明确不知道",
    }
    return "\n".join(
        f"- {item['character']}｜{labels.get(item['stance'], item['stance'])}｜{item['claim']}"
        f"（确信度 {float(item['confidence']):.2f}；依据：{item['evidence']}）"
        for item in items
    )
