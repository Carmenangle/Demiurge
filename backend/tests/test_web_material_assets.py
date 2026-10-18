"""P2-1 完整刀：上网素材「落盘即资产记录」。

覆盖链：save_web_material → rag_store.index_web_material_asset → index_generation
（Chroma 记录带 source/source_url/tags/source_meta，repo 作用域＝合成 web-materials）；
delete_web_material → delete_web_material_asset 同步删记录。

回归锁：index_generation 默认路径**不写** source 三键（既有生成链逐字不变）；
入库失败**不阻断保存**（文件已落盘即报成功，资产库缺席该条只留 warning）。
"""
from __future__ import annotations

import base64
import json

import pytest

from app.services import image_store, rag_store, view_urls, web_material_candidates

# 1x1 PNG（合法魔数）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeStore:
    """最小 Chroma 替身：add_documents / delete / get（按 id 存取）。

    add_documents 用赋值而非 append ⇒ 同 id 重复写即 upsert（与 Chroma 语义一致），
    幂等断言靠它。
    """

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def add_documents(self, documents, ids=None):
        for doc, doc_id in zip(documents, ids or []):
            self.docs[doc_id] = {"content": doc.page_content,
                                 "metadata": dict(doc.metadata)}

    def delete(self, ids=None):
        for i in ids or []:
            self.docs.pop(i, None)

    def get(self, ids=None, where=None):  # noqa: ARG002 - _find_store/_dump 用
        if ids:                                   # 按 id 取（_find_store 路径）
            i = ids[0]
            d = self.docs.get(i)
            return {"ids": [i] if d else [],
                    "documents": [d["content"]] if d else [],
                    "metadatas": [d["metadata"]] if d else []}
        return {"ids": list(self.docs),          # 全量导出（_dump 路径）
                "documents": [d["content"] for d in self.docs.values()],
                "metadatas": [d["metadata"] for d in self.docs.values()]}


@pytest.fixture()
def store(monkeypatch):
    """把 rag_store 的 collection 获取换成 FakeStore（不碰真 Chroma/嵌入端点）。"""
    fake = FakeStore()
    monkeypatch.setattr(rag_store, "_store", lambda collection, cfg: fake)
    return fake


def _register_candidate(url: str, *, provider: str = "danbooru",
                        source_url: str = "https://danbooru.donmai.us/posts/123",
                        tags: list[str] | None = None):
    web_material_candidates._CANDIDATES.clear()
    web_material_candidates.register_candidates(
        [{"full_url": url, "source_url": source_url, "title": "测试图",
          "tags": tags if tags is not None else ["1girl", "solo", "long_hair"],
          "author": "tester", "rating": "general"}],
        query="1girl solo", provider=provider,
    )


def _save(monkeypatch, tmp_path, url: str, title: str = "测试图"):
    monkeypatch.setattr(image_store, "_from_src", lambda *a, **k: (_PNG, "png"))
    return image_store.save_web_material(str(tmp_path), url, title=title)


def _url_of(res: dict) -> str:
    return view_urls.local_view(res["path"])


def _doc_id_of(res: dict) -> str:
    return rag_store.generation_doc_id(rag_store.WEB_MATERIALS_REPO_ID, _url_of(res))


# ===== 保存即入库 =====

def test_save_web_material_落盘即入库(monkeypatch, tmp_path, store):
    """保存成功 ⇒ 恰好一条资产记录：合成 repo、source、来源页、原站 tags、source_meta。"""
    url = "https://cdn.donmai.us/original/ab/cd/x.jpg"
    _register_candidate(url)
    res = _save(monkeypatch, tmp_path, url, title="测试图")

    doc_id = _doc_id_of(res)
    assert list(store.docs) == [doc_id]                    # 恰好一条，无重复写入
    doc = store.docs[doc_id]
    md = doc["metadata"]
    assert md["kind"] == "generation"
    assert md["repo_id"] == rag_store.WEB_MATERIALS_REPO_ID
    assert rag_store.WEB_MATERIALS_REPO_ID == "web-materials"
    assert md["source"] == "danbooru"
    assert md["source_url"] == "https://danbooru.donmai.us/posts/123"
    assert "1girl" in md["tags"] and "solo" in md["tags"] and "long_hair" in md["tags"]
    assert md["media_type"] == "image"
    meta = json.loads(md["source_meta"])
    assert meta["author"] == "tester"
    assert meta["rating"] == "general"
    assert meta["query"] == "1girl solo"
    assert meta["provider"] == "danbooru"
    # 嵌入文本含标题+标签（不退化到「生成图片」占位）
    assert "测试图" in doc["content"] and "1girl" in doc["content"]
    web_material_candidates._CANDIDATES.clear()


def test_重复入库幂等_同图同doc_id(monkeypatch, tmp_path, store):
    """同 image_url 重复入库走同一 doc_id ⇒ upsert 不产生第二条。"""
    url = "https://cdn.donmai.us/original/ab/cd/x.jpg"
    _register_candidate(url)
    res = _save(monkeypatch, tmp_path, url)
    prov = image_store._load_provenance(
        image_store.web_materials_dir(str(tmp_path)))[res["filename"]]

    assert rag_store.index_web_material_asset(_url_of(res), prov) is True
    assert rag_store.index_web_material_asset(_url_of(res), prov) is True
    assert list(store.docs) == [_doc_id_of(res)]
    web_material_candidates._CANDIDATES.clear()


def test_source_归一化_非图站provider归web(monkeypatch, tmp_path, store):
    """bing-images 等非图站 provider ⇒ source 归一为 web（枚举不无限膨胀）。"""
    url = "https://img.example/1.png"
    _register_candidate(url, provider="bing-images",
                        source_url="https://img.example/page", tags=[])
    res = _save(monkeypatch, tmp_path, url)
    md = store.docs[_doc_id_of(res)]["metadata"]
    assert md["source"] == "web"
    assert md["source_url"] == "https://img.example/page"
    web_material_candidates._CANDIDATES.clear()


# ===== 删除同步 =====

def test_delete_web_material_同步删资产记录(monkeypatch, tmp_path, store):
    """删素材文件 ⇒ 资产记录同步消失（不留幽灵条目）。"""
    url = "https://cdn.donmai.us/original/ab/cd/x.jpg"
    _register_candidate(url)
    res = _save(monkeypatch, tmp_path, url)
    assert _doc_id_of(res) in store.docs

    assert image_store.delete_web_material(str(tmp_path), res["filename"]) is True
    assert _doc_id_of(res) not in store.docs
    web_material_candidates._CANDIDATES.clear()


def test_delete_未入库素材_删除记录返回False不报错(monkeypatch, tmp_path, store):
    """从没入库过的素材（本刀之前保存的）删记录时静默返回 False。"""
    url = "https://cdn.donmai.us/original/ab/cd/x.jpg"
    _register_candidate(url)
    res = _save(monkeypatch, tmp_path, url)
    store.docs.clear()                                     # 模拟「从未入库」
    assert rag_store.delete_web_material_asset(_url_of(res)) is False
    web_material_candidates._CANDIDATES.clear()


# ===== 失败语义 =====

def test_素材入库失败不阻断保存(monkeypatch, tmp_path, store):
    """嵌入端点挂掉 ⇒ 保存照常成功（文件在、返回正常），只库裡缺席。"""
    url = "https://cdn.donmai.us/original/ab/cd/x.jpg"
    _register_candidate(url)

    def _boom(*a, **k):
        raise RuntimeError("embed down")

    monkeypatch.setattr(rag_store, "index_generation", _boom)
    res = _save(monkeypatch, tmp_path, url)
    assert res["filename"].endswith(".png")
    saved = image_store.web_materials_dir(str(tmp_path)) / res["filename"]
    assert saved.exists()                                   # 文件已落盘
    assert store.docs == {}                                 # 库里没有，但保存成功了
    web_material_candidates._CANDIDATES.clear()


def test_index_web_material_asset_空url直接返回False(store):
    assert rag_store.index_web_material_asset("", {}) is False
    assert rag_store.index_web_material_asset("   ", {"tags": ["a"]}) is False
    assert rag_store.delete_web_material_asset("") is False


# ===== 回归锁：既有生成链默认路径逐字不变 =====

def test_index_generation_默认路径不写_source三键(store):
    """不传 source 三参 ⇒ metadata 不出现这三个键（既有记录形状不变）。"""
    image_url = "/local-view?path=/tmp/x.png"
    rag_store.index_generation("r1", None, prompt="p", tags="a,b",
                               image_url=image_url)
    doc_id = rag_store.generation_doc_id("r1", image_url)
    assert doc_id.startswith("gen-")
    md = store.docs[doc_id]["metadata"]
    assert "source" not in md
    assert "source_url" not in md
    assert "source_meta" not in md
    assert md["kind"] == "generation"
    # ⚠ _auto_tags 从 tags + prompt 双侧切分 ⇒ "p" 也成分标签（改造前即此行为）
    assert md["tags"] == "a,b,p"


def test_index_generation_显式传source才写键(store):
    image_url = "/local-view?path=/tmp/y.png"
    rag_store.index_generation("r1", None, prompt="p", image_url=image_url,
                               source="pixiv", source_url="https://www.pixiv.net/artworks/1",
                               source_meta='{"author":"a"}')
    md = store.docs[rag_store.generation_doc_id("r1", image_url)]["metadata"]
    assert md["source"] == "pixiv"
    assert md["source_url"] == "https://www.pixiv.net/artworks/1"
    assert md["source_meta"] == '{"author":"a"}'


# ===== embed 配置从 user_state 解析（后端自发链） =====

def test_embed_config_from_state_解析嵌套embedModel(monkeypatch, tmp_path):
    from app import config

    (tmp_path / "user_state.json").write_text(json.dumps(
        {"settings": {"embedModel": {
            "baseUrl": "http://localhost:11434/v1", "apiKey": "k",
            "modelName": "qwen3-embedding:latest", "mode": "local",
            "modelDir": "D:/models/embed", "rerankerDir": "D:/models/rerank",
            "proxyUrl": "http://127.0.0.1:7890"}}}), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    cfg = rag_store.embed_config_from_state()
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.api_key == "k"
    assert cfg.embed_model == "qwen3-embedding:latest"
    assert cfg.mode == "local"
    assert cfg.model_dir == "D:/models/embed"
    assert cfg.reranker_dir == "D:/models/rerank"
    assert cfg.proxy == "http://127.0.0.1:7890"


def test_embed_config_from_state_缺失回退远程空配置(monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)      # 无 user_state.json
    cfg = rag_store.embed_config_from_state()
    assert cfg.base_url == "" and cfg.api_key == "" and cfg.mode == "remote"


# ===== 读侧投影：素材记录带回来源字段 =====

def test_list_generations_投影source字段(monkeypatch, tmp_path, store):
    """读侧投影带 source/sourceUrl/sourceMeta（camelCase 契约，前端详情面板用）。"""
    url = "https://cdn.donmai.us/original/ab/cd/x.jpg"
    _register_candidate(url)
    res = _save(monkeypatch, tmp_path, url)

    items = rag_store.list_generations(rag_store.WEB_MATERIALS_REPO_ID, None)
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "danbooru"
    assert it["sourceUrl"] == "https://danbooru.donmai.us/posts/123"
    assert isinstance(it["sourceMeta"], dict)
    assert it["sourceMeta"]["author"] == "tester"
    assert it["sourceMeta"]["rating"] == "general"
    assert it["image_url"] == _url_of(res)
    web_material_candidates._CANDIDATES.clear()
