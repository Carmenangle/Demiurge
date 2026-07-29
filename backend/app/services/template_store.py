"""工作流模板持久化：每个模板存为一个 JSON 文件。

模板结构：
{
  "id": "uuid",
  "name": "模板名",
  "source_path": "原始 workflow 文件路径（可选）",
  "exposed": [
    {
      "node_id": "5",
      "field": "steps",
      "label": "采样步数",       # 展示用标签
      "control": "number",       # 控件类型 text/number/textarea/select/image/seed/boolean
      "semantic": "steps",       # 语义标签，供 AI 自动填充
      "default": 20              # 默认值
    }
  ],
  "created_at": 1700000000.0,
  "updated_at": 1700000000.0
}
"""
from __future__ import annotations

import json
import time
import uuid

from app.config import TEMPLATES_DIR


def _ensure_dir() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _path(template_id: str) -> "object":
    return TEMPLATES_DIR / f"{template_id}.json"


def _normalize_ids(values) -> list[str]:
    out: list[str] = []
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in out:
            out.append(item)
    return out


def _normalize(record: dict) -> dict:
    """返回标准模板副本：节点 id 非空字符串、去重保序，并兼容旧输入字段。"""
    if not isinstance(record, dict):
        return record
    normalized = dict(record)
    inputs = _normalize_ids(record.get("input_node_ids"))
    for legacy in (record.get("prompt_node_id"), record.get("image_node_id")):
        inputs = _normalize_ids([*inputs, legacy])
    exposed: list[dict] = []
    for field in record.get("exposed") or []:
        if not isinstance(field, dict):
            continue
        node_id = str(field.get("node_id") or "").strip()
        if node_id:
            exposed.append({**field, "node_id": node_id})
    normalized.update({
        "exposed": exposed,
        "node_order": _normalize_ids(record.get("node_order")),
        "input_node_ids": inputs,
        "output_node_ids": _normalize_ids(record.get("output_node_ids")),
        "primary_output_node_id": str(record.get("primary_output_node_id") or "").strip(),
        "prompt_node_id": str(record.get("prompt_node_id") or "").strip(),
        "image_node_id": str(record.get("image_node_id") or "").strip(),
    })
    return normalized


def ordered_node_ids(record: dict) -> list[str]:
    """画布节点顺序：用户顺序、其余暴露节点、输入节点、输出节点。"""
    tpl = _normalize(record)
    exposed = [field["node_id"] for field in tpl.get("exposed", [])]
    return _normalize_ids([
        *tpl.get("node_order", []), *exposed,
        *tpl.get("input_node_ids", []), *tpl.get("output_node_ids", []),
    ])


def list_templates() -> list[dict]:
    _ensure_dir()
    items: list[dict] = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            items.append(_normalize(json.loads(p.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    items.sort(key=lambda t: t.get("updated_at", 0), reverse=True)
    return items


def get_template(template_id: str) -> dict | None:
    p = _path(template_id)
    if not p.exists():
        return None
    try:
        return _normalize(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def save_template(data: dict, template_id: str | None = None) -> dict:
    _ensure_dir()
    now = time.time()
    if template_id:
        existing = get_template(template_id) or {}
        created = existing.get("created_at", now)
    else:
        template_id = uuid.uuid4().hex
        created = now

    # 新建模板时嵌入工作流快照，使模板独立于源文件后续修改
    workflow_data = data.get("workflow_data")
    if workflow_data is None:
        src = data.get("source_path", "")
        if src:
            try:
                from pathlib import Path as _Path
                workflow_data = json.loads(_Path(src).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                workflow_data = None

    record = _normalize({
        "id": template_id,
        "name": data.get("name", "未命名模板"),
        "source_path": data.get("source_path", ""),
        "workflow_data": workflow_data,
        "exposed": data.get("exposed", []),
        "node_order": data.get("node_order", []),
        "description": data.get("description", ""),
        "prompt_node_id": data.get("prompt_node_id", ""),
        "image_node_id": data.get("image_node_id", ""),
        "input_node_ids": data.get("input_node_ids", []),
        "output_node_ids": data.get("output_node_ids", []),
        "primary_output_node_id": data.get("primary_output_node_id", ""),
        "created_at": created,
        "updated_at": now,
    })
    _path(template_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def delete_template(template_id: str) -> bool:
    p = _path(template_id)
    if p.exists():
        p.unlink()
        return True
    return False
