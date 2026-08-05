"""偏置预设端点：导入/列表/读取/删除。路由薄——解析+组装交给 preset_store。

预设文件夹路径来自前端设置（presetDir），随请求透传。同名冲突 409（对标角色卡/世界书导入）。
仅剧情模式用（前端「预设」按钮只在 story 模式显示）；激活预设由前端存 activePresetName，随对话透传。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.services import preset_store

router = APIRouter()


def _parse_preset(raw: bytes) -> dict:
    try:
        preset = json.loads(raw.decode("utf-8-sig", "replace"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"预设 JSON 非法：{exc}") from exc
    if not isinstance(preset, dict) or "prompts" not in preset:
        raise HTTPException(status_code=400, detail="不是有效的 ST 预设（缺 prompts）")
    return preset


@router.get("/")
def list_presets(base: str = "") -> dict[str, object]:
    if not base:
        return {"items": []}
    return {"items": [s.__dict__ for s in preset_store.list_presets(base)]}


@router.post("/import")
async def import_preset(
    file: UploadFile = File(...),
    base: str = Form(...),
    overwrite: bool = Form(False),
    name: str = Form(""),
) -> dict[str, object]:
    if not base:
        raise HTTPException(status_code=400, detail="未设置预设文件夹路径")
    raw = await file.read()
    preset = _parse_preset(raw)
    pname = (name or "").strip() or (file.filename or "preset").rsplit(".", 1)[0]
    try:
        summary = preset_store.save(base, pname, preset, overwrite=overwrite)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail={"reason": "exists", "name": str(exc)}) from exc
    return {"ok": True, **summary.__dict__}


@router.get("/detail")
def get_preset(base: str, name: str) -> dict[str, object]:
    preset = preset_store.read_preset(base, name)
    if preset is None:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"name": name, "preset": preset}


class SavePresetRequest(BaseModel):
    base: str
    name: str
    preset: dict


@router.post("/save")
def save_preset(req: SavePresetRequest) -> dict[str, object]:
    """保存编辑后的预设（覆盖）。前端改片段开关/内容后调用。"""
    summary = preset_store.save(req.base, req.name, req.preset, overwrite=True)
    return {"ok": True, **summary.__dict__}


class PresetRef(BaseModel):
    base: str
    name: str


@router.post("/delete")
def delete_preset(ref: PresetRef) -> dict[str, object]:
    ok = preset_store.delete_preset(ref.base, ref.name)
    if not ok:
        raise HTTPException(status_code=404, detail="预设不存在或删除失败")
    return {"ok": True}


# ── 预设级正则：存在预设 JSON 的 regexScripts 键，仅该预设激活时生效（介于全局与卡内嵌之间）──

@router.get("/regex")
def get_preset_regex(base: str, name: str) -> dict[str, object]:
    """读某预设内嵌的正则脚本（regexScripts 键）。无则空。"""
    scripts = preset_store.read_regex(base, name)
    return {"items": scripts}


class PresetRegexRequest(BaseModel):
    base: str
    name: str
    scripts: list[dict]


@router.post("/regex")
def save_preset_regex(req: PresetRegexRequest) -> dict[str, object]:
    """把正则脚本写入某预设的 regexScripts 键（覆盖）。预设不存在 → 404。"""
    scripts = preset_store.write_regex(req.base, req.name, req.scripts)
    if scripts is None:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"ok": True, "items": scripts}
