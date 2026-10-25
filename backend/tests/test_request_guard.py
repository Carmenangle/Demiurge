"""入站请求头安全闸回归（docs/security-hardening-origin-guard-and-svg-2026-10-24.md §1.3）。

覆盖三道闸的纯判定（origin_allowed / host_allowed / inbound_verdict）与真实中间件接线
（TestClient 走 app.main 全链，含跨站 multipart 的真实失败路径）。

⚠ app.main 的测试导入闸（env 预置 + worker 中性化）在 tests/conftest.py 顶部（2026-10-24
从本文件上收——本文件原是唯一导入方，CSP 头测试成为第二个导入方后按约定抽公共闸）。
任何新的 app.main 导入方直接 import 即可；需要新 LAF_* env 语义时先回闸子处对齐。
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services import request_guard

# Starlette TestClient 默认对端是 ("testclient", 50000)，过不了 client-IP 门；
# 显式伪造回环对端，让 Origin/Host 两道闸成为被测对象。
_LOOPBACK_CLIENT = ("127.0.0.1", 51000)


# ── 纯判定：origin_allowed ────────────────────────────────────────────────────
def test_origin_回环三主机与回环段任意端口放行():
    for value in (
        "http://127.0.0.1:5173",
        "http://localhost:8010",
        "http://[::1]:15173",
        "http://127.0.0.5:9",  # 回环整段，不只 127.0.0.1
    ):
        assert request_guard.origin_allowed(value), value


def test_origin_非回环与null与空拒绝():
    for value in (
        "http://evil.com",
        "https://192.168.1.5:8080",
        "http://10.0.0.1",
        "http://169.254.169.254",
        "null",  # sandboxed iframe 的 opaque origin
        "NULL",
        "",
    ):
        assert not request_guard.origin_allowed(value), value


# ── 纯判定：host_allowed ─────────────────────────────────────────────────────
def test_host_回环与testserver放行():
    for value in (
        "127.0.0.1:8010",
        "localhost",
        "[::1]:8010",  # IPv6 Host 带方括号与端口
        "127.0.0.5",
        "testserver",  # Starlette TestClient 默认
        "TESTSERVER",  # 大小写不敏感
    ):
        assert request_guard.host_allowed(value), value


def test_host_空与外部拒绝():
    for value in (
        "",
        "evil.com:8010",
        "testserver.evil.com",  # 后缀拼贴不算
        "192.168.1.5",
    ):
        assert not request_guard.host_allowed(value), value


# ── 纯判定：inbound_verdict 三道闸统一裁决 ───────────────────────────────────
def test_allow_remote时全部闸跳过():
    verdict = request_guard.inbound_verdict(
        client_host="192.168.1.5",
        origin="http://evil.com",
        host_header="evil.com",
        allow_remote=True,
    )
    assert verdict is None


def test_非回环client保持既有文案():
    verdict = request_guard.inbound_verdict(
        client_host="testclient", origin="", host_header="testserver", allow_remote=False
    )
    assert verdict == "仅允许本机访问（如需局域网访问请设 LAF_ALLOW_REMOTE=1）"


def test_回环client与缺失Origin正常放行():
    verdict = request_guard.inbound_verdict(
        client_host="127.0.0.1", origin="", host_header="127.0.0.1:8010", allow_remote=False
    )
    assert verdict is None


def test_恶意Origin拒绝():
    verdict = request_guard.inbound_verdict(
        client_host="127.0.0.1",
        origin="http://evil.com",
        host_header="127.0.0.1:8010",
        allow_remote=False,
    )
    assert verdict == "请求 Origin 非本机来源，已拒绝（跨站请求防护）"


def test_Origin为null拒绝():
    verdict = request_guard.inbound_verdict(
        client_host="127.0.0.1",
        origin="null",
        host_header="127.0.0.1:8010",
        allow_remote=False,
    )
    assert verdict is not None and "Origin" in verdict


def test_恶意Host拒绝():
    verdict = request_guard.inbound_verdict(
        client_host="127.0.0.1", origin="", host_header="evil.com:8010", allow_remote=False
    )
    assert verdict == "请求 Host 非本机，已拒绝（DNS rebinding 防护）"


# ── 中间件接线（真实 app，伪造回环对端） ─────────────────────────────────────
def test_健康检查默认流量放行():
    client = TestClient(app, client=_LOOPBACK_CLIENT)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_浏览器合法Origin放行():
    client = TestClient(app, client=_LOOPBACK_CLIENT)
    for origin in ("http://127.0.0.1:5173", "http://localhost:15173"):
        resp = client.get("/api/health", headers={"Origin": origin})
        assert resp.status_code == 200, origin


def test_跨站Origin与null被中间件拒绝():
    client = TestClient(app, client=_LOOPBACK_CLIENT)
    for origin in ("http://evil.com", "null"):
        resp = client.get("/api/health", headers={"Origin": origin})
        assert resp.status_code == 403, origin


def test_跨站Host被中间件拒绝():
    client = TestClient(app, client=_LOOPBACK_CLIENT)
    resp = client.get("/api/health", headers={"Host": "evil.com:8010"})
    assert resp.status_code == 403


def test_跨站multipart上传被拦截_真实失败路径():
    """drive-by 仍可达的端点类（FastAPI 0.140 严格 Content-Type 只护 JSON 模型端点）：
    multipart 上传端点收跨站 <form enctype=multipart>（简单请求，必带 Origin）。
    恶意 Origin 必须在 handler 前被拦，文件不落盘。"""
    client = TestClient(app, client=_LOOPBACK_CLIENT)
    resp = client.post(
        "/api/user-state/wallpapers/upload",
        files={"file": ("evil.png", b"\x89PNG fake", "image/png")},
        data={"name": "evil"},
        headers={"Origin": "http://evil.com"},
    )
    assert resp.status_code == 403


def test_跨站表单编码POST到JSON模型端点也被拦截(monkeypatch):
    """form 编码体虽被 FastAPI 严格 Content-Type 挡成 422，但那是上游版本行为；
    本闸必须在 handler 之前拦（403 而非 422），把防线自有化。"""
    calls: list[tuple[str, str, str]] = []

    def _fake_stop(url: str, path: str, python_path: str) -> dict[str, object]:
        calls.append((url, path, python_path))
        return {"stopped": []}

    monkeypatch.setattr("app.services.comfy_launcher.stop", _fake_stop)
    client = TestClient(app, client=_LOOPBACK_CLIENT)
    resp = client.post(
        "/api/comfyui/stop",
        data={"path": "C:/victim", "url": "http://127.0.0.1:8188", "python_path": ""},
        headers={"Origin": "http://evil.com"},
    )
    assert resp.status_code == 403
    assert calls == []


def test_合法Origin的JSON_POST到达业务层(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def _fake_stop(url: str, path: str, python_path: str) -> dict[str, object]:
        calls.append((url, path, python_path))
        return {"stopped": []}

    monkeypatch.setattr("app.services.comfy_launcher.stop", _fake_stop)
    client = TestClient(app, client=_LOOPBACK_CLIENT)
    resp = client.post(
        "/api/comfyui/stop",
        json={"path": "", "url": "http://127.0.0.1:8188", "python_path": ""},
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert resp.status_code == 200
    assert calls == [("http://127.0.0.1:8188", "", "")]


def test_默认TestClient地址仍被client门拦截且文案不变():
    """既有语义锁：不带 client= 的 TestClient（对端 testclient）走原来的 403 文案，
    保住「TestClient 需配 LAF_ALLOW_REMOTE=1 或伪造回环对端」这一既有生态约定。"""
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "仅允许本机访问（如需局域网访问请设 LAF_ALLOW_REMOTE=1）"
