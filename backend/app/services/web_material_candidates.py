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


def register_candidates(images: list[dict], query: str = "", provider: str = "") -> None:
    """登记一批搜索结果的 full_url 为可下载候选。images 为 M1.2 返回结构。"""
    if not images:
        return
    now = time.time()
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
        # FIFO 淘汰 + TTL 清理
        while len(_CANDIDATES) > _MAX_CANDIDATES:
            _CANDIDATES.popitem(last=False)
        expired = [u for u, m in _CANDIDATES.items()
                   if now - m.get("registered_at", 0) > _CANDIDATE_TTL_SECONDS]
        for u in expired:
            _CANDIDATES.pop(u, None)


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
