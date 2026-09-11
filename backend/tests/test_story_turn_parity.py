"""双端剧情回合号派生一致性（M1 审计 #1，2026-09-06）。

旧会话（无 turnNo 链）首次封口走派生兜底：前端 promptHistory 与后端 to_prompt_history
必须数出同一条数，否则封口区间错位（多封丢真记忆/少封留残留）。两端对同一夹具
（shared/fixtures/story-turn-parity.json：system 提示/媒体气泡/非剧情路由/空正文/parts 文本）
对拍派生回合号。前端对拍见 frontend/src/lib/storyTurn.test.ts。
"""
import json
from pathlib import Path

from app.services import agent_graph, chat_snapshot

FIXTURE = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "story-turn-parity.json"


def test_与前端promptHistory对拍_直喂历史派生一致():
    """直喂分支的真实输入是前端 promptHistory 产物（已过滤），用 to_prompt_history
    复现同一过滤后再派生。"""
    messages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    history = chat_snapshot.to_prompt_history(messages)
    assert sum(1 for h in history if h["role"] == "assistant") == 3
    assert agent_graph._next_story_turn({"history": history, "story_turn": 0}) == 4


def test_快照完整回路派生一致(tmp_path, monkeypatch):
    """同一夹具落成真实快照文件，走 load_prompt_history 完整回路。"""
    messages = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snap = tmp_path / "chat.json"
    snap.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(chat_snapshot, "_path", lambda thread_id: snap)

    assert chat_snapshot.load_prompt_history("r1") is not None
    assert agent_graph._next_story_turn({"repo_id": "r1", "story_turn": 0}) == 4


# ── M1 审计 #7：历史嵌入 LRU 缓存（同文本稳态零网络调用，换模型不串向量） ──


def test_历史嵌入缓存_同文本只嵌一次且换模型不串(monkeypatch):
    from app.services import agent_graph as ag

    calls: list[list[str]] = []

    class _FakeEmbeddings:
        def embed_documents(self, texts):
            calls.append(list(texts))
            return [[len(t), 1.0] for t in texts]

    from app.services import rag_backend
    monkeypatch.setattr(rag_backend, "embeddings", lambda cfg: _FakeEmbeddings())

    ctx = {"embed_base": "http://x", "embed_model": "m1"}
    embed = ag._history_embed_fn(ctx)
    first = embed(["甲文本", "乙文本"])
    second = embed(["甲文本"])  # 命中缓存
    assert len(calls) == 1 and calls[0] == ["甲文本", "乙文本"]
    assert second == [first[0]]

    other = ag._history_embed_fn({"embed_base": "http://x", "embed_model": "m2"})
    other(["甲文本"])  # 换模型 → 重新嵌入，不串向量
    assert len(calls) == 2 and calls[1] == ["甲文本"]
