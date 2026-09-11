"""上网素材「受控下载」候选注册表（M1.3）。

核心语义：`/web-materials/save` 只接受**本会话搜索结果登记过**的图片 URL，
不接受客户端任意提交 URL 落盘（防供应链：任意 URL 落盘是安全隐患）。
灵感搜索（inspiration.search_and_refine）成功返回图片结果时登记候选；
下载保存时校验 src 必须命中候选（data URI / local-view 本地可信来源豁免）。

实现：进程内注册表，TTL 过期自动淘汰 + FIFO 上限防膨胀。跨进程/重启后
候选丢失 → 保存被拒，属预期（灵感卡在会话快照里，重新搜索即可再存）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

_CANDIDATE_TTL_SECONDS = 30 * 60          # 候选有效 30 分钟（搜索结果时效性）
_MAX_CANDIDATES = 4096                    # FIFO 上限防内存膨胀

_LOCK = threading.Lock()
# {full_url: {"source_url": str, "query": str, "provider": str, "registered_at": float}}
_CANDIDATES: "OrderedDict[str, dict]" = OrderedDict()

# 最近一批登记的图片 URL（顺序 = 搜索结果顺序，1 起对应 pick 序号）。
# 2026-09-10：图片直链（Bing 的 murl）常带查询串且很长，让模型逐字抄写易截断/改字；
# 序号（pick=3）比长 URL 稳定，故额外记住批次序供 candidate_at 反查。
# 就地替换（切片赋值）而非重新绑定，读写都在同一把锁下，无需 global。
_LAST_BATCH: list[str] = []


def register_candidates(images: list[dict], query: str = "", provider: str = "") -> None:
    """登记一批搜索结果的 full_url 为可下载候选。images 为 M1.2 返回结构。

    同时把本批 URL 记为「最近一批」（顺序不变），供 candidate_at(pick) 按序号取用。
    """
    if not images:
        return
    now = time.time()
    batch: list[str] = []
    with _LOCK:
        for image in images:
            if not isinstance(image, dict):
                continue
            url = str(image.get("full_url") or "").strip()
            if not url:
                continue
            _CANDIDATES[url] = {
                "source_url": str(image.get("source_url") or ""),
                "query": query,
                "provider": provider,
                "registered_at": now,
            }
            _CANDIDATES.move_to_end(url)
            if url not in batch:
                batch.append(url)
        # FIFO 淘汰 + TTL 清理
        while len(_CANDIDATES) > _MAX_CANDIDATES:
            _CANDIDATES.popitem(last=False)
        expired = [u for u, m in _CANDIDATES.items()
                   if now - m.get("registered_at", 0) > _CANDIDATE_TTL_SECONDS]
        for u in expired:
            _CANDIDATES.pop(u, None)
        if batch:
            _LAST_BATCH[:] = batch


def candidate_at(index: int) -> str:
    """按 1 起序号取「最近一批」搜索结果的图片 URL（越界/已过期返回空串）。"""
    try:
        position = int(index)
    except (TypeError, ValueError):
        return ""
    if position < 1:
        return ""
    with _LOCK:
        if position > len(_LAST_BATCH):
            return ""
        url = _LAST_BATCH[position - 1]
    # is_candidate 自带锁，必须在 _LOCK 外调用（threading.Lock 不可重入）
    return url if is_candidate(url) else ""


def last_batch_size() -> int:
    """最近一批搜索结果的图片数量（0 = 本进程内还没有过搜索结果）。"""
    with _LOCK:
        return len(_LAST_BATCH)


def candidate_meta(url: str) -> dict:
    """返回候选元数据（未登记/已过期返回空 dict）。"""
    url = (url or "").strip()
    if not url:
        return {}
    now = time.time()
    with _LOCK:
        meta = _CANDIDATES.get(url)
        if not meta:
            return {}
        if now - meta.get("registered_at", 0) > _CANDIDATE_TTL_SECONDS:
            _CANDIDATES.pop(url, None)
            return {}
        return dict(meta)


def is_candidate(url: str) -> bool:
    """该 URL 是否已登记为可下载候选。"""
    return bool(candidate_meta(url))
