"""chat_memory（LangGraph + SqliteSaver 对话记忆属主）：多轮历史 / 打断续写 / 摘要与替换。

隔离要点：`_saver` 与 `_blank_graph` 两个单例都绑模块级 `CHECKPOINT_DB`
（真实 backend/data/checkpoints.db）。每个用例把该绑定指向 tmp 并把单例清 None——
这是本模块此前覆盖率只有 24% 的直接原因：没有用例敢碰真实 checkpoint 库。
"""

import types

import pytest
from langchain_core.messages import AIMessage

from app.services import chat_memory


@pytest.fixture(autouse=True)
def _isolate_checkpoint_db(tmp_path, monkeypatch):
    """checkpoint 库落 tmp + 重置两个进程级单例（否则单例跨用例指向真实库）。"""
    monkeypatch.setattr(chat_memory, "CHECKPOINT_DB", tmp_path / "checkpoints.db")
    monkeypatch.setattr(chat_memory, "_saver", None)
    monkeypatch.setattr(chat_memory, "_blank_graph", None)
    yield
    # 本用例建的 sqlite 连接是单例、进程生命周期内不关；测试里主动关掉，消 ResourceWarning
    saver = chat_memory._saver
    if saver is not None:
        try:
            saver.conn.close()
        except Exception:
            pass


class _FakeLLM:
    """summarize_history/compact 的假模型：记录入参，可注入返回与异常。"""

    def __init__(self, content: str = "摘要正文", error: Exception | None = None):
        self.seen: list = []
        self.content = content
        self.error = error

    def invoke(self, msgs):
        self.seen.append(msgs)
        if self.error is not None:
            raise self.error
        return AIMessage(content=self.content)


def test_追加与历史往返_含多模态图片拆块():
    chat_memory.append_message("t1", "assistant", "你好")
    chat_memory.append_message("t1", "user", "看图", ["http://a/1.png", "http://a/2.png"])

    hist = chat_memory.get_history("t1")

    assert [h["role"] for h in hist] == ["assistant", "user"]
    assert hist[0] == {"role": "assistant", "content": "你好", "images": []}
    assert hist[1]["content"] == "看图"
    assert hist[1]["images"] == ["http://a/1.png", "http://a/2.png"]
    # 空线程 → 空历史（不炸）
    assert chat_memory.get_history("never-used") == []


def test_get_history_容忍奇异多模态形态():
    """content 列表里混原始字符串/图片串/非 dict 数字：文本拼接、图片取 url，不炸。"""
    graph = chat_memory._get_blank_graph()
    config = {"configurable": {"thread_id": "t-weird"}}
    graph.update_state(config, {"messages": [AIMessage(content=[
        "前缀文本",
        {"type": "text", "text": "加一段"},
        {"type": "image_url", "image_url": {"url": "http://x/1.png"}},
        {"type": "image_url", "image_url": "http://x/2.png"},
        "42",
    ])]})

    hist = chat_memory.get_history("t-weird")

    assert len(hist) == 1
    assert hist[0]["content"] == "前缀文本加一段42"
    assert hist[0]["images"] == ["http://x/1.png", "http://x/2.png"]


def test_append_turn_提交整轮与打断后缀():
    chat_memory.append_turn("t2", "画一张图", None, "完成了")
    hist = chat_memory.get_history("t2")
    assert [(h["role"], h["content"]) for h in hist] == [("user", "画一张图"), ("assistant", "完成了")]

    # interrupted → 拼统一「已打断」标记（跨轮续写识别信号）
    chat_memory.append_turn("t3", "问", [], "答一半", interrupted=True)
    hist3 = chat_memory.get_history("t3")
    assert hist3[-1]["content"] == "答一半" + chat_memory.INTERRUPT_MARKER

    # 空助手文本不落（strip 后为空）
    chat_memory.append_turn("t4", "", None, "   ")
    assert chat_memory.get_history("t4") == []

    # 无 user 文本但有图 → 占位「（见图）」，图片随 user 消息带历史
    chat_memory.append_turn("t5", "", ["http://a/1.png"], "看图作答")
    hist5 = chat_memory.get_history("t5")
    assert hist5[0]["content"] == "（见图）"
    assert hist5[0]["images"] == ["http://a/1.png"]


def test_mark_interrupted_用户消息去重与半成品补写():
    chat_memory.append_message("t6", "user", "同一句")
    chat_memory.mark_interrupted("t6", "同一句", None, "半成品")

    hist = chat_memory.get_history("t6")
    # user 已在图启动时写入 → 不重复补；半成品 AI 文本以「打断」标注补一条
    assert [h["role"] for h in hist] == ["user", "assistant"]
    assert "半成品" in hist[-1]["content"] and "打断" in hist[-1]["content"]

    # user 不一致 → 补 user；空半成品不补 assistant
    chat_memory.mark_interrupted("t6", "另一句", None, "  ")
    hist = chat_memory.get_history("t6")
    assert [h["role"] for h in hist] == ["user", "assistant", "user"]
    assert hist[-1]["content"] == "另一句"


def test_clear_history_清空对话线():
    chat_memory.append_message("t7", "user", "x")
    assert chat_memory.get_history("t7")
    chat_memory.clear_history("t7")
    assert chat_memory.get_history("t7") == []


def test_summarize_history_空输入不调模型():
    fake = _FakeLLM()
    result = chat_memory.summarize_history([], fake, [])
    assert result == {"ok": False, "summary": "", "image_count": 0}
    assert fake.seen == []


def test_summarize_history_拼接历史与生成记录():
    hist = [
        {"role": "user", "content": "画场景1", "images": []},
        {"role": "assistant", "content": "好了", "images": ["http://a/1.png", "http://a/2.png"]},
        {"role": "assistant", "content": "  ", "images": []},
    ]
    gens = [{"prompt": "场景1提示词"}, {"prompt": "场景1提示词"}, {"prompt": "场景2提示词"}]
    fake = _FakeLLM("要点：两场景")

    result = chat_memory.summarize_history(hist, fake, gens)

    assert result["ok"] is True and result["image_count"] == 3
    user_msg = fake.seen[0][1].content
    assert "用户：画场景1" in user_msg
    assert "[2张图片]" in user_msg                      # 图片只记占位省 token
    assert "累计生成图片 3 张" in user_msg
    assert "场景1提示词" in user_msg and "场景2提示词" in user_msg
    assert user_msg.count("场景1提示词") == 1            # 去重
    assert "助手：好了[2张图片]" in user_msg             # 图片只记占位省 token（拼占位不留空格）
    assert user_msg.count("助手：") == 1                 # 空文本条目不入历史文本


def test_summarize_history_空摘要与异常都算失败():
    hist = [{"role": "user", "content": "q", "images": []}]

    blank = chat_memory.summarize_history(hist, _FakeLLM("   "), [])
    assert blank["ok"] is False and blank["summary"] == ""

    boom = chat_memory.summarize_history(
        hist, _FakeLLM(error=RuntimeError("上游超时")), [{"prompt": "p"}])
    assert boom["ok"] is False and boom["error"] == "上游超时"


def test_compact_成功才替换_失败保留原历史():
    chat_memory.append_message("t9", "user", "第一句")

    ok = chat_memory.compact("t9", _FakeLLM("摘要正文"), [{"prompt": "p"}])
    assert ok["ok"] is True
    hist = chat_memory.get_history("t9")
    assert hist[0]["content"].startswith("【历史摘要】")

    fail = chat_memory.compact("t9", _FakeLLM(error=RuntimeError("x")), [])
    assert fail["ok"] is False
    assert chat_memory.get_history("t9") == hist        # 失败不动原历史


def test_replace_history_严格替换():
    chat_memory.append_message("t10", "user", "旧1")
    chat_memory.append_message("t10", "assistant", "旧2")

    chat_memory.replace_history("t10", [
        {"role": "user", "content": "新1"},
        {"role": "assistant", "content": "新2", "images": ["http://a/i.png"]},
    ])

    hist = chat_memory.get_history("t10")
    assert [(h["role"], h["content"]) for h in hist] == [("user", "新1"), ("assistant", "新2")]
    assert hist[1]["images"] == ["http://a/i.png"]


class _StubGraph:
    """替身编译图：记录传给 stream 的输入，按脚本吐 (chunk, meta)。"""

    def __init__(self, chunks):
        self._chunks = chunks
        self.seen: list = []

    def stream(self, payload, config, stream_mode=None):
        self.seen.append((payload, config, stream_mode))
        yield from self._chunks


def test_stream_chat_产出增量与空块跳过(monkeypatch):
    chunk = lambda text: types.SimpleNamespace(content=text)  # noqa: E731
    stub = _StubGraph([(chunk("你"), {}), (chunk(""), {}),
                       (chunk([{"type": "text", "text": "好"}]), {})])
    monkeypatch.setattr(chat_memory, "_build_graph", lambda llm, system: stub)

    deltas = list(chat_memory.stream_chat(object(), "t11", "系统词", "用户输入"))

    assert deltas == ["你", "好"]                       # 空块被跳过；分段 content 被展平
    payload, config, mode = stub.seen[0]
    assert mode == "messages"
    assert config == {"configurable": {"thread_id": "t11"}}
    assert payload["messages"][0].content == "用户输入"


def test_stream_chat_多模态构造(monkeypatch):
    stub = _StubGraph([])
    monkeypatch.setattr(chat_memory, "_build_graph", lambda llm, system: stub)

    deltas = list(chat_memory.stream_chat(
        object(), "t12", "系统词", "看图说", ["http://a/1.png"]))

    assert deltas == []
    (payload, _config, _mode), = stub.seen
    content = payload["messages"][0].content
    assert content[0] == {"type": "text", "text": "看图说"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "http://a/1.png"}}


def test_stream_chat_走真实编译图_非流式模型不产增量():
    """真图 + 假模型（无 token 级回调）：图能跑通、不炸；是否有增量取决于模型可流式性。"""
    deltas = list(chat_memory.stream_chat(_FakeLLM("完整回复"), "t13", "系统词", "输入"))
    assert all(isinstance(d, str) for d in deltas)
