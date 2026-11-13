"""私聊滚动上下文摘要（2026-11-12）：缓存命中 / 增量合并 / 编辑失效。"""
from __future__ import annotations

from app.services import agent_context, chat_context_summary


def _history(count: int, prefix: str = "m") -> list[dict]:
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"{prefix}{index:02d}" + ("y" * 200),
        }
        for index in range(count)
    ]


def _patch_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_context_summary, "_DIR", tmp_path / "summaries")
    return tmp_path / "summaries"


def test_chat_summary_cache_reuses_and_merges_only_new_old_segments(monkeypatch, tmp_path):
    _patch_dir(monkeypatch, tmp_path)
    full_calls: list[int] = []
    inc_calls: list[tuple[int, str]] = []

    def full(old):
        full_calls.append(len(old))
        return "S1"

    def inc(old, prior):
        inc_calls.append((len(old), prior))
        return "S2"

    history = _history(12)
    kwargs = dict(
        max_tokens=200, history_override=history, compact_trigger_pct=50,
        summarize_fn=full, summarize_with_prior_fn=inc,
        summary_scope="chat:v1",
    )

    first = agent_context.recent_history("t1", **kwargs)
    assert full_calls == [11]
    assert inc_calls == []
    assert first[0]["content"].startswith("【更早对话的长期摘要")
    assert "S1" in first[0]["content"]
    assert first[-1]["content"] == history[-1]["content"]
    cache = chat_context_summary.load("t1", "chat:v1")
    assert cache and cache["covered"] == 11

    # 同一段历史再跑：命中缓存，不再调摘要。
    second = agent_context.recent_history("t1", **kwargs)
    assert full_calls == [11]
    assert inc_calls == []
    assert second == first

    # 追加新消息：只把新增旧段合并进已有摘要。
    expanded = history + _history(3, prefix="n")
    third = agent_context.recent_history(
        "t1", **{**kwargs, "history_override": expanded},
    )
    assert full_calls == [11]
    assert len(inc_calls) == 1
    assert inc_calls[0][1] == "S1"
    assert inc_calls[0][0] >= 1
    assert "S2" in third[0]["content"]


def test_chat_summary_cache_invalidated_when_history_edited(monkeypatch, tmp_path):
    _patch_dir(monkeypatch, tmp_path)
    full_calls: list[int] = []

    def full(old):
        full_calls.append(len(old))
        return "S1"

    history = _history(12)
    kwargs = dict(
        max_tokens=200, history_override=history, compact_trigger_pct=50,
        summarize_fn=full, summarize_with_prior_fn=lambda _old, _prior: "S2",
        summary_scope="chat:v1",
    )
    assert agent_context.recent_history("t1", **kwargs)
    assert full_calls == [11]

    edited = [dict(item) for item in history]
    edited[0]["content"] = "被用户编辑过的开头" + "z" * 200
    assert agent_context.recent_history(
        "t1", **{**kwargs, "history_override": edited},
    )
    assert full_calls == [11, 11]


def test_chat_summarizer_uses_chat_specific_prompt(monkeypatch):
    captured: dict = {}

    def fake_chat(_base, _key, _model, system, user, **_kwargs):
        captured["system"] = system
        captured["user"] = user
        return "摘要"

    monkeypatch.setattr(chat_context_summary._llm, "chat", fake_chat)
    ctx = {
        "chat_base": "b", "chat_key": "k", "chat_model": "m",
        "context_max_tokens": 20_000,
    }
    full, inc = chat_context_summary.make_summarizers(ctx)
    assert full and inc

    assert full([{"role": "user", "content": "以后叫我主人"}]) == "摘要"
    assert "长期记忆归档" in captured["system"]
    assert "称呼" in captured["system"]
    assert "图片生成细节" in captured["system"]

    assert inc([{"role": "assistant", "content": "新发生的事"}], "旧摘要") == "摘要"
    assert "【已有摘要】" in captured["user"]
    assert "旧摘要" in captured["user"]


def test_clear_thread_removes_all_scope_caches(monkeypatch, tmp_path):
    _patch_dir(monkeypatch, tmp_path)
    for scope in ("chat:chat-summary-v1", "group_chat:chat-summary-v1"):
        chat_context_summary.save(
            "thread-1", scope, covered=2, tail_hash="h", summary="摘要",
        )
        assert chat_context_summary.load("thread-1", scope)

    chat_context_summary.clear_thread("thread-1")

    assert chat_context_summary.load("thread-1", "chat:chat-summary-v1") is None
    assert chat_context_summary.load("thread-1", "group_chat:chat-summary-v1") is None
