"""Repack chromadb / langchain-chroma into platform-neutral wheels for Chaquopy.

chromadb 侧的三处改动（均为移动端硬约束，桌面 venv 不受影响）：
  1. 删掉随包分发的 chromadb_rust_bindings.abi3.so（manylinux，Android 无法加载），
     改由单独交叉编译的 chromadb-rust-bindings==0.1.0 提供同名顶层模块；
  2. 删掉 chromadb/test（不入运行期，省体积）；
  3. api/types.py 的 pybase64 换成标准库 base64（pybase64 是 C 扩展且无 Android 轮子）。
元数据同步裁掉只在服务端/默认嵌入函数路径才用到的依赖：
onnxruntime / tokenizers / kubernetes / bcrypt / typer / build / mmh3 / pybase64；
uvicorn[standard] 收窄成 uvicorn（standard extra 会拖 uvloop/httptools/watchfiles）。
langchain-chroma 只放宽 py3.13 的 numpy 下限（Chaquopy 最高 1.26.2）。
"""
from __future__ import annotations

import base64
import hashlib
import re
import zipfile
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
REPACK = SPIKE / "repack"
OUT = SPIKE / "wheelhouse"

CHROMADB_SRC = REPACK / "chromadb-1.5.9-cp39-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
LANGCHAIN_CHROMA_SRC = REPACK / "langchain_chroma-1.1.0-py3-none-any.whl"
LANGGRAPH_SQLITE_SRC = REPACK / "langgraph_checkpoint_sqlite-3.1.0-py3-none-any.whl"
MCP_SRC = REPACK / "mcp-2.0.0-py3-none-any.whl"

DROP_PREFIXES = ("chromadb_rust_bindings/", "chromadb/test/")
DROP_REQUIRES = {
    "build", "onnxruntime", "tokenizers", "kubernetes", "bcrypt", "typer",
    "mmh3", "pybase64",
}
ADD_REQUIRES = ["chromadb-rust-bindings==0.1.0"]

PYBASE64_CALLS = [
    (
        "        return pybase64.b64encode_as_string(\n",
        "        return base64.b64encode(\n",
    ),
    (
        "            packed_data = pybase64.b64decode(b64_string)\n",
        "            packed_data = base64.b64decode(b64_string)\n",
    ),
]


def digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def rewrite_metadata(text: str, drop: set[str], add: list[str]) -> str:
    # 新增的 Requires-Dist 必须落在头部字段区（首个空行之前），否则 pip 会把它们当成正文忽略
    headers, sep, body = text.partition("\n\n")
    out: list[str] = []
    for line in headers.splitlines():
        m = re.match(r"Requires-Dist:\s*([A-Za-z0-9_.\-]+)", line)
        if m and m.group(1).lower() in drop:
            continue
        if line.startswith("Requires-Dist: uvicorn[standard]"):
            line = line.replace("uvicorn[standard]", "uvicorn")
        if line.startswith("Provides-Extra:"):
            continue
        out.append(line)
    out.extend(f"Requires-Dist: {req}" for req in add)
    result = "\n".join(out) + sep + body
    header = result.partition("\n\n")[0].splitlines()
    for req in add:
        # 回归护栏：pip 只读头部字段区，写到正文里会静默漏装依赖（0.4.0 首建实锤）
        assert f"Requires-Dist: {req}" in header, f"Requires-Dist {req} not in metadata header"
    return result


def repack(src: Path, out_name: str, metadata_rewrite, new_version: str | None = None) -> Path:
    zin = zipfile.ZipFile(src)
    dist_info = next(
        n.split("/")[0] for n in zin.namelist() if n.endswith("/METADATA")
    )
    dist_name = dist_info[: -len(".dist-info")].rsplit("-", 1)[0]
    new_dist_info = (
        f"{dist_name}-{new_version}.dist-info" if new_version else dist_info
    )
    entries: dict[str, bytes] = {}
    for name in zin.namelist():
        if name.endswith("/") or name.startswith(DROP_PREFIXES):
            continue
        data = zin.read(name)
        if name == "chromadb/api/types.py":
            text = data.decode("utf-8").replace("import pybase64\n", "import base64\n", 1)
            for old, new in PYBASE64_CALLS:
                text = text.replace(old, new)
            assert "pybase64" not in text, "pybase64 patch incomplete"
            data = text.encode("utf-8")
        elif name == f"{dist_info}/METADATA":
            text = metadata_rewrite(data.decode("utf-8"))
            if new_version:
                text = re.sub(r"^Version: .*$", f"Version: {new_version}", text, count=1, flags=re.M)
                assert f"Version: {new_version}" in text
            data = text.encode("utf-8")
        elif name == f"{dist_info}/WHEEL":
            data = (
                "Wheel-Version: 1.0\n"
                "Generator: demiurge-m1-repack\n"
                "Root-Is-Purelib: true\n"
                "Tag: py3-none-any\n"
            ).encode()
        elif name == f"{dist_info}/RECORD":
            continue
        if new_dist_info != dist_info and name.startswith(dist_info + "/"):
            rel = new_dist_info + name[len(dist_info):]
        else:
            rel = name
        entries[rel] = data

    record = [f"{p},sha256={digest(d)},{len(d)}" for p, d in sorted(entries.items())]
    record.append(f"{new_dist_info}/RECORD,,")
    entries[f"{new_dist_info}/RECORD"] = ("\n".join(record) + "\n").encode()

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / out_name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, data in entries.items():
            zf.writestr(p, data)
    print(f"{target.name} ({target.stat().st_size / 1e6:.2f} MB, {len(entries)} files)")
    return target


def metadata_chromadb(text: str) -> str:
    return rewrite_metadata(text, DROP_REQUIRES, ADD_REQUIRES)


def metadata_langchain_chroma(text: str) -> str:
    text = text.replace(
        "Requires-Dist: numpy>=2.1.0; python_version >= '3.13'",
        "Requires-Dist: numpy>=1.26.0; python_version >= '3.13'",
    )
    assert "numpy>=2.1.0" not in text
    return text


def metadata_langgraph_sqlite(text: str) -> str:
    # 只用到 checkpoint.sqlite.SqliteSaver，从不进入 langgraph.store.sqlite（那才是 sqlite-vec 的唯一使用点）
    text = re.sub(r"^Requires-Dist: sqlite-vec.*\n", "", text, flags=re.M)
    assert "sqlite-vec" not in text
    return text


def metadata_mcp(text: str) -> str:
    # pywin32 只在 Windows 生效；Android 上不存在也永远用不到，但 pip 在 Windows 主机解析时
    # 仍会按 sys_platform=win32 去要它（--platform 不影响环境标记），故从元数据里摘掉
    text = re.sub(r"^Requires-Dist: pywin32.*\n", "", text, flags=re.M)
    assert "pywin32" not in text
    return text


def main() -> None:
    repack(CHROMADB_SRC, "chromadb-1.5.9-py3-none-any.whl", metadata_chromadb)
    repack(
        LANGCHAIN_CHROMA_SRC,
        "langchain_chroma-1.1.0+android1-py3-none-any.whl",
        metadata_langchain_chroma,
        new_version="1.1.0+android1",
    )
    repack(
        LANGGRAPH_SQLITE_SRC,
        "langgraph_checkpoint_sqlite-3.1.0+android1-py3-none-any.whl",
        metadata_langgraph_sqlite,
        new_version="3.1.0+android1",
    )
    repack(
        MCP_SRC,
        "mcp-2.0.0+android1-py3-none-any.whl",
        metadata_mcp,
        new_version="2.0.0+android1",
    )


if __name__ == "__main__":
    main()
