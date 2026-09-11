"""下载并校验固定版 Qwen3-VL-Embedding-2B（支持分段并行与断点复用）。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "Qwen/Qwen3-VL-Embedding-2B"
REVISION = "9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda"
SIZE = 4_255_140_312
SHA256 = "c73fa9caeddeb3ff831d46c085a7a5708343248ca777e90f2d486964464509c1"
ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "data" / "models" / "vl-embedding" / "Qwen3-VL-Embedding-2B"
CHUNK_SIZE = 8 * 1024 * 1024
PARTS = (SIZE + CHUNK_SIZE - 1) // CHUNK_SIZE
WORKERS = 8
URL = ("https://modelscope.cn/api/v1/models/Qwen/Qwen3-VL-Embedding-2B/repo"
       "?Revision=master&FilePath=model.safetensors")


def _download(index: int) -> Path:
    start = index * CHUNK_SIZE
    end = min(SIZE - 1, start + CHUNK_SIZE - 1)
    target = MODEL_DIR / f".model.part.{index:02d}"
    expected = end - start + 1
    if target.is_file() and target.stat().st_size == expected:
        return target
    target.unlink(missing_ok=True)
    subprocess.run([
        "curl.exe", "--noproxy", "*", "-L", "--fail", "--retry", "5",
        "--retry-delay", "2", "--range", f"{start}-{end}", "-o", str(target), URL,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if target.stat().st_size != expected:
        raise RuntimeError(f"分段 {index} 大小错误")
    return target


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)  # ModelScope 国内源直连，避免本机外网代理限速
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        parts = list(pool.map(_download, range(PARTS)))
    weight = MODEL_DIR / ".model.safetensors.assembling"
    digest = hashlib.sha256()
    with weight.open("wb") as output:
        for part in parts:
            with part.open("rb") as source:
                while block := source.read(1024 * 1024):
                    output.write(block)
                    digest.update(block)
    if weight.stat().st_size != SIZE or digest.hexdigest() != SHA256:
        raise RuntimeError("Qwen3-VL-Embedding 权重大小或 SHA-256 校验失败")
    weight.replace(MODEL_DIR / "model.safetensors")
    for part in parts:
        part.unlink(missing_ok=True)
    (MODEL_DIR / "artifact-manifest.json").write_text(json.dumps({
        "repo": REPO, "huggingface_revision": REVISION, "modelscope_revision": "master",
        "file": "model.safetensors",
        "size": SIZE, "sha256": SHA256, "format": "safetensors",
        "license": "apache-2.0",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(MODEL_DIR), "sha256": SHA256}))


if __name__ == "__main__":
    main()
