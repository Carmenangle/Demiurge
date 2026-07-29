"""MCP 服务器配置持久化：读写 data/mcp_config.json。

结构（列表）：[{id, name, type: "stdio"|"sse", command?, args?, url?, enabled}]
- stdio：本地命令型，command + args（如 npx -y @xxx/mcp-server）
- sse：远程型，url（http/https + SSE transport）
落盘模式仿 comfy_launcher / user_state：缺失/损坏返回空列表，不抛。
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.config import DATA_DIR


def _path() -> Path:
    return DATA_DIR / "mcp_config.json"


def load_servers() -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return []


def save_servers(servers: list[dict]) -> list[dict]:
    """整体覆盖写。为每个缺 id 的项补 id。返回落盘后的列表。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = []
    for s in servers or []:
        if not isinstance(s, dict):
            continue
        s = dict(s)
        if not s.get("id"):
            s["id"] = uuid4().hex
        normalized.append(s)
    _path().write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return normalized


def enabled_servers() -> list[dict]:
    """仅返回启用的服务器（供 agent 加载工具用）。"""
    return [s for s in load_servers() if s.get("enabled")]


def get_server(server_id: str) -> dict | None:
    for s in load_servers():
        if s.get("id") == server_id:
            return s
    return None
