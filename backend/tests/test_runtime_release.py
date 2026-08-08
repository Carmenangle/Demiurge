import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "runtime_release", ROOT / "scripts" / "runtime_release.py"
)
assert SPEC and SPEC.loader
runtime_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_release)


def test_runtime_target_matrix_only_publishes_full_rag_platforms():
    targets = runtime_release.load_targets(ROOT / "release" / "runtime-targets.json")

    assert set(targets) == {
        "windows-x64-full-rag",
        "macos-arm64-full-rag",
        "linux-x64-full-rag",
    }
    assert targets["windows-x64-full-rag"].accelerator == "cuda"
    assert targets["macos-arm64-full-rag"].accelerator == "mps"
    assert targets["linux-x64-full-rag"].accelerator == "cuda"
    assert all(target.python_version == "3.13.11" for target in targets.values())


def test_full_rag_keeps_application_outside_frozen_base(tmp_path):
    targets = runtime_release.load_targets(ROOT / "release" / "runtime-targets.json")
    full = runtime_release.pyinstaller_command(
        targets["windows-x64-full-rag"], ROOT, tmp_path
    )

    assert str(ROOT / "release" / "runtime-layered.spec") in full
    spec = (ROOT / "release" / "runtime-layered.spec").read_text(encoding="utf-8")
    assert 'entry[0].startswith("app.")' in spec
    assert "sys.stdlib_module_names" in spec
    assert '"sentence_transformers", "transformers", "torch"' in spec


def test_full_rag_has_content_addressed_base_and_rag_layers():
    targets = runtime_release.load_targets(ROOT / "release" / "runtime-targets.json")
    full = targets["windows-x64-full-rag"]

    assert runtime_release.base_id(ROOT, full)
    assert runtime_release.rag_id(ROOT, full)


def test_base_id_tracks_layout_version(monkeypatch):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    original = runtime_release.base_id(ROOT, target)

    monkeypatch.setattr(
        runtime_release,
        "BASE_LAYOUT_VERSION",
        runtime_release.BASE_LAYOUT_VERSION + 1,
    )

    assert runtime_release.base_id(ROOT, target) != original


def test_windows_base_overwrites_stale_vc_runtime(monkeypatch, tmp_path):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    system32 = tmp_path / "Windows" / "System32"
    system32.mkdir(parents=True)
    required = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
    for name in required:
        (system32 / name).write_bytes(b"current-" + name.encode())
    tree = tmp_path / "runtime"
    internal = tree / "_internal"
    internal.mkdir(parents=True)
    (internal / "msvcp140.dll").write_bytes(b"stale")
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))

    runtime_release.stage_windows_vc_runtime(tree, target)

    for name in required:
        assert (internal / name).read_bytes() == b"current-" + name.encode()


def test_rag_id_tracks_layout_version(monkeypatch):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    original = runtime_release.rag_id(ROOT, target)

    monkeypatch.setattr(
        runtime_release,
        "RAG_LAYOUT_VERSION",
        runtime_release.RAG_LAYOUT_VERSION + 1,
    )

    assert runtime_release.rag_id(ROOT, target) != original


def test_rag_dependencies_and_pinned_torch_install_in_one_secure_transaction(
    monkeypatch, tmp_path,
):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    commands = []
    monkeypatch.setattr(
        runtime_release, "_run",
        lambda command, cwd: commands.append(command),
    )

    runtime_release.build_rag_tree(ROOT, target, tmp_path / "rag")

    assert len(commands) == 1
    command = commands[0]
    assert f"torch=={target.torch_version}" in command
    assert str(ROOT / "backend" / "requirements-reranker.txt") in command
    assert str(ROOT / "release" / "requirements-rag.lock") in command
    assert "tokenizers==0.22.2" in (
        ROOT / "release" / "requirements-rag.lock"
    ).read_text(encoding="utf-8")
    assert "--extra-index-url" in command
    assert command[command.index("--index-url") + 1].startswith("https://")
    assert "--no-compile" in command


def test_build_dependency_installs_override_insecure_machine_pip_config(
    monkeypatch, tmp_path,
):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    commands = []
    monkeypatch.setattr(runtime_release, "_run", lambda command, cwd: commands.append(command))

    runtime_release.install_build_dependencies(ROOT, target)

    assert len(commands) == 2
    assert all(command[command.index("--index-url") + 1].startswith("https://") for command in commands)
    assert any("pyinstaller==" in part for part in commands[1])


def test_runtime_build_rejects_insecure_pypi_override(monkeypatch):
    monkeypatch.setenv("DEMIURGE_PYPI_INDEX_URL", "http://mirror.invalid/simple")

    with pytest.raises(ValueError, match="HTTPS"):
        runtime_release.pip_index_args()


def test_rag_tree_moves_deep_licenses_out_of_site_packages(monkeypatch, tmp_path):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    tree = tmp_path / "rag"

    def fake_run(command, cwd):
        packages = Path(command[command.index("--target") + 1])
        metadata = packages / "torch-2.13.0+cu130.dist-info"
        (metadata / "licenses" / "third_party" / "libs_3rdparty").mkdir(
            parents=True
        )
        (metadata / "METADATA").write_text(
            "Name: torch\nVersion: 2.13.0+cu130\n", encoding="utf-8"
        )
        (metadata / "licenses" / "third_party" / "libs_3rdparty" / "LICENSE").write_text(
            "license", encoding="utf-8"
        )

    monkeypatch.setattr(runtime_release, "_run", fake_run)

    runtime_release.build_rag_tree(ROOT, target, tree)

    assert not (
        tree / "site-packages" / "torch-2.13.0+cu130.dist-info" / "licenses"
    ).exists()
    assert (
        tree / "licenses" / "torch-2.13.0+cu130"
        / "third_party" / "libs_3rdparty" / "LICENSE"
    ).read_text(encoding="utf-8") == "license"


def test_runtime_environment_uses_writable_data_without_bundled_models(tmp_path):
    env = runtime_release.runtime_environment(tmp_path, "full-rag")

    assert env["LAF_DATA_DIR"] == str(tmp_path / "data")
    assert env["LAF_FRONTEND_DIST"] == str(tmp_path / "frontend")
    assert env["LAF_COMFY_EXT_DIR"] == str(tmp_path / "comfyui-ext")
    assert "LAF_BUNDLED_RERANKER_DIR" not in env


def test_full_rag_tree_does_not_require_bundled_model_weights(tmp_path):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["macos-arm64-full-rag"]
    tree = tmp_path / "runtime"
    (tree / "frontend").mkdir(parents=True)
    (tree / "frontend" / "index.html").write_text("ok", encoding="utf-8")
    (tree / target.executable_name).write_text("bin", encoding="utf-8")
    assert runtime_release.validate_runtime_tree(tree, target) == []


def test_runtime_targets_do_not_pin_or_download_model_weights():
    manifest = json.loads(
        (ROOT / "release" / "runtime-targets.json").read_text(encoding="utf-8")
    )
    workflow = (ROOT / ".github" / "workflows" / "runtime-release.yml").read_text(
        encoding="utf-8"
    )

    assert "reranker" not in manifest
    assert "huggingface" not in workflow.lower()
    assert "缓存完整 RAG 权重" not in workflow


def test_split_asset_writes_reassembly_manifest(tmp_path):
    archive = tmp_path / "runtime.zip"
    archive.write_bytes(b"0123456789")

    parts = runtime_release.split_asset(archive, max_part_bytes=4)
    manifest = json.loads(
        (tmp_path / "runtime.zip.parts.json").read_text(encoding="utf-8")
    )

    assert [part.read_bytes() for part in parts] == [b"0123", b"4567", b"89"]
    assert manifest["archive"] == "runtime.zip"
    assert manifest["parts"] == [part.name for part in parts]
    assert manifest["sha256"] == runtime_release.sha256_file(archive)


def test_content_addressed_directory_promotion_retries_windows_lock(
    monkeypatch, tmp_path,
):
    source = tmp_path / "pending"
    destination = tmp_path / "content-id"
    source.mkdir()
    original_rename = Path.rename
    calls = {"count": 0}

    def flaky_rename(path, target):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("temporarily locked")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    monkeypatch.setattr(runtime_release.time, "sleep", lambda _seconds: None)

    runtime_release.promote_directory(source, destination)

    assert calls["count"] == 2
    assert destination.is_dir()


def test_content_addressed_directory_promotion_copies_after_persistent_windows_lock(
    monkeypatch, tmp_path,
):
    source = tmp_path / "pending"
    destination = tmp_path / "content-id"
    source.mkdir()
    (source / "manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(Path, "rename", lambda _path, _target: (_ for _ in ()).throw(
        PermissionError("persistently locked")
    ))
    monkeypatch.setattr(runtime_release.time, "sleep", lambda _seconds: None)

    runtime_release.promote_directory(source, destination, attempts=2)

    assert (destination / "manifest.json").read_text(encoding="utf-8") == "{}"


def test_npm_executable_resolves_windows_command_wrapper(monkeypatch):
    monkeypatch.setattr(
        runtime_release.shutil,
        "which",
        lambda name: "C:/node/npm.cmd" if name == "npm.cmd" else None,
    )
    assert runtime_release.npm_executable() == "C:/node/npm.cmd"


def test_runtime_ci_does_not_reuse_foreign_offline_npm_cache(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(runtime_release, "_run", lambda command, cwd: commands.append(command))

    runtime_release.install_frontend_dependencies(
        tmp_path, tmp_path / "frontend", "npm", prefer_offline=False
    )

    assert commands == [["npm", "ci"]]


def test_runtime_self_check_resolves_relative_tree_before_changing_cwd(
    monkeypatch, tmp_path,
):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["linux-x64-full-rag"]
    tree = tmp_path / "runtime"
    tree.mkdir()
    (tree / target.executable_name).touch()
    seen = {}

    class Result:
        returncode = 0
        stdout = json.dumps({
            "status": "ok",
            "torch_version": target.torch_version,
            "torch_cuda": "13.0",
        })
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        seen["env"] = kwargs["env"]
        return Result()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_release.subprocess, "run", fake_run)
    runtime_release.run_runtime_self_check(Path("runtime"), target)

    assert Path(seen["command"][0]).is_absolute()
    assert Path(seen["cwd"]).is_absolute()
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_cuda_runtime_self_check_rejects_cpu_torch(monkeypatch, tmp_path):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    tree = tmp_path / "runtime"
    tree.mkdir()
    (tree / target.executable_name).touch()

    class Result:
        returncode = 0
        stdout = json.dumps({
            "status": "ok",
            "torch_version": "2.13.0+cpu",
            "torch_cuda": None,
        })
        stderr = ""

    monkeypatch.setattr(runtime_release.subprocess, "run", lambda *args, **kwargs: Result())

    with pytest.raises(RuntimeError, match="CUDA Torch 版本不匹配"):
        runtime_release.run_runtime_self_check(tree, target)


def test_application_layer_keeps_backend_source_visible(tmp_path):
    root = tmp_path / "project"
    (root / "backend" / "app").mkdir(parents=True)
    (root / "backend" / "app" / "main.py").write_text("app = None", encoding="utf-8")
    (root / "comfyui-ext").mkdir()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    tree = tmp_path / "application"

    runtime_release.build_application_tree(root, frontend, tree, "v0.15")

    assert (tree / "backend" / "app" / "main.py").is_file()
    assert not (tree / "backend.zip").exists()


def test_reusable_layers_require_matching_ids_and_verified_assets(tmp_path):
    target = runtime_release.load_targets(
        ROOT / "release" / "runtime-targets.json"
    )["windows-x64-full-rag"]
    base = tmp_path / "base.zip"
    rag = tmp_path / "rag.zip.part01"
    base.write_bytes(b"base")
    rag.write_bytes(b"rag")
    layers = {
        "base": {"archive": "base.zip", "assets": [{
            "name": base.name, "size": base.stat().st_size,
            "sha256": runtime_release.sha256_file(base),
        }]},
        "rag": {"archive": "rag.zip", "definition_id": "rag-definition", "assets": [{
            "name": rag.name, "size": rag.stat().st_size,
            "sha256": runtime_release.sha256_file(rag),
        }]},
    }
    runtime_release.write_json(tmp_path / f"Demiurge-update-old-{target.id}.json", {
        "base_id": "base-id", "rag_id": "rag-content", "layers": layers,
    })

    assert runtime_release.reusable_layers(
        tmp_path, target, "base-id", "rag-definition"
    ) == layers
    rag.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="大小错误|校验失败"):
        runtime_release.reusable_layers(tmp_path, target, "base-id", "rag-definition")
