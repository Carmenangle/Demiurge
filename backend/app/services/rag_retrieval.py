"""普通知识库的稀疏召回与多路融合；不依赖 Chroma 或路由层。"""
from __future__ import annotations

import re


def _tokenize(text: str) -> list[str]:
    value = (text or "").lower()
    return re.findall(r"[a-z0-9]+", value) + re.findall(r"[一-鿿]", value)


def sparse_rank(query: str, documents: list[dict], k: int) -> list[dict]:
    """BM25 关键词召回；依赖不可用或语料为空时返回空。"""
    if not documents or not query.strip():
        return []
    try:
        from rank_bm25 import BM25Plus
    except ImportError:
        return []
    corpus = [_tokenize(f"{doc.get('title', '')} {doc.get('content', '')}") for doc in documents]
    if not any(corpus):
        return []
    query_tokens = _tokenize(query)
    query_set = set(query_tokens)
    scores = BM25Plus(corpus).get_scores(query_tokens)
    order = sorted(range(len(documents)), key=lambda index: float(scores[index]), reverse=True)
    return [
        documents[index] for index in order
        if query_set.intersection(corpus[index]) and float(scores[index]) > 0
    ][:k]


def rrf_fuse(rankings: list[tuple[str, list[dict]]], k: int, c: int = 60) -> list[dict]:
    """融合系统库 Dense、仓库库 Dense 与 BM25，多路命中按稳定文档 id 去重。"""
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    channels: dict[str, list[str]] = {}
    for channel, ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            ident = str(item.get("id") or "")
            if not ident:
                continue
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (c + rank)
            by_id.setdefault(ident, item)
            if channel not in channels.setdefault(ident, []):
                channels[ident].append(channel)
    order = sorted(scores, key=lambda ident: (-scores[ident], ident))
    return [
        {**by_id[ident], "score": scores[ident], "channels": channels[ident]}
        for ident in order[:k]
    ]


def needs_rerank(candidates: list[dict]) -> bool:
    """只有首选结果缺少 Dense/BM25 交叉验证时才值得运行 Cross-Encoder。"""
    if len(candidates) < 2:
        return False
    channels = {str(channel) for channel in candidates[0].get("channels", []) or []}
    has_dense = any(channel.startswith("dense:") for channel in channels)
    return not (has_dense and "bm25" in channels)
