"""ComfyUI 主机白名单簿（services/comfy_hosts + POST /comfyui/config 保存即登记）。

第 1 步的验收合同：设置页保存 ComfyUI 地址 = 落盘 comfy_config.json **且**把该主机登记进
url_guard 白名单（内存 + comfy_hosts.json），重启后由 lifespan 播种回来；登记失败只提示
不打断保存；主机簿是 PC 独占面（远程客户端 403，判定见 test_remote_gate）。

手机壳那条 `/api/mobile/comfy-host` 的行为归 test_comfy_host_register.py（本轮定案「先共存」：
同一文件、同一 schema、登记幂等）。
"""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers import comfyui as comfyui_router
from app.services import comfy_hosts, comfy_launcher, remote_gate, url_guard


@pytest.fixture(autouse=True)
def _restore_extra_hosts():
    """白名单是模块级可变列表：用例后恢复原状，防跨用例泄漏。"""
    saved = list(url_guard._COMFYUI_EXTRA)
    yield
    url_guard._COMFYUI_EXTRA[:] = saved


@pytest.fixture(autouse=True)
def _redirect_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """读写一律落 tmp：绝不碰真实 backend/data/（comfy_config.json 是真实安装路径配置）。

    `comfy_launcher` 在导入期就绑死了 DATA_DIR，且 conftest 的自动夹具刻意把
    `_config_path` 钉回真实文件（保 audio_merge.find_ffmpeg 的读语义）——重定向读写必须
    自己 patch `_config_path`，这是 test_comfy_launcher.py 的既有口径。
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(comfy_launcher, "DATA_DIR", tmp_path)
    monkeypatch.setattr(comfy_launcher, "_config_path", lambda: tmp_path / "comfy_config.json")


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(comfyui_router.router)
    return TestClient(app)


# ── 服务层 ──

def test_云端https主机登记后validate放行且落盘():
    assert comfy_hosts.register_from_url("https://Comfy.Example.com:443/") == "comfy.example.com"
    assert url_guard.validate_comfyui_url("https://comfy.example.com/") == "https://comfy.example.com"
    saved = json.loads((comfy_hosts._hosts_path()).read_text(encoding="utf-8"))
    assert saved == {"hosts": ["comfy.example.com"]}


def test_裸host_port形态也认():
    assert comfy_hosts.register_from_url("192.168.1.9:8188") == "192.168.1.9"
    assert comfy_hosts.load_hosts() == ["192.168.1.9"]


def test_重复登记幂等_文件不出现两次():
    comfy_hosts.register_from_url("http://10.9.9.9:8188")
    comfy_hosts.register_from_url("http://10.9.9.9:8188")
    assert comfy_hosts.load_hosts() == ["10.9.9.9"]


def test_回环不算登记_不污染主机簿():
    assert comfy_hosts.register_from_url("http://127.0.0.1:8188") == "loopback"
    assert comfy_hosts.load_hosts() == []
    assert not comfy_hosts._hosts_path().exists()


@pytest.mark.parametrize("bad", ["", "   ", "http://", "ftp://comfy.example.com/x", "not a url"])
def test_形态不合法一律抛_不写文件(bad: str):
    with pytest.raises(ValueError):
        comfy_hosts.register_from_url(bad)
    assert comfy_hosts._hosts_path().exists() is False


def test_上限已满时抛错且不追加文件():
    comfy_hosts.register_from_url("http://10.0.0.1:8188")
    for i in range(2, url_guard.MAX_EXTRA_HOSTS + 1):
        url_guard.register_comfyui_host(f"10.0.0.{i}")
    with pytest.raises(ValueError, match="上限"):
        comfy_hosts.register_from_url("http://10.9.9.9:8188")
    assert comfy_hosts.load_hosts() == ["10.0.0.1"]


def test_播种恢复内存白名单_脏条目跳过_损坏文件不抛():
    comfy_hosts.register_from_url("https://a.example.com")
    comfy_hosts.register_from_url("https://b.example.com")
    # 模拟重启：内存判定面清空，落盘仍在
    url_guard._COMFYUI_EXTRA.clear()
    assert comfy_hosts.seed_runtime() == ["a.example.com", "b.example.com"]
    assert url_guard.validate_comfyui_url("https://b.example.com") == "https://b.example.com"
    # 脏条目（非法形态）跳过，不拖垮整本簿子
    comfy_hosts._hosts_path().write_text(
        json.dumps({"hosts": ["ok.example.com", "bad host!", "10.1.2.3"]}), encoding="utf-8")
    url_guard._COMFYUI_EXTRA.clear()
    assert comfy_hosts.seed_runtime() == ["ok.example.com", "10.1.2.3"]
    # 文件损坏 / 缺失：不抛
    comfy_hosts._hosts_path().write_text("{not json", encoding="utf-8")
    assert comfy_hosts.load_hosts() == [] and comfy_hosts.seed_runtime() == []
    comfy_hosts._hosts_path().unlink()
    assert comfy_hosts.seed_runtime() == []


# ── 路由层：保存即登记 ──

def test_保存云端地址_配置与白名单双落盘(client: TestClient):
    resp = client.post("/config", json={
        "path": "D:/ComfyUI", "url": "https://cloud.example.com:8188", "python_path": ""})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registered"] == "cloud.example.com"
    assert body["register_error"] == ""
    cfg = json.loads((comfy_launcher._config_path()).read_text(encoding="utf-8"))
    assert cfg["url"] == "https://cloud.example.com:8188"
    assert comfy_hosts.load_hosts() == ["cloud.example.com"]


def test_登记失败不打断保存_原因回给前端(client: TestClient):
    for i in range(1, url_guard.MAX_EXTRA_HOSTS + 1):
        url_guard.register_comfyui_host(f"10.0.0.{i}")
    resp = client.post("/config", json={"path": "", "url": "http://10.9.9.9:8188"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registered"] == "" and "上限" in body["register_error"]
    # 配置照存——否则用户改一次地址就得重来
    assert json.loads(comfy_launcher._config_path().read_text(encoding="utf-8"))["url"] == "http://10.9.9.9:8188"


def test_保存回环地址_registered为loopback(client: TestClient):
    body = client.post("/config", json={"path": "", "url": "http://127.0.0.1:8188"}).json()
    assert body["registered"] == "loopback" and body["register_error"] == ""
    assert comfy_hosts.load_hosts() == []


def test_GET_hosts给出判定面落盘面与上限(client: TestClient):
    client.post("/config", json={"path": "", "url": "http://192.168.7.7:8188"})
    body = client.get("/hosts").json()
    assert body["hosts"] == ["192.168.7.7"]
    assert body["saved"] == ["192.168.7.7"]
    assert body["cap"] == url_guard.MAX_EXTRA_HOSTS


# ── 地址簿删条目：注销链路（A 段） ──

def test_注销主机_内存与落盘一起移除():
    comfy_hosts.register_from_url("https://a.example.com")
    comfy_hosts.register_from_url("https://b.example.com")
    assert comfy_hosts.remove_from_url("https://a.example.com/") == "a.example.com"
    assert url_guard.comfy_extra_hosts() == ("b.example.com",)
    assert comfy_hosts.load_hosts() == ["b.example.com"]


def test_注销幂等_不在簿子里也成功且不写文件():
    assert comfy_hosts.remove_from_url("https://never.example.com") == "never.example.com"
    assert comfy_hosts._hosts_path().exists() is False


def test_注销回环_不碰文件也不报错():
    assert comfy_hosts.remove_from_url("http://127.0.0.1:8188") == "127.0.0.1"
    assert comfy_hosts._hosts_path().exists() is False


def test_注销只改一层时另一层也被清掉():
    """手改过文件/环境变量也声明同一台时，两层可能不一致——删除必须两处都清。"""
    comfy_hosts.register_from_url("http://10.8.8.8:8188")
    url_guard._COMFYUI_EXTRA.clear()          # 模拟「内存面丢了、文件还在」
    assert comfy_hosts.remove_from_url("http://10.8.8.8:8188") == "10.8.8.8"
    assert comfy_hosts.load_hosts() == []


def test_注销后名额释放_可再登记一台():
    for i in range(1, url_guard.MAX_EXTRA_HOSTS + 1):
        comfy_hosts.register_from_url(f"http://10.0.0.{i}:8188")
    with pytest.raises(ValueError, match="上限"):
        comfy_hosts.register_from_url("http://10.9.9.9:8188")
    comfy_hosts.remove_from_url("http://10.0.0.1:8188")
    assert comfy_hosts.register_from_url("http://10.9.9.9:8188") == "10.9.9.9"


def test_环境变量声明的主机不接受运行期注销(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(url_guard, "_ENV_HOSTS", frozenset({"env.example.com"}))
    url_guard.register_comfyui_host("env.example.com")
    with pytest.raises(ValueError, match="环境变量"):
        comfy_hosts.remove_from_url("https://env.example.com")
    assert "env.example.com" in url_guard.comfy_extra_hosts()


def test_POST_hosts只登记不改默认地址(client: TestClient):
    """加第二台机不能把 ps1 脚本依赖的默认地址顶掉。"""
    client.post("/config", json={"path": "D:/ComfyUI", "url": "http://127.0.0.1:8188"})
    resp = client.post("/hosts", json={"url": "https://cloud.example.com"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"registered": "cloud.example.com"}
    cfg = json.loads((comfy_launcher._config_path()).read_text(encoding="utf-8"))
    assert cfg["url"] == "http://127.0.0.1:8188"
    assert comfy_hosts.load_hosts() == ["cloud.example.com"]


def test_POST_hosts形态不合法给400(client: TestClient):
    resp = client.post("/hosts", json={"url": "not a url"})
    assert resp.status_code == 400
    assert comfy_hosts._hosts_path().exists() is False


def test_DELETE_hosts幂等并清两层(client: TestClient):
    client.post("/hosts", json={"url": "http://192.168.7.7:8188"})
    first = client.delete("/hosts", params={"url": "http://192.168.7.7:8188"})
    assert first.status_code == 200 and first.json() == {"removed": "192.168.7.7"}
    assert url_guard.comfy_extra_hosts() == ()
    assert comfy_hosts.load_hosts() == []
    assert client.delete("/hosts", params={"url": "http://192.168.7.7:8188"}).status_code == 200


def test_DELETE_hosts环境变量主机给400(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(url_guard, "_ENV_HOSTS", frozenset({"env.example.com"}))
    resp = client.delete("/hosts", params={"url": "https://env.example.com"})
    assert resp.status_code == 400 and "环境变量" in resp.json()["detail"]


# ── 与手机壳共存：同一文件、同一 schema（本轮定案「壳不改成委托」的前提） ──

def _load_shell():
    import importlib.util

    path = (Path(__file__).resolve().parents[2] / "mobile" / "spike" / "android-spike"
            / "app" / "src" / "main" / "python" / "mobile_server.py")
    assert path.is_file(), f"手机壳不在预期位置：{path}"
    spec = importlib.util.spec_from_file_location("mobile_server_hosts_interop", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_壳层写的文件服务读得到并播种(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """共存的前提：两个写者共用 `LAF_DATA_DIR/comfy_hosts.json`，互不遮蔽。

    壳层读的是环境变量、服务读的是 `config.DATA_DIR`——在真机上这两条解析到同一个目录。
    """
    shell = _load_shell()
    monkeypatch.setenv("LAF_DATA_DIR", str(tmp_path))
    url_guard._COMFYUI_EXTRA.clear()
    shell._save_comfy_hosts(["192.168.1.9", "pc.tail5cfc8.ts.net"])

    assert comfy_hosts.load_hosts() == ["192.168.1.9", "pc.tail5cfc8.ts.net"]
    assert comfy_hosts.seed_runtime() == ["192.168.1.9", "pc.tail5cfc8.ts.net"]
    assert url_guard.validate_comfyui_url("http://192.168.1.9:8188")


def test_服务写的文件壳层读得到(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    shell = _load_shell()
    monkeypatch.setenv("LAF_DATA_DIR", str(tmp_path))
    comfy_hosts.register_from_url("https://cloud.example.com")
    assert shell._load_comfy_hosts() == ["cloud.example.com"]
    assert shell._seed_comfy_hosts() == ["cloud.example.com"]


# ── 安全闸：主机簿属 PC 独占面 ──

def test_远程客户端读不到主机簿():
    # 登记主机＝放宽 SSRF 白名单；这一面（读与写）不能让远端碰（见 remote_gate docstring）
    for path in ("/api/comfyui/hosts", "/api/comfyui/config"):
        assert "PC 独占" in remote_gate.verdict(path, None, token_enabled=True), path
    # 裸远端连门都进不来
    assert remote_gate.verdict("/api/comfyui/hosts", None, token_enabled=False) == (
        remote_gate.INTERLOCK_DETAIL)
