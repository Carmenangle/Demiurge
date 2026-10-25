"""把安装器依赖暂存到 Tauri 资源目录。

安装包只允许携带 `dependencies/` 这一棵子树；用户预设、userdata、backend/data
等运行数据永远不进入暂存目录，也不进入 `bundle.resources`。
目前唯一的依赖是 MinGit（来源与 SHA256 由 release/portable-windows.json 固定）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portable_release import APP_NAME, CHUNK_SIZE, extract_zip, sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE_CONFIG = ROOT / "release" / "portable-windows.json"
DEFAULT_STAGING = ROOT / "desktop" / "tauri" / "installer-staging"
DEFAULT_CACHE_DIR = ROOT / ".installer-work" / "downloads"
TAURI_CONF = ROOT / "desktop" / "tauri" / "tauri.conf.json"
PER_MACHINE_OVERLAY = ROOT / "desktop" / "tauri" / "installer" / "per-machine.conf.json"

ALLOWED_TOP_LEVEL = {"dependencies"}
ALLOWED_DEPENDENCY_ENTRIES = {"git", "MANIFEST.json"}
MANIFEST_NAME = "MANIFEST.json"
EXPECTED_RESOURCES = {"installer-staging/dependencies": "dependencies"}
# WebView2 离线整包 205 MiB，会把安装器的 2/3 撑成一个系统组件；
# 安装包按定案只带 bootstrapper，运行时大层走第三步的按需下载/离线导入。
EXPECTED_WEBVIEW_INSTALL_MODE = "embedBootstrapper"
FORBIDDEN_PATH_PARTS = {
    "presets",
    "userdata",
    "user_state.json",
    "chat.json",
    "desktop.json",
    "runtime.pid",
    ".env",
}
FORBIDDEN_PATH_PREFIXES = ("backend/data",)


def load_mingit_config(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 2:
        raise RuntimeError(f"便携包配置版本不受支持：{config_path}")
    mingit = config.get("mingit")
    if not isinstance(mingit, dict) or not mingit.get("url") or not mingit.get("sha256"):
        raise RuntimeError(f"便携包配置缺少 MinGit 来源或校验值：{config_path}")
    return mingit


def assert_installer_config(conf_path: Path = TAURI_CONF) -> None:
    """安装资源必须是显式白名单，防止有人把仓库目录（预设/用户数据）整体塞进去。"""
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    bundle = conf.get("bundle") or {}
    if bundle.get("active") is not True:
        raise RuntimeError("bundle.active 必须为 true")
    if bundle.get("targets") != ["nsis"]:
        raise RuntimeError(f"bundle.targets 必须只含 nsis，实际 {bundle.get('targets')}")
    if conf.get("mainBinaryName") != APP_NAME:
        raise RuntimeError(f"安装包主程序必须叫 {APP_NAME}.exe，实际 {conf.get('mainBinaryName')}")
    resources = bundle.get("resources")
    if resources != EXPECTED_RESOURCES:
        raise RuntimeError(
            f"bundle.resources 必须严格等于 {EXPECTED_RESOURCES}，实际 {resources}"
        )
    windows = bundle.get("windows") or {}
    mode = windows.get("webviewInstallMode") or {}
    if mode.get("type") != EXPECTED_WEBVIEW_INSTALL_MODE:
        raise RuntimeError(
            f"WebView2 交付方式必须是 {EXPECTED_WEBVIEW_INSTALL_MODE}，实际 {mode.get('type')}"
        )
    if (windows.get("nsis") or {}).get("installMode") != "currentUser":
        raise RuntimeError("基础安装器必须是 currentUser（perMachine 由覆盖文件产出）")


def assert_per_machine_overlay(conf_path: Path = PER_MACHINE_OVERLAY) -> None:
    overlay = json.loads(conf_path.read_text(encoding="utf-8"))
    if overlay != {"bundle": {"windows": {"nsis": {"installMode": "perMachine"}}}}:
        raise RuntimeError(f"perMachine 覆盖只允许改 installMode，实际 {overlay}")


def assert_staging_root(staging: Path) -> None:
    """只允许清理 Tauri 目录下固定名字的暂存目录，避免误删仓库其它内容。"""
    resolved = staging.resolve()
    expected_parent = (ROOT / "desktop" / "tauri").resolve()
    if resolved.parent != expected_parent or resolved.name != "installer-staging":
        raise RuntimeError(f"暂存目录必须是 {expected_parent / 'installer-staging'}，实际 {resolved}")


def audit_staging(staging: Path) -> list[str]:
    """返回暂存树里所有越界项；空列表才允许进入安装包。"""
    errors: list[str] = []
    if not staging.is_dir():
        return [f"暂存目录不存在：{staging}"]

    top_level = {path.name for path in staging.iterdir()}
    for name in sorted(top_level - ALLOWED_TOP_LEVEL):
        errors.append(f"白名单外的顶层条目：{name}")

    dependencies = staging / "dependencies"
    if dependencies.is_dir():
        for name in sorted({path.name for path in dependencies.iterdir()} - ALLOWED_DEPENDENCY_ENTRIES):
            errors.append(f"dependencies 下白名单外的条目：{name}")

    for path in sorted(staging.rglob("*")):
        relative = path.relative_to(staging).as_posix()
        lowered = relative.lower()
        if path.is_symlink():
            errors.append(f"安装资源禁止符号链接：{relative}")
            continue
        if not path.is_file() and not path.is_dir():
            errors.append(f"安装资源含特殊文件：{relative}")
            continue
        if path.is_file():
            allowed = lowered == f"dependencies/{MANIFEST_NAME.lower()}" or lowered.startswith(
                "dependencies/git/"
            )
            if not allowed:
                errors.append(f"安装资源不在依赖白名单内：{relative}")
        parts = lowered.split("/")
        if any(part in FORBIDDEN_PATH_PARTS for part in parts):
            errors.append(f"安装资源含用户数据或预设名称：{relative}")
        if lowered.startswith(FORBIDDEN_PATH_PREFIXES):
            errors.append(f"安装资源含后端运行数据：{relative}")
    return sorted(set(errors))


def download_mingit(mingit: dict, destination: Path, *, attempts: int = 6) -> Path:
    """带断点续传的下载；GitHub 直连在本机会中途超时，必须能接着已有字节继续。"""
    expected = str(mingit["sha256"]).lower()
    if destination.is_file() and sha256_file(destination) == expected:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    urls = [str(mingit["url"]), *(str(item) for item in mingit.get("mirrors") or [])]

    for attempt in range(1, attempts + 1):
        url = urls[(attempt - 1) % len(urls)]
        offset = destination.stat().st_size if destination.is_file() else 0
        headers = {"User-Agent": APP_NAME}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                append = bool(offset) and response.status == 206
                with destination.open("ab" if append else "wb") as output:
                    shutil.copyfileobj(response, output, CHUNK_SIZE)
        except urllib.error.HTTPError as error:
            if error.code == 416:
                if sha256_file(destination) == expected:
                    return destination
                destination.unlink(missing_ok=True)
            time.sleep(min(attempt, 5))
            continue
        except (TimeoutError, urllib.error.URLError, OSError):
            time.sleep(min(attempt, 5))
            continue

        if sha256_file(destination) == expected:
            return destination
        destination.unlink(missing_ok=True)

    raise RuntimeError(
        f"MinGit 下载失败（已尝试 {attempts} 次）；可重跑脚本续传：{destination}"
    )


def stage_mingit(
    staging: Path,
    archive: Path,
    mingit: dict,
    *,
    reset: bool = True,
) -> dict:
    expected_sha = str(mingit["sha256"]).lower()
    actual_sha = sha256_file(archive)
    if actual_sha != expected_sha:
        raise RuntimeError(f"MinGit SHA256 不匹配：{actual_sha} != {expected_sha}")

    if reset and staging.exists():
        shutil.rmtree(staging)
    dependencies = staging / "dependencies"
    dependencies.mkdir(parents=True, exist_ok=True)
    extract_zip(archive, dependencies / "git", strip_root=False)
    if not (dependencies / "git" / "cmd" / "git.exe").is_file():
        raise RuntimeError("MinGit 压缩包缺少 cmd/git.exe")

    manifest = {
        "schema_version": 1,
        "dependencies": {
            "git": {
                "version": str(mingit.get("version", "")),
                "url": str(mingit["url"]),
                "sha256": expected_sha,
            }
        },
    }
    (dependencies / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    errors = audit_staging(staging)
    if errors:
        raise RuntimeError("暂存树未通过安装包白名单检查：\n" + "\n".join(f"- {item}" for item in errors))
    return manifest


def stage(
    staging: Path = DEFAULT_STAGING,
    *,
    config_path: Path = DEFAULT_RELEASE_CONFIG,
    mingit_archive: Path | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict:
    assert_installer_config()
    assert_per_machine_overlay()
    assert_staging_root(staging)
    mingit = load_mingit_config(config_path)
    archive = mingit_archive or download_mingit(mingit, cache_dir / "mingit.zip")
    return stage_mingit(staging, archive, mingit)


def summarize(staging: Path) -> str:
    files = [path for path in staging.rglob("*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    return f"{len(files)} 个文件，{total / 1024 / 1024:.1f} MiB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--config", type=Path, default=DEFAULT_RELEASE_CONFIG)
    parser.add_argument("--mingit-archive", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()

    manifest = stage(
        args.staging_dir,
        config_path=args.config,
        mingit_archive=args.mingit_archive,
        cache_dir=args.cache_dir,
    )
    git = manifest["dependencies"]["git"]
    print(f"已暂存 MinGit {git['version']}（SHA256 {git['sha256'][:12]}…）：{summarize(args.staging_dir)}")
    print(args.staging_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
