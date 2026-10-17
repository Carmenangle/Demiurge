"""标签快照（版本化反推打标）：新增/列表/切换/删除 全生命周期。"""
from __future__ import annotations

import pytest

from app.services import rag_store


class FakeStore:
    """最小 Chroma 替身：只支撑快照链路用到的 get / update_document。"""

    def __init__(self) -> None:
        self.metadatas: dict[str, dict] = {}
        self.docs: dict[str, str] = {}

    def get(self, ids=None, where=None):  # noqa: ARG002
        i = ids[0]
        return {"ids": [i], "documents": [self.docs.get(i, "")],
                "metadatas": [self.metadatas.get(i)]}

    def update_document(self, doc_id: str, doc) -> None:
        self.docs[doc_id] = doc.page_content
        self.metadatas[doc_id] = doc.metadata


@pytest.fixture()
def rag(monkeypatch):
    store = FakeStore()
    store.docs["g1"] = "p"
    store.metadatas["g1"] = {"kind": "generation", "tags": "", "prompt": "p"}
    monkeypatch.setattr(rag_store, "_find_store",
                        lambda doc_id, repo_id, cfg: (store, store.metadatas[doc_id])
                        if doc_id in store.metadatas else (None, None))
    return store   # Document 构造会拷贝 metadata ⇒ 断言必须每次从 store 现读


def test_snapshot_lifecycle(rag):
    cfg = None
    md = lambda: rag.metadatas["g1"]  # noqa: E731
    s1 = rag_store.add_tag_snapshot("g1", "r", cfg, ["a", "b", "a"], model="wd-swinv2-v3")
    assert s1 is not None and s1["tags"] == ["a", "b"]        # 去重保序
    assert md()["tags"] == "a,b" and md()["active_tag_snapshot"] == s1["id"]

    s2 = rag_store.add_tag_snapshot("g1", "r", cfg, ["c", "d"], model="m2")
    assert md()["tags"] == "c,d" and md()["active_tag_snapshot"] == s2["id"]  # 新版即当前

    got = rag_store.list_tag_snapshots("g1", "r", cfg)
    assert [s["id"] for s in got["snapshots"]] == [s1["id"], s2["id"]]
    assert got["active"] == s2["id"]

    # 切回 v1：tags 同步成 v1，但两版都在
    assert rag_store.set_active_tag_snapshot("g1", "r", cfg, s1["id"]) is True
    assert md()["tags"] == "a,b"
    assert len(rag_store.list_tag_snapshots("g1", "r", cfg)["snapshots"]) == 2

    # 删当前版（v1）→ 回退到最后一版 v2
    assert rag_store.delete_tag_snapshot("g1", "r", cfg, s1["id"]) is True
    assert md()["active_tag_snapshot"] == s2["id"] and md()["tags"] == "c,d"
    # 删不存在 → False
    assert rag_store.delete_tag_snapshot("g1", "r", cfg, "nope") is False


def test_snapshot_unknown_doc(rag):
    assert rag_store.add_tag_snapshot("nope", "r", None, ["a"]) is None
    assert rag_store.list_tag_snapshots("nope", "r", None) == {"snapshots": [], "active": ""}
    assert rag_store.set_active_tag_snapshot("nope", "r", None, "x") is False
