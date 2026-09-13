"""ComfyUI 工作流提交：模板读取、转换、注入、校验与提交。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.services import comfyui_client, model_lease, reranker, template_store, workflow_injector
from app.services.comfyui_client import ComfyError
from app.services.url_guard import validate_comfyui_url
from app.services.workflow_convert import ui_to_api


class WorkflowSubmissionError(ValueError):
    def __init__(self, status: int, detail: object):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _uniquify_output_filenames(api: dict) -> None:
    """输出文件名逐次唯一化（2026-08-30 用户实锤）：模板的 filename_prefix 若为固定
    字面量（含 ComfyUI 不展开的 %date:… 伪日期模式），每次提交都写同一个文件——
    首尾帧两任务几乎同时提交，后存者覆盖先存者，两个槽位/资产库记录指向同一文件。
    每次提交给输出前缀追加提交级唯一后缀；模板文件不必改。"""
    suffix = uuid.uuid4().hex[:8]
    for node in api.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        prefix = inputs.get("filename_prefix")
        if isinstance(prefix, str) and prefix.strip():
            inputs["filename_prefix"] = f"{prefix.strip()}_{suffix}"


def _ready_url(url: str) -> str:
    try:
        normalized = validate_comfyui_url(url)
    except ValueError as exc:
        raise WorkflowSubmissionError(400, str(exc)) from exc
    if not comfyui_client.is_up(normalized):
        raise WorkflowSubmissionError(400, "ComfyUI 未运行，请先启动")
    return normalized


def submit_template(template_id: str, values: dict[str, object], prompt: str = "",
                    url: str = "", client_id: str = "",
                    loras: list[dict[str, object]] | None = None,
                    lora_mode: str = "single") -> dict[str, object]:
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
    # 唯一输出前缀：模板自带 %date% 秒级前缀，同秒完成任务会互相覆盖输出文件
    workflow_injector.set_unique_output_prefix(
        api, f"Demiurge_{uuid.uuid4().hex[:12]}")
    missing = workflow_injector.inject_template_values(
        api,
        template.get("exposed", []),
        values,
        prompt,
        str(template.get("prompt_node_id") or ""),
    )
    if missing:
        raise WorkflowSubmissionError(422, {"missing": missing})
    # 模板会保存编辑时的 LoRA 节点值。自动插画未选中任何 LoRA 时必须清零，
    # 否则空角色栈会静默执行模板里遗留的任意风格/真人 LoRA。
    if lora_mode == "none" or not loras:
        workflow_injector.disable_all_loras(api)
    elif loras:
        lora_node_id = next((
            str(field.get("node_id") or "") for field in template.get("exposed", [])
            if str(field.get("binding") or "") == "lora_name"
            or (str(field.get("semantic") or "") == "lora_name"
                and str(field.get("field") or "") != "lora_name")
        ), "")
        if not workflow_injector.inject_lora_stack(api, lora_node_id, loras):
            raise WorkflowSubmissionError(
                422, "模板没有已标注且受支持的 LoRA 加载器，无法应用所选 LoRA 模式",
            )
    _uniquify_output_filenames(api)
    reranker.release_accelerator_memory()
    lease = model_lease.acquire(
        f"comfy-submit:{uuid.uuid4().hex}", "comfyui", priority=100, ttl_seconds=1800,
    )
    if lease is None:
        raise WorkflowSubmissionError(503, "本地模型资源正被更高优先级任务占用，请稍后重试")
    try:
        prompt_id = comfyui_client.submit_prompt(normalized_url, api, client_id)
    except ComfyError as exc:
        model_lease.release(lease.token)
        raise WorkflowSubmissionError(exc.status, f"提交失败：{exc.detail}") from exc
    if prompt_id:
        model_lease.rebind(lease.token, f"comfyui:{prompt_id}")
    else:
        model_lease.release(lease.token)
    return {"ok": True, "prompt_id": prompt_id, "node_count": len(api)}


def submit_graph(workflow: dict[str, object], url: str, client_id: str = "") -> dict[str, object]:
    normalized_url = _ready_url(url)
    try:
        api = ui_to_api(workflow, normalized_url)
    except Exception as exc:  # noqa: BLE001
        raise WorkflowSubmissionError(400, f"工作流转换失败：{exc}") from exc
    try:
        object_info = comfyui_client.fetch_object_info(normalized_url)
    except ComfyError as exc:
        raise WorkflowSubmissionError(exc.status, f"无法校验工作流输出节点：{exc.detail}") from exc
    output_nodes = [
        node_id for node_id, node in api.items()
        if isinstance(node, dict)
        and bool(object_info.get(str(node.get("class_type") or ""), {}).get("output_node"))
    ]
    if not output_nodes:
        raise WorkflowSubmissionError(
            422,
            "工作流没有可执行输出节点；单节点参数画布不能作为完整工作流提交，请重新打开卡片并选择完毕",
        )
    _uniquify_output_filenames(api)
    reranker.release_accelerator_memory()
    lease = model_lease.acquire(
        f"comfy-submit:{uuid.uuid4().hex}", "comfyui", priority=100, ttl_seconds=1800,
    )
    if lease is None:
        raise WorkflowSubmissionError(503, "本地模型资源正被更高优先级任务占用，请稍后重试")
    try:
        prompt_id = comfyui_client.submit_prompt(normalized_url, api, client_id)
    except ComfyError as exc:
        model_lease.release(lease.token)
        raise WorkflowSubmissionError(exc.status, f"ComfyUI 拒绝：{exc.detail[:800]}") from exc
    if prompt_id:
        model_lease.rebind(lease.token, f"comfyui:{prompt_id}")
    else:
        model_lease.release(lease.token)
    return {"ok": True, "prompt_id": prompt_id, "node_count": len(api)}
