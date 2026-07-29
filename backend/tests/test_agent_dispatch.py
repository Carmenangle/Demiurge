"""Supervisor 分派 Interface 测试：模型拥有语义判断，代码只校验能力条件。"""
import json

import pytest

from app.services import agent_context, agent_graph as ag


def _ctx(**over) -> dict:
    base = {"chat_base": "b", "chat_key": "k", "chat_model": "m"}
    base.update(over)
    return base


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


def test_模型异常或畸形输出安全回退对话():
    def boom(*args, **kwargs):
        raise RuntimeError("上游失败")

    assert _route("模糊请求", ctx=_ctx(chat_fn=boom)) == "answer"
    assert _route("模糊请求", ctx=_ctx(
        chat_fn=lambda *args, **kwargs: "not-json",
    )) == "answer"


# ── 多轮上下文窗口与独立执行提示词 ──

def test_最近历史分别保留用户和AI各六条(monkeypatch):
    from app.services import chat_memory

    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(20)
    ])

    history = agent_context.recent_history("thread")

    assert len(history) == 12
    assert history[0]["content"] == "消息8"
    assert history[-1]["content"] == "消息19"
    assert sum(item["role"] == "user" for item in history) == 6
    assert sum(item["role"] == "assistant" for item in history) == 6


def test_上下文按角色分别截取而非简单取最后十二条(monkeypatch):
    from app.services import chat_memory

    history = [
        *[{"role": "user", "content": f"用户{i}"} for i in range(10)],
        *[{"role": "assistant", "content": f"助手{i}"} for i in range(8)],
    ]
    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: history)

    selected = agent_context.recent_history("thread")

    assert [item["content"] for item in selected if item["role"] == "user"] == [
        "用户4", "用户5", "用户6", "用户7", "用户8", "用户9",
    ]
    assert [item["content"] for item in selected if item["role"] == "assistant"] == [
        "助手2", "助手3", "助手4", "助手5", "助手6", "助手7",
    ]


def test_最近历史受默认两万token预算限制(monkeypatch):
    from app.services import chat_memory

    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ])

    history = agent_context.recent_history("thread")

    assert len(history) == 12
    assert all(len(item["content"]) < 3000 for item in history)
    assert sum(agent_context.estimate_tokens(item["content"]) + 4 for item in history) <= 20_000


def test_最近历史接受自定义token预算(monkeypatch):
    from app.services import chat_memory

    monkeypatch.setattr(chat_memory, "get_history", lambda _thread: [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "设" * 3000}
        for i in range(12)
    ])

    history = agent_context.recent_history("thread", max_tokens=9_000)

    assert sum(agent_context.estimate_tokens(item["content"]) + 4 for item in history) <= 9_000


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
