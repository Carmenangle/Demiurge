"""Agent 插件注册表单测：内置插件完整、路由可用性条件。"""
from __future__ import annotations

from app.services import agent_plugins


def test_内置插件覆盖全部路由():
    routes = {p.route for p in agent_plugins.all_plugins()}
    assert {"answer", "roleplay", "generate", "img2img", "analyze",
            "video", "inspire", "tool_agent", "edit", "plan"} <= routes
    plan = agent_plugins.get("plan")
    assert plan is not None and "智能编造" in plan.label


def test_路由可用性条件():
    tool_on = lambda cfg, key: True  # noqa: E731
    assert agent_plugins.route_available(
        "answer", has_images=False, has_card=False, has_mcp=False,
        agent_cfg={}, tool_on=tool_on) is True
    assert agent_plugins.route_available(
        "roleplay", has_images=False, has_card=False, has_mcp=False,
        agent_cfg={}, tool_on=tool_on) is False
    assert agent_plugins.route_available(
        "roleplay", has_images=False, has_card=True, has_mcp=False,
        agent_cfg={}, tool_on=tool_on) is True
    assert agent_plugins.route_available(
        "generate", has_images=True, has_card=False, has_mcp=False,
        agent_cfg={}, tool_on=tool_on) is False
    assert agent_plugins.route_available(
        "img2img", has_images=False, has_card=False, has_mcp=False,
        agent_cfg={}, tool_on=tool_on) is False
    assert agent_plugins.route_available(
        "tool_agent", has_images=False, has_card=False, has_mcp=False,
        agent_cfg={}, tool_on=tool_on) is False
    assert agent_plugins.route_available(
        "tool_agent", has_images=False, has_card=False, has_mcp=True,
        agent_cfg={}, tool_on=tool_on) is True


def test_工具开关关闭时路由不可用():
    tool_on = lambda cfg, key: key != "generate_image"  # noqa: E731
    assert agent_plugins.route_available(
        "generate", has_images=False, has_card=False, has_mcp=False,
        agent_cfg={}, tool_on=tool_on) is False


def test_full模式智能编造节点走自由循环(monkeypatch, tmp_path):
    from app.services import agent_graph, capability_sandbox, fabric_loop

    class FakeOutcome:
        status = "done"
        reply = "自由循环完成"

    called = {}

    def fake_run_loop(**kwargs):
        called.update(kwargs)
        return FakeOutcome()

    monkeypatch.setattr(fabric_loop, "run_loop", fake_run_loop)
    from app.services import plan_tasks
    monkeypatch.setattr(plan_tasks, "_agent_access_mode",
                        lambda: capability_sandbox.ACCESS_FULL)
    ctx = {"output_dir": str(tmp_path), "chat_base": "", "chat_key": "",
           "chat_model": "m", "thread_id": "t1", "message_id": "m1"}
    result = agent_graph.plan_compiler_node({"user_text": "写个文件", "trace": [], "_ctx": ctx})
    assert result["result_text"] == "自由循环完成"
    assert called.get("access_mode") == capability_sandbox.ACCESS_FULL
    assert called.get("output_dir") == str(tmp_path)
    assert called.get("lease_id")
