"""手机本机模型端点：模型字段单独留手机，非模型设置仍归电脑。

钉住四条合同：
1. 只收模型字段（ComfyUI 地址/路径等非模型设置写不进手机本机存档）；
2. 仓库、选中项与其他设置原样保留；
3. 空密钥不抹掉本机已有非空值（与 user-state 写入同一护栏）；
4. `/api/mobile/local-models` 永远不走反向代理（联机时由手机自己答）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers import mobile_remote as mobile_remote_router
from app.services import mobile_local_models, mobile_remote, repo_meta


@pytest.fixture()
def phone_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    return tmp_path


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(mobile_remote_router.router, prefix="/api/mobile")
    return TestClient(app)


def _seed(tmp: Path, payload: dict) -> None:
    (tmp / "user_state.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_没有本机存档时返回空模型(phone_data: Path):
    body = _client().get("/api/mobile/local-models").json()
    assert body["models"] == {}
    assert isinstance(body["lan_ip"], str)


def test_读本机模型只回模型字段(phone_data: Path):
    _seed(phone_data, {
        "repos": [{"id": "r1"}],
        "selection": {"repoId": "r1"},
        "settings": {
            "chatModels": [{"id": "c1", "apiKey": "k"}],
            "activeChatModelId": "c1",
            "comfyuiUrl": "http://192.0.2.9:8188",
            "theme": "dark",
        },
    })
    body = _client().get("/api/mobile/local-models").json()
    assert body["models"] == {
        "chatModels": [{"id": "c1", "apiKey": "k"}],
        "activeChatModelId": "c1",
    }


def test_写本机模型保留其余状态并忽略非模型字段(phone_data: Path):
    _seed(phone_data, {
        "repos": [{"id": "r1"}],
        "selection": {"repoId": "r1"},
        "settings": {"theme": "dark", "comfyuiUrl": "http://pc:8188"},
    })
    resp = _client().post("/api/mobile/local-models", json={"models": {
        "chatModels": [{"id": "c1", "apiKey": "phone-key"}],
        "activeChatModelId": "c1",
        "embedModel": {"mode": "remote", "baseUrl": "https://api.example.com/v1", "modelName": "e"},
        "comfyuiUrl": "http://evil:8188",
    }})
    assert resp.status_code == 200, resp.text
    saved = json.loads((phone_data / "user_state.json").read_text(encoding="utf-8"))
    assert saved["repos"] == [{"id": "r1"}]
    assert saved["selection"] == {"repoId": "r1"}
    assert saved["settings"]["theme"] == "dark"
    assert saved["settings"]["comfyuiUrl"] == "http://pc:8188"
    assert saved["settings"]["chatModels"][0]["apiKey"] == "phone-key"
    assert saved["settings"]["embedModel"]["modelName"] == "e"
    assert resp.json()["models"]["activeChatModelId"] == "c1"


def test_空密钥不抹掉本机已有密钥(phone_data: Path):
    _seed(phone_data, {"settings": {
        "chatModels": [{"id": "c1", "name": "旧", "apiKey": "stored-key"}],
        "embedModel": {"mode": "remote", "apiKey": "embed-key"},
    }})
    body = _client().post("/api/mobile/local-models", json={"models": {
        "chatModels": [{"id": "c1", "name": "新", "apiKey": ""}],
        "embedModel": {"mode": "remote", "apiKey": ""},
    }}).json()
    assert body["models"]["chatModels"][0]["name"] == "新"
    assert body["models"]["chatModels"][0]["apiKey"] == "stored-key"
    assert body["models"]["embedModel"]["apiKey"] == "embed-key"


@pytest.mark.parametrize("models,match", [
    ({"chatModels": {}}, "chatModels 必须是数组"),
    ({"embedModel": []}, "embedModel 必须是对象"),
    ({"vlmModel": "x"}, "vlmModel 必须是对象"),
    ({"activeChatModelId": 1}, "activeChatModelId 必须是字符串"),
])
def test_形状校验(phone_data: Path, models: dict, match: str):
    resp = _client().post("/api/mobile/local-models", json={"models": models})
    assert resp.status_code == 400
    assert match in resp.json()["detail"]


def test_损坏存档不炸(phone_data: Path):
    (phone_data / "user_state.json").write_text("{", encoding="utf-8")
    body = _client().get("/api/mobile/local-models").json()
    assert body["models"] == {}
    # 覆盖写后存档恢复为合法 JSON
    assert _client().post("/api/mobile/local-models", json={"models": {
        "activeImageModelId": "i1"}}).status_code == 200
    saved = json.loads((phone_data / "user_state.json").read_text(encoding="utf-8"))
    assert saved["settings"]["activeImageModelId"] == "i1"


def test_本机模型端点永不代理():
    assert mobile_remote.should_proxy_path("/api/mobile/local-models") is False


class _FakeSock:
    def __init__(self, ip: str) -> None:
        self._ip = ip

    def __enter__(self) -> "_FakeSock":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def settimeout(self, _timeout: float) -> None:
        pass

    def connect(self, _addr: tuple[str, int]) -> None:
        pass

    def getsockname(self) -> tuple[str, int]:
        return (self._ip, 40000)


def test_lan_ip取路由源地址(phone_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        mobile_local_models.socket, "socket", lambda *a, **k: _FakeSock("192.168.1.7"))
    assert mobile_local_models.lan_ip() == "192.168.1.7"


def test_lan_ip不拿回环当局域网地址(phone_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        mobile_local_models.socket, "socket", lambda *a, **k: _FakeSock("127.0.0.1"))
    assert mobile_local_models.lan_ip() == ""
