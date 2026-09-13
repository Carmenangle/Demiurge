"""多 Agent 预设持久化：读写 data/agents.json。

每个 Agent = 一套人设/行为配置，对话时可切换。结构：
{ id, name, systemPrompt, memory, temperature?, topP?, maxTokens?,
  tools: {generate_image,generate_video,image_to_image,analyze_image,search_inspiration,mcp,skills}(bool),
  isDefault(bool，默认 Agent 带普通对话优先的工具调用规则), enabled }

agent_id 为空时智能体用内置默认行为（image_agent._AGENT_SYSTEM_BASE），与加此功能前完全一致——不破坏原功能。
落盘模式仿 mcp_store / skills_store。
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.config import DATA_DIR

# 内置工具键（供前端工具开关 + 后端过滤）。MCP/技能不在此，走 mcpServerIds/skillIds 列表选择
TOOL_KEYS = ["generate_image", "generate_video", "image_to_image", "analyze_image", "search_inspiration"]


def _path() -> Path:
    return DATA_DIR / "agents.json"


def load_agents() -> list[dict]:
    p = _path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return []


def save_agents(agents: list[dict]) -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = []
    for a in agents or []:
        if not isinstance(a, dict):
            continue
        a = dict(a)
        if not a.get("id"):
            a["id"] = uuid4().hex
        normalized.append(a)
    _path().write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def get_agent(agent_id: str) -> dict | None:
    if not agent_id:
        return None
    for a in load_agents():
        if a.get("id") == agent_id:
            return a
    return None
