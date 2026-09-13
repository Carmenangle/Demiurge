"""`image_agent._build` 的建链路径回归（此前零覆盖）。

为什么需要它：`tool_agent_adapter.run` 用宽 `except Exception` 把 `_build` 抛出的异常
吞成一句「工具专家执行失败：…」文本，所以 `_build` 里的参数名写错不会暴露给用户，
只会让整条「工具专家」路由静默不可用。
2026-09-10 实锤一次：`build_model(..., retries=1)`，而签名早已改名 `sdk_retries`
（`llm.py:33`）→ `TypeError: build_model() got an unexpected keyword argument 'retries'`，
被宽 except 吞掉，路由一用即废且无人发现。

红线：本测试不得触达真实外部副作用——MCP 进程、技能目录、checkpointer、网络全部替掉。
"""
from app.services import image_agent, mcp_client, skills_store


def _isolate_collaborators(monkeypatch) -> None:
    """只留建链主干：MCP 工具、技能片段、checkpointer 全部换成惰性替身。

    `mcp_client.load_mcp_tools()` 会去连真实 MCP 服务器（进程/网络），
    `skills_store.enabled_prompt_fragments()` 读用户数据目录——
    两者在 `_build` 里都被宽 `except Exception` 兜着，真跑起来只会引入慢与不确定。
    """
    monkeypatch.setattr(mcp_client, "load_mcp_tools", lambda: [])
    monkeypatch.setattr(skills_store, "enabled_prompt_fragments", lambda: [])


def test_构建链不再因参数名漂移抛TypeError(monkeypatch):
    """`_build` 必须能走到 `create_agent`，且送给 `build_model` 的关键字是现行签名。

    断言 `sdk_retries` 在、`retries` 不在：前者是现行参数名，后者是旧名（一传就 TypeError）。
    """
    captured: dict = {}

    def fake_build_model(*args, **kwargs):
        captured["build_model_kwargs"] = kwargs
        return "MODEL_SENTINEL"

    def fake_create_agent(**kwargs):
        captured["create_agent_kwargs"] = kwargs
        return "AGENT_SENTINEL"

    monkeypatch.setattr(image_agent._llm, "build_model", fake_build_model)
    monkeypatch.setattr("langchain.agents.create_agent", fake_create_agent)
    _isolate_collaborators(monkeypatch)

    agent = image_agent._build(
        "chat-base", "chat-key", "chat-model",
        "gen-base", "gen-key", "gen-model",
        [],                      # image_sink：仅工具执行时写入，建链阶段不碰
        memory_mode="external_turn",   # 跳过 get_saver()，不碰 checkpointer 库
    )

    assert agent == "AGENT_SENTINEL"                                  # 真走到建链尾
    assert captured["create_agent_kwargs"]["model"] == "MODEL_SENTINEL"
    assert captured["build_model_kwargs"]["sdk_retries"] == 1
    assert "retries" not in captured["build_model_kwargs"]
