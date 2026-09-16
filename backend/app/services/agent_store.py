"""多 Agent 预设持久化：读写 data/agents.json。

每个 Agent = 一套人设/行为配置，对话时可切换。结构：
{ id, name, systemPrompt, memory, temperature?, topP?, maxTokens?,
  tools: {generate_image,generate_video,image_to_image,analyze_image,search_inspiration,mcp,skills}(bool),
  isDefault(bool，默认 Agent 带普通对话优先的工具调用规则), enabled }

agent_id 为空时智能体用内置默认行为（image_agent.AGENT_SYSTEM_BASE），与加此功能前完全一致——不破坏原功能。
落盘模式交 `<json_file_store>` 统一。
"""
from __future__ import annotations

from pathlib import Path

from app.config import DATA_DIR
from app.services import json_file_store

# 内置工具键（供前端工具开关 + 后端过滤）。MCP/技能不在此，走 mcpServerIds/skillIds 列表选择
TOOL_KEYS = ["generate_image", "generate_video", "image_to_image", "analyze_image", "search_inspiration"]


def _path() -> Path:
    return DATA_DIR / "agents.json"


def load_agents() -> list[dict]:
    return json_file_store.read_list(_path(), errors=json_file_store.ERRORS_STANDARD)


def save_agents(agents: list[dict]) -> list[dict]:
    """整体覆盖写，为缺 id 的项补 id。返回落盘后的列表。"""
    return json_file_store.write_list(_path(), agents)


def get_agent(agent_id: str) -> dict | None:
    if not agent_id:
        return None
    return json_file_store.find_by_id(load_agents(), agent_id)
