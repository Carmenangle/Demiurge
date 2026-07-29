"""RAG 向量后端 Adapter：嵌入配置、嵌入调用与 Chroma 实例缓存。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from app.config import CHROMA_DIR


@dataclass(frozen=True)
class EmbedConfig:
    """嵌入和可选重排模型配置的单一属主。"""

    base_url: str = ""
    api_key: str = ""
    embed_model: str = "text-embedding-3-small"
    model_dir: str = ""
    reranker_dir: str = ""
    mode: Literal["remote", "local"] = "remote"

def _norm_url(base_url: str) -> str:
    url = (base_url or "").rstrip("/")
    if not url.endswith("/v1") and "/chat/completions" not in url:
        url += "/v1"
    return url


class _RemoteEmbeddings(Embeddings):
    """OpenAI 兼容远程嵌入 Adapter；禁用环境代理。"""

    def __init__(self, cfg: EmbedConfig):
        self._url = _norm_url(cfg.base_url) + "/embeddings"
        self._headers = {"Authorization": f"Bearer {cfg.api_key or 'not-needed'}"}
        self._model = cfg.embed_model
        self._keep_alive = (urlparse(cfg.base_url).hostname or "").lower() in {
            "localhost", "127.0.0.1", "::1",
        }

    def _embed(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(trust_env=False, timeout=120) as client:
            payload = {"model": self._model, "input": texts}
            if self._keep_alive:
                payload["keep_alive"] = "30m"
            response = client.post(self._url, headers=self._headers, json=payload)
            if self._keep_alive and response.status_code in {400, 422}:
                payload.pop("keep_alive", None)
                response = client.post(self._url, headers=self._headers, json=payload)
            response.raise_for_status()
            data = response.json()["data"]
        data.sort(key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class _LocalEmbeddings(Embeddings):
    """SentenceTransformer 本地嵌入 Adapter。"""

    def __init__(self, model_dir: str):
        self._model_dir = model_dir.strip()
        self._local = None

    def _model(self):
        if self._local is not None:
            return self._local
        model_path = Path(self._model_dir)
        if not model_path.is_dir():
            raise ValueError(f"本地嵌入模型目录不存在：{self._model_dir}")
        try:
            from sentence_transformers import SentenceTransformer
            self._local = SentenceTransformer(self._model_dir, local_files_only=True)
            return self._local
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"加载本地嵌入模型失败：{exc}") from exc

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model().encode(
            texts, normalize_embeddings=False, convert_to_numpy=True,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


EmbeddingKey = tuple[str, ...]
_EMBEDDING_CACHE: dict[EmbeddingKey, Embeddings] = {}
_STORE_CACHE: dict[tuple[str, EmbeddingKey], Chroma] = {}


def embedding_key(cfg: EmbedConfig) -> EmbeddingKey:
    """只包含会改变向量或嵌入调用的配置；reranker 不得分裂 Chroma 缓存。"""
    if cfg.mode == "local":
        return "local", cfg.model_dir
    return "remote", cfg.base_url, cfg.api_key, cfg.embed_model


def embeddings(cfg: EmbedConfig) -> Embeddings:
    key = embedding_key(cfg)
    if key not in _EMBEDDING_CACHE:
        if cfg.mode == "local":
            if not cfg.model_dir.strip():
                raise ValueError("本地嵌入模式未填写模型目录")
            _EMBEDDING_CACHE[key] = _LocalEmbeddings(cfg.model_dir)
        elif cfg.mode == "remote":
            _EMBEDDING_CACHE[key] = _RemoteEmbeddings(cfg)
        else:
            raise ValueError(f"未知嵌入模式：{cfg.mode}")
    return _EMBEDDING_CACHE[key]


def embed_query(cfg: EmbedConfig, query: str) -> list[float]:
    return embeddings(cfg).embed_query(query)


def store(collection: str, cfg: EmbedConfig) -> Chroma:
    """按 collection 与嵌入配置复用 Chroma，保证连续写读一致。"""
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    key = (collection, embedding_key(cfg))
    if key not in _STORE_CACHE:
        _STORE_CACHE[key] = Chroma(
            collection_name=collection,
            embedding_function=embeddings(cfg),
            persist_directory=str(CHROMA_DIR),
        )
    return _STORE_CACHE[key]


def local_model_files_status(path: Path) -> tuple[bool, list[str]]:
    """检查 SentenceTransformer/HuggingFace 本地模型文件是否完整。"""
    metadata = path / "modules.json"
    direct_config = path / "config.json"
    if not metadata.is_file() and not direct_config.is_file():
        return False, ["modules.json 或 config.json"]

    indexed_shards: set[Path] = set()
    for index_path in path.rglob("*.safetensors.index.json"):
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            indexed_shards.update(
                index_path.parent / str(name)
                for name in (payload.get("weight_map") or {}).values()
            )
        except (OSError, ValueError, TypeError):
            return False, [str(index_path.relative_to(path)) + " 无法解析"]
    weights = [
        candidate for candidate in path.rglob("*")
        if candidate.is_file()
        and candidate.suffix.lower() in {".safetensors", ".bin", ".pt", ".onnx"}
    ]
    weights.extend(shard for shard in indexed_shards if shard.is_file())
    if not any(weight.stat().st_size > 0 for weight in weights):
        return False, ["本地权重文件（*.safetensors、*.bin、*.pt 或 *.onnx）"]
    missing_shards = [
        str(shard.relative_to(path))
        for shard in indexed_shards
        if not shard.is_file() or shard.stat().st_size <= 0
    ]
    return (False, missing_shards) if missing_shards else (True, [])
