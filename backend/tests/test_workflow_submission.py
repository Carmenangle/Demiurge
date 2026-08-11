import json

import pytest

from app.services import workflow_submission
from app.services.comfyui_client import ComfyError


def _ready(monkeypatch):
    monkeypatch.setattr(workflow_submission.comfyui_client, "is_up", lambda url: True)


def _output_info(monkeypatch):
    monkeypatch.setattr(
        workflow_submission.comfyui_client,
        "fetch_object_info",
        lambda _url: {"SaveImage": {"output_node": True}},
    )


def test_模板提交集中完成读取注入和上游提交(tmp_path, monkeypatch):
    source = tmp_path / "workflow.json"
    source.write_text(json.dumps({"1": {"class_type": "Node", "inputs": {}}}), encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source), "exposed": [], "prompt_node_id": "",
    })
    _ready(monkeypatch)
    calls = []
    order = []
    monkeypatch.setattr(
        workflow_submission.reranker,
        "release_accelerator_memory",
        lambda: order.append("release"),
    )
    monkeypatch.setattr(
        workflow_submission.comfyui_client,
        "submit_prompt", lambda url, api, client_id: (
            order.append("submit") or calls.append((url, api, client_id)) or "prompt-1"
        ),
    )

    result = workflow_submission.submit_template(
        "template-1", {}, "", "http://127.0.0.1:8188", "client-1",
    )

    assert result == {"ok": True, "prompt_id": "prompt-1", "node_count": 1}
    assert calls[0][2] == "client-1"
    assert order == ["release", "submit"]


def test_模板缺少必填输入返回422(tmp_path, monkeypatch):
    source = tmp_path / "workflow.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source), "exposed": [], "prompt_node_id": "",
    })
    _ready(monkeypatch)
    monkeypatch.setattr(workflow_submission.workflow_injector, "inject_template_values", lambda *args: ["steps"])

    with pytest.raises(workflow_submission.WorkflowSubmissionError) as exc_info:
        workflow_submission.submit_template("template-1", {}, "", "http://127.0.0.1:8188")

    assert exc_info.value.status == 422
    assert exc_info.value.detail == {"missing": ["steps"]}


def test_自动插画选择lora但模板没有有效加载器时明确失败(tmp_path, monkeypatch):
    source = tmp_path / "workflow.json"
    source.write_text(json.dumps({"1": {"class_type": "Node", "inputs": {}}}), encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source), "exposed": [], "prompt_node_id": "",
    })
    _ready(monkeypatch)

    with pytest.raises(workflow_submission.WorkflowSubmissionError) as exc_info:
        workflow_submission.submit_template(
            "template-1", {}, "", "http://127.0.0.1:8188", loras=[
                {"name": "role.safetensors", "weight": 0.8},
            ],
        )

    assert exc_info.value.status == 422
    assert "LoRA 加载器" in str(exc_info.value.detail)


def test_多LoRA模板提交主动插入全部角色并重接所有采样器(tmp_path, monkeypatch):
    source = tmp_path / "workflow.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source),
        "exposed": [{
            "node_id": "20", "field": "lora_name", "binding": "lora_name",
        }],
        "prompt_node_id": "",
    })
    _ready(monkeypatch)
    monkeypatch.setattr(workflow_submission, "ui_to_api", lambda _graph, _url: {
        "10": {"class_type": "UNETLoader", "inputs": {}},
        "20": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["10", 0], "lora_name": "old", "strength_model": 0.8,
        }},
        "30": {"class_type": "KSampler", "inputs": {"model": ["20", 0]}},
        "31": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["20", 0]}},
    })
    captured = {}
    monkeypatch.setattr(workflow_submission.reranker, "release_accelerator_memory", lambda: None)
    monkeypatch.setattr(
        workflow_submission.comfyui_client,
        "submit_prompt",
        lambda _url, api, _client_id: captured.setdefault("api", api) or "prompt-1",
    )

    workflow_submission.submit_template(
        "template-1", {}, "", "http://127.0.0.1:8188",
        lora_mode="multi",
        loras=[
            {"name": "role-a.safetensors", "weight": 0.9},
            {"name": "role-b.safetensors", "weight": 1.0},
        ],
    )

    api = captured["api"]
    assert api["20"]["inputs"]["lora_name"] == "role-a.safetensors"
    assert api["32"]["inputs"]["lora_name"] == "role-b.safetensors"
    assert api["30"]["inputs"]["model"] == ["32", 0]
    assert api["31"]["inputs"]["model"] == ["32", 0]


def test_图提交保留ComfyUI错误语义(monkeypatch):
    _ready(monkeypatch)
    _output_info(monkeypatch)
    monkeypatch.setattr(
        workflow_submission,
        "ui_to_api",
        lambda graph, url: {"1": {"class_type": "SaveImage", "inputs": {}}},
    )
    monkeypatch.setattr(
        workflow_submission.comfyui_client,
        "submit_prompt",
        lambda *args: (_ for _ in ()).throw(ComfyError("invalid node", 400)),
    )

    with pytest.raises(workflow_submission.WorkflowSubmissionError) as exc_info:
        workflow_submission.submit_graph({}, "http://127.0.0.1:8188")

    assert exc_info.value.status == 400
    assert exc_info.value.detail == "ComfyUI 拒绝：invalid node"


def test_图提交在ComfyUI执行前释放Reranker(monkeypatch):
    _ready(monkeypatch)
    _output_info(monkeypatch)
    order = []
    monkeypatch.setattr(
        workflow_submission,
        "ui_to_api",
        lambda graph, url: {"1": {"class_type": "SaveImage", "inputs": {}}},
    )
    monkeypatch.setattr(
        workflow_submission.reranker,
        "release_accelerator_memory",
        lambda: order.append("release"),
    )
    monkeypatch.setattr(
        workflow_submission.comfyui_client,
        "submit_prompt",
        lambda *args: order.append("submit") or "prompt-1",
    )

    workflow_submission.submit_graph({}, "http://127.0.0.1:8188")
    assert order == ["release", "submit"]


def test_图提交在请求ComfyUI前拒绝无输出的单节点残片(monkeypatch):
    _ready(monkeypatch)
    _output_info(monkeypatch)
    monkeypatch.setattr(
        workflow_submission,
        "ui_to_api",
        lambda graph, url: {"39": {"class_type": "CLIPTextEncode", "inputs": {}}},
    )
    submitted = []
    monkeypatch.setattr(
        workflow_submission.comfyui_client,
        "submit_prompt",
        lambda *args: submitted.append(args),
    )

    with pytest.raises(workflow_submission.WorkflowSubmissionError) as exc_info:
        workflow_submission.submit_graph({}, "http://127.0.0.1:8188")

    assert exc_info.value.status == 422
    assert "没有可执行输出节点" in str(exc_info.value.detail)
    assert submitted == []
