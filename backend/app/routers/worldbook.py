"""独立世界书端点：导入/列表/读取/删除。路由薄——落盘交给 worldbook_store。

世界书文件夹路径来自前端设置（worldbookDir），随请求透传（与 characterDir 同模式）。
同名冲突：overwrite=false 且已存在 → 409，前端弹覆盖确认（对标角色卡导入语义）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.services import character_store, worldbook_edit, worldbook_store

router = APIRouter()


def _parse_book(raw: bytes) -> dict:
    """解析 ST 世界书 JSON：必须是含 entries 的对象。"""
    try:
        book = json.loads(raw.decode("utf-8-sig", "replace"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"世界书 JSON 非法：{exc}") from exc
    if not isinstance(book, dict) or "entries" not in book:
        raise HTTPException(status_code=400, detail="不是有效的世界书（缺 entries）")
    return book


@router.get("/")
def list_worldbooks(base: str = "") -> dict[str, object]:
    if not base:
        return {"items": []}
    return {"items": [s.__dict__ for s in worldbook_store.list_books(base)]}


@router.post("/import")
async def import_worldbook(
    file: UploadFile = File(...),
    base: str = Form(...),
    overwrite: bool = Form(False),
    name: str = Form(""),
) -> dict[str, object]:
    """导入独立世界书到 base。名称默认取文件名（去扩展名），可显式指定。同名且 overwrite=False → 409。"""
    if not base:
        raise HTTPException(status_code=400, detail="未设置世界书文件夹路径")
    raw = await file.read()
    book = _parse_book(raw)
    wb_name = (name or "").strip() or (file.filename or "worldbook").rsplit(".", 1)[0]
    try:
        summary = worldbook_store.save(base, wb_name, book, overwrite=overwrite)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409, detail={"reason": "exists", "name": str(exc)},
        ) from exc
    return {"ok": True, **summary.__dict__}


@router.get("/detail")
def get_worldbook(base: str, name: str) -> dict[str, object]:
    book = worldbook_store.read_book(base, name)
    if book is None:
        raise HTTPException(status_code=404, detail="世界书不存在")
    return {"name": name, "book": book}


class WorldbookRef(BaseModel):
    base: str
    name: str


@router.post("/delete")
def delete_worldbook(ref: WorldbookRef) -> dict[str, object]:
    ok = worldbook_store.delete_book(ref.base, ref.name)
    if not ok:
        raise HTTPException(status_code=404, detail="世界书不存在或删除失败")
    return {"ok": True}


# ── ⑤ 条目级增删改：独立世界书(base+name) / 卡内嵌(character_dir+card_name)统一定位 ──


class EntryFields(BaseModel):
    content: str = ""
    comment: str = ""
    keys: list[str] = []
    constant: bool = False
    enabled: bool = True


class EntryLocation(BaseModel):
    # 二选一：独立世界书传 base+name；卡内嵌传 character_dir+card_name
    base: str = ""
    name: str = ""
    character_dir: str = ""
    card_name: str = ""


def _read_book(loc: EntryLocation) -> tuple[dict, str]:
    """按定位读出 book dict，返回 (book, kind)。kind ∈ {standalone, card}。缺定位/不存在 → 400/404。"""
    if loc.base and loc.name:
        book = worldbook_store.read_book(loc.base, loc.name)
        if book is None:
            raise HTTPException(status_code=404, detail="世界书不存在")
        return book, "standalone"
    if loc.character_dir and loc.card_name:
        book = character_store.read_worldbook(loc.character_dir, loc.card_name)
        if book is None:
            raise HTTPException(status_code=404, detail="该卡没有内嵌世界书")
        return book, "card"
    raise HTTPException(status_code=400, detail="缺少世界书定位（base+name 或 character_dir+card_name）")


def _write_book(loc: EntryLocation, kind: str, book: dict) -> None:
    if kind == "standalone":
        worldbook_store.save(loc.base, loc.name, book, overwrite=True)
    else:
        character_store.write_worldbook(loc.character_dir, loc.card_name, book)


@router.post("/entries")
def list_entries(loc: EntryLocation) -> dict[str, object]:
    """列出某世界书全部条目（含 index，供前端定位编辑）。"""
    book, _ = _read_book(loc)
    return {"entries": worldbook_edit.list_entries(book)}


class EntryAddRequest(EntryLocation):
    entry: EntryFields


@router.post("/entry/add")
def add_entry(req: EntryAddRequest) -> dict[str, object]:
    """新增一条世界书条目，返回其 index。"""
    book, kind = _read_book(req)
    idx = worldbook_edit.add_entry(book, req.entry.model_dump())
    _write_book(req, kind, book)
    return {"ok": True, "index": idx}


class EntryUpdateRequest(EntryLocation):
    index: int
    entry: EntryFields


@router.post("/entry/update")
def update_entry(req: EntryUpdateRequest) -> dict[str, object]:
    """更新第 index 条条目。越界 → 404。"""
    book, kind = _read_book(req)
    if not worldbook_edit.update_entry(book, req.index, req.entry.model_dump()):
        raise HTTPException(status_code=404, detail="条目不存在")
    _write_book(req, kind, book)
    return {"ok": True}


class EntryDeleteRequest(EntryLocation):
    index: int


@router.post("/entry/delete")
def delete_entry(req: EntryDeleteRequest) -> dict[str, object]:
    """删除第 index 条条目。越界 → 404。"""
    book, kind = _read_book(req)
    if not worldbook_edit.delete_entry(book, req.index):
        raise HTTPException(status_code=404, detail="条目不存在")
    _write_book(req, kind, book)
    return {"ok": True}
