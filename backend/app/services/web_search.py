"""联网灵感搜索：DuckDuckGo 免密钥网页抓取 → 提炼成生图提示词灵感。

用于「找服装/发型/画风参考」等：搜网页摘要，交对话模型提炼成一组英文提示词标签，
前端渲染成「灵感卡」，卡内提示词可一键插入对话。
注意：访问【外网】必须走系统代理（trust_env=True，httpx 默认）；
trust_env=False 只用于连本机 127.0.0.1 服务（那种反而不能走代理）。本模块是外网，故用默认。
"""
import re

import httpx

_DDG_HTML = "https://html.duckduckgo.com/html/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) laf-inspiration/1.0"}


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&quot;", '"').replace("&#x27;", "'").replace("&#39;", "'"))
    return re.sub(r"\s+", " ", s).strip()


def web_search(query: str, max_results: int = 6, proxy: str = "") -> list[dict]:
    """返回 [{title, snippet, url}]。失败返回空列表（不抛，调用方兜底）。
    proxy 为访问外网的代理地址（如 http://127.0.0.1:7897）；空则直连。"""
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

