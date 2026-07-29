import pytest

from app.services import workflow_build_turn as build_turn


def test_prepare_keeps_history_correction_and_current_graph(monkeypatch):
    seen = {}

    def resolve(cfg, query, comfy_url, *, k):
        seen.update(query=query, comfy_url=comfy_url, k=k)
        return "candidates"

    monkeypatch.setattr(build_turn.node_candidates, "resolve", resolve)
    graph = {"1": {"class_type": "SaveImage", "inputs": {}}}
    turn = build_turn.prepare(
        object(), need="继续修改", comfy_url="http://comfy", current_graph=graph,
        history=[
            {"role": "assistant", "text": "使用 WD14"},
            {"role": "user", "text": "改用本机 llama 反推"},
        ],
        k=12,
    )

    assert "改用本机 llama 反推" in turn.context_query
    assert "改用本机 llama 反推" in turn.history_text
    assert turn.current_graph == graph
    assert turn.current_graph is not graph
    assert turn.candidates == "candidates"
    assert seen == {"query": turn.context_query, "comfy_url": "http://comfy", "k": 12}


def test_prepare_applies_query_optimizer_before_candidate_resolution(monkeypatch):
    monkeypatch.setattr(
        build_turn.node_candidates,
        "resolve",
        lambda _cfg, query, _url, *, k: {"query": query, "k": k},
    )

    turn = build_turn.prepare(
        object(), need="Anima 出图", comfy_url="c", current_graph=None, history=None, k=8,
        query_optimizer=lambda query: query + " UNETLoader VAELoader",
    )

    assert turn.context_query.endswith("UNETLoader VAELoader")
    assert turn.candidates == {"query": turn.context_query, "k": 8}


def test_prepare_rejects_empty_need_before_external_lookup(monkeypatch):
    monkeypatch.setattr(
        build_turn.node_candidates,
        "resolve",
        lambda *_args, **_kwargs: pytest.fail("不应查询节点候选"),
    )

    with pytest.raises(ValueError, match="需求为空"):
        build_turn.prepare(
            object(), need="  ", comfy_url="c", current_graph=None, history=None, k=8,
        )
