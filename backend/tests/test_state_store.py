"""档位化存档层 `app/state_store.py` 的回归例（2026-11-02 P1）。

三条合同，逐条都是「设置没了」这类事故的直接对立面：

1. 档位**认识**（含未打标的 0＝拆分前单档形状）⇒ 正常读，不备份、不重建；
2. 档位**不认**（更高档 / 字段类型不对 / JSON 坏 / 根不是对象）⇒ 先落
   `<名>.bak-<时间戳>` 再建新，读侧退回空档；
3. **备份失败绝不建新** ⇒ 该路径进入拒写：宁可设置改不动，也不拿空状态盖掉读不懂的档案
   （2026-10-28 实测过一次「按空状态整体覆盖写」把盘上真 key 抹成空串）。

备份名必须能被卸载面扫到，所以时间戳格式和 `<名>.bak-` 前缀也在这里钉住。
"""
from __future__ import annotations

import ast
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from app import state_store


@pytest.fixture(autouse=True)
def _clean_blocked_registry():
    """拒写集合是模块级进程状态：逐例快照还原，避免串味。"""
    saved = set(state_store._BLOCKED)
    yield
    state_store._BLOCKED.clear()
    state_store._BLOCKED.update(saved)


def _write(path: Path, payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def _backups(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.glob(f"{path.name}.bak-*"))


# ── 档 1：认识就照读 ────────────────────────────────────────────────────────────
def test_未打标旧档按零档读取(tmp_path: Path):
    """0 是**认识**的形状（拆分前单档），走迁移不是走建新 ⇒ 不许产生备份。"""
    doc = tmp_path / "user_state.json"
    _write(doc, {"repos": [{"id": "r1"}], "settings": {"theme": "dark"}})

    assert state_store.load(doc) == {"repos": [{"id": "r1"}], "settings": {"theme": "dark"}}
    assert _backups(doc) == []


def test_当前档位原样读取(tmp_path: Path):
    doc = tmp_path / "repos.json"
    _write(doc, {"schemaVersion": state_store.SCHEMA_VERSION, "repos": [{"id": "r1"}]})

    loaded = state_store.load(doc)
    assert loaded["repos"] == [{"id": "r1"}]
    assert _backups(doc) == []


def test_档案不存在就是空档不建备份(tmp_path: Path):
    assert state_store.load(tmp_path / "missing.json") == {}
    assert list(tmp_path.iterdir()) == []


# ── 档 2：不认 ⇒ 备份 + 建新 ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    "payload,expect",
    [
        ({"schemaVersion": 99, "settings": {}}, "更高档位"),
        ({"schemaVersion": "1"}, "档位是字符串"),
        ({"schemaVersion": True}, "档位是布尔"),
        ({"schemaVersion": -3}, "档位为负"),
        ({"schemaVersion": 1.5}, "档位是小数"),
        ([1, 2, 3], "根是数组"),
        ("\"just a string\"", "根是字符串"),
        ("{not-json", "JSON 坏了"),
    ],
)
def test_不认识的形状一律备份后建新(tmp_path: Path, payload, expect: str):
    doc = tmp_path / "user_state.json"
    _write(doc, payload)

    assert state_store.load(doc) == {}, f"{expect} 必须退回空档"

    backups = _backups(doc)
    assert len(backups) == 1, f"{expect} 未留备份：{backups}"
    # 备份是逐字节副本：内容能还原，用户才不是「丢了档案」而是「多了个备份」
    assert (tmp_path / backups[0]).read_text(encoding="utf-8") == (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
    # 原名让位 ⇒ 下一次写就是新档
    assert not doc.exists()
    assert state_store.blocked_paths() == ()


def test_备份名匹配时间戳格式并落在同目录(tmp_path: Path):
    import re

    doc = tmp_path / "user_state.json"
    _write(doc, {"schemaVersion": 99})
    state_store.load(doc)

    backups = _backups(doc)
    assert len(backups) == 1
    # 卸载面按 `<登记名>.bak-*` 清扫（见 app/data_layout.SETTINGS_BACKUP_STEMS 与生成的 NSIS 钩子）
    assert re.fullmatch(r"user_state\.json\.bak-\d{8}-\d{6}", backups[0]), backups[0]


def test_同一秒重复撞名不增殖备份(tmp_path: Path, monkeypatch):
    """每次读不懂都留一份的话，磁盘上会堆出几十个同内容文件 ⇒ 撞名复用已有的那份。"""
    fixed = SimpleNamespace(strftime=lambda _fmt: "20261102-030405")
    monkeypatch.setattr(state_store, "time", fixed)
    doc = tmp_path / "user_state.json"
    _write(doc, {"schemaVersion": 99})

    first = state_store.backup(doc)
    second = state_store.backup(doc)

    assert first == second == tmp_path / "user_state.json.bak-20261102-030405"
    assert len(_backups(doc)) == 1


def test_同一秒源内容改变保留两版(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_store, "time", SimpleNamespace(strftime=lambda _fmt: "20261102-030405"))
    doc = tmp_path / "user_state.json"
    doc.write_bytes(b'{"version":1}')
    first = state_store.backup(doc)
    assert first is not None

    doc.write_bytes(b'{"version":2}')
    second = state_store.backup(doc)

    assert second == tmp_path / "user_state.json.bak-20261102-030405-1"
    assert first.read_bytes() == b'{"version":1}'
    assert second.read_bytes() == doc.read_bytes() == b'{"version":2}'
    assert state_store.backup(doc) == second
    assert len(_backups(doc)) == 2
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("existing", [b'{"schemaVersion":', b'{"schemaVersion":98}'])
def test_已有不完整或不同内容备份不覆盖不复用(tmp_path: Path, monkeypatch, existing: bytes):
    monkeypatch.setattr(state_store, "time", SimpleNamespace(strftime=lambda _fmt: "20261102-030405"))
    doc = tmp_path / "user_state.json"
    doc.write_bytes(b'{"schemaVersion":99}')
    original = tmp_path / "user_state.json.bak-20261102-030405"
    numbered = tmp_path / "user_state.json.bak-20261102-030405-1"
    original.write_bytes(existing)
    numbered.write_bytes(b"older backup")
    stat = doc.stat()
    os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    result = state_store.backup(doc)

    assert result == tmp_path / "user_state.json.bak-20261102-030405-2"
    assert result.read_bytes() == doc.read_bytes() == b'{"schemaVersion":99}'
    assert original.read_bytes() == existing
    assert numbered.read_bytes() == b"older backup"
    assert state_store.backup(doc) == result
    assert len(_backups(doc)) == 3
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("failure_stage", ["copy", "replace"])
def test_备份失败清理临时文件且同秒重试得到完整副本(tmp_path: Path, monkeypatch, failure_stage: str):
    monkeypatch.setattr(state_store, "time", SimpleNamespace(strftime=lambda _fmt: "20261102-030405"))
    doc = tmp_path / "user_state.json"
    content = b'{"schemaVersion":99,"settings":{"theme":"dark"}}'
    doc.write_bytes(content)
    attempts: list[Path] = []

    def _partial_copy(source: Path, target: Path):
        attempts.append(target)
        target.write_bytes(source.read_bytes()[:4])
        raise OSError("copy interrupted")

    def _failed_replace(self: Path, target: Path):
        attempts.append(self)
        raise OSError("publish interrupted")

    with monkeypatch.context() as failing:
        if failure_stage == "copy":
            failing.setattr(state_store, "copy2", _partial_copy)
        else:
            failing.setattr(Path, "replace", _failed_replace)
        assert state_store.backup(doc) is None

    assert doc.read_bytes() == content
    assert len(attempts) == len(set(attempts)) == 2
    remaining = set(tmp_path.iterdir())
    result = state_store.backup(doc)

    assert result == tmp_path / "user_state.json.bak-20261102-030405"
    assert result.read_bytes() == doc.read_bytes() == content
    assert remaining == {doc}
    assert set(tmp_path.iterdir()) == {doc, result}


def test_半拷贝失败后再次读取不得删除原档(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_store, "time", SimpleNamespace(strftime=lambda _fmt: "20261102-030405"))
    doc = tmp_path / "user_state.json"
    content = b'{"schemaVersion":99}'
    doc.write_bytes(content)

    def _partial_copy(source: Path, target: Path):
        target.write_bytes(source.read_bytes()[:4])
        raise OSError("copy interrupted")

    monkeypatch.setattr(state_store, "copy2", _partial_copy)
    assert state_store.backup(doc) is None
    assert state_store.load(doc) == {}
    assert doc.read_bytes() == content
    assert str(doc) in state_store.blocked_paths()
    assert state_store.save(doc, {"settings": {}}) is False
    # 拒写判定在建锁之前；`.lock` 是 load 走「备份+建新」时留下的常驻接缝文件
    assert set(tmp_path.iterdir()) == {doc, tmp_path / "user_state.json.lock"}


def test_并发备份不把未完成拷贝暴露成成功(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_store, "time", SimpleNamespace(strftime=lambda _fmt: "20261102-030405"))
    doc = tmp_path / "user_state.json"
    content = b'{"schemaVersion":99,"settings":{"theme":"dark"}}'
    doc.write_bytes(content)
    copy_started = Event()
    release_copy = Event()
    real_copy = state_store.copy2

    def _paused_copy(source: Path, target: Path):
        if not copy_started.is_set():
            target.write_bytes(content[:4])
            copy_started.set()
            assert release_copy.wait(timeout=10), "未释放第一个拷贝"
        return real_copy(source, target)

    monkeypatch.setattr(state_store, "copy2", _paused_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(state_store.backup, doc)
        try:
            assert copy_started.wait(timeout=5), "第一个拷贝未启动"
            second = pool.submit(state_store.backup, doc)
            result = second.result(timeout=5)
            assert result is not None
            assert result.read_bytes() == content
            assert not first.done()
            assert _backups(doc) == [result.name]
        finally:
            release_copy.set()
        assert first.result(timeout=5) == result

    assert result.read_bytes() == doc.read_bytes() == content
    assert set(tmp_path.iterdir()) == {doc, result}


# ── 档 3：备份失败 ⇒ 拒写 ──────────────────────────────────────────────────────
def test_备份失败则不建新并拒写该档案(tmp_path: Path, monkeypatch):
    doc = tmp_path / "user_state.json"
    _write(doc, {"schemaVersion": 99, "settings": {"apiKey": "keep-me"}})

    def _boom(*_a, **_k):
        raise OSError("disk is busy")

    monkeypatch.setattr(state_store, "copy2", _boom)

    assert state_store.load(doc) == {}
    assert str(doc) in state_store.blocked_paths()
    # 最关键的一条：原档案**还在原地、内容未变**，没被空状态盖掉
    assert json.loads(doc.read_text(encoding="utf-8"))["settings"]["apiKey"] == "keep-me"
    assert _backups(doc) == []

    assert state_store.save(doc, {"settings": {"theme": "liquid-glass"}}) is False
    assert json.loads(doc.read_text(encoding="utf-8"))["settings"]["apiKey"] == "keep-me"
    # 读侧同样保持退回空档，不会一边拒写一边把旧档当合法档读进来
    assert state_store.load(doc) == {}


def test_拒写状态在档案被搬走后解除(tmp_path: Path, monkeypatch):
    doc = tmp_path / "user_state.json"
    _write(doc, {"schemaVersion": 99})

    def _boom(*_a, **_k):
        raise OSError("disk is busy")

    monkeypatch.setattr(state_store, "copy2", _boom)
    assert state_store.load(doc) == {}
    assert str(doc) in state_store.blocked_paths()

    monkeypatch.undo()
    doc.unlink()  # 用户自己把档案挪走了 ⇒ 没有数据可保护，拒写状态自动解除
    assert state_store.load(doc) == {}
    assert state_store.blocked_paths() == ()
    assert state_store.save(doc, {"settings": {}}) is True


def test_备份成功但原档删不掉仍然拒写(tmp_path: Path, monkeypatch):
    doc = tmp_path / "user_state.json"
    _write(doc, {"schemaVersion": 99})

    real_unlink = Path.unlink

    def _no_unlink(self: Path, *_a, **_k):
        if self == doc:
            raise OSError("locked by another process")
        return real_unlink(self, *_a, **_k)

    monkeypatch.setattr(Path, "unlink", _no_unlink)

    assert state_store.load(doc) == {}
    assert len(_backups(doc)) == 1  # 有备份兜着，但原档仍在
    assert doc.exists()
    assert state_store.save(doc, {"settings": {}}) is False


# ── 档 3.5：备份-删除接缝（2026-11-07 B 项）────────────────────────────────────
def test_备份后源内容变化则不删除(tmp_path: Path, monkeypatch):
    """「备份成功」不代表「删除时源文件还是那一份」：两段式之间被改写就不许删。"""
    doc = tmp_path / "user_state.json"
    doc.write_bytes(b'{"v":1}')
    real_copy = state_store.copy2

    def _copy_then_rewrite(source: Path, target: Path):
        real_copy(source, target)
        source.write_bytes(b'{"v":2}')

    monkeypatch.setattr(state_store, "copy2", _copy_then_rewrite)

    got = state_store.delete_with_backup(doc)

    assert got["deleted"] is False and got["reason"] == "changed-during-backup"
    assert doc.read_bytes() == b'{"v":2}'          # 新内容保留
    assert got["backup"] is not None and got["backup"].read_bytes() == b'{"v":1}'


def test_备份后源文件消失返回missing且备份保留(tmp_path: Path, monkeypatch):
    doc = tmp_path / "user_state.json"
    doc.write_bytes(b'{"v":1}')
    real_copy = state_store.copy2

    def _copy_then_unlink(source: Path, target: Path):
        real_copy(source, target)
        source.unlink()

    monkeypatch.setattr(state_store, "copy2", _copy_then_unlink)

    got = state_store.delete_with_backup(doc)

    assert got["deleted"] is False and got["reason"] == "missing"
    assert not doc.exists()
    assert got["backup"] is not None and got["backup"].read_bytes() == b'{"v":1}'


def test_备份失败一次unlink都不发生(tmp_path: Path, monkeypatch):
    doc = tmp_path / "user_state.json"
    doc.write_bytes(b'{"v":1}')
    monkeypatch.setattr(state_store, "backup", lambda path: None)
    real_unlink = Path.unlink

    def _no_unlink(self: Path, *_a, **_k):
        if self == doc:
            raise AssertionError("备份失败时不得删除源文件")
        return real_unlink(self, *_a, **_k)

    monkeypatch.setattr(Path, "unlink", _no_unlink)

    got = state_store.delete_with_backup(doc)

    assert got["deleted"] is False and got["reason"] == "backup-failed"
    assert doc.read_bytes() == b'{"v":1}'


def test_删除成功保留备份(tmp_path: Path):
    doc = tmp_path / "user_state.json"
    doc.write_bytes(b'{"v":1}')

    got = state_store.delete_with_backup(doc)

    assert got["deleted"] is True and got["reason"] == "ok"
    assert not doc.exists()
    assert got["backup"] is not None
    assert got["backup"].is_file() and got["backup"].read_bytes() == b'{"v":1}'


def test_删除失败保留备份且错误可见(tmp_path: Path, monkeypatch):
    doc = tmp_path / "user_state.json"
    doc.write_bytes(b'{"v":1}')
    real_unlink = Path.unlink

    def _no_unlink(self: Path, *_a, **_k):
        if self == doc:
            raise OSError("locked")
        return real_unlink(self, *_a, **_k)

    monkeypatch.setattr(Path, "unlink", _no_unlink)

    got = state_store.delete_with_backup(doc)

    assert got["deleted"] is False and got["reason"] == "unlink-failed"
    assert "locked" in got["error"]
    assert got["backup"] is not None and got["backup"].is_file()


def test_拒绝重建时源内容变化也拒写(tmp_path: Path, monkeypatch):
    doc = tmp_path / "user_state.json"
    _write(doc, {"schemaVersion": 99, "settings": {"apiKey": "old"}})
    real_copy = state_store.copy2

    def _copy_then_rewrite(source: Path, target: Path):
        real_copy(source, target)
        source.write_text(
            json.dumps({"schemaVersion": 99, "settings": {"apiKey": "new"}}), encoding="utf-8")

    monkeypatch.setattr(state_store, "copy2", _copy_then_rewrite)

    assert state_store.load(doc) == {}
    assert doc.exists() and "new" in doc.read_text(encoding="utf-8")
    assert str(doc) in state_store.blocked_paths()
    assert state_store.save(doc, {"settings": {}}) is False


def test_删除接缝是唯一删除入口():
    """源码形态锁：worldbook 删除与「不认重建」都只许走 delete_with_backup。

    直接读源码 + AST 断言调用集合（不用正则，注释/字符串不会误命中）；有人改回
    「backup 之后再单独 unlink」的旧两段式 ⇒ 本例如红，且只有本例红。
    """
    root = Path(__file__).resolve().parents[1]

    def _calls(rel: str, func_name: str) -> set[str]:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == func_name
        )
        found: set[str] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                found.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                found.add(node.func.id)
        return found

    book_calls = _calls("app/services/worldbook_store.py", "delete_book")
    assert "delete_with_backup" in book_calls
    assert "backup" not in book_calls and "unlink" not in book_calls

    reject_calls = _calls("app/state_store.py", "_reject_and_rebuild")
    assert "delete_with_backup" in reject_calls
    assert "backup" not in reject_calls and "unlink" not in reject_calls


# ── 写侧：打档与原子写 ─────────────────────────────────────────────────────────
def test_写入打上当前档位(tmp_path: Path):
    doc = tmp_path / "repos.json"

    assert state_store.save(doc, {"repos": [{"id": "r1"}]}) is True

    on_disk = json.loads(doc.read_text(encoding="utf-8"))
    assert on_disk["schemaVersion"] == state_store.SCHEMA_VERSION
    assert on_disk["repos"] == [{"id": "r1"}]


def test_调用方自带的档位字段被本层覆盖(tmp_path: Path):
    """档位由属主说了算：调用方塞进来的 99 不能把一个可写档伪装成「更新的版本」。"""
    doc = tmp_path / "user_state.json"
    state_store.save(doc, {"schemaVersion": 99, "settings": {"theme": "dark"}})

    on_disk = json.loads(doc.read_text(encoding="utf-8"))
    assert on_disk["schemaVersion"] == state_store.SCHEMA_VERSION
    assert on_disk["settings"] == {"theme": "dark"}


def test_写失败抛错而不是静默回退(tmp_path: Path):
    """写失败属于「操作失败」，必须冒出来；不能和「不敢写」共用一个 False 信号。"""
    blocker = tmp_path / "user_state.json"
    blocker.mkdir()  # 目标是目录 ⇒ replace 必失败

    with pytest.raises(OSError):
        state_store.save(blocker, {"settings": {}})
    # 临时文件不得残留；跨进程锁文件是常驻接缝的一部分（不随写入成败删除）
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "user_state.json", "user_state.json.lock"]
    assert list(tmp_path.glob("*.tmp")) == []
