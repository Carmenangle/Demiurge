"""手机端在线同步（services/mobile_sync + /api/mobile/sync 路由）。

覆盖：未联机拒绝、并发 409、真实拉包→导入→清理全流程、包内不含密钥且本机密钥保留。
上游用本地 HTTP 服务扮演 PC（真实 zip 由 config_bundle.export_bundle 产出），不 mock
导入逻辑——「同步」的价值就在这条真实链路上。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers import mobile_remote as mobile_remote_router
from app.services import config_bundle, mobile_remote, mobile_sync, regex_store, repo_meta


def _point_data_dir(monkeypatch: pytest.MonkeyPatch, data_dir: Path, roots: dict[str, Path]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(repo_meta, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(regex_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_bundle, "PROMPT_TEMPLATES_DIR", roots["prompts"])


def _write_state(data_dir: Path, settings: dict) -> None:
    (data_dir / "user_state.json").write_text(
        json.dumps({"repos": [], "settings": settings}, ensure_ascii=False),
        encoding="utf-8")


def _settings(tmp: Path, prefix: str) -> dict:
    return {
        "theme": "dark" if prefix == "pc" else "light",
        "outputDir": str(tmp / f"{prefix}-works"),
        "characterDir": str(tmp / f"{prefix}-characters"),
        "worldbookDir": str(tmp / f"{prefix}-worldbooks"),
        "presetDir": str(tmp / f"{prefix}-presets"),
        "workflowDir": str(tmp / f"{prefix}-workflows"),
        "chatModels": [{"id": "c1", "apiKey": f"sk-{prefix}", "baseUrl": "https://api",
                        "modelName": "m1"}],
    }


@pytest.fixture()
def phone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """先把「PC」数据根打一个真包，再切成手机根，返回 (bundle, phone 状态)。"""
    dirs = {k: tmp_path / f"pc-{k}" for k in ("works", "characters", "worldbooks",
                                              "presets", "workflows", "prompts")}
    _point_data_dir(monkeypatch, tmp_path / "pc-data", dirs)
    _write_state(tmp_path / "pc-data", _settings(tmp_path, "pc"))
    (dirs["characters"] / "卡A").mkdir(parents=True, exist_ok=True)
    (dirs["characters"] / "卡A" / "card.json").write_text('{"name":"卡A"}', encoding="utf-8")
    (dirs["worldbooks"] / "世界.json").write_text('{"name":"世界"}', encoding="utf-8")
    bundle_path, _name = config_bundle.export_bundle(include_secrets=False)

    phone_dirs = {k: tmp_path / f"phone-{k}" for k in ("works", "characters", "worldbooks",
                                                      "presets", "workflows", "prompts")}
    _point_data_dir(monkeypatch, tmp_path / "phone-data", phone_dirs)
    _write_state(tmp_path / "phone-data", _settings(tmp_path, "phone"))
    monkeypatch.setattr(mobile_sync, "_job", {"state": "idle"})
    monkeypatch.setattr(mobile_remote, "load_config", lambda: {
        "enabled": True, "base_url": "http://127.0.0.1:1", "token": "t-sync-123"})
    return {"bundle": bundle_path, "data_dir": tmp_path / "phone-data", "dirs": phone_dirs}


@pytest.fixture()
def api() -> TestClient:
    app = FastAPI()
    app.include_router(mobile_remote_router.router, prefix="/api/mobile")
    return TestClient(app)


def _wait_done(timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = mobile_sync.status()
        if state.get("state") in ("done", "error"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"同步未在 {timeout}s 内结束：{mobile_sync.status()}")


class _BundleServer:
    """最小 PC：任何 GET 都回同一个 zip，并记录收到的请求（路径/令牌）。"""

    def __init__(self, payload: bytes) -> None:
        seen: list[dict] = []
        self.seen = seen

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — http.server 约定
                seen.append({"path": self.path,
                             "token": self.headers.get(mobile_remote.PC_TOKEN_HEADER, "")})
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args) -> None:  # 静音测试日志
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "_BundleServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def test_未联机时拒绝同步(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mobile_sync, "_job", {"state": "idle"})
    monkeypatch.setattr(mobile_remote, "load_config", lambda: {
        "enabled": False, "base_url": "", "token": ""})
    with pytest.raises(mobile_sync.SyncError):
        mobile_sync.start(overwrite=False)


def test_同步进行中再点返回_409(api: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mobile_sync, "_job", {"state": "downloading"})
    resp = api.post("/api/mobile/sync", json={"overwrite": False})
    assert resp.status_code == 409
    assert "进行中" in resp.json()["detail"]


def test_全流程_拉包导入本机并清理临时文件(
    phone: dict, monkeypatch: pytest.MonkeyPatch,
):
    payload = phone["bundle"].read_bytes()
    with _BundleServer(payload) as server:
        monkeypatch.setattr(mobile_remote, "load_config", lambda: {
            "enabled": True, "base_url": server.base_url, "token": "t-sync-123"})
        started = mobile_sync.start(overwrite=False)
        assert started["state"] == "downloading"
        final = _wait_done()

    assert final["state"] == "done", final
    assert final["bytes"] == len(payload)
    result = final["result"]
    assert result["secrets_included"] is False
    # 资源真的落到了手机根
    assert (phone["dirs"]["characters"] / "卡A" / "card.json").is_file()
    assert (phone["dirs"]["worldbooks"] / "世界.json").is_file()
    # 请求确实打到同步口，且带的是 PC 令牌头
    assert server.seen[0]["path"] == "/api/config/pull"
    assert server.seen[0]["token"] == "t-sync-123"
    # 临时包不留在手机上
    transfer = phone["data_dir"] / "transfer"
    assert not list(transfer.glob("online-sync-*.zip"))


def test_密钥不随包走_本机已有密钥保留(
    phone: dict, monkeypatch: pytest.MonkeyPatch,
):
    with _BundleServer(phone["bundle"].read_bytes()) as server:
        monkeypatch.setattr(mobile_remote, "load_config", lambda: {
            "enabled": True, "base_url": server.base_url, "token": "t-sync-123"})
        mobile_sync.start(overwrite=False)
        final = _wait_done()
    assert final["state"] == "done", final
    state = json.loads((phone["data_dir"] / "user_state.json").read_text(encoding="utf-8"))
    key = state["settings"]["chatModels"][0]["apiKey"]
    # 手机自己的密钥保持不变（包内无密钥 ≠ 清空）
    assert key == "sk-phone"


def test_包太大时按可用空间提前拒绝(
    phone: dict, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(mobile_sync, "_SPACE_MARGIN", 1 << 62)
    with _BundleServer(phone["bundle"].read_bytes()) as server:
        monkeypatch.setattr(mobile_remote, "load_config", lambda: {
            "enabled": True, "base_url": server.base_url, "token": "t-sync-123"})
        mobile_sync.start(overwrite=False)
        final = _wait_done()
    assert final["state"] == "error"
    assert "存储不足" in final["error"]
