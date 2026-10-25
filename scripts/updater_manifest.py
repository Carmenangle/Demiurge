"""Tauri 自动更新清单（latest.json）的渲染、校验与更新配置守卫。

签名由 `tauri build` 产出：`<installer>.exe` + `<installer>.exe.sig`。NSIS 的更新载荷
就是安装器本身（插件按 PE 头识别后直接执行），`.sig` 是 minisign 签名的 base64 文本，
服务端原样放进 latest.json，应用侧用内嵌公钥验签。这里不做密码学验签（Rust 自检用
固定夹具覆盖），只负责：

- 版本、资产 URL、签名三者绑定，不允许清单版本与签名内版本漂移；
- 拒绝非 HTTPS 端点、非 `.exe` 资产名、`-Machine` 变体与路径穿越；
- 守卫 `tauri.conf.json` 的更新配置：签名必需、禁降级、禁不安全传输。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime_delivery import (
    SAFE_NAME_PATTERN,
    normalize_version,
    release_base,
)

ROOT = Path(__file__).resolve().parents[1]
TAURI_CONF = ROOT / "desktop" / "tauri" / "tauri.conf.json"
TEST_PUBKEY = ROOT / "desktop" / "tauri" / "testdata" / "updater-test.pub"
DEFAULT_ARTIFACT_DIR = ROOT / "release-output" / "installers"
PLATFORM = "windows-x86_64"
SIGNATURE_VERSION_PREFIX = "version:"


def _decode_signature_envelope(path: Path) -> tuple[str, list[str]]:
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw or any(char.isspace() for char in raw):
        raise ValueError(f"签名文件必须是单行 base64：{path}")
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"签名文件不是合法的 minisign base64：{path}") from exc
    lines = decoded.splitlines()
    if len(lines) < 4 or not lines[2].startswith("trusted comment: "):
        raise ValueError(f"签名文件不是 minisign 格式：{path}")
    return raw, lines


def load_signature(path: Path) -> tuple[str, str]:
    """返回 (base64 签名原文, 签名绑定的版本)。Tauri 的 .sig 是单行 base64 文本。"""
    raw, lines = _decode_signature_envelope(path)
    fields = dict(
        field.split(":", 1)
        for field in lines[2][len("trusted comment: ") :].split("\t")
        if ":" in field
    )
    version = fields.get("version", "").strip()
    if not version:
        raise ValueError(
            f"签名未绑定版本（缺 version 字段）；Tauri CLI 需支持 requireSignedVersion：{path}"
        )
    return raw, version


def minisign_key_id(signature_path: Path) -> bytes:
    """从 .sig 信封解出 8 字节 key id（签名行：算法 2 + key id 8 + 签名 64）。"""
    _, lines = _decode_signature_envelope(signature_path)
    try:
        raw = base64.b64decode(lines[1], validate=True)
    except binascii.Error as exc:
        raise ValueError(f"签名数据行不是合法 base64：{signature_path}") from exc
    if len(raw) != 74 or raw[:2] not in (b"ED", b"Ed"):
        raise ValueError(f"签名数据行不是合法的 minisign 签名：{signature_path}")
    return raw[2:10]


def _require_https(url: str, label: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label} 必须是 HTTPS 地址：{url}")
    return url


def _require_updater_artifact(name: str) -> str:
    if (
        not SAFE_NAME_PATTERN.fullmatch(name)
        or not name.endswith(".exe")
        or "-Machine" in name
    ):
        raise ValueError(f"更新资产必须是 currentUser 安装器 exe（非 -Machine）：{name}")
    return name


def render_manifest(
    *,
    version: str,
    signature_b64: str,
    signature_version: str,
    artifact_name: str,
    base_url: str,
    notes: str = "",
    pub_date: datetime | None = None,
    platform: str = PLATFORM,
) -> dict:
    normalized = normalize_version(version)
    signed = normalize_version(signature_version)
    if signed != normalized:
        raise ValueError(f"签名绑定版本 {signed} 与清单版本 {normalized} 不一致")
    artifact = _require_updater_artifact(artifact_name)
    base = _require_https(base_url.rstrip("/"), "更新资产根地址")
    if not re.fullmatch(r"[0-9a-z][0-9a-z_-]*", platform):
        raise ValueError(f"平台键不合法：{platform}")
    stamp = (pub_date or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "version": normalized,
        "notes": notes,
        "pub_date": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            platform: {
                "signature": signature_b64.strip(),
                "url": f"{base}/{artifact}",
            }
        },
    }


def discover_artifact(directory: Path, version: str) -> Path:
    """挑出指定版本的 currentUser 安装器；Machine 变体不进更新通道。"""
    artifact = Path(directory) / f"Demiurge-Setup-User-{normalize_version(version)}.exe"
    if not artifact.is_file():
        raise ValueError(f"找不到 currentUser 更新资产：{artifact}")
    return artifact


def decode_updater_pubkey(pubkey: str) -> bytes:
    """base64 信封 → 42 字节 minisign 公钥（算法 2 + key id 8 + 公钥 32）。"""
    try:
        pubkey_text = base64.b64decode(str(pubkey), validate=True).decode("utf-8")
        pubkey_lines = pubkey_text.splitlines()
        key_bytes = base64.b64decode(pubkey_lines[1], validate=True)
    except (binascii.Error, UnicodeDecodeError, IndexError) as exc:
        raise RuntimeError("plugins.updater.pubkey 不是合法的 minisign 公钥 base64") from exc
    if len(pubkey_lines) < 2 or len(key_bytes) != 42:
        raise RuntimeError("plugins.updater.pubkey 不是合法的 minisign 公钥")
    return key_bytes


def updater_pubkey_bytes(conf_path: Path = TAURI_CONF) -> bytes:
    conf = json.loads(Path(conf_path).read_text(encoding="utf-8"))
    updater = (conf.get("plugins") or {}).get("updater") or {}
    return decode_updater_pubkey(str(updater.get("pubkey") or ""))


def assert_signature_matches_pubkey(
    signature_path: Path, conf_path: Path = TAURI_CONF
) -> str:
    """发布门禁：签名必须与 conf 内嵌公钥同钥，否则客户端更新必然验签失败。

    构建只用私钥签名、客户端只用 `plugins.updater.pubkey` 验签；两者错配时构建
    与自检都可能全绿，直到真实更新下载完才失败。这里比较 minisign key id 提前拦截。
    """
    actual = minisign_key_id(signature_path)
    expected = updater_pubkey_bytes(conf_path)[2:10]
    if actual != expected:
        raise RuntimeError(
            "更新包签名与 tauri.conf.json 的 plugins.updater.pubkey 不是同一把钥："
            f"签名 key id={actual[::-1].hex().upper()}，"
            f"内嵌公钥 key id={expected[::-1].hex().upper()}。"
            "客户端只用内嵌公钥验签，继续发布会造成全量更新失败；"
            "请把 conf 换成生产公钥，或改用与 conf 匹配的签名钥。"
        )
    return expected[::-1].hex().upper()


def assert_updater_config(conf_path: Path = TAURI_CONF) -> dict:
    conf = json.loads(Path(conf_path).read_text(encoding="utf-8"))
    bundle = conf.get("bundle") or {}
    if bundle.get("createUpdaterArtifacts") is not True:
        raise RuntimeError("bundle.createUpdaterArtifacts 必须为 true（无签名密钥时由构建脚本临时关闭）")

    updater = (conf.get("plugins") or {}).get("updater") or {}
    decode_updater_pubkey(str(updater.get("pubkey") or ""))

    endpoints = updater.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise RuntimeError("plugins.updater.endpoints 必须是非空列表")
    for endpoint in endpoints:
        _require_https(str(endpoint), "plugins.updater.endpoints")

    if updater.get("requireSignedVersion") is not True:
        raise RuntimeError("plugins.updater.requireSignedVersion 必须为 true（拒绝版本/资产错配的降级）")
    if updater.get("allowDowngrades") is True:
        raise RuntimeError("plugins.updater.allowDowngrades 必须关闭")
    for dangerous in (
        "dangerousInsecureTransportProtocol",
        "dangerousAcceptInvalidCerts",
        "dangerousAcceptInvalidHostnames",
    ):
        if updater.get(dangerous) is True:
            raise RuntimeError(f"plugins.updater.{dangerous} 必须关闭")
    if ((updater.get("windows") or {}).get("installMode")) != "passive":
        raise RuntimeError("plugins.updater.windows.installMode 必须是 passive")
    return updater


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="发布版本，例如 v0.15 或 0.15.0")
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--artifact", type=Path, help="显式指定安装器 exe（默认按版本在目录里挑）")
    parser.add_argument("--signature", type=Path, help="显式指定 .sig（默认 <artifact>.sig）")
    parser.add_argument("--tag", help="release tag；与 --repository 一起推导资产根地址")
    parser.add_argument("--repository", help="owner/repo；与 --tag 一起推导资产根地址")
    parser.add_argument("--base-url", help="直接指定资产根地址（必须 https）")
    parser.add_argument("--notes", default="", help="更新说明；也可用 --notes-file")
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT_DIR / "latest.json")
    args = parser.parse_args()

    assert_updater_config()

    artifact = args.artifact or discover_artifact(args.artifact_dir, args.version)
    signature_path = args.signature or Path(str(artifact) + ".sig")
    if not artifact.is_file() or not signature_path.is_file():
        raise SystemExit(f"缺少更新包或签名：{artifact} / {signature_path}")

    if args.base_url:
        base_url = args.base_url
    elif args.tag and args.repository:
        base_url = release_base(args.tag, args.repository)
    else:
        raise SystemExit("必须提供 --base-url，或同时提供 --tag 与 --repository")

    signature_b64, signature_version = load_signature(signature_path)
    key_id = assert_signature_matches_pubkey(signature_path)
    notes = args.notes
    if args.notes_file:
        notes = args.notes_file.read_text(encoding="utf-8").strip()

    manifest = render_manifest(
        version=args.version,
        signature_b64=signature_b64,
        signature_version=signature_version,
        artifact_name=artifact.name,
        base_url=base_url,
        notes=notes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已生成自动更新清单：{args.output}")
    print(f"版本 {manifest['version']} → {manifest['platforms'][PLATFORM]['url']}")
    print(f"签名与内嵌更新公钥一致（key id={key_id}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
