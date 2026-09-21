# -*- coding: utf-8 -*-
"""③ 引擎路径统一（2026-09-18）：引擎域（state/tables/chronicle/事实/认知/世界观快照…）

历史错轨：引擎族用 `Path(base)/safe_seg(repo_id)`（UUID 目录），chat/图/文档/画布/卡走
`repo_meta.folder_name()`（仓库名目录）⇒ 同一仓库两套落点，实测 `凌霄仙母录` 下 state.json
分裂成 turn=36（引擎处）与 turn=21（repo_meta 处）两份。

本刀：新增 `repo_meta.engine_repo_dir(output_dir, repo_id)` 作为引擎域**唯一路径属主**，
12 处调用点改指；旧 UUID 目录有数据且新路径无数据时整目录迁移（含备份、幂等、幂等失败不丢）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import repo_meta
from app.services.pathnames import safe_seg


# ── 夹具：可控的 user_state（repos 名表） ───────────────────────────────────

def _write_state(tmp_path, monkeypatch, repos, *, works_root=""):
    """把 DATA_DIR/user_state.json 指到 tmp_path，注入 repos 清单。"""
    from app.config import DATA_DIR
    monkeypatch.setattr(repo_meta, "DATA_DIR", tmp_path)
    (tmp_path / "user_state.json").write_text(
        json.dumps({"repos": repos, "settings": {"outputDir": works_root}},
                   ensure_ascii=False), encoding="utf-8")
    return DATA_DIR


def _repo(repo_id: str, name: str, parent_id: str = ""):
    return {"id": repo_id, "name": name, **({"parentId": parent_id} if parent_id else {})}


# ── engine_repo_dir：路径解析口径 ──────────────────────────────────────────

def test_engine_repo_dir_中文仓库名用safe_dir目录(tmp_path, monkeypatch):
    """named repo → 与 chat/图/文档同域（仓库名文件夹，保中文）。"""
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    got = repo_meta.engine_repo_dir(str(tmp_path / "works"), "uuid-1")
    assert got == Path(str(tmp_path / "works")) / "凌霄仙母录"


def test_engine_repo_dir_子仓库嵌父作品文件夹(tmp_path, monkeypatch):
    """子仓库 → <父名>/<子名>（与 repo_folder_path 口径一致，防同名子仓库互撞）。"""
    _write_state(tmp_path, monkeypatch, [
        _repo("p-1", "凌霄仙母录"),
        _repo("c-1", "SAVE01", parent_id="p-1"),
    ])
    got = repo_meta.engine_repo_dir(str(tmp_path / "works"), "c-1")
    assert got == Path(str(tmp_path / "works")) / "凌霄仙母录" / "SAVE01"


def test_engine_repo_dir_home与无记录退回UUID段(tmp_path, monkeypatch):
    """home / 无仓库记录 → 退回 safe_seg(repo_id)（与老口径逐字节一致，零迁移）。"""
    _write_state(tmp_path, monkeypatch, [])
    root = str(tmp_path / "works")
    assert repo_meta.engine_repo_dir(root, "home") == Path(root) / "home"
    assert repo_meta.engine_repo_dir(root, "uuid-plain") == Path(root) / safe_seg("uuid-plain")


def test_engine_repo_dir_空base或空repo原样返回(tmp_path):
    assert repo_meta.engine_repo_dir("", "uuid-1") == Path(safe_seg("uuid-1"))
    assert repo_meta.engine_repo_dir(str(tmp_path), "") == Path(safe_seg("", "x"))


def test_engine_repo_dir_有仓库名但无base退回当前目录段(tmp_path, monkeypatch):
    """无 base（未配置作品库根）时不得拼出绝对/相对混淆路径——退回 repo_id 段。"""
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    assert repo_meta.engine_repo_dir("", "uuid-1") == Path(safe_seg("uuid-1"))


# ── 迁移：旧 UUID 目录 → 新仓库名目录 ──────────────────────────────────────

def test_migrate_engine_dir_旧目录有数据新目录无_整目录搬运(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = tmp_path / "works"
    old = root / safe_seg("uuid-1")
    old.mkdir(parents=True)
    (old / "state.json").write_text('{"快照":{"turn":36}}', encoding="utf-8")
    (old / "chronicle.db").write_bytes(b"SQLite format 3\x00")

    got = repo_meta.engine_repo_dir(str(root), "uuid-1")
    assert got == root / "凌霄仙母录"
    assert (got / "state.json").read_text(encoding="utf-8") == '{"快照":{"turn":36}}'
    assert (got / "chronicle.db").read_bytes() == b"SQLite format 3\x00"
    assert not old.exists(), "旧目录应已让位（整体 rename），不留半份分叉"


def test_migrate_engine_dir_新目录已有数据_旧数据不覆盖且留档(tmp_path, monkeypatch):
    """新路径已有文件（另一分支写过的陈旧副本）：**不得覆盖**，把旧目录整体挪到
    `<新目录>/.engine_legacy_<旧段>/` 留档，避免静默丢弃。"""
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = tmp_path / "works"
    new = root / "凌霄仙母录"
    new.mkdir(parents=True)
    (new / "state.json").write_text('{"快照":{"turn":21}}', encoding="utf-8")
    old = root / safe_seg("uuid-1")
    old.mkdir(parents=True)
    (old / "state.json").write_text('{"快照":{"turn":36}}', encoding="utf-8")

    got = repo_meta.engine_repo_dir(str(root), "uuid-1")
    assert (got / "state.json").read_text(encoding="utf-8") == '{"快照":{"turn":21}}'
    keeper = got / f".engine_legacy_{safe_seg('uuid-1')}"
    assert (keeper / "state.json").read_text(encoding="utf-8") == '{"快照":{"turn":36}}'


def test_migrate_engine_dir_新位置已有同名备份目录_不重复搬且不抛(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = tmp_path / "works"
    new = root / "凌霄仙母录"
    new.mkdir(parents=True)
    (new / "state.json").write_text("{}", encoding="utf-8")
    keeper = new / f".engine_legacy_{safe_seg('uuid-1')}"
    keeper.mkdir()
    (keeper / "state.json").write_text('{"old":true}', encoding="utf-8")
    old = root / safe_seg("uuid-1")
    old.mkdir(parents=True)
    (old / "state.json").write_text('{"快照":{"turn":36}}', encoding="utf-8")

    repo_meta.engine_repo_dir(str(root), "uuid-1")  # 仅取副作用：触发迁移尝试
    assert (keeper / "state.json").read_text(encoding="utf-8") == '{"old":true}'
    # 旧目录不再是「干净可搬」→ 原地保留（不覆盖任何一方，数据不丢）
    assert old.is_dir()


def test_migrate_engine_dir_幂等_重复调用无副作用(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = tmp_path / "works"
    old = root / safe_seg("uuid-1")
    old.mkdir(parents=True)
    (old / "state.json").write_text('{"快照":{"turn":36}}', encoding="utf-8")

    first = repo_meta.engine_repo_dir(str(root), "uuid-1")
    stamp = (first / "state.json").stat().st_mtime_ns
    second = repo_meta.engine_repo_dir(str(root), "uuid-1")
    assert second == first
    assert (first / "state.json").stat().st_mtime_ns == stamp
    assert not old.exists()


def test_engine_repo_dir_老口径与新口径重合时不迁移(tmp_path, monkeypatch):
    """repo_id 段与仓库名段相同（home / 纯 ASCII 名）：路径本就同一条，零操作。"""
    _write_state(tmp_path, monkeypatch, [_repo("home", "")])
    root = tmp_path / "works"
    d = root / safe_seg("home")
    d.mkdir(parents=True)
    (d / "state.json").write_text('{"快照":{"turn":5}}', encoding="utf-8")

    got = repo_meta.engine_repo_dir(str(root), "home")
    assert got == d
    assert (got / "state.json").read_text(encoding="utf-8") == '{"快照":{"turn":5}}'
    assert not list(got.glob(".engine_legacy_*")), "重合路径不得触发任何备份/搬迁"


# ── 12 处调用点改指：行为锁（写读同源 + 与 repo_meta 同域） ────────────────

@pytest.mark.parametrize("module_name,file_name", [
    ("character_state", "state.json"),
    ("narrative_store", "chronicle.db"),
    ("table_store", "tables.json"),
    ("manual_table_fill", "table_progress.json"),
    ("worldbook_store", "worldbook.json"),
])
def test_引擎族落点与repo_meta同域(tmp_path, monkeypatch, module_name, file_name):
    """每个引擎模块的落点必须 = engine_repo_dir(base, repo_id) 之下。
    覆盖有独立路径函数的 5 个属主（其余 3 个 DB 模块只有 _connect，见下一测）。"""
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = str(tmp_path / "works")
    mod = __import__(f"app.services.{module_name}", fromlist=["_"])
    want = repo_meta.engine_repo_dir(root, "uuid-1") / file_name

    resolver = {
        "character_state": lambda: mod.state_path(root, "uuid-1"),
        "narrative_store": lambda: mod.db_path(root, "uuid-1"),
        "table_store": lambda: mod.tables_path(root, "uuid-1"),
        "manual_table_fill": lambda: mod._progress_path(root, "uuid-1"),
        "worldbook_store": lambda: mod.repo_snapshot_path(root, "uuid-1"),
    }[module_name]
    assert resolver() == want, f"{module_name} 落点未与引擎域统一"


@pytest.mark.parametrize("module_name,file_name", [
    ("temporal_fact_store", "temporal_facts.db"),
    ("character_belief", "character_beliefs.db"),
    ("narrative_ci", "narrative_ci.db"),
    ("visual_preference", "visual_preferences.db"),
])
def test_单库模块落点与repo_meta同域(tmp_path, monkeypatch, module_name, file_name):
    """只有 `_connect`（无独立路径函数）的 DB 模块：实际建库位置必须落在引擎域。
    通过真建一次连接后检查落盘文件位置来验证（而非猜内部函数名）。"""
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = str(tmp_path / "works")
    mod = __import__(f"app.services.{module_name}", fromlist=["_"])
    conn = mod._connect(root, "uuid-1")
    try:
        got = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    finally:
        conn.close()
    assert got == repo_meta.engine_repo_dir(root, "uuid-1") / file_name


def test_visual_preference读visual_ci同域(tmp_path, monkeypatch):
    """visual_preference 的 red 资产读取（不走 _connect）也必须与引擎域同源。"""
    import sqlite3
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = tmp_path / "works"
    d = repo_meta.engine_repo_dir(str(root), "uuid-1")
    d.mkdir(parents=True)
    con = sqlite3.connect(str(d / "visual_ci.db"))
    con.execute("CREATE TABLE diagnostics (generation_id TEXT, verdict TEXT)")
    con.execute("INSERT INTO diagnostics VALUES ('g-red','red')")
    con.commit()
    con.close()
    from app.services import visual_preference
    assert visual_preference._red_asset_ids(str(root), "uuid-1") == {"g-red"}


def test_agent_context_assembly世界书会话与repo_meta同域(tmp_path, monkeypatch):
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = str(tmp_path / "works")
    from app.services import agent_context_assembly as aca
    got = aca._worldbook_session_state_path({"output_dir": root, "repo_id": "uuid-1"})
    assert got == str(repo_meta.engine_repo_dir(root, "uuid-1") / "worldbook_session.json")


def test_引擎族不再出现裸safe_seg拼路径(tmp_path):
    """源码形态锁：`Path(base) / safe_seg(repo_id, strip=False)` 这一老口径写法
    在所有引擎模块中必须清零（改指 engine_repo_dir 的唯一路径属主）。"""
    import re
    services = Path(__file__).resolve().parents[1] / "app" / "services"
    offenders: list[str] = []
    for src in services.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        # 去掉注释行后匹配（注释里提到老写法属正常说明）
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        if re.search(r"Path\(base\)\s*/\s*safe_seg\(\s*repo_id\s*,\s*strip=False\s*\)", body):
            offenders.append(src.name)
    assert offenders == [], f"仍用老口径拼引擎路径：{offenders}"


# ── 2026-09-20 实锤：新位置只被「非引擎产物」占住时，引擎数据不得被留档留到看不见 ──

def test_migrate_engine_dir_新目录只有会话产物_引擎文件必须并入(tmp_path, monkeypatch):
    """凌霄仙母录实测事故：新路径只因 `chat.json`/图片存在，就被判成「已有数据」⇒ 旧引擎目录
    被整体 rename 进 `.engine_legacy_*` 留档 ⇒ **活跃路径从此没有 tables.json/state.json**，
    UI 上表现为「数据表内容全部丢失」。

    口径修正：「新位置已有数据」必须指**已有引擎文件**；只有会话/图片等非引擎产物时，
    要先把旧目录里的引擎文件**并入**活跃路径，再把旧目录留档。
    """
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = tmp_path / "works"
    new = root / "凌霄仙母录"
    new.mkdir(parents=True)
    (new / "chat.json").write_text('{"messages":[]}', encoding="utf-8")  # 非引擎产物
    (new / "封面.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    old = root / safe_seg("uuid-1")
    old.mkdir(parents=True)
    (old / "tables.json").write_text('{"tables":[{"name":"背包物品表"}]}', encoding="utf-8")
    (old / "state.json").write_text('{"快照":{"turn":36}}', encoding="utf-8")

    got = repo_meta.engine_repo_dir(str(root), "uuid-1")

    assert got == new
    assert (got / "tables.json").is_file(), "引擎数据必须并入活跃路径（否则 UI 里表格全空）"
    assert (got / "state.json").read_text(encoding="utf-8") == '{"快照":{"turn":36}}'
    assert (got / "chat.json").read_text(encoding="utf-8") == '{"messages":[]}', "非引擎产物不得被动"
    assert (got / "封面.png").read_bytes() == b"\x89PNG\r\n\x1a\n"
    keeper = got / f".engine_legacy_{safe_seg('uuid-1')}"
    assert keeper.is_dir(), "旧目录仍要留档（保留可回滚底）"
    assert (keeper / "tables.json").is_file(), "留档必须是完整副本"


def test_migrate_engine_dir_新目录只有会话产物_已并入后幂等(tmp_path, monkeypatch):
    """并入完成后再调一次：不得重复复制、不得覆盖、不得再搬。"""
    _write_state(tmp_path, monkeypatch, [_repo("uuid-1", "凌霄仙母录")])
    root = tmp_path / "works"
    new = root / "凌霄仙母录"
    new.mkdir(parents=True)
    (new / "chat.json").write_text("{}", encoding="utf-8")
    old = root / safe_seg("uuid-1")
    old.mkdir(parents=True)
    (old / "tables.json").write_text('{"tables":[1]}', encoding="utf-8")

    first = repo_meta.engine_repo_dir(str(root), "uuid-1")
    (first / "tables.json").write_text('{"tables":[2]}', encoding="utf-8")  # 用户后续改动
    stamp = (first / "tables.json").stat().st_mtime_ns

    second = repo_meta.engine_repo_dir(str(root), "uuid-1")

    assert second == first
    assert (first / "tables.json").read_text(encoding="utf-8") == '{"tables":[2]}', "已被写过的引擎文件不得回灌旧值"
    assert (first / "tables.json").stat().st_mtime_ns == stamp


def test_migrate_engine_dir_子仓库老布局嵌父作品文件夹_也能找到(tmp_path, monkeypatch):
    """子仓库的老引擎目录有两种历史布局：`<根>/<uuid>` 与 `<根>/<父名>/<uuid>`。
    凌霄仙母录实测是后者（`pictures/凌霄仙母录/09f79e6f-…`）⇒ 两种都必须能找到。"""
    _write_state(tmp_path, monkeypatch, [
        _repo("p-1", "凌霄仙母录"),
        _repo("c-1", "SAVE01", parent_id="p-1"),
    ])
    root = tmp_path / "works"
    new = root / "凌霄仙母录" / "SAVE01"
    new.mkdir(parents=True)
    (new / "chat.json").write_text("{}", encoding="utf-8")
    old = root / "凌霄仙母录" / safe_seg("c-1")
    old.mkdir(parents=True)
    (old / "tables.json").write_text('{"tables":[1]}', encoding="utf-8")

    got = repo_meta.engine_repo_dir(str(root), "c-1")

    assert got == new
    assert (got / "tables.json").read_text(encoding="utf-8") == '{"tables":[1]}', \
        "嵌在父作品文件夹下的老引擎目录也必须能被发现并并入"
