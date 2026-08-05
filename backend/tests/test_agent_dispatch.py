"""Supervisor 分派 Interface 测试：模型拥有语义判断，代码只校验能力条件。"""
import json
import random
import threading

import pytest

from app.services import agency, agent_context, agent_graph as ag, image_prompt_extract, worldbook, worldbook_store
from app.services import roleplay_agency as ra
from app.services.agent_contracts import RunContext


def _ctx(**over) -> dict:
    base = {"chat_base": "b", "chat_key": "k", "chat_model": "m"}
    base.update(over)
    return base


def test_agent_state保留流式完成标记():
    assert "_streamed_result" in ag.AgentState.__annotations__


def test_主剧情世界书选择范围贯通到curator读写与trace(monkeypatch, tmp_path):
    repo_id = "work"
    book = {"entries": [
        {"content": "冷倾雪角色条目", "keys": ["冷倾雪"]},
        {"content": "未参与本轮的机制条目"},
    ]}
    worldbook_store.ensure_repo_snapshot(str(tmp_path), repo_id, [book])
    monkeypatch.setattr(ag, "_repo_worldbook", lambda ctx: book)
    monkeypatch.setattr(worldbook, "ensure_indexed", lambda *args, **kwargs: False)
    monkeypatch.setattr(worldbook, "_retrieve", lambda *args, **kwargs: [])
    events = []
    monkeypatch.setattr(
        ag.run_trace, "emit", lambda ctx, event, **data: events.append((event, data)),
    )
    ctx = _ctx(output_dir=str(tmp_path), repo_id=repo_id, embed_model="embed")

    injected = ag._resolve_worldbook(ctx, "冷倾雪醒来")
    context_fn = ag._curator_worldbook_context_fn(ctx, repo_id)
    writer = ag._curator_worldbook_fn(ctx, repo_id)

    assert "冷倾雪角色条目" in injected
    assert ctx["_selected_worldbook_indices"] == [0]
    curator_context = context_fn("任意正文")
    assert '"index":0' in curator_context
    assert '"index":1' not in curator_context
    assert writer([
        {"op": "worldbook_update", "index": 1, "text": "越权", "evidence": "正文"},
        {"op": "worldbook_add", "text": "新事实"},
    ]) == 1
    scope = next(data for event, data in events if event == "worldbook.update_scope")
    assert scope == {"allowed_indices": [0], "rejected_indices": [1]}


def test_首次世界书索引立即发送非阻塞状态事件(monkeypatch):
    book = {"entries": [{"content": "塞西莉亚角色条目", "keys": ["塞西莉亚"]}]}
    emitted = []
    traces = []
    monkeypatch.setattr(ag, "_repo_worldbook", lambda ctx: book)
    monkeypatch.setattr(
        worldbook,
        "schedule_index",
        lambda repo_id, entries, cfg, on_initial=None: bool(on_initial and on_initial(len(entries))),
    )
    monkeypatch.setattr(
        worldbook,
        "assemble_selection",
        lambda *args, **kwargs: worldbook.Selection("【世界设定】", [0]),
    )
    monkeypatch.setattr(
        ag.run_trace, "emit", lambda ctx, event, **data: traces.append((event, data)),
    )
    ctx = _ctx(
        repo_id="new-work", embed_model="embed", stream_sink=emitted.append,
    )

    assert ag._resolve_worldbook(ctx, "塞西莉亚") == "【世界设定】"
    assert emitted == [{
        "rag_status": {"state": "start", "kind": "worldbook", "count": 1},
    }]
    assert traces[-1] == (
        "worldbook.index", {"status": "started", "initial": True, "count": 1},
    )


def _decision(route: str, confidence: str = "high", alternatives=None) -> str:
    return json.dumps({
        "route": route,
        "confidence": confidence,
        "alternatives": alternatives or [],
    })


def _dispatch(text, *, images=None, ctx=None) -> dict:
    state = {"user_text": text, "images": images or [], "_ctx": ctx or _ctx()}
    return ag.supervisor_node(state)


def _route(text, *, images=None, ctx=None) -> str:
    return _dispatch(text, images=images, ctx=ctx)["route"]


def test_每个普通轮次都由主管模型判断并收到上下文与附件状态():
    captured = {}

    def decide(*args, **kwargs):
        captured["system"] = args[3]
        captured["user"] = args[4]
        return _decision("answer")

    assert _route(
        "继续处理刚才的内容",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=decide,
            history=[{"role": "assistant", "content": "刚才确定了界面设计。"}],
        ),
    ) == "answer"
    assert "【本轮可用路由】" in captured["system"]
    assert "附件数量：1" in captured["user"]
    assert "刚才确定了界面设计" in captured["user"]


def test_主管追踪包含模型消息与最终路由(monkeypatch):
    events = []
    monkeypatch.setattr(ag.run_trace, "emit",
                        lambda ctx, event, **data: events.append((event, data)))

    assert _route("推进剧情", ctx=_ctx(
        chat_fn=lambda *args, **kwargs: json.dumps({
            "route": "answer", "confidence": "high", "alternatives": [], "scene": "dialogue",
        }),
    )) == "answer"

    request = next(data for event, data in events if event == "model.request")
    decision = next(data for event, data in events if event == "agent.completed")
    assert request["agent"] == "supervisor"
    assert request["messages"][-1]["role"] == "user"
    assert "推进剧情" in request["messages"][-1]["content"]
    assert decision["route"] == "answer" and decision["scene"] == "dialogue"


def test_审查已有提示词的问题由主管模型分派为对话():
    captured = {}

    def decide(*args, **kwargs):
        captured["system"] = args[3]
        return _decision("answer")

    result = _route(
        "生成效果不满意，你再看看之前的提示词有什么问题，为什么输入框和拖动手柄都被吞了",
        images=["result.png"],
        ctx=_ctx(chat_fn=decide),
    )

    assert result == "answer"
    assert "审查已有提示词" in captured["system"]
    assert "根据图片产出新的提示词" in captured["system"]


def test_相同模糊文本可由主管结合语境作出不同分派():
    text = "参考这张图继续处理"
    assert _route(
        text, images=["reference.png"],
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("answer")),
    ) == "answer"
    assert _route(
        text, images=["reference.png"],
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("img2img")),
    ) == "img2img"


@pytest.mark.parametrize(("route", "images"), [
    ("answer", []),
    ("generate", []),
    ("img2img", ["reference.png"]),
    ("analyze", ["reference.png"]),
    ("video", []),
    ("inspire", []),
])
def test_主管高置信分派不再被关键词规则覆盖(route, images):
    assert _route(
        "同一段自然语言可以有不同理解",
        images=images,
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision(route)),
    ) == route


def test_主管只看到本轮结构上可用的路由():
    captured = {}

    def decide(*args, **kwargs):
        captured["available"] = args[3].split("【本轮可用路由】", 1)[1]
        return _decision("answer")

    _route(
        "处理附件",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=decide,
            agent_cfg={"tools": {"image_to_image": False, "analyze_image": True}},
        ),
    )

    assert "- answer：" in captured["available"]
    assert "- analyze：" in captured["available"]
    assert "- generate：" not in captured["available"]
    assert "- img2img：" not in captured["available"]
    assert "- tool_agent：" not in captured["available"]


def test_主管低置信时选择卡严格使用模型候选():
    result = _dispatch(
        "参考这张图处理一下",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision(
                "answer", "low", ["img2img", "analyze", "video"],
            ),
            message_id="bot-1",
            user_message_id="user-1",
        ),
    )

    assert result["route"] == "clarify"
    assert result["route_choice"]["messageId"] == "bot-1"
    assert result["route_choice"]["userMessageId"] == "user-1"
    assert [item["route"] for item in result["route_choice"]["options"]] == [
        "answer", "img2img", "analyze",
    ]


def test_选择卡过滤关闭工具与非法候选():
    result = _dispatch(
        "处理附件",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision(
                "answer", "low", ["analyze", "unknown", "img2img"],
            ),
            agent_cfg={"tools": {"image_to_image": True, "analyze_image": False}},
        ),
    )

    assert [item["route"] for item in result["route_choice"]["options"]] == [
        "answer", "img2img",
    ]


def test_低置信候选过滤后不足两个则安全回退对话():
    result = _dispatch(
        "处理附件",
        images=["reference.png"],
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision(
                "answer", "low", ["generate", "unknown"],
            ),
        ),
    )

    assert result["route"] == "answer"
    assert "route_choice" not in result


def test_模型不能选择缺少必要附件的路由():
    assert _route(
        "处理一下",
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("img2img")),
    ) == "answer"
    assert _route(
        "整理提示词",
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("analyze")),
    ) == "answer"


def test_模型不能越过Agent工具开关():
    assert _route(
        "画只猫",
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision("generate"),
            agent_cfg={"tools": {"generate_image": False}},
        ),
    ) == "answer"


def test_无MCP时工具专家不可用():
    assert _route(
        "查资料",
        ctx=_ctx(chat_fn=lambda *args, **kwargs: _decision("tool_agent")),
    ) == "answer"


def test_有MCP时工具专家可用():
    assert _route(
        "查资料",
        ctx=_ctx(
            chat_fn=lambda *args, **kwargs: _decision("tool_agent"),
            has_mcp=True,
        ),
    ) == "tool_agent"


def test_用户选择可跳过主管模型但不能绕过能力条件():
    def boom(*args, **kwargs):
        raise AssertionError("显式选择不应再次调用主管模型")

    assert _route(
        "继续处理",
        images=["reference.png"],
        ctx=_ctx(forced_route="img2img", chat_fn=boom),
    ) == "img2img"
    assert _route(
        "继续处理",
        ctx=_ctx(forced_route="img2img", chat_fn=boom),
    ) == "answer"
    assert _route(
        "继续处理",
        ctx=_ctx(forced_route="unknown", chat_fn=boom),
    ) == "answer"


def test_角色卡纯文本直达roleplay且不调用主管模型():
    def boom(*args, **kwargs):
        raise AssertionError("角色卡纯文本不应重复调用主管模型")

    ctx = _ctx(
        character_dir="cards", card_name="Lyra", chat_fn=boom,
    )
    result = _dispatch("两人开始激烈争吵", ctx=ctx)

    assert result["route"] == "roleplay"
    assert ctx["scene"] == "conflict"


def test_主管模型不复用联网搜索代理():
    captured = {}

    def decide(*args, **kwargs):
        captured.update(kwargs)
        return _decision("answer")

    assert _route("普通问题", ctx=_ctx(chat_fn=decide, proxy="http://127.0.0.1:7897")) == "answer"
    assert "proxy" not in captured


def test_主管模型使用当前对话模型代理():
    captured = {}

    def decide(*args, **kwargs):
        captured.update(kwargs)
        return _decision("answer")

    assert _route("普通问题", ctx=_ctx(chat_fn=decide, chat_proxy="http://chat-proxy")) == "answer"
    assert captured["proxy"] == "http://chat-proxy"


def test_模型异常或畸形输出安全回退对话():
    def boom(*args, **kwargs):
        raise RuntimeError("上游失败")

    assert _route("模糊请求", ctx=_ctx(chat_fn=boom)) == "answer"
    assert _route("模糊请求", ctx=_ctx(
        chat_fn=lambda *args, **kwargs: "not-json",
    )) == "answer"


# ── 多轮上下文窗口与独立执行提示词 ──

def test_最近历史分别保留用户和AI各六条():
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(20)
    ]

    history = agent_context.recent_history("thread", history_override=source)

    assert len(history) == 12
    assert history[0]["content"] == "消息8"
    assert history[-1]["content"] == "消息19"
    assert sum(item["role"] == "user" for item in history) == 6
    assert sum(item["role"] == "assistant" for item in history) == 6


def test_上下文按角色分别截取而非简单取最后十二条():
    history = [
        *[{"role": "user", "content": f"用户{i}"} for i in range(10)],
        *[{"role": "assistant", "content": f"助手{i}"} for i in range(8)],
    ]
    selected = agent_context.recent_history("thread", history_override=history)

    assert [item["content"] for item in selected if item["role"] == "user"] == [
        "用户4", "用户5", "用户6", "用户7", "用户8", "用户9",
    ]
    assert [item["content"] for item in selected if item["role"] == "assistant"] == [
        "助手2", "助手3", "助手4", "助手5", "助手6", "助手7",
    ]


def test_最近历史受默认两万token预算限制():
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ]

    history = agent_context.recent_history("thread", history_override=source)

    assert len(history) == 12
    assert all(len(item["content"]) < 3000 for item in history)
    assert sum(agent_context.estimate_tokens(item["content"]) + 4 for item in history) <= 20_000


def test_最近历史接受自定义token预算():
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ]

    history = agent_context.recent_history("thread", max_tokens=9_000, history_override=source)

    assert sum(agent_context.estimate_tokens(item["content"]) + 4 for item in history) <= 9_000


def test_最近历史token上限为0则不裁剪():
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ]

    history = agent_context.recent_history("thread", max_tokens=0, history_override=source)

    # 0=无上限：per_role 各取 6 条共 12 条，且内容全量不截断。
    assert len(history) == 12
    assert all(len(item["content"]) == 3000 for item in history)


def test_最近历史接受自定义每角色条数():
    source = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(20)
    ]

    history = agent_context.recent_history("thread", per_role=3, history_override=source)

    assert len(history) == 6
    assert sum(item["role"] == "user" for item in history) == 3
    assert sum(item["role"] == "assistant" for item in history) == 3
    assert history[-1]["content"] == "消息19"


def test_agent上下文优先使用前端显式历史且不上传已删除消息(monkeypatch):
    from app.services import chat_memory

    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: [
        {"role": "assistant", "content": "已被前端删除的拒绝历史"},
    ])
    visible = [
        {"role": "user", "content": "保留的用户消息"},
        {"role": "assistant", "content": "保留的正常回复"},
    ]

    history = agent_context.recent_history("thread", history_override=visible)

    assert history == visible
    assert all("已被前端删除" not in item["content"] for item in history)


def test_agent上下文快照存在时不回退到旧checkpoint(monkeypatch):
    from app.services import chat_memory

    monkeypatch.setattr(agent_context.chat_snapshot, "load_prompt_history", lambda _thread: [])
    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: [
        {"role": "assistant", "content": "旧checkpoint拒绝历史"},
    ])

    assert agent_context.recent_history("thread") == []


def test_agent上下文仅在快照不存在时回退checkpoint(monkeypatch):
    from app.services import chat_memory

    fallback = [{"role": "assistant", "content": "checkpoint历史"}]
    monkeypatch.setattr(agent_context.chat_snapshot, "load_prompt_history", lambda _thread: None)
    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: fallback)

    assert agent_context.recent_history("thread") == fallback


def test_依赖上文的生图请求会整理成独立提示词():
    calls = []
    ctx = _ctx(
        history=[
            {"role": "user", "content": "角色是金发绿瞳的文学系大小姐。"},
            {"role": "assistant", "content": "已经确定服装使用鼠尾草绿长裙。"},
        ],
        chat_fn=lambda *args, **kwargs: calls.append((args, kwargs)) or "金发绿瞳文学系大小姐，鼠尾草绿长裙，生成全身图",
    )

    prompt = agent_context.standalone_execution_prompt(ctx, "按刚才的设定生成全身图，其他不变")

    assert prompt.startswith("金发绿瞳文学系大小姐")
    assert calls


def test_完整执行提示词不额外调用上下文整理():
    def boom(*args, **kwargs):
        raise AssertionError("完整提示词不应额外调用模型")

    text = "生成一张金发绿瞳成年女性的全身角色设定图，鼠尾草绿长裙，白色背景"
    assert agent_context.standalone_execution_prompt(_ctx(history=[], chat_fn=boom), text) == text


# ── 剧情扮演路由：关联角色卡的作品，通用对话并入 roleplay ──

def _card_ctx(**over) -> dict:
    return _ctx(character_dir="D:/cards", card_name="Lyra", **over)


def test_有卡作品的普通对话并入剧情扮演():
    # 主管判 answer，但作品关联了角色卡 → 强制并入 roleplay，保持人设
    assert _route("你好", ctx=_card_ctx(chat_fn=lambda *a, **k: _decision("answer"))) == "roleplay"


def test_无卡作品保持通用对话():
    assert _route("你好", ctx=_ctx(chat_fn=lambda *a, **k: _decision("answer"))) == "answer"


def test_有卡作品仍可分派生图():
    # 明确生图意图用零 LLM 强规则分派，不被 roleplay 吞掉
    assert _route("画一张她的立绘", ctx=_card_ctx(
        gen_base="b", gen_key="k", gen_model="m",
        chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用主管")))) == "generate"


def test_角色卡强执行规则不把出图问题误当生图命令():
    assert _route(
        "出图效果为什么不好",
        ctx=_card_ctx(gen_base="b", gen_key="k", gen_model="m",
                      chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用主管"))),
    ) == "roleplay"


def test_roleplay_不可用当无卡():
    assert not ag._route_available("roleplay", False, _ctx())
    assert ag._route_available("roleplay", False, _card_ctx())


def _decision_scene(route: str, scene: str) -> str:
    return json.dumps({"route": route, "confidence": "high", "alternatives": [], "scene": scene})


def test_场景分类写回ctx():
    # supervisor 那次调用产出的 scene 规整后写入 ctx，供 roleplay_node 选链/配图
    ctx = _card_ctx(chat_fn=lambda *a, **k: _decision_scene("roleplay", "情色"))
    state = {"user_text": "她凑近了", "images": ["reference.png"], "_ctx": ctx}
    ag.supervisor_node(state)
    assert ctx["scene"] == "nsfw"  # 中文别名归一


def test_场景缺失不写ctx():
    ctx = _card_ctx(chat_fn=lambda *a, **k: _decision("roleplay"))
    state = {"user_text": "你好", "images": ["reference.png"], "_ctx": ctx}
    ag.supervisor_node(state)
    assert "scene" not in ctx  # 无 scene 字段 → 不写，保持缺省


def test_roleplay状态块里的user宏未设人设时回退我(monkeypatch):
    # 状态块/快照常含字段名「对{{user}}态度」，它在旧 substitute 点之后才拼进 base。
    # 用户没填人设名时也不能让字面 {{user}} 漏进 system → 被模型照抄进正文。收口替换须兜到「我」。
    captured = {}

    # deps 非 None 才会走 st_block + state_instruction 拼接分支
    class _Deps:
        pass
    monkeypatch.setattr(ag, "_agency_prelude",
                        lambda ctx, text: (_Deps(), 1, 0.0, "\n\n【当前状态】\nLyra·对{{user}}态度: 好奇"))
    monkeypatch.setattr(ag, "_agency_propose", lambda ctx, deps, aff, wb, text="": ("", False))
    monkeypatch.setattr(ag, "_agency_writeback",
                        lambda ctx, deps, reply, turn, aff, lost: (reply, [], {}))
    monkeypatch.setattr(ag, "_resolve_preset",
                        lambda ctx, wb, **k: ([], None, False, [], []))
    monkeypatch.setattr(ag, "_resolve_worldbook", lambda ctx, q: "")
    # 记忆检索走假件（本测只验宏替换，不碰 RAG）
    monkeypatch.setattr(ra, "recall_chronicle", lambda *a, **k: "")

    def fake_chat_messages(base, key, model, messages, *, temperature, **kw):
        captured["system"] = messages[0]["content"]
        captured["kwargs"] = kw
        return "好的"

    monkeypatch.setattr(ag._llm, "chat_messages", fake_chat_messages)
    ctx = _card_ctx(user_name="")  # 关键：没设人设名
    ag.roleplay_node({"user_text": "你好", "images": [], "_ctx": ctx})
    assert "{{user}}" not in captured["system"]      # 无字面残留
    assert "对我态度" in captured["system"]           # 回退「我」
    assert "proxy" not in captured["kwargs"]          # 联网代理不注入聊天模型


def test_roleplay把机械召回候选与预设上下文合并后只调一次主模型(monkeypatch, tmp_path):
    calls = []
    events = []
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应独立调用 Recall LLM")),
        rng=random.Random(0), state_base=str(tmp_path), curator_gate=1.0,
        index_fn=lambda text, title: None,
    )
    monkeypatch.setattr(ag, "_agency_prelude", lambda ctx, text: (deps, 1, 10.0, ""))
    monkeypatch.setattr(
        ag, "_agency_propose",
        lambda ctx, current, affinity, wb, text="": (calls.append("world") or ("", False)),
    )
    monkeypatch.setattr(ag, "_resolve_worldbook", lambda ctx, query: "世界书候选")
    monkeypatch.setattr(
        ag, "_resolve_preset",
        lambda ctx, wb, **kw: ([{"role": "system", "content": "GrayWill预设"}], None, False, [], []),
    )
    monkeypatch.setattr(ag, "_rag_recall_text", lambda *a, **k: calls.append("rag") or "作品记忆候选")
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: events.append((event, data)),
    )
    monkeypatch.setattr(
        ra, "recall_chronicle",
        lambda *a, **k: calls.append("recall_candidates") or "往事纪要候选\n作品记忆候选",
    )

    def roleplay_once(*args, **kwargs):
        messages = args[3]
        system = messages[0]["content"]
        assert "GrayWill预设" in system
        assert "往事纪要候选" in system
        assert "作品记忆候选" in system
        assert "<illustration>" in system
        assert "composition" in system and "weight" in system
        assert "<表格更新>" not in system
        assert messages[-1] == {"role": "user", "content": "继续剧情"}
        calls.append("roleplay")
        return "剧情正文"

    monkeypatch.setattr(
        ag._llm, "chat_messages",
        roleplay_once,
    )
    monkeypatch.setattr(
        ag, "_table_maintenance",
        lambda ctx, repo_id, reply, turn: calls.append("table"), raising=False,
    )
    monkeypatch.setattr(ra, "maybe_illustrate", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: calls.append("chronicle"))
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: calls.append("curator") or 0)

    out = ag.roleplay_node({
        "user_text": "继续剧情", "images": [],
        "_ctx": _card_ctx(output_dir=str(tmp_path), repo_id="work", comfy_illustrate=True),
    })

    assert out["result_text"] == "剧情正文"
    assert calls == ["world", "rag", "recall_candidates", "roleplay", "table", "chronicle", "curator"]
    assert not any(event == "model.request" and data.get("agent") == "recall" for event, data in events)


def test_世界agent收到本轮输入与召回后的npc条目(monkeypatch, tmp_path):
    captured = {}
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )

    def consult(current, **kwargs):
        captured.update(kwargs)
        return [agency.Verdict(
            "塞西莉亚", agency.OUTCOME_ACCEPT, agency.DEGREE_PARTIAL, 20, 60, "普通成功",
            intent="留下幽影徽章", goal="维持对主角的长期掌控",
        )]

    monkeypatch.setattr(ra, "consult_world", consult)
    ctx = _ctx(
        chat_base="base", chat_key="key", chat_model="model",
        persona="合集空壳卡", history=[{"role": "assistant", "content": "[在场] 塞西莉亚"}],
    )

    directive, _ = ag._agency_propose(
        ctx, deps, None, "【角色卡·塞西莉亚】掌控欲极强", "委婉拒绝收养",
    )

    assert "【角色卡·塞西莉亚】掌控欲极强" in captured["core"]
    assert "委婉拒绝收养" in captured["scene"]
    assert captured["affinity"] is None
    assert "留下幽影徽章" in directive
    assert ctx["_agency_goal_deltas"] == [{
        "field": "叙事/塞西莉亚·当前目标", "op": "set",
        "value": "维持对主角的长期掌控",
        "evidence": "World Agent依据在场角色core与本轮场景推导",
    }]


def test_comfy高潮提取失败时把中文正文作为Profile场景源而非旧prompt(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    assert not hasattr(ag, "_extract_image_prompt")
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)

    clean, images, request = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="白给谷", scene="nsfw",
            comfy_illustrate=True, persona="银发、蓝眼", history=[], proxy="",
        ),
        deps,
        "白给谷站在窗边，回头看向镜头。",
        turn=3,
        affinity=10.0,
        lost=False,
    )

    assert clean == "白给谷站在窗边，回头看向镜头。"
    assert images == []
    assert request["prompt"] == ""
    assert request["scene_spec"] == {
        "narrative": "白给谷站在窗边，回头看向镜头。",
            "draft_prompt": "白给谷站在窗边，回头看向镜头。，银发、蓝眼",
            "appearance": "银发、蓝眼",
            "wardrobe": "", "locale": "", "actors": ["白给谷"], "rating": "nsfw",
            "aspect_ratio": "2:3",
        }
    assert trace[-1][0] == "illustration.request"
    assert trace[-1][1]["status"] == "emitted"


def test_comfy首个用户回合即使已有开场白也兜底生成插画(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")

    clean, images, request = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="塞西莉亚",
            scene="conflict", comfy_illustrate=True,
            persona="乌黑长发，猩红眼眸，成熟丰腴的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        "塞西莉亚站在孤儿院门前，猩红眼眸安静地注视着他。",
        turn=2,
        affinity=10.0,
        lost=False,
        user_text="委婉拒绝收养。",
    )

    assert clean.startswith("塞西莉亚")
    assert images == []
    assert request["actors"] == ["塞西莉亚"]
    assert request["scene_spec"]["rating"] == "sfw"
    assert request["anchor"] == "塞西莉亚站在孤儿院门前，猩红眼眸安静地注视着他。"
    assert trace[-1] == (
        "illustration.request",
        {
            "status": "emitted", "reason": "first_story_reply",
            "scene": "conflict", "inferred_scene": "conflict",
            "actor_count": 1, "prompt_chars": len(request["prompt"]),
        },
    )


def test_首轮隐藏思考中的成人词不会污染收养开局插画(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<think>检查做爱、性交、色情等词是否需要破甲。</think>\n"
        "<content>塞西莉亚接受了拒绝，俯身将幽影徽章留在长凳上。</content>"
    )

    clean, images, request = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="塞西莉亚",
            scene="conflict", comfy_illustrate=True,
            persona="乌黑长发，猩红眼眸，成熟的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        reply,
        turn=2,
        affinity=10.0,
        lost=False,
        user_text="委婉拒绝收养，在孤儿院安定前不会离开。",
    )

    assert images == []
    assert request["scene_spec"]["rating"] == "sfw"
    assert request["scene_spec"]["narrative"] == "塞西莉亚接受了拒绝，俯身将幽影徽章留在长凳上。"
    assert "<think>" not in request["scene_spec"]["draft_prompt"]
    assert all(term not in request["scene_spec"]["draft_prompt"] for term in (
        "adult characters", "explicit", "intimate scene", "做爱", "性交", "色情",
    ))
    assert trace[-1][1]["inferred_scene"] == "conflict"


def test_首轮只有未闭合隐藏思考时不提交插画(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")

    _, images, request = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="塞西莉亚",
            scene="conflict", comfy_illustrate=True,
            persona="乌黑长发，猩红眼眸，成熟的幽影帝主",
            history=[{"role": "assistant", "content": "开场白"}], proxy="",
        ),
        deps,
        "<think>规划时列举裸露和亲吻，但在输出正文前截断",
        turn=2,
        affinity=10.0,
        lost=False,
        user_text="委婉拒绝收养。",
    )

    assert images == []
    assert request == {}


def test_supervisor误判且主模型漏计划时明确成人剧情仍触发生图(monkeypatch, tmp_path):
    from app.services import character_state

    trace = []
    monkeypatch.setattr(
        ag.run_trace, "emit",
        lambda ctx, event, **data: trace.append((event, data)),
    )
    deps = ra.AgencyDeps(
        chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path),
    )
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")

    clean, images, request = ag._agency_writeback(
        _ctx(
            repo_id="work", thread_id="work", card_name="冷倾雪", scene="dialogue",
            comfy_illustrate=True, persona="black hair, red eyes", history=[], proxy="",
        ),
        deps,
        "她的喘息骤然变得急促，身体在高潮中剧烈颤抖。\n\n许久后，她终于平静下来。",
        turn=3,
        affinity=10.0,
        lost=False,
        user_text="第二天,描写冷倾雪的饥渴难耐与完全征服收为己用,肉戏尽可能的丰富",
    )

    assert clean.endswith("她终于平静下来。")
    assert images == []
    assert request["actors"] == ["冷倾雪"]
    quality, content = request["prompt"].splitlines()
    assert quality == image_prompt_extract.COMFY_QUALITY_TAGS
    assert content.isascii() and "orgasm" in content
    assert request["anchor"] == "她的喘息骤然变得急促，身体在高潮中剧烈颤抖。"
    assert trace[-1][0] == "illustration.request"
    assert trace[-1][1]["status"] == "emitted"
    assert trace[-1][1]["reason"] == "local_scene_fallback"


def test_comfy采用主生成高潮计划并保留指定锚点(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    monkeypatch.setattr(ra, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(ra, "maybe_curate", lambda *a, **k: 0)
    reply = (
        "她跃上高台，披风在雷光中扬起。\n\n人群终于安静下来。"
        '<illustration>{"anchor":"披风在雷光中扬起。",'
        '"camera":"35mm low angle","composition":"triangular composition",'
        '"aspect_ratio":"3:2",'
        '"subjects":[{"name":"白给谷","description":"silver-haired swordswoman","weight":1.4}],'
        '"prompt":"lightning, ruined hall","motion":2}</illustration>'
    )

    clean, images, request = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白给谷", scene="dialogue", comfy_illustrate=True),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert clean == "她跃上高台，披风在雷光中扬起。\n\n人群终于安静下来。"
    assert images == []
    assert request["anchor"] == "披风在雷光中扬起。"
    assert request["actors"] == ["白给谷"] and request["motion"] == 2
    assert request["scene_spec"]["aspect_ratio"] == "3:2"
    quality, content = request["prompt"].splitlines()
    assert quality == image_prompt_extract.COMFY_QUALITY_TAGS
    assert content.startswith("35mm low angle, triangular composition")
    assert "(silver-haired swordswoman:1.4)" in request["prompt"]


def test_comfy优先采用主模型同轮生成的选中模式提示词(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "她站在窗边。"
        '<illustration>{"anchor":"她站在窗边。","camera":"low angle",'
        '"visual_thesis":"窗上倒影与本人形成对望",'
        '"hierarchy":"眼睛与倒影为第一视觉中心，房间逐渐概括",'
        '"palette_material":"冷蓝玻璃与暖金肤色",'
        '"lighting_logic":"窗外月光穿过玻璃照亮眼睛并把倒影压入冷色阴影",'
        '"composition":"centered","subjects":[{"name":"白给谷","description":"1girl"}],'
        '"prompt":"rim light","profile_prompt":"主模型直接生成的完整画面描述。","motion":0}</illustration>'
    )

    _, _, request = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白给谷", scene="dialogue", comfy_illustrate=True,
             prompt_profile="krea2"),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert request["prompt"] == "主模型直接生成的完整画面描述。"
    assert request["scene_spec"]["profile"] == "krea2"
    assert request["scene_spec"]["profile_prompt"] == request["prompt"]
    assert request["scene_spec"]["art_direction"] == {
        "visual_thesis": "窗上倒影与本人形成对望",
        "hierarchy": "眼睛与倒影为第一视觉中心，房间逐渐概括",
        "palette_material": "冷蓝玻璃与暖金肤色",
        "lighting_logic": "窗外月光穿过玻璃照亮眼睛并把倒影压入冷色阴影",
    }


def test_RunContext含NPC目标增量时仍剥离控制块并发出插画请求(monkeypatch, tmp_path):
    from app.services import character_state

    captured = {}
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    def writeback(*_args, **kwargs):
        captured["raw"] = kwargs["raw_deltas"]
        return 10.0, 10.0

    monkeypatch.setattr(ra, "writeback", writeback)
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    ctx = RunContext(
        thread_id="work", message="委婉拒绝收养", output_dir=str(tmp_path), repo_id="work",
        card_name="神权大陆", comfy_illustrate=True, prompt_profile="krea2",
    )
    ctx["scene"] = "conflict"
    ctx["_agency_goal_deltas"] = [{
        "field": "叙事/塞西莉亚·当前目标", "op": "set", "value": "长期观察主角",
        "evidence": "World Agent依据本轮场景推导",
    }]
    reply = (
        "院长看向石凳上的黑色匣子。"
        '<illustration>{"anchor":"院长看向石凳上的黑色匣子。","camera":"low angle",'
        '"composition":"rule of thirds","subjects":[{"name":"匣子","description":"black box"}],'
        '"prompt":"black box, stone bench","profile_prompt":"完整画面提示词。","motion":0}</illustration>'
    )

    clean, images, request = ag._agency_writeback(
        ctx, deps, reply, turn=2, affinity=0.0, lost=False,
    )

    assert clean == "院长看向石凳上的黑色匣子。"
    assert images == []
    assert request["prompt"] == "完整画面提示词。"
    assert request["anchor"] == "院长看向石凳上的黑色匣子。"
    assert captured["raw"] == [{
        "field": "叙事/塞西莉亚·当前目标", "op": "set", "value": "长期观察主角",
        "evidence": "World Agent依据本轮场景推导",
    }]
    assert "profile_prompt" not in clean


def test_插画后处理异常时不把内部控制块和提示词回退到正文(monkeypatch, tmp_path):
    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("writeback failed")))
    reply = (
        "可见剧情正文。"
        '<状态更新>[]</状态更新>'
        '<表格更新>[]</表格更新>'
        '<illustration>{"anchor":"可见剧情正文。","camera":"low angle",'
        '"composition":"centered","subjects":[{"name":"角色"}],'
        '"prompt":"secret image prompt","motion":0}</illustration>'
    )

    clean, images, request = ag._agency_writeback(
        _ctx(repo_id="work", card_name="角色", comfy_illustrate=True),
        deps, reply, turn=2, affinity=0.0, lost=False,
    )

    assert clean == "可见剧情正文。"
    assert images == [] and request == {}
    assert "secret image prompt" not in clean
    assert "状态更新" not in clean and "表格更新" not in clean


def test_插画JSON被模型截断时清除提示词并把降级图片锚定高潮段(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (0.0, 0.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    reply = (
        "<think>规划俯身、凝视、光影和高潮构图。</think>\n"
        "<content>日常铺垫。\n\n"
        "她俯身凝视着他，长发在午后的光影里垂落。\n\n"
        "她弯下腰，将乌木匣子搁在长凳上。\n\n"
        "院长从门后走出来询问情况。</content>\n"
        '<illustration>{"anchor":"院长从门后走出来询问情况。","camera":"中近景",'
        '"visual_thesis":"不应显示给用户的提示词","aspect_'
    )
    ctx = _ctx(repo_id="work", card_name="神权大陆", comfy_illustrate=True)
    ctx["history"] = []

    clean, images, request = ag._agency_writeback(
        ctx, deps, reply, turn=1, affinity=0.0, lost=False,
    )

    assert images == []
    assert "<illustration>" not in clean
    assert "visual_thesis" not in clean and "不应显示给用户的提示词" not in clean
    assert request["anchor"] == "她弯下腰，将乌木匣子搁在长凳上。"
    offset = ag._illustration_anchor_offset(clean, request)
    assert offset is not None
    assert clean[:offset].endswith(request["anchor"])
    assert "院长从门后走出来" in clean[offset:]


def test_krea2高潮按nsfw语言边界校验(monkeypatch, tmp_path):
    from app.services import character_state

    deps = ra.AgencyDeps(chat_fn=lambda *a, **k: "[]", rng=random.Random(0), state_base=str(tmp_path))
    monkeypatch.setattr(ra, "extract_status_snapshot", lambda reply: {})
    monkeypatch.setattr(ra, "parse_state_block", lambda reply: (reply, []))
    monkeypatch.setattr(ra, "writeback", lambda *a, **k: (10.0, 10.0))
    monkeypatch.setattr(character_state, "load_state", lambda *a, **k: {})
    monkeypatch.setattr(ra, "_narr", lambda *a, **k: "")
    valid = (
        "中景低机位拍摄。侧向暖光勾勒轮廓。人物保留稳定外貌。"
        "She wears the clothing established by the current scene. "
        "She leans against the rail while facing the viewer. "
        "背景为当前地点。主体位于三分线。"
    )
    reply = (
        "高潮正文。"
        '<illustration>{"anchor":"高潮正文。","camera":"low angle",'
        '"composition":"rule of thirds","subjects":[{"name":"白给谷","description":"1girl"}],'
        f'"prompt":"rim light","profile_prompt":"{valid}","motion":0}}</illustration>'
    )

    _, _, request = ag._agency_writeback(
        _ctx(repo_id="work", card_name="白给谷", scene="climax", comfy_illustrate=True,
             prompt_profile="krea2"),
        deps, reply, turn=2, affinity=10.0, lost=False,
    )

    assert request["prompt"] == valid
    assert request["scene_spec"]["rating"] == "nsfw"


def test_回合号使用完整快照而不是裁剪历史(monkeypatch):
    from app.services import chat_snapshot

    full = [
        {"role": "assistant", "content": f"回复{i}"}
        for i in range(12)
    ]
    monkeypatch.setattr(chat_snapshot, "load_prompt_history", lambda _thread_id: full)

    assert ag._next_story_turn({
        "repo_id": "work",
        "history": [{"role": "assistant", "content": "裁剪后仅剩一轮"}],
    }) == 13


def test_自动插画从已配置角色名机械识别在场角色():
    prompt, motion, actors = ag._build_image_prompt(
        _ctx(
            card_name="Lyra", illustration_actor_names=["Lyra", "Nia", "Mira"],
        ),
        paragraph="Lyra 与 Nia 在长廊奔跑。", appearance="", wardrobe="", locale="",
    )

    assert prompt and motion == 2
    assert actors == ["Lyra", "Nia"]


def test_插画槽事件位于锚点且前后文本不丢失():
    text = "高潮段落。\n\n后续状态。"
    events = ag._ordered_illustration_events(text, [{
        "id": "slot-1", "prompt": "p", "motion": 0, "actors": [],
        "anchor_offset": len("高潮段落。"),
    }])

    assert events == [
        {"delta": "高潮段落。"},
        {"illustrate_request": {"prompt": "p", "motion": 0, "actors": []}, "id": "slot-1"},
        {"delta": "\n\n后续状态。"},
    ]
    assert "".join(event.get("delta", "") for event in events) == text


def test_输出正则改写兜底锚点后仍按最终正文创建高潮槽():
    final_reply = "她全身______，在高潮中剧烈颤抖。\n\n许久后，她终于平静下来。"
    request = {
        "anchor": "她全身痉挛，在高潮中剧烈颤抖。",
        "allow_anchor_fallback": True,
    }

    offset = ag._illustration_anchor_offset(final_reply, request)

    assert offset == len("她全身______，在高潮中剧烈颤抖。")


def test_流式插画只发槽位和最终正文偏移():
    events = ag._streamed_illustration_events([{
        "id": "slot-1", "prompt": "p", "motion": 2, "actors": ["Lyra"],
        "anchor_offset": 5,
    }])

    assert events == [{
        "illustrate_request": {
            "prompt": "p", "motion": 2, "actors": ["Lyra"], "offset": 5,
        },
        "id": "slot-1",
    }]


def test_正文最终化后立即发插画请求且不等待记忆维护():
    emitted = []
    out = {
        "result_text": "最终正文",
        "illustrate_recs": [{
            "id": "slot-1", "prompt": "完整提示词", "motion": 1,
            "actors": ["Lyra"], "anchor_offset": 2,
        }],
    }
    assert ag._emit_roleplay_ready(_ctx(stream_sink=emitted.append), out) is True
    assert emitted == [
        {"replace": "最终正文"},
        {"illustrate_request": {
            "prompt": "完整提示词", "motion": 1, "actors": ["Lyra"], "offset": 2,
        }, "id": "slot-1"},
    ]


def test_独立表格维护只写数据库且不把维护响应放进对话(monkeypatch, tmp_path):
    from app.services import table_store

    repo_id = "work"
    table_store.save(str(tmp_path), repo_id, table_store.default_tables())
    captured = {}
    traces = []

    def maintain(base, key, model, system, user, **kwargs):
        captured.update(system=system, user=user)
        return json.dumps([{
            "op": "insert", "table": table_store.GLOBAL_TABLE,
            "values": {"时间": "第五日", "地点": "边地孤儿院", "世界状态": "平静", "世界规则": "未变"},
        }], ensure_ascii=False)

    monkeypatch.setattr(ag._llm, "chat", maintain)
    monkeypatch.setattr(
        ag.run_trace, "emit", lambda ctx, event, **data: traces.append((event, data)),
    )
    ctx = _ctx(
        output_dir=str(tmp_path), repo_id=repo_id, message="留在孤儿院",
    )

    ag._table_maintenance(ctx, repo_id, "剧情正文。" * 80, turn=1)

    global_table = next(
        table for table in table_store.load(str(tmp_path), repo_id)
        if table["name"] == table_store.GLOBAL_TABLE
    )
    assert global_table["rows"][0][:2] == ["第五日", "边地孤儿院"]
    assert "<表格更新>" not in captured["system"] + captured["user"]
    assert captured["user"].endswith("剧情正文。" * 80)
    assert any(event == "agent.completed" and data.get("agent") == "table_maintenance"
               for event, data in traces)


def test_roleplay记忆维护阻塞时正文与插画已同时启动(monkeypatch):
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    emitted = []

    class _Deps:
        renderer = None

    monkeypatch.setattr(ag, "_agency_prelude", lambda ctx, text: (_Deps(), 1, 0.0, ""))
    monkeypatch.setattr(ag, "_agency_propose", lambda *args, **kwargs: ("", False))
    monkeypatch.setattr(ag, "_resolve_worldbook", lambda *args: "")
    monkeypatch.setattr(ag, "_resolve_preset", lambda *args, **kwargs: ([], None, False, [], []))
    monkeypatch.setattr(ra, "recall_chronicle", lambda *args, **kwargs: "")
    monkeypatch.setattr(ag, "_rag_recall_text", lambda *args: "")
    monkeypatch.setattr(ag._llm, "chat_messages", lambda *args, **kwargs: "最终正文")
    monkeypatch.setattr(
        ag,
        "_agency_writeback",
        lambda *args: ("最终正文", [], {
            "prompt": "完整提示词", "motion": 1, "actors": ["Lyra"], "anchor": "",
        }),
    )

    def blocked_maintenance(*args):
        maintenance_started.set()
        release_maintenance.wait(2)

    monkeypatch.setattr(ag, "_agency_maintenance", blocked_maintenance)
    ctx = _card_ctx(
        repo_id="work", thread_id="work", comfy_illustrate=True,
        stream_sink=emitted.append,
    )
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(ag.roleplay_node({
            "user_text": "继续剧情", "images": [], "_ctx": ctx,
        })),
    )
    worker.start()
    try:
        assert maintenance_started.wait(1)
        assert [event.keys() & {"replace", "illustrate_request"} for event in emitted] == [
            {"replace"}, {"illustrate_request"},
        ]
        assert worker.is_alive()
    finally:
        release_maintenance.set()
        worker.join(2)

    assert not worker.is_alive()
    assert result["_eager_result"] is True
