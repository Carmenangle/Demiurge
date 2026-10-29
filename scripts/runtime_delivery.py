"""运行时按需交付的 pin 与更新清单校验/渲染。

pin 只回答三件事：去哪拿、拿哪份清单、哪些层必装。层的体积与 SHA256 仍以
`runtime_release.py` 产出的 `Demiurge_update_*.json` 为唯一真源，不在 pin 里复制一份。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIN = ROOT / "release" / "runtime-delivery.json"
DEFAULT_PORTABLE_CONFIG = ROOT / "release" / "portable-windows.json"
DEFAULT_TAURI_CONF = ROOT / "desktop" / "tauri" / "tauri.conf.json"
DEFAULT_TARGET = "windows-x64-full-rag"
PIN_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 2
REQUIRED_LAYERS = ("base", "application")
OPTIONAL_LAYERS = ("rag",)
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")
TARGET_PATTERN = re.compile(r"^[0-9a-z][0-9a-z-]*$")
REPOSITORY_PATTERN = re.compile(r"^[0-9A-Za-z_.-]+/[0-9A-Za-z_.-]+$")
SAFE_NAME_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ARCHIVE_SUFFIXES = (".tar.gz", ".zip", ".7z")


def platform_of(target: str) -> str:
    """目标 ID 的平台段：`windows-x64-full-rag` ⇒ `windows`。"""
    return target.split("-", 1)[0]


def archive_stem(name: str) -> str:
    """归档基名（去掉 .zip/.tar.gz/.7z）；分卷名由它派生。"""
    lowered = name.lower()
    for suffix in ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def normalize_version(raw: str) -> str:
    match = VERSION_PATTERN.fullmatch(str(raw).strip())
    if not match:
        raise ValueError(f"版本必须是 v<主>.<次>[.<修订>]：{raw}")
    major, minor, patch = match.groups()
    return f"{int(major)}.{int(minor)}.{int(patch or 0)}"


def manifest_name(tag: str, target: str = DEFAULT_TARGET) -> str:
    if not TARGET_PATTERN.fullmatch(target):
        raise ValueError(f"目标名不合法：{target}")
    safe_tag = str(tag).strip()
    if not SAFE_NAME_PATTERN.fullmatch(safe_tag):
        raise ValueError(f"tag 不合法：{tag}")
    return f"Demiurge_update_{platform_of(target)}_{safe_tag}.json"


def release_base(tag: str, repository: str) -> str:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError(f"仓库名必须是 owner/repo：{repository}")
    safe_tag = str(tag).strip()
    if not SAFE_NAME_PATTERN.fullmatch(safe_tag):
        raise ValueError(f"tag 不合法：{tag}")
    return f"https://github.com/{repository}/releases/download/{safe_tag}"


def asset_url(base: str, name: str) -> str:
    if not SAFE_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"资产名不合法：{name}")
    return f"{base.rstrip('/')}/{name}"


def _require_dict(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    return value


def _require_name(value: object, label: str) -> str:
    text = str(value or "")
    if not SAFE_NAME_PATTERN.fullmatch(text):
        raise ValueError(f"{label} 不是合法的文件名：{value}")
    return text


def _require_sha256(value: object, label: str) -> str:
    text = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(text):
        raise ValueError(f"{label} 不是 SHA256：{value}")
    return text


def validate_pin(pin: dict, *, allow_file: bool = False) -> dict:
    pin = _require_dict(pin, "pin")
    if pin.get("schema_version") != PIN_SCHEMA_VERSION:
        raise ValueError(f"pin.schema_version 必须为 {PIN_SCHEMA_VERSION}")
    tag = str(pin.get("tag") or "")
    if not SAFE_NAME_PATTERN.fullmatch(tag):
        raise ValueError(f"pin.tag 不合法：{tag}")
    version = str(pin.get("version") or "")
    if version != normalize_version(tag):
        raise ValueError(f"pin.version 必须等于 tag 的归一版本：{tag} -> {normalize_version(tag)}")
    target = str(pin.get("target") or "")
    if not TARGET_PATTERN.fullmatch(target):
        raise ValueError(f"pin.target 不合法：{target}")

    base = str(pin.get("release_base") or "")
    schemes = ("https://", "file://") if allow_file else ("https://",)
    if not base.startswith(schemes):
        raise ValueError(f"pin.release_base 必须是 {'/'.join(schemes)}：{base}")
    mirrors = pin.get("mirrors") or []
    if not isinstance(mirrors, list) or any(not str(item).startswith("https://") for item in mirrors):
        raise ValueError("pin.mirrors 必须是 https 地址列表")

    expected_manifest = manifest_name(tag, target)
    if pin.get("manifest") != expected_manifest:
        raise ValueError(f"pin.manifest 必须是 {expected_manifest}，实际 {pin.get('manifest')}")

    required = pin.get("required_layers")
    if tuple(required or ()) != REQUIRED_LAYERS:
        raise ValueError(f"pin.required_layers 必须逐字等于 {list(REQUIRED_LAYERS)}")
    optional = pin.get("optional_layers")
    if optional is None:
        optional = []
    if not isinstance(optional, list) or tuple(optional) not in ((), OPTIONAL_LAYERS):
        raise ValueError(f"pin.optional_layers 只能是 [] 或 {list(OPTIONAL_LAYERS)}")
    if set(required) & set(optional):
        raise ValueError("必装层与可选层不能重叠")
    return pin


def load_pin(path: Path = DEFAULT_PIN) -> dict:
    return validate_pin(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_layer(name: str, layer: object) -> dict:
    layer = _require_dict(layer, f"layers.{name}")
    archive = _require_name(layer.get("archive"), f"layers.{name}.archive")
    try:
        size = int(layer.get("size"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"layers.{name}.size 必须是整数") from exc
    if size <= 0:
        raise ValueError(f"layers.{name}.size 必须大于 0")
    _require_sha256(layer.get("sha256"), f"layers.{name}.sha256")

    assets = layer.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError(f"layers.{name}.assets 不能为空")
    seen: set[str] = set()
    total = 0
    for index, asset in enumerate(assets):
        asset = _require_dict(asset, f"layers.{name}.assets[{index}]")
        asset_name = _require_name(asset.get("name"), f"layers.{name}.assets[{index}].name")
        if asset_name in seen:
            raise ValueError(f"layers.{name}.assets 出现重复资产：{asset_name}")
        seen.add(asset_name)
        try:
            asset_size = int(asset.get("size"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"layers.{name}.assets[{index}].size 必须是整数") from exc
        if asset_size <= 0:
            raise ValueError(f"layers.{name}.assets[{index}].size 必须大于 0")
        _require_sha256(asset.get("sha256"), f"layers.{name}.assets[{index}].sha256")
        total += asset_size
    if total != size:
        raise ValueError(f"layers.{name} 的分卷体积之和 {total} 不等于 archive.size {size}")

    names = [str(asset["name"]) for asset in assets]
    if len(names) == 1:
        if names[0] != archive:
            raise ValueError(f"layers.{name} 单档资产名必须等于 archive：{archive}")
    else:
        prefix = f"{archive_stem(archive)}.part"
        for asset_name in names:
            if not asset_name.startswith(prefix):
                raise ValueError(f"layers.{name} 分卷名必须以 {prefix} 开头：{asset_name}")
    return layer


def validate_manifest(manifest: dict, pin: dict, *, include_rag: bool = False) -> dict:
    manifest = _require_dict(manifest, "manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"manifest.schema_version 必须为 {MANIFEST_SCHEMA_VERSION}")
    if manifest.get("target") != pin["target"]:
        raise ValueError(f"manifest.target 与 pin 不一致：{manifest.get('target')} != {pin['target']}")
    if normalize_version(str(manifest.get("app_version") or "")) != pin["version"]:
        raise ValueError(
            f"manifest.app_version 与 pin.version 不一致：{manifest.get('app_version')} != {pin['version']}"
        )
    for key in ("base_id", "application_id"):
        value = str(manifest.get(key) or "").strip()
        if not value:
            raise ValueError(f"manifest 缺少 {key}")

    layers = _require_dict(manifest.get("layers"), "manifest.layers")
    required = list(pin["required_layers"])
    if include_rag:
        required.extend(pin.get("optional_layers") or [])
    for name in required:
        if name not in layers:
            raise ValueError(f"manifest 缺少 {name} 层")
        validate_layer(name, layers[name])
    if "rag" in layers and str(manifest.get("rag_id") or "").strip() == "":
        raise ValueError("manifest 带 rag 层但缺少 rag_id")

    seen: set[str] = set()
    for name, layer in layers.items():
        for asset in _require_dict(layer, f"layers.{name}").get("assets") or []:
            asset_name = str(_require_dict(asset, f"layers.{name}.asset").get("name") or "")
            if asset_name in seen:
                raise ValueError(f"不同层复用了同一个资产名：{asset_name}")
            seen.add(asset_name)
    return manifest


def verify_layer_assets(manifest: dict, assets_dir: Path, *, include_rag: bool = False) -> None:
    """离线包预检：清单里每一层所需的分卷/单档必须在目录里且哈希一致。"""
    import hashlib

    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
        return value.hexdigest()

    for name in ("base", "application") + (("rag",) if include_rag else ()):
        layer = manifest["layers"].get(name)
        if layer is None:
            continue
        for asset in layer.get("assets") or []:
            path = Path(assets_dir) / str(asset["name"])
            if not path.is_file():
                raise FileNotFoundError(f"离线包缺少 {asset['name']}")
            if path.stat().st_size != int(asset["size"]) or digest(path) != str(asset["sha256"]).lower():
                raise ValueError(f"离线包资产校验失败：{asset['name']}")


def render_pin(
    tag: str,
    repository: str,
    *,
    target: str = DEFAULT_TARGET,
    mirrors: list[str] | None = None,
) -> dict:
    tag = str(tag).strip()
    pin = {
        "schema_version": PIN_SCHEMA_VERSION,
        "tag": tag,
        "version": normalize_version(tag),
        "target": target,
        "release_base": release_base(tag, repository),
        "manifest": manifest_name(tag, target),
        "required_layers": list(REQUIRED_LAYERS),
        "optional_layers": list(OPTIONAL_LAYERS),
        "mirrors": list(mirrors or []),
    }
    return validate_pin(pin)


def write_pin(pin: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--pin", type=Path, default=DEFAULT_PIN)
    validate.add_argument("--manifest", type=Path)
    validate.add_argument("--include-rag", action="store_true")

    pin = sub.add_parser("pin")
    pin.add_argument("--tag", required=True)
    pin.add_argument("--repository", required=True)
    pin.add_argument("--target", default=DEFAULT_TARGET)
    pin.add_argument("--mirror", action="append", default=[])
    pin.add_argument("--output", type=Path, default=DEFAULT_PIN)

    version = sub.add_parser("version")
    version.add_argument("--tag", required=True)

    args = parser.parse_args()
    if args.command == "version":
        print(normalize_version(args.tag))
        return 0

    if args.command == "pin":
        payload = render_pin(args.tag, args.repository, target=args.target, mirrors=args.mirror)
        print(write_pin(payload, args.output))
        return 0

    payload = load_pin(args.pin)
    if args.manifest is not None:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        validate_manifest(manifest, payload, include_rag=args.include_rag)
        verify_layer_assets(manifest, args.manifest.parent, include_rag=args.include_rag)
    print(f"pin OK：{payload['tag']} / {payload['target']} / 必装 {payload['required_layers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
