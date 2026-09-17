"""`image_agent._build` 的建链路径回归（此前零覆盖）。

为什么需要它：`tool_agent_adapter.run` 用宽 `except Exception` 把 `_build` 抛出的异常
吞成一句「工具专家执行失败：…」文本，所以 `_build` 里的参数名写错不会暴露给用户，
只会让整条「工具专家」路由静默不可用。
2026-09-10 实锤一次：`build_model(..., retries=1)`，而签名早已改名 `sdk_retries`
（`llm.py:33`）→ `TypeError: build_model() got an unexpected keyword argument 'retries'`，
被宽 except 吞掉，路由一用即废且无人发现。

红线：本测试不得触达真实外部副作用——MCP 进程、技能目录、checkpointer、网络全部替掉。
"""
import types

from langchain_core.messages import AIMessage, HumanMessage

from app.services import (
    agent_store, generation_store, image_agent, image_gen, inspiration,
    mcp_client, skills_store,
)


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


# ---------------------------------------------------------------------------
# T4.3 低覆盖补测（2026-09-17）：工具集组装 / system 组装 / 四个工具的真实行为。
# 接缝与上文一致（create_agent 收参即吐哨兵），工具本体从 captured 的 tool_map
# 里取出**直接调**（`tool.func(...)`），行为面就是闭包函数体——不经图执行。
# ---------------------------------------------------------------------------


def _capture_build(monkeypatch, *, agent_cfg=None):
    """搭好 _build 的全部替身；返回 captured（含 tool_map / invokes / sink 等）。"""
    captured: dict = {}

    def fake_build_model(*args, **kwargs):
        captured["build_model_kwargs"] = kwargs
        invokes: list = []

        def _invoke(msgs):
            invokes.append(msgs)
            return AIMessage(content="masterpiece, 1girl")

        captured["invokes"] = invokes
        return types.SimpleNamespace(invoke=_invoke)

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        captured["tool_map"] = {t.name: t for t in kwargs["tools"]}
        return "AGENT_SENTINEL"

    monkeypatch.setattr(image_agent._llm, "build_model", fake_build_model)
    monkeypatch.setattr("langchain.agents.create_agent", fake_create_agent)
    monkeypatch.setattr(agent_store, "get_agent", lambda aid: agent_cfg)
    _isolate_collaborators(monkeypatch)
    return captured


def _run_build(captured, monkeypatch, **overrides):
    values = dict(chat_base="cb", chat_key="ck", chat_model="cm",
                  gen_base="gb", gen_key="gk", gen_model="gm",
                  image_sink=[], memory_mode="external_turn")
    values.update(overrides)
    agent = image_agent._build(**values)
    return agent, captured


def test_build_默认工具集与内置人设(monkeypatch):
    captured = _capture_build(monkeypatch)
    _run_build(captured, monkeypatch)
    assert captured["tool_map"].keys() == {"generate_image", "search_inspiration"}
    assert captured["system_prompt"].startswith(image_agent.AGENT_SYSTEM_BASE)
    assert captured["checkpointer"] is None                # external_turn 不挂 checkpointer


def test_build_带图物理裁剪工具集(monkeypatch):
    """has_images=True：只剩 image_to_image + search_inspiration，并追加专用指令块。"""
    captured = _capture_build(monkeypatch)
    _run_build(captured, monkeypatch, image_sink=[], has_images=True)
    assert captured["tool_map"].keys() == {"image_to_image", "search_inspiration"}
    assert "【当前对话用户已上传图片】" in captured["system_prompt"]
    assert "generate_image" not in captured["tool_map"]


def test_build_analyze_image已删除_删除锁(monkeypatch):
    """T4 遗留项收口（2026-09-17 用户拍板：删除）：

    `analyze_image` 工具副本原为「已定义但两分支均不注册」的死代码，
    且 system 提示词虚假声称可调用（模型一调就 unknown tool）。
    supervisor 侧的 `analyze` 一等路由（agent_graph.analyze_node）是独立活实现，
    不受本删除影响。本锁固化三件事：模块无此符号、工具表无此名、
    system 提示词不再声称「反推图片提示词」能力。
    """
    captured = _capture_build(monkeypatch)
    _run_build(captured, monkeypatch, image_sink=[], has_images=True)
    assert "analyze_image" not in captured["tool_map"]
    captured2 = _capture_build(monkeypatch)
    _run_build(captured2, monkeypatch, image_sink=[])
    assert "analyze_image" not in captured2["tool_map"]
    # 符号层面已删除 + 提示词不再虚假声称
    assert not hasattr(image_agent, "analyze_image")
    assert "analyze_image" not in image_agent.AGENT_SYSTEM_BASE
    assert "反推图片提示词" not in image_agent.AGENT_SYSTEM_BASE


def test_build_自定义agent人设记忆温度与工具开关(monkeypatch):
    cfg = {"systemPrompt": "  自定义人设  ", "memory": "喜欢冷色调",
           "temperature": 0.2, "tools": {"generate_image": False, "search_inspiration": False}}
    captured = _capture_build(monkeypatch, agent_cfg=cfg)
    _run_build(captured, monkeypatch)

    assert captured["system_prompt"] == (
        "自定义人设\n\n【长期记忆（关于用户/偏好）】\n喜欢冷色调")
    assert captured["build_model_kwargs"]["temperature"] == 0.2
    assert captured["tool_map"] == {}                      # 两个开关都关 → 工具集为空


def test_build_风格模板注入写法指引(monkeypatch):
    monkeypatch.setattr(image_agent, "guidance_for", lambda *a, **k: "STYLE-GUIDE")
    captured = _capture_build(monkeypatch)
    _run_build(captured, monkeypatch, style_template="国风厚涂")
    assert "【生图提示词写法】STYLE-GUIDE" in captured["system_prompt"]


def test_build_mcp工具并入与边界说明(monkeypatch):
    captured = _capture_build(monkeypatch)
    monkeypatch.setattr(mcp_client, "load_mcp_tools",        # 盖在 _isolate 之后
                        lambda: [types.SimpleNamespace(name="web_search")])
    _run_build(captured, monkeypatch)
    assert "web_search" in captured["tool_map"]
    assert "【外部工具（MCP）调用边界】" in captured["system_prompt"]
    assert "web_search" in captured["system_prompt"]


def test_build_mcp装载失败留痕不阻断(monkeypatch):
    def _boom():
        raise RuntimeError("MCP 连接失败")

    captured = _capture_build(monkeypatch)
    monkeypatch.setattr(mcp_client, "load_mcp_tools", _boom)  # 盖在 _isolate 之后
    agent, captured = _run_build(captured, monkeypatch)       # 不抛
    assert agent == "AGENT_SENTINEL"
    assert "【外部工具（MCP）调用边界】" not in captured["system_prompt"]
    assert "generate_image" in captured["tool_map"]           # 内置工具不受影响


def test_generate_image_成功落库并进sink(monkeypatch):
    monkeypatch.setattr(image_gen, "generate", lambda *a, **k: "http://x/1.png")
    monkeypatch.setattr(generation_store, "persist_image",
                        lambda *a, **k: {"id": "img-1", "url": "saved.png"})
    captured = _capture_build(monkeypatch)
    sink: list = []
    _run_build(captured, monkeypatch, image_sink=sink)

    out = captured["tool_map"]["generate_image"].func(prompt="a girl, cinematic")

    assert out == "SUCCESS: 已生成图片。提示词：a girl, cinematic"
    assert sink == [{"id": "img-1", "url": "saved.png"}]


def test_generate_image_失败返回ERROR不入sink(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("下游拒绝")

    monkeypatch.setattr(image_gen, "generate", _boom)
    captured = _capture_build(monkeypatch)
    sink: list = []
    _run_build(captured, monkeypatch, image_sink=sink)

    out = captured["tool_map"]["generate_image"].func(prompt="p")

    assert out.startswith("ERROR: 生图失败：下游拒绝")
    assert sink == []


def test_image_to_image_只收最近一条带图消息的全部图(monkeypatch):
    """多图全传；更早消息里的历史图不卷入（取最近一条带图消息后 break）。"""
    monkeypatch.setattr(image_gen, "generate_with_images",
                        lambda base, key, model, prompt, imgs, **k: "http://x/2.png")
    monkeypatch.setattr(generation_store, "persist_image",
                        lambda *a, **k: {"id": "img-2", "url": "saved.png"})
    captured = _capture_build(monkeypatch)
    sink: list = []
    _run_build(captured, monkeypatch, image_sink=sink, has_images=True)
    tool = captured["tool_map"]["image_to_image"]
    state = {"messages": [
        HumanMessage(content=[{"type": "image_url", "image_url": {"url": "历史图"}}]),
        AIMessage(content="好"),
        HumanMessage(content=[
            {"type": "text", "text": "给她们加帽子"},
            {"type": "image_url", "image_url": {"url": "本轮图A"}},
            {"type": "image_url", "image_url": {"url": "本轮图B"}},
        ]),
    ]}

    out = tool.func(prompt="给她们加帽子", state=state)

    assert out == "SUCCESS: 已基于 2 张参考图生成图片。提示词：给她们加帽子"
    assert sink == [{"id": "img-2", "url": "saved.png"}]


def test_image_to_image_无图与失败都返回ERROR(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("接口超时")

    captured = _capture_build(monkeypatch)
    sink: list = []
    _run_build(captured, monkeypatch, image_sink=sink, has_images=True)
    tool = captured["tool_map"]["image_to_image"]

    assert tool.func(prompt="p", state={"messages": [HumanMessage(content="没图")]}) == \
        "ERROR: 未找到用户上传的图片，无法图生图。"

    monkeypatch.setattr(image_gen, "generate_with_images", _boom)
    state = {"messages": [HumanMessage(
        content=[{"type": "image_url", "image_url": {"url": "u1"}}])]}
    assert tool.func(prompt="p", state=state).startswith("ERROR: 图生图失败：接口超时")
    assert sink == []


def test_search_inspiration_成功落灵感卡进sink(monkeypatch):
    monkeypatch.setattr(inspiration, "search_and_refine",
                        lambda *a, **k: {"title": "秋季穿搭", "content": "正文",
                                         "sources": [{"title": "s", "url": "u"}],
                                         "images": []})
    cards: list = []
    monkeypatch.setattr(generation_store, "persist_inspiration",
                        lambda tid, title, content, sources, images: cards.append(
                            {"id": "c1", "title": title, "content": content}) or cards[0])
    captured = _capture_build(monkeypatch)
    insp_sink: list = []
    _run_build(captured, monkeypatch, insp_sink=insp_sink)

    out = captured["tool_map"]["search_inspiration"].func(query="autumn outfit")

    assert out.startswith("SUCCESS: 已生成灵感卡「秋季穿搭」")
    assert insp_sink and insp_sink[0]["title"] == "秋季穿搭"


def test_search_inspiration_无结果与异常都返回ERROR(monkeypatch):
    from app.services.inspiration import NoResults

    captured = _capture_build(monkeypatch)
    insp_sink: list = []
    _run_build(captured, monkeypatch, insp_sink=insp_sink)
    tool = captured["tool_map"]["search_inspiration"]

    monkeypatch.setattr(inspiration, "search_and_refine",
                        lambda *a, **k: {"title": "", "content": "", "sources": []})
    assert tool.func(query="q") == "ERROR: 未能从搜索结果整理出内容。"
    assert insp_sink == []

    monkeypatch.setattr(inspiration, "search_and_refine",
                        lambda *a, **k: (_ for _ in ()).throw(NoResults("无结果")))
    assert tool.func(query="q") == "ERROR: 联网搜索无结果（网络或搜索源不可用）。"

    monkeypatch.setattr(inspiration, "search_and_refine",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络断")))
    assert tool.func(query="q") == "ERROR: 找灵感失败：网络断"
