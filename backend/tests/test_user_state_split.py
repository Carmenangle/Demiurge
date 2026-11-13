"""用户态三区拆分的回归例（2026-11-02 P1）。

拆分前 `user_state.json` 一个文件同时装着**设置**（档 2，勾选才删）和**作品索引**（档 3，永不删），
于是「勾选删除用户数据」会把索引一起删掉：作品文件夹都活着，列表却空了——违反用户口径
「仓库、预设、角色卡、世界书那些产物还是不删」。现在索引单立一份 `repos.json` 落产物档。

**API 与 wire 形状刻意没动**（`UserState{repos,settings,selection}` 原样）：拆的只是落盘位置，
前端 / 手机壳 / Tauri 零改动。所以本文件同时钉住「对外读到的形状不变」这一条。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import data_layout, state_store
from app.routers import user_state
from app.services import repo_meta


@pytest.fixture()
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    state_store._BLOCKED.clear()
    yield tmp_path
    state_store._BLOCKED.clear()


def _legacy(dir_: Path, payload: dict) -> Path:
    """写一份「拆分前的单档形状」（没有 schemaVersion，repos 和 settings 同居）。"""
    path = dir_ / "user_state.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ── 路径与档位登记 ─────────────────────────────────────────────────────────────
def test_两档路径各自属主且跟随注入(data: Path):
    assert repo_meta.user_state_path() == data / "user_state.json"
    assert repo_meta.repos_path() == data / "repos.json"


def test_作品索引归产物档而设置档归设置(data: Path):
    assert data_layout.tier(data_layout.SCOPE_USERDATA, "repos.json") == data_layout.PRODUCT
    assert data_layout.tier(data_layout.SCOPE_USERDATA, "user_state.json") == data_layout.SETTINGS

    names = {name for name, _tier, _kind
             in data_layout.deletion_names(data_layout.SCOPE_USERDATA, include_settings=True)}
    assert "user_state.json" in names
    # 产物档不进删除面（勾选与否都不进）
    for checked in (True, False):
        deletion = {name for name, _t, _k
                    in data_layout.deletion_names(data_layout.SCOPE_USERDATA,
                                                  include_settings=checked)}
        assert "repos.json" not in deletion


def test_索引档的备份故意不登记(tmp_path: Path):
    """`repos.json.bak-*` 仍是产物：未登记 ⇒ unknown ⇒ 删除面白名单不碰。

    只有设置档的备份登记成档 2（`SETTINGS_BACKUP_STEMS`），别把这条当疏漏补上。
    """
    assert data_layout.tier(data_layout.SCOPE_USERDATA, "repos.json.bak-20261102-030405") \
        == data_layout.UNKNOWN
    assert data_layout.SETTINGS_BACKUP_STEMS[data_layout.SCOPE_USERDATA] == ("user_state.json",)


# ── 搬家（自举迁移）────────────────────────────────────────────────────────────
def test_旧单档搬成两档且读侧形状不变(data: Path):
    legacy = {
        "repos": [{"id": "r1", "name": "作品甲"}],
        "settings": {"theme": "dark", "outputDir": "D:/works"},
        "selection": {"repoId": "r1"},
    }
    _legacy(data, legacy)

    assert repo_meta.migrate_user_state_layout() == {"moved": 1}

    index = json.loads((data / "repos.json").read_text(encoding="utf-8"))
    assert index["repos"] == legacy["repos"]
    assert index["schemaVersion"] == state_store.SCHEMA_VERSION

    settings_doc = json.loads((data / "user_state.json").read_text(encoding="utf-8"))
    assert "repos" not in settings_doc, "设置档不该再留一份索引副本"
    assert settings_doc["settings"] == legacy["settings"]
    assert settings_doc["selection"] == legacy["selection"]

    backups = sorted(p.name for p in data.glob("user_state.json.bak-*"))
    assert len(backups) == 1, f"搬家必须留一份旧档备份：{backups}"

    # 对外形状不变：合并视图照旧一次拿到三样
    assert repo_meta.load_user_state() == legacy


def test_搬家幂等第二次什么都不做(data: Path):
    _legacy(data, {"repos": [{"id": "r1"}], "settings": {}})

    assert repo_meta.migrate_user_state_layout()["moved"] == 1
    assert repo_meta.migrate_user_state_layout() == {"moved": 0, "skipped": "index-exists"}

    assert len(list(data.glob("user_state.json.bak-*"))) == 1
    assert len(list(data.glob("repos.json.bak-*"))) == 0


def test_没有旧档或旧档没索引时不建空索引(data: Path):
    assert repo_meta.migrate_user_state_layout() == {"moved": 0, "skipped": "no-legacy-repos"}
    _legacy(data, {"settings": {"theme": "dark"}})
    assert repo_meta.migrate_user_state_layout() == {"moved": 0, "skipped": "no-legacy-repos"}
    assert not (data / "repos.json").exists()


def test_备份失败时搬家仍立索引档并保留旧档副本(data: Path, monkeypatch):
    """搬家是「先立新档再收旧档」：备份失败只让副本多留一会儿，不会让仓库列表消失。"""

    def _boom(*_a, **_k):
        raise OSError("disk is busy")

    _legacy(data, {"repos": [{"id": "r1"}], "settings": {"theme": "dark"}})
    monkeypatch.setattr(state_store, "copy2", _boom)

    result = repo_meta.migrate_user_state_layout()
    assert result == {"moved": 1, "skipped": "backup-failed-keeps-copy"}
    assert json.loads((data / "repos.json").read_text(encoding="utf-8"))["repos"] == [{"id": "r1"}]
    # 旧档原样没动（含凭据与索引副本）
    assert json.loads((data / "user_state.json").read_text(encoding="utf-8"))["repos"] == [{"id": "r1"}]
    assert repo_meta.load_user_state()["repos"] == [{"id": "r1"}]


# ── 读侧合并 ───────────────────────────────────────────────────────────────────
def test_合并读以索引档为准而不回退旧副本(data: Path):
    _legacy(data, {"repos": [{"id": "old"}], "settings": {"theme": "dark"}})
    state_store.save(repo_meta.repos_path(), {"repos": [{"id": "new", "name": "作品乙"}]})

    state = repo_meta.load_user_state()
    assert state["repos"] == [{"id": "new", "name": "作品乙"}]
    assert state["settings"] == {"theme": "dark"}


def test_没有索引档时沿用设置档里的旧副本(data: Path):
    """迁移没跑（或跑坏了）也不能读不到仓库列表——这是拆分的降级面。"""
    _legacy(data, {"repos": [{"id": "r1"}], "settings": {}})
    assert repo_meta.load_user_state()["repos"] == [{"id": "r1"}]


def test_空索引档不算缺席(data: Path):
    """搬完但列表确实是空的 ⇒ 不能被旧副本「复活」。"""
    _legacy(data, {"repos": [{"id": "ghost"}], "settings": {}})
    state_store.save(repo_meta.repos_path(), {"repos": []})

    state = repo_meta.load_user_state()
    assert state["repos"] == []


def test_设置档读不懂不牵连索引档(data: Path):
    """一档坏了不带走另一档：设置被备份重建时，作品索引仍读得到（产物不许跟着丢）。"""
    _legacy(data, {"settings": {"theme": "dark"}})
    (data / "user_state.json").write_text("{broken", encoding="utf-8")
    state_store.save(repo_meta.repos_path(), {"repos": [{"id": "r1"}]})

    state = repo_meta.load_user_state()
    assert state.get("settings") is None
    assert state["repos"] == [{"id": "r1"}]
    assert len(list(data.glob("user_state.json.bak-*"))) == 1


# ── 写侧分档 ───────────────────────────────────────────────────────────────────
def test_写设置档不碰作品索引(data: Path):
    state_store.save(repo_meta.repos_path(), {"repos": [{"id": "r1"}]})

    assert repo_meta.save_settings({"theme": "liquid-glass"}) is True

    assert json.loads((data / "repos.json").read_text(encoding="utf-8"))["repos"] == [{"id": "r1"}]
    settings_doc = json.loads((data / "user_state.json").read_text(encoding="utf-8"))
    assert settings_doc["settings"] == {"theme": "liquid-glass"}
    assert "repos" not in settings_doc


def test_写索引档不碰设置(data: Path):
    repo_meta.save_settings({"theme": "dark"}, selection={"repoId": "r1"})

    assert repo_meta.save_repos([{"id": "r1", "name": "作品甲"}]) is True

    index = json.loads((data / "repos.json").read_text(encoding="utf-8"))
    assert index["repos"] == [{"id": "r1", "name": "作品甲"}]
    settings_doc = json.loads((data / "user_state.json").read_text(encoding="utf-8"))
    assert settings_doc["settings"] == {"theme": "dark"}
    assert settings_doc["selection"] == {"repoId": "r1"}
    assert "repos" not in settings_doc


def test_非列表的索引写入归一成空列表(data: Path):
    """索引档只装列表：客户端给怪形状时落空列表，而不是把字符串塞进产物档。"""
    repo_meta.save_repos("not-a-list")  # type: ignore[arg-type]
    assert json.loads((data / "repos.json").read_text(encoding="utf-8"))["repos"] == []


def test_拒写时属主返回False且档案搬走后恢复(data: Path, monkeypatch):
    """档坏到不敢写 ⇒ 一路冒到 HTTP：`{"ok": false}`，而不是假装保存成功。"""

    def _boom(*_a, **_k):
        raise OSError("disk is busy")

    doc = repo_meta.user_state_path()
    doc.write_text(json.dumps({"schemaVersion": 99}), encoding="utf-8")
    monkeypatch.setattr(state_store, "copy2", _boom)

    assert repo_meta.save_settings({"theme": "dark"}) is False
    assert state_store.blocked_paths() == (str(doc),)

    assert user_state.set_state(user_state.UserState(settings={"theme": "dark"})) == {"ok": False}
    # 拒写期间盘上内容一个字节没动（这正是「不拿空状态盖掉读不懂的档案」）
    assert json.loads(doc.read_text(encoding="utf-8")) == {"schemaVersion": 99}

    # 恢复口径 = ERROR 日志里那句「人工把该文件搬走或改对」；拒写状态在下一次读时自动解除
    doc.unlink()
    assert user_state.get_state().settings is None
    assert state_store.blocked_paths() == ()
    payload = user_state.UserState(repos=[{"id": "r1"}], settings={"theme": "dark"})
    assert user_state.set_state(payload) == {"ok": True}
    assert user_state.get_state().repos == [{"id": "r1"}]


# ── 路由层：wire 形状不变 ──────────────────────────────────────────────────────
def test_路由按档分写且读回合并视图(data: Path):
    user_state.set_state(user_state.UserState(
        repos=[{"id": "r1", "name": "作品甲"}],
        settings={"theme": "dark"},
        selection={"repoId": "r1"},
    ))

    state = user_state.get_state()
    assert state.repos == [{"id": "r1", "name": "作品甲"}]
    assert state.settings == {"theme": "dark"}
    assert state.selection == {"repoId": "r1"}

    assert (data / "repos.json").is_file()
    assert "repos" not in json.loads((data / "user_state.json").read_text(encoding="utf-8"))


def test_请求不带repos时索引档原样保留(data: Path):
    """缺席≠删除：只回传设置（手机端改一项设置）不得把电脑上的作品索引清空。"""
    user_state.set_state(user_state.UserState(repos=[{"id": "r1"}], settings={"theme": "dark"}))

    user_state.set_state(user_state.UserState(settings={"theme": "liquid-glass"}))

    assert json.loads((data / "repos.json").read_text(encoding="utf-8"))["repos"] == [{"id": "r1"}]
    assert user_state.get_state().repos == [{"id": "r1"}]
    assert user_state.get_state().settings == {"theme": "liquid-glass"}


def test_显式空repos才是清空(data: Path):
    user_state.set_state(user_state.UserState(repos=[{"id": "r1"}], settings={}))

    user_state.set_state(user_state.UserState(repos=[], settings={}))

    assert user_state.get_state().repos == []
