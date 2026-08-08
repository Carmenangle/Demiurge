import pytest

from app.services import workflow_port_planner as planner
from app.services import image_prompt_style


def _args(**overrides):
    values = {
        "scene": "把提示词改成夜景",
        "image_count": 0,
        "node_schema": [{"id": "1", "widgets": []}],
        "model_name": "anima.safetensors",
        "style": "",
        "style_template": "",
        "force": False,
        "repo_id": "",
        "base_url": "url",
        "api_key": "key",
        "model": "model",
        "proxy": "",
    }
    values.update(overrides)
    return values


def test_plan_owns_parse_force_and_enrichment_order(monkeypatch):
    calls = []
    monkeypatch.setattr(image_prompt_style, "guidance_for", lambda *_: "ANIMA")
    monkeypatch.setattr(planner, "_validate_models", lambda *_: calls.append("validate"))
    monkeypatch.setattr(planner, "_inject_lora", lambda *_: calls.append("lora"))
    monkeypatch.setattr(planner, "_inject_colors", lambda *_: calls.append("colors"))

    result = planner.plan(
        **_args(force=True),
        chat_fn=lambda *_args, **_kwargs: "```json\n{\"is_orchestration\":false,\"ops\":[]}\n```",
    )

    assert result["is_orchestration"] is True
    assert calls == ["validate", "lora", "colors"]


def test_plan_rejects_missing_scene_and_nodes_before_model_call():
    called = False

    def chat_fn(*_args, **_kwargs):
        nonlocal called
        called = True
        return "{}"

    with pytest.raises(planner.WorkflowPortPlanError, match="内容为空"):
        planner.plan(**_args(scene="", image_count=0), chat_fn=chat_fn)
    assert called is False


def test_plan_reports_unparseable_model_output():
    with pytest.raises(planner.WorkflowPortPlanResponseError, match="未返回可解析"):
        planner.plan(**_args(), chat_fn=lambda *_args, **_kwargs: "not-json")
