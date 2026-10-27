"""前端 dist 静态挂载的缓存口径回归（2026-10-27 手机「界面渲染出错（已降级）」实锤）。

机制：换包后旧 `index.html` 若仍可从 WebView HTTP 缓存取到，它引用的旧 chunk 名
（`RepoViews-ClPMpCoF.js`）已经不在磁盘上 ⇒ 动态导入 404 ⇒ 掉进降级卡；而自救只做
`location.reload()`，缓存里的 index 照样端回来。根闸＝文档响应必须禁缓存。

⚠ 为什么用 mini app 注入而不是 TestClient(app)：与 test_csp_header 同一理由——
`app.mount("/")` 只在导入期 `LAF_FRONTEND_DIST` 指到含 index.html 的目录时才存在，
收集顺序一变就没这条挂载（pytest-randomly）。行为面用 mini app 挂同一个
`_DistStaticFiles`，接线面另用源码形状锁兜住。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from app.main import _DistStaticFiles, _static_cache_control

_APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-CwkPVovc.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    (tmp_path / "onboarding").mkdir()
    (tmp_path / "onboarding" / "scene.png").write_bytes(b"\x89PNG")
    app = Starlette()
    app.mount("/", _DistStaticFiles(directory=tmp_path, html=True), name="frontend")
    return TestClient(app)


def test_口径纯函数分三类():
    # 文档（含根路径与显式 index.html）：禁缓存。
    # ⚠ 入参不是 URL 而是 StaticFiles.get_path 的产物——根是 "."，Windows 分隔符是 "\"，
    # 直接按 "/" 切首段会在 Windows 上把 assets\ 判成媒体（首轮实测就是这么红的）。
    assert _static_cache_control("") == "no-store"
    assert _static_cache_control(".") == "no-store"
    assert _static_cache_control("index.html") == "no-store"
    assert _static_cache_control("guide.html") == "no-store"
    # 内容 hash 命名的构建产物：满一年 immutable（正/反斜杠同判）
    assert _static_cache_control("assets/index-CwkPVovc.js").endswith("immutable")
    assert _static_cache_control("assets\\RepoViews-Cyv11Gq1.js").endswith("immutable")
    assert "max-age=31536000" in _static_cache_control("assets/RepoViews-Cyv11Gq1.js")
    # 其余＝dist 根下的非哈希媒体：有期限，避免换包后长期拿旧字节
    assert _static_cache_control("onboarding/scene.png") == "public, max-age=3600"
    assert _static_cache_control("demiurge-icon.png") == "public, max-age=3600"


def test_文档响应禁缓存(client: TestClient):
    for path in ("/", "/index.html"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-store", path


def test_哈希资产给immutable而媒体给期限(client: TestClient):
    asset = client.get("/assets/index-CwkPVovc.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    media = client.get("/onboarding/scene.png")
    assert media.status_code == 200
    assert media.headers["cache-control"] == "public, max-age=3600"


def test_带缓存头的静态类确实挂在dist上():
    # 形态锁：`/` 的挂载必须是 _DistStaticFiles，退回裸 StaticFiles 即红——
    # 那等于把 index.html 放回启发式缓存，降级卡复发。
    source = (_APP_DIR / "main.py").read_text(encoding="utf-8")
    assert 'app.mount("/", _DistStaticFiles(' in source
    assert 'app.mount("/", StaticFiles(' not in source
