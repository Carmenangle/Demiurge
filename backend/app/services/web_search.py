"""联网灵感搜索：可插拔搜索源 Adapter 注册表 + 两个内置源（bing-cn / ddg）。

形态对照 scene_illustration 的 renderer 注册表：
- 协议定义一张接口，每个 Adapter 注册一个名字
- 调用方不感知具体源，只传 provider 名字
- 新增搜索源 = 注册一个新 Adapter，不改调用方

文字源现状：`bing-cn`（cn.bing.com，国内**直连**可用）+ `ddg`（DuckDuckGo，
需代理）。不传 provider 时按 `_AUTO_CHAIN` 回落（bing-cn → ddg），所以
「不开代理也能找灵感」；显式传 provider 则只用该源，不回落。

代理口径（2026-09-10 校正，勿再照抄旧注释）：本模块访问【外网】，一律
`trust_env=False` + **显式 proxy 参数**——不读系统 env，避免「改了环境变量但
调用方不知道」的隐性生效路径；`proxy` 为空即真直连。代理地址由调用方提供：
- 灵感/联网检索链路：前端 `globalProxyAddress(settings)`（系统代理优先，手填兜底）；
- 后端自检：`services/system_proxy.resolve()`（读 WinINET 系统代理 → 环境变量 → 手填）。

`trust_env=False` 的另一处用途是连本机 127.0.0.1 服务（那种反而**不能**走代理，
见 `llm.py`），目的与本模块相反，勿混淆。
"""
import html
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

# 自动回落链：未显式指定 provider 时按序逐个尝试，取第一个有结果的源。
# 顺序理由（2026-09-10 实测）：`bing-cn` 国内**直连**可用 → 不开代理也能搜；
# `ddg` 在有代理时结果更国际化，作第二跳。反过来代价很大——ddg 直连会先耗满
# 20s 超时才回落，等于每次没配代理都白等一轮。
_AUTO_CHAIN: tuple[str, ...] = ("bing-cn", "ddg")


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
    """去标签 + 还原 HTML 实体 + 压空白。

    实体用 `html.unescape` 统一还原（不再手写替换表）——手写表漏掉的实体
    （如 Bing 摘要里的 `&ensp;`）会原样进模型上下文，看着像乱码。
    """
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
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


# ── Bing 中国站实现（国内可直连，2026-09-10 新增） ────────────────
# 为什么加它：DDG 在国内直连不通，导致「不开代理就用不了找灵感」；cn.bing.com
# 国内可直连（实测 3 组查询全部 200 + 10 条 organic 结果），且与既有
# `BingImageSearchAdapter` 同源同构（都是 `b_algo`/`iusc` 那套 HTML），
# 免密钥、零配置。`www.bing.com` 在国内会 302 到 cn.bing.com，故直接打 cn 域。
#
# 解析口径（2026-09-10 实测校准）：标题取 `<h2><a href=...>`，摘要取
# `<p class="…b_lineclamp…">`；两者**独立 findall 后按索引配对**——比
# 先切 `<li class="b_algo">…</li>` 块更稳（块内含嵌套 `</li>` 时非贪婪切分会截断）。
# 计数不一致时**宁可丢弃全部摘要**也不按索引硬配：错位会把 A 的摘要挂到 B 上，
# 静默污染模型上下文（同类教训见画布节点卡「#20 框里是 #18」）。
# 已知未处理：`bing.com/ck/a` 跳转式结果链接（三组实测 0 例，出现时原样返回）。

_BING_CN = "https://cn.bing.com/search"
_BING_CN_TIMEOUT = 12  # 回落链首跳，失败要快，别让 ddg 白等 20s


class BingCnSearchAdapter:
    """Bing 中国站（cn.bing.com）HTML 搜索实现。国内可直连，免密钥。"""

    def search(self, query: str, max_results: int = 6, proxy: str = "") -> list[dict]:
        if not (query or "").strip():
            return []
        try:
            client_kw: dict = {"timeout": _BING_CN_TIMEOUT, "follow_redirects": True,
                               "trust_env": False}
            if proxy and proxy.strip():
                client_kw["proxy"] = proxy.strip()
            with httpx.Client(**client_kw) as c:
                r = c.get(_BING_CN, headers=_HEADERS, params={"q": query, "ensearch": "0"})
                r.raise_for_status()
                page = r.text
        except Exception:
            return []
        links = re.findall(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
        snippets = re.findall(r'<p class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', page, re.S)
        if len(links) != len(snippets):
            snippets = []  # 结构漂移：宁缺摘要，不错位配对
        out: list[dict] = []
        for i, (url, title) in enumerate(links):
            if len(out) >= max_results:
                break
            href = _strip_tags(url)
            if href.startswith("/"):
                href = "https://cn.bing.com" + href
            if not href.startswith(("http://", "https://")):
                continue
            out.append({
                "title": _strip_tags(title),
                "snippet": _strip_tags(snippets[i]) if i < len(snippets) else "",
                "url": href,
            })
        return out


register_adapter("bing-cn", BingCnSearchAdapter())


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

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _query_tokens(text: str) -> set[str]:
    """查询词的判据 token：中文用字符 bigram，拉丁文按词（小写）。

    中文不用 jieba（零依赖），bigram 已够判「结果是否在谈这个词」；
    英文**必须用词**——字符 bigram 会把「maid」与任何含 ma/ai/id 的英文都判相关，
    形同虚设（2026-09-11 实锤：`maid dress victorian style` 被放松成只搜 maid，
    bigram 判据照样放行）。
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return set()
    if _CJK_RE.search(s):
        compact = s.replace(" ", "")
        return {compact[i:i + 2] for i in range(len(compact) - 1)}
    return {w.lower() for w in re.findall(r"[A-Za-z0-9]+", s) if len(w) >= 2}


def filter_relevant(query: str, results: list[dict]) -> list[dict]:
    """丢掉与查询词不相关的结果，返回仍相关的子集。

    为什么必须有这一层（2026-09-11 实锤）：Bing CN 对**无 cookie 的 HTML SERP**
    会静默「改写/放松查询」——搜「超时空辉夜姬的月见八千代的外貌信息」返回的是
    「超（汉语汉字）_百度百科 / 超星 / 超自然行动组」（放松到首字），搜「搜索X」
    返回的是搜狗/360/一起搜/必应/Google 五个**搜索引擎首页**（把引导动词当主题词）。
    页面上没有任何「已显示…的结果」提示，搜索框里还是原查询，解析层无从分辨——
    垃圾结果照单全收喂给模型，模型只好声明检索失败或**凭角色名编造设定**。

    判据：query 的 token（中文 bigram / 英文词）在「标题+摘要」里的**命中数**：
    token 总数 ≥2 → 至少命中 2 个才保留；只有 1 个 token → 命中 1 个即可；
    0 个 token（单字中文）→ 不过滤（没有可判信号）。
    「至少 2」这一条不能降成「有交集就行」：2 字中文查询（如「可畏」）只有 1 个
    bigram，有交集就行的话放松结果照样全放行（同日实锤）。
    - 「可畏」 vs 「可（汉语文字）_百度百科」：{可畏} 不命中 → 丢
    - 「月见八千代」 vs 「月见八千代 - 萌娘百科」：命中 4/4 → 留
    - 「超时空辉夜姬」 vs 「超辉夜姬！ - 萌娘百科」：命中 {辉夜,夜姬} 2/5 → 留（容错改写）
    - `maid dress victorian style` vs 「maid（英语单词）」：命中 1/4 → 丢
    """
    qs = _query_tokens(query)
    if not qs:
        return results
    need = 2 if len(qs) >= 2 else 1

    def keep(r: dict) -> bool:
        got = _query_tokens(f"{r.get('title', '')} {r.get('snippet', '')}")
        return len(qs & got) >= need

    return [r for r in results if keep(r)]


def web_search(query: str, max_results: int = 6, proxy: str = "",
               provider: str | None = None) -> list[dict]:
    """返回 [{title, snippet, url}]。失败返回空列表（不抛，调用方兜底）。

    provider 为搜索源名称（`bing-cn` / `ddg`）；**None / 空 / "auto" = 走自动回落链**
    （`_AUTO_CHAIN`，国内直连源在前）。显式指定时**只试这一个源、不回落**——
    既然点名了源，悄悄换源只会把失败藏起来。
    `"auto"` 之所以也算自动：前端设置项的值要能原样透传；若把它当未注册源，
    会静默返回空列表，是最难查的一类故障。
    proxy 为访问外网的代理地址（如 http://127.0.0.1:7897）；空则直连。

    每个源的结果先过 `filter_relevant`：引擎改写查询返回的无关结果**等同该源失败**，
    继续回落，而不是把垃圾喂给调用方的模型。
    """
    name = (provider or "").strip()
    names = _AUTO_CHAIN if (not name or name.lower() == "auto") else (name,)
    for name in names:
        adapter = get_adapter(name)
        if adapter is None:
            continue
        results = adapter.search(query, max_results=max_results, proxy=proxy)
        results = filter_relevant(query, results)
        if results:
            return results
    return []


def probe(proxy: str = "", query: str = "ping", provider: str | None = None) -> tuple[bool, str]:
    """联网搜索可用性探针：真跑一次最小搜索，返回 (能否搜到, 说明)。

    为什么不探别的站点：本机直连某些境外静态端点（如 gstatic 204）可能通，
    但搜索源照样拿不到结果——「端口在听」「某站可达」都不等于**搜索真的能用**
    （2026-09-10 实测：gstatic 直连 204，同一时刻 DDG 直连超时）。
    这里的判据与灵感搜索完全同源（同一 Adapter、同一代理），结论可直接采信。
    """
    try:
        results = web_search(query, max_results=2, proxy=proxy, provider=provider)
    except Exception as exc:  # noqa: BLE001 - 探针只报告可用性，不上抛
        return False, f"{type(exc).__name__}: {exc}"[:200]
    if results:
        return True, f"搜索正常（返回 {len(results)} 条）"
    return False, "搜索无结果（网络或搜索源不可用）"
