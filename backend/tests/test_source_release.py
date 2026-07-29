import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "source_release", ROOT / "scripts" / "source_release.py"
)
assert SPEC and SPEC.loader
source_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_release)


def test_source_target_matrix_covers_native_platforms():
    targets = source_release.load_targets(ROOT / "release" / "source-targets.json")

    assert set(targets) == {
        "windows-x64",
        "linux-x64",
        "macos-arm64",
        "macos-x64",
    }
    assert targets["windows-x64"].python_versions == ("3.10", "3.11", "3.12", "3.13", "3.14")
    assert targets["macos-x64"].python_versions == ("3.10", "3.11", "3.12", "3.13")


def test_runtime_ci_frontend_install_uses_native_online_optional_dependencies(
    monkeypatch, tmp_path,
):
    commands = []
    monkeypatch.setattr(source_release, "_run", lambda command, cwd: commands.append(command))

    source_release.install_native_npm_dependencies(tmp_path, "npm")

    assert commands == [["npm", "ci"]]


def test_source_pip_download_is_binary_only_and_version_scoped(tmp_path):
    target = source_release.load_targets(
        ROOT / "release" / "source-targets.json"
    )["linux-x64"]
    command = source_release.pip_download_command(
        target, ROOT, tmp_path / "wheels", "3.13"
    )

    assert "download" in command
    assert "--only-binary=:all:" in command
    assert command[command.index("--python-version") + 1] == "3.13"
    assert str(ROOT / "backend" / "requirements.txt") in command


def test_source_archives_match_platform_conventions(tmp_path):
    targets = source_release.load_targets(ROOT / "release" / "source-targets.json")

    assert source_release.archive_path(tmp_path, targets["windows-x64"], "v1").suffix == ".zip"
    assert source_release.archive_path(tmp_path, targets["linux-x64"], "v1").name.endswith(".tar.gz")
