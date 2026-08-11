"""模型代理必须按用途透传，联网搜索代理不得污染聊天模型。"""
import json

from app.routers import ai_chat, ai_common, ai_text
from app.services import inspiration, llm, preset_store


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


def test提示词Profile独立调用携带当前防拦截预设(monkeypatch, tmp_path):
    preset = {
        "prompts": [{
            "identifier": "guard", "role": "system", "content": "防拦截规则生效",
        }],
        "prompt_order": [{"order": [{"identifier": "guard", "enabled": True}]}],
    }
    preset_store.save(str(tmp_path), "guard", preset)
    captured = {}

    def fake_chat(_base, _key, _model, system, _user, **_kwargs):
        captured["system"] = system
        return json.dumps({
            "visual_hook": "a mirror reflection frames the focused adult face",
            "primary_focus": "the reflected adult face",
            "supporting_elements": ["window frame"],
            "content": (
                "adult woman, black hair, red eyes, close-up, side lighting, shallow depth of field. "
                "Her face remains sharp while the room recedes into soft shadow."
            ),
        })

    monkeypatch.setattr(ai_text, "chat", fake_chat)
    result = ai_text.gen_profile_prompt(ai_text.ProfilePromptRequest(
        profile="anima_tags",
        scene={"rating": "sfw", "actors": ["冷倾雪"]},
        preset_dir=str(tmp_path), preset_name="guard", user_name="我",
        base_url="b", api_key="k", model="m",
    ))

    assert result["profile"] == "anima_tags"
    assert "防拦截规则生效" in captured["system"]
    assert "内部生图提示词任务" in captured["system"]


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
