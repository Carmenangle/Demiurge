"""跨模块契约测试：EmbedConfig 默认值 + parser/convert 共享穿透集的有意差异。

这些是「架构不变量」——PrimitiveNode 差异是有意的（见 workflow-convert 记忆），
测试把它钉死，防后续误合并。
"""
from app.services.rag_backend import EmbedConfig
from app.services import workflow_parser, workflow_convert
from app.services.agent_contracts import RunContext


def test_embed_config_默认模型():
    assert EmbedConfig().embed_model == "text-embedding-3-small"
    assert EmbedConfig().mode == "remote"
    c = EmbedConfig("u", "k", "m")
    assert (c.base_url, c.api_key, c.embed_model) == ("u", "k", "m")


def test_embed_config_保留本地模型目录():
    c = EmbedConfig("u", "k", "m", "D:/embedding", "D:/reranker")
    assert c.model_dir == "D:/embedding"
    assert c.reranker_dir == "D:/reranker"


def test_穿透集共享核心():
    assert workflow_parser.SKIP_TYPES == workflow_parser.PASSTHROUGH_TYPES
    assert workflow_parser.PASSTHROUGH_TYPES == {"Note", "MarkdownNote", "Reroute"}


def test_convert_额外并入PrimitiveNode是有意差异():
    # convert 运行期须穿透 PrimitiveNode（值已并入下游），parser 不 skip 它以暴露 value。
    # _NON_EXEC 是 convert 内的局部量，此处校验其组成规则的两个前提常量。
    assert "PrimitiveNode" not in workflow_parser.SKIP_TYPES
    assert "PrimitiveNode" not in workflow_parser.PASSTHROUGH_TYPES
    # convert 从 parser 导入的正是共享穿透集（同一来源，非各写一份）
    assert workflow_convert.PASSTHROUGH_TYPES is workflow_parser.PASSTHROUGH_TYPES


def test_convert_object_info复用统一ComfyUI适配器(monkeypatch):
    calls = []
    expected = {"SaveImage": {"input": {"required": {}}}}
    monkeypatch.setattr(
        workflow_convert.comfyui_client,
        "fetch_object_info",
        lambda url, timeout: calls.append((url, timeout)) or expected,
    )

    assert workflow_convert._object_info("http://127.0.0.1:8188") == expected
    assert calls == [("http://127.0.0.1:8188", 5)]


def test_convert_object_info失败时保留内置映射降级(monkeypatch):
    def fail(*_args, **_kwargs):
        raise workflow_convert.comfyui_client.ComfyError("未启动")

    monkeypatch.setattr(workflow_convert.comfyui_client, "fetch_object_info", fail)

    assert workflow_convert._object_info("http://127.0.0.1:8188") == {}


def test_agent上下文token上限契约():
    assert RunContext(thread_id="t", message="m").context_max_tokens == 20_000
    custom = RunContext(thread_id="t", message="m", context_max_tokens=48_000)
    assert custom["context_max_tokens"] == 48_000


def test_agent显式可见历史允许空列表覆盖checkpoint():
    context = RunContext(thread_id="t", message="m", history_override=[])
    assert context.history_override == []
    assert context["history_override"] == []


def test_agent上下文透传本轮原始输入():
    ctx = RunContext(thread_id="t", message="完整用户输入")
    assert ctx["message"] == "完整用户输入"
    assert ctx.get("message") == "完整用户输入"
    assert "message" in ctx


def test_agent生图质量契约():
    assert RunContext(thread_id="t", message="m").image_quality == "high"
    custom = RunContext(thread_id="t", message="m", image_quality="medium")
    assert custom["image_quality"] == "medium"


def test_agent按模型类型保留独立代理地址():
    ctx = RunContext(
        thread_id="t", message="m", proxy_url="search",
        chat_proxy_url="chat", gen_proxy_url="image",
        video_proxy_url="video", embed_proxy_url="embed",
    )
    assert ctx["proxy"] == "search"
    assert (ctx["chat_proxy"], ctx["gen_proxy"]) == ("chat", "image")
    assert (ctx["vid_proxy"], ctx["embed_proxy"]) == ("video", "embed")


def test_agent蒙版原图只合并到视觉输入一次():
    context = RunContext(
        thread_id="t", message="m", images=["reference.png"],
        image_mask={"image": "original.png", "mask": "mask.png"},
    )
    assert context.input_images() == ["original.png", "reference.png"]
    context.images.insert(0, "original.png")
    assert context.input_images() == ["original.png", "reference.png"]


def test_运行期瞬态键可写回并读出():
    # 修复扮演失败 'RunContext' object does not support item assignment：
    # graph 节点把 scene/_regex_scripts 等瞬态键写回 ctx，供后续节点读。
    ctx = RunContext(thread_id="t", message="m")
    assert ctx.get("scene") is None          # 未写入 → 默认 None
    ctx["scene"] = "谷中冷倾雪"                # 曾抛 item assignment 错
    ctx["_regex_scripts"] = [1, 2, 3]
    assert ctx["scene"] == "谷中冷倾雪"
    assert ctx.get("_regex_scripts") == [1, 2, 3]
    assert "scene" in ctx
    assert "context_max_tokens" in ctx        # __contains__ 也覆盖固定字段


def test_运行期瞬态键支持弹出且缺失时返回默认值():
    ctx = RunContext(thread_id="t", message="m")
    ctx["_agency_goal_deltas"] = [{"field": "叙事/塞西莉亚·当前目标"}]

    assert ctx.pop("_agency_goal_deltas") == [{"field": "叙事/塞西莉亚·当前目标"}]
    assert ctx.pop("_agency_goal_deltas", []) == []
    assert "_agency_goal_deltas" not in ctx


def test_瞬态键不影响相等比较():
    # extras compare=False：写瞬态键不改变 RunContext 相等性（幂等/去重语义不破）
    a = RunContext(thread_id="t", message="m")
    b = RunContext(thread_id="t", message="m")
    a["scene"] = "x"
    assert a == b
