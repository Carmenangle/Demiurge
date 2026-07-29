from app.services import rag_backend, rag_store
from app.services import rag_retrieval
from langchain_core.documents import Document


def test_ordinary_rag_rrf_rewards_dense_and_sparse_agreement():
    shared = {"id": "shared", "content": "target"}
    hits = rag_retrieval.rrf_fuse([
        ("dense:system", [{"id": "dense-only", "content": "x"}, shared]),
        ("bm25", [shared]),
    ], 3)

    assert hits[0]["id"] == "shared"
    assert hits[0]["channels"] == ["dense:system", "bm25"]


def test_ordinary_rag_sparse_retrieves_exact_term():
    hits = rag_retrieval.sparse_rank("WD14Tagger", [
        {"id": "noise", "content": "通用图像描述"},
        {"id": "exact", "content": "使用 WD14Tagger 反推标签"},
    ], 2)

    assert [hit["id"] for hit in hits] == ["exact"]


def test_ordinary_rag_skips_reranker_when_dense_and_sparse_agree(monkeypatch):
    class FakeStore:
        def get(self):
            return {
                "documents": ["WD14Tagger 用于反推标签"],
                "metadatas": [{"kind": "system", "title": "节点说明"}],
            }

        def similarity_search_by_vector(self, vector, k, filter):
            return [Document(
                page_content="WD14Tagger 用于反推标签",
                metadata={"kind": "system", "title": "节点说明"},
            )]

    monkeypatch.setattr(rag_backend, "embed_query", lambda cfg, query: [0.1, 0.2])
    monkeypatch.setattr(rag_store, "_store", lambda collection, cfg: FakeStore())
    monkeypatch.setattr(
        rag_store.reranker,
        "rerank",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应精排确定性结果")),
    )

    hits = rag_store.retrieve_with_trace("home", rag_backend.EmbedConfig(), "WD14Tagger", 4)

    assert hits[0]["channels"] == ["dense:system", "bm25"]


def test_retrieve_with_trace_embeds_query_once_and_uses_bm25_fallback(monkeypatch):
    calls = []

    class FakeEmbeddings:
        def embed_query(self, query):
            calls.append(query)
            return [0.1, 0.2]

    class FakeStore:
        def __init__(self, rows, dense):
            self.rows = rows
            self.dense = dense

        def get(self):
            return {
                "documents": [row.page_content for row in self.rows],
                "metadatas": [row.metadata for row in self.rows],
            }

        def similarity_search_by_vector(self, vector, k, filter):
            assert vector == [0.1, 0.2]
            assert filter == {"kind": {"$ne": "generation"}}
            return self.dense[:k]

    system_doc = Document(page_content="系统帮助", metadata={"kind": "system", "title": "帮助"})
    exact_doc = Document(page_content="WD14Tagger 用于反推标签", metadata={"kind": "document", "title": "节点说明"})
    generation = Document(page_content="WD14Tagger", metadata={"kind": "generation"})
    stores = {
        rag_store.SYSTEM_COLLECTION: FakeStore([system_doc], [system_doc]),
        rag_store._repo_collection("home"): FakeStore([exact_doc, generation], []),
    }
    monkeypatch.setattr(rag_backend, "embed_query", lambda cfg, query: FakeEmbeddings().embed_query(query))
    monkeypatch.setattr(rag_store, "_store", lambda collection, cfg: stores[collection])
    monkeypatch.setattr(rag_store.reranker, "rerank", lambda *args, **kwargs: [])

    hits = rag_store.retrieve_with_trace("home", rag_backend.EmbedConfig(), "WD14Tagger", 4)

    assert calls == ["WD14Tagger"]
    assert any(hit["content"] == exact_doc.page_content and "bm25" in hit["channels"] for hit in hits)
    assert all(hit["kind"] != "generation" for hit in hits)
