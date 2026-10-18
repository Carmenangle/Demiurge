"""标签类变更不触发重嵌入（2026-10-18 优化①）。

根因：`_content_for` 原把 tags 拼进语义嵌入文本 ⇒ 切快照/改标签每次都重嵌入整条文档（~1.6s），
但图片本身没变、向量不该重算。改法：语义嵌入只放 prompt + 描述；标签类操作只更新元数据。
"""
from app.services.rag_store import _content_for, _save_tags_meta


def test_content_for_excludes_tags():
    """标签不进语义嵌入 ⇒ 同图不同标签，嵌入内容完全相同（切标签无需重嵌入）。"""
    a = _content_for({"kind": "generation", "prompt": "P", "tags": "alpha,beta", "description": "D"})
    b = _content_for({"kind": "generation", "prompt": "P", "tags": "gamma,delta", "description": "D"})
    assert a == b
    assert "alpha" not in a and "gamma" not in b
    assert "P" in a and "D" in a


def test_content_for_description_still_counts():
    """描述是语义 ⇒ 换描述必须改变嵌入内容（否则语义搜索会漏）。"""
    a = _content_for({"kind": "generation", "prompt": "P", "description": "红裙少女"})
    b = _content_for({"kind": "generation", "prompt": "P", "description": "蓝裙少女"})
    assert a != b


def test_content_for_empty_falls_back():
    assert _content_for({"kind": "generation"}) == "生成图片"


def test_save_tags_meta_updates_metadata_only():
    """有 _collection 时只更新元数据、不调 update_document（不重嵌入）。"""
    calls = {"update": 0, "reembed": 0}

    class FakeColl:
        def update(self, **_kw):
            calls["update"] += 1

    class FakeStore:
        _collection = FakeColl()

        def update_document(self, *_a, **_k):
            calls["reembed"] += 1

    _save_tags_meta(FakeStore(), "d1", {"kind": "generation", "tags": "x,y"})
    assert calls == {"update": 1, "reembed": 0}


def test_save_tags_meta_falls_back_without_collection():
    """私有 API 不可用时回退 update_document（能跑，只是退化为重嵌入）。"""
    calls = {"reembed": 0}

    class FakeStore:
        @property
        def _collection(self):
            raise AttributeError("no _collection")

        def update_document(self, *_a, **_k):
            calls["reembed"] += 1

    _save_tags_meta(FakeStore(), "d1", {"kind": "generation"})
    assert calls["reembed"] == 1
