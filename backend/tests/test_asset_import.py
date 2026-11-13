"""资产库导入：元信息解析（ComfyUI / A1111 / 视频容器）与导入服务/路由。

隔离纪律：DB_PATH 指向 tmp（conftest 未隔离）；rag_store._store 换 FakeStore；
缩略图换空实现——测试不碰真实 Chroma/嵌入端点，也不跑 ffmpeg。
"""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image, PngImagePlugin

from app import db as app_db
from app.routers import rag as rag_router
from app.services import asset_import, asset_store, media_metadata, rag_store, repo_meta, thumbnail_store
from app.services.rag_backend import EmbedConfig


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(app_db, "DB_PATH", tmp_path / "app.db")
    app_db.init_db()
    return app_db


class FakeStore:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def add_documents(self, documents, ids=None):
        for doc, doc_id in zip(documents, ids or []):
            self.docs[doc_id] = {"content": doc.page_content, "metadata": dict(doc.metadata)}

    def get(self, ids=None, where=None):  # noqa: ARG002 - _inherit_existing_fields 只按 id 取
        if ids:
            doc = self.docs.get(ids[0])
            return {"ids": [ids[0]] if doc else [],
                    "documents": [doc["content"]] if doc else [],
                    "metadatas": [doc["metadata"]] if doc else []}
        return {"ids": [], "documents": [], "metadatas": []}


@pytest.fixture()
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(rag_store, "_store", lambda collection, cfg: fake)
    # 缩略图不在本刀测试面内，且视频假字节会拉 ffmpeg ⇒ 统一置空
    monkeypatch.setattr(thumbnail_store, "ensure_thumb", lambda path: None)
    return fake


_COMFY_GRAPH = {
    "1": {"class_type": "CLIPTextEncode",
          "inputs": {"text": "masterpiece, 1girl, <lora:styleA:0.7>", "clip": ["2", 0]}},
    "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "animagine.safetensors"}},
    "3": {"class_type": "LoraLoaderModelOnly",
          "inputs": {"lora_name": "styleA.safetensors", "model": ["2", 0], "strength_model": 0.7}},
    "4": {"class_type": "Seed (rgthree)", "inputs": {"seed": 123456}},
    "5": {"class_type": "KSamplerAdvanced",
          "inputs": {"positive": ["1", 0], "negative": ["6", 0], "steps": 28, "cfg": 5.5,
                     "sampler_name": "euler_ancestral", "scheduler": "normal",
                     "noise_seed": ["4", 0], "denoise": 1.0, "model": ["3", 0],
                     "latent_image": ["7", 0]}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad hands"}},
    "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
}


def _png_bytes(info: dict[str, str], size=(64, 96)) -> bytes:
    meta = PngImagePlugin.PngInfo()
    for key, value in info.items():
        meta.add_text(key, value)
    buf = io.BytesIO()
    Image.new("RGB", size, (32, 32, 32)).save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


# ===== 解析器（纯函数）=====

def test_a1111_parameters_正负拆分与参数归一():
    parsed = media_metadata.parse_a1111_parameters(
        "1girl, solo\nNegative prompt: lowres, bad hands\n"
        "Steps: 30, Sampler: DPM++ 2M, Schedule type: Karras, CFG scale: 7, "
        "Seed: 42, Size: 512x768, Model: anything-v5, Denoising strength: 0.4"
    )
    assert parsed["source"] == "a1111"
    assert parsed["prompt"] == "1girl, solo"
    assert parsed["negativePrompt"] == "lowres, bad hands"
    assert parsed["params"] == {
        "steps": 30, "sampler": "DPM++ 2M", "scheduler": "Karras", "cfg": 7.0,
        "seed": 42, "width": 512, "height": 768, "model": "anything-v5", "denoise": 0.4,
    }


def test_a1111_提取_lora标签():
    parsed = media_metadata.parse_a1111_parameters(
        "1girl, <lora:styleA:0.7>, <lora:styleB>\nSteps: 20, Sampler: Euler, CFG scale: 4, Seed: 1"
    )
    assert parsed["params"]["loraNames"] == ["styleA", "styleB"]


def test_comfyui_graph_解析提示词模型_lora与采样参数():
    parsed = media_metadata.parse_comfyui_graph(json.dumps(_COMFY_GRAPH))
    assert parsed["source"] == "comfyui"
    assert parsed["prompt"] == "masterpiece, 1girl, <lora:styleA:0.7>"
    assert parsed["negativePrompt"] == "bad hands"
    params = parsed["params"]
    assert params["model"] == "animagine.safetensors"
    assert params["loraNames"] == ["styleA.safetensors"]
    assert params["seed"] == 123456          # Seed (rgthree) 值节点经连线解析
    assert params["steps"] == 28
    assert params["cfg"] == 5.5
    assert params["sampler"] == "euler_ancestral"
    assert (params["width"], params["height"]) == (832, 1216)


def test_extract_image_metadata_以文件实际像素覆盖latent尺寸():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "a.png"
        target.write_bytes(_png_bytes({"prompt": json.dumps(_COMFY_GRAPH)}))
        meta = media_metadata.extract_image_metadata(target)
    assert (meta["width"], meta["height"]) == (64, 96)
    assert (meta["params"]["width"], meta["params"]["height"]) == (64, 96)


# ===== 导入服务 =====

def test_导入png_落盘并提取元信息(db, store, tmp_path):
    files = io.BytesIO(_png_bytes({"prompt": json.dumps(_COMFY_GRAPH)}))
    result = asset_import.import_media(
        str(tmp_path), "r1", "我的图.png", files, embed_cfg=EmbedConfig())
    item = result["item"]
    assert result["embedded"] is True
    assert item["mediaType"] == "image"
    assert item["source"] == "import"
    assert item["prompt"] == "masterpiece, 1girl, <lora:styleA:0.7>"
    assert item["modelName"] == "animagine.safetensors"
    assert item["loraNames"] == ["styleA.safetensors"]
    gen = item["sourceMeta"]["generation"]
    assert gen["source"] == "comfyui"
    assert gen["negativePrompt"] == "bad hands"
    assert gen["params"]["seed"] == 123456
    assert item["sourceMeta"]["originalName"] == "我的图.png"
    assert item["sourceMeta"]["byteSize"] > 0
    # 落盘位置：<作品>/pictures/原文件名.png
    saved = tmp_path / "r1" / "pictures" / "我的图.png"
    assert saved.is_file()
    # 向量库也拿到同一条记录（渲染文本走提示词，不是占位）
    doc = next(iter(store.docs.values()))
    assert "1girl" in doc["content"]
    assert doc["metadata"]["source"] == "import"


def test_导入视频走videos目录且保留提取参数(db, store, tmp_path, monkeypatch):
    monkeypatch.setattr(asset_import.media_metadata, "extract_video_metadata", lambda path: {
        "kind": "video", "format": "MP4", "width": 832, "height": 1504, "duration": 10.2,
        "source": "container", "prompt": "slow pan, rain", "negativePrompt": "",
        "params": {"durationSeconds": 10.2, "width": 832, "height": 1504, "fps": 24.0,
                   "encoder": "Lavf61"},
    })
    result = asset_import.import_media(str(tmp_path), "r1", "片段.mp4", io.BytesIO(b"\x00" * 128))
    item = result["item"]
    assert item["mediaType"] == "video"
    assert item["prompt"] == "slow pan, rain"
    assert item["sourceMeta"]["generation"]["params"]["fps"] == 24.0
    assert (tmp_path / "r1" / "videos" / "片段.mp4").is_file()


def test_重名文件不覆盖已有文件(db, store, tmp_path):
    data = _png_bytes({"parameters": "1girl\nSteps: 20, Sampler: Euler, CFG scale: 4, Seed: 1"})
    asset_import.import_media(str(tmp_path), "r1", "same.png", io.BytesIO(data))
    asset_import.import_media(str(tmp_path), "r1", "same.png", io.BytesIO(data))
    names = sorted(p.name for p in (tmp_path / "r1" / "pictures").iterdir())
    assert names == ["same.png", "same_2.png"]


def test_不支持的扩展名抛ValueError(db, store, tmp_path):
    with pytest.raises(ValueError, match="不支持的文件类型"):
        asset_import.import_media(str(tmp_path), "r1", "notes.txt", io.BytesIO(b"x"))


def test_嵌入失败仍保留资产记录(db, tmp_path, monkeypatch):
    def boom(collection, cfg):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(rag_store, "_store", boom)
    monkeypatch.setattr(thumbnail_store, "ensure_thumb", lambda path: None)
    data = _png_bytes({"parameters": "1girl\nSteps: 20, Sampler: Euler, CFG scale: 4, Seed: 1"})
    result = asset_import.import_media(
        str(tmp_path), "r1", "a.png", io.BytesIO(data),
        embed_cfg=EmbedConfig(base_url="http://127.0.0.1:9", mode="remote"),
    )
    assert result["embedded"] is False
    assert "embedding down" in result["embedError"]
    assert asset_store.get_row(result["item"]["id"]) is not None


# ===== 路由 =====

def _route_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(rag_router.router, prefix="/api/rag")
    return TestClient(app)


def test_导入路由_多文件与单文件失败并存(db, store, tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: str(tmp_path))
    png = _png_bytes({"parameters": "1girl\nSteps: 20, Sampler: Euler, CFG scale: 4, Seed: 1"})
    client = _route_client()
    resp = client.post(
        "/api/rag/assets/import",
        data={"repo_id": "r1", "output_dir": str(tmp_path)},
        files=[("files", ("ok.png", png, "image/png")),
               ("files", ("bad.txt", b"hello", "text/plain"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["item"]["prompt"] == "1girl"
    assert body["errors"] == [{"name": "bad.txt", "error": "不支持的文件类型：.txt"}]


def test_导入路由_未配置仓库文件夹拒绝(db, store, tmp_path, monkeypatch):
    monkeypatch.setattr(repo_meta, "output_dir_from_state", lambda: "")
    client = _route_client()
    resp = client.post(
        "/api/rag/assets/import",
        data={"repo_id": "r1", "output_dir": str(tmp_path)},
        files=[("files", ("a.png", b"x", "image/png"))],
    )
    assert resp.status_code == 400
    assert "仓库文件夹" in resp.json()["detail"]
