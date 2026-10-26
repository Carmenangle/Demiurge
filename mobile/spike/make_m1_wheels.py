"""Assemble Chaquopy-compatible cp313 Android wheels for the five Rust deps (M1)."""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import zipfile
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
SRC = SPIKE / "src"
OUT = SPIKE / "wheelhouse"
# PC 侧 venv 的 site-packages 是取源码的源；按本仓库位置解析，允许环境变量指到别处。
REPO = SPIKE.parents[1]
SITE = Path(os.environ.get("DEMIURGE_SITE_PACKAGES") or REPO / "backend/.venv/Lib/site-packages")

PLATFORM_TAG = "android_24_arm64_v8a"
MULTIARCH = "aarch64-linux-android"
EXT_SUFFIX = f".cpython-313-{MULTIARCH}.so"


def digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def add_tree(entries: dict[str, bytes], root: Path, prefix: str, patterns=("*.py", "*.pyi", "py.typed")) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if not any(path.match(pat) for pat in patterns):
            continue
        rel = path.relative_to(root).as_posix()
        entries[f"{prefix}/{rel}"] = path.read_bytes()


def build(
    name: str,
    version: str,
    so: Path,
    ext_rel: str,
    py_root: Path,
    package_prefix: str,
    metadata: str,
    extra_files: dict[str, Path] | None = None,
) -> Path:
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    wheel_name = f"{name.replace('-', '_')}-{version}-cp313-cp313-{PLATFORM_TAG}.whl"
    entries: dict[str, bytes] = {}
    add_tree(entries, py_root, package_prefix)
    for rel, path in (extra_files or {}).items():
        entries[rel] = path.read_bytes()
    entries[ext_rel] = so.read_bytes()
    entries[f"{dist_info}/METADATA"] = metadata.encode()
    entries[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: demiurge-m1\n"
        "Root-Is-Purelib: false\n"
        f"Tag: cp313-cp313-{PLATFORM_TAG}\n"
    ).encode()
    entries[f"{dist_info}/top_level.txt"] = (package_prefix.split("/")[0] + "\n").encode()
    record = [f"{p},sha256={digest(d)},{len(d)}" for p, d in sorted(entries.items())]
    record.append(f"{dist_info}/RECORD,,")
    entries[f"{dist_info}/RECORD"] = ("\n".join(record) + "\n").encode()

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / wheel_name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, data in entries.items():
            zf.writestr(p, data)
    print(f"{target.name} ({target.stat().st_size / 1e6:.2f} MB, {len(entries)} files)")
    return target


def main() -> None:
    build(
        "orjson", "3.11.9",
        SRC / "orjson-3.11.9/target/aarch64-linux-android/release/liborjson.so",
        f"orjson/orjson{EXT_SUFFIX}",
        SRC / "orjson-3.11.9/pysrc/orjson", "orjson",
        "Metadata-Version: 2.1\nName: orjson\nVersion: 3.11.9\nRequires-Python: >=3.10\n",
    )
    build(
        "ormsgpack", "1.12.2",
        SRC / "ormsgpack-1.12.2/target/aarch64-linux-android/release/libormsgpack.so",
        f"ormsgpack/ormsgpack{EXT_SUFFIX}",
        SRC / "ormsgpack-1.12.2/python/ormsgpack", "ormsgpack",
        "Metadata-Version: 2.1\nName: ormsgpack\nVersion: 1.12.2\nRequires-Python: >=3.10\n",
    )
    rpds_files = {f"rpds/{p.name}": p for p in [SITE / "rpds/__init__.py", SITE / "rpds/__init__.pyi", SITE / "rpds/py.typed"]}
    build(
        "rpds-py", "2026.6.3",
        SRC / "rpds_py-2026.6.3/target/aarch64-linux-android/release/librpds.so",
        f"rpds/rpds{EXT_SUFFIX}",
        Path("__empty_rpds__"), "rpds",
        "Metadata-Version: 2.1\nName: rpds-py\nVersion: 2026.6.3\nRequires-Python: >=3.11\n",
        extra_files=rpds_files,
    )
    build(
        "uuid-utils", "0.17.0",
        SRC / "uuid_utils-0.17.0/target/aarch64-linux-android/release/libuuid_utils.so",
        f"uuid_utils/_uuid_utils{EXT_SUFFIX}",
        SRC / "uuid_utils-0.17.0/python/uuid_utils", "uuid_utils",
        "Metadata-Version: 2.1\nName: uuid-utils\nVersion: 0.17.0\nRequires-Python: >=3.10\n",
    )
    build(
        "tiktoken", "0.13.0",
        SRC / "tiktoken-0.13.0/target/aarch64-linux-android/release/libtiktoken.so",
        f"tiktoken/_tiktoken{EXT_SUFFIX}",
        SRC / "tiktoken-0.13.0/tiktoken", "tiktoken",
        "Metadata-Version: 2.1\nName: tiktoken\nVersion: 0.13.0\nRequires-Python: >=3.9\n"
        "Requires-Dist: regex\nRequires-Dist: requests\n",
        extra_files={"tiktoken_ext/openai_public.py": SRC / "tiktoken-0.13.0/tiktoken_ext/openai_public.py"},
    )
    # jiter 的 sdist 不含 python 包目录，maturin 从 pyproject 生成三行 __init__；直接取已装版本
    jiter_files = {f"jiter/{p.name}": p for p in [SITE / "jiter/__init__.py", SITE / "jiter/__init__.pyi", SITE / "jiter/py.typed"]}
    build(
        "jiter", "0.16.0",
        SRC / "jiter-0.16.0/target/aarch64-linux-android/release/libjiter_python.so",
        f"jiter/jiter{EXT_SUFFIX}",
        Path("__empty_jiter__"), "jiter",
        "Metadata-Version: 2.1\nName: jiter\nVersion: 0.16.0\nRequires-Python: >=3.9\n",
        extra_files=jiter_files,
    )


if __name__ == "__main__":
    main()
