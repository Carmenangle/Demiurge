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
