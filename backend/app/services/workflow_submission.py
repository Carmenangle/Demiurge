"""ComfyUI 工作流提交：模板读取、转换、注入、校验与提交。"""
from __future__ import annotations

import json
from pathlib import Path

from app.services import comfyui_client, reranker, template_store, workflow_injector
from app.services.comfyui_client import ComfyError
from app.services.url_guard import validate_comfyui_url
from app.services.workflow_convert import ui_to_api


class WorkflowSubmissionError(ValueError):
    def __init__(self, status: int, detail: object):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _ready_url(url: str) -> str:
    try:
        normalized = validate_comfyui_url(url)
    except ValueError as exc:
        raise WorkflowSubmissionError(400, str(exc)) from exc
    if not comfyui_client.is_up(normalized):
        raise WorkflowSubmissionError(400, "ComfyUI 未运行，请先启动")
    return normalized


def submit_template(template_id: str, values: dict[str, object], prompt: str,
                    url: str, client_id: str = "") -> dict[str, object]:
    template = template_store.get_template(template_id)
    if template is None:
        raise WorkflowSubmissionError(400, "模板不存在")
    source = str(template.get("source_path") or "")
    if not source or not Path(source).is_file():
        raise WorkflowSubmissionError(400, "模板缺少原始工作流文件，无法启动")

    normalized_url = _ready_url(url)
    try:
        workflow = json.loads(Path(source).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WorkflowSubmissionError(400, f"工作流 JSON 解析失败：{exc}") from exc

    api = ui_to_api(workflow, normalized_url)
    missing = workflow_injector.inject_template_values(
        api,
        template.get("exposed", []),
        values,
        prompt,
        str(template.get("prompt_node_id") or ""),
    )
    if missing:
        raise WorkflowSubmissionError(422, {"missing": missing})
    reranker.release_accelerator_memory()
    try:
        prompt_id = comfyui_client.submit_prompt(normalized_url, api, client_id)
    except ComfyError as exc:
        raise WorkflowSubmissionError(exc.status, f"提交失败：{exc.detail}") from exc
    return {"ok": True, "prompt_id": prompt_id, "node_count": len(api)}


def submit_graph(workflow: dict[str, object], url: str, client_id: str = "") -> dict[str, object]:
    normalized_url = _ready_url(url)
    try:
        api = ui_to_api(workflow, normalized_url)
    except Exception as exc:  # noqa: BLE001
        raise WorkflowSubmissionError(400, f"工作流转换失败：{exc}") from exc
    reranker.release_accelerator_memory()
    try:
        prompt_id = comfyui_client.submit_prompt(normalized_url, api, client_id)
    except ComfyError as exc:
        raise WorkflowSubmissionError(exc.status, f"ComfyUI 拒绝：{exc.detail[:800]}") from exc
    return {"ok": True, "prompt_id": prompt_id, "node_count": len(api)}
