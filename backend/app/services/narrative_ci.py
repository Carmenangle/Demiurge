"""Narrative CI：内容中立、非阻断的剧情一致性诊断与处置记录。"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable

from app.services.pathnames import safe_seg

DB_NAME = "narrative_ci.db"
RESOLUTIONS = {"open", "fixed", "foreshadow", "retcon", "accepted"}


def _diagnostic(turn: int, code: str, message: str, evidence: str,
                source: str, severity: str = "warning") -> dict:
    raw = f"{turn}|{code}|{message}|{evidence}|{source}"
    return {
        "id": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "turn": turn, "code": code, "severity": severity,
        "message": message, "evidence": evidence, "source": source,
        "status": "open",
    }


def evaluate(text: str, *, turn: int, facts: Iterable[dict] = (),
             raw_deltas: Iterable[dict] = (), beliefs: Iterable[dict] = ()) -> list[dict]:
    """只返回带证据诊断；不分类内容、不阻断、不重写正文。"""
    body = text or ""
    diagnostics: list[dict] = []
    grouped: dict[tuple[str, str], list[dict]] = {}
    fact_list = list(facts)
    for fact in fact_list:
        grouped.setdefault((str(fact.get("subject") or ""),
                            str(fact.get("predicate") or "")), []).append(fact)
    for (subject, predicate), items in grouped.items():
        objects = {str(item.get("object") or "") for item in items}
        if subject and predicate and len(objects) > 1:
            diagnostics.append(_diagnostic(
                turn, "active_fact_conflict",
                f"{subject} 的“{predicate}”同时存在多个有效事实。",
                "；".join(sorted(objects)), "temporal_fact_store", "error",
            ))
    for fact in fact_list:
        subject = str(fact.get("subject") or "").strip()
        object_ = str(fact.get("object") or "").strip()
        if subject and object_ and (
            f"{subject}不是{object_}" in body or f"{subject}并非{object_}" in body
            or f"{subject}不在{object_}" in body
        ):
            diagnostics.append(_diagnostic(
                turn, "fact_contradiction", f"正文可能否定当前有效事实：{subject}／{object_}。",
                str(fact.get("evidence") or object_), "temporal_fact_store", "error",
            ))
    for delta in raw_deltas:
        if not isinstance(delta, dict):
            continue
        field = str(delta.get("field") or "")
        evidence = str(delta.get("evidence") or "").strip()
        value = delta.get("value")
        if field.startswith("数值/"):
            try:
                jump = abs(float(value)) if isinstance(value, (str, int, float)) else 0
            except (TypeError, ValueError):
                jump = 0
            if jump > 30:
                diagnostics.append(_diagnostic(
                    turn, "relationship_jump", f"{field} 单回合变化 {value}，需要明确剧情依据。",
                    evidence or "状态更新未提供证据", "character_state",
                ))
        if field.endswith("所在") and not evidence:
            diagnostics.append(_diagnostic(
                turn, "location_without_transition", f"{field} 已改变但没有过渡证据。",
                str(value or ""), "character_state",
            ))
    known = {(str(item.get("character") or ""), str(item.get("fact_id") or ""))
             for item in beliefs if str(item.get("stance") or "") == "knows"}
    for item in beliefs:
        character = str(item.get("character") or "").strip()
        fact_id = str(item.get("fact_id") or "").strip()
        if str(item.get("stance") or "") == "unknown" and (character, fact_id) not in known:
            claim = str(item.get("claim") or "").strip()
            if character and claim and character in body and claim in body:
                diagnostics.append(_diagnostic(
                    turn, "knowledge_overreach", f"{character} 在正文中使用了尚未知晓的事实。",
                    claim, "character_belief", "error",
                ))
    unique = {item["id"]: item for item in diagnostics}
    return list(unique.values())


def _connect(base: str, repo_id: str) -> sqlite3.Connection:
    path = Path(base) / safe_seg(repo_id, strip=False) / DB_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS diagnostics ("
        "id TEXT PRIMARY KEY, turn INTEGER NOT NULL, code TEXT NOT NULL, severity TEXT NOT NULL, "
        "message TEXT NOT NULL, evidence TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL)"
    )
    return conn


def save(base: str, repo_id: str, diagnostics: Iterable[dict]) -> int:
    if not (base and repo_id):
        return 0
    count = 0
    with _connect(base, repo_id) as conn:
        for item in diagnostics:
            result = conn.execute(
                "INSERT OR IGNORE INTO diagnostics VALUES(?,?,?,?,?,?,?,?)",
                (item["id"], item["turn"], item["code"], item["severity"],
                 item["message"], item["evidence"], item["source"], "open"),
            )
            count += max(0, result.rowcount)
    return count


def list_diagnostics(base: str, repo_id: str, *, status: str = "") -> list[dict]:
    with _connect(base, repo_id) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM diagnostics WHERE status=? ORDER BY turn DESC,id", (status,),
            )
        else:
            rows = conn.execute("SELECT * FROM diagnostics ORDER BY turn DESC,id")
        return [dict(row) for row in rows]


def resolve(base: str, repo_id: str, diagnostic_id: str, status: str) -> bool:
    if status not in RESOLUTIONS - {"open"}:
        raise ValueError("未知 Narrative CI 处置状态")
    with _connect(base, repo_id) as conn:
        result = conn.execute(
            "UPDATE diagnostics SET status=? WHERE id=?", (status, diagnostic_id),
        )
    return bool(result.rowcount)
