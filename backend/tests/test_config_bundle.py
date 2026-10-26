"""离线资源同步包：可移植设置 + 素材/作品仓库/正则/上网素材/资产账本。

核心合同：设备路径/网络设置不随包走（导入端强制保留自己的值）；密钥默认省略且
省略不等于清空（本机已有密钥不被抹）；素材/正则默认不覆盖同名，作品仓库按新身份
导入且子仓库嵌回父作品；资产账本用「仓库文件夹 + 夹内相对路径」表示落点、导入端
按本机新路径重算 id；zip-slip 与伪造包一律拒。
"""
from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile

from pathlib import Path

import httpx
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.services import (
    asset_library, asset_store, collection_artifacts, config_bundle, rag_store,
    regex_store, remote_gate, repo_meta, thumbnail_store, view_urls,
)
from app.services.rag_backend import EmbedConfig
from app.routers import config_transfer


def _write_state(data_dir: Path, settings: dict, *, repos: list | None = None) -> None:
    payload: dict = {"repos": repos if repos is not None else [{"id": "r1", "name": "作品"}],
                     "settings": settings}
    (data_dir / "user_state.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_full_pc(pc: dict) -> Path:
    """给 PC 补上作品仓库（含子仓库）、全局正则与上网素材。"""
    works = pc["tmp"] / "pc-works"
    works.mkdir(exist_ok=True)
    _write_text(works / "作品甲" / "_repo.json", '{"id":"r1","name":"作品甲"}')
    _write_text(works / "作品甲" / "chat.json", '{"messages":[1]}')
    _write_text(works / "作品甲" / "docs" / "设定.md", "甲")
    _write_text(
        works / "作品甲" / "SAVE01" / "_repo.json",
        '{"id":"c1","name":"SAVE01"}')
    _write_text(works / "作品甲" / "SAVE01" / "chat.json", '{"messages":[2]}')
    _write_text(works / "作品乙" / "_repo.json", '{"id":"r2","name":"作品乙"}')
    _write_text(works / "作品乙" / "state.json", "{}")
    web = works / "_web_materials"
    web.mkdir()
    (web / "pic.png").write_bytes(b"pc-pic")
    (web / "new.png").write_bytes(b"pc-new")
    _write_text(web / ".provenance.json", json.dumps({
        "pic.png": {"source_url": "https://a/pic"},
        "new.png": {"source_url": "https://a/new"},
    }))

    state = json.loads(
        (pc["data_dir"] / "user_state.json").read_text(encoding="utf-8"))
    state["repos"] = [
        {"id": "r1", "name": "作品甲"},
        {"id": "r2", "name": "作品乙"},
        {"id": "c1", "name": "SAVE01", "parentId": "r1"},
        {"id": "no-folder", "name": "未落盘作品"},
    ]
    (pc["data_dir"] / "user_state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    (pc["data_dir"] / "regex_scripts.json").write_text(json.dumps([
        {"id": "rx1", "scriptName": "去广告", "find": "a"},
        {"id": "rx2", "scriptName": "补全", "find": "b"},
    ], ensure_ascii=False), encoding="utf-8")
    return works


def _setup_phone(
    pc: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str = "phone-full",
    repos: list[dict] | None = None,
) -> dict:
    """手机端目录 + 状态；outputDir 下已有同名作品甲（制造 id/目录冲突）。"""
    phone_data = pc["tmp"] / f"{name}-data"
    phone_data.mkdir()
    dirs = {
        key: pc["tmp"] / f"{name}-{key}"
        for key in ("works", "characters", "worldbooks", "presets", "workflows", "prompts")
    }
    for root in dirs.values():
        root.mkdir()
    (dirs["works"] / "作品甲").mkdir()
    _write_text(dirs["works"] / "作品甲" / "chat.json", '{"phone":true}')
    _write_text(dirs["works"] / "作品甲" / "_repo.json", '{"id":"r1","name":"作品甲"}')
    _write_state(phone_data, {
        "theme": "light",
        "outputDir": str(dirs["works"]),
        "characterDir": str(dirs["characters"]),
        "worldbookDir": str(dirs["worldbooks"]),
        "presetDir": str(dirs["presets"]),
        "workflowDir": str(dirs["workflows"]),
    }, repos=repos if repos is not None else [{"id": "r1", "name": "作品甲"}])
    monkeypatch.setattr(repo_meta, "DATA_DIR", phone_data)
    monkeypatch.setattr(config, "DATA_DIR", phone_data)
    monkeypatch.setattr(regex_store, "DATA_DIR", phone_data)
    monkeypatch.setattr(config_bundle, "PROMPT_TEMPLATES_DIR", dirs["prompts"])
    return {"data_dir": phone_data, "dirs": dirs}


def _read_state(data_dir: Path) -> dict:
    return json.loads((data_dir / "user_state.json").read_text(encoding="utf-8"))


@pytest.fixture()
def pc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    data_dir = tmp_path / "pc-data"
    data_dir.mkdir()
    roots = {
        key: tmp_path / f"pc-{key}"
        for key in ("characters", "worldbooks", "presets", "workflows", "prompts")
    }
    for root in roots.values():
        root.mkdir()
    monkeypatch.setattr(repo_meta, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(regex_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_bundle, "PROMPT_TEMPLATES_DIR", roots["prompts"])

    settings = {
        "theme": "dark",
        "activePresetName": "夜航",
        "outputDir": str(tmp_path / "pc-works"),
        "characterDir": str(roots["characters"]),
        "worldbookDir": str(roots["worldbooks"]),
        "presetDir": str(roots["presets"]),
        "workflowDir": str(roots["workflows"]),
        "comfyuiPath": "C:/ComfyUI",
        "comfyuiPython": "C:/ComfyUI/py.exe",
        "modelsDir": "C:/ComfyUI/models",
        "chatBgPath": "C:/bg.png",
        "comfyuiUrl": "http://127.0.0.1:8188",
        "proxyUrl": "http://127.0.0.1:7890",
        "proxyEnabled": True,
        "systemProxyUrl": "http://127.0.0.1:7890",
        "systemProxySource": "system",
        "chatModels": [
            {"id": "c1", "apiKey": "sk-pc-chat", "baseUrl": "https://api", "modelName": "m1",
             "proxyMode": "on"},
            {"id": "c2", "apiKey": "sk-new", "baseUrl": "https://api2", "modelName": "m2",
             "proxyMode": "on"},
        ],
        "imageModels": [
            {"id": "i1", "apiKey": "sk-pc-img", "baseUrl": "https://img", "modelName": "im",
             "proxyMode": "on"},
        ],
        "videoModels": [],
        "embedModel": {"mode": "remote", "apiKey": "sk-embed", "baseUrl": "", "modelName": "e"},
        "vlmModel": {"mode": "remote", "apiKey": "sk-vlm", "baseUrl": "", "modelName": "v"},
        "hfToken": "hf-pc",
        "civitaiToken": "civ-pc",
        "smitheryKey": "sm-pc",
        "pixivRefreshToken": "px-pc",
        "danbooruLogin": "dl-pc",
        "danbooruApiKey": "dk-pc",
    }
    _write_state(data_dir, settings)

    (roots["characters"] / "卡A").mkdir()
    (roots["characters"] / "卡A" / "card.json").write_text('{"name":"卡A"}', encoding="utf-8")
    (roots["worldbooks"] / "世界.json").write_text('{"name":"世界"}', encoding="utf-8")
    (roots["presets"] / "预设.json").write_text('{"name":"预设"}', encoding="utf-8")
    (roots["workflows"] / "流程.json").write_text('{"nodes":[]}', encoding="utf-8")
    (roots["prompts"] / "abcdef12.json").write_text('{"kind":"anima_tags"}', encoding="utf-8")
    return {"data_dir": data_dir, "roots": roots, "settings": settings, "tmp": tmp_path}


def _read_settings(data_dir: Path) -> dict:
    state = json.loads((data_dir / "user_state.json").read_text(encoding="utf-8"))
    return state["settings"]


def _bundle_settings(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read(config_bundle.SETTINGS_MEMBER))


def test_导出默认剥离设备路径网络项与密钥(pc: dict):
    zip_path, filename = config_bundle.export_bundle()
    assert filename.startswith("demiurge-config-") and filename.endswith(".demicfg.zip")
    portable = _bundle_settings(zip_path)
    for key in config_bundle.PRESERVE_KEYS:
        assert key not in portable, key
    assert portable["theme"] == "dark"
    assert portable["activePresetName"] == "夜航"
    assert "apiKey" not in portable["chatModels"][0]
    assert "apiKey" not in portable["embedModel"]
    assert "hfToken" not in portable and "civitaiToken" not in portable
    assert "smitheryKey" not in portable and "danbooruApiKey" not in portable

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        meta = json.loads(zf.read(config_bundle.BUNDLE_META))
    assert config_bundle.BUNDLE_META in names and config_bundle.SETTINGS_MEMBER in names
    assert "assets/characters/卡A/card.json" in names
    assert "assets/worldbooks/世界.json" in names
    assert "assets/presets/预设.json" in names
    assert "assets/workflows/流程.json" in names
    assert "assets/prompt_templates/abcdef12.json" in names
    assert meta["app"] == "demiurge" and meta["kind"] == "config"
    assert meta["version"] == config_bundle.FORMAT_VERSION
    assert meta["secrets_included"] is False
    assert meta["assets"]["characters"]["files"] == 1


def test_本机资源清单与各资源页列表同口径(pc: dict):
    """同步面板的计数必须和用户点进资源页看到的一致（否则「同步了没」仍然说不清）。"""
    _seed_full_pc(pc)
    (pc["roots"]["presets"] / "预设.json").write_text(
        '{"prompts": []}', encoding="utf-8")

    data = config_bundle.inventory()
    counts = data["counts"]
    assert counts["characters"] == 1
    assert counts["worldbooks"] == 1
    assert counts["presets"] == 1
    assert counts["workflows"] == 1
    assert counts["prompt_templates"] == 1
    assert counts["repos"] == 4
    assert counts["regex"] == 2
    # provenance 是来源账本，不计入上网素材
    assert counts["web_materials"] == 2

    app = FastAPI()
    app.include_router(config_transfer.router, prefix="/api/config")
    body = TestClient(app).get("/api/config/inventory")
    assert body.status_code == 200, body.text
    assert body.json()["counts"] == counts


def test_勾选密钥后原样携带(pc: dict):
    zip_path, _ = config_bundle.export_bundle(include_secrets=True)
    portable = _bundle_settings(zip_path)
    assert portable["chatModels"][0]["apiKey"] == "sk-pc-chat"
    assert portable["embedModel"]["apiKey"] == "sk-embed"
    assert portable["hfToken"] == "hf-pc"
    assert portable["civitaiToken"] == "civ-pc"


def test_导入保留手机端设备路径并合并可移植设置(pc: dict, monkeypatch: pytest.MonkeyPatch):
    zip_path, _ = config_bundle.export_bundle()

    phone_data = pc["tmp"] / "phone-data"
    phone_data.mkdir()
    phone_dirs = {key: pc["tmp"] / f"phone-{key}"
                  for key in ("works", "characters", "worldbooks", "presets", "workflows",
                              "prompts")}
    for root in phone_dirs.values():
        root.mkdir()
    phone_settings = {
        "theme": "light",
        "outputDir": str(phone_dirs["works"]),
        "characterDir": str(phone_dirs["characters"]),
        "worldbookDir": str(phone_dirs["worldbooks"]),
        "presetDir": str(phone_dirs["presets"]),
        "workflowDir": str(phone_dirs["workflows"]),
        "comfyuiUrl": "http://10.0.2.2:8188",
        "proxyUrl": "http://phone-proxy:1",
        "chatModels": [
            {"id": "c1", "apiKey": "sk-phone-chat", "baseUrl": "https://phone", "modelName": "pm"},
            {"id": "c9", "apiKey": "sk-phone-only", "baseUrl": "", "modelName": "p9"},
        ],
        "hfToken": "hf-phone",
    }
    _write_state(phone_data, phone_settings, repos=[{"id": "phone-repo", "name": "手机作品"}])
    (phone_dirs["characters"] / "卡A").mkdir()
    (phone_dirs["characters"] / "卡A" / "card.json").write_bytes(b"phone-card")

    monkeypatch.setattr(repo_meta, "DATA_DIR", phone_data)
    monkeypatch.setattr(config, "DATA_DIR", phone_data)
    monkeypatch.setattr(config_bundle, "PROMPT_TEMPLATES_DIR", phone_dirs["prompts"])
    result = config_bundle.import_bundle(zip_path)

    merged = _read_settings(phone_data)
    # 设备路径与网络项：手机端现值不被 PC 覆盖
    assert merged["outputDir"] == str(phone_dirs["works"])
    assert merged["characterDir"] == str(phone_dirs["characters"])
    assert merged["comfyuiUrl"] == "http://10.0.2.2:8188"
    assert merged["proxyUrl"] == "http://phone-proxy:1"
    # 可移植设置：PC 值覆盖手机
    assert merged["theme"] == "dark"
    assert merged["activePresetName"] == "夜航"
    # 密钥省略 ≠ 清空：同 id 模型保留手机密钥；PC 新模型进列表
    by_id = {m["id"]: m for m in merged["chatModels"]}
    assert by_id["c1"]["apiKey"] == "sk-phone-chat"
    assert by_id["c2"]["baseUrl"] == "https://api2"
    assert "apiKey" not in by_id["c2"]
    assert merged["hfToken"] == "hf-phone"
    # 顶层其他数据不丢
    state = json.loads((phone_data / "user_state.json").read_text(encoding="utf-8"))
    assert state["repos"] == [{"id": "phone-repo", "name": "手机作品"}]
    # 素材默认不覆盖同名；PC 独有素材导入手机
    assert (phone_dirs["characters"] / "卡A" / "card.json").read_bytes() == b"phone-card"
    assert result["assets"]["characters"] == {"imported": 0, "skipped": 1}
    assert result["assets"]["worldbooks"]["imported"] == 1
    assert (phone_dirs["worldbooks"] / "世界.json").is_file()


def test_导入默认不覆盖_勾选覆盖才替换(pc: dict, monkeypatch: pytest.MonkeyPatch):
    zip_path, _ = config_bundle.export_bundle()
    phone_data = pc["tmp"] / "phone-overwrite"
    phone_data.mkdir()
    # 手机端目录直接复用 PC 素材根，构造同名冲突
    _write_state(phone_data, {
        "outputDir": str(pc["roots"]["worldbooks"]),
        "characterDir": str(pc["roots"]["characters"]),
        "worldbookDir": str(pc["roots"]["worldbooks"]),
        "presetDir": str(pc["roots"]["presets"]),
        "workflowDir": str(pc["roots"]["workflows"]),
    })
    (pc["roots"]["characters"] / "卡A" / "card.json").write_bytes(b"phone-card")
    monkeypatch.setattr(repo_meta, "DATA_DIR", phone_data)
    monkeypatch.setattr(config, "DATA_DIR", phone_data)

    result = config_bundle.import_bundle(zip_path)
    assert result["assets"]["characters"] == {"imported": 0, "skipped": 1}
    assert (pc["roots"]["characters"] / "卡A" / "card.json").read_bytes() == b"phone-card"

    result = config_bundle.import_bundle(zip_path, overwrite=True)
    assert result["assets"]["characters"] == {"imported": 1, "skipped": 0}
    assert (pc["roots"]["characters"] / "卡A" / "card.json").read_text(
        encoding="utf-8").startswith('{"name":"卡A"}')


def test_导入拒绝zip_slip且不落盘(pc: dict, tmp_path: Path):
    meta = {"app": "demiurge", "kind": "config", "version": 1}
    evil = _write_zip(tmp_path / "evil.zip", {
        config_bundle.BUNDLE_META: json.dumps(meta).encode(),
        config_bundle.SETTINGS_MEMBER: b"{}",
        "assets/characters/../../evil.txt": b"x",
    })
    target = pc["roots"]["characters"]
    before = sorted(p.name for p in target.rglob("*"))
    with pytest.raises(config_bundle.ConfigBundleError, match="zip-slip"):
        config_bundle.import_bundle(evil)
    assert not (tmp_path / "evil.txt").exists()
    assert sorted(p.name for p in target.rglob("*")) == before


@pytest.mark.parametrize("files", [
    {config_bundle.SETTINGS_MEMBER: b"{}"},
    {config_bundle.BUNDLE_META: b'{"app":"other","kind":"config"}',
     config_bundle.SETTINGS_MEMBER: b"{}"},
    {config_bundle.BUNDLE_META: b'{"app":"demiurge","kind":"repo"}',
     config_bundle.SETTINGS_MEMBER: b"{}"},
    {config_bundle.BUNDLE_META: b'{"app":"demiurge","kind":"config"}',
     config_bundle.SETTINGS_MEMBER: b"[]"},
    {config_bundle.BUNDLE_META: b'{"app":"demiurge","kind":"config"}',
     config_bundle.SETTINGS_MEMBER: b"{}",
     "assets/unknown/a.json": b"{}"},
])
def test_拒绝伪造包(pc: dict, tmp_path: Path, files: dict[str, bytes]):
    bad = _write_zip(tmp_path / "bad.zip", files)
    with pytest.raises(config_bundle.ConfigBundleError):
        config_bundle.import_bundle(bad)


def test_路由导出与导入(pc: dict):
    app = FastAPI()
    app.include_router(config_transfer.router, prefix="/api/config")
    client = TestClient(app)

    response = client.get("/api/config/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "demiurge-config-" in response.headers["content-disposition"]

    response = client.post(
        "/api/config/import",
        files={"file": ("cfg.zip", io.BytesIO(response.content), "application/zip")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["secrets_included"] is False
    assert set(body["assets"]) == set(config_bundle.ASSET_KINDS)

    assert client.post(
        "/api/config/import",
        files={"file": ("cfg.txt", io.BytesIO(b"x"), "text/plain")},
    ).status_code == 400


def test_导入不阻塞事件循环_壳层看门狗不会打断(pc: dict, monkeypatch: pytest.MonkeyPatch):
    """含作品仓库的包要跑好几秒。同步重 IO 留在事件循环里会把 /api/health 饿死，
    而手机端壳层 supervisor 是「5s 一跳、连三跳就重启 uvicorn」⇒ 导入半途被打断，
    表现成「只有素材进来了、仓库是空的」。这条测试锁住「导入期间事件循环仍在跑」。"""
    app = FastAPI()
    app.include_router(config_transfer.router, prefix="/api/config")

    def slow_import(*_args, **_kwargs):
        time.sleep(0.3)
        return {"ok": True}

    monkeypatch.setattr(config_bundle, "import_bundle", slow_import)

    async def run() -> tuple[int, int]:
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/config/import",
                    files={"file": ("bundle.zip", b"x", "application/zip")},
                )
        finally:
            beat.cancel()
        return response.status_code, ticks

    status, ticks = asyncio.run(run())
    assert status == 200
    # 0.3s / 0.01s ≈ 30 跳；阻塞在事件循环上的写法只能给出 0~2 跳
    assert ticks >= 10


def test_远端不能导出配置包(pc: dict):
    assert remote_gate.verdict("/api/config/export", None, token_enabled=True) is not None
    assert remote_gate.verdict("/api/config/import", None, token_enabled=False) is not None
    assert remote_gate.verdict(
        "/api/config/export/save", None, token_enabled=True) is not None
    assert remote_gate.verdict("/api/config/reveal", None, token_enabled=True) is not None


def test_导出包含仓库正则与上网素材_父仓库不重复包子仓库(pc: dict):
    _seed_full_pc(pc)
    zip_path, _ = config_bundle.export_bundle()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        meta = json.loads(zf.read(config_bundle.BUNDLE_META))
        items = json.loads(zf.read(config_bundle.REPOS_MEMBER))["items"]
        scripts = json.loads(zf.read(config_bundle.REGEX_MEMBER))

    by_folder = {item["folder"]: item for item in items}
    assert set(by_folder) == {"作品甲", "作品甲/SAVE01", "作品乙"}
    parent = by_folder["作品甲"]["index"]
    child = by_folder["作品甲/SAVE01"]["index"]
    assert f"repos/{parent}/chat.json" in names
    assert f"repos/{parent}/docs/设定.md" in names
    # 子仓库内容只出现在自己的分组，父仓库分组不重复打包
    assert f"repos/{parent}/SAVE01/chat.json" not in names
    assert f"repos/{child}/chat.json" in names
    assert f"repos/{by_folder['作品乙']['index']}/state.json" in names
    assert "web_materials/pic.png" in names
    assert "web_materials/.provenance.json" in names
    assert "regex.json" in names
    assert [script["id"] for script in scripts] == ["rx1", "rx2"]
    assert meta["version"] == config_bundle.FORMAT_VERSION
    assert meta["repos"] == {"count": 3, "files": 7, "bytes": meta["repos"]["bytes"]}
    assert meta["regex"]["count"] == 2
    assert meta["web_materials"]["files"] == 3


def test_导入仓库_子仓库嵌到父作品且冲突换新身份(
    pc: dict, monkeypatch: pytest.MonkeyPatch,
):
    _seed_full_pc(pc)
    zip_path, _ = config_bundle.export_bundle()
    phone = _setup_phone(pc, monkeypatch)
    result = config_bundle.import_bundle(zip_path)
    works = phone["dirs"]["works"]

    assert result["repos"]["imported"] == 3
    items = {item["name"]: item for item in result["repos"]["items"]}
    assert set(items) == {"作品甲", "作品乙", "SAVE01"}
    parent = items["作品甲"]
    child = items["SAVE01"]
    assert parent["repo_id"] != "r1"
    assert Path(parent["folder"]) == works / "作品甲-2"
    assert Path(child["folder"]) == works / "作品甲-2" / "SAVE01"
    assert (works / "作品甲-2" / "chat.json").read_text(encoding="utf-8") == '{"messages":[1]}'
    assert (works / "作品甲-2" / "SAVE01" / "chat.json").read_text(
        encoding="utf-8") == '{"messages":[2]}'
    assert json.loads(
        (works / "作品甲-2" / "SAVE01" / "_repo.json").read_text(encoding="utf-8")
    )["id"] == child["repo_id"]

    state = _read_state(phone["data_dir"])
    assert any(rec.get("id") == "r1" and rec.get("name") == "作品甲"
               for rec in state["repos"])
    children = [rec for rec in state["repos"] if rec.get("name") == "SAVE01"]
    assert children[0]["parentId"] == parent["repo_id"]
    # 本机原有作品与原文件夹不动
    assert (works / "作品甲" / "chat.json").read_text(encoding="utf-8") == '{"phone":true}'


def test_导入正则与上网素材_默认跳过同名(
    pc: dict, monkeypatch: pytest.MonkeyPatch,
):
    _seed_full_pc(pc)
    zip_path, _ = config_bundle.export_bundle()
    phone = _setup_phone(pc, monkeypatch)
    works = phone["dirs"]["works"]
    (works / "_web_materials").mkdir()
    (works / "_web_materials" / "pic.png").write_bytes(b"phone-pic")
    (works / "_web_materials" / ".provenance.json").write_text(
        json.dumps({"pic.png": {"source_url": "https://phone/pic"}}), encoding="utf-8")
    (phone["data_dir"] / "regex_scripts.json").write_text(json.dumps([
        {"id": "rx1", "scriptName": "去广告", "find": "phone"},
        {"id": "rx9", "scriptName": "本机", "find": "keep"},
    ], ensure_ascii=False), encoding="utf-8")

    result = config_bundle.import_bundle(zip_path)
    assert result["regex"] == {"imported": 1, "skipped": 1}
    assert result["web_materials"] == {"imported": 1, "skipped": 1}

    scripts = json.loads(
        (phone["data_dir"] / "regex_scripts.json").read_text(encoding="utf-8"))
    by_id = {script["id"]: script for script in scripts}
    assert by_id["rx1"]["find"] == "phone"
    assert by_id["rx2"]["find"] == "b"
    assert by_id["rx9"]["find"] == "keep"

    assert (works / "_web_materials" / "pic.png").read_bytes() == b"phone-pic"
    assert (works / "_web_materials" / "new.png").read_bytes() == b"pc-new"
    provenance = json.loads(
        (works / "_web_materials" / ".provenance.json").read_text(encoding="utf-8"))
    assert provenance["pic.png"]["source_url"] == "https://phone/pic"
    assert provenance["new.png"]["source_url"] == "https://a/new"


def test_导入勾选覆盖才替换正则与上网素材(
    pc: dict, monkeypatch: pytest.MonkeyPatch,
):
    _seed_full_pc(pc)
    zip_path, _ = config_bundle.export_bundle()
    phone = _setup_phone(pc, monkeypatch)
    works = phone["dirs"]["works"]
    (works / "_web_materials").mkdir()
    (works / "_web_materials" / "pic.png").write_bytes(b"phone-pic")
    (works / "_web_materials" / ".provenance.json").write_text(
        json.dumps({"pic.png": {"source_url": "https://phone/pic"}}), encoding="utf-8")
    (phone["data_dir"] / "regex_scripts.json").write_text(json.dumps([
        {"id": "rx1", "scriptName": "去广告", "find": "phone"},
    ], ensure_ascii=False), encoding="utf-8")

    result = config_bundle.import_bundle(zip_path, overwrite=True)
    assert result["regex"] == {"imported": 2, "skipped": 0}
    assert result["web_materials"] == {"imported": 2, "skipped": 0}

    scripts = json.loads(
        (phone["data_dir"] / "regex_scripts.json").read_text(encoding="utf-8"))
    assert {script["id"]: script["find"] for script in scripts}["rx1"] == "a"
    assert (works / "_web_materials" / "pic.png").read_bytes() == b"pc-pic"
    provenance = json.loads(
        (works / "_web_materials" / ".provenance.json").read_text(encoding="utf-8"))
    assert provenance["pic.png"]["source_url"] == "https://a/pic"


def test_拒绝仓库路径逃逸与上网素材zip_slip(pc: dict, tmp_path: Path):
    meta = {"app": "demiurge", "kind": "config", "version": 2}
    bad_repo = _write_zip(tmp_path / "bad-repo.zip", {
        config_bundle.BUNDLE_META: json.dumps(meta).encode(),
        config_bundle.SETTINGS_MEMBER: b"{}",
        config_bundle.REPOS_MEMBER: json.dumps({"items": [{
            "index": 0, "record": {"id": "r1", "name": "逃逸"}, "folder": "../evil",
        }]}).encode(),
        "repos/0/a.txt": b"x",
    })
    with pytest.raises(config_bundle.ConfigBundleError, match="仓库路径不合法"):
        config_bundle.import_bundle(bad_repo)

    bad_web = _write_zip(tmp_path / "bad-web.zip", {
        config_bundle.BUNDLE_META: json.dumps(meta).encode(),
        config_bundle.SETTINGS_MEMBER: b"{}",
        "web_materials/../../evil.txt": b"x",
    })
    with pytest.raises(config_bundle.ConfigBundleError, match="zip-slip"):
        config_bundle.import_bundle(bad_web)
    assert not (tmp_path / "evil.txt").exists()


def test_导出保存到下载目录并可定位(pc: dict, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch):
    _seed_full_pc(pc)
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(config_bundle, "download_base_dir", lambda: downloads)
    app = FastAPI()
    app.include_router(config_transfer.router, prefix="/api/config")
    client = TestClient(app)

    response = client.post("/api/config/export/save?include_secrets=false")
    assert response.status_code == 200, response.text
    body = response.json()
    target = Path(body["path"])
    assert target.is_file()
    assert target.parent == (downloads / "Demiurge").resolve()
    assert target.name.startswith("demiurge-config-")
    assert body["bytes"] == target.stat().st_size

    second = client.post("/api/config/export/save?include_secrets=false")
    assert second.status_code == 200
    assert second.json()["path"] != body["path"]

    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"x")
    rejected = client.post("/api/config/reveal", json={"path": str(outside)})
    assert rejected.status_code == 400

    monkeypatch.setattr(collection_artifacts, "reveal_in_folder",
                        lambda path, select=True: True)
    opened = client.post("/api/config/reveal", json={"path": body["path"]})
    assert opened.status_code == 200, opened.text
    assert opened.json() == {"ok": True}


# ===== 资产账本 assets.json（v3）：手机「生成内容」空态的收口 =====
# 包里没有资产元数据 ⇒ 设备侧既无 SQLite 主表也无向量库 ⇒ 列表页恒空。
# 这一组锁住「元数据随包走 + 导入端按本机路径重定位」的合同。

def _asset_row(repo_id: str, path: Path, *, index: int) -> dict:
    url = view_urls.local_view(str(path))
    return {
        "id": rag_store.generation_doc_id(repo_id, url),
        "repo_id": repo_id, "kind": "generation", "media_type": "image",
        "source": "anima", "image_url": url,
        "prompt": f"提示词{index}", "description": f"描述{index}",
        "tags": ["1girl", "liduke"], "template_name": "夜航", "model_name": "flux",
        "lora_names": "loraA", "created_at": 1700000000000 + index,
        "source_url": "", "source_meta": "", "derived_from": "[]",
    }


def _seed_assets(works: Path, repo_id: str, folder: str, count: int) -> list[dict]:
    """按本机真实路径写进主表（图片必须真的在盘上，否则账本无法对账落点）。"""
    rows: list[dict] = []
    for i in range(count):
        path = works / folder / "pictures" / f"{i:04d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        row = _asset_row(repo_id, path, index=i)
        asset_store.upsert_asset(row)
        rows.append(row)
    return rows


def _bundle_asset_items(zip_path: Path) -> tuple[list[dict], dict]:
    with zipfile.ZipFile(zip_path) as zf:
        items = json.loads(zf.read(config_bundle.ASSETS_MEMBER))["items"]
        meta = json.loads(zf.read(config_bundle.BUNDLE_META))
    return items, meta


def test_导出资产账本用文件夹加相对路径_设备绝对路径不进包(pc: dict):
    works = _seed_full_pc(pc)
    _seed_assets(works, "r1", "作品甲", 2)

    zip_path, _ = config_bundle.export_bundle()
    items, meta = _bundle_asset_items(zip_path)

    assert [item["rel"] for item in items] == [
        "pictures/0001.png", "pictures/0000.png"]      # created_at 倒序＝列表页顺序
    assert {item["folder"] for item in items} == {"作品甲"}
    assert {item["repo_id"] for item in items} == {"r1"}
    assert meta["asset_index"] == {"count": 2, "skipped": 0}
    # 随包走的是「写入形状」元数据：导入端拿它直接 upsert，往返不丢字段
    assert items[0]["prompt"] == "提示词1"
    assert items[0]["tags"] == ["1girl", "liduke"]
    assert items[0]["template_name"] == "夜航"
    assert items[0]["created_at"] == 1700000000001
    # 落点只用两段表示；设备绝对路径与 local-view 地址（path= 里编码了绝对路径）都不进包
    blob = json.dumps(items, ensure_ascii=False)
    assert "local-view" not in blob and "pc-works" not in blob
    assert "image_url" not in items[0] and "thumb_url" not in items[0]


def test_资产账本子仓库文件归子仓库_最长匹配(pc: dict):
    """父仓库打包时刻意跳过子仓库目录；取父夹会拼出一个包里不存在的相对路径。"""
    works = _seed_full_pc(pc)
    _seed_assets(works, "r1", "作品甲", 1)
    _seed_assets(works, "c1", "作品甲/SAVE01", 1)

    zip_path, _ = config_bundle.export_bundle()
    items, _meta = _bundle_asset_items(zip_path)

    assert {item["folder"] for item in items} == {"作品甲", "作品甲/SAVE01"}
    child = next(item for item in items if item["folder"] == "作品甲/SAVE01")
    assert child["rel"] == "pictures/0000.png"
    assert child["repo_id"] == "c1"


def test_资产账本跳过无落点记录_不影响整包(pc: dict):
    works = _seed_full_pc(pc)
    _seed_assets(works, "r1", "作品甲", 1)
    # 远程图（ComfyUI 在线产出）与原文件已删：都没有可对账的本机落点
    asset_store.upsert_asset({
        "id": "gen-remote", "repo_id": "r1",
        "image_url": view_urls.remote_view("x.png", comfyui_url="http://127.0.0.1:8188")})
    asset_store.upsert_asset({
        "id": "gen-gone", "repo_id": "r1",
        "image_url": view_urls.local_view(str(works / "作品甲" / "gone.png"))})
    # 作品库根之外（ComfyUI 目录）本包不认领
    outside = pc["tmp"] / "comfy-out" / "1.png"
    outside.parent.mkdir()
    outside.write_bytes(b"png")
    asset_store.upsert_asset({
        "id": "gen-outside", "repo_id": "r1", "image_url": view_urls.local_view(str(outside))})
    # 回收站记录不进包：`iter_export_rows` 在 SQL 层就按 deleted_at is null 过滤，
    # 根本不进账本，故不占 skipped 计数（skipped 只统计「有行但对不上落点」）。
    asset_store.upsert_asset({
        "id": "gen-trash", "repo_id": "r1",
        "image_url": view_urls.local_view(
            str(works / "作品甲" / "pictures" / "0000.png")),
        "deleted_at": 1})

    zip_path, _ = config_bundle.export_bundle()
    items, meta = _bundle_asset_items(zip_path)
    assert meta["asset_index"] == {"count": 1, "skipped": 3}
    assert "gen-trash" not in {item["id"] for item in items}
    assert [item["rel"] for item in items] == ["pictures/0000.png"]


def test_主表空时账本走向量库_与列表页同口径(pc: dict, monkeypatch: pytest.MonkeyPatch):
    """本机现状＝主表 0 行、元数据全在 Chroma（未跑 backfill）⇒ 只认主表会导出空账本。

    判定必须复用 `asset_db_empty`（列表页回退用的同一个谓词）；这里用假 Chroma 读取
    函数固定路由，不依赖真向量库。
    """
    works = _seed_full_pc(pc)
    picture = works / "作品甲" / "pictures" / "0000.png"
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b"png")
    assert asset_store.count_assets() == 0
    monkeypatch.setattr(asset_library, "asset_db_empty", lambda: True)
    monkeypatch.setattr(rag_store, "iter_export_rows",
                        lambda: [_asset_row("r1", picture, index=0)])

    zip_path, _ = config_bundle.export_bundle()
    items, meta = _bundle_asset_items(zip_path)
    assert meta["asset_index"] == {"count": 1, "skipped": 0}
    assert items[0]["rel"] == "pictures/0000.png"
    assert items[0]["prompt"] == "提示词0"


def test_导入资产账本按本机新路径重算id_列表页直接可读(
    pc: dict, monkeypatch: pytest.MonkeyPatch,
):
    works = _seed_full_pc(pc)
    _seed_assets(works, "r1", "作品甲", 2)
    zip_path, _ = config_bundle.export_bundle()
    phone = _setup_phone(pc, monkeypatch)
    before = asset_store.count_assets()

    result = config_bundle.import_bundle(zip_path)

    assert result["asset_index"]["present"] is True
    assert result["asset_index"]["imported"] == 2
    assert result["asset_index"]["skipped"] == 0
    assert result["asset_index"]["failed"] == 0
    # 作品甲与手机端同名 ⇒ 换新身份、落 作品甲-2；账本跟着新身份重定位
    parent = next(i for i in result["repos"]["items"] if i["name"] == "作品甲")
    new_repo_id = str(parent["repo_id"])
    assert Path(parent["folder"]) == phone["dirs"]["works"] / "作品甲-2"

    rows = asset_store.list_assets([new_repo_id], limit=20, offset=0, media_type="")
    assert len(rows) == 2
    for row, index in zip(rows, (1, 0)):
        assert row["repo_id"] == new_repo_id
        assert row["prompt"] == f"提示词{index}"
        assert row["tags"] == ["1girl", "liduke"]
        # id 按新 repo_id + 新 image_url 重算，否则与本机后续写/删用的 id 对不上
        assert row["id"] == rag_store.generation_doc_id(new_repo_id, row["image_url"])
        # 图片随仓库进了包，URL 在本机可解析 ⇒ 列表页点开能看到图
        resolved = thumbnail_store.local_path_of_url(str(row["image_url"]))
        assert resolved is not None and resolved.is_file()
        assert resolved.parent == phone["dirs"]["works"] / "作品甲-2" / "pictures"

    # 主表一旦有行，读路径不再回退向量库 ⇒ 「生成内容」不再恒空（#74 的正解）
    assert asset_library.asset_db_empty() is False
    page = asset_library.list_generations_page(
        new_repo_id, [new_repo_id], cfg=EmbedConfig(),
        limit=20, before_ts=0, offset=0, media_type="")
    assert [item["id"] for item in page["items"]] == [row["id"] for row in rows]
    assert page["total"] == 2
    # PC 侧那两行属别的设备路径，只作为导入前的既有数据存在，不被改写
    assert asset_store.count_assets() == before + 2


def test_v2包没有资产账本成员照常导入(
    pc: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    works = _seed_full_pc(pc)
    _seed_assets(works, "r1", "作品甲", 1)
    source, _ = config_bundle.export_bundle()
    legacy = tmp_path / "legacy.zip"
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(legacy, "w") as dst:
        meta = json.loads(src.read(config_bundle.BUNDLE_META))
        meta["version"] = 2
        dst.writestr(config_bundle.BUNDLE_META, json.dumps(meta).encode())
        for info in src.infolist():
            if info.filename in (config_bundle.BUNDLE_META, config_bundle.ASSETS_MEMBER):
                continue
            dst.writestr(info.filename, src.read(info.filename))

    _setup_phone(pc, monkeypatch)
    result = config_bundle.import_bundle(legacy)
    assert result["meta"]["version"] == 2
    assert result["asset_index"] == {
        "present": False, "imported": 0, "skipped": 0, "failed": 0}
    assert result["repos"]["imported"] == 3


def test_资产账本rel越界一律拒_即便对应仓库没进来(pc: dict, tmp_path: Path):
    """zip-slip 守卫必须在映射查找**之前**：不能因为「这条本来就 skip」而放过恶意 rel。"""
    evil = _write_zip(tmp_path / "evil-asset.zip", {
        config_bundle.BUNDLE_META: json.dumps(
            {"app": "demiurge", "kind": "config", "version": 3}).encode(),
        config_bundle.SETTINGS_MEMBER: b"{}",
        config_bundle.ASSETS_MEMBER: json.dumps({"items": [{
            "repo_id": "r1", "folder": "作品甲", "rel": "../evil.png",
            "prompt": "x", "tags": [], "created_at": 1,
        }]}).encode(),
    })
    with pytest.raises(config_bundle.ConfigBundleError, match="zip-slip"):
        config_bundle.import_bundle(evil)
    assert not (pc["tmp"] / "evil.png").exists()


def test_资产账本条目对应仓库未导入则跳过(pc: dict, tmp_path: Path):
    """没有 repos.json ⇒ 两张映射表都空：账本一条也落不了位，但不报错（素材照常同步）。"""
    bundle = _write_zip(tmp_path / "assets-only.zip", {
        config_bundle.BUNDLE_META: json.dumps(
            {"app": "demiurge", "kind": "config", "version": 3}).encode(),
        config_bundle.SETTINGS_MEMBER: b"{}",
        config_bundle.ASSETS_MEMBER: json.dumps({"items": [{
            "repo_id": "r1", "folder": "作品甲", "rel": "pictures/0000.png",
            "prompt": "x", "tags": [], "created_at": 1,
        }]}).encode(),
    })
    result = config_bundle.import_bundle(bundle)
    assert result["asset_index"] == {
        "present": True, "imported": 0, "skipped": 1, "failed": 0}


def test_资产账本单条写坏不阻断整包(pc: dict, monkeypatch: pytest.MonkeyPatch):
    """一条写坏不能毁掉整包导入——真机量级是 57 仓库 / 880 条，坏一条就回滚＝白同步。

    用假 upsert 精确制造「第二条抛错」，而不是构造一条畸形容器的记录：要锁的是
    循环的错误隔离，不是某个字段的解析分支。
    """
    works = _seed_full_pc(pc)
    _seed_assets(works, "r1", "作品甲", 2)
    zip_path, _ = config_bundle.export_bundle()
    _setup_phone(pc, monkeypatch)

    seen: list[str] = []
    real_upsert = asset_store.upsert_asset

    def flaky(row: dict) -> None:
        seen.append(str(row["prompt"]))
        if len(seen) == 2:
            raise RuntimeError("模拟单条写坏")
        real_upsert(row)

    monkeypatch.setattr(asset_store, "upsert_asset", flaky)
    result = config_bundle.import_bundle(zip_path)
    assert result["asset_index"] == {
        "present": True, "imported": 1, "skipped": 0, "failed": 1}
    assert len(seen) == 2          # 坏掉的那条没有中断循环
    assert result["repos"]["imported"] == 3
