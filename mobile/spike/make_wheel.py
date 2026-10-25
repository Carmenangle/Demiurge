"""Assemble a Chaquopy-compatible wheel from a cargo-built pydantic_core .so."""
from __future__ import annotations

import base64
import hashlib
import sys
import zipfile
from pathlib import Path

VERSION = "2.46.4"
MULTIARCH = {"arm64_v8a": "aarch64-linux-android", "x86_64": "x86_64-linux-android"}
ABI_TAG = {"arm64_v8a": "arm64_v8a", "x86_64": "x86_64"}
TARGET_DIR = {"arm64_v8a": "aarch64-linux-android", "x86_64": "x86_64-linux-android"}

SPIKE = Path(__file__).resolve().parent
SRC = SPIKE / "pydantic_core-2.46.4" / "python" / "pydantic_core"
OUT = SPIKE / "wheelhouse"

METADATA = """Metadata-Version: 2.1
Name: pydantic_core
Version: {version}
Summary: Core functionality for Pydantic validation and serialization
License: MIT
Requires-Python: >=3.9
Requires-Dist: typing-extensions>=4.14.1
""".format(version=VERSION)


def digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build(abi_key: str) -> Path:
    so = (
        SPIKE
        / "pydantic_core-2.46.4"
        / "target"
        / TARGET_DIR[abi_key]
        / "release"
        / "lib_pydantic_core.so"
    )
    if not so.is_file():
        raise SystemExit(f"missing {so}")
    platform_tag = f"android_24_{ABI_TAG[abi_key]}"
    ext = f"_pydantic_core.cpython-313-{MULTIARCH[abi_key]}.so"
    dist_info = f"pydantic_core-{VERSION}.dist-info"
    wheel_name = f"pydantic_core-{VERSION}-cp313-cp313-{platform_tag}.whl"

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / wheel_name
    entries: dict[str, bytes] = {}
    for path in sorted(SRC.iterdir()):
        if path.is_file() and path.suffix in {".py", ".pyi"} or path.name == "py.typed":
            entries[f"pydantic_core/{path.name}"] = path.read_bytes()
    entries[f"pydantic_core/{ext}"] = so.read_bytes()
    entries[f"{dist_info}/METADATA"] = METADATA.encode()
    entries[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: demiurge-spike\n"
        "Root-Is-Purelib: false\n"
        f"Tag: cp313-cp313-{platform_tag}\n"
    ).encode()
    entries[f"{dist_info}/top_level.txt"] = b"pydantic_core\n"

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
    for key in sys.argv[1:] or ["arm64_v8a"]:
        build(key)
