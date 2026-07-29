"""终端用户启动器（scripts/launcher.py）关键逻辑测试。
覆盖：版本检测（manifest 优先 / 未装判定）与下载取消链路。
tkinter 仅在实例化窗口时才需要显示，模块级 import 在无头环境可用。
"""
import importlib.util
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("launcher", ROOT / "scripts" / "launcher.py")
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


@pytest.fixture
def in_launcher_dir(monkeypatch, tmp_path):
    """把 launcher_dir 指到 tmp_path，隔离文件系统副作用。"""
    monkeypatch.setattr(launcher, "launcher_dir", lambda: tmp_path)
    return tmp_path


def _cfg():
    return {
        "app_exe": "ComfyUI-Wrapping-paper-Runtime.exe",
        "runtime_dir": "data/runtime",
        "data_dir": "data/userdata",
    }


def test_installed_version_empty_when_nothing_installed(in_launcher_dir):
    assert launcher.installed_version(_cfg()) == ""
    assert launcher.runtime_installed(_cfg()) is False


def test_installed_version_reads_runtime_manifest_first(in_launcher_dir):
    rt = in_launcher_dir / "data" / "runtime"
    rt.mkdir(parents=True)
    (rt / "runtime-manifest.json").write_text(
        json.dumps({"app_version": "0.14.2"}), encoding="utf-8"
    )
    (in_launcher_dir / "version.txt").write_text("0.1.0", encoding="utf-8")

    # manifest 优先于旧 version.txt
    assert launcher.installed_version(_cfg()) == "0.14.2"


def test_installed_version_falls_back_to_version_txt(in_launcher_dir):
    (in_launcher_dir / "version.txt").write_text("0.9.9", encoding="utf-8")
    assert launcher.installed_version(_cfg()) == "0.9.9"


def test_runtime_installed_true_when_exe_present(in_launcher_dir):
    rt = in_launcher_dir / "data" / "runtime"
    rt.mkdir(parents=True)
    (rt / "ComfyUI-Wrapping-paper-Runtime.exe").write_text("bin", encoding="utf-8")
    assert launcher.runtime_installed(_cfg()) is True


def test_settings_are_saved_as_utf8_json(in_launcher_dir):
    cfg = _cfg() | {
        "auto_update": False,
        "auto_start": True,
        "close_to_tray": True,
        "label": "后台运行",
    }

    launcher.save_config(cfg)

    saved = json.loads((in_launcher_dir / "data" / "launcher-settings.json").read_text(encoding="utf-8"))
    assert saved == {
        "auto_update": False,
        "auto_start": True,
        "close_to_tray": True,
    }


def test_saved_settings_override_distributed_config(in_launcher_dir):
    (in_launcher_dir / "launcher-config.json").write_text(
        json.dumps({"auto_update": True, "edition": "standard"}), encoding="utf-8"
    )
    settings = in_launcher_dir / "data" / "launcher-settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"auto_update": False, "edition": "full-rag"}), encoding="utf-8"
    )

    cfg = launcher.load_config()

    assert cfg["auto_update"] is False
    assert cfg["edition"] == "full-rag"


def test_portable_full_rag_state_sets_edition_without_saved_preference(
    in_launcher_dir,
):
    runtime = in_launcher_dir / "data" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "current.json").write_text(
        json.dumps({"schema_version": 2, "edition": "full-rag"}),
        encoding="utf-8",
    )

    cfg = launcher.load_config()

    assert cfg["edition"] == "full-rag"


def test_layered_runtime_never_falls_back_to_whole_package_update(in_launcher_dir):
    cfg = _cfg()
    runtime = in_launcher_dir / "data" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "current.json").write_text(
        json.dumps({"schema_version": 2, "edition": "full-rag"}),
        encoding="utf-8",
    )

    assert launcher.legacy_full_update_allowed(cfg) is False


def test_disabling_auto_update_never_checks_without_explicit_action(in_launcher_dir):
    cfg = _cfg() | {"auto_update": False}
    assert launcher.should_check_updates(cfg) is False
    assert launcher.should_check_updates(cfg, force=True) is True

    rt = in_launcher_dir / "data" / "runtime"
    rt.mkdir(parents=True)
    (rt / cfg["app_exe"]).write_text("bin", encoding="utf-8")
    assert launcher.should_check_updates(cfg) is False


def test_missing_runtime_uses_install_action(in_launcher_dir):
    cfg = _cfg()
    assert launcher.primary_action(cfg) == "install"

    rt = in_launcher_dir / "data" / "runtime"
    rt.mkdir(parents=True)
    (rt / cfg["app_exe"]).write_text("bin", encoding="utf-8")
    assert launcher.primary_action(cfg) == "start"


def test_existing_source_project_uses_start_action(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.platform, "system", lambda: "Windows")
    project = tmp_path / "project"
    launcher_output = project / "release-assets"
    launcher_output.mkdir(parents=True)
    (project / "backend" / "app").mkdir(parents=True)
    (project / "backend" / "app" / "main.py").write_text("app = None", encoding="utf-8")
    python = project / "backend" / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("bin", encoding="utf-8")
    frontend = project / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    (project / "comfyui-ext").mkdir()
    monkeypatch.setattr(launcher, "launcher_dir", lambda: launcher_output)

    assert launcher.runtime_installed(_cfg()) is False
    assert launcher.source_project_root() == project
    assert launcher.primary_action(_cfg()) == "start"
    command, cwd, env = launcher.launch_spec(_cfg())
    assert command[:3] == [str(python), "-m", "uvicorn"]
    assert cwd == project / "backend"
    assert env["LAF_DATA_DIR"] == str(project / "backend" / "data")
    assert env["LAF_FRONTEND_DIST"] == str(frontend)


def test_github_api_rate_limit_falls_back_to_public_release(monkeypatch):
    expected = {"tag_name": "v1.2.3", "assets": []}

    def rate_limited(_repo):
        raise urllib.error.HTTPError("https://api.github.com", 403, "rate limit", {}, None)

    monkeypatch.setattr(launcher, "_fetch_github_api_release", rate_limited)
    monkeypatch.setattr(
        launcher,
        "_fetch_release_without_api",
        lambda repo, edition: expected,
    )

    assert launcher.fetch_latest_release("owner/repo", "standard") == expected


def test_portable_git_is_prepended_to_child_path(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "launcher_dir", lambda: tmp_path)
    git_cmd = tmp_path / "dependencies" / "git" / "cmd"
    git_cmd.mkdir(parents=True)

    env = launcher.with_portable_git({"PATH": "C:\\Windows"})

    assert env["PATH"].split(launcher.os.pathsep)[0] == str(git_cmd)


def test_window_and_tray_icon_uses_packaged_exe_icon():
    icon = launcher.app_icon_path()
    assert icon.name == "app-icon.png"
    assert icon.is_file()

    source = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    assert 'Image.open(app_icon_path())' in source
    assert 'Image.new("RGBA"' not in source


def test_legacy_full_rag_parts_are_selected_without_standard_fallback(monkeypatch):
    monkeypatch.setattr(launcher.platform, "system", lambda: "Windows")
    monkeypatch.setattr(launcher.platform, "machine", lambda: "AMD64")
    assets = [
        {"name": "tool-windows-x64-standard.zip"},
        {"name": "tool-windows-x64-full-rag.zip.part01"},
        {"name": "tool-windows-x64-full-rag.zip.parts.json"},
    ]

    assert launcher.pick_asset(assets, "full-rag") is None
    assert launcher.pick_parts_manifest(assets, "full-rag")["name"].endswith(".parts.json")


def test_downloader_strips_common_archive_root(tmp_path):
    archive = tmp_path / "layer.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("application-a/backend.zip", b"backend")
        bundle.writestr("application-a/frontend/index.html", b"ok")

    extracted = tmp_path / "extracted"
    launcher.Downloader().extract(archive, extracted)

    assert (extracted / "backend.zip").read_bytes() == b"backend"
    assert (extracted / "frontend" / "index.html").read_bytes() == b"ok"


def test_layered_state_resolves_version_executable_and_update_plan(in_launcher_dir):
    cfg = _cfg()
    rt = in_launcher_dir / "data" / "runtime"
    state = {
        "schema_version": 2,
        "app_version": "1.2.3",
        "base_id": "base-a",
        "application_id": "app-a",
        "rag_id": "",
    }
    (rt / "base" / "base-a").mkdir(parents=True)
    (rt / "base" / "base-a" / cfg["app_exe"]).write_text("bin", encoding="utf-8")
    (rt / "apps" / "app-a" / "frontend").mkdir(parents=True)
    (rt / "apps" / "app-a" / "backend.zip").write_bytes(b"zip")
    (rt / "apps" / "app-a" / "frontend" / "index.html").write_text("ok", encoding="utf-8")
    (rt / "current.json").write_text(json.dumps(state), encoding="utf-8")
    manifest = {
        "layers": {
            "base": {"id": "base-a"},
            "application": {"id": "app-b"},
        }
    }

    assert launcher.installed_version(cfg) == "1.2.3"
    assert launcher.runtime_installed(cfg) is True
    assert launcher.runtime_executable(cfg).parent.name == "base-a"
    assert launcher.layer_update_plan(cfg, manifest) == ["application"]


def test_write_current_state_keeps_previous_for_rollback(in_launcher_dir):
    cfg = _cfg()
    rt = in_launcher_dir / "data" / "runtime"
    rt.mkdir(parents=True)
    old = {"schema_version": 2, "app_version": "1"}
    new = {"schema_version": 2, "app_version": "2"}
    (rt / "current.json").write_text(json.dumps(old), encoding="utf-8")

    launcher.write_current_state(cfg, new)

    assert json.loads((rt / "current.json").read_text(encoding="utf-8")) == new
    assert json.loads((rt / "current.previous.json").read_text(encoding="utf-8")) == old


def test_downloader_cancel_stops_download_loop(monkeypatch, tmp_path):
    """cancel() 后 download() 应在下一个 chunk 抛 DownloadCancelled。"""
    dl = launcher.Downloader()

    class FakeResp:
        def __init__(self):
            self._chunks = [b"x" * 1024] * 1000
        def getheader(self, _):
            return str(1024 * 1000)
        def read(self, _n):
            return self._chunks.pop(0) if self._chunks else b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    calls = {"n": 0}

    def on_progress(done, total, fn):
        calls["n"] += 1
        if calls["n"] == 1:
            dl.cancel()  # 第一个 chunk 后取消

    with pytest.raises(launcher.DownloadCancelled):
        dl.download("http://x/a.zip", tmp_path / "a.zip", on_progress)
    # 取消后没有继续跑完 1000 个 chunk
    assert calls["n"] < 5
