"""模型市场浏览：CivitAI / Hugging Face 搜索与筛选（下载走 model_downloader）。

外网必须走代理（proxy 参数），与 127.0.0.1 的 trust_env=False 相反。
只做浏览/搜索，归一化成前端要的卡片结构；实际下载沿用 model_downloader。
"""
import time

import httpx

_CIVITAI_API = "https://civitai.com/api/v1/models"


class BrowseError(Exception):
    """浏览失败，区分错误来源供前端给准确提示。
    kind='upstream' 上游服务器 5xx（对方临时不可用，非代理问题）；
    kind='network' 连接层失败（代理没开/不通/超时）；kind='other' 其他。"""
    def __init__(self, message: str, kind: str = "other"):
        self.kind = kind
        super().__init__(message)


def _client(proxy: str) -> httpx.Client:
    kw: dict = {"trust_env": False, "follow_redirects": True, "timeout": 30}
    if proxy and proxy.strip():
        kw["proxy"] = proxy.strip()
    return httpx.Client(**kw)


def _get_json(c: httpx.Client, url: str, *, params: dict | None = None,
              headers: dict | None = None, retries: int = 3) -> dict:
    """GET 并解析 JSON，对 5xx（尤其 Civitai 常见的临时 503）做退避重试。
    重试耗尽仍 5xx → BrowseError('upstream')；连接失败 → BrowseError('network')。"""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = c.get(url, params=params, headers=headers)
        except httpx.RequestError as e:
            # 连接/代理/超时层错误：多为代理问题，重试一次后即报 network
            last_exc = e
            if attempt < retries - 1:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise BrowseError(f"无法连接（确认代理已开启且可用）：{e}", "network")
        if r.status_code >= 500:
            last_exc = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            if attempt < retries - 1:
                time.sleep(0.8 * (attempt + 1))  # 退避重试，503 多为临时过载
                continue
            raise BrowseError(
                f"对方服务器暂时不可用（HTTP {r.status_code}），这是上游临时过载，非代理问题，请稍后重试。",
                "upstream")
        if r.status_code >= 400:
            raise BrowseError(f"请求被拒绝（HTTP {r.status_code}）：{r.text[:200]}", "other")
        return r.json()
    raise BrowseError(f"请求失败：{last_exc}", "other")


def _civitai_card(m: dict) -> dict:
    """归一 CivitAI 模型条目为前端卡片。取首个版本的首图/文件/基础模型。"""
    versions = m.get("modelVersions") or []
    v0 = versions[0] if versions else {}
    imgs = [i.get("url", "") for i in (v0.get("images") or []) if i.get("url")]
    stats = m.get("stats") or {}
    return {
        "id": m.get("id"),
        "name": m.get("name", ""),
        "type": m.get("type", ""),
        "nsfw": bool(m.get("nsfw", False)),
        "creator": (m.get("creator") or {}).get("username", ""),
        "downloads": stats.get("downloadCount", 0) or 0,
        "likes": stats.get("thumbsUpCount", 0) or 0,
        "cover": imgs[0] if imgs else "",
        "base_model": v0.get("baseModel", ""),
        "version_id": v0.get("id"),
        # 下载用：首个版本的首个文件直链
        "download_url": (v0.get("files") or [{}])[0].get("downloadUrl", ""),
        "model_url": f"https://civitai.com/models/{m.get('id')}",
    }


def civitai_browse(
    proxy: str = "",
    query: str = "",
    types: str = "",          # Checkpoint / LORA / VAE / Controlnet / TextualInversion / Upscaler
    sort: str = "Highest Rated",
    period: str = "AllTime",
    base_models: str = "",    # 如 "SDXL 1.0" / "Pony" / "Flux.1 D"
    nsfw: bool = False,
    cursor: str = "",
    limit: int = 24,
    token: str = "",
) -> dict:
    """浏览/搜索 CivitAI 模型。游标分页。返回 {items:[卡片], next_cursor}。"""
    params: dict = {"limit": limit, "sort": sort, "period": period, "nsfw": str(nsfw).lower()}
    if query:
        params["query"] = query
    if types:
        params["types"] = types
    if base_models:
        params["baseModels"] = base_models
    if cursor:
        params["cursor"] = cursor
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with _client(proxy) as c:
        d = _get_json(c, _CIVITAI_API, params=params, headers=headers)
    items = [_civitai_card(m) for m in (d.get("items") or [])]
    return {"items": items, "next_cursor": (d.get("metadata") or {}).get("nextCursor", "")}


# —— CivArchive：跨平台模型归档，按 sha256 聚合多下载源（原平台删了也能从镜像下）——
_CIVARCHIVE = "https://civarchive.com"


def civarchive_search(proxy: str = "", query: str = "", type: str = "",
                      page: int = 1, nsfw: bool = False) -> dict:
    """搜索 CivArchive。返回 {items:[卡片], total}。
    kind=version 指向模型版本，kind=file 指向具体文件(带 sha256)。"""
    params: dict = {"page": page}
    if query:
        params["q"] = query
    if type:
        params["type"] = type
    with _client(proxy) as c:
        d = _get_json(c, f"{_CIVARCHIVE}/api/search", params=params)
    items = []
    for x in d.get("results") or []:
        if x.get("kind") not in ("version", "file"):
            continue  # 跳过 user 等
        if x.get("is_nsfw") and not nsfw:
            continue
        # sha256：file kind 的 url 形如 /sha256/<hash>
        sha = ""
        direct = ""
        url = x.get("url", "") or ""
        if url.startswith("/sha256/"):
            sha = url[len("/sha256/"):]
        else:
            # version kind：url 形如 /models/113841?modelVersionId=123021 → civitai 直下链接
            import re as _re
            mv = _re.search(r"modelVersionId=(\d+)", url)
            if mv and (x.get("platform") == "civitai"):
                direct = f"https://civitai.com/api/download/models/{mv.group(1)}"
        items.append({
            "id": str(x.get("id", "")),
            "name": x.get("name", ""),
            "type": x.get("type", ""),
            "kind": x.get("kind", ""),
            "nsfw": bool(x.get("is_nsfw", False)),
            "downloads": x.get("download_count", 0) or 0,
            "cover": x.get("image_url") or "",
            "base_model": x.get("base_model", "") or "",
            "platform": x.get("platform", "") or "",
            "sha256": sha,
            "direct_url": direct,     # version kind 的 civitai 直下链接（file kind 走 sha256 多源）
            "civarchive_url": _CIVARCHIVE + url if url.startswith("/") else url,
        })
    return {"items": items, "total": d.get("total_hits", 0)}


def civarchive_sources(proxy: str, sha256: str) -> dict:
    """按 sha256 拿该文件的全部下载源（civitai/huggingface/镜像）。
    返回 {files:[{filename,url,source,is_gated,is_paid}], model:{...}}。"""
    with _client(proxy) as c:
        return _get_json(c, f"{_CIVARCHIVE}/api/sha256/{sha256}")
