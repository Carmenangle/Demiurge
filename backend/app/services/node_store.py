"""节点索引存储：完整包管理、能力分块、Dense/BM25 单路 Hybrid 召回。"""
from __future__ import annotations

import logging
import re

from langchain_core.documents import Document

from app.services import rag_backend
from app.services.rag_backend import EmbedConfig


NODE_INDEX_COLLECTION = "node_index"
NODE_CHUNK_COLLECTION = "node_index_chunks_v1"

_BM25_CACHE: dict[tuple, dict] = {}
_NODE_CHUNK_READY_CACHE: dict[rag_backend.EmbeddingKey, bool] = {}


def _store(collection: str, cfg: EmbedConfig):
    return rag_backend.store(collection, cfg)


def _invalidate(cfg: EmbedConfig) -> None:
    _BM25_CACHE.clear()
    _NODE_CHUNK_READY_CACHE.pop(rag_backend.embedding_key(cfg), None)


def index_node_pack(cfg: EmbedConfig, pack_id: str, title: str, content: str,
                    node_names: list[str], categories: list[str],
                    python_module: str, *, content_source: str = "auto") -> None:
    """写入或覆盖管理页使用的完整插件包文档。"""
    store = _store(NODE_INDEX_COLLECTION, cfg)
    document = Document(page_content=content, metadata={
        "kind": "node_pack", "id": pack_id, "title": title,
        "node_names": ",".join(node_names),
        "categories": ",".join(dict.fromkeys(categories)),
        "python_module": python_module,
        "content_source": content_source,
    })
    _invalidate(cfg)
    try:
        if store.get(ids=[pack_id]).get("ids"):
            store.update_document(pack_id, document)
            return
    except Exception:
        pass
    store.add_documents([document], ids=[pack_id])


def delete_node_pack(cfg: EmbedConfig, pack_id: str) -> None:
    """删除已卸载插件的完整包和所有检索分块。"""
    try:
        _store(NODE_INDEX_COLLECTION, cfg).delete(ids=[pack_id])
    except Exception:
        pass
    try:
        chunk_store = _store(NODE_CHUNK_COLLECTION, cfg)
        chunk_ids = chunk_store.get(where={"pack_id": pack_id}).get("ids", []) or []
        if chunk_ids:
            chunk_store.delete(ids=chunk_ids)
    except Exception:
        pass
    _invalidate(cfg)


def index_node_chunks(cfg: EmbedConfig, pack_id: str, title: str,
                      chunks: list[dict], python_module: str, *,
                      content_source: str = "auto") -> None:
    """覆盖一个插件包的检索分块，完整包文档不受影响。"""
    store = _store(NODE_CHUNK_COLLECTION, cfg)
    try:
        old_ids = store.get(where={"pack_id": pack_id}).get("ids", []) or []
        if old_ids:
            store.delete(ids=old_ids)
    except Exception:
        pass
    documents: list[Document] = []
    ids: list[str] = []
    for index, chunk in enumerate(chunks):
        chunk_id = f"{pack_id}::chunk::{index:04d}"
        ids.append(chunk_id)
        documents.append(Document(page_content=chunk.get("content", ""), metadata={
            "kind": "node_chunk", "id": chunk_id, "pack_id": pack_id,
            "title": title,
            "node_names": ",".join(chunk.get("node_names", [])),
            "categories": ",".join(chunk.get("categories", [])),
            "python_module": python_module,
            "content_source": content_source,
        }))
    try:
        if documents:
            store.add_documents(documents, ids=ids)
    finally:
        # 删除旧分块后即使重建失败，也必须让检索重新检查集合完整性。
        _invalidate(cfg)


def node_chunks_ready(cfg: EmbedConfig, pack_id: str) -> bool:
    try:
        data = _store(NODE_CHUNK_COLLECTION, cfg).get(where={"pack_id": pack_id}, limit=1)
        return bool(data.get("ids"))
    except Exception:
        return False


def chunk_collection_ready(cfg: EmbedConfig) -> bool:
    """只有完整包与分块包集合严格一致时才允许切换到分块检索。"""
    cache_key = rag_backend.embedding_key(cfg)
    cached = _NODE_CHUNK_READY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    expected = {pack["id"] for pack in list_node_packs(cfg)}
    if not expected:
        _NODE_CHUNK_READY_CACHE[cache_key] = False
        return False
    try:
        data = _store(NODE_CHUNK_COLLECTION, cfg).get(include=["metadatas"])
        actual = {
            str(metadata.get("pack_id"))
            for metadata in (data.get("metadatas", []) or [])
            if isinstance(metadata, dict) and metadata.get("pack_id")
        }
    except Exception:
        actual = set()
    ready = expected == actual
    _NODE_CHUNK_READY_CACHE[cache_key] = ready
    return ready


def list_node_packs(cfg: EmbedConfig) -> list[dict]:
    try:
        data = _store(NODE_INDEX_COLLECTION, cfg).get()
    except Exception:
        return []
    ids = data.get("ids", []) or []
    metadatas = data.get("metadatas", []) or []
    return [
        {
            "id": document_id,
            "title": metadata.get("title", ""),
            "node_names": [
                name for name in (metadata.get("node_names", "") or "").split(",") if name
            ],
            "categories": [
                category for category in (metadata.get("categories", "") or "").split(",")
                if category
            ],
            "python_module": metadata.get("python_module", ""),
            "content_source": metadata.get("content_source", "auto"),
        }
        for document_id, metadata in zip(ids, metadatas)
    ]


def get_node_pack(cfg: EmbedConfig, pack_id: str) -> dict | None:
    try:
        data = _store(NODE_INDEX_COLLECTION, cfg).get(ids=[pack_id])
    except Exception:
        return None
    ids = data.get("ids", []) or []
    if not ids:
        return None
    metadata = (data.get("metadatas", []) or [{}])[0] or {}
    documents = data.get("documents", []) or [""]
    return {
        "id": ids[0], "title": metadata.get("title", ""),
        "content": documents[0] or "",
        "node_names": [
            name for name in (metadata.get("node_names", "") or "").split(",") if name
        ],
        "categories": [
            category for category in (metadata.get("categories", "") or "").split(",")
            if category
        ],
        "python_module": metadata.get("python_module", ""),
        "content_source": metadata.get("content_source", "auto"),
    }


def update_node_pack_content(cfg: EmbedConfig, pack_id: str, content: str) -> bool:
    """持久覆盖插件包正文，并让完整包与能力分块立即保持一致。"""
    store = _store(NODE_INDEX_COLLECTION, cfg)
    try:
        existing = store.get(ids=[pack_id])
    except Exception:
        return False
    if not existing.get("ids"):
        return False
    metadata = (existing.get("metadatas", []) or [{}])[0] or {}
    metadata["content_source"] = "manual"
    store.update_document(pack_id, Document(page_content=content, metadata=metadata))
    node_names = [
        name for name in (metadata.get("node_names", "") or "").split(",") if name
    ]
    categories = [
        category for category in (metadata.get("categories", "") or "").split(",")
        if category
    ]
    index_node_chunks(
        cfg,
        pack_id=pack_id,
        title=str(metadata.get("title", "")),
        chunks=[{
            "content": content,
            "node_names": node_names,
            "categories": categories,
        }],
        python_module=str(metadata.get("python_module", "")),
        content_source="manual",
    )
    return True


def _tokenize(text: str) -> list[str]:
    value = (text or "").lower()
    return re.findall(r"[a-z0-9]+", value) + re.findall(r"[一-鿿]", value)


def _get_bm25(cfg: EmbedConfig, collection: str):
    key = (collection, cfg)
    cached = _BM25_CACHE.get(key)
    if cached is not None:
        return cached["bm25"], cached["packs"]
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return None, []
    try:
        data = _store(collection, cfg).get()
    except Exception:
        return None, []
    packs = []
    corpus = []
    for document_id, metadata, content in zip(
        data.get("ids", []) or [], data.get("metadatas", []) or [],
        data.get("documents", []) or [],
    ):
        metadata = metadata or {}
        node_names = [
            name for name in (metadata.get("node_names", "") or "").split(",") if name
        ]
        pack = {
            "id": metadata.get("pack_id") or document_id,
            "title": metadata.get("title", ""), "content": content or "",
            "node_names": node_names, "python_module": metadata.get("python_module", ""),
        }
        packs.append(pack)
        corpus.append(_tokenize(
            f"{pack['title']} {' '.join(node_names) * 3} {pack['content']}"
        ))
    if not corpus:
        return None, []
    bm25 = BM25Okapi(corpus)
    _BM25_CACHE[key] = {"bm25": bm25, "packs": packs}
    return bm25, packs


def _bm25_search(cfg: EmbedConfig, query: str, k: int, collection: str) -> list[dict]:
    bm25, packs = _get_bm25(cfg, collection)
    tokens = _tokenize(query)
    if bm25 is None or not packs or not tokens:
        return []
    scores = bm25.get_scores(tokens)
    order = sorted(range(len(packs)), key=lambda index: scores[index], reverse=True)
    return [packs[index] for index in order[:k] if scores[index] > 0]


def _merge_candidates(items: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in items:
        pack_id = str(item.get("id") or item.get("python_module") or item.get("title") or "")
        if pack_id not in merged:
            merged[pack_id] = {
                **item, "id": pack_id,
                "node_names": list(item.get("node_names", []) or []),
            }
            continue
        target = merged[pack_id]
        known = set(target.get("node_names", []))
        for name in item.get("node_names", []) or []:
            if name not in known:
                target["node_names"].append(name)
                known.add(name)
        content = str(item.get("content", "") or "")
        if content and content not in str(target.get("content", "") or ""):
            target["content"] = f"{target.get('content', '')}\n\n{content}".strip()
    return list(merged.values())


def _rrf_fuse(dense: list[dict], sparse: list[dict], k: int, c: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for rank, item in enumerate(dense):
        pack_id = item.get("id") or item.get("python_module") or item.get("title")
        scores[pack_id] = scores.get(pack_id, 0.0) + 1.0 / (c + rank)
        by_id[pack_id] = item
    for rank, item in enumerate(sparse):
        pack_id = item.get("id") or item.get("python_module") or item.get("title")
        scores[pack_id] = scores.get(pack_id, 0.0) + 1.0 / (c + rank)
        by_id[pack_id] = (
            _merge_candidates([by_id[pack_id], item])[0] if pack_id in by_id else item
        )
    order = sorted(scores, key=lambda pack_id: scores[pack_id], reverse=True)
    return [by_id[pack_id] for pack_id in order[:k]]


def _dense_documents_to_candidates(documents) -> list[dict]:
    dense: list[dict] = []
    for document in documents:
        metadata = document.metadata or {}
        dense.append({
            "id": metadata.get("pack_id") or metadata.get("id") or "",
            "title": metadata.get("title", ""), "content": document.page_content,
            "node_names": [
                name for name in (metadata.get("node_names", "") or "").split(",")
                if name
            ],
            "python_module": metadata.get("python_module", ""),
        })
    return dense


def search_node_packs_many(
    cfg: EmbedConfig, queries: list[str], k: int = 8,
    *, dense_indexes: set[int] | None = None,
) -> list[list[dict]]:
    """批量嵌入指定查询，再对全部查询分别执行 BM25 与融合。"""
    if not queries:
        return []
    collection = NODE_CHUNK_COLLECTION if chunk_collection_ready(cfg) else NODE_INDEX_COLLECTION
    dense_groups: list[list[dict]] = [[] for _ in queries]
    try:
        vector_store = _store(collection, cfg)
        indexes = (
            list(range(len(queries))) if dense_indexes is None
            else sorted(index for index in dense_indexes if 0 <= index < len(queries))
        )
        vectors = rag_backend.embeddings(cfg).embed_documents([queries[index] for index in indexes])
        for index, vector in zip(indexes, vectors):
            documents = vector_store.similarity_search_by_vector(vector, k=max(k, 12))
            dense_groups[index] = _dense_documents_to_candidates(documents)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("uvicorn.error").warning(
            "search_node_packs_many dense 失败 base_url=%r model=%r: %s",
            cfg.base_url, cfg.embed_model, exc,
        )
    groups: list[list[dict]] = []
    for query, dense in zip(queries, dense_groups):
        sparse = _bm25_search(cfg, query, max(k, 12), collection)
        dense = _merge_candidates(dense)
        sparse = _merge_candidates(sparse)
        for item in dense:
            if not item.get("id"):
                item["id"] = item.get("python_module") or item.get("title")
        groups.append(_rrf_fuse(dense, sparse, k) if dense or sparse else [])
    return groups


def search_node_packs(cfg: EmbedConfig, query: str, k: int = 8) -> list[dict]:
    """单路兼容接口；多路查询应调用 search_node_packs_many。"""
    groups = search_node_packs_many(cfg, [query], k=k)
    return groups[0] if groups else []
