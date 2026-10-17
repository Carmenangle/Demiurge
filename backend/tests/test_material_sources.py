"""P2-1 切片：图站素材源（danbooru / pixiv）搜索 + 候选登记 + 端点契约。

锁四件事：
1. danbooru 映射：file_url→full_url、preview→thumb、tags/author 拆装；受限帖（无 file_url）跳过；
2. pixiv：无 token 返回可读指引（不抛）；mock SDK 下映射正确；
3. 搜索成功即登记受控下载候选（保存走既有 save 闸，安全模型不变）——候选带 tags/author；
4. 端点：读凭证面板设置透传；空 query 400。
"""
from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest

from app.routers import comfyui as comfyui_router
from app.services import material_sources, web_material_candidates


class _FakeResp:
    def __init__(self, status: int, payload, content: bytes | None = None,
                 headers: dict | None = None):
        self.status_code = status
        self._payload = payload
        # 与真实响应一致：content 为 JSON 字节（生产代码读 content + json.loads）
        self.content = content if content is not None else json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp: _FakeResp):
        self._resp = resp
        self.last_params: dict | None = None
        self.last_data: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None, headers=None):  # noqa: ARG002
        self.last_params = params
        return self._resp

    def post(self, url, data=None, params=None, headers=None):  # noqa: ARG002
        self.last_data = data
        self.last_params = params
        return self._resp


def _stub_http(monkeypatch, resp: _FakeResp) -> None:
    # ⚠ 同时屏蔽 curl 主路径（否则测试会真跑子进程抓网图）；强制走 httpx 替身
    monkeypatch.setattr(material_sources.shutil, "which", lambda name: None)  # noqa: ARG005
    monkeypatch.setattr(material_sources, "_client",
                        lambda proxy_url="", timeout=None: _FakeClient(resp))  # noqa: ARG005


def _stub_http_capture(monkeypatch, resp: _FakeResp) -> dict:
    """替身 HTTP，并暴露最后一次请求的 client（供断言查询参数）。"""
    holder: dict = {}

    def make(proxy_url="", timeout=None):  # noqa: ARG001
        holder["client"] = _FakeClient(resp)
        return holder["client"]

    monkeypatch.setattr(material_sources.shutil, "which", lambda name: None)  # noqa: ARG005
    monkeypatch.setattr(material_sources, "_client", make)
    return holder


_DB_POST = {
    "id": 987,
    "file_url": "https://cdn.donmai.us/original/aa/bb/abc.jpg",
    "large_file_url": "https://cdn.donmai.us/sample/aa/bb/abc.jpg",
    "preview_file_url": "https://cdn.donmai.us/preview/abc.jpg",
    "tag_string": "1girl solo blue_sky",
    "tag_string_artist": "some_artist",
    "tag_string_general": "1girl solo blue_sky",
    "tag_string_meta": "highres",
    "image_width": 1445,
    "image_height": 1197,
    "file_size": 1234567,
    "created_at": "2026-10-15T12:28:24.266-04:00",
    "score": 42,
    "fav_count": 7,
    "rating": "g",
}


def test_danbooru_maps_fields_and_skips_restricted(monkeypatch):
    posts = [
        _DB_POST,
        {"id": 1},  # 受限帖：无 file_url → 跳过
        {**_DB_POST, "id": 988, "file_url": "https://cdn.donmai.us/original/x.jpg"},
    ]
    _stub_http(monkeypatch, _FakeResp(200, posts))

    r = material_sources.search_danbooru("blue_sky 1girl", limit=10)

    assert r["ok"] is True
    assert len(r["items"]) == 2
    first = r["items"][0]
    assert first["full_url"].endswith("abc.jpg")
    assert first["large_url"].endswith("sample/aa/bb/abc.jpg")  # 查看大图用 large（原图太大）
    assert first["thumb_url"].endswith("preview/abc.jpg")
    assert first["source_url"] == "https://danbooru.donmai.us/posts/987"
    assert first["tags"] == ["1girl", "solo", "blue_sky"]
    assert first["author"] == "some artist"
    assert first["provider"] == "danbooru"
    # 详情/编辑面板字段（分组标签 + 元数据）
    assert first["post_id"] == 987
    assert first["tags_meta"] == ["highres"]
    assert (first["width"], first["height"]) == (1445, 1197)
    assert first["created_at"].startswith("2026-10-15")
    assert first["fav_count"] == 7


def test_danbooru_auth_requires_login_key_pairing(monkeypatch):
    """danbooru 鉴权＝用户名 + API Key 配对；只填 Key 不附加（半个凭证不算鉴权）。"""
    h = _stub_http_capture(monkeypatch, _FakeResp(200, []))
    material_sources.search_danbooru("x", api_key="k", login="")
    assert "api_key" not in h["client"].last_params
    assert "login" not in h["client"].last_params

    h = _stub_http_capture(monkeypatch, _FakeResp(200, []))
    material_sources.search_danbooru("x", api_key="k", login="u")
    assert h["client"].last_params["login"] == "u"
    assert h["client"].last_params["api_key"] == "k"


def test_danbooru_auth_failure_readable(monkeypatch):
    _stub_http(monkeypatch, _FakeResp(401, {}))
    r = material_sources.search_danbooru("x", api_key="bad", login="u", limit=5)
    assert r["ok"] is False
    assert "用户名" in r["error"]


def test_danbooru_registers_controlled_download_candidates(monkeypatch):
    _stub_http(monkeypatch, _FakeResp(200, [_DB_POST]))

    material_sources.search_danbooru("blue_sky", limit=5)

    meta = web_material_candidates.candidate_meta(
        "https://cdn.donmai.us/original/aa/bb/abc.jpg")
    assert meta  # 已登记（= /web-materials/save 会放行）
    assert meta["provider"] == "danbooru"
    assert meta["tags"] == ["1girl", "solo", "blue_sky"]  # 原站标签随候选保留
    assert meta["author"] == "some artist"


def test_danbooru_http_error_readable(monkeypatch):
    _stub_http(monkeypatch, _FakeResp(400, {}))
    r = material_sources.search_danbooru("bad-query", limit=5)
    assert r["ok"] is False
    assert "400" in r["error"]


def test_danbooru_network_error_readable(monkeypatch):
    def boom(proxy_url=""):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(material_sources, "_client", boom)
    # 屏蔽 curl 主路径，直测 httpx 兜底的网络错误可读性（否则真跑 curl，danbooru 可达时返回 ok=True 误报）
    monkeypatch.setattr(material_sources.shutil, "which", lambda name: None)  # noqa: ARG005
    r = material_sources.search_danbooru("x", limit=1)
    assert r["ok"] is False
    assert "代理" in r["error"] or "网络" in r["error"]


def test_pixiv_without_token_gives_guidance():
    r = material_sources.search_pixiv("初音ミク", refresh_token="")
    assert r["ok"] is False
    assert "refresh token" in r["error"].lower()
    assert "无需另填用户名" in r["error"]  # pixiv 是 OAuth 令牌，与 danbooru 的配对鉴权不同


def test_pixiv_maps_via_sdk_and_registers(monkeypatch):
    illust = SimpleNamespace(
        id=555, title="イラスト", x_restrict=0,
        image_urls=SimpleNamespace(large="https://i.pximg.net/l.jpg",
                                   medium="https://i.pximg.net/m.jpg",
                                   square_medium="https://i.pximg.net/s.jpg"),
        tags=[SimpleNamespace(name="初音ミク"), SimpleNamespace(name="1girl")],
        user=SimpleNamespace(name="artist_a"),
    )

    class _FakeAPI:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def search_illust(self, word, **kwargs):  # noqa: ARG002
            return SimpleNamespace(illusts=[illust])

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)

    r = material_sources.search_pixiv("初音ミク", refresh_token="tok")

    assert r["ok"] is True
    item = r["items"][0]
    assert item["full_url"] == "https://i.pximg.net/l.jpg"
    assert item["source_url"] == "https://www.pixiv.net/artworks/555"
    assert item["tags"] == ["初音ミク", "1girl"]
    assert item["author"] == "artist_a"
    assert web_material_candidates.is_candidate("https://i.pximg.net/l.jpg")


def test_search_dispatches_and_rejects_unknown():
    assert material_sources.search("nope", "x")["ok"] is False
    assert "未知素材源" in material_sources.search("nope", "x")["error"]


# ===== 端点契约（直测路由函数） =====

def test_route_reads_credentials_from_settings(monkeypatch):
    calls = {}

    def fake_search(source, query, limit, **kw):
        calls.update({"source": source, "query": query, "limit": limit, **kw})
        return {"ok": True, "items": [], "error": "", "provider": source}

    monkeypatch.setattr(material_sources, "search", fake_search)
    monkeypatch.setattr("app.services.repo_meta.setting_str_from_state",
                        lambda key: {"pixivRefreshToken": "tok-1",
                                     "danbooruLogin": "user-1",
                                     "danbooruApiKey": "key-1"}.get(key, ""))

    got = comfyui_router.web_material_search(comfyui_router.WebMaterialSearchRequest(
        source="pixiv", query="初音ミク", limit=9, proxy="http://p:1"))

    assert got["ok"] is True
    assert calls["source"] == "pixiv"
    assert calls["pixiv_token"] == "tok-1"
    assert calls["danbooru_key"] == "key-1"
    assert calls["danbooru_login"] == "user-1"   # 用户名与 Key 一并透传（配对鉴权）
    assert calls["proxy"] == "http://p:1"
    assert calls["limit"] == 9


def test_danbooru_page_param_passed_through(monkeypatch):
    h = _stub_http_capture(monkeypatch, _FakeResp(200, [_DB_POST]))
    material_sources.search_danbooru("x", page=3)
    assert h["client"].last_params["page"] == 3
    # 越界页码被钳到 danbooru 允许范围 1~1000
    h = _stub_http_capture(monkeypatch, _FakeResp(200, [_DB_POST]))
    material_sources.search_danbooru("x", page=99999)
    assert h["client"].last_params["page"] == 1000


def test_route_proxy_falls_back_to_settings(monkeypatch):
    """前端传空代理时后端兜底读设置（2026-10-16 实锤：前端探测值为空 → 直连超时）。"""
    calls: dict = {}

    def fake_search(source, query, limit, **kw):
        calls.update(kw)
        return {"ok": True, "items": [], "error": "", "provider": source}

    monkeypatch.setattr(material_sources, "search", fake_search)
    monkeypatch.setattr("app.services.repo_meta.setting_str_from_state",
                        lambda key: {"systemProxyUrl": "http://127.0.0.1:7897",
                                     "proxyUrl": "http://127.0.0.1:9999"}.get(key, ""))

    comfyui_router.web_material_search(comfyui_router.WebMaterialSearchRequest(
        source="danbooru", query="x", proxy=""))

    assert calls["proxy"] == "http://127.0.0.1:7897"  # systemProxyUrl 优先于 proxyUrl


def test_search_allows_empty_query(monkeypatch):
    """空 query 合法（danbooru 默认按最新返回＝时间序浏览，2026-10-16 用户口径）。"""
    h = _stub_http_capture(monkeypatch, _FakeResp(200, [_DB_POST]))
    r = material_sources.search_danbooru("")
    assert h["client"].last_params["tags"] == ""
    assert r["ok"] is True


def test_route_allows_empty_query(monkeypatch):
    """端点不再对空 query 回 400（改为允许默认浏览）。"""
    got: dict = {}

    def fake_search(source, query, limit, **kw):  # noqa: ARG001
        got["query"] = query
        return {"ok": True, "items": [], "error": "", "provider": source}

    monkeypatch.setattr(material_sources, "search", fake_search)
    monkeypatch.setattr("app.services.repo_meta.setting_str_from_state", lambda key: "")
    comfyui_router.web_material_search(comfyui_router.WebMaterialSearchRequest(
        source="danbooru", query="   "))
    assert got["query"] == "   "  # 不抛 400，原样透传（服务层自行 strip）


# ===== 收藏（danbooru 账号收藏夹） =====

def test_favorite_requires_login_key_pairing():
    r = material_sources.favorite_danbooru(123, login="", api_key="k")
    assert r["ok"] is False
    assert "配对" in r["error"]
    r = material_sources.favorite_danbooru(123, login="u", api_key="")
    assert r["ok"] is False


def test_favorite_success_and_already(monkeypatch):
    h = _stub_http_capture(monkeypatch, _FakeResp(201, {}))
    r = material_sources.favorite_danbooru(123, login="u", api_key="k")
    assert r["ok"] is True and r["already"] is False
    assert h["client"].last_data == {"post_id": 123}
    assert h["client"].last_params["login"] == "u"

    _stub_http(monkeypatch, _FakeResp(409, {}))  # 已收藏 → 幂等成功
    r = material_sources.favorite_danbooru(123, login="u", api_key="k")
    assert r["ok"] is True and r["already"] is True


def test_favorite_auth_failure_readable(monkeypatch):
    _stub_http(monkeypatch, _FakeResp(403, {}))
    r = material_sources.favorite_danbooru(123, login="u", api_key="k")
    assert r["ok"] is False
    assert "鉴权" in r["error"]


# ===== 「查看大图」图片代理（SSRF 白名单） =====

def test_fetch_image_bytes_rejects_foreign_host():
    with pytest.raises(RuntimeError, match="不允许的图片主机"):
        material_sources.fetch_image_bytes("http://169.254.169.254/latest/meta-data")


def test_fetch_image_bytes_success(monkeypatch):
    _stub_http(monkeypatch, _FakeResp(200, {}, content=b"\x89PNG", headers={"content-type": "image/png"}))
    data, ctype = material_sources.fetch_image_bytes(
        "https://cdn.donmai.us/original/aa/bb/x.jpg")
    assert data == b"\x89PNG"
    assert ctype == "image/png"


def test_fetch_image_bytes_falls_back_to_direct(monkeypatch):
    """httpx 兜底路径：cdn.donmai.us 走代理不通、直连才通（2026-10-16 实测）。"""
    monkeypatch.setattr(material_sources.shutil, "which", lambda name: None)  # noqa: ARG005（屏蔽 curl 主路径）
    calls: list[str] = []

    def make(proxy_url="", timeout=None):  # noqa: ARG001
        calls.append(proxy_url)
        if proxy_url:
            raise RuntimeError("proxy dead")
        return _FakeClient(_FakeResp(200, {}, content=b"IMG", headers={"content-type": "image/jpeg"}))

    monkeypatch.setattr(material_sources, "_client", make)
    data, ctype = material_sources.fetch_image_bytes(
        "https://cdn.donmai.us/original/x.jpg", proxy="http://127.0.0.1:7897")
    assert data == b"IMG"
    assert ctype == "image/jpeg"
    assert calls == ["http://127.0.0.1:7897", ""]  # 先代理、后直连


def test_route_empty_query_no_longer_400(monkeypatch):
    """v2.4：空 query 改为合法（danbooru 默认最新）——旧的 400 契约已废弃。"""
    monkeypatch.setattr(material_sources, "search",
                        lambda s, q, l, **kw: {"ok": True, "items": [], "error": "", "provider": s})  # noqa: ARG005
    monkeypatch.setattr("app.services.repo_meta.setting_str_from_state", lambda key: "")  # noqa: ARG005
    got = comfyui_router.web_material_search(
        comfyui_router.WebMaterialSearchRequest(source="danbooru", query=""))
    assert got["ok"] is True


# ===== pixiv 信息流（recommended / ranking / bookmarks / author） =====

def test_pixiv_without_token_following():
    r = material_sources.pixiv_following(refresh_token="")
    assert r["ok"] is False
    assert "refresh token" in r["error"].lower()


def test_pixiv_following_maps_users(monkeypatch):
    prev = SimpleNamespace(
        user=SimpleNamespace(id=11, name="作者A", account="aaa",
                            profile_image_urls=SimpleNamespace(medium="https://i.pximg.net/a.jpg")),
        illusts=[],
    )

    class _FakeAPI2:
        user_id = 99

        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def user_following(self, user_id, offset=0):  # noqa: ARG002
            assert user_id == 99
            return SimpleNamespace(user_previews=[prev])

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI2
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)

    r = material_sources.pixiv_following(refresh_token="tok")
    assert r["ok"] is True
    u = r["users"][0]
    assert u["id"] == 11 and u["name"] == "作者A" and u["account"] == "aaa"
    assert u["avatar_url"] == "https://i.pximg.net/a.jpg"


def test_search_pixiv_mode_dispatch(monkeypatch):
    """mode 路由：recommended/ranking/bookmarks/author 各自调对应 SDK 方法。"""
    calls: dict = {}

    class _FakeAPI3:
        user_id = 7

        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_recommended(self, offset=0):
            calls["recommended"] = offset
            return SimpleNamespace(illusts=[])

        def illust_ranking(self, mode="day", offset=0):  # noqa: ARG002
            calls["ranking"] = (mode, offset)
            return SimpleNamespace(illusts=[])

        def user_bookmarks_illust(self, user_id, offset=0):  # noqa: ARG002
            calls["bookmarks"] = (user_id, offset)
            return SimpleNamespace(illusts=[])

        def user_illusts(self, user_id, offset=0):  # noqa: ARG002
            calls["author"] = (user_id, offset)
            return SimpleNamespace(illusts=[])

        def search_illust(self, word, **kwargs):  # noqa: ARG002
            calls["search"] = word
            return SimpleNamespace(illusts=[])

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI3
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)

    cases = [("recommended", ""), ("ranking", "week"), ("bookmarks", ""), ("author", "123")]
    for mode, param in cases:
        calls.clear()
        r = material_sources.search_pixiv("", refresh_token="t", mode=mode, mode_param=param)
        assert r["ok"] is False  # 空 illusts ⇒ 没有结果（仅验证路由）
        if mode == "recommended":
            assert "recommended" in calls
        elif mode == "ranking":
            assert calls["ranking"] == ("week", 0)
        elif mode == "bookmarks":
            assert calls["bookmarks"] == (7, 0)
        elif mode == "author":
            assert calls["author"] == ("123", 0)


# ===== pixiv 收藏（账号收藏夹 illust_bookmark_add） =====

def test_favorite_pixiv_requires_token():
    r = material_sources.favorite_pixiv(5, refresh_token="")
    assert r["ok"] is False
    assert "refresh token" in r["error"].lower()


def test_favorite_pixiv_add(monkeypatch):
    calls: dict = {}

    class _FakeAPI4:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_bookmark_add(self, illust_id):
            calls["add"] = illust_id
            return SimpleNamespace()

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI4
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)
    r = material_sources.favorite_pixiv(42, refresh_token="t")
    assert r["ok"] is True and r["already"] is False
    assert calls["add"] == 42


def test_unfavorite_pixiv_remove(monkeypatch):
    calls: dict = {}

    class _FakeAPI7:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_bookmark_delete(self, illust_id):
            calls["del"] = illust_id
            return SimpleNamespace()

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI7
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)
    r = material_sources.unfavorite_pixiv(42, refresh_token="t")
    assert r["ok"] is True and r["already"] is False
    assert calls["del"] == 42


def test_route_favorite_remove_pixiv(monkeypatch):
    calls: dict = {}

    def fake_unfav(illust_id, refresh_token="", proxy=""):  # noqa: ARG002
        calls["unfav"] = illust_id
        return {"ok": True, "already": False, "error": ""}

    def fake_fav(illust_id, refresh_token="", proxy=""):  # noqa: ARG002
        calls["fav"] = illust_id
        return {"ok": True, "already": False, "error": ""}

    monkeypatch.setattr(material_sources, "unfavorite_pixiv", fake_unfav)
    monkeypatch.setattr(material_sources, "favorite_pixiv", fake_fav)
    monkeypatch.setattr("app.services.repo_meta.setting_str_from_state", lambda key: "")
    comfyui_router.web_material_favorite(
        comfyui_router.WebMaterialFavoriteRequest(post_id=9, source="pixiv", remove=True))
    assert calls.get("unfav") == 9
    comfyui_router.web_material_favorite(
        comfyui_router.WebMaterialFavoriteRequest(post_id=8, source="pixiv", remove=False))
    assert calls.get("fav") == 8


def test_pixiv_multi_page_maps_all_pages(monkeypatch):
    """多页作品：meta_pages 全量映射成 pages + page_count（查看大图翻页 / 下载全部页）。"""

    def page(i):
        return SimpleNamespace(image_urls=SimpleNamespace(
            large=f"https://i.pximg.net/p{i}.jpg", medium=f"https://i.pximg.net/m{i}.jpg",
            square_medium=f"https://i.pximg.net/s{i}.jpg"))

    ill = SimpleNamespace(
        id=100, title="multi", x_restrict=0, width=100, height=100, page_count=3,
        image_urls=SimpleNamespace(large="https://i.pximg.net/p0.jpg",
                                   medium="https://i.pximg.net/m0.jpg",
                                   square_medium="https://i.pximg.net/s0.jpg"),
        tags=[], user=SimpleNamespace(name="a"),
        meta_pages=[page(0), page(1), page(2)],
    )

    class _FakeAPI8:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_recommended(self, offset=0):  # noqa: ARG002
            return SimpleNamespace(illusts=[ill])

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI8
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)
    r = material_sources.search_pixiv("", refresh_token="t", mode="recommended")
    it = r["items"][0]
    assert it["page_count"] == 3
    assert it["pages"] == ["https://i.pximg.net/p0.jpg", "https://i.pximg.net/p1.jpg", "https://i.pximg.net/p2.jpg"]
    assert it["full_url"] == "https://i.pximg.net/p0.jpg"   # 卡片仍用第 0 页


def test_pixiv_pages_uses_detail(monkeypatch):
    """多页补全走 illust_detail（feed 对大作品只给 page_count、meta_pages 空 ⇒ 下载会只剩封面）。"""

    class _FakeAPI9:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_detail(self, illust_id):
            assert illust_id == 777

            def page(i):
                return SimpleNamespace(image_urls=SimpleNamespace(
                    large=f"https://i.pximg.net/p{i}.jpg", medium=""))

            return SimpleNamespace(illust=SimpleNamespace(
                meta_pages=[page(0), page(1), page(2)],
                image_urls=SimpleNamespace(large="https://i.pximg.net/p0.jpg")))

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI9
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)
    r = material_sources.pixiv_pages(777, refresh_token="t")
    assert r["ok"] is True
    assert r["pages"] == ["https://i.pximg.net/p0.jpg", "https://i.pximg.net/p1.jpg", "https://i.pximg.net/p2.jpg"]
    assert r["page_count"] == 3


def test_pixiv_pages_registers_candidates(monkeypatch):
    """detail 补拉的每一页都要登记成受控下载候选——否则 save 闸只放行搜索时登记的封面，多页只存 1 张。"""

    class _FakeAPI10:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_detail(self, illust_id):  # noqa: ARG002
            def page(i):
                return SimpleNamespace(image_urls=SimpleNamespace(
                    large=f"https://i.pximg.net/p{i}.jpg", medium=""))

            return SimpleNamespace(illust=SimpleNamespace(
                meta_pages=[page(0), page(1), page(2)], tags=[], user=SimpleNamespace(name="a"),
                image_urls=SimpleNamespace(large="https://i.pximg.net/p0.jpg")))

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI10
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)
    r = material_sources.pixiv_pages(777, refresh_token="t")
    assert r["ok"] is True and len(r["pages"]) == 3
    # 每一页都应已登记为候选（/web-materials/save 的候选闸才放行）
    for u in r["pages"]:
        assert web_material_candidates.is_candidate(u), f"未登记候选：{u}"


def test_pixiv_pages_prefers_original_for_download(monkeypatch):
    """下载用原图（originals，全分辨率），查看用 large；登记候选的是原图。"""

    class _FakeAPI11:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_detail(self, illust_id):  # noqa: ARG002
            def page(i):
                return SimpleNamespace(image_urls=SimpleNamespace(
                    large=f"https://i.pximg.net/large_p{i}.jpg",
                    original=f"https://i.pximg.net/original_p{i}.jpg", medium=""))

            return SimpleNamespace(illust=SimpleNamespace(
                meta_pages=[page(0), page(1)], tags=[], user=SimpleNamespace(name="a"),
                image_urls=SimpleNamespace(large="https://i.pximg.net/large_p0.jpg")))

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI11
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)
    r = material_sources.pixiv_pages(888, refresh_token="t")
    assert r["pages"] == ["https://i.pximg.net/large_p0.jpg", "https://i.pximg.net/large_p1.jpg"]
    assert r["originals"] == ["https://i.pximg.net/original_p0.jpg", "https://i.pximg.net/original_p1.jpg"]
    # 候选登记的是原图（下载用），large 不登记
    assert web_material_candidates.is_candidate("https://i.pximg.net/original_p1.jpg")
    assert not web_material_candidates.is_candidate("https://i.pximg.net/large_p1.jpg")


# ===== pixiv feed：full_url 用原图（下载全分辨率），large_url 用 large（查看大图） =====

def test_pixiv_feed_full_url_is_original(monkeypatch):
    """搜索/推荐 feed 的 item：full_url＝原图（下载落盘全分辨率），large_url＝large（查看大图）。
    旧前端只存 full_url 也能拿到原图；查看大图仍走 large（快）。"""

    single = SimpleNamespace(
        id=1, title="s", x_restrict=0, width=1, height=1, page_count=1,
        image_urls=SimpleNamespace(large="https://i.pximg.net/s_large.jpg", original="",
                                   medium="", square_medium="https://i.pximg.net/s_thumb.jpg"),
        meta_pages=[], meta_single_page=SimpleNamespace(original_image_url="https://i.pximg.net/s_orig.png"),
        tags=[], user=SimpleNamespace(name="a"))

    def page(i):
        return SimpleNamespace(image_urls=SimpleNamespace(
            large=f"https://i.pximg.net/m{i}_large.jpg", original=f"https://i.pximg.net/m{i}_orig.png", medium=""))

    multi = SimpleNamespace(
        id=2, title="m", x_restrict=0, width=1, height=1, page_count=2,
        image_urls=SimpleNamespace(large="https://i.pximg.net/m0_large.jpg", original="",
                                   medium="", square_medium="https://i.pximg.net/m_thumb.jpg"),
        meta_pages=[page(0), page(1)], meta_single_page={},
        tags=[], user=SimpleNamespace(name="a"))

    class _FakeAPI12:
        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def illust_recommended(self, offset=0):  # noqa: ARG002
            return SimpleNamespace(illusts=[single, multi])

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI12
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)
    r = material_sources.search_pixiv("", refresh_token="t", mode="recommended")
    s, m = r["items"]
    assert s["full_url"] == "https://i.pximg.net/s_orig.png"        # 单页：原图
    assert s["large_url"] == "https://i.pximg.net/s_large.jpg"      # 查看大图：large
    assert m["full_url"] == "https://i.pximg.net/m0_orig.png"       # 多页：第0页原图
    assert m["large_url"] == "https://i.pximg.net/m0_large.jpg"


# ===== pixiv 收藏夹：max_bookmark_id 游标翻页（无 offset，2026-10-16 修） =====

def test_search_pixiv_bookmarks_no_offset_uses_cursor(monkeypatch):
    """收藏夹翻页用 max_bookmark_id 游标，绝不传 offset（真实 SDK 无 offset 参数，传了 TypeError）。"""
    calls: dict = {}

    class _FakeAPI5:
        user_id = 7

        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        # 与真实签名一致：没有 offset（多传会 TypeError）
        def user_bookmarks_illust(self, user_id, max_bookmark_id=None, **kw):  # noqa: ARG002
            calls["max_bookmark_id"] = max_bookmark_id
            return SimpleNamespace(illusts=[], next_url=None)

        @classmethod
        def parse_qs(cls, nu):  # noqa: ARG002
            return {}

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI5
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)

    r = material_sources.search_pixiv("", refresh_token="t", mode="bookmarks")
    assert calls["max_bookmark_id"] is None   # 第一页无游标、且没有 offset（否则上一行 KeyError/TypeError）
    assert r["ok"] is False                   # 空结果 → 可读错误，不崩


def test_search_pixiv_bookmarks_extracts_next_cursor(monkeypatch):
    """有下一页时从 next_url 抽 max_bookmark_id 作为 next_cursor 回传前端。"""
    class _FakeAPI6:
        user_id = 7

        def auth(self, refresh_token=None):  # noqa: ARG002
            return True

        def set_api_proxy(self, url):  # noqa: ARG002
            return None

        def user_bookmarks_illust(self, user_id, max_bookmark_id=None, **kw):  # noqa: ARG002
            ill = SimpleNamespace(
                id=555, title="t", x_restrict=0, width=100, height=100,
                image_urls=SimpleNamespace(large="https://i.pximg.net/l.jpg",
                                           medium="https://i.pximg.net/m.jpg",
                                           square_medium="https://i.pximg.net/s.jpg"),
                tags=[], user=SimpleNamespace(name="a"),
            )
            return SimpleNamespace(
                illusts=[ill],
                next_url="https://app-api.pixiv.net/v1/user/bookmarks/illust?user_id=7&max_bookmark_id=998877",
            )

        @classmethod
        def parse_qs(cls, nu):
            from urllib.parse import parse_qs, urlparse
            return {k: v[0] for k, v in parse_qs(urlparse(nu).query).items()}

    fake_mod = types.ModuleType("pixivpy3")
    fake_mod.AppPixivAPI = _FakeAPI6
    monkeypatch.setitem(sys.modules, "pixivpy3", fake_mod)

    r = material_sources.search_pixiv("", refresh_token="t", mode="bookmarks")
    assert r["ok"] is True
    assert r.get("next_cursor") == "998877"


# ===== pixiv 下载要原图：feed full_url + pixiv_pages =====

def test_save_web_series_backend(monkeypatch, tmp_path):
    """save-series：后端一次拉全页 + 逐页落盘（前端崩溃也不影响；用原图 originals）。"""
    from app.services import image_store

    monkeypatch.setattr(material_sources, "pixiv_pages", lambda iid, refresh_token="", proxy="": {  # noqa: ARG005
        "ok": True, "pages": ["p1", "p2"], "originals": ["o1", "o2"], "page_count": 2})
    calls: list = []

    def fake_save(output_dir, src, source_url="", title="", thread_id=""):  # noqa: ARG001
        calls.append((src, title))
        return {"filename": src}

    monkeypatch.setattr(image_store, "save_web_material", fake_save)
    r = image_store.save_web_series(str(tmp_path), 1, "https://www.pixiv.net/artworks/1", "T")
    assert r["ok"] is True and r["saved"] == 2 and r["failed"] == 0 and r["page_count"] == 2
    assert [c[0] for c in calls] == ["o1", "o2"]           # 用原图
    assert calls[0][1] == "T · P1/2"                        # 多页标题带页码


# ===== 防盗链 Referer 按 host 区分（pximg ≠ pixiv 子串，2026-10-17 修） =====

def test_referer_for_url_pixiv_vs_danbooru():
    # ⚠ i.pximg.net 不含子串 "pixiv"——早先 `"pixiv" in host` 判错 ⇒ pximg 吃 danbooru Referer 403
    assert material_sources._referer_for_url("https://i.pximg.net/img-master/c/360.jpg") \
        == "https://www.pixiv.net/"
    assert material_sources._referer_for_url("https://www.pixiv.net/artworks/1") \
        == "https://www.pixiv.net/"
    assert material_sources._referer_for_url("https://cdn.donmai.us/original/aa/bb/x.jpg") \
        == "https://danbooru.donmai.us/"


def test_route_favorite_dispatches_source(monkeypatch):
    calls: dict = {}

    def fake_pixiv(illust_id, refresh_token="", proxy=""):  # noqa: ARG002
        calls["pixiv"] = illust_id
        return {"ok": True, "already": False, "error": ""}

    def fake_danbooru(post_id, login="", api_key="", proxy=""):  # noqa: ARG002
        calls["danbooru"] = post_id
        return {"ok": True, "already": False, "error": ""}

    monkeypatch.setattr(material_sources, "favorite_pixiv", fake_pixiv)
    monkeypatch.setattr(material_sources, "favorite_danbooru", fake_danbooru)
    monkeypatch.setattr("app.services.repo_meta.setting_str_from_state", lambda key: "")
    comfyui_router.web_material_favorite(
        comfyui_router.WebMaterialFavoriteRequest(post_id=5, source="pixiv"))
    assert calls["pixiv"] == 5
    comfyui_router.web_material_favorite(
        comfyui_router.WebMaterialFavoriteRequest(post_id=6, source="danbooru"))
    assert calls["danbooru"] == 6
