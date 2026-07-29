from app.services import tool_agent_adapter


def _ctx():
    return {
        "thread_id": "thread-1", "repo_id": "repo-1",
        "chat_base": "cb", "chat_key": "ck", "chat_model": "cm",
        "gen_base": "gb", "gen_key": "gk", "gen_model": "gm",
        "embed_base": "eb", "embed_key": "ek", "embed_model": "em",
        "size": "1024x1024", "output_dir": "out",
    }


def test_遗留工具流适配为专家节点结果(monkeypatch):
    def fake_stream(*args, **kwargs):
        assert args[0] == "thread-1"
        assert kwargs["memory_mode"] == "external_turn"
        yield {"delta": "完成"}
        yield {"image": "image.png", "image_id": "image-1"}
        yield {"inspiration": {"id": "card-1"}}
        yield {"interrupted": True}

    monkeypatch.setattr(tool_agent_adapter.image_agent, "stream_agent", fake_stream)

    result = tool_agent_adapter.run(_ctx(), "执行", [], ["工具专家"])

    assert result == {
        "result_text": "完成",
        "image_recs": [{"id": "image-1", "url": "image.png"}],
        "insp_cards": [{"id": "card-1"}],
        "trace": ["工具专家"],
        "_interrupted": True,
    }


def test_遗留工具异常转换为节点文本(monkeypatch):
    monkeypatch.setattr(
        tool_agent_adapter.image_agent,
        "stream_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = tool_agent_adapter.run(_ctx(), "执行", [], [])

    assert result["result_text"] == "工具专家执行失败：boom"
