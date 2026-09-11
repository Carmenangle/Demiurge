from app.services import node_store
from app.services.rag_backend import EmbedConfig
from langchain_core.documents import Document


class _FakeStore:
    def __init__(self, data=None):
        self.data = data or {"ids": [], "metadatas": []}
        self.updated = []
        self.added = []
        self.deleted = []

    def get(self, **kwargs):
        return self.data

    def update_document(self, document_id, document):
        self.updated.append((document_id, document))

    def add_documents(self, documents, ids):
        self.added.append((documents, ids))

    def delete(self, ids):
        self.deleted.extend(ids)


def test_node_chunks_merge_by_pack_without_losing_nodes():
    hits = node_store._merge_candidates([
        {"id": "core", "title": "nodes", "content": "UNETLoader", "node_names": ["UNETLoader"]},
        {"id": "plugin", "title": "other", "content": "OtherNode", "node_names": ["OtherNode"]},
        {"id": "core", "title": "nodes", "content": "VAELoader", "node_names": ["VAELoader"]},
    ])

    assert [hit["id"] for hit in hits] == ["core", "plugin"]
    assert hits[0]["node_names"] == ["UNETLoader", "VAELoader"]
    assert "UNETLoader" in hits[0]["content"]
    assert "VAELoader" in hits[0]["content"]


def test_rrf_merges_dense_and_sparse_chunks_from_same_pack():
    hits = node_store._rrf_fuse(
        [{"id": "core", "content": "VAEEncode", "node_names": ["VAEEncode"]}],
        [{"id": "core", "content": "LoadImage", "node_names": ["LoadImage"]}],
        4,
    )

    assert hits[0]["node_names"] == ["VAEEncode", "LoadImage"]


def test_update_pack_content_updates_full_pack_and_replaces_chunks(monkeypatch):
    metadata = {
        "title": "pack", "node_names": "NodeA,NodeB", "categories": "image",
        "python_module": "custom_nodes.pack", "content_source": "auto",
    }
    full_store = _FakeStore({"ids": ["pack"], "metadatas": [metadata]})
    chunk_store = _FakeStore({"ids": ["old-chunk"], "metadatas": [{}]})
    monkeypatch.setattr(
        node_store,
        "_store",
        lambda collection, _cfg: (
            full_store if collection == node_store.NODE_INDEX_COLLECTION else chunk_store
        ),
    )

    assert node_store.update_node_pack_content(EmbedConfig(), "pack", "人工用途说明") is True

    _, full_document = full_store.updated[0]
    assert full_document.page_content == "人工用途说明"
    assert full_document.metadata["content_source"] == "manual"
    assert chunk_store.deleted == ["old-chunk"]
    chunk_document = chunk_store.added[0][0][0]
    assert chunk_document.page_content == "人工用途说明"
    assert chunk_document.metadata["node_names"] == "NodeA,NodeB"
    assert chunk_document.metadata["content_source"] == "manual"


def test_search_node_packs_many_batches_remote_embedding(monkeypatch):
    embedded = []

    class FakeEmbeddings:
        def embed_documents(self, texts):
            embedded.append(list(texts))
            return [[float(index)] for index, _ in enumerate(texts)]

    class FakeVectorStore:
        def similarity_search_by_vector(self, vector, k):
            index = int(vector[0])
            return [Document(
                page_content=f"content-{index}",
                metadata={"pack_id": f"pack-{index}", "node_names": f"Node{index}"},
            )]

    monkeypatch.setattr(node_store, "chunk_collection_ready", lambda _cfg: False)
    monkeypatch.setattr(node_store, "_store", lambda _collection, _cfg: FakeVectorStore())
    monkeypatch.setattr(node_store.rag_backend, "embeddings", lambda _cfg: FakeEmbeddings())
    monkeypatch.setattr(node_store, "_bm25_search", lambda *_args, **_kwargs: [])

    groups = node_store.search_node_packs_many(
        EmbedConfig(), ["Anima loader", "WD14 tagger", "llama caption"], k=4,
        dense_indexes={0, 2},
    )

    assert embedded == [["Anima loader", "llama caption"]]
    assert [group[0]["id"] if group else None for group in groups] == [
        "pack-0", None, "pack-1",
    ]
