from langchain_core.documents import Document

from app.services import rag_backend, rag_store


class MemoryStore:
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
            rows = [(key, doc) for key, doc in rows
                    if all(doc.metadata.get(k) == v for k, v in where.items())]
        rows = list(rows)
        return {
            "ids": [key for key, _doc in rows],
            "documents": [doc.page_content for _key, doc in rows],
            "metadatas": [doc.metadata for _key, doc in rows],
        }

    def update_document(self, doc_id, document):
        self.rows[doc_id] = document

    def similarity_search_by_vector(self, _vector, k, filter):
        assert filter == {"kind": "generation"}
        return [doc for doc in self.rows.values()
                if doc.metadata.get("kind") == "generation"][:k]


def test_vlm_description_is_indexed_without_replacing_display_prompt(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)

    rag_store.index_generation(
        "repo", rag_backend.EmbedConfig(), "anime woman", "red dress",
        "image://one", description="雨夜里撑伞的红裙人物",
    )

    item = rag_store.list_generations("repo", rag_backend.EmbedConfig())[0]
    indexed = next(iter(store.rows.values()))
    assert item["prompt"] == "anime woman"
    assert item["description"] == "雨夜里撑伞的红裙人物"
    assert "雨夜" in indexed.page_content


def test_generation_semantic_search_is_separate_and_description_update_reembeds(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_args: store)
    monkeypatch.setattr(rag_backend, "embed_query", lambda _cfg, query: [float(len(query))])
    cfg = rag_backend.EmbedConfig()
    rag_store.index_generation("repo", cfg, "anime woman", "red dress", "image://one")
    doc_id = next(iter(store.rows))

    assert rag_store.set_generation_description(doc_id, "repo", cfg, "雨夜街道，红裙人物")
    hits = rag_store.search_generations(["repo"], cfg, "雨夜红裙", 4)

    assert hits[0]["id"] == doc_id
    assert hits[0]["description"] == "雨夜街道，红裙人物"
    assert "雨夜街道" in store.rows[doc_id].page_content


def test_generation_reliable_index_retries_inside_rag_module(monkeypatch):
    calls: list[str] = []

    def flaky(*_args, **_kwargs):
        calls.append("index")
        if len(calls) < 3:
            raise RuntimeError("busy")

    monkeypatch.setattr(rag_store, "index_generation", flaky)
    sleeps: list[float] = []

    attempts = rag_store.index_generation_reliable(
        "repo", rag_backend.EmbedConfig(), "prompt", attempts=3, sleep_fn=sleeps.append,
    )

    assert attempts == 3
    assert sleeps == [0.8, 1.6]


def test_document_import_reports_partial_progress(monkeypatch):
    calls: list[str] = []

    def index(_repo, _cfg, text, _title):
        calls.append(text)
        if text == "bad":
            raise RuntimeError("embedding failed")
        return 2

    monkeypatch.setattr(rag_store, "index_document", index)

    try:
        rag_store.import_documents(
            "repo", rag_backend.EmbedConfig(), [("good", "A"), ("bad", "B")],
        )
    except rag_store.DocumentImportError as exc:
        assert exc.imported == 1
        assert "已入 1 篇" in str(exc)
    else:
        raise AssertionError("应报告部分导入失败")
