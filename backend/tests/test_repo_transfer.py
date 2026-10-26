"""M5 换机迁移：作品包导出/导入（services/repo_transfer + 薄路由）。

核心合同：一个 zip = 一个作品（文件夹自包含 + __transfer/meta.json 里的 repos 记录）；
导入身份证冲突换新 id；zip-slip 与非 Demiurge 包一律拒。
"""
from __future__ import annotations

import io
import json
import shutil
import zipfile

from pathlib import Path

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.services import repo_meta, repo_transfer
from app.routers import transfer as transfer_router


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    data_dir = tmp_path / "data"
    works = tmp_path / "works"
    data_dir.mkdir()
    works.mkdir()
    monkeypatch.setattr(repo_meta, "DATA_DIR", data_dir)      # user_state 落点
    monkeypatch.setattr(config, "DATA_DIR", data_dir)          # transfer 临时目录
    (data_dir / "user_state.json").write_text(json.dumps({
        "repos": [{"id": "repo-1", "name": "星海日记", "cards": ["A"]}],
        "settings": {"outputDir": str(works)},
    }, ensure_ascii=False), encoding="utf-8")
    folder = works / "星海日记"
    (folder / "chats").mkdir(parents=True)
    (folder / "cover.png").write_bytes(b"png")
    (folder / "chats" / "day1.json").write_text("{}", encoding="utf-8")
    repo_meta.write_repo_marker(folder, "repo-1")
    return {"works": works, "data_dir": data_dir, "folder": folder}


def test_导出包含文件与repos记录(workspace: dict):
    zip_path, filename = repo_transfer.export_repo("repo-1")
    assert filename == "星海日记.demirepo.zip"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert {"__transfer/meta.json", "cover.png", "chats/day1.json", "_repo.json"} <= names
        meta = json.loads(zf.read("__transfer/meta.json"))
        assert meta["app"] == "demiurge"
        assert meta["repo"]["id"] == "repo-1"
        assert meta["repo"]["name"] == "星海日记"


def test_导出未配置作品根时给固定文案(workspace: dict):
    (workspace["data_dir"] / "user_state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(repo_transfer.TransferError) as exc:
        repo_transfer.export_repo("repo-1")
    assert exc.value.args[0] == repo_meta.WORKS_ROOT_NOT_CONFIGURED_MSG


def _reset_state(workspace: dict) -> None:
    """把目标机清成「真干净」：既无 repos 记录，也无遗留作品目录。

    身份占用判定不只看 state，还会按 `_repo.json` 标记扫作品根（`find_repo_folder`）——
    同 id 的两个目录会让 `repo_folder_path` 反查歧义，所以那种情况下换新 id 是对的。
    故只清 state 不清目录时，目标机并不干净，「保留原 id」的断言前提不成立。
    """
    (workspace["data_dir"] / "user_state.json").write_text(
        json.dumps({"repos": [], "settings": {"outputDir": str(workspace["works"])}},
                   ensure_ascii=False),
        encoding="utf-8")
    shutil.rmtree(workspace["folder"], ignore_errors=True)


def test_导入往返_文件与记录与身份标记还原(workspace: dict):
    zip_path, _ = repo_transfer.export_repo("repo-1")
    _reset_state(workspace)
    result = repo_transfer.import_repo(zip_path)
    assert result["repo_id"] == "repo-1"          # 干净目标机：保留原 id
    assert result["name"] == "星海日记"
    folder = Path(result["folder"])
    assert (folder / "cover.png").read_bytes() == b"png"
    assert (folder / "chats" / "day1.json").exists()
    marker = json.loads((folder / "_repo.json").read_text(encoding="utf-8"))
    assert marker["id"] == "repo-1"
    state = repo_meta.load_user_state()
    assert state["repos"][0]["id"] == "repo-1"
    assert state["repos"][0]["cards"] == ["A"]    # repos 记录随包迁移


def test_身份冲突时换新id并防目录撞名(workspace: dict):
    zip_path, _ = repo_transfer.export_repo("repo-1")   # 原机原 id 仍在
    result = repo_transfer.import_repo(zip_path)
    assert result["repo_id"] != "repo-1"
    assert Path(result["folder"]).name.startswith("星海日记")
    ids = [r["id"] for r in repo_meta.load_user_state()["repos"]]
    assert len(ids) == len(set(ids)) == 2


def test_zip_slip一律拒绝(workspace: dict):
    evil = workspace["data_dir"] / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("__transfer/meta.json", json.dumps(
            {"app": "demiurge", "version": 1, "repo": {"id": "x", "name": "X"}}))
        zf.writestr("../escape.txt", "pwn")
    with pytest.raises(repo_transfer.TransferError, match="zip-slip"):
        repo_transfer.import_repo(evil)


def test_非Demiurge包拒绝(workspace: dict):
    fake = workspace["data_dir"] / "fake.zip"
    with zipfile.ZipFile(fake, "w") as zf:
        zf.writestr("random.txt", "hi")
    with pytest.raises(repo_transfer.TransferError, match="作品包"):
        repo_transfer.import_repo(fake)


# ── 薄路由 ──

@pytest.fixture()
def api(workspace: dict) -> TestClient:
    app = FastAPI()
    app.include_router(transfer_router.router, prefix="/api/transfer")
    return TestClient(app)


def test_路由导出返回zip附件(api: TestClient, workspace: dict):
    resp = api.get("/api/transfer/export", params={"repo_id": "repo-1"})
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "__transfer/meta.json" in zf.namelist()


def test_路由导入multipart往返(api: TestClient, workspace: dict):
    zip_path, _ = repo_transfer.export_repo("repo-1")
    _reset_state(workspace)
    resp = api.post("/api/transfer/import",
                    files={"file": ("星海日记.demirepo.zip", zip_path.read_bytes(),
                                    "application/zip")})
    assert resp.status_code == 200
    assert resp.json()["repo_id"] == "repo-1"
