"""角色动态状态端点：读某作品线的当前状态 + 人工修正/回滚（缺口6）。

状态属主是 character_state（<base>/<repo_id>/state.json）；本路由薄——校验+转发。
人工改标 source=user（设定注入，非剧情），不推进剧情回合。base 由前端传 output_dir。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import character_state

router = APIRouter()


def _dump(st: character_state.CharacterState) -> dict[str, object]:
    return {
        "card_name": st.card_name,
        "repo_id": st.repo_id,
        "数值": {k: v.to_dict() for k, v in st.数值.items()},
        "叙事": {k: v.to_dict() for k, v in st.叙事.items()},
        "快照": st.快照.to_dict(),
        "历史": st.历史,
    }


@router.get("/")
def get_state(output_dir: str, repo_id: str, card_name: str = "") -> dict[str, object]:
    """读某作品线当前状态。无文件 → 空状态（各字段为空，不报错）。"""
    st = character_state.load_state(output_dir, repo_id, card_name)
    return _dump(st)


class FieldEdit(BaseModel):
    field: str          # "数值/好感度" 或 "叙事/态度"
    value: object       # 数值→float；叙事→str


class PatchStateRequest(BaseModel):
    output_dir: str
    repo_id: str
    card_name: str = ""
    edits: list[FieldEdit]


@router.patch("/")
def patch_state(req: PatchStateRequest) -> dict[str, object]:
    """人工修正：把字段设为精确值（数值直接设，非累加），标 source=user 落盘。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    st = character_state.load_state(req.output_dir, req.repo_id, req.card_name)
    turn = character_state.current_turn(st)
    done = character_state.set_fields(
        st, [e.model_dump() for e in req.edits], turn=turn)
    character_state.save_state(req.output_dir, st)
    return {"updated": done, **_dump(st)}


class RollbackRequest(BaseModel):
    output_dir: str
    repo_id: str
    card_name: str = ""
    n: int = 1          # 撤销最近几条变更


@router.post("/rollback")
def rollback_state(req: RollbackRequest) -> dict[str, object]:
    """回滚：撤销最近 n 条变更，字段还原到审计历史的 from 值后落盘。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    if req.n < 1:
        raise HTTPException(status_code=400, detail="n 须 ≥ 1")
    st = character_state.load_state(req.output_dir, req.repo_id, req.card_name)
    undone = character_state.rollback_last(st, n=req.n)
    character_state.save_state(req.output_dir, st)
    return {"undone": undone, **_dump(st)}


# ── ⑤ 状态表字段删除 + 导入导出（新增字段复用 PATCH，会创建缺失的键）──


class DeleteFieldRequest(BaseModel):
    output_dir: str
    repo_id: str
    card_name: str = ""
    field: str          # "数值/好感度" 或 "叙事/态度"


@router.post("/delete-field")
def delete_field(req: DeleteFieldRequest) -> dict[str, object]:
    """删除一个状态字段。删不存在的字段 → 404。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    st = character_state.load_state(req.output_dir, req.repo_id, req.card_name)
    if not character_state.delete_field(st, req.field):
        raise HTTPException(status_code=404, detail="未找到该字段")
    character_state.save_state(req.output_dir, st)
    return {"ok": True, **_dump(st)}


@router.get("/export")
def export_state(output_dir: str, repo_id: str, card_name: str = "") -> dict[str, object]:
    """导出某作品线状态（JSON，供备份/迁移）。无文件 → 空状态结构。"""
    st = character_state.load_state(output_dir, repo_id, card_name)
    return {"version": 1, **_dump(st)}


class ImportStateRequest(BaseModel):
    output_dir: str
    repo_id: str
    card_name: str = ""
    state: dict       # 导出格式（含 数值/叙事/历史/快照）


@router.post("/import")
def import_state(req: ImportStateRequest) -> dict[str, object]:
    """导入整表状态（覆盖当前作品线状态）。repo_id/card_name 以请求为准。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    st = character_state.from_dict(req.state, repo_id=req.repo_id, card_name=req.card_name)
    character_state.save_state(req.output_dir, st)
    return {"ok": True, **_dump(st)}
