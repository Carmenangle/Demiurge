"""上网素材源适配层（P2-1 切片）：pixiv / danbooru 图站搜索。

统一返回结构 `{full_url, thumb_url, source_url, title, tags, author, provider}`，
搜索成功后**自动登记受控下载候选**（web_material_candidates）——保存仍走既有
`/web-materials/save`，安全模型不变（只落登记过的 URL，防供应链）。

- **danbooru**：公开 API `posts.json`，无需鉴权（api_key 可选附加提额）。
- **pixiv**：官方 `pixivpy3` SDK（内置 OAuth 常量），需 refresh token。

外网受限/凭证缺失时返回 `{ok: False, error: <可读指引>}`，不抛错（调用方 toast 展示）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

import httpx

from app.services import web_material_candidates

_log = logging.getLogger("uvicorn.error")

_DANBOORU_API = "https://danbooru.donmai.us/posts.json"
_DANBOORU_POST = "https://danbooru.donmai.us/posts"
_DANBOORU_FAVORITES = "https://danbooru.donmai.us/favorites.json"
_PIXIV_ARTWORK = "https://www.pixiv.net/artworks"
_TIMEOUT = 20
_IMAGE_TIMEOUT = 60  # 大图直连可能很慢（实测 1445×1197 约 20s）
# 「查看大图」代理白名单：只允许图站 CDN（防 SSRF——url 由前端传入）
_ALLOWED_IMAGE_HOSTS = ("donmai.us", "pximg.net", "pixiv.net")


def _client(proxy_url: str = "", timeout: float = _TIMEOUT) -> httpx.Client:
    # trust_env=False：与本项目其他外网调用一致，避免误用环境代理
    if proxy_url:
        return httpx.Client(proxy=proxy_url, timeout=timeout, trust_env=False)
    return httpx.Client(timeout=timeout, trust_env=False)


def _curl_request(method: str, url: str, params: dict | None = None,
                  data: dict | None = None, proxy: str = "", timeout: int = 60,
                  ) -> tuple[int, bytes] | None:
    """curl 子进程请求，返回 (status, body)；curl 缺失/失败返回 None（由调用方兜底）。

    2026-10-16 用户实测：httpx 走代理请求 danbooru 偶发
    `[SSL: UNEXPECTED_EOF_WHILE_READING]`（TLS 被中断，翻页时触发）⇒ 先用 curl
    走系统代理（同一代理下 curl 稳定），失败再回落 httpx。
    """
    curl = shutil.which("curl")
    if not curl:
        return None
    import subprocess
    import tempfile
    from urllib.parse import urlencode

    q = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    full = f"{url}?{q}" if q else url
    fd, tmp = tempfile.mkstemp(suffix=".body")
    os.close(fd)
    try:
        args = [curl, "-sS", "-L", "--max-time", str(timeout),
                "-o", tmp, "-w", "%{http_code}"]
        if proxy:
            args += ["-x", proxy]
        if method == "POST":
            args += ["-X", "POST"]
            for k, v in (data or {}).items():
                args += ["--data-urlencode", f"{k}={v}"]
        args.append(full)
        try:
            proc = subprocess.run(args, capture_output=True, timeout=timeout + 15, text=True)
        except Exception as exc:  # noqa: BLE001 超时/异常 → 回落
            _log.warning("curl 请求异常 %s: %s", url, exc)
            return None
        if proc.returncode != 0:
            _log.warning("curl 请求非零退出(%s) %s: %s",
                         proc.returncode, url, (proc.stderr or "")[:200])
            return None
        status_line = (proc.stdout or "0").strip().splitlines()
        status = int(status_line[-1]) if status_line and status_line[-1].isdigit() else 0
        return status, Path(tmp).read_bytes()
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass


def search_danbooru(query: str, limit: int = 20, api_key: str = "", login: str = "",
                    page: int = 1, proxy: str = "") -> dict:
    """danbooru 公开 API 搜索。返回 {ok, items, error, provider}。

    ⚠ 鉴权是**用户名 + API Key 两个字段配对**（danbooru 用 Basic 语义）：
    只填 api_key 不生效，必须配 login（用户名）——两者齐全才附加到查询参数提额；
    都不填=匿名请求（公开 API 可用，限额较低）。
    `page`：danbooru 分页（1~1000，每页 limit 条）。
    **空 query 合法**：danbooru 默认按 id 降序（=最新在前）返回，即「按时间排序的默认浏览」
    （2026-10-16 用户口径：不选排行榜/收藏夹就是默认时间序）。
    """
    params: dict[str, str | int] = {
        "tags": (query or "").strip(),
        "limit": max(1, min(limit, 50)),
        "page": max(1, min(int(page or 1), 1000)),
    }
    if api_key and login:
        params["api_key"] = api_key
        params["login"] = login

    # 通路：curl 优先（httpx 走代理偶发 SSL UNEXPECTED_EOF）→ httpx 兜底（重试 2 次）
    curl_res = _curl_request("GET", _DANBOORU_API, params=params, proxy=proxy)
    if curl_res is not None:
        status, body = curl_res
    else:
        status, body = 0, b""
        last: Exception | None = None
        for _attempt in range(3):
            try:
                with _client(proxy) as c:
                    r = c.get(_DANBOORU_API, params=params,
                              headers={"User-Agent": "Demiurge/1.0 (material source)"})
                status, body = r.status_code, r.content
                last = None
                break
            except Exception as e:  # noqa: BLE001 抖动重试
                last = e
        if last is not None:
            return {"ok": False, "items": [], "provider": "danbooru",
                    "error": f"danbooru 请求失败（检查网络或设置里的代理）：{last}"}
    if status in (401, 403):
        return {"ok": False, "items": [], "provider": "danbooru",
                "error": "danbooru 鉴权失败：请检查「设置 → 凭证」里的用户名与 API Key 是否配对正确"
                         "（danbooru 需要用户名 + API Key 两个字段）"}
    if status != 200:
        return {"ok": False, "items": [], "provider": "danbooru",
                "error": f"danbooru 返回 HTTP {status}"
                         + ("（查询语法或标签无效）" if status == 400 else "")}
    try:
        posts = json.loads(body)
    except Exception:  # noqa: BLE001 非 JSON（代理错误页等）
        return {"ok": False, "items": [], "provider": "danbooru",
                "error": "danbooru 返回了非 JSON 内容（可能被代理拦截，检查代理设置）"}

    items: list[dict] = []
    for p in posts if isinstance(posts, list) else []:
        full = str(p.get("file_url") or "").strip()
        if not full:
            continue  # 受限帖（仅预览）跳过
        pid = p.get("id")
        items.append({
            "full_url": full,
            "large_url": str(p.get("large_file_url") or full),  # 1920px 版（「查看大图」用它：原图常 20MB+）
            "thumb_url": str(p.get("preview_file_url") or p.get("large_file_url") or full),
            "source_url": f"{_DANBOORU_POST}/{pid}",
            "title": f"danbooru #{pid}",
            "tags": [t for t in str(p.get("tag_string") or "").split() if t][:24],
            "author": str(p.get("tag_string_artist") or "").replace("_", " ").strip(),
            "provider": "danbooru",
            "rating": str(p.get("rating") or ""),
            # 「编辑/详情」面板用：分组标签 + 分辨率 + 上传时间 + 评分/收藏数
            "post_id": pid,
            "tags_general": [t for t in str(p.get("tag_string_general") or "").split() if t],
            "tags_artist": [t for t in str(p.get("tag_string_artist") or "").split() if t],
            "tags_character": [t for t in str(p.get("tag_string_character") or "").split() if t],
            "tags_copyright": [t for t in str(p.get("tag_string_copyright") or "").split() if t],
            "tags_meta": [t for t in str(p.get("tag_string_meta") or "").split() if t],
            "width": p.get("image_width") or 0,
            "height": p.get("image_height") or 0,
            "file_size": p.get("file_size") or 0,
            "created_at": str(p.get("created_at") or ""),
            "score": p.get("score") or 0,
            "fav_count": p.get("fav_count") or 0,
            "is_favorited": bool(p.get("is_favorited")),
        })
    if not items:
        return {"ok": False, "items": [], "provider": "danbooru",
                "error": "没有结果（换个标签试试；danbooru 标签用英文下划线，如 blue_sky 1girl）"}
    web_material_candidates.register_candidates(items, query=query, provider="danbooru")
    return {"ok": True, "items": items, "provider": "danbooru", "error": ""}


def _pixiv_auth(refresh_token: str, proxy: str):
    """构造并鉴权 AppPixivAPI（forward proxy 挂到底层 requests 会话）。

    ⚠ 关键：pixivpy3 的 set_api_proxy() 是把 self.hosts 整体替换成传入地址（API 镜像机制，
    默认 http://app-api.pixivlite.com），【不能】用来做 HTTP 转发代理。用 Clash 这类转发代理
    必须挂到底层 requests/cloudscraper 会话的 proxies 上，让 SDK 仍以真实 app-api.pixiv.net
    为目标，经代理 CONNECT 出去。auth 后 self.user_id 即为 token 内编码的账号 id。
    """
    from pixivpy3 import AppPixivAPI

    api = AppPixivAPI()
    if proxy:
        api.requests.proxies = {"http": proxy, "https": proxy}
    api.auth(refresh_token=refresh_token.strip())
    return api


def _pixiv_illusts_to_items(illusts) -> list[dict]:
    """把 pixiv illust 列表映射成统一 item 结构（同时覆盖搜索/推荐/排行榜/收藏/作者流）。

    多页作品：`meta_pages` 含**全部页**（p0..pN-1，与顶层 image_urls 的 p0 重复），每页都有
    `image_urls.large`（2026-10-16 实测：搜索返回里 meta_pages 的 large 已填充，无需再调 detail）。
    ⇒ 汇总成 `pages`（全部页 large URL）+ `page_count`，供前端「查看大图翻页 / 下载全部页」。
    """
    items: list[dict] = []
    for ill in (illusts or []):
        urls = getattr(ill, "image_urls", None)
        full = str(getattr(urls, "large", "") or getattr(urls, "medium", "") or "")
        if not full:
            continue
        # 原图（全分辨率，下载用）：多页取 meta_pages[0].image_urls.original；
        # 单页取 meta_single_page.original_image_url 或 image_urls.original。
        # 2026-10-17 实测：feed 里这两处 original 都 100% 已填充，无需再调 detail。
        mp0 = (getattr(ill, "meta_pages", None) or [None])
        original = str(getattr(getattr(mp0[0], "image_urls", None), "original", "") or "") if mp0[0] else ""
        if not original:
            msp = getattr(ill, "meta_single_page", None)
            original = str((getattr(msp, "original_image_url", "") if msp else "")
                           or getattr(urls, "original", "") or "")
        # full_url 用**原图**（下载落盘即全分辨率；旧前端只存 full_url 也能拿到原图）；
        # large_url 保持 large（1200px）供「查看大图」快速加载 ⇒ 互不影响。
        full_url = original or full
        tags = [t.name for t in (getattr(ill, "tags", None) or []) if getattr(t, "name", "")]
        # 多页：收集每页 large URL（meta_pages 含 p0；为空则单页）
        pages: list[str] = []
        for pg in (getattr(ill, "meta_pages", None) or []):
            pu = getattr(pg, "image_urls", None)
            pl = str(getattr(pu, "large", "") or getattr(pu, "medium", "") or "")
            if pl:
                pages.append(pl)
        if not pages:
            pages = [full]
        page_count = int(getattr(ill, "page_count", 0) or 0) or len(pages)
        items.append({
            "full_url": full_url,   # 原图（下载落盘全分辨率；旧前端只存 full_url 也能拿到原图）
            "large_url": full,      # 1200px（「查看大图」快速加载用，与 full_url 解耦）
            # 缩略图用 medium（540px，**自然长宽比**）而非 square_medium（360×360 方裁）——
            # 与 danbooru 市场一致，瀑布流里卡片按原图比例显示（2026-10-17 用户要求）。
            "thumb_url": str(getattr(urls, "medium", "") or getattr(urls, "square_medium", "") or full),
            "source_url": f"{_PIXIV_ARTWORK}/{ill.id}",
            "title": str(getattr(ill, "title", "") or f"pixiv #{ill.id}"),
            "tags": tags[:24],
            "author": str(getattr(getattr(ill, "user", None), "name", "") or ""),
            "provider": "pixiv",
            "rating": "r18" if getattr(ill, "x_restrict", 0) else "all",
            "post_id": int(getattr(ill, "id", 0) or 0),  # pixiv 即 illust id（收藏用）
            "width": getattr(ill, "width", 0) or 0,
            "height": getattr(ill, "height", 0) or 0,
            "page_count": page_count,   # 多页作品总页数（单页=1）
            "pages": pages,             # 全部页 large URL（查看大图翻页 / 下载全部页用）
            # pixiv 列表接口不返 created_at（需 detail），留空
        })
    return items


def search_pixiv(query: str = "", limit: int = 20, refresh_token: str = "",
                 page: int = 1, proxy: str = "", mode: str = "search",
                 mode_param: str = "") -> dict:
    """pixiv 搜索 / 信息流（官方 pixivpy3 SDK）。

    mode ∈ {search, recommended, ranking, bookmarks, author}：
    - search：关键词搜索（空 query ⇒ 回退 recommended，pixiv 无公开「最新」流）
    - recommended：推荐（默认进入页）
    - ranking：排行榜（mode_param=day|week|month）
    - bookmarks：我的收藏夹（需鉴权，user_id 来自 token）
    - author：某作者作品（mode_param=作者 user_id）
    `page`：用 offset 近似翻页。
    """
    if not refresh_token:
        return {"ok": False, "items": [], "provider": "pixiv",
                "error": "pixiv 需要 refresh token：请到「设置 → 凭证 → 上网素材源鉴权」填写"
                         "（获取：命令行跑 uvx gppt login -j，复制返回的 refresh_token；"
                         "refresh token 已含账号身份，无需另填用户名）"}
    try:
        api = _pixiv_auth(refresh_token, proxy)
        p = max(1, int(page or 1))
        offset = (p - 1) * max(1, min(limit, 50))
        m = (mode or "search").strip()
        if m == "search" and not (query or "").strip():
            m = "recommended"  # 空 query 无「最新」流 ⇒ 推荐兜底
        next_cursor = ""
        if m == "recommended":
            result = api.illust_recommended(offset=offset)
        elif m == "ranking":
            result = api.illust_ranking(mode=(mode_param or "day").strip() or "day", offset=offset)
        elif m == "bookmarks":
            # ⚠ pixiv 收藏夹翻页用 **max_bookmark_id 游标**（在返回的 next_url 里），
            # `user_bookmarks_illust` 【没有 offset 参数】——传 offset 直接 TypeError。
            kw: dict = {}
            if (mode_param or "").strip():
                kw["max_bookmark_id"] = mode_param.strip()
            result = api.user_bookmarks_illust(api.user_id, **kw)
            nu = getattr(result, "next_url", None)
            if nu:
                qs = api.parse_qs(nu) or {}
                next_cursor = str(qs.get("max_bookmark_id", "") or "")
        elif m == "author":
            result = api.user_illusts((mode_param or "").strip(), offset=offset)
        else:  # search
            result = api.search_illust((query or "").strip(),
                                      search_target="partial_match_for_tags",
                                      sort="popular_desc", offset=offset)
    except Exception as e:  # noqa: BLE001 鉴权失败/网络不可达统一转可读错误
        return {"ok": False, "items": [], "provider": "pixiv",
                "error": f"pixiv 请求失败（{"代理 " + proxy + "，已探活" if proxy else "直连"}）：{e}。提示：VPN 模式代理（Clash 等）无需配置代理地址，直连即被接管；若配置了地址请确认端口正在监听（设置 → 路径与代理）"}

    items = _pixiv_illusts_to_items(getattr(result, "illusts", None) or [])
    if not items:
        return {"ok": False, "items": [], "provider": "pixiv",
                "error": "没有结果（换个关键词 / 筛选条件试试）"}
    web_material_candidates.register_candidates(items, query=query, provider="pixiv")
    out: dict = {"ok": True, "items": items, "provider": "pixiv", "error": ""}
    if next_cursor:
        out["next_cursor"] = next_cursor   # 收藏夹下一页游标（前端接续加载用）
    return out


def pixiv_pages(illust_id: int, refresh_token: str = "", proxy: str = "") -> dict:
    """多页作品全页 URL（illust_detail）：`pages`＝large(1200px，查看大图用)，
    `originals`＝原图(全分辨率，下载用)。两者逐页对齐（无原图的页回退 large）。

    ⚠ feed（搜索/推荐/排行榜）对**大作品**常只给 `page_count`、`meta_pages` 为空或不全
    （2026-10-16 实测：某 99 页作品 feed 里 page_count=99 但 meta_pages=0）⇒ 直接下载只会
    拿到封面一页。故查看大图/下载全部页前**按需**调本接口补全（illust_detail 稳返全部页）。
    """
    if not illust_id:
        return {"ok": False, "pages": [], "originals": [], "error": "缺少 illust_id"}
    if not refresh_token:
        return {"ok": False, "pages": [], "originals": [], "error": "pixiv 需要 refresh token：请到「设置 → 凭证」填写"}
    try:
        api = _pixiv_auth(refresh_token, proxy)
        d = getattr(api.illust_detail(illust_id), "illust", None)
        pages: list[str] = []      # large（查看大图）
        originals: list[str] = []  # 原图（下载）
        for pg in (getattr(d, "meta_pages", None) or []):
            pu = getattr(pg, "image_urls", None)
            pl = str(getattr(pu, "large", "") or getattr(pu, "medium", "") or "")
            po = str(getattr(pu, "original", "") or "")
            if pl:
                pages.append(pl)
                originals.append(po or pl)   # 没有原图的页回退 large
        if not pages:  # 单页兜底：原图在 meta_single_page.original_image_url 或 image_urls.original
            u = str(getattr(getattr(d, "image_urls", None), "large", "") or "")
            if u:
                msp = getattr(d, "meta_single_page", None)
                o = str((getattr(msp, "original_image_url", "") if msp else "")
                        or getattr(getattr(d, "image_urls", None), "original", "") or "")
                pages = [u]
                originals = [o or u]
        # ⚠ 关键：把**下载用的原图页**登记成「受控下载候选」——`/web-materials/save` 只放行登记过的
        # URL，搜索时只登记了封面；detail 补拉的页不登记就会被 save 闸拒（400）⇒ 多页只存封面 1 张。
        # detail 出自已鉴权 pixiv API，与搜索结果同等信任。
        reg = originals if originals else pages
        if reg:
            tags = [t.name for t in (getattr(d, "tags", None) or []) if getattr(t, "name", "")]
            author = str(getattr(getattr(d, "user", None), "name", "") or "")
            web_material_candidates.register_candidates(
                [{"full_url": u, "source_url": f"{_PIXIV_ARTWORK}/{illust_id}",
                  "tags": tags, "author": author, "provider": "pixiv"} for u in reg],
                query="", provider="pixiv",
            )
        return {"ok": True, "pages": pages, "originals": originals,
                "page_count": len(pages), "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "pages": [], "originals": [], "error": f"pixiv 获取作品分页失败：{e}"}


def pixiv_following(refresh_token: str = "", proxy: str = "", page: int = 1) -> dict:
    """pixiv 关注作者列表（user_following）。

    返回 {ok, users:[{id, name, account, avatar_url}], error, provider}；
    avatar_url 仍在 i.pximg.net ⇒ 前端走 marketImageProxyUrl 代理。
    """
    if not refresh_token:
        return {"ok": False, "users": [], "provider": "pixiv",
                "error": "pixiv 需要 refresh token：请到「设置 → 凭证」填写"}
    try:
        api = _pixiv_auth(refresh_token, proxy)
        p = max(1, int(page or 1))
        offset = (p - 1) * 30
        result = api.user_following(api.user_id, offset=offset)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "users": [], "provider": "pixiv",
                "error": f"pixiv 关注列表获取失败（token 失效或网络不可达）：{e}"}
    users: list[dict] = []
    for u in (getattr(result, "user_previews", None) or []):
        user = getattr(u, "user", None)
        if not user:
            continue
        pimg = getattr(getattr(user, "profile_image_urls", None), "medium", "") or ""
        users.append({
            "id": int(getattr(user, "id", 0) or 0),
            "name": str(getattr(user, "name", "") or ""),
            "account": str(getattr(user, "account", "") or ""),
            "avatar_url": str(pimg),
        })
    if not users:
        return {"ok": False, "users": [], "provider": "pixiv",
                "error": "没有关注的作者（或已到列表末尾）"}
    return {"ok": True, "users": users, "provider": "pixiv", "error": ""}


def favorite_pixiv(illust_id: int, refresh_token: str = "", proxy: str = "") -> dict:
    """收藏到 pixiv 账号收藏夹（illust_bookmark_add）。幂等：已收藏报错软当 already=True。"""
    if not illust_id:
        return {"ok": False, "already": False, "error": "缺少 illust_id（该结果可能非 pixiv 作品）"}
    if not refresh_token:
        return {"ok": False, "already": False,
                "error": "pixiv 需要 refresh token：请到「设置 → 凭证」填写"}
    try:
        api = _pixiv_auth(refresh_token, proxy)
        api.illust_bookmark_add(illust_id)
        return {"ok": True, "already": False, "error": ""}
    except Exception as e:  # noqa: BLE001
        err = str(e)
        # pixiv 已收藏再 add 会返回错误（400 / already 语义），软当成功
        if "already" in err.lower() or "400" in err:
            return {"ok": True, "already": True, "error": ""}
        return {"ok": False, "already": False, "error": f"pixiv 收藏失败：{err}"}


def unfavorite_pixiv(illust_id: int, refresh_token: str = "", proxy: str = "") -> dict:
    """从 pixiv 账号收藏夹移除（illust_bookmark_delete）。幂等：未收藏报错软当成功。"""
    if not illust_id:
        return {"ok": False, "already": False, "error": "缺少 illust_id（该结果可能非 pixiv 作品）"}
    if not refresh_token:
        return {"ok": False, "already": False,
                "error": "pixiv 需要 refresh token：请到「设置 → 凭证」填写"}
    try:
        api = _pixiv_auth(refresh_token, proxy)
        api.illust_bookmark_delete(illust_id)
        return {"ok": True, "already": False, "error": ""}
    except Exception as e:  # noqa: BLE001
        err = str(e)
        # 未收藏再 delete 会返回错误（400 / not found），软当成功（目标态＝未收藏）
        if "400" in err or "not" in err.lower() or "404" in err:
            return {"ok": True, "already": True, "error": ""}
        return {"ok": False, "already": False, "error": f"pixiv 取消收藏失败：{err}"}


def search(source: str, query: str, limit: int = 20, *, pixiv_token: str = "",
           danbooru_key: str = "", danbooru_login: str = "", page: int = 1,
           proxy: str = "", mode: str = "search", mode_param: str = "") -> dict:
    """统一入口：source ∈ {danbooru, pixiv}。pixiv 支持 mode/recommended/ranking/bookmarks/author。"""
    src = (source or "").strip().lower()
    if src == "danbooru":
        return search_danbooru(query, limit, api_key=danbooru_key,
                               login=danbooru_login, page=page, proxy=proxy)
    if src == "pixiv":
        return search_pixiv(query, limit, refresh_token=pixiv_token,
                            page=page, proxy=proxy, mode=mode, mode_param=mode_param)
    return {"ok": False, "items": [], "provider": src, "error": f"未知素材源：{source}"}


def favorite_danbooru(post_id: int, login: str, api_key: str, proxy: str = "") -> dict:
    """收藏到 danbooru 账号收藏夹（POST /favorites.json，需 用户名 + API Key 配对）。

    幂等语义：已收藏（409/422）返回 ok:True + already=True，不当失败。
    """
    if not (login.strip() and api_key.strip()):
        return {"ok": False, "already": False,
                "error": "收藏需要 danbooru 用户名 + API Key 配对：请到「设置 → 凭证 → 上网素材源鉴权」填写"}
    if not post_id:
        return {"ok": False, "already": False, "error": "缺少 post_id（该结果可能非 danbooru 帖）"}
    # 通路与搜索一致：curl 优先（httpx 走代理偶发 SSL EOF）→ httpx 兜底
    curl_res = _curl_request("POST", _DANBOORU_FAVORITES,
                             params={"login": login.strip(), "api_key": api_key.strip()},
                             data={"post_id": int(post_id)}, proxy=proxy)
    if curl_res is not None:
        status = curl_res[0]
    else:
        try:
            with _client(proxy) as c:
                r = c.post(_DANBOORU_FAVORITES,
                           data={"post_id": int(post_id)},
                           params={"login": login.strip(), "api_key": api_key.strip()},
                           headers={"User-Agent": "Demiurge/1.0 (material source)"})
            status = r.status_code
        except Exception as e:  # noqa: BLE001 网络不可达统一转可读错误
            return {"ok": False, "already": False, "error": f"收藏请求失败（网络/代理）：{e}"}
    if status in (200, 201, 204):
        return {"ok": True, "already": False, "error": ""}
    if status in (409, 422):
        return {"ok": True, "already": True, "error": ""}  # 已收藏
    if status in (401, 403):
        return {"ok": False, "already": False,
                "error": "收藏失败：鉴权不足（检查用户名/API Key 配对，或账号权限）"}
    return {"ok": False, "already": False, "error": f"收藏失败：HTTP {status}"}


def fetch_image_bytes(url: str, proxy: str = "") -> tuple[bytes, str]:
    """抓取图站图片字节（供「查看大图」经后端代理）。

    仅允许白名单 host（url 由前端传入 ⇒ 防 SSRF）。返回 (bytes, content_type)。

    ⚠ **主路径用 curl 子进程（2026-10-16 实测）**：本机 Python 的 HTTP 栈（httpx/urllib）
    抓 `cdn.donmai.us` 大文件会挂死（>200s 无输出），而 curl 同一 URL 约 20s 完成
    （curl 走系统级代理/Happy Eyeballs）。故 curl 优先、httpx 兜底（curl 缺失时）。
    单次超时 90s；失败抛 RuntimeError（路由层转 502）。
    """
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_IMAGE_HOSTS):
        raise RuntimeError(f"不允许的图片主机：{host or '(空)'}")

    # 通路顺序按 host 区分（2026-10-16 实测，治「查看大图」慢）：
    # - **pixiv（pximg）必须走代理**：直连 i.pximg.net 恒定失败（且 `--connect-timeout` 前要 ~21s 才超时）。
    #   若先试直连，每张图都白等一遍再回落代理——缩略图被懒加载/缓存掩盖，查看大图是阻塞单发 ⇒ 暴露成"极慢"。
    #   ⇒ pximg **只走代理**（curl 代理 → httpx 代理），跳过注定失败的死直连。
    # - **danbooru CDN 直连最快**（走代理反而不通）⇒ 直连优先、代理兜底。
    is_pixiv = "pximg" in host or "pixiv" in host

    curl = shutil.which("curl")
    if curl:
        if is_pixiv:
            if proxy:
                data = _fetch_via_curl(curl, url, proxy)
                if data is not None:
                    return data
                return _fetch_via_httpx(url, proxy)   # curl 代理失败 → httpx 代理（不同连接栈）
            data = _fetch_via_curl(curl, url, "")     # 无代理：只能直连试一次
            if data is not None:
                return data
        else:
            for px in (("", proxy) if proxy else ("",)):  # 直连优先、代理兜底
                data = _fetch_via_curl(curl, url, px)
                if data is not None:
                    return data
    return _fetch_via_httpx(url, proxy)


def _guess_image_type(url: str) -> str:
    ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
    return {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")


def _referer_for_url(url: str) -> str:
    """防盗链 Referer 按 host 区分（2026-10-17 实测）：
    - pximg.net / pixiv.net（pixiv CDN）⇒ https://www.pixiv.net/
    - donmai.us（danbooru CDN）⇒ https://danbooru.donmai.us/
    ⚠ pixiv 图片 CDN 域名是 **pximg.net**（i.pximg.net），**不含子串 "pixiv"**——
    早先误写成 `"pixiv" in host` 导致 pximg 全被判成 danbooru Referer ⇒ 403（图全裂）。
    """
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if "pximg" in host or "pixiv" in host:
        return "https://www.pixiv.net/"
    return "https://danbooru.donmai.us/"


def _fetch_via_curl(curl: str, url: str, proxy: str) -> tuple[bytes, str] | None:
    """curl 抓图（写临时文件，避免与 -w 输出混杂）。失败返回 None 由调用方回落。

    ⚠ **env 剥离代理变量**：curl 即便带 `-x` 也会被继承来的 `NO_PROXY`/`HTTP_PROXY` 干扰
    （`NO_PROXY='*'` 会让 `-x` 失效、改走直连 ⇒ pximg 白等 ~21s 超时）。故子进程env 里
    去掉这些变量，让 curl **只认我们显式传的 `-x`**，行为确定。
    ⚠ `--connect-timeout 10`：连接阶段挂起时 10s 快速失败（默认会拖到 ~21s），别浪费在死直连上。
    """
    import subprocess
    import tempfile

    env = {k: v for k, v in os.environ.items()
           if k not in ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy",
                        "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")}
    fd, tmp = tempfile.mkstemp(suffix=".img")
    os.close(fd)
    try:
        args = [curl, "-sS", "-L", "--fail", "--connect-timeout", "10", "--max-time", "90",
                "-o", tmp, "-w", "%{content_type}",
                "-H", "User-Agent: Demiurge/1.0 (material source)",
                "-H", f"Referer: {_referer_for_url(url)}"]
        if proxy:
            args += ["-x", proxy]
        args.append(url)
        try:
            proc = subprocess.run(args, capture_output=True, timeout=120, text=True, env=env)
        except Exception as exc:  # noqa: BLE001 超时/缺 curl 依赖 → 回落
            _log.warning("curl 抓图失败 url=%s: %s", url, exc)
            return None
        if proc.returncode != 0:
            _log.warning("curl 抓图非零退出(%s) url=%s: %s",
                         proc.returncode, url, (proc.stderr or "")[:200])
            return None
        data = Path(tmp).read_bytes()
        if not data:
            return None
        ctype = (proc.stdout or "").strip().split(";")[0] or _guess_image_type(url)
        return data, ctype
    except Exception as exc:  # noqa: BLE001 读临时文件等异常 → 回落
        _log.warning("curl 抓图后处理失败 url=%s: %s", url, exc)
        return None
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except OSError:
            pass


def _fetch_via_httpx(url: str, proxy: str) -> tuple[bytes, str]:
    """httpx 兜底抓图（先代理、后直连两级重试）。

    建连包 pinned_host：白名单域名的解析只发生一次，连接钉在解析到的 IP
    （TOCTOU 关窗；curl 主通路是子进程无法钉扎，见 url_guard 模块 docstring）。
    """
    from urllib.parse import urlparse

    from app.services.url_guard import pinned_host

    host = (urlparse(url).hostname or "").lower()
    attempts = ([proxy] if proxy else []) + [""]
    last: Exception | None = None
    for px in attempts:
        try:
            with pinned_host(host), _client(px, timeout=_IMAGE_TIMEOUT) as c:
                r = c.get(url, headers={
                    "User-Agent": "Demiurge/1.0 (material source)",
                    "Referer": _referer_for_url(url),  # 防盗链按 host 区分（pximg/danbooru）
                })
            if r.status_code == 200:
                return r.content, r.headers.get("content-type", _guess_image_type(url))
            last = RuntimeError(f"抓取图片失败：HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001 换下一种通路
            last = RuntimeError(f"抓取图片失败（{px or '直连'}）：{e}")
    raise RuntimeError(f"{last}（已尝试代理与直连）")
