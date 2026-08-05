"""模型代理必须按用途透传，联网搜索代理不得污染聊天模型。"""
from app.routers import ai_chat, ai_common, ai_text
from app.services import inspiration, llm


def test_build_chat_model透传聊天代理(monkeypatch):
    captured = {}
    marker = object()

    def fake_build(*args, **kwargs):
        captured.update(kwargs)
        return marker

    monkeypatch.setattr(llm, "build_model", fake_build)

    assert ai_common.build_chat_model("b", "k", "m", proxy="chat-proxy") is marker
    assert captured["proxy"] == "chat-proxy"


def test文本路由透传聊天代理(monkeypatch):
    captured = {}

    def fake_chat(*args, **kwargs):
        captured.update(kwargs)
        return "prompt"

    monkeypatch.setattr(ai_text, "chat", fake_chat)

    result = ai_text.gen_prompt(ai_text.PromptRequest(
        scene="scene", base_url="b", api_key="k", model="m", proxy="chat-proxy",
    ))

    assert result == {"prompt": "prompt"}
    assert captured["proxy"] == "chat-proxy"


def test提示词Profile路由透传聊天代理(monkeypatch):
    captured = {}

    def fake_chat(*args, **kwargs):
        captured.update(kwargs)
        return "1girl, solo, red dress, low angle"

    monkeypatch.setattr(ai_text, "chat", fake_chat)
    result = ai_text.gen_profile_prompt(ai_text.ProfilePromptRequest(
        profile="anima_tags", scene={"rating": "sfw"},
        base_url="b", api_key="k", model="m", proxy="chat-proxy",
    ))

    assert result["profile"] == "anima_tags"
    assert captured["proxy"] == "chat-proxy"


def test流式聊天路由透传聊天代理(monkeypatch):
    captured = {}

    def fake_build(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ai_chat, "build_chat_model", fake_build)

    ai_chat.chat_stream(ai_chat.ChatRequest(
        message="hello", use_rag=False,
        base_url="b", api_key="k", model="m", proxy="chat-proxy",
    ))

    assert captured["proxy"] == "chat-proxy"


def test灵感搜索与聊天模型使用独立代理(monkeypatch):
    captured = {}

    def fake_search(query, *, max_results, proxy):
        captured["search_proxy"] = proxy
        return [{"title": "title", "snippet": "snippet", "url": "https://example.test"}]

    def fake_chat(*args, **kwargs):
        captured["chat_proxy"] = kwargs.get("proxy")
        return "tag"

    monkeypatch.setattr(inspiration.ws, "web_search", fake_search)
    monkeypatch.setattr(inspiration._llm, "chat", fake_chat)

    inspiration.search_and_refine(
        "query", "b", "k", "m", proxy="search-proxy", chat_proxy="chat-proxy",
    )

    assert captured == {"search_proxy": "search-proxy", "chat_proxy": "chat-proxy"}
