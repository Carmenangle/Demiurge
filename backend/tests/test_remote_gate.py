"""L2 联机同库：远程能力闸回归（services/remote_gate + main 中间接线 + save-local 写闸）。

覆盖三件事：① 裸远端互锁（无令牌的 LAF_ALLOW_REMOTE 一律 403）；② DENY 路由类与
SCOPE 路径域（回环客户端逐字不变）；③ save-local 写路径闸（路由体内，PC 本机 no-op）。
令牌中间件本身的鉴权行为归 test_mobile_local_auth，这里只测闸的判定。
"""
from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from app.main import app
import app.main as main_module
from app.services import remote_gate, repo_meta

_LOOP = ("127.0.0.1", 51000)
_REMOTE = ("192.168.1.5", 51000)


@pytest.fixture()
def remote_open(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """远端可达 + 令牌启用（闸判定用 env_token 现读，无需真装中间件）。"""
    monkeypatch.setenv("LAF_LOCAL_TOKEN", "t-l2")
    monkeypatch.setattr(main_module, "_ALLOW_REMOTE", True)
    return TestClient(app, client=_REMOTE)


@pytest.fixture()
def remote_bare(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """裸远端：ALLOW_REMOTE 开、令牌缺——三件套缺一即拒。"""
    monkeypatch.delenv("LAF_LOCAL_TOKEN", raising=False)
    monkeypatch.setattr(main_module, "_ALLOW_REMOTE", True)
    return TestClient(app, client=_REMOTE)


@pytest.fixture()
def loop_open(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("LAF_LOCAL_TOKEN", raising=False)
    monkeypatch.setattr(main_module, "_ALLOW_REMOTE", True)
    return TestClient(app, client=_LOOP)


# ── 纯判定 ──

def test_回环对端不算远程():
    assert remote_gate.is_remote("127.0.0.1") is False
    assert remote_gate.is_remote("localhost") is False
    assert remote_gate.is_remote("192.168.1.5") is True
    assert remote_gate.is_remote("") is True
    # 注：IPv6 回环 "::1" 经 request_guard._host_of（按冒号切端口）会被判成空主机 ⇒
    # 非 回环 ⇒ 远端。这与 inbound_verdict 的既有语义一致（保守拒绝），闸不另开口子。


def test_路径域_数据根与作品根内放行_外部拒绝(monkeypatch: pytest.MonkeyPatch, tmp_path):
    data_dir = tmp_path / "data"
    works = tmp_path / "works"
    data_dir.mkdir()
    works.mkdir()
    (data_dir / "thumbs").mkdir()
    monkeypatch.setattr(remote_gate.config, "DATA_DIR", data_dir)
    # 作品根按 user_state.json 的**真实结构**注入：它在 settings.outputDir 下。
    # （早先夹具伪造顶层 {"outputDir": …}，让「作品根永远进不了允许根」这个缺陷测成了绿。）
    monkeypatch.setattr(repo_meta, "DATA_DIR", data_dir)
    (data_dir / "user_state.json").write_text(
        json.dumps({"settings": {"outputDir": str(works)}}), encoding="utf-8")

    def in_scope(path: str) -> bool:
        return remote_gate.path_in_scope(path)

    assert in_scope(str(data_dir / "thumbs" / "a.png"))
    assert in_scope(str(works / "repo1" / "cover.png"))
    assert in_scope(str(data_dir)) is True or in_scope(str(data_dir))  # 根自身
    assert in_scope(str(tmp_path / "outside" / "secret.env")) is False
    assert in_scope("C:\\Windows\\system32\\config") is False
    assert in_scope("") is False


def test_判定函数_互锁优先于路由类():
    assert remote_gate.verdict("/api/health", None, token_enabled=False) == (
        remote_gate.INTERLOCK_DETAIL)
    # 有令牌：普通端点放行、DENY 拒、SCOPE 外部拒
    assert remote_gate.verdict("/api/health", None, token_enabled=True) is None
    assert "PC 独占" in remote_gate.verdict("/api/comfyui/start", None, token_enabled=True)
    # ComfyUI 进程配置与主机簿：保存地址＝顺带放宽 SSRF 白名单，读簿＝侦察哪些主机被放宽过
    # （属主 services/comfy_hosts，合同 ARCHITECTURE 条 71）
    for path in ("/api/comfyui/config", "/api/comfyui/hosts"):
        assert "PC 独占" in remote_gate.verdict(path, None, token_enabled=True), path
    assert "path scope" in remote_gate.verdict(
        "/api/comfyui/local-view", "/etc/passwd", token_enabled=True)


def test_判定函数_在线同步拉包精确放行_导出仍拒():
    # 在线同步：/api/config/pull 是 DENY 前缀里的唯一例外（路由体内恒不含密钥）
    assert remote_gate.verdict("/api/config/pull", None, token_enabled=True) is None
    # 精确匹配：近似路径、导出/导入、带斜杠的变体都不放行
    for path in ("/api/config/export", "/api/config/import", "/api/config/pull/x",
                 "/api/config/pullz", "/api/config"):
        assert "PC 独占" in remote_gate.verdict(path, None, token_enabled=True), path
    # 互锁仍优先：裸远端拿到一样是 403
    assert remote_gate.verdict("/api/config/pull", None, token_enabled=False) == (
        remote_gate.INTERLOCK_DETAIL)


def test_写闸_回环no_op_远程域外抛错():
    remote_gate.ensure_write_scope("127.0.0.1", "C:/anywhere")  # 回环：不做任何事
    with pytest.raises(ValueError):
        remote_gate.ensure_write_scope("10.1.1.5", "C:/Windows/evil")


def test_写闸_作品根取自settings_outputDir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """回归：作品根在 user_state.json 的 settings.outputDir 下。

    旧实现读顶层 `state.get("outputDir")`（错层级）⇒ 作品根永远不在允许根，
    远程 save-local 写作品根一律 403（E2E A7 实测命中）。顶层假键也不得顶替正主。
    """
    data_dir = tmp_path / "data"
    works = tmp_path / "works"
    stray = tmp_path / "stray"
    for d in (data_dir, works, stray):
        d.mkdir()
    monkeypatch.setattr(remote_gate.config, "DATA_DIR", data_dir)
    monkeypatch.setattr(repo_meta, "DATA_DIR", data_dir)

    # 未配置：作品根不参与，只有 DATA_DIR
    assert remote_gate.path_in_scope(str(works / "a.png")) is False

    # 真实结构：settings.outputDir ⇒ 远程写放行
    (data_dir / "user_state.json").write_text(
        json.dumps({"settings": {"outputDir": str(works)}}), encoding="utf-8")
    remote_gate.ensure_write_scope("10.1.1.5", str(works / "repo1" / "x.png"))

    # 顶层 outputDir 不是属主结构，不能把 stray 拉进允许根
    (data_dir / "user_state.json").write_text(
        json.dumps({"outputDir": str(stray), "settings": {"outputDir": str(works)}}),
        encoding="utf-8")
    assert remote_gate.path_in_scope(str(stray / "a.png")) is False
    assert remote_gate.path_in_scope(str(works / "a.png")) is True


# ── 中间件接线（真实 app） ──

def test_裸远端一律拒绝(remote_bare: TestClient):
    resp = remote_bare.get("/api/health")
    assert resp.status_code == 403
    assert "LAF_LOCAL_TOKEN" in resp.json()["detail"]


def test_远端普通端点放行(remote_open: TestClient):
    resp = remote_open.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_远端进程控制与PC独占端点拒绝(remote_open: TestClient):
    for path in ("/api/comfyui/config", "/api/comfyui/hosts", "/api/comfyui/start", "/api/models"):
        resp = remote_open.get(path)
        assert resp.status_code == 403, path
        assert "PC 独占" in resp.json()["detail"], path


def test_远端在线同步拉包_过闸且恒不含密钥(remote_open: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.services import config_bundle

    zip_path = tmp_path / "bundle.zip"
    zip_path.write_bytes(b"PK\x03\x04")
    seen: dict[str, bool] = {}

    def fake_export(*, include_secrets: bool = False):
        seen["include_secrets"] = include_secrets
        return zip_path, "demiurge-config.zip"

    monkeypatch.setattr(config_bundle, "export_bundle", fake_export)
    resp = remote_open.get("/api/config/pull")
    assert resp.status_code == 200
    assert resp.content == b"PK\x03\x04"
    assert seen["include_secrets"] is False
    # 想塞 include_secrets 也无效：路由签名里没有这个参数，多传一律被忽略
    zip_path.write_bytes(b"PK\x03\x04")  # 响应结束会把包删掉（cleanup_bundle_file），重建一份
    resp = remote_open.get("/api/config/pull", params={"include_secrets": "true"})
    assert resp.status_code == 200
    assert seen["include_secrets"] is False
    # 同一前缀下的导出/导入仍然 PC 独占
    assert remote_open.get("/api/config/export").status_code == 403


def test_回环对端不受闸影响_行为逐字不变(loop_open: TestClient):
    # ALLOW_REMOTE 开着、令牌缺着——回环对端照样进得了 config（闸只对非回环生效）
    resp = loop_open.get("/api/comfyui/config")
    assert resp.status_code == 200
    assert loop_open.get("/api/health").status_code == 200


def test_远端local_view域外拒绝_域内过闸(remote_open: TestClient, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    resp = remote_open.get("/api/comfyui/local-view", params={"path": str(outside)})
    assert resp.status_code == 403
    assert "path scope" in resp.json()["detail"]
    # 域内（DATA_DIR 下，文件不存在）：闸放行，由 local-view 自身语义回 4xx
    from app import config as app_config

    resp2 = remote_open.get("/api/comfyui/local-view", params={
        "path": str(app_config.DATA_DIR / "thumbs" / "no-such.png")})
    assert resp2.status_code != 403 or "remote_gate" not in resp2.json().get("detail", "")


def test_远端save_local域外写入拒绝(remote_open: TestClient, tmp_path):
    resp = remote_open.post("/api/comfyui/save-local", json={
        "output_dir": str(tmp_path), "repo_id": "r", "src": "data:image/png;base64,iVBOR"})
    assert resp.status_code == 403
    assert "remote_gate" in resp.json()["detail"] or "作品根" in resp.json()["detail"]


def test_回环save_local不受闸影响(loop_open: TestClient, monkeypatch: pytest.MonkeyPatch):
    from app.routers import comfyui as comfyui_router

    called = {}

    def fake_save_local(output_dir, repo_id, **kwargs):
        called["output_dir"] = output_dir
        return "saved.png", {}

    monkeypatch.setattr(comfyui_router.image_store, "save_local", fake_save_local)
    resp = loop_open.post("/api/comfyui/save-local", json={
        "output_dir": "D:/anywhere", "repo_id": "r", "src": "data:image/png;base64,iVBOR"})
    assert resp.status_code == 200
    assert called["output_dir"] == "D:/anywhere"
