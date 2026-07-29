"""多 Agent 预设端点：列表/保存 + 取内置默认提示词（供前端"默认规则"展示）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import agent_store

router = APIRouter()


class AgentTools(BaseModel):
    generate_image: bool = True
    generate_video: bool = True
    image_to_image: bool = True
    analyze_image: bool = True
    search_inspiration: bool = True


class Agent(BaseModel):
    id: str = ""
    name: str
    systemPrompt: str = ""
    memory: str = ""
    temperature: float | None = None
    topP: float | None = None
    maxTokens: int | None = None
    tools: AgentTools = AgentTools()
    mcpServerIds: list[str] = []   # 选中启用的 MCP 服务器 id（空=都不用）
    skillIds: list[str] = []       # 选中启用的技能 id（空=都不用）
    isDefault: bool = False
    enabled: bool = True


@router.get("")
def list_agents() -> list[Agent]:
    return [Agent(**a) for a in agent_store.load_agents()]


@router.post("")
def save_agents(agents: list[Agent]) -> list[Agent]:
    saved = agent_store.save_agents([a.model_dump() for a in agents])
    return [Agent(**a) for a in saved]


@router.get("/default-prompt")
def default_prompt() -> dict:
    """返回内置默认系统提示词（普通对话优先 + 显式工具调用规则）。"""
    from app.services.image_agent import _AGENT_SYSTEM_BASE
    return {"prompt": _AGENT_SYSTEM_BASE}
