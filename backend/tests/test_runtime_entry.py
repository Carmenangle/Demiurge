import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "runtime_entry", ROOT / "scripts" / "runtime_entry.py"
)
assert SPEC and SPEC.loader
runtime_entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_entry)


def test_full_rag_runtime_configures_dependencies_without_bundled_model(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("LAF_RUNTIME_EDITION", "full-rag")
    monkeypatch.setenv("LAF_BUNDLED_RERANKER_DIR", "stale")

    runtime_entry.configure_environment(tmp_path)

    assert runtime_entry.os.environ["LAF_RUNTIME_EDITION"] == "full-rag"
    assert "LAF_BUNDLED_RERANKER_DIR" not in runtime_entry.os.environ


def test_full_rag_self_check_reports_torch_build(monkeypatch, capsys):
    class TorchVersion:
        cuda = "13.0"

    class Torch:
        __version__ = "2.13.0+cu130"
        version = TorchVersion()

    monkeypatch.setenv("LAF_RUNTIME_EDITION", "full-rag")
    monkeypatch.setattr(
        runtime_entry.importlib, "import_module",
        lambda name: Torch() if name == "torch" else object(),
    )

    runtime_entry.self_check()

    payload = json.loads(capsys.readouterr().out)
    assert payload["torch_version"] == "2.13.0+cu130"
    assert payload["torch_cuda"] == "13.0"


def test_packaged_main_uses_combined_frontend_backend_port_without_console_logging(
    monkeypatch, tmp_path,
):
    seen = {}
    monkeypatch.setenv("LAF_NO_BROWSER", "1")
    monkeypatch.setattr(runtime_entry, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(runtime_entry, "configure_environment", lambda root: {})
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda app, **kwargs: seen.update(app=app, **kwargs)),
    )

    runtime_entry.main()

    assert seen["app"] == "app.main:app"
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8010
    assert seen["log_config"] is None


def test_browser_waits_for_runtime_port(monkeypatch):
    checks = iter((False, False, True))
    opened = []
    monkeypatch.setattr(runtime_entry, "_port_open", lambda port: next(checks))
    monkeypatch.setattr(runtime_entry.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(runtime_entry.webbrowser, "open", opened.append)

    runtime_entry._open_browser_when_ready(8010, attempts=3)

    assert opened == ["http://127.0.0.1:8010"]
