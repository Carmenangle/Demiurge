"""通用数据表端点：读/导入模板/增删改行/导出。路由薄——落盘编排交给 table_store。

按 repo_id 隔离（<output_dir>/<repo_id>/tables.json）。好感度/纪要由 state/narrative 路由各自管，
本路由只管其余通用表（背包/技能/任务/角色/选项…）。base 由前端传 output_dir。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.ai_common import ChatModelReq
from app.services import chat_snapshot, llm, manual_table_fill, table_store

router = APIRouter()


@router.get("/")
def list_tables(output_dir: str, repo_id: str) -> dict[str, object]:
    """列出某作品线全部通用表（含列头/行/说明）。无库 → 空列表。"""
    return {"tables": table_store.load(output_dir, repo_id)}


class ImportRequest(BaseModel):
    output_dir: str
    repo_id: str
    template: dict[str, Any]
    replace: bool = False


@router.post("/import-template")
def import_template(req: ImportRequest) -> dict[str, object]:
    """导入 TavernDB chatSheets 模板定义通用表 schema。replace=覆盖，否则只补新表。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    n = table_store.import_template(req.output_dir, req.repo_id, req.template, replace=req.replace)
    return {"ok": True, "imported": n, "tables": table_store.load(req.output_dir, req.repo_id)}


class RowRequest(BaseModel):
    output_dir: str
    repo_id: str
    table: str
    values: dict[str, str] = {}


@router.post("/rows")
def add_row(req: RowRequest) -> dict[str, object]:
    """给某表新增一行。返回更新后的表列表。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    tables = table_store.load(req.output_dir, req.repo_id)
    n = table_store.apply_ops(tables, [{"op": "insert", "table": req.table, "values": req.values}])
    if n:
        table_store.save(req.output_dir, req.repo_id, tables)
    return {"ok": True, "tables": tables}


class UpdateRequest(BaseModel):
    output_dir: str
    repo_id: str
    table: str
    row: int
    values: dict[str, str]


@router.post("/update")
def update_row(req: UpdateRequest) -> dict[str, object]:
    """改某表某行的单元格（values 键为列名）。返回更新后的表列表。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    tables = table_store.load(req.output_dir, req.repo_id)
    n = table_store.apply_ops(
        tables, [{"op": "update", "table": req.table, "row": req.row, "values": req.values}])
    if n:
        table_store.save(req.output_dir, req.repo_id, tables)
    return {"ok": True, "tables": tables}


class DeleteRequest(BaseModel):
    output_dir: str
    repo_id: str
    table: str
    row: int


@router.post("/delete")
def delete_row(req: DeleteRequest) -> dict[str, object]:
    """删某表某行（0 基行号）。返回更新后的表列表。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    tables = table_store.load(req.output_dir, req.repo_id)
    n = table_store.apply_ops(tables, [{"op": "delete", "table": req.table, "row": req.row}])
    if n:
        table_store.save(req.output_dir, req.repo_id, tables)
    return {"ok": True, "tables": tables}


@router.get("/export")
def export_tables(output_dir: str, repo_id: str) -> dict[str, object]:
    """导出某作品线全部通用表（JSON，备份/迁移用）。"""
    return {"version": 1, "repo_id": repo_id, "tables": table_store.load(output_dir, repo_id)}


class CreateRequest(BaseModel):
    output_dir: str
    repo_id: str
    name: str
    columns: list[str]
    note: str = ""
    rule: str = ""
    col_types: dict[str, str] = {}
    key_col: str = ""


@router.post("/create")
def create_table(req: CreateRequest) -> dict[str, object]:
    """新建一张空通用表（引导式建表用）。表名重复/无列 → 400。返回更新后的表列表。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    tables = table_store.load(req.output_dir, req.repo_id)
    tbl = table_store.create_table(
        tables, req.name, req.columns, note=req.note, rule=req.rule,
        col_types=req.col_types, key_col=req.key_col)
    if tbl is None:
        raise HTTPException(status_code=400, detail="表名重复或没有有效列")
    table_store.save(req.output_dir, req.repo_id, tables)
    return {"ok": True, "tables": tables}


class DropRequest(BaseModel):
    output_dir: str
    repo_id: str
    table: str


@router.post("/drop")
def drop_table(req: DropRequest) -> dict[str, object]:
    """按表名删整表。返回更新后的表列表。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    tables = table_store.load(req.output_dir, req.repo_id)
    if table_store.drop_table(tables, req.table):
        table_store.save(req.output_dir, req.repo_id, tables)
    return {"ok": True, "tables": tables}


class MetaRequest(BaseModel):
    output_dir: str
    repo_id: str
    table: str
    note: str | None = None
    rule: str | None = None
    key_col: str | None = None
    mode: str | None = None


@router.post("/set-meta")
def set_meta(req: MetaRequest) -> dict[str, object]:
    """改某表的说明/更新规则/身份列/注入模式（full 全量 / retrieval 检索）。返回更新后的表列表。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    tables = table_store.load(req.output_dir, req.repo_id)
    if table_store.set_meta(tables, req.table, note=req.note, rule=req.rule,
                            key_col=req.key_col, mode=req.mode):
        table_store.save(req.output_dir, req.repo_id, tables)
    return {"ok": True, "tables": tables}


@router.get("/config")
def get_config(output_dir: str, repo_id: str) -> dict[str, object]:
    """读填表 6 参数（缺文件回退默认）。"""
    return {"config": table_store.load_config(output_dir, repo_id)}


class ConfigRequest(BaseModel):
    output_dir: str
    repo_id: str
    config: dict[str, Any] = {}


@router.post("/config")
def set_config(req: ConfigRequest) -> dict[str, object]:
    """写填表 6 参数（填表频率/回看/最小长度等）。返回落盘后的完整配置。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    return {"ok": True, "config": table_store.save_config(req.output_dir, req.repo_id, req.config)}


@router.get("/status")
def table_status(output_dir: str, repo_id: str, card_name: str = "") -> dict[str, object]:
    """返回当前会话层数及每张可补表的未记录进度。"""
    return manual_table_fill.table_status(
        output_dir, repo_id, card_name, chat_snapshot.load(repo_id),
    )


class ManualFillRequest(ChatModelReq):
    output_dir: str
    repo_id: str
    card_name: str = ""
    selected: list[str] = []
    recent_turns: int = 5
    batch_turns: int = 3
    overwrite: bool | None = None


@router.post("/manual-fill")
def manual_fill(req: ManualFillRequest) -> dict[str, object]:
    """按选表与回合范围调用填表 Agent；重叠时先返回确认，不擅自覆盖。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    try:
        return manual_table_fill.run_manual_fill(
            base=req.output_dir, repo_id=req.repo_id, card_name=req.card_name,
            selected=req.selected, recent_turns=max(1, min(req.recent_turns, 200)),
            batch_turns=max(1, min(req.batch_turns, 50)), overwrite=req.overwrite,
            base_url=req.base_url, api_key=req.api_key, model=req.model, proxy=req.proxy,
            chat_fn=llm.chat,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"手动填表失败：{exc}")
