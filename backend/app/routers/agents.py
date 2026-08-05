"""多 Agent 预设端点：列表/保存 + 取内置默认提示词（供前端"默认规则"展示）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import agent_store, builtin_agents

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


# ── ③ 内置智能体：展示图里所有默认 Agent + 参数 + 绑定工具，开放高价值字段覆盖 ──


@router.get("/builtin")
def list_builtin_agents() -> list[dict]:
    """列出所有内置 Agent 的元数据 + 默认值 + 当前生效值（含用户覆盖）。"""
    return builtin_agents.registry_view()


@router.post("/builtin")
def save_builtin_overrides(overrides: dict) -> list[dict]:
    """保存内置 Agent 覆盖（{agent_id: {field: value}}），只接受注册表声明的可编辑字段。"""
    builtin_agents.save_overrides(overrides)
    return builtin_agents.registry_view()
