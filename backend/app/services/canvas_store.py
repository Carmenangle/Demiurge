"""画布布局持久化（canvas.json）：只存布局/连线/视口/灵感卡，不污染 generation_store/快照/角色卡。

文件位置：<output_dir>/<repo_id>/canvas.json（作品文件夹内）。
形状：
{
  "nodes": { "<nodeId>": { "x": 0, "y": 0, "w": 240, "h": 320, "custom": true } },
  "edges": [{ "source": "img-0", "target": "img-1" }],
  "viewport": { "x": 0, "y": 0, "scale": 1 },
  "inspiration_cards": [{ "id": "...", "title": "...", "content": "...", "kind": "preset", "x": 0, "y": 0, "w": 280, "h": 180 }],
  "deleted_ids": ["gen-<generationId>", ...]   # 已删除投影节点黑名单（防止 refresh 投影复活）
}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services import repo_meta


def _canvas_path(output_dir: str, repo_id: str) -> Path:
    folder = repo_meta.repo_folder(output_dir, repo_id) if output_dir else None
    if folder is None:
        return Path("") / f"canvas_{repo_id}.json"  # 无 output_dir 时不落盘（调用方应避免）
    return folder / "canvas.json"


def load_layout(output_dir: str, repo_id: str) -> dict[str, Any]:
    """读取该作品画布布局；文件缺失/损坏返回空结构（默认布局）。"""
    empty = {
        "nodes": {}, "edges": [],
        "viewport": {"x": 0, "y": 0, "scale": 1},
        "inspiration_cards": [],
        "reference_images": [],
        "deleted_ids": [],
    }
    if not output_dir or not repo_id:
        return empty
    try:
        path = _canvas_path(output_dir, repo_id)
        if not path.is_file():
            return empty
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "nodes": data.get("nodes") if isinstance(data.get("nodes"), dict) else {},
            "edges": data.get("edges") if isinstance(data.get("edges"), list) else [],
            "viewport": data.get("viewport") if isinstance(data.get("viewport"), dict)
            else {"x": 0, "y": 0, "scale": 1},
            # 向后兼容：旧 canvas.json 没该字段则返回空列表
            "inspiration_cards": data.get("inspiration_cards") if isinstance(data.get("inspiration_cards"), list) else [],
            "reference_images": data.get("reference_images") if isinstance(data.get("reference_images"), list) else [],
            "deleted_ids": data.get("deleted_ids") if isinstance(data.get("deleted_ids"), list) else [],
        }
    except Exception:
        return empty


def save_layout(output_dir: str, repo_id: str, layout: dict[str, Any]) -> None:
    """原子写布局（临时文件 + replace，避免半写损坏）。"""
    if not output_dir or not repo_id:
        return
    path = _canvas_path(output_dir, repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(layout, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)
