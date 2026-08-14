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


def test_rag_layer_installs_owner_finder_before_frozen_importers(
    monkeypatch, tmp_path: Path,
):
    packages = tmp_path / "rag" / "layer-1" / "site-packages"
    packages.mkdir(parents=True)
    (tmp_path / "current.json").write_text(
        json.dumps({"edition": "full-rag", "rag_id": "layer-1"}),
        encoding="utf-8",
    )
    original_meta_path = list(runtime_entry.sys.meta_path)
    monkeypatch.setattr(runtime_entry.sys, "meta_path", original_meta_path.copy())

    runtime_entry.configure_environment(tmp_path)

    finder = runtime_entry.sys.meta_path[0]
    assert isinstance(finder, runtime_entry._ExternalRagFinder)
    assert finder.packages == packages.resolve()


def test_external_rag_finder_only_claims_owned_package(monkeypatch, tmp_path: Path):
    finder = runtime_entry._ExternalRagFinder(tmp_path)
    calls = []
    monkeypatch.setattr(
        runtime_entry.PathFinder, "find_spec",
        lambda fullname, path, target=None: calls.append((fullname, path)) or "spec",
    )

    assert finder.find_spec("torch.autograd", ["torch-path"]) == "spec"
    assert finder.find_spec("app.main", ["app-path"]) is None
    assert calls == [("torch.autograd", ["torch-path"])]


def test_full_rag_self_check_reports_torch_build(monkeypatch, capsys):
    class TorchVersion:
        cuda = "13.0"

    class Torch:
        __version__ = "2.13.0+cu130"
        version = TorchVersion()

    monkeypatch.setenv("LAF_RUNTIME_EDITION", "full-rag")
    imported = []
    monkeypatch.setattr(
        runtime_entry.importlib, "import_module",
        lambda name: imported.append(name) or (Torch() if name == "torch" else object()),
    )

    runtime_entry.self_check()

    payload = json.loads(capsys.readouterr().out)
    assert payload["torch_version"] == "2.13.0+cu130"
    assert payload["torch_cuda"] == "13.0"
    assert imported[:3] == ["torch", "transformers", "sentence_transformers"]


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


def test_packaged_main_honors_runtime_port_override(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("LAF_NO_BROWSER", "1")
    monkeypatch.setenv("LAF_RUNTIME_PORT", "18111")
    monkeypatch.setattr(runtime_entry, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(runtime_entry, "configure_environment", lambda root: {})
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda app, **kwargs: seen.update(app=app, **kwargs)),
    )

    runtime_entry.main()

    assert seen["port"] == 18111


def test_runtime_port_rejects_invalid_override(monkeypatch):
    monkeypatch.setenv("LAF_RUNTIME_PORT", "70000")

    try:
        runtime_entry.runtime_port()
    except ValueError as exc:
        assert "out of range" in str(exc)
    else:
        raise AssertionError("invalid port should be rejected")


def test_browser_waits_for_runtime_port(monkeypatch):
    checks = iter((False, False, True))
    opened = []
    monkeypatch.setattr(runtime_entry, "_port_open", lambda port: next(checks))
    monkeypatch.setattr(runtime_entry.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(runtime_entry.webbrowser, "open", opened.append)

    runtime_entry._open_browser_when_ready(8010, attempts=3)

    assert opened == ["http://127.0.0.1:8010"]
