"""构建 currentUser / perMachine 两套 NSIS 安装器，并做内容与静默安装核验。

产物写到 `release-output/installers/`，文件名区分 User / Machine。
默认每次都会重新暂存依赖；`--skip-stage` 只用于已经确认暂存树干净的重跑。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime_delivery import normalize_version
from stage_installer_dependencies import (
    DEFAULT_STAGING,
    PER_MACHINE_OVERLAY,
    TAURI_CONF,
    assert_installer_config,
    assert_per_machine_overlay,
    audit_staging,
    stage,
    summarize,
)
from updater_manifest import assert_signature_matches_pubkey, assert_updater_config

ROOT = Path(__file__).resolve().parents[1]
TAURI_DIR = ROOT / "desktop" / "tauri"
OUTPUT_DIR = ROOT / "release-output" / "installers"
WORK_DIR = ROOT / ".installer-work"
CLI_PACKAGE = "@tauri-apps/cli@2"
PRODUCT_NAME = "Demiurge"
UNINSTALL_REGISTRY_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT_NAME}"
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
UPDATER_SIGNATURE_SUFFIX = ".sig"


def signing_key_available() -> bool:
    return bool(
        os.environ.get("TAURI_SIGNING_PRIVATE_KEY")
        or os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PATH")
    )


def updater_public_key_text() -> str:
    """解码 tauri.conf.json 的 updater 公钥（base64 信封 → 标准 minisign 公钥文件文本）。

    阵营一（无 Authenticode）发布纪律的一部分：校验和与更新器共用同一把钥，
    `SHA256SUMS.pub` 随每次发布导出，谨慎用户可用标准 minisign 工具离线验签。
    """
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    return base64.b64decode(conf["plugins"]["updater"]["pubkey"]).decode("utf-8")


def sign_checksums(sums: Path, version: str = "") -> Path | None:
    """给 SHA256SUMS.txt 附加 minisign 签名（与更新器同一把钥匙、同一信封格式）。

    未配置钥匙（TAURI_SIGNING_PRIVATE_KEY(_PATH)）时留痕跳过——HTTPS Release 下载
    仍受 TLS 保护，签名是给镜像/离线分发场景的增强，不阻塞发布。
    version 只进签名 trusted comment（溯源），不参与更新器校验。
    """
    if not signing_key_available():
        print("[installer] 未配置签名钥匙：SHA256SUMS.txt 未签名（HTTPS 下载仍受 TLS 保护）",
              flush=True)
        return None
    args = ["signer", "sign", str(sums)]
    if version:
        args += ["--app-version", version]
    run_cli(args)
    signature = Path(str(sums) + UPDATER_SIGNATURE_SUFFIX)
    if not signature.is_file():
        raise RuntimeError(f"signer sign 未产出 {signature.name}")
    print(f"[installer] 校验和签名：{signature.name}", flush=True)
    return signature


def assert_signing_key_matches_updater_key() -> str | None:
    """发布前 fail-fast：签名私钥必须与 conf 内嵌公钥同钥。

    构建只用私钥签名、客户端只用 `plugins.updater.pubkey` 验签；两者错配时
    构建与自检仍会全绿，直到真实更新下载完才全量验签失败。这里先用私钥签一个
    固定挑战，再比较 minisign key id；无钥匙时跳过，不阻塞本地无签名构建。
    """
    if not signing_key_available():
        return None
    challenge = WORK_DIR / "updater-key-check.txt"
    challenge.parent.mkdir(parents=True, exist_ok=True)
    challenge.write_text("demiurge updater key check\n", encoding="utf-8")
    signature = Path(str(challenge) + UPDATER_SIGNATURE_SUFFIX)
    signature.unlink(missing_ok=True)
    try:
        run_cli(["signer", "sign", str(challenge)])
        if not signature.is_file():
            raise RuntimeError(f"signer sign 未产出 {signature.name}")
        key_id = assert_signature_matches_pubkey(signature)
    finally:
        signature.unlink(missing_ok=True)
        challenge.unlink(missing_ok=True)
    print(f"[installer] 签名钥与内嵌更新公钥一致（key id={key_id}）", flush=True)
    return key_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_version(conf_path: Path = TAURI_CONF) -> str:
    return normalize_version(str(json.loads(conf_path.read_text(encoding="utf-8"))["version"]))


def deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def variant_config(variant: str, version: str, overlay: Path | None) -> Path:
    """把一个最小覆盖配置交给 Tauri：版本 + 可选的 perMachine 覆盖。

    `--config` 是深合并，不需要复制整份 `tauri.conf.json`；这样版本归一与安装模式
    都只有一处真源，也避免两份配置漂移。
    """
    payload: dict = {"version": version}
    if overlay is not None:
        payload = deep_merge(payload, json.loads(overlay.read_text(encoding="utf-8")))
    if not signing_key_available():
        # Tauri CLI 在有 createUpdaterArtifacts 却没有签名密钥时会直接拒绝构建；
        # 显式关掉更新产物，让本地与未配置密钥的 CI 仍能出安装器。
        payload = deep_merge(payload, {"bundle": {"createUpdaterArtifacts": False}})
    path = WORK_DIR / "config" / f"{variant}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path.resolve()


def normalized_entry(entry: str) -> str:
    return entry.replace("\\", "/").strip("/").lower()


def forbidden_entries(entries: list[str]) -> list[str]:
    """从安装器文件清单里挑出用户预设/运行数据；空列表才算干净。"""
    offenders: list[str] = []
    for entry in entries:
        lowered = normalized_entry(entry)
        if not lowered:
            continue
        if any(part in FORBIDDEN_PATH_PARTS for part in lowered.split("/")):
            offenders.append(entry)
            continue
        if lowered.startswith(FORBIDDEN_PATH_PREFIXES):
            offenders.append(entry)
    return sorted(set(offenders))


def seven_zip_executable() -> str:
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("7z")
    if found:
        return found
    raise RuntimeError("需要 7-Zip 来列出安装器内容（未找到 7z.exe）")


def installer_entries(installer: Path) -> list[str]:
    result = subprocess.run(
        [seven_zip_executable(), "l", "-slt", str(installer)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    entries: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("Path = "):
            entries.append(line[len("Path = "):])
    return entries


LEVEL_BY_MODE = {"perMachine": "admin", "currentUser": "user", "both": "multiUser"}


def nsi_facts(script: str) -> dict[str, object]:
    """NSIS 脚本里两种 RequestExecutionLevel 都写着，只有 !define INSTALLMODE 选中那条生效。"""
    mode_match = re.search(r'!define INSTALLMODE "([^"]+)"', script)
    mode = mode_match.group(1) if mode_match else ""
    active = re.search(
        rf'!if "\$\{{INSTALLMODE\}}" == "{re.escape(mode)}"[^\n]*\n\s*RequestExecutionLevel (\w+)',
        script,
    ) if mode else None
    return {
        "install_mode": mode,
        "request_execution_level": active.group(1) if active else LEVEL_BY_MODE.get(mode, ""),
        "directory_page": bool(re.search(r"^!insertmacro MUI_PAGE_DIRECTORY", script, re.MULTILINE)),
    }


def verify_install_tree(root: Path) -> list[str]:
    errors: list[str] = []
    for required in (
        "Demiurge.exe",
        "dependencies/git/cmd/git.exe",
        "dependencies/MANIFEST.json",
        "uninstall.exe",
    ):
        if not (root / required).is_file():
            errors.append(f"安装目录缺少 {required}")
    stray = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    ]
    errors.extend(f"安装目录出现禁止项：{item}" for item in forbidden_entries(stray))
    return errors


def run_cli(args: list[str], *, cwd: Path = TAURI_DIR) -> None:
    executable = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    command = [executable, "-y", CLI_PACKAGE, *args]
    print(f"[installer] {' '.join(command)}", flush=True)
    env = dict(os.environ)
    env.setdefault("npm_config_update_notifier", "false")
    subprocess.run(command, cwd=cwd, check=True, env=env)


def newest(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.is_file()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def preserve_updater_signature(
    bundle_exe: Path, target_exe: Path, *, since: float
) -> Path | None:
    """把本次构建的更新签名复制到产物 exe 旁边。

    Tauri 2 的 NSIS 更新载荷就是安装器本身（插件按 PE 头识别后直接执行），签名为
    `<exe>.sig`；latest.json 的 signature 字段取该文件内容。Machine 变体也留签名，
    但不进更新通道——清单只引用 User 包。
    `since` 过滤上一次构建留下的同名文件，避免把旧签名当成本次产物。
    """
    if not signing_key_available():
        return None
    if bundle_exe.stat().st_mtime < since:
        raise RuntimeError(f"安装器不是本次构建产物：{bundle_exe}")
    signature = Path(str(bundle_exe) + UPDATER_SIGNATURE_SUFFIX)
    if not signature.is_file():
        raise RuntimeError(f"构建已启用更新签名，但没有产出 {signature.name}")
    target_signature = Path(str(target_exe) + UPDATER_SIGNATURE_SUFFIX)
    shutil.copy2(signature, target_signature)
    return target_signature


def build_variant(variant: str, version: str, overlay: Path | None) -> Path:
    config = variant_config(variant, version, overlay)
    started = time.time()
    run_cli(["build", "--bundles", "nsis", "--config", str(config)])

    artifact = newest(
        [
            path
            for path in (
                TAURI_DIR / "target" / "release" / "bundle" / "nsis"
            ).glob("*-setup.exe")
            if path.stat().st_mtime >= started
        ]
    )
    if artifact is None:
        raise RuntimeError("Tauri 没有产出本次构建的 NSIS 安装器")
    nsi = newest(list((TAURI_DIR / "target" / "release").glob("nsis/**/installer.nsi")))
    if nsi is None:
        raise RuntimeError("找不到生成的 installer.nsi，无法核对安装模式")

    nsi_dir = WORK_DIR / "nsis" / variant
    nsi_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(nsi, nsi_dir / "installer.nsi")
    facts = nsi_facts(nsi.read_text(encoding="utf-8", errors="replace"))
    expected_mode = "perMachine" if variant == "machine" else "currentUser"
    if facts["install_mode"] != expected_mode:
        raise RuntimeError(f"{variant} 安装器的安装模式不正确：{facts}")
    if facts["request_execution_level"] != LEVEL_BY_MODE[expected_mode]:
        raise RuntimeError(f"{variant} 安装器的提权级别不正确：{facts}")
    if not facts["directory_page"]:
        raise RuntimeError(f"{variant} 安装器缺少自定义安装目录页")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    label = "Machine" if variant == "machine" else "User"
    target = OUTPUT_DIR / f"Demiurge-Setup-{label}-{version}.exe"
    shutil.copy2(artifact, target)
    signature = preserve_updater_signature(artifact, target, since=started)
    print(
        f"[installer] {label}: {target.name} "
        f"({target.stat().st_size / 1024 / 1024:.1f} MiB, "
        f"RequestExecutionLevel={facts['request_execution_level'] or '?'})",
        flush=True,
    )
    if signature is not None:
        print(f"[installer] {label} 更新签名：{signature.name}", flush=True)
    return target


def run_installed_self_test(install_dir: Path) -> list[str]:
    """安装目录里的壳必须能自检通过（隔离 home，不碰本机真实数据）。"""
    home = WORK_DIR / "smoke-home"
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    env = dict(os.environ)
    env["DEMIURGE_SHELL_HOME"] = str(home)
    result = subprocess.run(
        [str(install_dir / "Demiurge.exe"), "--self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )
    summary = re.search(r"self-test: (\d+) passed, (\d+) failed", result.stdout)
    if result.returncode != 0 or summary is None or summary.group(2) != "0":
        return [
            (
                "安装后的壳自检未通过："
                f"exit={result.returncode} "
                f"summary={summary.group(0) if summary else result.stdout.strip()[-200:]}"
            )
        ]
    print(f"[installer] 安装目录自检：{summary.group(0)}", flush=True)
    return []


def residue_targets(install_dir: Path, appdata: Path) -> list[tuple[str, Path]]:
    """currentUser 静默卸载后必须消失的位置。

    `HKCU\\Software\\Demiurge\\Demiurge`（上次安装路径）不在此列：Tauri 生成的 NSIS 把它的清理
    绑在「删除应用数据」勾选框（`$DeleteAppDataCheckboxState = 1`）上，`/S` 永远读不到该状态。
    """
    return [
        ("安装目录", install_dir),
        (
            "开始菜单",
            appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / PRODUCT_NAME,
        ),
    ]


def uninstall_residue(install_dir: Path) -> list[str]:
    import winreg  # Windows-only：只有 NSIS 卸载核验会走到这里

    appdata = Path(os.environ["APPDATA"])
    leftovers = [
        f"{label}：{path}" for label, path in residue_targets(install_dir, appdata) if path.exists()
    ]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_KEY):
            leftovers.append(f"注册表：HKCU\\{UNINSTALL_REGISTRY_KEY}")
    except FileNotFoundError:
        pass
    return leftovers


def smoke_install(installer: Path) -> list[str]:
    smoke_root = WORK_DIR / "smoke-install"
    if smoke_root.exists():
        shutil.rmtree(smoke_root)
    smoke_root.mkdir(parents=True)
    install_dir = smoke_root / "app"
    print(f"[installer] 静默安装到 {install_dir}", flush=True)
    subprocess.run([str(installer), "/S", f"/D={install_dir}"], check=True)

    deadline = time.time() + 120
    while time.time() < deadline and not (install_dir / "uninstall.exe").is_file():
        time.sleep(1)
    errors = verify_install_tree(install_dir)
    if errors:
        return errors
    errors = run_installed_self_test(install_dir)
    if errors:
        return errors

    print("[installer] 静默卸载", flush=True)
    subprocess.run([str(install_dir / "uninstall.exe"), "/S"], check=True)
    deadline = time.time() + 120
    while time.time() < deadline and uninstall_residue(install_dir):
        time.sleep(1)
    leftovers = uninstall_residue(install_dir)
    if leftovers:
        return [f"卸载后仍有残留：{item}" for item in leftovers]
    print("[installer] 卸载后安装目录/开始菜单/注册表登记项均消失", flush=True)
    shutil.rmtree(smoke_root, ignore_errors=True)
    shutil.rmtree(WORK_DIR / "smoke-home", ignore_errors=True)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-stage", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--skip-machine", action="store_true")
    parser.add_argument(
        "--version",
        help="安装器版本（默认取 desktop/tauri/tauri.conf.json）；release tag 的归一版本，如 0.15.0",
    )
    args = parser.parse_args()

    assert_installer_config()
    assert_per_machine_overlay()
    assert_updater_config()
    assert_signing_key_matches_updater_key()

    if not args.skip_stage:
        stage(DEFAULT_STAGING)
        print(f"[installer] 依赖暂存：{summarize(DEFAULT_STAGING)}", flush=True)
    errors = audit_staging(DEFAULT_STAGING)
    if errors:
        print("暂存树未通过白名单检查：", *errors, sep="\n- ")
        return 1

    version = normalize_version(args.version) if args.version else read_version()
    outputs = [build_variant("user", version, None)]
    if not args.skip_machine:
        outputs.append(build_variant("machine", version, PER_MACHINE_OVERLAY))

    for installer in outputs:
        entries = installer_entries(installer)
        offenders = forbidden_entries(entries)
        if offenders:
            print(f"{installer.name} 含禁止项：{offenders[:10]}")
            return 1
        print(f"[installer] {installer.name} 内容清单干净（{len(entries)} 项）", flush=True)

    if not args.skip_smoke:
        errors = smoke_install(outputs[0])
        if errors:
            print("静默安装核验失败：", *errors, sep="\n- ")
            return 1
        print("[installer] 静默安装 / 卸载核验通过", flush=True)

    sums = OUTPUT_DIR / "SHA256SUMS.txt"
    sums.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in outputs),
        encoding="utf-8",
    )
    # 阵营一发布纪律（无 Authenticode）：校验和的可验证性自有钥链兜底——公钥随发布
    # 导出、有钥匙就签（README/RELEASE.md 记录用户侧验证方法）。
    (OUTPUT_DIR / "SHA256SUMS.pub").write_text(updater_public_key_text(), encoding="utf-8")
    sign_checksums(sums, version=version)
    print(f"[installer] 完成：{OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
