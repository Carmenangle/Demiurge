"""资产库导入：把用户拖入/选中的图片·视频落盘到作品目录，并提取内嵌元信息。

与生成链的区别只在「文件从哪来」：落盘位置（`<作品>/pictures|videos`）、
资产记录形状、缩略图、列表读路径都与生成产物共用同一套（asset_store + rag_store）。
元信息解析规则单点属主在 `media_metadata`，本模块只做「解析结果 → 记录字段」的映射：

- prompt / negativePrompt → 记录 prompt（负面提示词与生成参数进 `source_meta.generation`）
- model → model_name；loraNames → lora_names（与生成链同字段，详情面板直接显示）
- 其余参数（steps/sampler/cfg/seed/fps/duration…）→ `source_meta.generation.params`

入库顺序：先 `asset_store.upsert_asset`（主表是真源，列表读它），再尽力
`rag_store.index_generation` 补向量（embedding 未配置/失败不影响导入成功）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import BinaryIO

from app.services import (
    asset_store,
    image_store,
    media_metadata,
    pathnames,
    rag_store,
    repo_meta,
    thumbnail_store,
    view_urls,
)
from app.services.rag_backend import EmbedConfig

_CHUNK = 1024 * 1024


def _unique_dest(base: Path, stem: str, suffix: str) -> Path:
    """保留原文件名的落盘路径；重名时追加 _2/_3…，不覆盖已有文件。"""
    candidate = base / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = base / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    return base / f"{stem}_{os.urandom(4).hex()}{suffix}"


def _write_stream(fileobj: BinaryIO, dest: Path) -> int:
    """流式落盘（视频可能数百 MB，不整块读内存），返回字节数。"""
    tmp = dest.with_name(f".{dest.name}.{os.urandom(4).hex()}.tmp")
    size = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = fileobj.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return size


def import_media(
    output_dir: str,
    repo_id: str,
    filename: str,
    fileobj: BinaryIO,
    *,
    embed_cfg: EmbedConfig | None = None,
) -> dict:
    """导入单个媒体文件 → {item, embedded, embedError}。

    文件类型不支持抛 ValueError（路由层转 400，其余文件照常导入）；
    嵌入失败只回填 embedError，不抛（记录已在主表，可后续补索引）。
    """
    if not output_dir:
        raise ValueError("未配置「仓库文件夹」")
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    kind = media_metadata.media_kind(suffix)
    if not kind:
        raise ValueError(f"不支持的文件类型：{suffix or '（无扩展名）'}")

    root = repo_meta.repo_folder(output_dir, repo_id)
    base = image_store.media_dir(root, kind)
    stem = pathnames.safe_dir(Path(name).stem, fallback="import")[:80]
    dest = _unique_dest(base, stem, suffix)
    byte_size = _write_stream(fileobj, dest)

    meta = (media_metadata.extract_video_metadata(dest) if kind == "video"
            else media_metadata.extract_image_metadata(dest))
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    prompt = str(meta.get("prompt") or "").strip()
    negative = str(meta.get("negativePrompt") or "").strip()
    model = str(params.get("model") or "").strip()
    loras = [str(n).strip() for n in (params.get("loraNames") or []) if str(n).strip()]

    image_url = view_urls.local_view(str(dest))
    created_at = int(time.time() * 1000)
    generation = {
        "source": str(meta.get("source") or ""),
        "negativePrompt": negative,
        "params": params,
    }
    source_meta = json.dumps({
        "generation": generation,
        "originalName": name,
        "byteSize": byte_size,
        "importedAt": created_at,
    }, ensure_ascii=False)
    doc_id = rag_store.generation_doc_id(repo_id, image_url)
    record = {
        "id": doc_id,
        "repo_id": repo_id,
        "kind": "generation",
        "media_type": kind,
        "source": "import",
        "image_url": image_url,
        "thumb_url": thumbnail_store.thumb_url_for(image_url),
        "prompt": prompt,
        "description": "",
        "tags": [],
        "template_name": "",
        "model_name": model,
        "lora_names": ",".join(loras),
        "created_at": created_at,
        "source_meta": source_meta,
    }
    asset_store.upsert_asset(record)

    embedded = False
    embed_error = ""
    if embed_cfg is not None:
        try:
            rag_store.index_generation(
                repo_id, embed_cfg, prompt, image_url=image_url, created_at=created_at,
                model_name=model, lora_names=",".join(loras), media_type=kind,
                source="import", source_meta=source_meta,
            )
            embedded = True
        except Exception as exc:  # noqa: BLE001 [EXC-留痕] 向量失败不阻断导入，主表已可浏览
            embed_error = str(exc)
    row = asset_store.get_row(doc_id)
    return {
        "item": asset_store._row_to_item(row) if row else record,
        "embedded": embedded,
        "embedError": embed_error,
    }
