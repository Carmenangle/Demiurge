"""后台活动聚合（2026-10-19）。

面板除「统一 job 注册表」（GET /api/jobs）外的四类长操作：
模型/工作流下载、节点安装更新切内核、LoRA 触发词同步、对话后置维护。

真源在各子系统自己的进度快照里，本路由只做只读投影（见 service 模块 docstring）。
"""
from fastapi import APIRouter

from app.services import activity_feed

router = APIRouter()


@router.get("/background")
def background() -> dict[str, object]:
    """当前在跑（或刚结束 120s 内）的外部长操作。"""
    return {"items": activity_feed.snapshot()}
