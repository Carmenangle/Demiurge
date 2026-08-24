"""角色卡端点：导入/列表/读取/删除/导出。路由薄——解析交给 character_card，落盘交给 character_store。

角色卡文件夹路径来自前端设置（characterDir），随请求透传（与 outputDir 同模式）。
一键 bundle：卡若内嵌世界书/正则，随卡一起落到同一文件夹。
同名覆盖：overwrite=true 才覆盖；覆盖前若已有对话记录，前端应先调 /export-chat 保留。
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.services import character_card, character_store
from app.services.character_card import CardParseError

router = APIRouter()


@router.get("/")
def list_characters(base: str = "") -> dict[str, object]:
    if not base:
        return {"items": []}
    items = [s.__dict__ for s in character_store.list_cards(base)]
    return {"items": items}


class ScanRequest(BaseModel):
    base: str
    worldbook_dir: str = ""   # 已设则把卡内嵌世界书外拆成独立世界书并从卡剥离（含存量迁移）


@router.post("/scan")
def scan_loose(req: ScanRequest) -> dict[str, object]:
    """扫描角色卡文件夹根目录下手动放入的散装卡文件(.json/.png)，解析入库后删源。
    已设 worldbook_dir 时把内嵌世界书外拆成独立世界书（含存量卡迁移）。供前端刷新时调用。"""
    if not req.base:
        raise HTTPException(status_code=400, detail="未设置角色卡文件夹路径")
    return character_store.scan_loose_cards(req.base, req.worldbook_dir)


class SnapshotRequest(BaseModel):
    character_dir: str        # 源库（角色卡文件夹）
    card_name: str
    output_dir: str           # 仓库文件夹根（作品文件夹落于此）
    user_name: str = ""       # 绑定的用户人设名（当时选中档，快照进作品）
    user_persona: str = ""    # 绑定的用户人设描述


@router.post("/snapshot-to-work")
def snapshot_to_work(req: SnapshotRequest) -> dict[str, object]:
    """新建作品时把源库卡+用户人设快照进作品仓库文件夹（卡+世界书+正则+头像+persona.json），运行时优先读快照。

    作品文件夹 = <output_dir>/<safe(卡名)>/（父作品仓库名=卡名）。幂等：已快照过则不覆盖，
    保快照隔离（改源卡/人设不回灌已建作品）。缺参/源无卡 → created=False（对话回退读源库，不阻断）。
    """
    if not (req.character_dir and req.card_name and req.output_dir):
        return {"ok": False, "created": False}
    # 父作品文件夹 = <output_dir>/<safe(卡名)>/（与 addCardWork 建父仓库 name=卡名 一致）
    work_folder = str(character_store.card_dir(req.output_dir, req.card_name))
    created = character_store.snapshot_to_work(req.character_dir, req.card_name, work_folder)
    persona_created = character_store.snapshot_persona_to_work(
        req.output_dir, req.card_name, req.user_name, req.user_persona,
    )
    return {"ok": True, "created": created, "persona_created": persona_created}


class RepoSnapshotRequest(BaseModel):
    character_dir: str
    card_names: list[str]
    output_dir: str
    repo_id: str


@router.post("/snapshot-to-repo")
def snapshot_to_repo(req: RepoSnapshotRequest) -> dict[str, object]:
    """绑定保存时把所选角色卡快照到当前仓库，供角色卡模式和头像表情读取。"""
    if not (req.character_dir and req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少角色卡目录、仓库目录或仓库 ID")
    return {"ok": True, **character_store.snapshot_cards_to_repo(
        req.character_dir, req.card_names, req.output_dir, req.repo_id,
    )}


@router.post("/preview")
async def preview_import(file: UploadFile = File(...)) -> dict[str, object]:
    """只解析不落盘：返回卡名、是否带世界书/正则，供前端做 bundle/覆盖确认。"""
    raw = await file.read()
    try:
        card = character_card.parse_card_bytes(raw, file.filename or "")
    except CardParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    entries = (card.character_book or {}).get("entries") or [] if card.has_worldbook else []
    return {
        "name": card.name,
        "spec": card.spec,
        "has_worldbook": card.has_worldbook,
        "has_regex": card.has_regex,
        "worldbook_entries": len(entries),
        "regex_count": len(card.regex_scripts),
    }


@router.post("/import")
async def import_character(
    file: UploadFile = File(...),
    base: str = Form(...),
    overwrite: bool = Form(False),
    worldbook_dir: str = Form(""),
) -> dict[str, object]:
    """导入一张卡到 base 文件夹。PNG 原图一并留存。同名且 overwrite=False → 409。
    已设 worldbook_dir 时把内嵌世界书外拆成独立世界书（名=卡名）并从卡剥离。"""
    if not base:
        raise HTTPException(status_code=400, detail="未设置角色卡文件夹路径")
    raw = await file.read()
    try:
        card = character_card.parse_card_bytes(raw, file.filename or "")
    except CardParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    is_png = raw.startswith(character_card.PNG_SIGNATURE) or (file.filename or "").lower().endswith(".png")
    try:
        character_store.save_card(
            base, card, avatar=raw if is_png else None, overwrite=overwrite,
        )
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"reason": "exists", "name": str(exc),
                    "has_chat": character_store.has_chat(base, card.name)},
        ) from exc
    if worldbook_dir:
        character_store.extract_embedded_worldbook(base, card.name, worldbook_dir)
    # 外拆后重新取 summary（has_worldbook 已变 False，卡变干净）
    summary = character_store._summary(character_store.card_dir(base, card.name), card.name)
    return {"ok": True, **summary.__dict__}


class CardRef(BaseModel):
    base: str
    name: str


@router.get("/detail")
def get_character(base: str, name: str) -> dict[str, object]:
    card = character_store.read_card(base, name)
    if card is None:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    return card


@router.get("/repo-detail")
def get_character_repo_detail(output_dir: str, repo_id: str, name: str) -> dict[str, object]:
    """画布模式：优先读仓库快照角色卡，不存在回退源库。"""
    base = character_store.repo_card_base(output_dir, repo_id, name)
    if not base:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    card = character_store.read_card(base, name)
    if card is None:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    return card


class CardUpdateRequest(BaseModel):
    base: str
    name: str
    description: str = ""
    first_mes: str = ""
    creator_notes: str = ""


@router.patch("/detail")
def update_character(req: CardUpdateRequest) -> dict[str, object]:
    """保存角色描述、开场白与创作者注释；空字符串是有效值。"""
    if not (req.base and req.name):
        raise HTTPException(status_code=400, detail="缺少角色卡目录或名称")
    try:
        return character_store.update_card_fields(req.base, req.name, req.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="角色卡不存在") from exc


@router.get("/media")
def get_character_media(
    base: str, name: str, output_dir: str = "", repo_id: str = "",
) -> dict[str, object]:
    resolved_base = character_store.repo_card_base(output_dir, repo_id, name) or base
    if not character_store.card_exists(resolved_base, name):
        raise HTTPException(status_code=404, detail="角色卡不存在")
    return {
        "base": resolved_base,
        "folder": character_store.card_dir(resolved_base, name).name,
        "has_avatar": (character_store.card_dir(resolved_base, name) / character_store.AVATAR_FILE).is_file(),
        "expressions": character_store.list_expressions(resolved_base, name),
    }


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...), base: str = Form(...), name: str = Form(...),
) -> dict[str, object]:
    raw = await file.read()
    if not raw.startswith(character_card.PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="头像必须是 PNG 图片")
    try:
        character_store.write_avatar(base, name, raw)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="角色卡不存在") from exc
    return {"ok": True}


@router.post("/expression")
async def upload_expression(
    file: UploadFile = File(...), base: str = Form(...), name: str = Form(...),
    expression: str = Form(...),
) -> dict[str, object]:
    raw = await file.read()
    if not raw.startswith(character_card.PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="表情必须是 PNG 图片")
    try:
        filename = character_store.write_expression(base, name, expression, raw)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="角色卡不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "file": filename}


@router.get("/regex")
def get_character_regex(base: str, name: str) -> dict[str, object]:
    """读该卡内嵌正则（regex.json）。显示层脚本前端渲染时用，无则空。"""
    return {"items": character_store.read_regex(base, name)}


@router.get("/export-chat")
def export_chat(base: str, name: str) -> dict[str, object]:
    """导出该卡的对话记录（覆盖前保留用）。无记录返回 null。"""
    return {"name": name, "chat": character_store.read_chat(base, name)}


@router.post("/delete")
def delete_character(ref: CardRef) -> dict[str, object]:
    ok = character_store.delete_card(ref.base, ref.name)
    if not ok:
        raise HTTPException(status_code=404, detail="角色卡不存在或删除失败")
    return {"ok": True}
