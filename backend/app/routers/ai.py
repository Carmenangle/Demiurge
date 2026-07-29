"""/api/ai 聚合路由：把拆分后的子路由（文本/智能体/对话）挂到同一前缀下。
端点实现分居 ai_text / ai_agent / ai_chat；此处仅聚合，保持 main.py 挂载不变。
"""
from fastapi import APIRouter

from app.routers import ai_agent, ai_chat, ai_model_probe, ai_text, ai_workflow_builder

router = APIRouter()


@router.get("/")
def list_ai() -> dict[str, object]:
    return {"items": []}


router.include_router(ai_text.router)
router.include_router(ai_agent.router)
router.include_router(ai_chat.router)
router.include_router(ai_model_probe.router)
router.include_router(ai_workflow_builder.router)
