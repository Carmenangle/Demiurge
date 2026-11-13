"""用户档的跨进程读改写（2026-11-07 H 项）。

`state_store.save` 原本只做「临时文件 + replace」：单次写不撕裂，但
「读 → 改 → 写」整段没有跨进程锁，两个实例会各自拿着旧档回写。默认只处理
设置档：`save_settings` 必须在锁内重新读最新档，未显式提供的 `selection`
不能被另一实例的旧副本抹掉；真正需要「读最新再追加」的调用走 `mutate`。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

_MUTATE_CHILD = r"""
import os
import time
from pathlib import Path

from app import state_store

go = Path(os.environ["GO_PATH"])
while not go.exists():
    time.sleep(0.01)

path = Path(os.environ["STATE_PATH"])
item = os.environ["ITEM"]


def apply(doc):
    items = list(doc.get("items") or [])
    items.append(item)
    time.sleep(0.8)  # 放大「读后写前」窗口：无锁时两进程必然都基于同一份旧档
    return {"items": items}, True


print("MUTATE_OK", state_store.mutate(path, apply))
"""

_HOLD_CHILD = r"""
import os
import time
from pathlib import Path

from app import state_store
from app.services import repo_meta

with state_store.path_lock(repo_meta.user_state_path(), timeout=20):
    Path(os.environ["HELD"]).write_text("1", encoding="utf-8")
    time.sleep(1.2)
    # 持锁期间完成另一次合法写入：等待方必须读到这一份，而不是它自己早先的旧副本
    state_store.save(
        repo_meta.user_state_path(),
        {"settings": {"theme": "child"}, "selection": {"repo": "r2"}},
    )
"""


def _spawn(code: str, env_extra: dict[str, str]) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(env_extra)
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_mutate两进程并发追加不丢更新(tmp_path: Path):
    """旧代码没有「锁内读最新再写」的接缝 ⇒ 后写者会用旧列表盖掉先写者。"""
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"schemaVersion": 1, "items": []}), encoding="utf-8"
    )
    go = tmp_path / "go"
    procs = [
        _spawn(
            _MUTATE_CHILD,
            {
                "GO_PATH": str(go),
                "STATE_PATH": str(state_path),
                "ITEM": item,
            },
        )
        for item in ("A", "B")
    ]
    go.write_text("1", encoding="utf-8")
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"mutate 失败：{err}"
        assert "MUTATE_OK" in out

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert sorted(payload["items"]) == ["A", "B"], (
        f"并发读改写丢了更新：{payload['items']}"
    )


def test_save_settings会等同一路径的跨进程锁(tmp_path: Path, monkeypatch):
    """`save_settings` 必须在锁内读最新档；否则另一实例持锁期间写入的 `selection` 会被旧副本抹掉。"""
    from app import state_store
    from app.services import repo_meta

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(repo_meta, "DATA_DIR", data_dir)
    user_state = data_dir / "user_state.json"
    state_store.save(user_state, {"settings": {"theme": "old"}, "selection": {"repo": "r1"}})

    held = tmp_path / "held"
    proc = _spawn(_HOLD_CHILD, {"LAF_DATA_DIR": str(data_dir), "HELD": str(held)})
    try:
        deadline = time.monotonic() + 10
        while not held.exists():
            assert time.monotonic() < deadline, "子进程未拿到锁"
            assert proc.poll() is None, proc.stderr.read() if proc.stderr else "子进程提前退出"
            time.sleep(0.02)

        started = time.monotonic()
        assert repo_meta.save_settings({"theme": "new"}) is True
        elapsed = time.monotonic() - started
        assert elapsed >= 0.8, f"save_settings 没有等跨进程锁（{elapsed:.2f}s）"
    finally:
        proc.communicate(timeout=60)

    payload = json.loads(user_state.read_text(encoding="utf-8"))
    assert payload["settings"] == {"theme": "new"}
    assert payload["selection"] == {"repo": "r2"}, "另一实例持锁期间写入的 selection 不得被旧副本覆盖"


def test_save_settings走锁内读改写接缝():
    """接线锁：`save_settings` 必须调 `mutate`，不许回到「先 load 再 save」的旧两段式。"""
    import ast

    source = (BACKEND / "app" / "services" / "repo_meta.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "save_settings"
    )
    calls: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            calls.add(node.func.id)

    assert "mutate" in calls
    assert "save" not in calls and "load" not in calls
