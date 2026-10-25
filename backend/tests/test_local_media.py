import pytest

from app.services.local_media import LocalMediaError, open_local_media


def test_完整媒体返回文件元数据(tmp_path):
    path = tmp_path / "sample.png"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.partial is False
    assert media.media_type == "image/png"
    assert media.headers["Accept-Ranges"] == "bytes"
    assert media.headers["Content-Length"] == "6"
    # ⚠ 必须带 no-cache：角色 LoRA 落盘是「同名覆盖、URL 不变」，缺这条浏览器按启发式
    #   新鲜期缓存 ⇒ 重新生图后界面仍显示旧图、要手动刷新才更新（2026-09-24 用户实锤）。
    assert media.headers["Cache-Control"] == "no-cache"


def test_range读取只返回指定区间(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path), "bytes=1-3")

    assert media.partial is True
    assert media.media_type == "video/mp4"
    assert media.headers["Content-Range"] == "bytes 1-3/6"
    assert media.headers["Cache-Control"] == "no-cache"
    assert b"".join(media.iter_bytes(chunk_size=2)) == b"bcd"


@pytest.mark.parametrize(
    ("name", "media_type"),
    [("clip.m4v", "video/x-m4v"), ("clip.ogv", "video/ogg")],
)
def test_补充视频容器返回正确media_type(tmp_path, name, media_type):
    path = tmp_path / name
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.media_type == media_type
    assert media.headers["Accept-Ranges"] == "bytes"


def test_音频返回audio_media_type(tmp_path):
    path = tmp_path / "voice.wav"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.partial is False
    assert media.media_type == "audio/wav"
    assert media.headers["Accept-Ranges"] == "bytes"
    assert media.headers["Content-Length"] == "6"
    assert media.headers["Cache-Control"] == "no-cache"


def test_音频range读取(tmp_path):
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path), "bytes=2-4")

    assert media.partial is True
    assert media.media_type == "audio/mpeg"
    assert media.headers["Content-Range"] == "bytes 2-4/6"
    assert b"".join(media.iter_bytes()) == b"cde"


@pytest.mark.parametrize(
    ("name", "header", "status"),
    [("secret.txt", None, 403), ("missing.png", None, 404), ("sample.png", "bytes=5-1", 416)],
)
def test_拒绝非法本地媒体请求(tmp_path, name, header, status):
    path = tmp_path / name
    if name == "sample.png":
        path.write_bytes(b"abcdef")

    with pytest.raises(LocalMediaError) as exc_info:
        open_local_media(str(path), header)

    assert exc_info.value.status == status


def test_路由层把no_cache透传给媒体响应(tmp_path):
    """路由级锁：`Cache-Control: no-cache` 必须真正出现在 HTTP 响应头里。

    直测路由函数（仓库惯例，不起 TestClient）：完整文件走 `FileResponse`（Starlette 会
    另行补 ETag/Last-Modified，两者组合＝「每次协商、覆盖即拿新图」）。
    """
    from app.routers.comfyui import local_view

    class _FakeRequest:
        headers: dict[str, str] = {}

    path = tmp_path / "shot.png"
    path.write_bytes(b"abcdef")

    response = local_view(str(path), _FakeRequest())

    cache = (response.headers.get("cache-control")
             or response.headers.get("Cache-Control"))
    assert cache == "no-cache"


# ---------------------------------------------------------------------------
# 2026-10-24 审计改进项 2：local-view SVG 响应头加固
# （docs/security-hardening-origin-guard-and-svg-2026-10-24.md §2）
# 真实失败路径：白名单含 .svg 且远程 svg 可经 image_proxy（content-type image/* 即放行）
# 落盘 ⇒ 直接导航到内嵌 <script> 的恶意 svg ＝ 脚本在应用同源执行。可观测代理 =
# nosniff 全量在位 + svg 响应带 CSP sandbox（直航禁脚本/表单，静态图形照常渲染）。
# ---------------------------------------------------------------------------

_SVG_WITH_SCRIPT = (
    '<svg xmlns="http://www.w3.org/2000/svg">'
    "<script>alert(1)</script>"
    '<circle cx="50" cy="50" r="40" fill="red"/>'
    "</svg>"
)


def test_svg返回标准类型与安全响应头(tmp_path):
    path = tmp_path / "art.svg"
    path.write_text(_SVG_WITH_SCRIPT, encoding="utf-8")

    media = open_local_media(str(path))

    # 类型必须回标准 image/svg+xml（兜底 image/<ext> 产出非标准 image/svg 是笔误，
    # 非标准类型会让浏览器内容嗅探）。
    assert media.media_type == "image/svg+xml"
    assert media.headers["X-Content-Type-Options"] == "nosniff"
    assert media.headers["Content-Security-Policy"] == "sandbox"
    # 既有缓存合同逐字保持：no-cache ＝ 每次协商、覆盖即拿新图。
    assert media.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize("name", ["shot.png", "clip.mp4"])
def test_非svg媒体只加nosniff不加CSP(tmp_path, name):
    """nosniff 全量加（对位图/视频无副作用）；CSP sandbox 仅 svg（不过度加头）。"""
    path = tmp_path / name
    path.write_bytes(b"abcdef")

    media = open_local_media(str(path))

    assert media.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in media.headers


def test_svg_range分片安全头不漏(tmp_path):
    """partial + svg 组合：CSP/nosniff/Content-Range 三者同时在位（206 分支同源取头）。"""
    path = tmp_path / "art.svg"
    path.write_text(_SVG_WITH_SCRIPT, encoding="utf-8")

    media = open_local_media(str(path), "bytes=0-3")

    assert media.partial is True
    assert media.headers["Content-Security-Policy"] == "sandbox"
    assert media.headers["X-Content-Type-Options"] == "nosniff"
    assert media.headers["Content-Range"] == f"bytes 0-3/{path.stat().st_size}"


def test_路由层svg直航响应带CSP_sandbox与nosniff(tmp_path):
    """路由级锁（真实失败路径）：恶意 svg（内嵌 <script>）直航 local-view 时，
    CSP sandbox + nosniff 必须真正出现在 HTTP 响应头里——浏览器据此禁脚本执行。"""
    from app.routers.comfyui import local_view

    class _FakeRequest:
        headers: dict[str, str] = {}

    path = tmp_path / "evil.svg"
    path.write_text(_SVG_WITH_SCRIPT, encoding="utf-8")

    response = local_view(str(path), _FakeRequest())

    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.headers["cache-control"] == "no-cache"


def test_路由层206分片安全头不漏(tmp_path):
    """路由级锁：Range 请求走 StreamingResponse（206），安全头与 Content-Range 同源不漏。"""
    from app.routers.comfyui import local_view

    class _FakeRangeRequest:
        headers: dict[str, str] = {"Range": "bytes=0-3"}

    path = tmp_path / "evil.svg"
    path.write_text(_SVG_WITH_SCRIPT, encoding="utf-8")

    response = local_view(str(path), _FakeRangeRequest())

    assert response.status_code == 206
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-range"].startswith("bytes 0-3/")
