"""模型名枚举校验：拦编造的文件名，但绝不误拦。"""
import pytest

from app.services import model_options
from app.services.model_options import validate_plan

# object_info 的真实形状：枚举是 [[选项...], {配置}]，非枚举第 0 项是类型名
FAKE_INFO = {
    "UNETLoader": {"input": {"required": {
        "unet_name": [["krea2.safetensors", "flux.safetensors"], {}],
        "weight_dtype": [["default", "fp8_e4m3fn"], {}],
    }}},
    "LoraLoaderModelOnly": {"input": {"required": {
        "lora_name": [["style.safetensors", "Krea2-线条动漫_平涂-2D动漫.safetensors"], {}],
        "strength_model": ["FLOAT", {"default": 1.0}],
    }}},
}


@pytest.fixture
def fake_object_info(monkeypatch):
    calls: list[str] = []

    def fake(url, node="", *a, **k):
        calls.append(node)
        return {node: FAKE_INFO[node]} if node in FAKE_INFO else {}

    monkeypatch.setattr(model_options, "fetch_object_info", fake)
    return calls


def _nodes():
    return [
        {"id": "14", "type": "UNETLoader"},
        {"id": "19", "type": "LoraLoaderModelOnly"},
    ]


def _plan(node_id, widget, value):
    return {"is_orchestration": True, "summary": "换模型",
            "ops": [{"node_id": node_id, "input": widget,
                     "action": "set_widget", "value": value}]}


def test_options_for_reads_enum(fake_object_info):
    got = model_options.options_for("http://x", "UNETLoader", "unet_name")
    assert got == ["krea2.safetensors", "flux.safetensors"]


def test_options_for_non_enum_widget_returns_empty(fake_object_info):
    """FLOAT 这类不是枚举，不能误当成可选列表。"""
    assert model_options.options_for(
        "http://x", "LoraLoaderModelOnly", "strength_model") == []


def test_options_for_unknown_node(fake_object_info):
    assert model_options.options_for("http://x", "NoSuchNode", "unet_name") == []


def test_valid_name_passes(fake_object_info):
    plan = _plan("14", "unet_name", "krea2.safetensors")
    assert validate_plan(plan, _nodes(), "http://x") == []
    assert len(plan["ops"]) == 1


def test_invented_name_is_dropped(fake_object_info):
    """核心：编造的文件名必须拦掉，否则跑到加载那步才报错。"""
    plan = _plan("14", "unet_name", "totally-made-up.safetensors")
    dropped = validate_plan(plan, _nodes(), "http://x")
    assert len(dropped) == 1
    assert plan["ops"] == []
    assert "已移除" in plan["summary"]


def test_case_and_slash_differences_are_repaired_not_dropped(fake_object_info):
    plan = _plan("14", "unet_name", "KREA2.SAFETENSORS")
    assert validate_plan(plan, _nodes(), "http://x") == []
    assert plan["ops"][0]["value"] == "krea2.safetensors"


def test_cjk_lora_name_passes(fake_object_info):
    plan = _plan("19", "lora_name", "Krea2-线条动漫_平涂-2D动漫.safetensors")
    assert validate_plan(plan, _nodes(), "http://x") == []


def test_unreachable_comfyui_passes_everything(monkeypatch):
    """ComfyUI 没启动时不能把正确操作也拦掉。"""
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(model_options, "fetch_object_info", boom)
    plan = _plan("14", "unet_name", "anything.safetensors")
    assert validate_plan(plan, _nodes(), "http://x") == []
    assert len(plan["ops"]) == 1


def test_non_model_widgets_untouched(fake_object_info):
    plan = {"is_orchestration": True, "ops": [
        {"node_id": "14", "input": "weight_dtype",
         "action": "set_widget", "value": "whatever"}]}
    assert validate_plan(plan, _nodes(), "http://x") == []
    assert len(plan["ops"]) == 1


def test_other_actions_untouched(fake_object_info):
    plan = {"is_orchestration": True, "ops": [
        {"node_id": "14", "input": "unet_name",
         "action": "set_image", "image_index": 1}]}
    assert validate_plan(plan, _nodes(), "http://x") == []
    assert len(plan["ops"]) == 1


def test_unknown_node_id_is_skipped(fake_object_info):
    plan = _plan("999", "unet_name", "nope.safetensors")
    assert validate_plan(plan, _nodes(), "http://x") == []


def test_only_bad_op_removed_others_kept(fake_object_info):
    plan = {"is_orchestration": True, "summary": "", "ops": [
        {"node_id": "14", "input": "unet_name",
         "action": "set_widget", "value": "krea2.safetensors"},
        {"node_id": "19", "input": "lora_name",
         "action": "set_widget", "value": "ghost.safetensors"},
    ]}
    dropped = validate_plan(plan, _nodes(), "http://x")
    assert len(dropped) == 1
    assert len(plan["ops"]) == 1
    assert plan["ops"][0]["node_id"] == "14"


def test_empty_plan_is_noop(fake_object_info):
    plan = {"is_orchestration": True, "ops": []}
    assert validate_plan(plan, _nodes(), "http://x") == []
