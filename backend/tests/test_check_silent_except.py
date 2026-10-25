"""check_silent_except 门禁自身的回归（2026-10-24 审计改进项 8）。

覆盖判定口径三豁免（raise/日志/[EXC-保留] 标注）与棘轮比较语义；
脚本在 backend/scripts/ 下（非 app 包），用 importlib 按路径加载。
"""
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_silent_except", Path(__file__).resolve().parents[1] / "scripts" / "check_silent_except.py"
)
assert _SPEC is not None and _SPEC.loader is not None
cse = importlib.util.module_from_spec(_SPEC)
sys.modules["check_silent_except"] = cse
_SPEC.loader.exec_module(cse)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sample.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_静默兜底被计数(tmp_path):
    p = _write(tmp_path, "try:\n    f()\nexcept Exception:\n    return None\n")
    assert cse.scan_file(p) == [3]


def test_裸except也计数(tmp_path):
    p = _write(tmp_path, "try:\n    f()\nexcept:\n    pass\n")
    assert cse.scan_file(p) == [3]


def test_有raise不计数(tmp_path):
    p = _write(tmp_path, "try:\n    f()\nexcept Exception:\n    raise\n")
    assert cse.scan_file(p) == []


def test_有日志调用不计数(tmp_path):
    p = _write(
        tmp_path,
        "try:\n    f()\nexcept Exception:\n    _LOG.warning('x 失败: %s', 'e')\n    return None\n",
    )
    assert cse.scan_file(p) == []


def test_带保留标注不计数(tmp_path):
    p = _write(
        tmp_path,
        "try:\n    f()\nexcept Exception:  # noqa: BLE001 [EXC-保留] 列表绝不 500\n    return []\n",
    )
    assert cse.scan_file(p) == []


def test_窄异常不计数(tmp_path):
    p = _write(tmp_path, "try:\n    f()\nexcept ValueError:\n    return None\n")
    assert cse.scan_file(p) == []


def test_棘轮比较_上升与新文件都算回归():
    baseline = {"app/services/a.py": 2}
    assert cse.compare({"app/services/a.py": 3}, baseline) != []
    assert cse.compare({"app/services/new.py": 1}, baseline) != []


def test_棘轮比较_持平与下降放行():
    baseline = {"app/services/a.py": 2, "app/services/b.py": 1}
    assert cse.compare({"app/services/a.py": 2}, baseline) == []
    assert cse.compare({"app/services/a.py": 1}, baseline) == []


def test_仓库当前计数不超过基线():
    """真源棘轮：对 app/ 实跑一遍，与基线文件对拍（防基线被人手改松）。"""
    import json

    data = json.loads(cse.BASELINE_PATH.read_text(encoding="utf-8"))
    current = cse.scan_tree(Path("app"))
    assert cse.compare(current, {k: int(v) for k, v in data["counts"].items()}) == []
    assert sum(current.values()) == data["total"]
