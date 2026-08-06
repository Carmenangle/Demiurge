"""Agent 单轮结构化追踪：UTF-8 JSONL、turn_id 关联、大小轮转、密钥脱敏。"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.config import DATA_DIR

TRACE_DIR = DATA_DIR / "logs"
TRACE_FILE = TRACE_DIR / "agent-trace.jsonl"
MAX_BYTES = int(os.environ.get("LAF_AGENT_TRACE_MAX_BYTES", 10 * 1024 * 1024))
BACKUPS = int(os.environ.get("LAF_AGENT_TRACE_BACKUPS", 5))
ENABLED = os.environ.get("LAF_AGENT_TRACE", "1").strip().lower() not in {"0", "false", "off"}

_LOCK = threading.Lock()
_SECRET_KEYS = {"api_key", "apikey", "authorization", "token", "secret", "chat_key", "gen_key", "vid_key", "embed_key"}


def _redact(value: Any, key: str = "") -> Any:
    if key.lower().replace("-", "_") in _SECRET_KEYS:
        return "***"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return f"[image-data-uri {len(value)} chars]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _ctx_value(ctx: Any, key: str, default: str = "") -> str:
    if hasattr(ctx, "get"):
        return str(ctx.get(key, default) or default)
    return str(getattr(ctx, key, default) or default)


def _rotate(path: Path) -> None:
    if MAX_BYTES <= 0 or not path.is_file() or path.stat().st_size < MAX_BYTES:
        return
    if BACKUPS <= 0:
        path.unlink(missing_ok=True)
        return
    oldest = path.with_name(f"{path.name}.{BACKUPS}")
    oldest.unlink(missing_ok=True)
    for idx in range(BACKUPS - 1, 0, -1):
        src = path.with_name(f"{path.name}.{idx}")
        if src.exists():
            src.replace(path.with_name(f"{path.name}.{idx + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def emit(ctx: Any, event: str, /, **data: Any) -> None:
    """追加一条结构化事件；追踪失败绝不阻断主流程。"""
    if not ENABLED:
        return
    try:
        record = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "turn_id": _ctx_value(ctx, "turn_id"),
            "thread_id": _ctx_value(ctx, "thread_id"),
            "repo_id": _ctx_value(ctx, "repo_id") or _ctx_value(ctx, "thread_id"),
            "event": event,
            "data": _redact(data),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _LOCK:
            TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate(TRACE_FILE)
            with TRACE_FILE.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
    except Exception:
        return


def read_recent(repo_id: str, *, turn_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """读取当前 Trace 中指定作品的最近事件；不跨作品，不返回损坏行。"""
    wanted_repo = (repo_id or "").strip()
    if not wanted_repo or not TRACE_FILE.is_file():
        return []
    cap = max(1, min(int(limit), 200))
    try:
        lines = TRACE_FILE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("repo_id") != wanted_repo:
            continue
        if turn_id and record.get("turn_id") != turn_id:
            continue
        records.append(record)
        if len(records) >= cap:
            break
    records.reverse()
    return records
