"""跨模块契约测试：EmbedConfig 默认值 + parser/convert 共享穿透集的有意差异。

这些是「架构不变量」——PrimitiveNode 差异是有意的（见 workflow-convert 记忆），
测试把它钉死，防后续误合并。

末尾一组是「调用签名契约」：`llm.build_model` 参数改名后漏改调用方，会静默失效（见下）。
"""
import ast
import inspect
from pathlib import Path

from app.services.rag_backend import EmbedConfig
from app.services import workflow_parser, workflow_convert, llm as _llm
from app.services.agent_contracts import RunContext

_APP_DIR = Path(__file__).resolve().parents[1] / "app"


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


def test_agent工作区模式契约():
    assert RunContext(thread_id="t", message="m").workspace_mode == "story"
    context = RunContext(thread_id="t", message="m", workspace_mode="edit")
    assert context["workspace_mode"] == "edit"


def test_agent多角色卡与生图外貌来源契约():
    context = RunContext(
        thread_id="t", message="m", card_name="露娜",
        card_names=["露娜", "米拉"], opening_card_name="露娜",
        appearance_source="character_card",
    )
    assert context["card_names"] == ["露娜", "米拉"]
    assert context["opening_card_name"] == "露娜"
    assert context["appearance_source"] == "character_card"


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


# ---------------------------------------------------------------------------
# 调用签名契约：build_model 参数名漂移必须立刻红
# ---------------------------------------------------------------------------

def _build_model_call_sites() -> list[tuple[str, int, list[str]]]:
    """静态扫描 app/ 下所有 `build_model(...)` 调用，返回 (相对路径, 行号, 关键字参数名)。

    只认名字（或属性名）为 `build_model` 的调用；带 `**kwargs` 展开的调用无法静态判定，
    直接跳过（不当假阳性报警）。

    用默认 `utf-8` 读即可：全仓源文件禁止 UTF-8 BOM（`test_源文件不含UTF8BOM` 契约）。
    2026-09-11 之前 `novel_tools.py` 带 BOM，本函数被迫用 `utf-8-sig` 兜；BOM 清理后
    收窄回默认编码，BOM 回归由契约测试当场红。
    """
    sites: list[tuple[str, int, list[str]]] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "build_model":
                continue
            if any(kw.arg is None for kw in node.keywords):   # **kwargs 展开，跳过
                continue
            sites.append((
                str(path.relative_to(_APP_DIR)),
                node.lineno,
                [kw.arg for kw in node.keywords if kw.arg],
            ))
    return sites


def test_build_model调用点不传未声明参数():
    """全仓 `build_model` 调用点的关键字必须是现行签名的参数，否则报出「文件:行号 传了什么」。

    2026-09-10 实锤：`image_agent._build` 传 `retries=1`，签名早已改名 `sdk_retries`
    → `TypeError` 被 tool_agent 路由的宽 `except Exception` 吞成文本，路由静默不可用。
    此前 `image_agent._build` 零测试覆盖，所以没人发现。此处把这一类彻底堵死。
    """
    declared = set(inspect.signature(_llm.build_model).parameters)
    bad = [
        f"{rel}:{lineno} 传了未声明参数 {sorted(set(kwargs) - declared)}"
        for rel, lineno, kwargs in _build_model_call_sites()
        if set(kwargs) - declared
    ]
    assert bad == [], "build_model 调用点出现未声明的参数名：\n" + "\n".join(bad)


def test_build_model扫描器确实扫到调用点():
    """护栏自检：扫描器若因目录变动扫到 0 个调用点，上面的断言会恒真而失去意义。"""
    assert len(_build_model_call_sites()) >= 5


# ---------------------------------------------------------------------------
# 源码卫生契约：UTF-8 BOM 禁入 app/
# ---------------------------------------------------------------------------

def test_源文件不含UTF8BOM():
    """app/ 下任何 .py 不得以 UTF-8 BOM（EF BB BF）开头。

    2026-09-10 实锤：`novel_tools.py` 带 BOM，解释器认、但任何用默认 `utf-8`
    读源码做静态扫描的工具（如 AST 护栏）都会报 SyntaxError（U+FEFF），
    当时被迫全员 `utf-8-sig` 特判。09-11 已清理全仓唯一一处并立此契约——
    BOM 回归必须当场红，而不是下一个工具作者再踩一遍。
    """
    offenders = [
        str(p.relative_to(_APP_DIR))
        for p in sorted(_APP_DIR.rglob("*.py"))
        if p.read_bytes()[:3] == b"\xef\xbb\xbf"
    ]
    assert offenders == []

