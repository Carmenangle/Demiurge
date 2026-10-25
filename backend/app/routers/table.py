"""通用数据表端点：读/导入模板/增删改行/导出。路由薄——落盘编排交给 table_store。

按 repo_id 隔离（<output_dir>/<repo_id>/tables.json）。好感度/纪要由 state/narrative 路由各自管，
本路由只管其余通用表（背包/技能/任务/角色/选项…）。base 由前端传 output_dir。
行级增删改统一走 `table_store.mutate`（唯一事务接缝：读 → apply_ops → 有变化才落盘，审计 C5）；
建表/删表/改 meta 统一走 `table_store.mutate_structure`（同口径的结构接缝，审计 P6）。
本路由只保留**校验与 no-op 响应口径**（`/create` 无变化 → 400；`/drop`、`/set-meta` 无变化 → 静默 200）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import chat_snapshot, llm, manual_table_fill, table_store
from app.services.ai_common import ChatModelReq


def _mask_proxy(proxy: str) -> str:
    """代理走线只暴露 host:port，别把带账号的 URL 回给前端。"""
    raw = (proxy or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(raw if "://" in raw else f"http://{raw}")
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}" if host else "自定义代理"
    except Exception:  # noqa: BLE001 [EXC-保留] 解析失败按「自定义代理」呈现
        return "自定义代理"

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
    tables, _n = table_store.mutate(
        req.output_dir, req.repo_id,
        [{"op": "insert", "table": req.table, "values": req.values}])
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
    tables, _n = table_store.mutate(
        req.output_dir, req.repo_id,
        [{"op": "update", "table": req.table, "row": req.row, "values": req.values}])
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
    tables, _n = table_store.mutate(
        req.output_dir, req.repo_id,
        [{"op": "delete", "table": req.table, "row": req.row}])
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
    tables, created = table_store.mutate_structure(
        req.output_dir, req.repo_id,
        lambda t: table_store.create_table(
            t, req.name, req.columns, note=req.note, rule=req.rule,
            col_types=req.col_types, key_col=req.key_col))
    if not created:
        raise HTTPException(status_code=400, detail="表名重复或没有有效列")
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
    tables, _changed = table_store.mutate_structure(
        req.output_dir, req.repo_id,
        lambda t: table_store.drop_table(t, req.table))
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
    tables, _changed = table_store.mutate_structure(
        req.output_dir, req.repo_id,
        lambda t: table_store.set_meta(t, req.table, note=req.note, rule=req.rule,
                                       key_col=req.key_col, mode=req.mode))
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
    # 2026-09-13：显式区间（缺口补跑）。非空时只处理这些层、不做覆盖确认；
    # 空 = 旧行为（按回看层数 / 游标规划）。
    ranges: list[list[int]] = []


@router.get("/manual-fill/progress")
def manual_fill_progress(output_dir: str, repo_id: str) -> dict[str, object]:
    """轮询手动填表批次进度（2026-09-13：十几批要跑几分钟，UI 需要实时反馈）。"""
    if not (output_dir and repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    state = manual_table_fill.get_fill_progress(f"{output_dir}|{repo_id}")
    return {"running": state["running"], "batch_done": state["batch_done"], "batch_total": state["batch_total"]}


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
            chat_fn=llm.chat, ranges=req.ranges,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        # 2026-10-20 用户实锤「手动填表失败：调用对话模型失败：Connection error.」：
        # `build_model` 只在 proxy **非空**时注入代理 http_client（见其 docstring 的
        # 「Connection error / timed out」教训），所以这个异常几乎必然与代理走线有关。
        # 但调用方看不出到底是「直连」还是「走了哪个代理」⇒ 把走线写进错误信息。
        route = f"代理 {_mask_proxy(req.proxy)}" if (req.proxy or "").strip() else "直连（未注入代理）"
        raise HTTPException(
            status_code=502, detail=f"手动填表失败：{exc}｜模型 {req.model or '?'} @ {route}")
