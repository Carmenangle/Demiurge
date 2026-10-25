"""P1-1/2/3 资产主表：schema、列表/分页、标签统计、FTS5 搜索、回收站、双写对账。

⚠ 隔离纪律：`app.db.DB_PATH` 指向 tmp（conftest 未隔离它——本模块自己钉），
绝不写真实 app.db。
"""
from __future__ import annotations

import pytest

from app import db as app_db
from app.services import asset_store, rag_store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(app_db, "DB_PATH", tmp_path / "app.db")
    app_db.init_db()
    return app_db


def _mk(asset_id: str, repo_id: str = "r1", ts: int = 1000, **over) -> dict:
    item = {
        "id": asset_id, "repo_id": repo_id, "kind": "generation",
        "media_type": "image", "source": "", "image_url": f"image://{asset_id}",
        "thumb_url": "", "prompt": "1girl solo", "description": "",
        "tags": ["1girl", "solo"], "template_name": "", "model_name": "",
        "lora_names": "", "created_at": ts, "source_url": "", "source_meta": "",
        "derived_from": "[]",
    }
    item.update(over)
    return item


# ===== 写入/读取形状 =====

def test_upsert_and_list_roundtrip_shape(db):
    """落库 → 列表：GenLike camelCase 形状与 rag_store.list_generations 一致。"""
    asset_store.upsert_asset(_mk("a1", source="pixiv", source_url="https://pixiv/1",
                                 source_meta='{"author":"x"}',
                                 tags=["1girl", "solo", "long_hair"]))

    items = asset_store.list_assets(["r1"])
    assert len(items) == 1
    it = items[0]
    assert set(it.keys()) == {
        "id", "repo_id", "prompt", "description", "image_url", "tags",
        "templateName", "modelName", "loraNames", "mediaType", "thumbUrl",
        "derivedFrom", "source", "sourceUrl", "sourceMeta", "created_at",
    }
    assert it["tags"] == ["1girl", "solo", "long_hair"]
    assert it["source"] == "pixiv"
    assert it["sourceMeta"] == {"author": "x"}
    assert it["derivedFrom"] == []


def test_keyset_pagination_multi_repo(db):
    asset_store.upsert_asset(_mk("r1-a", "r1", 1200))
    asset_store.upsert_asset(_mk("r1-b", "r1", 1100))
    asset_store.upsert_asset(_mk("r2-a", "r2", 1350))
    asset_store.upsert_asset(_mk("r2-b", "r2", 1000))

    page1 = asset_store.list_assets(["r1", "r2"], limit=2)
    assert [i["id"] for i in page1] == ["r2-a", "r1-a"]
    page2 = asset_store.list_assets(["r1", "r2"], limit=2, before_ts=page1[-1]["created_at"])
    assert [i["id"] for i in page2] == ["r1-b", "r2-b"]
    assert {i["id"] for i in page1}.isdisjoint({i["id"] for i in page2})


def test_tag_stats_excludes_trash(db):
    asset_store.upsert_asset(_mk("a1", tags=["1girl", "solo"]))
    asset_store.upsert_asset(_mk("a2", tags=["1girl"]))
    asset_store.upsert_asset(_mk("a3", tags=["1girl"]))
    asset_store.soft_delete("a3")

    stats = {s["tag"]: s["count"] for s in asset_store.tag_stats(["r1"])}
    assert stats["1girl"] == 2      # 回收站里的不计入
    assert stats["solo"] == 1


# ===== FTS5 结构化搜索 =====

def test_search_fts_free_text_and_tag_and(db):
    asset_store.upsert_asset(_mk("a1", tags=["1girl", "solo"], prompt="1girl solo standing"))
    asset_store.upsert_asset(_mk("a2", tags=["1girl"], prompt="1girl sitting"))
    asset_store.upsert_asset(_mk("a3", tags=["scenery"], prompt="mountain scenery"))

    # 多标签 AND：必须带齐
    both = asset_store.search_assets(["r1"], tags=["1girl", "solo"])
    assert [i["id"] for i in both] == ["a1"]
    only_girl = asset_store.search_assets(["r1"], tags=["1girl"])
    assert {i["id"] for i in only_girl} == {"a1", "a2"}

    # FTS 全文（英文 token）
    fts = asset_store.search_assets(["r1"], q="sitting")
    assert [i["id"] for i in fts] == ["a2"]

    # 中文子串走 LIKE 兜底（FTS tokenizer 对中文不分词）
    asset_store.upsert_asset(_mk("a4", tags=[], prompt="森林中的少女", description=""))
    cjk = asset_store.search_assets(["r1"], q="森林")
    assert {i["id"] for i in cjk} == {"a4"}

    # 搜索不含回收站
    asset_store.soft_delete("a1")
    assert asset_store.search_assets(["r1"], tags=["solo"]) == []


def test_search_media_type_filter(db):
    asset_store.upsert_asset(_mk("img", media_type="image"))
    asset_store.upsert_asset(_mk("vid", media_type="video"))
    assert [i["id"] for i in asset_store.search_assets(["r1"], media_type="video")] == ["vid"]


# ===== 回收站 =====

def test_soft_delete_restore_purge_cycle(db):
    asset_store.upsert_asset(_mk("a1", tags=["keep"]))
    assert asset_store.soft_delete("a1") is True

    assert asset_store.list_assets(["r1"]) == []                      # 存活列表不含
    trash = asset_store.list_assets(["r1"], deleted=True)
    assert [i["id"] for i in trash] == ["a1"]                         # 回收站可见
    assert trash[0]["tags"] == ["keep"]                               # 标签不丢

    row = asset_store.restore("a1")
    assert row is not None and row["id"] == "a1"                      # 返回行供重建向量
    assert [i["id"] for i in asset_store.list_assets(["r1"])] == ["a1"]
    assert asset_store.list_assets(["r1"], deleted=True) == []

    asset_store.soft_delete("a1")
    assert asset_store.purge("a1") is True
    assert asset_store.list_assets(["r1"], deleted=True) == []


def test_upsert_does_not_resurrect_trashed(db):
    """回填/重建索引 upsert 不得复活回收站记录（deleted_at 不被覆盖）。"""
    asset_store.upsert_asset(_mk("a1"))
    asset_store.soft_delete("a1")
    asset_store.upsert_asset(_mk("a1", ts=2000))
    assert asset_store.list_assets(["r1"]) == []
    assert len(asset_store.list_assets(["r1"], deleted=True)) == 1


def test_clear_trash_scoped(db):
    asset_store.upsert_asset(_mk("a1", "r1"))
    asset_store.upsert_asset(_mk("a2", "r2"))
    asset_store.soft_delete("a1")
    asset_store.soft_delete("a2")
    assert asset_store.clear_trash(["r1"]) == 1
    assert [i["id"] for i in asset_store.list_assets(["r2"], deleted=True)] == ["a2"]
    assert asset_store.clear_trash([]) == 1


# ===== 双写（rag_store 写 Chroma 后同步主表） =====

class _FakeStore:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def add_documents(self, documents, ids=None):
        for doc, did in zip(documents, ids or []):
            self.docs[did] = {"content": doc.page_content, "metadata": dict(doc.metadata)}

    def get(self, ids=None, where=None):  # noqa: ARG002
        if ids:
            i = ids[0]
            d = self.docs.get(i)
            return {"ids": [i] if d else [], "documents": [d["content"]] if d else [],
                    "metadatas": [d["metadata"]] if d else []}
        return {"ids": list(self.docs),
                "documents": [d["content"] for d in self.docs.values()],
                "metadatas": [d["metadata"] for d in self.docs.values()]}

    def delete(self, ids=None):
        for i in ids or []:
            self.docs.pop(i, None)

    def update_document(self, doc_id, document):
        self.docs[doc_id] = {"content": document.page_content, "metadata": dict(document.metadata)}


@pytest.fixture()
def fake_store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(rag_store, "_store", lambda *_a: fake)
    return fake


def test_index_generation_double_writes_asset_row(db, fake_store):
    url = "/api/local-view?path=/tmp/x.png"
    rag_store.index_generation("r1", None, prompt="p", tags="1girl,solo",
                               image_url=url, description="desc")
    doc_id = rag_store.generation_doc_id("r1", url)
    assert doc_id in fake_store.docs                     # 向量侧
    rows = asset_store.list_assets(["r1"])
    assert [r["id"] for r in rows] == [doc_id]           # 主表侧
    assert set(rows[0]["tags"]) == {"1girl", "solo", "p"}
    assert rows[0]["description"] == "desc"
    assert rows[0]["created_at"] > 0


def test_delete_doc_moves_to_trash(db, fake_store):
    url = "/api/local-view?path=/tmp/y.png"
    rag_store.index_generation("r1", None, prompt="p", image_url=url)
    doc_id = rag_store.generation_doc_id("r1", url)
    assert rag_store.delete_doc(doc_id, "r1", None) is True
    assert doc_id not in fake_store.docs                            # 向量立即删
    assert asset_store.list_assets(["r1"]) == []                    # 存活列表不含
    assert [i["id"] for i in asset_store.list_assets(["r1"], deleted=True)] == [doc_id]


def test_set_doc_tags_and_description_sync_main_table(db, fake_store):
    url = "/api/local-view?path=/tmp/z.png"
    rag_store.index_generation("r1", None, prompt="p", image_url=url)
    doc_id = rag_store.generation_doc_id("r1", url)

    assert rag_store.set_doc_tags(doc_id, "r1", None, ["1girl", "smile"]) is True
    assert set(asset_store.list_assets(["r1"])[0]["tags"]) == {"1girl", "smile"}
    assert {s["tag"] for s in asset_store.tag_stats(["r1"])} == {"1girl", "smile"}

    assert rag_store.set_generation_description(doc_id, "r1", None, "新描述") is True
    assert asset_store.list_assets(["r1"])[0]["description"] == "新描述"
    assert [i["id"] for i in asset_store.search_assets(["r1"], q="新描述")] == [doc_id]


# ===== 路由层（P1-1 读路径 / P1-3 搜索 / P1-2 回收站端点） =====

def test_route_generations_reads_sqlite(db):
    from app.routers import rag as rag_router
    asset_store.upsert_asset(_mk("a1", "r1", 1200))
    asset_store.upsert_asset(_mk("a2", "r1", 1100))
    asset_store.upsert_asset(_mk("a3", "r1", 1000))

    page1 = rag_router.list_generations(rag_router.GenerationsRequest(repo_id="r1", limit=2))
    assert [i["id"] for i in page1["items"]] == ["a1", "a2"]
    assert page1["has_more"] is True
    tail = rag_router.list_generations(rag_router.GenerationsRequest(
        repo_id="r1", limit=2, before_ts=page1["items"][-1]["created_at"]))
    assert [i["id"] for i in tail["items"]] == ["a3"]
    assert tail["has_more"] is False


def test_route_generations_falls_back_to_chroma_when_db_empty(db, fake_store):
    """主表 0 行（未回填）⇒ 走向量库旧路径，列表不会变空。"""
    from app.routers import rag as rag_router
    url = "/api/local-view?path=/tmp/fb.png"
    rag_store.index_generation("r1", None, prompt="p", image_url=url)
    asset_store.delete_rows([rag_store.generation_doc_id("r1", url)])   # 模拟主表未回填
    # 双写钩子会重新写入 ⇒ 先清空再调（只验证回退分支存在）
    asset_store.clear_trash([])
    conn = asset_store.get_connection()
    with conn:
        conn.execute("delete from assets")
    conn.close()

    res = rag_router.list_generations(rag_router.GenerationsRequest(repo_id="r1"))
    assert [i["prompt"] for i in res["items"]] == ["p"]      # 回退 Chroma 拿到


def test_route_search_assets(db):
    from app.routers import rag as rag_router
    asset_store.upsert_asset(_mk("a1", tags=["1girl", "solo"]))
    asset_store.upsert_asset(_mk("a2", tags=["1girl"]))

    res = rag_router.search_assets(rag_router.SearchAssetsRequest(
        repo_ids=["r1"], tags=["1girl", "solo"], limit=10))
    assert [i["id"] for i in res["items"]] == ["a1"]
    assert res["has_more"] is False


def test_route_trash_restore_purge(db, fake_store):
    from fastapi import HTTPException
    from app.routers import rag as rag_router

    url = "/api/local-view?path=/tmp/trash.png"
    rag_store.index_generation("r1", None, prompt="p", tags="1girl", image_url=url)
    doc_id = rag_store.generation_doc_id("r1", url)

    # 存活记录不能直接彻底删除（必须先删除到回收站）
    with pytest.raises(HTTPException) as ei:
        rag_router.purge_asset(rag_router.AssetIdRequest(id=doc_id, repo_id="r1"))
    assert ei.value.status_code == 400

    assert rag_store.delete_doc(doc_id, "r1", None) is True
    trash = rag_router.list_trash(rag_router.GenerationsRequest(repo_id="r1"))
    assert [i["id"] for i in trash["items"]] == [doc_id]

    # 恢复：重建向量 + 清 deleted_at（id 不变）
    res = rag_router.restore_asset(rag_router.AssetIdRequest(id=doc_id, repo_id="r1"))
    assert res["id"] == doc_id
    assert doc_id in fake_store.docs                       # 向量已重建
    assert [i["id"] for i in asset_store.list_assets(["r1"])] == [doc_id]
    assert rag_router.list_trash(rag_router.GenerationsRequest(repo_id="r1"))["items"] == []

    # 再删再彻底删除
    rag_store.delete_doc(doc_id, "r1", None)
    assert rag_router.purge_asset(rag_router.AssetIdRequest(id=doc_id, repo_id="r1"))["ok"] is True
    assert asset_store.get_row(doc_id) is None


def test_route_tag_stats_reads_sqlite(db):
    from app.routers import rag as rag_router
    asset_store.upsert_asset(_mk("a1", tags=["1girl", "solo"]))
    asset_store.upsert_asset(_mk("a2", tags=["1girl"]))
    res = rag_router.tag_stats(rag_router.TagStatsRequest(repo_ids=["r1"]))
    stats = {s["tag"]: s["count"] for s in res["items"]}
    assert stats == {"1girl": 2, "solo": 1}


# ===== 两段式反推（2026-10-18，对齐 ComfyUI「反推动漫」工作流产物形态）=====

def test_parse_tagged_text_two_paragraphs_and_artist_marker():
    from app.services import asset_library

    text = (
        "masterpiece, score_9, year 2025, highres, safe, 1girl, liduke, piromizu, "
        "@//liduke/, @//piromizu/, blonde hair, red eyes, pink visor\n\n"
        "A single girl with blonde hair and red eyes stands confidently, one hand raised "
        "in a peace sign, wearing a pink visor against a stark white background."
    )
    tags, desc = asset_library.parse_tagged_text(text)
    assert tags[:6] == ["masterpiece", "score_9", "year 2025", "highres", "safe", "1girl"]
    assert tags.count("liduke") == 1        # 裸名 + @//liduke/ 折算后去重
    assert tags.count("piromizu") == 1
    assert "blonde hair" in tags
    assert desc.startswith("A single girl with blonde hair")


def test_parse_tagged_text_tags_only_and_separators():
    from app.services import asset_library

    tags, desc = asset_library.parse_tagged_text("1girl, solo，smile; 下划线_tag\nsecond line tag")
    assert tags == ["1girl", "solo", "smile", "下划线_tag", "second line tag"]
    assert desc == ""
    assert asset_library.parse_tagged_text("") == ([], "")
    assert asset_library.parse_tagged_text("   \n\n  ") == ([], "")

    # 描述段含多个空行：全部归入描述（不被当成标签）
    tags2, desc2 = asset_library.parse_tagged_text("1girl, smile\n\n第一段描述。\n\n第二段描述。")
    assert tags2 == ["1girl", "smile"]
    assert "第一段描述。" in desc2 and "第二段描述。" in desc2


def test_route_tag_text_saves_snapshot_and_description(db, fake_store):
    from app.routers import rag as rag_router

    url = "/api/local-view?path=/tmp/tagtext.png"
    rag_store.index_generation("r1", None, prompt="p", image_url=url)
    doc_id = rag_store.generation_doc_id("r1", url)

    text = "masterpiece, score_9, 1girl, @//liduke/, blonde hair\n\nA girl stands in shallow water."
    out = rag_router.tag_text(rag_router.TagTextRequest(
        id=doc_id, repo_id="r1", text=text, model="vlm", label="两段式",
    ))
    assert out["tags"][:4] == ["masterpiece", "score_9", "1girl", "liduke"]
    assert out["saved_description"] is True
    # 描述同步到主表（并重建语义索引走的是 set_generation_description）
    assert asset_store.list_assets(["r1"])[0]["description"] == "A girl stands in shallow water."
    snaps = rag_store.list_tag_snapshots(doc_id, "r1", None)
    assert len(snaps["snapshots"]) == 1
    assert snaps["snapshots"][0]["model"] == "vlm"


def test_route_tag_text_rejects_empty_tags(db, fake_store):
    """模型没按两段式输出（只给一整句描述）⇒ 不把整句当标签，返回 400 让前端提示重试。"""
    from fastapi import HTTPException
    from app.routers import rag as rag_router
    from app.services import asset_library

    url = "/api/local-view?path=/tmp/tagtext2.png"
    rag_store.index_generation("r1", None, prompt="p", image_url=url)
    doc_id = rag_store.generation_doc_id("r1", url)

    sentence = ("A single girl with blonde hair and red eyes stands confidently in shallow "
                "water while wearing a pink visor and holding a flower")
    tags, desc = asset_library.parse_tagged_text(sentence)
    assert tags == [] and desc == sentence          # 护栏：整句 → 描述，不当标签
    with pytest.raises(HTTPException) as ei:
        rag_router.tag_text(rag_router.TagTextRequest(id=doc_id, repo_id="r1", text=sentence))
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei2:
        rag_router.tag_text(rag_router.TagTextRequest(id=doc_id, repo_id="r1", text="   "))
    assert ei2.value.status_code == 400


def test_tag_snapshot_ops_sync_main_table(db, fake_store):
    """快照新增/切换后主表标签必须同步（2026-10-18 用户实测：切换后刷新标签回退）。"""
    url = "/api/local-view?path=/tmp/snap.png"
    rag_store.index_generation("r1", None, prompt="p", image_url=url)
    doc_id = rag_store.generation_doc_id("r1", url)

    s1 = rag_store.add_tag_snapshot(doc_id, "r1", None, ["a", "b"], model="m1")
    assert set(asset_store.list_assets(["r1"])[0]["tags"]) == {"a", "b"}
    s2 = rag_store.add_tag_snapshot(doc_id, "r1", None, ["c"], model="m2")
    assert set(asset_store.list_assets(["r1"])[0]["tags"]) == {"c"}
    assert s1 and s2

    assert rag_store.set_active_tag_snapshot(doc_id, "r1", None, s1["id"]) is True
    assert set(asset_store.list_assets(["r1"])[0]["tags"]) == {"a", "b"}

    assert rag_store.delete_tag_snapshot(doc_id, "r1", None, s1["id"]) is True
    assert set(asset_store.list_assets(["r1"])[0]["tags"]) == {"c"}



# ===== 空内容不覆盖（2026-10-22 资产库「提示词全空」事故回归锁） =====
# 事故链：`index_generation` 的 Chroma 侧是 add（同 id 静默忽略），主表侧此前无条件覆盖。
# 「发送至对话框」（text: "" ⇒ prompt=""）或仓库同步补录（prompt=""）一次即可把主表
# 提示词清空，而 Chroma 保留真值 ⇒ 列表读主表 ⇒ 用户看到「（无提示词记录）」。

def test_upsert_blank_prompt_keeps_existing(db):
    """空 prompt 重入不得抹掉已有提示词（真机 130 条就是被这样清的）。"""
    asset_store.upsert_asset(_mk("a1", prompt="masterpiece, 1girl, beach"))
    asset_store.upsert_asset(_mk("a1", prompt=""))

    row = asset_store.get_row("a1")
    assert row["prompt"] == "masterpiece, 1girl, beach"
    # FTS 也要能搜到（触发器按行重建，提示词不能从索引里消失）
    assert [i["id"] for i in asset_store.search_assets(["r1"], q="beach")] == ["a1"]


def test_upsert_non_blank_prompt_still_overwrites(db):
    """非空新值照常覆盖（改提示词重入库不能被「保护」误挡）。"""
    asset_store.upsert_asset(_mk("a1", prompt="old prompt"))
    asset_store.upsert_asset(_mk("a1", prompt="new prompt"))
    assert asset_store.get_row("a1")["prompt"] == "new prompt"


def test_upsert_blank_description_and_tags_keep_existing(db):
    """描述/标签同样「空不覆盖」，且 asset_tags 不被清空（防 tags_json 与结构化表劈叉）。"""
    asset_store.upsert_asset(_mk("a1", prompt="p", description="银发少女", tags=["1girl", "solo"]))
    asset_store.upsert_asset(_mk("a1", prompt="", description="", tags=[]))

    row = asset_store.get_row("a1")
    assert row["description"] == "银发少女"
    assert set(asset_store.list_assets(["r1"])[0]["tags"]) == {"1girl", "solo"}
    assert {s["tag"] for s in asset_store.tag_stats(["r1"])} == {"1girl", "solo"}


def test_index_generation_reentry_with_blank_prompt_keeps_prompt(db, fake_store):
    """「发送至对话框」式重入（同图同仓库、prompt 空）不得清空提示词与标签。"""
    url = "/api/local-view?path=/tmp/reentry.png"
    rag_store.index_generation("r1", None, prompt="masterpiece, 1girl", tags="1girl",
                               image_url=url, description="少女")
    doc_id = rag_store.generation_doc_id("r1", url)
    # 前端 SendToChatModal「发送至对话框」payload.text = "" ⇒ prompt=""
    rag_store.index_generation("r1", None, prompt="", image_url=url)

    assert fake_store.docs[doc_id]["metadata"]["prompt"] == "masterpiece, 1girl"
    item = asset_store.list_assets(["r1"])[0]
    assert item["prompt"] == "masterpiece, 1girl"
    assert item["description"] == "少女"
    assert "1girl" in item["tags"]


def test_index_generation_reentry_with_new_prompt_updates(db, fake_store):
    """重入带了新提示词 ⇒ 以新值为准（继承只补空，不锁死旧值）。"""
    url = "/api/local-view?path=/tmp/reentry2.png"
    rag_store.index_generation("r1", None, prompt="old", image_url=url)
    doc_id = rag_store.generation_doc_id("r1", url)
    rag_store.index_generation("r1", None, prompt="new", image_url=url)
    assert asset_store.get_row(doc_id)["prompt"] == "new"
