"""capability_registry P0 单测：注册期闸门 / manifest 漂移 / handler 可导入 / 可用性标记。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import capability_registry as cr


# ── 注册期闸门 ───────────────────────────────────────────────────────────────

def test_operation必须动词点宾语且全局唯一():
    with pytest.raises(ValueError, match="动词.宾语"):
        cr.register(cr.Capability(operation="noverb", category="comfyui", description="x"))
    with pytest.raises(ValueError, match="重复注册"):
        cr.register(cr.Capability(
            operation="workflow.list_templates", category="comfyui", description="x"))


def test_枚举字段非法立即拒绝():
    with pytest.raises(ValueError, match="category"):
        cr.register(cr.Capability(operation="bad.one", category="nope", description="x"))
    with pytest.raises(ValueError, match="side_effect_level"):
        cr.register(cr.Capability(operation="bad.two", category="comfyui", description="x",
                                  side_effect_level="nuclear"))
    with pytest.raises(ValueError, match="channel"):
        cr.register(cr.Capability(operation="bad.three", category="comfyui", description="x",
                                  channel="fiber"))
    with pytest.raises(ValueError, match="needs_model"):
        cr.register(cr.Capability(operation="bad.four", category="comfyui", description="x",
                                  needs_model="quantum"))
    with pytest.raises(ValueError, match="handler"):
        cr.register(cr.Capability(operation="bad.five", category="comfyui", description="x",
                                  handler="no_colon"))


def test_首批四条能力注册完整且分级正确():
    ops = {cap.operation for cap in cr.all_capabilities()}
    assert {"workflow.list_templates", "workflow.read_exposed_fields",
            "workflow.submit_template", "workflow.submit_batch"} <= ops
    submit = cr.get("workflow.submit_batch")
    assert submit is not None
    assert submit.side_effect_level == cr.SIDE_EFFECT_EXPENSIVE
    assert submit.channel == cr.CHANNEL_QUEUE
    assert submit.needs_model == "image"
    assert cr.get("workflow.list_templates").side_effect_level == cr.SIDE_EFFECT_READONLY


# ── handler 真源校验（真实失败路径：指向不存在的函数必须被抓出）──────────────

def test_validate_handlers全部可导入():
    assert cr.validate_handlers() == []


def test_validate_handlers抓出不存在的handler(monkeypatch):
    broken = cr.Capability(
        operation="ghost.action", category="comfyui", description="x",
        handler="app.services.capability_handlers:no_such_function")
    cr.register(broken)
    try:
        errors = cr.validate_handlers()
        assert any("ghost.action" in e and "no_such_function" in e for e in errors)
    finally:
        cr._REGISTRY.pop("ghost.action", None)


# ── manifest 漂移门禁 ────────────────────────────────────────────────────────

def test_生成物与注册表逐字节一致():
    manifest_path = Path(__file__).resolve().parents[1] / "app" / "generated" / "capability_manifest.json"
    current = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert current == cr.build_manifest()


def test_manifest条目字段完整():
    required = {"operation", "category", "description", "params_schema",
                "needs_model", "side_effect_level", "channel", "handler"}
    for cap in cr.all_capabilities():
        item = cap.to_manifest()
        assert required <= set(item)
        assert len(item["description"].strip()) >= 8  # description 是编排准确性第一决定因素，锁非空


# ── 批量适配器失败隔离 ───────────────────────────────────────────────────────

def test_submit_batch单条失败隔离(monkeypatch):
    from app.services import capability_handlers as ch
    from app.services.workflow_submission import WorkflowSubmissionError

    calls: list[dict] = []

    def fake(template_id, values, prompt, url, client_id="", loras=None, lora_mode="single"):
        calls.append(values)
        if values.get("seed") == 2:
            raise WorkflowSubmissionError(400, "ComfyUI 未运行，请先启动")
        return {"ok": True, "prompt_id": f"p{len(calls)}"}

    monkeypatch.setattr("app.services.capability_handlers.submit_template", fake)
    out = ch.submit_batch("t", [{"seed": 1}, {"seed": 2}, {"seed": 3}, "bad"], "p", "http://x")
    assert out["submitted"] == 2 and out["failed"] == 2
    assert out["results"][1]["ok"] is False
    assert "未运行" in out["results"][1]["detail"]
    assert out["results"][3]["detail"] == "变体值必须是对象"


# ── 运行时可用性标记 ─────────────────────────────────────────────────────────

def test_未配置模型打available_false():
    items = cr.with_availability(frozenset({"chat"}))
    by_op = {item["operation"]: item for item in items}
    assert by_op["workflow.list_templates"]["available"] is True   # needs_model=None
    assert by_op["workflow.submit_batch"]["available"] is False    # image 未配置
    items_full = cr.with_availability(frozenset({"chat", "image"}))
    by_op_full = {item["operation"]: item for item in items_full}
    assert by_op_full["workflow.submit_batch"]["available"] is True
