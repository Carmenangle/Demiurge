"""全局正则脚本端点：列表/保存/测试。路由薄——持久化交给 regex_store，跑正则交给 regex_engine。

全局正则跨作品生效；卡内嵌正则随卡（在 characters 侧）。前端「正则」按钮管理的是全局这组。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import regex_engine, regex_store

router = APIRouter()


@router.get("/")
def list_regex() -> dict[str, object]:
    return {"items": regex_store.load_scripts()}


class SaveRequest(BaseModel):
    scripts: list[dict[str, Any]]


@router.post("/save")
def save_regex(req: SaveRequest) -> dict[str, object]:
    return {"items": regex_store.save_scripts(req.scripts)}


class TestRequest(BaseModel):
    script: dict[str, Any]
    text: str
    placement: int = regex_engine.Placement.AI_OUTPUT
    is_markdown: bool = False
    is_prompt: bool = False
    depth: int | None = None


@router.post("/test")
def test_regex(req: TestRequest) -> dict[str, object]:
    """在给定文本上试跑单条脚本，返回结果供前端预览。"""
    script = regex_engine.from_st_dict(req.script)
    out = regex_engine.run_scripts(
        req.text, req.placement, [script],
        is_markdown=req.is_markdown, is_prompt=req.is_prompt, depth=req.depth,
    )
    return {"result": out, "changed": out != req.text}
