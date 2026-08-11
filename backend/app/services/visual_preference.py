"""作品级视觉偏好：显式二选一为真值，Elo 只排序，不删除或改写 LoRA。"""
from __future__ import annotations

import sqlite3
import time
import uuid
from collections import Counter
from pathlib import Path

from app.services.pathnames import safe_seg

DB_NAME = "visual_preferences.db"
REASONS = {"character", "action", "composition", "lighting", "color", "quality", "other"}


def _connect(base: str, repo_id: str) -> sqlite3.Connection:
    path = Path(base) / safe_seg(repo_id, strip=False) / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS comparisons ("
        "id TEXT PRIMARY KEY,winner_id TEXT NOT NULL,loser_id TEXT NOT NULL,"
        "reason TEXT NOT NULL,created_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scores (asset_id TEXT PRIMARY KEY,score REAL NOT NULL,"
        "wins INTEGER NOT NULL,losses INTEGER NOT NULL)"
    )
    return conn


def _score(conn: sqlite3.Connection, asset_id: str) -> tuple[float, int, int]:
    row = conn.execute("SELECT score,wins,losses FROM scores WHERE asset_id=?", (asset_id,)).fetchone()
    return (float(row["score"]), int(row["wins"]), int(row["losses"])) if row else (1000.0, 0, 0)


def record(base: str, repo_id: str, *, winner_id: str, loser_id: str,
           reason: str = "other") -> dict[str, object]:
    winner_id, loser_id = winner_id.strip(), loser_id.strip()
    if not (base and repo_id and winner_id and loser_id) or winner_id == loser_id:
        raise ValueError("偏好比较需要同一作品的两张不同资产")
    reason = reason if reason in REASONS else "other"
    with _connect(base, repo_id) as conn:
        winner, wins, winner_losses = _score(conn, winner_id)
        loser, loser_wins, losses = _score(conn, loser_id)
        expected = 1.0 / (1.0 + 10 ** ((loser - winner) / 400.0))
        delta = 24.0 * (1.0 - expected)
        conn.execute(
            "INSERT INTO comparisons VALUES(?,?,?,?,?)",
            (uuid.uuid4().hex, winner_id, loser_id, reason, time.time()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO scores VALUES(?,?,?,?)",
            (winner_id, winner + delta, wins + 1, winner_losses),
        )
        conn.execute(
            "INSERT OR REPLACE INTO scores VALUES(?,?,?,?)",
            (loser_id, loser - delta, loser_wins, losses + 1),
        )
    return {"winner_id": winner_id, "loser_id": loser_id, "reason": reason,
            "winner_score": winner + delta, "loser_score": loser - delta}


def score_map(base: str, repo_id: str) -> dict[str, float]:
    if not (base and repo_id):
        return {}
    with _connect(base, repo_id) as conn:
        return {str(row["asset_id"]): float(row["score"])
                for row in conn.execute("SELECT asset_id,score FROM scores")}


def rank(base: str, items: list[dict]) -> list[dict]:
    maps: dict[str, dict[str, float]] = {}
    indexed = list(enumerate(items))
    def key(pair: tuple[int, dict]) -> tuple[float, int]:
        index, item = pair
        repo_id = str(item.get("repo_id") or "")
        if repo_id not in maps:
            maps[repo_id] = score_map(base, repo_id)
        return maps[repo_id].get(str(item.get("id") or ""), 1000.0), -index
    return [item for _index, item in sorted(indexed, key=key, reverse=True)]


def summary(base: str, repo_id: str) -> dict[str, object]:
    with _connect(base, repo_id) as conn:
        comparisons = list(conn.execute("SELECT reason FROM comparisons"))
        scores = [dict(row) for row in conn.execute(
            "SELECT asset_id,score,wins,losses FROM scores ORDER BY score DESC,asset_id"
        )]
    reasons = Counter(str(row["reason"]) for row in comparisons)
    return {"comparisons": len(comparisons), "reasons": dict(reasons), "scores": scores}


def clear(base: str, repo_id: str) -> None:
    with _connect(base, repo_id) as conn:
        conn.execute("DELETE FROM comparisons")
        conn.execute("DELETE FROM scores")
