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


# ===== 2026-10-18：多仓库合并分页（资产库聚合页不再全量 dump） =====

def _seed_with(store, ids, tss):
    for gid, ts in zip(ids, tss):
        store.add_documents([Document(
            page_content=f"p-{gid}",
            metadata={"kind": "generation", "image_url": f"image://{gid}",
                      "repo_id": gid.split("-")[0], "tags": "", "prompt": f"p-{gid}",
                      "description": "", "template_name": "", "model_name": "",
                      "lora_names": "", "media_type": "image",
                      "derived_from": "[]", "created_at": ts},
        )], [gid])


def _two_repo_stores(monkeypatch):
    stores: dict[str, MemoryStore] = {}
    monkeypatch.setattr(rag_store, "_store",
                        lambda coll, cfg: stores.setdefault(coll, MemoryStore()))
    _seed_with(stores.setdefault("repo_r1", MemoryStore()),
               ["r1-a", "r1-b", "r1-c"], [1200, 1100, 1000])
    _seed_with(stores.setdefault("repo_r2", MemoryStore()),
               ["r2-a", "r2-b", "r2-c"], [1350, 1250, 1150])
    return stores


def test_multi_repo_merged_keyset(monkeypatch):
    """跨库按 created_at 倒序归并；游标翻页不重不漏；limit=0 全量合并。"""
    _two_repo_stores(monkeypatch)

    page1 = rag_store.list_generations_multi(
        ["r1", "r2"], rag_backend.EmbedConfig(), limit=3)
    assert [i["id"] for i in page1] == ["r2-a", "r2-b", "r1-a"]

    page2 = rag_store.list_generations_multi(
        ["r1", "r2"], rag_backend.EmbedConfig(), limit=3,
        before_ts=page1[-1]["created_at"])
    assert [i["id"] for i in page2] == ["r2-c", "r1-b", "r1-c"]
    assert {i["id"] for i in page1}.isdisjoint({i["id"] for i in page2})

    full = rag_store.list_generations_multi(["r1", "r2"], rag_backend.EmbedConfig())
    assert [i["id"] for i in full] == ["r2-a", "r2-b", "r1-a", "r2-c", "r1-b", "r1-c"]


def test_route_multi_repo_paginates(monkeypatch):
    """路由：repo_ids 非空走合并分页，has_more 按多取一条判定（与单仓库同语义）。"""
    _two_repo_stores(monkeypatch)

    page1 = rag_router.list_generations(rag_router.GenerationsRequest(
        repo_ids=["r1", "r2"], limit=2))
    assert [i["id"] for i in page1["items"]] == ["r2-a", "r2-b"]
    assert page1["has_more"] is True

    tail = rag_router.list_generations(rag_router.GenerationsRequest(
        repo_ids=["r1", "r2"], limit=2,
        before_ts=page1["items"][-1]["created_at"]))
    assert [i["id"] for i in tail["items"]] == ["r1-a", "r2-c"]
    assert tail["has_more"] is True

    last = rag_router.list_generations(rag_router.GenerationsRequest(
        repo_ids=["r1", "r2"], limit=10))
    assert len(last["items"]) == 6
    assert last["has_more"] is False


def test_multi_repo_dedupes_same_id(monkeypatch):
    """防御性去重：同 id 不应跨库，但历史迁移可能留下 —— 合并时只保留一条。"""
    stores: dict[str, MemoryStore] = {}
    monkeypatch.setattr(rag_store, "_store",
                        lambda coll, cfg: stores.setdefault(coll, MemoryStore()))
    _seed_with(stores.setdefault("repo_r1", MemoryStore()), ["same"], [1200])
    _seed_with(stores.setdefault("repo_r2", MemoryStore()), ["same"], [1200])

    merged = rag_store.list_generations_multi(["r1", "r2"], rag_backend.EmbedConfig())
    assert [i["id"] for i in merged] == ["same"]
