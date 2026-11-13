import json

import pytest

from app.services import workflow_submission
from app.services.comfyui_client import ComfyError


def _ready(monkeypatch):
    monkeypatch.setattr(workflow_submission.comfyui_client, "is_up", lambda url: True)


def _output_info(monkeypatch):
    monkeypatch.setattr(
        workflow_submission.comfyui_client,
        "fetch_object_info_types",
        lambda _url, _types: {"SaveImage": {"output_node": True}},
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


def test_预编译_api_workflow_优先于_source_path(monkeypatch):
    api = {"1": {"class_type": "SaveImage", "inputs": {}}}
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": "does-not-exist.json", "api_workflow": api,
        "exposed": [], "prompt_node_id": "",
    })
    _ready(monkeypatch)
    captured = {}

    def fake_submit(_url, value, _client_id):
        captured["api"] = value
        return "prompt-api"

    monkeypatch.setattr(workflow_submission.reranker, "release_accelerator_memory", lambda: None)
    monkeypatch.setattr(workflow_submission.comfyui_client, "submit_prompt", fake_submit)

    result = workflow_submission.submit_template(
        "template-api", {}, "", "http://127.0.0.1:8188",
    )

    assert result["ok"] is True
    assert result["prompt_id"] == "prompt-api"
    assert captured["api"]["1"]["class_type"] == "SaveImage"
    # 模板里的快照不能被提交链就地改写（下次提交仍从原始快照复制）。
    assert api["1"]["class_type"] == "SaveImage"


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


@pytest.mark.parametrize("lora_mode", ["single", "multi"])
def test_角色LoRA栈在两种模式均插入全部角色并重接所有采样器(
    lora_mode, tmp_path, monkeypatch,
):
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
        lora_mode=lora_mode,
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


def test_自动插画空LoRA栈禁用模板残留LoRA(tmp_path, monkeypatch):
    source = tmp_path / "workflow.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source), "exposed": [], "prompt_node_id": "",
    })
    _ready(monkeypatch)
    monkeypatch.setattr(workflow_submission, "ui_to_api", lambda _graph, _url: {
        "10": {"class_type": "UNETLoader", "inputs": {}},
        "20": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["10", 0],
            "lora_name": "Krea2-真人-NSW.safetensors",
            "strength_model": 0.8,
        }},
        "30": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["20", 0]}},
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
        lora_mode="single", loras=[],
    )

    assert captured["api"]["20"]["inputs"]["strength_model"] == 0


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


def test_输出文件名逐次唯一化_同名前缀不再互相覆盖():
    api = {
        "24": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": "Krea2-2ST-%date:yyyyMMddhhmmss%", "images": ["26", 0]}},
        "19": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "a.safetensors"}},
    }

    workflow_submission._uniquify_output_filenames(api)
    first = api["24"]["inputs"]["filename_prefix"]
    assert first.startswith("Krea2-2ST-%date:yyyyMMddhhmmss%_")  # 保留原前缀
    assert first != "Krea2-2ST-%date:yyyyMMddhhmmss%"

    workflow_submission._uniquify_output_filenames(api)
    assert api["24"]["inputs"]["filename_prefix"] != first  # 逐次不同：首尾帧两任务不再同名覆盖

    assert api["19"]["inputs"] == {"lora_name": "a.safetensors"}  # 非输出节点不动


def test_输出文件名唯一化跳过无前缀或空前缀节点():
    api = {
        "1": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "  "}},
        "3": "not-a-node",
    }
    workflow_submission._uniquify_output_filenames(api)
    assert "filename_prefix" not in api["1"]["inputs"]
    assert api["2"]["inputs"]["filename_prefix"] == "  "


def _submit_with_lora_template(monkeypatch, tmp_path, *, preserve_loras, loras):
    source = tmp_path / "workflow.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source), "exposed": [], "prompt_node_id": "",
        "preserve_loras": preserve_loras,
    })
    _ready(monkeypatch)
    monkeypatch.setattr(workflow_submission, "ui_to_api", lambda _graph, _url: {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax.safetensors"}},
        "20": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["10", 0],
            "lora_name": "minimax_h3_turbo_v4_step600_ema.safetensors",
            "strength_model": 1.0,
        }},
        "30": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"clip": ["20", 0]}},
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
        lora_mode="none", loras=loras,
    )
    return captured["api"]


def test_保留模板自带LoRA时空栈不清零(monkeypatch, tmp_path):
    """视频模板的蒸馏加速 LoRA 是 6 步采样成立的前提：空角色栈不得把它清零。"""
    api = _submit_with_lora_template(monkeypatch, tmp_path, preserve_loras=True, loras=[])

    assert api["20"]["inputs"]["strength_model"] == 1.0
    assert api["20"]["inputs"]["lora_name"] == "minimax_h3_turbo_v4_step600_ema.safetensors"


def test_保留模板自带LoRA时外部角色LoRA不串入(monkeypatch, tmp_path):
    """插画链即便带了角色 LoRA，也不能写进保留 LoRA 的模板（会破坏蒸馏权重）。"""
    api = _submit_with_lora_template(
        monkeypatch, tmp_path, preserve_loras=True,
        loras=[{"name": "role.safetensors", "weight": 0.8}],
    )

    assert api["20"]["inputs"]["lora_name"] == "minimax_h3_turbo_v4_step600_ema.safetensors"
    assert api["20"]["inputs"]["strength_model"] == 1.0
    assert "32" not in api


def test_模板只有rgthree动态槽时也能注入所选LoRA(tmp_path, monkeypatch):
    """模板没有手标 lora_name、只有 Power Lora Loader 时，锚点自动落到模型链末端；
    模板原有条目被本次所选 LoRA 替换（与标准 loader 的锚点语义一致）。"""
    source = tmp_path / "workflow.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source), "exposed": [], "prompt_node_id": "",
    })
    _ready(monkeypatch)
    monkeypatch.setattr(workflow_submission, "ui_to_api", lambda _graph, _url: {
        "1504": {"class_type": "Power Lora Loader (rgthree)", "inputs": {"model": ["10", 0]}},
        "1446": {"class_type": "Power Lora Loader (rgthree)", "inputs": {
            "model": ["1504", 0],
            "lora_1": {"on": True, "lora": "template.safetensors", "strength": 0.8,
                       "strengthTwo": None},
        }},
        "30": {"class_type": "KSampler", "inputs": {"model": ["1446", 0]}},
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
        loras=[{"name": "role.safetensors", "weight": 0.9}],
    )

    inputs = captured["api"]["1446"]["inputs"]
    assert inputs["lora_1"] == {
        "on": True, "lora": "role.safetensors", "strength": 0.9, "strengthTwo": None,
    }
    assert "lora_2" not in inputs
    assert "lora_1" not in captured["api"]["1504"]["inputs"]  # 只动锚点，上游加载器不碰


def test_未适配lora节点时422点名节点类型(tmp_path, monkeypatch):
    source = tmp_path / "workflow.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_submission.template_store, "get_template", lambda _id: {
        "source_path": str(source), "exposed": [], "prompt_node_id": "",
    })
    _ready(monkeypatch)
    monkeypatch.setattr(workflow_submission, "ui_to_api", lambda _graph, _url: {
        "1": {"class_type": "AceStepSFTLoraLoader", "inputs": {}},
    })

    with pytest.raises(workflow_submission.WorkflowSubmissionError) as exc_info:
        workflow_submission.submit_template(
            "template-1", {}, "", "http://127.0.0.1:8188",
            loras=[{"name": "role.safetensors", "weight": 0.8}],
        )

    assert exc_info.value.status == 422
    assert "AceStepSFTLoraLoader" in str(exc_info.value.detail)
