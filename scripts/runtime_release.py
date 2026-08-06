"""分层 Runtime 发布：Base、Application、RAG、更新清单。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse


GITHUB_ASSET_LIMIT = 1_900_000_000
APP_NAME = "Demiurge"
RUNTIME_NAME = "Demiurge-Runtime"
BASE_LAYOUT_VERSION = 2
RAG_LAYOUT_VERSION = 2
DEFAULT_PYPI_INDEX = "https://mirrors.aliyun.com/pypi/simple/"


class RuntimeTarget(NamedTuple):
    id: str
    os: str
    arch: str
    edition: str
    runner: str
    accelerator: str
    python_version: str
    pyinstaller_version: str
    torch_version: str = ""
    torch_index_url: str = ""

    @property
    def executable_name(self) -> str:
        return RUNTIME_NAME + (".exe" if self.os == "windows" else "")

    @property
    def full_rag(self) -> bool:
        return self.edition == "full-rag"


def load_targets(path: Path) -> dict[str, RuntimeTarget]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("不支持的 Runtime 目标清单版本")
    targets: dict[str, RuntimeTarget] = {}
    for item in raw["targets"]:
        target = RuntimeTarget(
            id=item["id"], os=item["os"], arch=item["arch"],
            edition=item["edition"], runner=item["runner"],
            accelerator=item["accelerator"],
            python_version=raw["python_version"],
            pyinstaller_version=raw["pyinstaller_version"],
            torch_version=item.get("torch_version", ""),
            torch_index_url=item.get("torch_index_url", ""),
        )
        if target.id in targets:
            raise ValueError(f"Runtime 目标重复：{target.id}")
        if target.full_rag and not target.torch_version:
            raise ValueError(f"完整 RAG 目标缺少固定 Torch 版本：{target.id}")
        targets[target.id] = target
    return targets


def runtime_environment(root: Path, edition: str) -> dict[str, str]:
    root = root.resolve()
    return {
        "LAF_RUNTIME_ROOT": str(root),
        "LAF_RUNTIME_EDITION": edition,
        "LAF_DATA_DIR": str(root / "data"),
        "LAF_FRONTEND_DIST": str(root / "frontend"),
        "LAF_COMFY_EXT_DIR": str(root / "comfyui-ext"),
    }


def _runtime_icon(target: RuntimeTarget, root: Path) -> Path | None:
    sys.path.insert(0, str(root / "scripts"))
    try:
        from generate_app_icon import generate, icon_for_os
    except ImportError:
        return None
    icon = icon_for_os(target.os)
    if icon is None and target.os in ("windows", "macos"):
        try:
            generate()
            icon = icon_for_os(target.os)
        except Exception as exc:  # noqa: BLE001
            print(f"跳过图标（{exc}）")
    return icon


def pyinstaller_command(target: RuntimeTarget, root: Path, work_dir: Path) -> list[str]:
    return [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(work_dir / "dist"),
        "--workpath", str(work_dir / "build"),
        str(root / "release" / "runtime-layered.spec"),
    ]


def pyinstaller_environment(target: RuntimeTarget, root: Path, work_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "LAF_BUILD_ROOT": str(root.resolve()),
        "LAF_BUILD_WORK_DIR": str(work_dir.resolve()),
        "LAF_BUILD_RUNTIME_NAME": RUNTIME_NAME,
    })
    icon = _runtime_icon(target, root)
    if icon:
        env["LAF_BUILD_ICON"] = str(icon)
    return env


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def content_id(parts: list[bytes | str], length: int = 16) -> str:
    digest = hashlib.sha256()
    for part in parts:
        data = part.encode("utf-8") if isinstance(part, str) else part
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()[:length]


def directory_parts(label: str, directory: Path) -> list[bytes | str]:
    parts: list[bytes | str] = []
    for path in sorted(
        candidate for candidate in directory.rglob("*")
        if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix != ".pyc"
    ):
        parts.extend((f"{label}/{path.relative_to(directory).as_posix()}", path.read_bytes()))
    return parts


def installed_distribution_snapshot(path: Path | None = None) -> list[str]:
    distributions = (
        importlib.metadata.distributions(path=[str(path)])
        if path is not None else importlib.metadata.distributions()
    )
    return sorted(
        f"{dist.metadata.get('Name', '').lower()}=={dist.version}"
        for dist in distributions if dist.metadata.get("Name")
    )


def base_id(root: Path, target: RuntimeTarget) -> str:
    tracked = [
        root / "backend" / "requirements.txt",
        root / "scripts" / "runtime_entry.py",
        root / "release" / "runtime-layered.spec",
    ]
    return content_id([
        str(BASE_LAYOUT_VERSION),
        target.os, target.arch, target.python_version, target.pyinstaller_version,
        *installed_distribution_snapshot(),
        *(part for path in tracked for part in (path.name, path.read_bytes())),
    ])


def rag_id(root: Path, target: RuntimeTarget) -> str:
    return content_id([
        str(RAG_LAYOUT_VERSION),
        target.os, target.arch, target.python_version, target.accelerator,
        target.torch_version, target.torch_index_url,
        (root / "backend" / "requirements-reranker.txt").read_bytes(),
    ])


def validate_runtime_tree(tree: Path, target: RuntimeTarget) -> list[str]:
    return [] if (tree / target.executable_name).is_file() else [
        f"缺少运行入口：{target.executable_name}"
    ]


def stage_windows_vc_runtime(tree: Path, target: RuntimeTarget) -> None:
    """用当前 Windows VC++ Runtime 覆盖 PyInstaller 可能收集到的旧版 DLL。"""
    if target.os != "windows":
        return
    system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    internal = tree / "_internal"
    required = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
    optional = (
        "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll", "vcruntime140_threads.dll",
    )
    missing = [name for name in required if not (system32 / name).is_file()]
    if missing:
        raise RuntimeError(f"构建机缺少 VC++ Runtime：{', '.join(missing)}")
    internal.mkdir(parents=True, exist_ok=True)
    for name in (*required, *optional):
        source = system32 / name
        if source.is_file():
            shutil.copy2(source, internal / name)


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def promote_directory(source: Path, destination: Path, *, attempts: int = 20) -> None:
    """内容寻址目录晋升；Windows 索引器短暂占用时有限重试。"""
    for attempt in range(attempts):
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.2)


def create_archive(tree: Path, archive: Path, root_name: str) -> Path:
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(candidate for candidate in tree.rglob("*") if candidate.is_file()):
                bundle.write(path, Path(root_name) / path.relative_to(tree))
        return archive
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(tree, arcname=root_name)
    return archive


def split_asset(archive: Path, max_part_bytes: int = GITHUB_ASSET_LIMIT) -> list[Path]:
    if archive.stat().st_size <= max_part_bytes:
        return [archive]
    parts: list[Path] = []
    with archive.open("rb") as source:
        index = 1
        while block := source.read(max_part_bytes):
            part = archive.with_name(f"{archive.name}.part{index:02d}")
            part.write_bytes(block)
            parts.append(part)
            index += 1
    write_json(archive.with_name(archive.name + ".parts.json"), {
        "schema_version": 1,
        "archive": archive.name,
        "size": archive.stat().st_size,
        "sha256": sha256_file(archive),
        "parts": [part.name for part in parts],
        "part_sha256": {part.name: sha256_file(part) for part in parts},
    })
    return parts


def package_layer(tree: Path, archive: Path, root_name: str, layer_id: str) -> tuple[list[Path], dict]:
    create_archive(tree, archive, root_name)
    archive_size = archive.stat().st_size
    archive_sha = sha256_file(archive)
    downloadable = split_asset(archive)
    assets = [{"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)} for path in downloadable]
    outputs = list(downloadable)
    if downloadable != [archive]:
        parts_manifest = archive.with_name(archive.name + ".parts.json")
        outputs.append(parts_manifest)
        archive.unlink()
    return outputs, {
        "id": layer_id,
        "archive": archive.name,
        "size": archive_size,
        "sha256": archive_sha,
        "assets": assets,
    }


def _run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def pip_index_args() -> list[str]:
    index = os.environ.get("DEMIURGE_PYPI_INDEX_URL", DEFAULT_PYPI_INDEX).strip()
    if not index.startswith("https://"):
        raise ValueError("DEMIURGE_PYPI_INDEX_URL 必须使用 HTTPS")
    args = [
        "--index-url", index,
        "--timeout", "120",
        "--retries", "5",
    ]
    host = urlparse(index).hostname
    if host:
        args.extend(("--trusted-host", host))
    return args


def npm_executable() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("未找到 npm，无法构建前端")
    return npm


def install_frontend_dependencies(root: Path, frontend_work: Path, npm: str, *, prefer_offline: bool = True) -> None:
    if not prefer_offline:
        _run([npm, "ci"], frontend_work)
        return
    cache = root / "vendor" / "npm"
    if cache.is_dir():
        offline = subprocess.run([npm, "ci", "--offline", "--cache", str(cache)], cwd=frontend_work, check=False)
        if offline.returncode == 0:
            return
        print("当前平台的 vendor/npm 不完整，回退联网安装。")
    _run([npm, "ci"], frontend_work)


def _host_matches(target: RuntimeTarget) -> bool:
    host_os = "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else "other"
    machine = platform.machine().lower()
    host_arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"amd64", "x86_64"} else machine
    return target.os == host_os and target.arch == host_arch


def install_build_dependencies(root: Path, target: RuntimeTarget) -> None:
    index_args = pip_index_args()
    _run([
        sys.executable, "-m", "pip", "install", *index_args,
        "-r", str(root / "backend" / "requirements.txt"),
    ], root)
    _run([
        sys.executable, "-m", "pip", "install", *index_args,
        f"pyinstaller=={target.pyinstaller_version}",
    ], root)


def build_frontend(root: Path, work_dir: Path, *, install_deps: bool) -> Path:
    frontend_work = work_dir / "frontend-source"
    shutil.copytree(root / "frontend", frontend_work, ignore=shutil.ignore_patterns("node_modules", "dist"))
    npm = npm_executable()
    install_frontend_dependencies(root, frontend_work, npm, prefer_offline=not install_deps)
    _run([npm, "run", "build"], frontend_work)
    return frontend_work / "dist"


def build_application_tree(root: Path, frontend_dist: Path, tree: Path, version: str) -> str:
    tree.mkdir(parents=True)
    application_id = content_id([
        *directory_parts("backend", root / "backend" / "app"),
        *directory_parts("frontend", frontend_dist),
        *directory_parts("comfyui-ext", root / "comfyui-ext"),
    ])
    shutil.copytree(
        root / "backend" / "app", tree / "backend" / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(frontend_dist, tree / "frontend")
    shutil.copytree(root / "comfyui-ext", tree / "comfyui-ext")
    write_json(tree / "application-manifest.json", {
        "schema_version": 1, "app_version": version, "application_id": application_id,
    })
    return application_id


def build_rag_tree(root: Path, target: RuntimeTarget, tree: Path) -> str:
    packages = tree / "site-packages"
    packages.mkdir(parents=True)
    install_command = [
        sys.executable, "-m", "pip", "install", "--upgrade", "--no-compile",
        *pip_index_args(),
        "--target", str(packages),
        "-r", str(root / "backend" / "requirements-reranker.txt"),
        f"torch=={target.torch_version}",
    ]
    if target.torch_index_url:
        install_command.extend(("--extra-index-url", target.torch_index_url))
    _run(install_command, root)
    licenses_root = tree / "licenses"
    for metadata in packages.glob("*.dist-info"):
        source = metadata / "licenses"
        if source.is_dir():
            licenses_root.mkdir(parents=True, exist_ok=True)
            shutil.move(source, licenses_root / metadata.name.removesuffix(".dist-info"))
    layer_id = content_id([
        rag_id(root, target), *installed_distribution_snapshot(packages),
    ])
    write_json(tree / "rag-manifest.json", {"schema_version": 1, "rag_id": layer_id})
    return layer_id


def run_runtime_self_check(
    layout: Path, target: RuntimeTarget, executable: Path | None = None,
) -> None:
    layout = layout.resolve()
    executable = executable or layout / target.executable_name
    with tempfile.TemporaryDirectory(prefix="runtime-self-check-", dir=layout.parent) as temp_data:
        env = os.environ.copy()
        env.update(runtime_environment(layout, target.edition))
        env.update({
            "LAF_RUNTIME_STATE": str(layout / "current.json"),
            "LAF_DATA_DIR": temp_data,
            "LAF_RUNTIME_SELF_TEST": "1",
            "LAF_NO_BROWSER": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        # 中文 Windows 上 Runtime 会按 GBK 输出中文日志，text=True 默认按 locale/UTF-8 解码会抛
        # UnicodeDecodeError。固定 UTF-8 并对无法解码的字节退让，保证自检只因真实失败而失败。
        result = subprocess.run(
            [str(executable.resolve())], cwd=layout, env=env, text=True, capture_output=True,
            check=False, encoding="utf-8", errors="replace",
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Runtime 自检失败：{detail}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("Runtime 自检没有返回有效结果") from exc
    if payload.get("status") != "ok":
        raise RuntimeError("Runtime 自检未通过")
    if target.full_rag:
        actual_torch = str(payload.get("torch_version") or "")
        if target.accelerator == "cuda":
            digits = target.torch_version.rpartition("+cu")[2]
            expected_cuda = f"{int(digits[:-1])}.{digits[-1]}" if digits.isdigit() else ""
            actual_cuda = str(payload.get("torch_cuda") or "")
            if actual_torch != target.torch_version or actual_cuda != expected_cuda:
                raise RuntimeError(
                    "CUDA Torch 版本不匹配："
                    f"期望 {target.torch_version} / CUDA {expected_cuda}，"
                    f"实际 {actual_torch or '未知'} / CUDA {actual_cuda or 'CPU'}"
                )
        elif actual_torch != target.torch_version:
            raise RuntimeError(
                f"Torch 版本不匹配：期望 {target.torch_version}，实际 {actual_torch or '未知'}"
            )


def build_runtime(root: Path, target: RuntimeTarget, version: str, output_dir: Path, work_dir: Path, *, install_deps: bool) -> list[Path]:
    if not _host_matches(target):
        raise RuntimeError(f"当前主机不能构建目标 {target.id}")
    if install_deps:
        install_build_dependencies(root, target)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    frontend_dist = build_frontend(root, work_dir, install_deps=install_deps)
    layout = work_dir / "layout"
    app_tree = layout / "apps" / "pending"
    app_layer_id = build_application_tree(root, frontend_dist, app_tree, version)
    final_app_tree = app_tree.with_name(app_layer_id)
    promote_directory(app_tree, final_app_tree)

    _run(pyinstaller_command(target, root, work_dir), root, pyinstaller_environment(target, root, work_dir))
    built_base = work_dir / "dist" / RUNTIME_NAME
    stage_windows_vc_runtime(built_base, target)
    errors = validate_runtime_tree(built_base, target)
    if errors:
        raise RuntimeError("；".join(errors))
    base_layer_id = base_id(root, target)
    base_tree = layout / "base" / base_layer_id
    shutil.copytree(built_base, base_tree)
    write_json(base_tree / "base-manifest.json", {
        "schema_version": 1, "base_id": base_layer_id,
        "python_version": target.python_version, "target": target.id,
    })

    rag_layer_id = ""
    rag_tree = None
    if target.full_rag:
        rag_tree = layout / "rag" / "pending"
        rag_layer_id = build_rag_tree(root, target, rag_tree)
        final_rag_tree = rag_tree.with_name(rag_layer_id)
        promote_directory(rag_tree, final_rag_tree)
        rag_tree = final_rag_tree

    state = {
        "schema_version": 2, "app_version": version, "target": target.id,
        "edition": target.edition, "base_id": base_layer_id,
        "application_id": app_layer_id, "rag_id": rag_layer_id,
    }
    write_json(layout / "current.json", state)
    run_runtime_self_check(layout, target, base_tree / target.executable_name)

    suffix = ".zip" if target.os == "windows" else ".tar.gz"
    outputs: list[Path] = []
    base_assets, base_layer = package_layer(
        base_tree,
        output_dir / f"{APP_NAME}-base-{base_layer_id}-{target.id}{suffix}",
        f"base-{base_layer_id}", base_layer_id,
    )
    outputs.extend(base_assets)
    app_assets, app_layer = package_layer(
        final_app_tree,
        output_dir / f"{APP_NAME}-application-{version}-{target.id}.zip",
        f"application-{app_layer_id}", app_layer_id,
    )
    outputs.extend(app_assets)
    layers = {"base": base_layer, "application": app_layer}
    if rag_tree is not None:
        rag_assets, rag_layer = package_layer(
            rag_tree,
            output_dir / f"{APP_NAME}-rag-{rag_layer_id}-{target.id}{suffix}",
            f"rag-{rag_layer_id}", rag_layer_id,
        )
        outputs.extend(rag_assets)
        layers["rag"] = rag_layer

    manifest = write_json(
        output_dir / f"{APP_NAME}-update-{version}-{target.id}.json",
        {**state, "layers": layers},
    )
    outputs.append(manifest)
    return outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=Path(__file__).resolve().parents[1] / "release" / "runtime-targets.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("matrix")
    build = sub.add_parser("build")
    build.add_argument("--target", required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--work-dir", type=Path, required=True)
    build.add_argument("--install-deps", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--target", required=True)
    verify.add_argument("--tree", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    targets = load_targets(args.targets)
    if args.command == "matrix":
        print(json.dumps({"include": [
            {"target": item.id, "runner": item.runner, "python-version": item.python_version}
            for item in targets.values()
        ]}, separators=(",", ":")))
        return 0
    target = targets.get(args.target)
    if target is None:
        raise SystemExit(f"未知 Runtime 目标：{args.target}")
    if args.command == "verify":
        errors = validate_runtime_tree(args.tree, target)
        for error in errors:
            print("ERROR: " + error)
        return 1 if errors else 0
    root = Path(__file__).resolve().parents[1]
    assets = build_runtime(root, target, args.version, args.output_dir, args.work_dir, install_deps=args.install_deps)
    for asset in assets:
        print(asset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
