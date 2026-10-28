"""守卫哨兵的回归：非 .py 文件不在快照面，至少要出现在跑前提示里（#62）。

快照只收 `*.py`；将来 `tests/fixtures/*.json` 这类参数化数据被中途改写时，
「跑批期间源码被改写」这道保险不成立。哨兵只提示、不判红，所以这里锁的是
「提示得出来」，不是退出码。
"""
from scripts import full_gate_pytest as gate


def _rig(tmp_path, monkeypatch, dirs=("app", "tests")):
    monkeypatch.setattr(gate, "BACKEND", tmp_path)
    monkeypatch.setattr(gate, "WATCHED_DIRS", dirs)
    for name in dirs:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_非py文件计数且跳过缓存目录(tmp_path, monkeypatch):
    root = _rig(tmp_path, monkeypatch)
    (root / "app" / "a.py").write_text("", encoding="utf-8")
    (root / "app" / "cases.json").write_text("{}", encoding="utf-8")
    pycache = root / "tests" / "__pycache__"
    pycache.mkdir()
    (pycache / "x.cpython-313.pyc").write_bytes(b"")

    count, sample = gate.non_python_files()

    assert count == 1
    assert sample == ["app/cases.json"]


def test_全绿目录不出提示(tmp_path, monkeypatch):
    root = _rig(tmp_path, monkeypatch)
    (root / "tests" / "test_a.py").write_text("", encoding="utf-8")

    assert gate.non_python_hint() == ""


def test_提示写明数量与路径且超量折叠(tmp_path, monkeypatch):
    root = _rig(tmp_path, monkeypatch, dirs=("tests",))
    for index in range(7):
        (root / "tests" / f"fixture-{index}.json").write_text("{}", encoding="utf-8")

    hint = gate.non_python_hint()

    assert hint.startswith("[gate] ⚠ 7 个非 .py 文件不在快照面")
    assert "tests/fixture-0.json" in hint
    assert "tests/fixture-5.json" not in hint
    assert hint.endswith(" …")


def test_缺目录不炸(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "BACKEND", tmp_path)
    monkeypatch.setattr(gate, "WATCHED_DIRS", ("app", "no-such-dir"))

    assert gate.non_python_files() == (0, [])
