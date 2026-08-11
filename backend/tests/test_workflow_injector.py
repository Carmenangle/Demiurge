"""纯注入逻辑测试：脱离 live ComfyUI 验证套值 + 提示词注入 + 缺失必填。"""
from app.services import workflow_injector


def test_套用暴露字段的用户值():
    api = {"5": {"inputs": {"steps": 1}}}
    exposed = [{"node_id": "5", "field": "steps"}]
    missing = workflow_injector.inject_template_values(api, exposed, {"5.steps": 20})
    assert api["5"]["inputs"]["steps"] == 20
    assert missing == []


def test_图像输入口为空记入缺失():
    api = {}
    exposed = [{"node_id": "7", "field": "image", "control": "image", "label": "底图"}]
    missing = workflow_injector.inject_template_values(api, exposed, {})
    assert missing == ["底图"]


def test_提示词注入首个常见文本字段():
    api = {"9": {"inputs": {"text": "old"}}}
    workflow_injector.inject_template_values(api, [], {}, "hello", "9")
    assert api["9"]["inputs"]["text"] == "hello"


def test_提示词无目标节点不注入():
    api = {"9": {"inputs": {"text": "old"}}}
    workflow_injector.inject_template_values(api, [], {}, "hello", "999")
    assert api["9"]["inputs"]["text"] == "old"


def test_未暴露的键不覆盖():
    api = {"5": {"inputs": {"steps": 1}}}
    missing = workflow_injector.inject_template_values(api, [], {"5.steps": 20})
    assert api["5"]["inputs"]["steps"] == 1  # 不在 exposed 内 → 不动
    assert missing == []


def test_追加多个model_only_lora并重接下游():
    api = {
        "10": {"class_type": "UNETLoader", "inputs": {}},
        "20": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["10", 0], "lora_name": "old", "strength_model": 0.8,
        }},
        "30": {"class_type": "KSampler", "inputs": {"model": ["20", 0]}},
    }
    workflow_injector.inject_lora_stack(api, "20", [
        {"name": "style.safetensors", "weight": 0.7},
        {"name": "a.safetensors", "weight": 0.9},
        {"name": "b.safetensors", "weight": 1.1},
    ])
    assert api["20"]["inputs"]["lora_name"] == "style.safetensors"
    assert api["31"]["inputs"]["model"] == ["20", 0]
    assert api["32"]["inputs"]["model"] == ["31", 0]
    assert api["30"]["inputs"]["model"] == ["32", 0]


def test_追加完整lora_loader时model和clip共同串联():
    api = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {"class_type": "LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["1", 1], "lora_name": "old",
            "strength_model": 0.8, "strength_clip": 0.8,
        }},
        "3": {"class_type": "KSampler", "inputs": {"model": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 1]}},
    }
    workflow_injector.inject_lora_stack(api, "2", [
        {"name": "style.safetensors", "weight": 0.7},
        {"name": "role.safetensors", "weight": 1.0},
    ])
    assert api["5"]["inputs"]["model"] == ["2", 0]
    assert api["5"]["inputs"]["clip"] == ["2", 1]
    assert api["3"]["inputs"]["model"] == ["5", 0]
    assert api["4"]["inputs"]["clip"] == ["5", 1]


def test_无lora模式把工作流内全部lora权重归零():
    api = {
        "1": {"class_type": "LoraLoader", "inputs": {
            "strength_model": 0.8, "strength_clip": 0.6,
        }},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"strength_model": 1.0}},
    }
    workflow_injector.disable_all_loras(api)
    assert api["1"]["inputs"]["strength_model"] == 0
    assert api["1"]["inputs"]["strength_clip"] == 0
    assert api["2"]["inputs"]["strength_model"] == 0
