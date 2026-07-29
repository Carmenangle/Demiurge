import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.workflow_parser import parse_workflow
from app.services import template_store

router = APIRouter()


@router.get("/")
def list_workflows() -> dict[str, object]:
    return {"items": []}


@router.get("/scan")
def scan_workflows(dir: str) -> dict[str, object]:
    """扫描目录及子目录下所有 .json 文件，返回文件列表。"""
    base = Path(dir)
    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=400, detail="目录不存在或不是文件夹")
    items = [
        {"name": p.name, "path": str(p), "rel": str(p.relative_to(base))}
        for p in sorted(base.rglob("*.json"))
        if p.is_file()
    ]
    return {"items": items}


class ParseRequest(BaseModel):
    path: str | None = None
    workflow: dict | None = None


@router.post("/parse")
def parse(req: ParseRequest) -> dict[str, object]:
    """解析 workflow：传 path 从磁盘读取，或直接传 workflow JSON。"""
    if req.workflow is not None:
        workflow = req.workflow
    elif req.path:
        p = Path(req.path)
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=400, detail="文件不存在")
        try:
            workflow = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")
    else:
        raise HTTPException(status_code=400, detail="需提供 path 或 workflow")

    nodes = parse_workflow(workflow)
    return {"nodes": nodes, "node_count": len(nodes)}


@router.get("/raw")
def raw_workflow(path: str) -> dict:
    """按路径返回原始工作流 JSON（供模板编辑页的 ComfyUI 画布预览载入）。"""
    p = Path(path)
    if not p.is_file():
        raise HTTPException(status_code=400, detail="文件不存在")
    try:
        return {"workflow": json.loads(p.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")


class ExposedField(BaseModel):
    node_id: str
    field: str
    label: str = ""
    control: str = "text"
    semantic: str = ""
    default: object | None = None


class TemplateRequest(BaseModel):
    name: str = "未命名模板"
    source_path: str = ""
    exposed: list[ExposedField] = []
    node_order: list[str] = []
    description: str = ""        # 能力描述（人工或 AI 生成），供对话 Agent 路由
    prompt_node_id: str = ""     # 提示词输入口节点 id（旧字段，保留兼容）
    image_node_id: str = ""      # 图像输入口节点 id（旧字段，保留兼容）
    input_node_ids: list[str] = []   # 替换输入节点列表（左侧接线/自身 widget）
    output_node_ids: list[str] = []  # 替换输出节点列表(右侧接线)
    primary_output_node_id: str = "" # 主输出节点（多输出时优先取用此节点产物）


@router.get("/templates")
def list_templates() -> dict[str, object]:
    return {"items": template_store.list_templates()}


@router.get("/templates/{template_id}")
def get_template(template_id: str) -> dict:
    tpl = template_store.get_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl


@router.get("/templates/{template_id}/raw")
def get_template_raw(template_id: str) -> dict:
    """返回模板原始工作流 JSON + 暴露节点 id 列表，供锁定画布载入并只显示这些节点。"""
    tpl = template_store.get_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    # 优先用保存时嵌入的快照（独立于源文件后续修改）；旧模板回退到 source_path
    workflow = tpl.get("workflow_data")
    if workflow is None:
        src = tpl.get("source_path", "")
        if not src or not Path(src).is_file():
            raise HTTPException(status_code=400, detail="模板缺少原始工作流文件")
        try:
            workflow = json.loads(Path(src).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise HTTPException(status_code=400, detail=f"工作流 JSON 解析失败：{e}")
    exposed_ids = template_store.ordered_node_ids(tpl)
    order = tpl.get("node_order", [])
    input_ids = tpl.get("input_node_ids", [])
    output_ids = tpl.get("output_node_ids", [])
    return {
        "workflow": workflow,
        "exposed_ids": exposed_ids,
        "node_order": order,
        "description": tpl.get("description", ""),
        "prompt_node_id": tpl.get("prompt_node_id", ""),
        "image_node_id": tpl.get("image_node_id", ""),
        "input_node_ids": input_ids,
        "output_node_ids": output_ids,
        "primary_output_node_id": tpl.get("primary_output_node_id", ""),
    }


@router.post("/templates")
def create_template(req: TemplateRequest) -> dict:
    return template_store.save_template(req.model_dump())


@router.put("/templates/{template_id}")
def update_template(template_id: str, req: TemplateRequest) -> dict:
    if template_store.get_template(template_id) is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return template_store.save_template(req.model_dump(), template_id)


@router.delete("/templates/{template_id}")
def remove_template(template_id: str) -> dict[str, bool]:
    return {"ok": template_store.delete_template(template_id)}
