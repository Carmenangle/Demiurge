"""搭工作流编排骨架的契约测试：用假 chat_fn + 打桩的 object_info/检索，
脱离 live ComfyUI 与真实 LLM，验证 build_graph/build_module/build_direct 的
「校验失败→回喂重试→收敛/超时返回」编排契约。不测 AI 质量，只测编排控制流。"""
import json

import pytest

from app.services import workflow_builder as wb
from app.routers import ai_workflow_builder as build_routes


# —— 打桩：一套极简自洽的节点体系（validate_graph 完全基于传入 object_info）——
# Save 是 output_node、无必填口 → 单节点图即可通过校验 + 可达性。
_OBJECT_INFO = {
    "Save": {"input": {"required": {}}, "output": [], "output_node": True},
    "Bad": {"input": {"required": {}}, "output": [], "output_node": True},
}
_VALID_GRAPH = {"1": {"class_type": "Save", "inputs": {}}}


@pytest.fixture(autouse=True)
def _stub_deps(monkeypatch):
    # 检索恒有结果（避免走「知识库为空」的 ValueError 分支）
    monkeypatch.setattr(wb.node_index, "search",
                        lambda cfg, need, k=10: [{"content": "pack", "node_names": ["Save"]}])
    monkeypatch.setattr(
        wb.workflow_build_turn.node_candidates.comfyui_client,
        "fetch_object_info",
        lambda url: _OBJECT_INFO,
    )
    monkeypatch.setattr(wb.node_index, "suggest_alternatives", lambda *a, **k: {})


def _chat_returning(*replies):
    """造一个假 chat_fn：按调用次序依次吐 replies，用尽后重复最后一个。"""
    seq = list(replies)
    calls = {"n": 0}

    def chat_fn(base_url, api_key, model, system, convo, temperature=0.2, proxy=""):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    chat_fn.calls = calls
    return chat_fn


def _cfg():
    from app.services.rag_backend import EmbedConfig
    return EmbedConfig("http://embed", "k", "m")


# ============ build_graph ============

def test_build_graph_first_try_success(monkeypatch):
    monkeypatch.setattr(wb, "save_workflow", lambda g, n, d: "saved/path.json")
    chat_fn = _chat_returning(json.dumps(_VALID_GRAPH))
    out = wb.build_graph(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                         cfg=_cfg(), need="出图", comfy_url="c", workflow_dir="d",
                         name="n", max_retries=4, current_graph={}, save=True)
    assert out["ok"] is True
    assert out["graph"] == _VALID_GRAPH
    assert out["errors"] == []
    assert out["path"] == "saved/path.json"
    assert chat_fn.calls["n"] == 1                     # 一次到位不重试


def test_build_graph_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(wb, "save_workflow", lambda g, n, d: "p")
    # 第一次吐坏 JSON（触发解析失败回喂），第二次吐合法图
    chat_fn = _chat_returning("这不是JSON", json.dumps(_VALID_GRAPH))
    out = wb.build_graph(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                         cfg=_cfg(), need="出图", comfy_url="c", workflow_dir="d",
                         name="n", max_retries=4, current_graph={}, save=False)
    assert out["ok"] is True
    assert chat_fn.calls["n"] == 2                     # 回喂了一次


def test_build_graph_exhausts_retries_returns_errors():
    # 恒吐坏 JSON：耗尽 max_retries 后 ok=False，带 last_errors
    chat_fn = _chat_returning("坏的")
    out = wb.build_graph(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                         cfg=_cfg(), need="出图", comfy_url="c", workflow_dir="d",
                         name="n", max_retries=3, current_graph={}, save=False)
    assert out["ok"] is False
    assert out["errors"]                               # 有错误说明
    assert chat_fn.calls["n"] == 3                     # 用满重试额度


def test_build_graph_empty_need_raises():
    with pytest.raises(ValueError):
        wb.build_graph(_chat_returning("{}"), base_url="b", api_key="k", model="m",
                       proxy="", cfg=_cfg(), need="   ", comfy_url="c",
                       workflow_dir="d", name="n", max_retries=2,
                       current_graph={}, save=False)


def test_build_graph_empty_search_uses_object_info(monkeypatch):
    monkeypatch.setattr(wb.node_index, "search", lambda cfg, need, k=10: [])
    out = wb.build_graph(_chat_returning(json.dumps(_VALID_GRAPH)), base_url="b", api_key="k", model="m",
                         proxy="", cfg=_cfg(), need="出图", comfy_url="c",
                         workflow_dir="d", name="n", max_retries=2,
                         current_graph={}, save=False)
    assert out["ok"] is True


def test_candidate_resolution_checks_inventory_before_rag(monkeypatch):
    calls = []
    oi = {
        "UNETLoader": {"display_name": "UNET 加载器", "category": "loaders"},
        "VAELoader": {"display_name": "VAE 加载器", "category": "loaders"},
    }
    monkeypatch.setattr(wb.workflow_build_turn.node_candidates.comfyui_client, "fetch_object_info",
                        lambda _url: calls.append("inventory") or oi)
    monkeypatch.setattr(wb.node_index, "search",
                        lambda *_args, **_kwargs: calls.append("rag") or [])

    resolved = wb.workflow_build_turn.node_candidates.resolve(_cfg(), "Anima 模型", "c")

    assert calls == ["inventory", "rag"]
    assert "UNETLoader" in resolved.names
    assert "VAELoader" in resolved.names


def test_build_plan_only_calls_chat_model_once():
    calls = []

    def chat_fn(*args, **kwargs):
        calls.append((args, kwargs))
        return "方案"

    out = wb.build_plan(
        chat_fn, base_url="b", api_key="k", model="m", proxy="",
        cfg=_cfg(), need="Anima 出图", comfy_url="c", current_graph={},
    )

    assert out == {"plan": "方案"}
    assert len(calls) == 1
    assert "不能据此断言本机未安装" in calls[0][0][3]
    assert "1200 个中文字符以内" in calls[0][0][3]
    assert "【安装状态事实】ComfyUI object_info 共返回 2 个节点" in calls[0][0][4]


# ============ build_direct ============


def test_build_route_chat_adapter_disables_hidden_transport_retries(monkeypatch):
    seen = {}

    def fake_chat(*args, **kwargs):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(build_routes, "chat", fake_chat)
    assert build_routes._build_chat("b", "k", "m", "s", "u") == "ok"
    assert seen["retries"] == 1

def test_build_direct_single_call_success():
    chat_fn = _chat_returning(json.dumps(_VALID_GRAPH))
    out = wb.build_direct(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                          cfg=_cfg(), need="出图", comfy_url="c", current_graph={})
    assert out["ok"] is True
    assert out["graph"] == _VALID_GRAPH
    assert chat_fn.calls["n"] == 1                     # 直连只调一次


def test_build_direct_includes_build_conversation_history():
    seen = {}

    def chat_fn(base_url, api_key, model, system, convo, temperature=0.2, proxy=""):
        seen["convo"] = convo
        return json.dumps(_VALID_GRAPH)

    out = wb.build_direct(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                          cfg=_cfg(), need="继续搭建", comfy_url="c", current_graph={},
                          history=[
                              {"role": "user", "text": "之前方案用 WD14 反推"},
                              {"role": "assistant", "text": "我会使用 WD14"},
                              {"role": "user", "text": "改用本机 llama 反推"},
                          ])
    assert out["ok"] is True
    assert "改用本机 llama 反推" in seen["convo"]


def test_build_direct_at_most_two_calls():
    # 恒吐坏 JSON：直连最多 2 次（初次 + 1 次纠错回喂）后如实返回失败
    chat_fn = _chat_returning("坏")
    out = wb.build_direct(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                          cfg=_cfg(), need="出图", comfy_url="c", current_graph={})
    assert out["ok"] is False
    assert out["errors"]
    assert chat_fn.calls["n"] == 2                     # 顶多两次，不无限重试


# ============ build_module ============

def test_build_module_success_merges(monkeypatch):
    # 打桩合并：直接返回一个通过校验的整图
    monkeypatch.setattr(wb.workflow_merge, "merge_module",
                        lambda base, nodes, anchors: {"graph": _VALID_GRAPH})
    module_reply = json.dumps({"nodes": {"a": {"class_type": "Save", "inputs": {}}},
                               "anchors": []})
    chat_fn = _chat_returning(module_reply)
    out = wb.build_module(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                          cfg=_cfg(), need="加一路", comfy_url="c",
                          current_graph={"9": {"class_type": "Save", "inputs": {}}},
                          max_retries=2)
    assert out["ok"] is True
    assert out["graph"] == _VALID_GRAPH


def test_build_module_no_nodes_returns_error():
    # AI 没吐 nodes：耗尽重试后 ok=False
    chat_fn = _chat_returning(json.dumps({"nodes": {}, "anchors": []}))
    out = wb.build_module(chat_fn, base_url="b", api_key="k", model="m", proxy="",
                          cfg=_cfg(), need="加一路", comfy_url="c",
                          current_graph={}, max_retries=2)
    assert out["ok"] is False
    assert out["errors"]
