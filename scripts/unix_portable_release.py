"""组装 macOS/Linux 开箱即用 Runtime 包。"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from portable_release import APP_NAME, _assemble_layer, _manifest, _safe_target, extract_zip


SUPPORTED_TARGETS = {
    "macos-arm64-full-rag",
    "linux-x64-full-rag",
}
GITHUB_ASSET_LIMIT = 2_000_000_000
VOLUME_SIZE = "1900m"


def extract_tar(archive: Path, destination: Path, *, strip_root: bool) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        members = [member for member in bundle.getmembers() if member.name.strip("/")]
        roots = {member.name.strip("/").split("/", 1)[0] for member in members}
        prefix = next(iter(roots)) + "/" if strip_root and len(roots) == 1 else ""
        for member in members:
            relative = member.name[len(prefix):] if prefix and member.name.startswith(prefix) else member.name
            relative = relative.strip("/")
            if not relative:
                continue
            _safe_target(destination, relative)
            bundle.extract(member.replace(name=relative, deep=False), destination, filter="data")


def extract_archive(archive: Path, destination: Path, *, strip_root: bool) -> None:
    if zipfile.is_zipfile(archive):
        extract_zip(archive, destination, strip_root=strip_root)
        return
    if tarfile.is_tarfile(archive):
        extract_tar(archive, destination, strip_root=strip_root)
        return
    raise RuntimeError(f"不支持的分层归档格式：{archive.name}")


def _target_labels(target: str) -> tuple[str, str, str]:
    os_name, arch, edition = target.split("-", 2)
    platform_label = "macOS" if os_name == "macos" else "Linux"
    arch_label = "ARM64" if arch == "arm64" else "Intel-x64" if os_name == "macos" else "x64"
    if edition != "full-rag":
        raise ValueError(f"终端用户包只支持 Full RAG 目标：{target}")
    return platform_label, arch_label, "Full-RAG"


def build_tree(
    assets_dir: Path, version: str, work_dir: Path, target: str,
) -> tuple[Path, str, dict]:
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"不支持的便携包目标：{target}")
    assets_dir = assets_dir.resolve()
    _, manifest = _manifest(assets_dir, version, target)
    if work_dir.exists():
        shutil.rmtree(work_dir)

    platform_label, arch_label, edition_label = _target_labels(target)
    root_name = f"{APP_NAME}-{version}-{platform_label}-{arch_label}-{edition_label}"
    tree = work_dir / root_name
    runtime = tree / "data" / "runtime"
    layers = manifest["layers"]
    layer_specs = [
        ("base", "base", "base_id"),
        ("application", "apps", "application_id"),
    ]
    if manifest.get("edition") != "full-rag":
        raise RuntimeError("终端用户包只支持 Full RAG Runtime")
    layer_specs.append(("rag", "rag", "rag_id"))
    for name, folder, state_key in layer_specs:
        archive = _assemble_layer(assets_dir, layers[name], work_dir / "archives")
        extract_archive(archive, runtime / folder / str(manifest[state_key]), strip_root=True)

    state = {
        key: manifest.get(key, "")
        for key in (
            "schema_version", "app_version", "target", "edition",
            "base_id", "application_id", "rag_id",
        )
    }
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "current.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    userdata = tree / "data" / "userdata"
    userdata.mkdir(parents=True)
    (userdata / ".keep").write_bytes(b"")

    executable = runtime / "base" / str(manifest["base_id"]) / f"{APP_NAME}-Runtime"
    executable.chmod(executable.stat().st_mode | 0o755)
    script_name = "Start-Demiurge.command" if target.startswith("macos-") else "start-demiurge.sh"
    start_script = tree / script_name
    start_script.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'RUNTIME="$ROOT/data/runtime"\n'
        'export LAF_RUNTIME_ROOT="$RUNTIME"\n'
        'export LAF_RUNTIME_STATE="$RUNTIME/current.json"\n'
        'export LAF_DATA_DIR="$ROOT/data/userdata"\n'
        f'exec "$RUNTIME/base/{manifest["base_id"]}/{APP_NAME}-Runtime"\n',
        encoding="utf-8",
        newline="\n",
    )
    start_script.chmod(0o755)
    return tree, root_name, manifest


def _write_tar(tree: Path, output: Path, root_name: str) -> list[Path]:
    def preserve_executable_mode(member: tarfile.TarInfo) -> tarfile.TarInfo:
        if (
            member.name.endswith((".command", ".sh"))
            or member.name.endswith(f"/{APP_NAME}-Runtime")
        ):
            member.mode |= 0o111
        return member

    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as bundle:
        bundle.add(tree, arcname=root_name, filter=preserve_executable_mode)
    return [output]


def _write_split_7z(tree: Path, output: Path, root_name: str) -> list[Path]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["7z", "a", "-t7z", "-mx=5", f"-v{VOLUME_SIZE}", str(output), root_name],
        cwd=tree.parent,
        check=True,
    )
    volumes = sorted(output.parent.glob(output.name + ".*"))
    if len(volumes) != 2 or any(path.stat().st_size >= GITHUB_ASSET_LIMIT for path in volumes):
        raise RuntimeError(f"Linux 完整版必须生成两个小于 2 GB 的分卷，实际为 {len(volumes)} 个")
    return volumes


def build_bundle(
    assets_dir: Path, version: str, output_dir: Path, work_dir: Path, target: str,
) -> list[Path]:
    tree, root_name, _manifest_payload = build_tree(assets_dir, version, work_dir, target)
    platform_label, arch_label, edition_label = _target_labels(target)
    prefix = f"{APP_NAME}-00-USER-DOWNLOAD-{platform_label}-{arch_label}-{edition_label}-{version}"
    if target == "linux-x64-full-rag":
        return _write_split_7z(tree, output_dir / f"{prefix}.7z", root_name)
    return _write_tar(tree, output_dir / f"{prefix}.tar.gz", root_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--target", required=True, choices=sorted(SUPPORTED_TARGETS))
    args = parser.parse_args()
    for output in build_bundle(
        args.assets_dir, args.version, args.output_dir, args.work_dir, args.target,
    ):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
