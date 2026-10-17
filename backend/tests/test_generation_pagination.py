"""P0-1 资产列表真分页：service 层 keyset 语义 + 路由透传契约。

锁三件事：
1. limit/before_ts 分页：created_at 倒序、keyset 游标翻页不重不漏；
2. 默认路径（无 limit/before_ts）逐字返回全量原顺序——兼容既有调用方；
3. 路由层透传 limit/before_ts 并正确包装 has_more（多取一条判定）。
"""
from __future__ import annotations

from langchain_core.documents import Document

from app.routers import rag as rag_router
from app.services import rag_backend, rag_store


class MemoryStore:
    """与 test_asset_search.MemoryStore 同构；where 额外支持 $lt/$lte（keyset 游标）。"""

    def __init__(self):
        self.rows: dict[str, Document] = {}

    def add_documents(self, docs, ids):
        self.rows.update(dict(zip(ids, docs, strict=True)))

    def get(self, ids=None, where=None):
        rows = self.rows.items()
        if ids is not None:
            wanted = set(ids)
            rows = [(key, doc) for key, doc in rows if key in wanted]
        if where:
            rows = [(key, doc) for key, doc in rows if _match(doc, where)]
        rows = list(rows)
        return {
            "ids": [key for key, _doc in rows],
            "documents": [doc.page_content for _key, doc in rows],
            "metadatas": [doc.metadata for _key, doc in rows],
        }

    def update_document(self, doc_id, document):
        self.rows[doc_id] = document

    def similarity_search_by_vector(self, _vector, k, filter):
        return [doc for doc in self.rows.values()
                if doc.metadata.get("kind") == "generation"][:k]


def _match(doc: Document, where: dict) -> bool:
    for key, cond in where.items():
        value = doc.metadata.get(key)
        if isinstance(cond, dict):
            for op, target in cond.items():
                if op == "$lt" and not (value is not None and value < target):
                    return False
                if op == "$lte" and not (value is not None and value <= target):
                    return False
        elif value != cond:
            return False
    return True


def _seed(store: MemoryStore, n: int = 5) -> None:
    """入库 n 条 generation：gen-0 最新（created_at 最大），依次递减。"""
    for i in range(n):
        ts_val = 1000 + (n - i) * 100
        store.add_documents([Document(
            page_content=f"prompt-{i}",
            metadata={"kind": "generation", "image_url": f"image://{i}",
                      "repo_id": "repo", "tags": "", "prompt": f"prompt-{i}",
                      "description": "", "template_name": "", "model_name": "",
                      "lora_names": "", "media_type": "image",
                      "derived_from": "[]", "created_at": ts_val},
        )], [f"gen-{i}"])


def test_limit_returns_newest_first(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)
    _seed(store, 5)

    items = rag_store.list_generations("repo", rag_backend.EmbedConfig(), limit=2)
    assert [i["prompt"] for i in items] == ["prompt-0", "prompt-1"]


def test_keyset_cursor_paginates_without_overlap(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)
    _seed(store, 5)

    page1 = rag_store.list_generations("repo", rag_backend.EmbedConfig(), limit=2)
    cursor = page1[-1]["created_at"]
    page2 = rag_store.list_generations(
        "repo", rag_backend.EmbedConfig(), limit=2, before_ts=cursor)

    ids1 = {i["id"] for i in page1}
    ids2 = {i["id"] for i in page2}
    assert not (ids1 & ids2)
    assert len(ids1 | ids2) == 4
    assert page2[-1]["created_at"] < cursor


def test_default_path_unchanged(monkeypatch):
    """无分页参数时全量返回且保持原顺序（兼容既有调用方与前端本地排序）。"""
    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)
    _seed(store, 3)

    items = rag_store.list_generations("repo", rag_backend.EmbedConfig())
    assert [i["id"] for i in items] == ["gen-0", "gen-1", "gen-2"]


def test_route_paginates_and_reports_has_more(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)
    _seed(store, 5)

    page1 = rag_router.list_generations(rag_router.GenerationsRequest(
        repo_id="repo", limit=2))
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True

    tail = rag_router.list_generations(rag_router.GenerationsRequest(
        repo_id="repo", limit=2, before_ts=page1["items"][-1]["created_at"]))
    assert len(tail["items"]) == 2
    assert tail["has_more"] is True
    assert {i["id"] for i in page1["items"]}.isdisjoint(
        {i["id"] for i in tail["items"]})

    last = rag_router.list_generations(rag_router.GenerationsRequest(
        repo_id="repo", limit=10))
    assert len(last["items"]) == 5
    assert last["has_more"] is False
