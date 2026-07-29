import hashlib
import importlib.util
import json
import sys
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "unix_portable_release", ROOT / "scripts" / "unix_portable_release.py"
)
assert SPEC and SPEC.loader
unix_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(unix_release)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layer(assets: Path, name: str, root: str, files: dict[str, bytes]) -> dict:
    archive = assets / name
    source = assets / "source"
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive, "w") as bundle:
            for relative, payload in files.items():
                bundle.writestr(f"{root}/{relative}", payload)
    else:
        with tarfile.open(archive, "w:gz") as bundle:
            for relative, payload in files.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                bundle.add(path, arcname=f"{root}/{relative}")
    return {
        "id": root.split("-", 1)[1],
        "archive": name,
        "size": archive.stat().st_size,
        "sha256": _sha256(archive),
        "assets": [{
            "name": name,
            "size": archive.stat().st_size,
            "sha256": _sha256(archive),
        }],
    }


def _assets(tmp_path: Path, target: str, edition: str) -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    base = _layer(
        assets, "base.tar.gz", "base-base-a",
        {"ComfyUI-Wrapping-paper-Runtime": b"runtime"},
    )
    application = _layer(
        assets, "application.zip", "application-app-a",
        {"backend/app/main.py": b"app = None"},
    )
    layers = {"base": base, "application": application}
    rag_id = ""
    if edition == "full-rag":
        layers["rag"] = _layer(
            assets, "rag.tar.gz", "rag-rag-a",
            {"site-packages/torch/__init__.py": b""},
        )
        rag_id = "rag-a"
    manifest = {
        "schema_version": 2,
        "app_version": "v0.15",
        "target": target,
        "edition": edition,
        "base_id": "base-a",
        "application_id": "app-a",
        "rag_id": rag_id,
        "layers": layers,
    }
    (assets / f"ComfyUI-Wrapping-paper-update-v0.15-{target}.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return assets


def test_macos_bundle_contains_runtime_rag_and_clickable_start_file(tmp_path):
    assets = _assets(tmp_path, "macos-arm64-full-rag", "full-rag")
    outputs = unix_release.build_bundle(
        assets, "v0.15", tmp_path / "out", tmp_path / "work",
        "macos-arm64-full-rag",
    )

    assert outputs[0].name == "ComfyUI-Wrapping-paper-00-USER-DOWNLOAD-macOS-ARM64-Full-RAG-v0.15.tar.gz"
    with tarfile.open(outputs[0], "r:gz") as bundle:
        names = set(bundle.getnames())
        start = next(member for member in bundle.getmembers() if member.name.endswith("Start-ComfyUI.command"))
    assert any(name.endswith("data/runtime/rag/rag-a/site-packages/torch/__init__.py") for name in names)
    assert start.mode & 0o111


def test_linux_standard_bundle_contains_shell_start_file(tmp_path):
    assets = _assets(tmp_path, "linux-x64-standard", "standard")
    outputs = unix_release.build_bundle(
        assets, "v0.15", tmp_path / "out", tmp_path / "work",
        "linux-x64-standard",
    )

    assert outputs[0].name == "ComfyUI-Wrapping-paper-00-USER-DOWNLOAD-Linux-x64-Standard-v0.15.tar.gz"
    with tarfile.open(outputs[0], "r:gz") as bundle:
        names = set(bundle.getnames())
    assert any(name.endswith("start-comfyui.sh") for name in names)


def test_linux_full_rag_uses_two_7z_volumes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    tree = tmp_path / "work" / "bundle"
    tree.mkdir(parents=True)
    output = Path("out") / "bundle.7z"
    seen = {}

    def fake_run(command, cwd, check):
        seen["command"] = command
        seen["cwd"] = cwd
        archive = Path(command[-2])
        if not archive.is_absolute():
            archive = Path(cwd) / archive
        archive.parent.mkdir(parents=True, exist_ok=True)
        (archive.parent / "bundle.7z.001").write_bytes(b"one")
        (archive.parent / "bundle.7z.002").write_bytes(b"two")

    monkeypatch.setattr(unix_release.subprocess, "run", fake_run)
    volumes = unix_release._write_split_7z(tree, output, "bundle")

    assert [path.suffix for path in volumes] == [".001", ".002"]
    assert "-v1900m" in seen["command"]
    assert Path(seen["command"][-2]).is_absolute()
    assert seen["cwd"] == tree.parent
