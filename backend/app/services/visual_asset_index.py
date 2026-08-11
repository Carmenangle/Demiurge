"""资产视觉向量 Adapter：Qwen3-VL-Embedding 与独立 Chroma collection。"""
from __future__ import annotations

import re
import sys
import gc
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, unquote, urlparse

from app.config import CHROMA_DIR, DATA_DIR
from app.services import model_lease

MODEL_DIR = DATA_DIR / "models" / "vl-embedding" / "Qwen3-VL-Embedding-2B"
_MODEL = None
_MODEL_LOCK = RLock()


def model_available() -> bool:
    weight = MODEL_DIR / "model.safetensors"
    return (
        (MODEL_DIR / "artifact-manifest.json").is_file()
        and (MODEL_DIR / "config.json").is_file()
        and weight.is_file()
        and weight.stat().st_size == 4_255_140_312
    )


def _model():
    global _MODEL
    if _MODEL is None:
        if not model_available():
            raise RuntimeError("Qwen3-VL-Embedding-2B 尚未完整安装")
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError:
            # 开发 venv 只装基础后端时，复用同一 base Python 已安装的 Full RAG 可选层。
            base_site = Path(sys.base_prefix) / "Lib" / "site-packages"
            if base_site.is_dir() and str(base_site) not in sys.path:
                sys.path.append(str(base_site))
            from sentence_transformers import SentenceTransformer

        _MODEL = SentenceTransformer(
            str(MODEL_DIR), local_files_only=True, device="cuda",
            model_kwargs={"torch_dtype": "float16"},
        )
    return _MODEL


def release_accelerator_memory() -> bool:
    global _MODEL
    with _MODEL_LOCK:
        existed = _MODEL is not None
        _MODEL = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (ImportError, RuntimeError):
            pass
        return existed


def _encode(inputs: list[object]) -> list[list[float]]:
    from app.services import model_lease

    lease = model_lease.acquire(
        "visual-asset-index", "visual_embedding", priority=10,
        estimated_mib=4600, ttl_seconds=600,
    )
    if lease is None:
        raise RuntimeError("ComfyUI 或更高优先级本地模型正在占用显存，视觉索引已排队等待")
    try:
        with _MODEL_LOCK:
            vectors = _model().encode(
                inputs, batch_size=1, normalize_embeddings=True, convert_to_numpy=True,
            )
            return vectors.tolist()
    finally:
        model_lease.release(lease.token)


model_lease.register_releaser("visual_embedding", release_accelerator_memory)


def _collection(repo_id: str):
    import chromadb

    rid = re.sub(r"[^a-zA-Z0-9_-]", "_", repo_id.strip()) or "home"
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=f"asset_visual_{rid}", metadata={"hnsw:space": "cosine"},
    )


def _local_path(image_url: str) -> Path | None:
    if "local-view" not in (image_url or ""):
        return None
    value = (parse_qs(urlparse(image_url).query).get("path") or [""])[0]
    path = Path(unquote(value)) if value else None
    return path if path and path.is_file() else None


def index_items(repo_id: str, items: list[dict]) -> dict[str, int]:
    """只索引仍存在的本机资产；跳过远程与裂图。"""
    rows = [(item, _local_path(str(item.get("image_url") or ""))) for item in items]
    rows = [(item, path) for item, path in rows if path is not None]
    if not rows:
        return {"indexed": 0, "skipped": len(items)}
    vectors = _encode([{"image": str(path)} for _item, path in rows])
    collection = _collection(repo_id)
    collection.upsert(
        ids=[str(item["id"]) for item, _path in rows], embeddings=vectors,
        documents=[str(item.get("description") or item.get("prompt") or "") for item, _path in rows],
        metadatas=[{"repo_id": repo_id, "image_url": str(item.get("image_url") or "")}
                   for item, _path in rows],
    )
    return {"indexed": len(rows), "skipped": len(items) - len(rows)}


def search(repo_ids: list[str], query: str, k: int) -> list[str]:
    """文字查询与图片向量同空间检索，返回 generation id 排名。"""
    collections = []
    for repo_id in dict.fromkeys(repo_ids):
        collection = _collection(repo_id)
        if collection.count() > 0:
            collections.append(collection)
    if not collections or not query.strip():
        return []
    vector = _encode([query.strip()])[0]
    ranked: list[str] = []
    for collection in collections:
        result = collection.query(query_embeddings=[vector], n_results=max(1, min(k, collection.count())))
        ranked.extend((result.get("ids") or [[]])[0])
    return list(dict.fromkeys(ranked))[:k]
