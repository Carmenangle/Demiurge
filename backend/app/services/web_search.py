"""联网灵感搜索：可插拔搜索源 Adapter 注册表 + DDG 默认实现。

形态对照 scene_illustration 的 renderer 注册表：
- 协议定义一张接口，每个 Adapter 注册一个名字
- 调用方不感知具体源，只传 provider 名字
- 新增搜索源 = 注册一个新 Adapter，不改调用方

注意：访问【外网】必须走系统代理（trust_env=True，httpx 默认）；
trust_env=False 只用于连本机 127.0.0.1 服务（那种反而不能走代理）。本模块是外网，故用默认。
"""
import json
import re
from typing import Protocol, runtime_checkable

import httpx


# ── Adapter 协议 ─────────────────────────────────────────────

@runtime_checkable
class SearchAdapter(Protocol):
    """搜索源适配器接口。每个搜索源实现此协议后注册到注册表。"""

    def search(self, query: str, max_results: int = 6, proxy: str = "") -> list[dict]:
        """返回 [{title, snippet, url}]。失败返回空列表。"""
        ...


@runtime_checkable
class ImageSearchAdapter(Protocol):
    """图片搜索适配器接口（M1.2）：返回 [{thumb_url, full_url, source_url, title?}]。"""

    def search_images(self, query: str, max_results: int = 8, proxy: str = "") -> list[dict]:
        """返回图片结果。失败返回空列表（调用方降级纯文字卡）。"""
        ...


# ── 注册表 ───────────────────────────────────────────────────

_ADAPTERS: dict[str, SearchAdapter] = {}
_DEFAULT_ADAPTER = "ddg"


def register_adapter(name: str, adapter: SearchAdapter) -> None:
    """注册一个搜索源 Adapter。新增源 = 在这里登记，不改调用方。"""
    _ADAPTERS[name] = adapter


def get_adapter(name: str) -> SearchAdapter | None:
    """取 Adapter；未注册返回 None。"""
    return _ADAPTERS.get(name)


def available_adapters() -> list[str]:
    """已注册的搜索源名称（供前端选择/能力探测）。"""
    return sorted(_ADAPTERS)


# ── 图片搜索注册表（M1.2，形态同文本注册表） ─────────────────────

_IMAGE_ADAPTERS: dict[str, ImageSearchAdapter] = {}
_IMAGE_DEFAULT = "bing-images"


def register_image_adapter(name: str, adapter: ImageSearchAdapter) -> None:
    """注册一个图片搜索源 Adapter。新增源 = 在这里登记，不改调用方。"""
    _IMAGE_ADAPTERS[name] = adapter


def get_image_adapter(name: str) -> ImageSearchAdapter | None:
    """取图片 Adapter；未注册返回 None。"""
    return _IMAGE_ADAPTERS.get(name)


def image_search(query: str, max_results: int = 8, proxy: str = "",
                 provider: str | None = None) -> list[dict]:
    """返回 [{thumb_url, full_url, source_url, title?}]。失败返回空列表（不抛，调用方降级）。"""
    name = provider or _IMAGE_DEFAULT
    adapter = get_image_adapter(name)
    if adapter is None:
        return []
    return adapter.search_images(query, max_results=max_results, proxy=proxy)


# ── DDG 实现 ─────────────────────────────────────────────────

_DDG_HTML = "https://html.duckduckgo.com/html/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) laf-inspiration/1.0"}


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&quot;", '"').replace("&#x27;", "'").replace("&#39;", "'"))
    return re.sub(r"\s+", " ", s).strip()


class DDGSearchAdapter:
    """DuckDuckGo HTML 搜索实现。免密钥，HTML 正则解析。"""

    def search(self, query: str, max_results: int = 6, proxy: str = "") -> list[dict]:
        if not (query or "").strip():
            return []
        try:
            # 外网：走用户配置的代理（本机直连外网常被墙/超时）。trust_env=False 关掉系统 env 代理，
            # 只用显式传入的 proxy，行为可控。proxy 为空则真直连。
            client_kw: dict = {"timeout": 20, "follow_redirects": True, "trust_env": False}
            if proxy and proxy.strip():
                client_kw["proxy"] = proxy.strip()
            with httpx.Client(**client_kw) as c:
                r = c.get(_DDG_HTML, headers=_HEADERS, params={"q": query})
                r.raise_for_status()
                html = r.text
        except Exception:
            return []
        out: list[dict] = []
        titles = re.findall(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
        snippets = re.findall(r'result__snippet"[^>]*>(.*?)</a>', html, re.S)
        for i, (url, title) in enumerate(titles[:max_results]):
            snip = snippets[i] if i < len(snippets) else ""
            out.append({
                "title": _strip_tags(title),
                "snippet": _strip_tags(snip),
                "url": _strip_tags(url),
            })
        return out


# ── 注册默认源 ─────────────────────────────────────────────────

register_adapter("ddg", DDGSearchAdapter())


# ── Bing Images 实现（M1.2 图片搜索） ──────────────────────────
# 解析 <a class="iusc" m="{json}"> 的 m 属性：murl=原图直链、turl=缩略图、
# purl=来源网页、t=标题。免密钥，HTML 正则解析（与 DDG 同为已知脆弱项，adapter 隔离故障域）。

_BING_IMAGES = "https://www.bing.com/images/search"


class BingImageSearchAdapter:
    """Bing Images 图片搜索实现。"""

    def search_images(self, query: str, max_results: int = 8, proxy: str = "") -> list[dict]:
        if not (query or "").strip():
            return []
        try:
            client_kw: dict = {"timeout": 20, "follow_redirects": True, "trust_env": False}
            if proxy and proxy.strip():
                client_kw["proxy"] = proxy.strip()
            with httpx.Client(**client_kw) as c:
                r = c.get(_BING_IMAGES, headers=_HEADERS,
                          params={"q": query, "form": "HDRSC2"})
                r.raise_for_status()
                html = r.text
        except Exception:
            return []
        import html as html_lib
        out: list[dict] = []
        for m in re.findall(r'<a[^>]*class="[^"]*iusc[^"]*"[^>]*m="([^"]+)"', html):
            try:
                meta = json.loads(html_lib.unescape(m))
            except (ValueError, TypeError):
                continue
            if not isinstance(meta, dict):
                continue
            murl = (meta.get("murl") or "").strip()
            if not murl.startswith(("http://", "https://")):
                continue
            turl = (meta.get("turl") or "").strip()
            purl = (meta.get("purl") or "").strip()
            title = (meta.get("t") or "").strip()
            out.append({
                "thumb_url": turl if turl.startswith(("http://", "https://")) else murl,
                "full_url": murl,
                "source_url": purl,
                "title": title,
            })
            if len(out) >= max_results:
                break
        return out


register_image_adapter("bing-images", BingImageSearchAdapter())


# ── 向后兼容入口 ─────────────────────────────────────────────

def web_search(query: str, max_results: int = 6, proxy: str = "",
               provider: str | None = None) -> list[dict]:
    """返回 [{title, snippet, url}]。失败返回空列表（不抛，调用方兜底）。

    provider 为搜索源名称（如 'ddg'），None 使用默认源。
    proxy 为访问外网的代理地址（如 http://127.0.0.1:7897）；空则直连。
    """
    name = provider or _DEFAULT_ADAPTER
    adapter = get_adapter(name)
    if adapter is None:
        return []
    return adapter.search(query, max_results=max_results, proxy=proxy)
