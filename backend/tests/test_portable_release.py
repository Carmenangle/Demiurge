import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "portable_release", ROOT / "scripts" / "portable_release.py"
)
assert SPEC and SPEC.loader
portable_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portable_release)


def _layer(assets: Path, name: str, root: str, files: dict[str, bytes]) -> dict:
    archive = assets / name
    with zipfile.ZipFile(archive, "w") as bundle:
        for relative, payload in files.items():
            bundle.writestr(f"{root}/{relative}", payload)
    return {
        "id": root.split("-", 1)[1],
        "archive": name,
        "size": archive.stat().st_size,
        "sha256": portable_release.sha256_file(archive),
        "assets": [{
            "name": name,
            "size": archive.stat().st_size,
            "sha256": portable_release.sha256_file(archive),
        }],
    }


def test_standard_manifest_is_rejected_for_terminal_user_bundle(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    base = _layer(
        assets, "base.zip", "base-base-a",
        {"Demiurge-Runtime.exe": b"runtime", "_internal/python313.dll": b"py"},
    )
    application = _layer(
        assets, "application.zip", "application-app-a",
        {"backend/app/main.py": b"app = None", "frontend/index.html": b"ok"},
    )
    manifest = {
        "schema_version": 2,
        "app_version": "v0.15",
        "target": "windows-x64-standard",
        "edition": "standard",
        "base_id": "base-a",
        "application_id": "app-a",
        "rag_id": "",
        "layers": {"base": base, "application": application},
    }
    (assets / "Demiurge_update_windows_v0.15.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    mingit = tmp_path / "mingit.zip"
    with zipfile.ZipFile(mingit, "w") as bundle:
        bundle.writestr("cmd/git.exe", b"git")
        bundle.writestr("COPYING", b"license")

    with pytest.raises(RuntimeError, match="只支持 Full RAG"):
        portable_release.build_portable(
            assets, mingit, "v0.15", tmp_path / "out", tmp_path / "work",
            "windows-x64-standard",
        )


def test_full_rag_portable_uses_single_7z_archive(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    base = _layer(assets, "base.zip", "base-base-a", {"Demiurge-Runtime.exe": b"runtime"})
    application = _layer(assets, "application.zip", "application-app-a", {"backend/app/main.py": b"app = None"})
    rag = _layer(assets, "rag.zip", "rag-rag-a", {"site-packages/torch/__init__.py": b""})
    manifest = {
        "schema_version": 2,
        "app_version": "v0.15",
        "target": "windows-x64-full-rag",
        "edition": "full-rag",
        "base_id": "base-a",
        "application_id": "app-a",
        "rag_id": "rag-a",
        "layers": {"base": base, "application": application, "rag": rag},
    }
    (assets / "Demiurge_update_windows_v0.15.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    mingit = tmp_path / "mingit.zip"
    with zipfile.ZipFile(mingit, "w") as bundle:
        bundle.writestr("cmd/git.exe", b"git")
    desktop = tmp_path / "desktop-publish"
    (desktop / "runtimes" / "win-x64").mkdir(parents=True)
    for name in ("Demiurge.exe", "Demiurge.dll", "runtimes/win-x64/WebView2Loader.dll"):
        (desktop / name).write_bytes(b"shell")
    (desktop / "Demiurge.pdb").write_bytes(b"symbols")

    seen = {}

    def fake_run(command, cwd, check):
        seen["command"] = command
        seen["cwd"] = cwd
        archive = Path(command[-2])
        assert archive.is_absolute()
        source = Path(cwd) / command[-1]
        assert (
            source / "data" / "runtime" / "rag" / "rag-a"
            / "site-packages" / "torch" / "__init__.py"
        ).is_file()
        assert (source / "start-dev.bat").is_file()
        assert (source / "stop-dev.bat").is_file()
        assert (source / "scripts" / "start-portable.ps1").is_file()
        assert (source / "scripts" / "stop-portable.ps1").is_file()
        start_script = (source / "scripts" / "start-portable.ps1").read_text(
            encoding="utf-8"
        )
        assert "DEMIURGE_PORT" in start_script
        assert "LAF_RUNTIME_PORT" in start_script
        assert (source / "install-shortcut.bat").is_file()
        assert (source / "scripts" / "install-shortcut.ps1").is_file()
        assert (source / "Demiurge.exe").is_file()
        assert (source / "Demiurge.dll").is_file()
        assert (source / "runtimes" / "win-x64" / "WebView2Loader.dll").is_file()
        assert not (source / "Demiurge.pdb").exists()
        state = json.loads((source / "data" / "runtime" / "current.json").read_text())
        assert state["edition"] == "full-rag"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"archive")

    monkeypatch.setattr(portable_release.subprocess, "run", fake_run)
    outputs = portable_release.build_portable(
        assets, mingit, "v0.15", Path("out"), tmp_path / "work",
        "windows-x64-full-rag", desktop,
    )

    assert [output.suffix for output in outputs] == [".7z"]
    assert not any(part.startswith("-v") for part in seen["command"])
    assert seen["command"][-1] == "Demiurge"


def _full_rag_assets(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    base = _layer(assets, "base.zip", "base-base-a", {"Demiurge-Runtime.exe": b"runtime"})
    application = _layer(assets, "application.zip", "application-app-a", {"backend/app/main.py": b""})
    rag = _layer(assets, "rag.zip", "rag-rag-a", {"site-packages/torch/__init__.py": b""})
    (assets / "Demiurge_update_windows_v0.15.json").write_text(
        json.dumps({
            "schema_version": 2,
            "app_version": "v0.15",
            "target": "windows-x64-full-rag",
            "edition": "full-rag",
            "base_id": "base-a",
            "application_id": "app-a",
            "rag_id": "rag-a",
            "layers": {"base": base, "application": application, "rag": rag},
        }),
        encoding="utf-8",
    )
    mingit = tmp_path / "mingit.zip"
    with zipfile.ZipFile(mingit, "w") as bundle:
        bundle.writestr("cmd/git.exe", b"git")
    return assets, mingit


def test_desktop_shell_is_mandatory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portable_release.subprocess, "run", lambda *a, **k: None)
    assets, mingit = _full_rag_assets(tmp_path)

    with pytest.raises(RuntimeError, match="必须带桌面壳"):
        portable_release.build_portable(
            assets, mingit, "v0.15", Path("out"), tmp_path / "work",
            "windows-x64-full-rag",
        )


def test_desktop_payload_without_exe_is_rejected(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portable_release.subprocess, "run", lambda *a, **k: None)
    assets, mingit = _full_rag_assets(tmp_path)
    desktop = tmp_path / "desktop-publish"
    desktop.mkdir()

    with pytest.raises(RuntimeError, match="Demiurge.exe"):
        portable_release.build_portable(
            assets, mingit, "v0.15", Path("out"), tmp_path / "work",
            "windows-x64-full-rag", desktop,
        )


def test_desktop_payload_conflicting_with_package_tree_is_rejected(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portable_release.subprocess, "run", lambda *a, **k: None)
    assets, mingit = _full_rag_assets(tmp_path)
    desktop = tmp_path / "desktop-publish"
    (desktop / "data" / "runtime").mkdir(parents=True)
    (desktop / "Demiurge.exe").write_bytes(b"shell")
    (desktop / "data" / "runtime" / "current.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="冲突"):
        portable_release.build_portable(
            assets, mingit, "v0.15", Path("out"), tmp_path / "work",
            "windows-x64-full-rag", desktop,
        )


def test_tauri_desktop_payload_lands_as_demiurge_exe(monkeypatch, tmp_path):
    """Tauri 壳产物（demiurge-shell.exe）也要能装，且包内落名 Demiurge.exe。

    换壳不该改包内路径：启动器、快捷方式与「壳向上找 data/runtime/current.json」只认
    Demiurge.exe。
    """
    monkeypatch.chdir(tmp_path)
    assets, mingit = _full_rag_assets(tmp_path)
    desktop = tmp_path / "tauri-release"
    desktop.mkdir()
    (desktop / "demiurge-shell.exe").write_bytes(b"tauri-shell")
    (desktop / "demiurge-shell.pdb").write_bytes(b"symbols")

    seen = {}

    def fake_run(command, cwd, check):
        seen["command"] = command
        source = Path(cwd) / command[-1]
        assert (source / "Demiurge.exe").read_bytes() == b"tauri-shell"
        assert not (source / "demiurge-shell.exe").exists()
        assert not (source / "demiurge-shell.pdb").exists()
        archive = Path(command[-2])
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"archive")

    monkeypatch.setattr(portable_release.subprocess, "run", fake_run)
    portable_release.build_portable(
        assets, mingit, "v0.15", Path("out"), tmp_path / "work",
        "windows-x64-full-rag", desktop,
    )
    assert [output.suffix for output in [Path(seen["command"][-2])]] == [".7z"]


def test_seven_zip_resolves_standard_windows_install(monkeypatch, tmp_path):
    if portable_release.os.name != "nt":
        pytest.skip("Windows path discovery")
    executable = tmp_path / "7-Zip" / "7z.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"7z")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("SEVEN_ZIP", raising=False)
    monkeypatch.setattr(portable_release.shutil, "which", lambda _name: None)

    assert portable_release.seven_zip_executable() == str(executable)
