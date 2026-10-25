"""Assemble a Chaquopy-compatible wheel from the cargo-built chromadb_rust_bindings cdylib."""
from __future__ import annotations

import base64
import hashlib
import sys
import zipfile
from pathlib import Path

VERSION = "0.1.0"
MULTIARCH = {"arm64_v8a": "aarch64-linux-android", "x86_64": "x86_64-linux-android"}
TARGET_DIR = {"arm64_v8a": "aarch64-linux-android", "x86_64": "x86_64-linux-android"}

SPIKE = Path(__file__).resolve().parent
OUT = SPIKE / "wheelhouse"

METADATA = """Metadata-Version: 2.1
Name: chromadb-rust-bindings
Version: {version}
Summary: Chroma rust bindings (Android cross-build for the Demiurge spike)
License: Apache-2.0
Requires-Python: >=3.9
""".format(version=VERSION)


def digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build(abi_key: str, profile: str = "debug") -> Path:
    so = (
        SPIKE
        / "chromadb-1.5.9"
        / "target"
        / TARGET_DIR[abi_key]
        / profile
        / "libchromadb_rust_bindings.so"
    )
    if not so.is_file():
        raise SystemExit(f"missing {so}")
    platform_tag = f"android_24_{abi_key}"
    ext = f"chromadb_rust_bindings.cpython-313-{MULTIARCH[abi_key]}.so"
    dist_info = f"chromadb_rust_bindings-{VERSION}.dist-info"
    wheel_name = f"chromadb_rust_bindings-{VERSION}-cp313-cp313-{platform_tag}.whl"

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / wheel_name
    entries: dict[str, bytes] = {}
    entries[ext] = so.read_bytes()
    entries[f"{dist_info}/METADATA"] = METADATA.encode()
    entries[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: demiurge-spike\n"
        "Root-Is-Purelib: false\n"
        f"Tag: cp313-cp313-{platform_tag}\n"
    ).encode()
    entries[f"{dist_info}/top_level.txt"] = b"chromadb_rust_bindings\n"

    record_lines = [
        f"{name},sha256={digest(data)},{len(data)}" for name, data in sorted(entries.items())
    ]
    record_lines.append(f"{dist_info}/RECORD,,")
    entries[f"{dist_info}/RECORD"] = ("\n".join(record_lines) + "\n").encode()

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    print(f"{target} ({target.stat().st_size / 1e6:.1f} MB)")
    return target


if __name__ == "__main__":
    args = sys.argv[1:]
    profile = "debug"
    flags = [a for a in args if a.startswith("--profile=")]
    for flag in flags:
        profile = flag.split("=", 1)[1]
        args.remove(flag)
    if "--release" in args:  # PLAN §5.5 的既有配方用的就是这个旗标
        profile = "release"
        args.remove("--release")
    for key in args or ["arm64_v8a"]:
        build(key, profile)
