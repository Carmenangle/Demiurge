"""MCP 服务器配置持久化：读写 data/mcp_config.json。

结构（列表）：[{id, name, type: "stdio"|"sse", command?, args?, url?, enabled}]
- stdio：本地命令型，command + args（如 npx -y @xxx/mcp-server）
- sse：远程型，url（http/https + SSE transport）
落盘模式交 `<json_file_store>` 统一：缺失/损坏返回空列表，不抛。
"""
from __future__ import annotations

from pathlib import Path

from app.config import DATA_DIR
from app.services import json_file_store


def _path() -> Path:
    return DATA_DIR / "mcp_config.json"


def load_servers() -> list[dict]:
    return json_file_store.read_list(_path(), errors=json_file_store.ERRORS_STANDARD)


def save_servers(servers: list[dict]) -> list[dict]:
    """整体覆盖写。为每个缺 id 的项补 id。返回落盘后的列表。"""
    return json_file_store.write_list(_path(), servers)


def enabled_servers() -> list[dict]:
    """仅返回启用的服务器（供 agent 加载工具用）。"""
    return [s for s in load_servers() if s.get("enabled")]


def get_server(server_id: str) -> dict | None:
    return json_file_store.find_by_id(load_servers(), server_id)
