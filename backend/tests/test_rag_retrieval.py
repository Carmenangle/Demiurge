from app.services import rag_backend, rag_store
from app.services import rag_retrieval
from langchain_core.documents import Document


def test_ordinary_rag_rrf_rewards_dense_and_sparse_agreement():
    shared = {"id": "shared", "content": "target"}
    hits = rag_retrieval.rrf_fuse([
        ("dense:system", [{"id": "dense-only", "content": "x"}, shared]),
        ("bm25", [shared]),
    ], 3)

    assert hits[0]["id"] == "shared"
    assert hits[0]["channels"] == ["dense:system", "bm25"]


def test_ordinary_rag_sparse_retrieves_exact_term():
    hits = rag_retrieval.sparse_rank("WD14Tagger", [
        {"id": "noise", "content": "通用图像描述"},
        {"id": "exact", "content": "使用 WD14Tagger 反推标签"},
    ], 2)

    assert [hit["id"] for hit in hits] == ["exact"]


def test_ordinary_rag_skips_reranker_when_dense_and_sparse_agree(monkeypatch):
    class FakeStore:
        def get(self):
            return {
                "documents": ["WD14Tagger 用于反推标签"],
                "metadatas": [{"kind": "system", "title": "节点说明"}],
            }

        def similarity_search_by_vector(self, vector, k, filter):
            return [Document(
                page_content="WD14Tagger 用于反推标签",
                metadata={"kind": "system", "title": "节点说明"},
            )]

    monkeypatch.setattr(rag_backend, "embed_query", lambda cfg, query: [0.1, 0.2])
    monkeypatch.setattr(rag_store, "_store", lambda collection, cfg: FakeStore())
    monkeypatch.setattr(
        rag_store.reranker,
        "rerank",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应精排确定性结果")),
    )

    hits = rag_store.retrieve_with_trace("home", rag_backend.EmbedConfig(), "WD14Tagger", 4)

    assert hits[0]["channels"] == ["dense:system", "bm25"]


def test_retrieve_with_trace_embeds_query_once_and_uses_bm25_fallback(monkeypatch):
    calls = []

    class FakeEmbeddings:
        def embed_query(self, query):
            calls.append(query)
            return [0.1, 0.2]

    class FakeStore:
        def __init__(self, rows, dense):
            self.rows = rows
            self.dense = dense

        def get(self):
            return {
                "documents": [row.page_content for row in self.rows],
                "metadatas": [row.metadata for row in self.rows],
            }

        def similarity_search_by_vector(self, vector, k, filter):
            assert vector == [0.1, 0.2]
            assert filter == {"kind": {"$ne": "generation"}}
            return self.dense[:k]

    system_doc = Document(page_content="系统帮助", metadata={"kind": "system", "title": "帮助"})
    exact_doc = Document(page_content="WD14Tagger 用于反推标签", metadata={"kind": "document", "title": "节点说明"})
    generation = Document(page_content="WD14Tagger", metadata={"kind": "generation"})
    stores = {
        rag_store.SYSTEM_COLLECTION: FakeStore([system_doc], [system_doc]),
        rag_store._repo_collection("home"): FakeStore([exact_doc, generation], []),
    }
    monkeypatch.setattr(rag_backend, "embed_query", lambda cfg, query: FakeEmbeddings().embed_query(query))
    monkeypatch.setattr(rag_store, "_store", lambda collection, cfg: stores[collection])
    monkeypatch.setattr(rag_store.reranker, "rerank", lambda *args, **kwargs: [])

    hits = rag_store.retrieve_with_trace("home", rag_backend.EmbedConfig(), "WD14Tagger", 4)

    assert calls == ["WD14Tagger"]
    assert any(hit["content"] == exact_doc.page_content and "bm25" in hit["channels"] for hit in hits)
    assert all(hit["kind"] != "generation" for hit in hits)


def test_story_rag_can_exclude_global_system_collection(monkeypatch):
    accessed = []

    class FakeStore:
        def get(self):
            return {
                "documents": ["作品内角色记忆"],
                "metadatas": [{"kind": "document", "title": "角色记忆"}],
            }

        def similarity_search_by_vector(self, vector, k, filter):
            return []

    def fake_store(collection, cfg):
        accessed.append(collection)
        assert collection != rag_store.SYSTEM_COLLECTION
        return FakeStore()

    monkeypatch.setattr(rag_backend, "embed_query", lambda cfg, query: [0.1])
    monkeypatch.setattr(rag_store, "_store", fake_store)
    monkeypatch.setattr(rag_store.reranker, "rerank", lambda *args, **kwargs: [])

    hits = rag_store.retrieve_with_trace(
        "story", rag_backend.EmbedConfig(), "角色", 4, include_system=False)

    assert accessed == [rag_store._repo_collection("story"), rag_store._repo_collection("story")]
    assert hits and all(hit["source"] == "repo:story" for hit in hits)


def test_asset_delete_removes_only_rag_record_and_keeps_local_file(tmp_path, monkeypatch):
    image = tmp_path / "kept.png"
    image.write_bytes(b"png")
    deleted = []

    class FakeStore:
        def __init__(self, found):
            self.found = found

        def get(self, ids=None):
            if ids and self.found:
                return {
                    "ids": ids,
                    "documents": ["prompt"],
                    "metadatas": [{
                        "kind": "generation",
                        "image_url": f"http://127.0.0.1:8010/api/local-view?path={image}",
                    }],
                }
            return {"ids": [], "documents": [], "metadatas": []}

        def delete(self, ids):
            deleted.extend(ids)

    system = FakeStore(False)
    repo = FakeStore(True)
    monkeypatch.setattr(
        rag_store, "_store",
        lambda collection, _cfg: system if collection == rag_store.SYSTEM_COLLECTION else repo,
    )

    assert rag_store.delete_doc("gen-1", "repo", rag_backend.EmbedConfig()) is True
    assert deleted == ["gen-1"]
    assert image.is_file()


def test_prune_removes_only_missing_local_generation(tmp_path, monkeypatch):
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"png")
    missing = tmp_path / "missing.png"
    deleted = []

    class FakeStore:
        def delete(self, ids):
            deleted.extend(ids)

    rows = [
        {"id": "missing", "kind": "generation",
         "image_url": f"http://127.0.0.1:8010/api/local-view?path={missing}"},
        {"id": "existing", "kind": "generation",
         "image_url": f"http://127.0.0.1:8010/api/local-view?path={existing}"},
        {"id": "remote", "kind": "generation", "image_url": "https://example.com/image.png"},
        {"id": "doc", "kind": "document",
         "image_url": f"http://127.0.0.1:8010/api/local-view?path={missing}"},
    ]
    monkeypatch.setattr(rag_store, "_store", lambda *_args: FakeStore())
    monkeypatch.setattr(rag_store, "_dump", lambda *_args: rows)

    assert rag_store.prune_missing_generations(
        "repo", rag_backend.EmbedConfig(),
    ) == 1
    assert deleted == ["missing"]


def test_prune_deletes_legacy_remote_view_only_when_comfyui_confirms_missing(monkeypatch):
    """legacy remote-view 直链（未落盘留存）只有 ComfyUI 明确 404 才算裂图。"""
    deleted = []
    probe_calls = []

    class FakeStore:
        def delete(self, ids):
            deleted.extend(ids)

    def fake_probe(url, filename, type, subfolder):
        probe_calls.append((url, filename, type, subfolder))
        return "missing"

    rows = [
        {"id": "legacy", "kind": "generation",
         "image_url": ("http://127.0.0.1:8010/api/comfyui/view?filename=old.png"
                       "&type=output&subfolder=&url=http%3A%2F%2F127.0.0.1%3A8188")},
    ]
    monkeypatch.setattr(rag_store, "_store", lambda *_args: FakeStore())
    monkeypatch.setattr(rag_store, "_dump", lambda *_args: rows)
    monkeypatch.setattr(rag_store.comfyui_client, "probe_view", fake_probe)

    assert rag_store.prune_missing_generations("repo", rag_backend.EmbedConfig()) == 1
    assert deleted == ["legacy"]
    assert probe_calls == [("http://127.0.0.1:8188", "old.png", "output", "")]


def test_prune_keeps_legacy_remote_view_when_file_exists_or_cannot_judge(monkeypatch):
    """ComfyUI 仍能取到（200）或未起/异常（无法判定）都不得删，防误删真源。"""
    deleted = []

    class FakeStore:
        def delete(self, ids):
            deleted.extend(ids)

    def make_rows(url_suffix):
        return [{"id": "legacy", "kind": "generation",
                 "image_url": ("http://127.0.0.1:8010/api/comfyui/view?filename=a.png"
                               f"&type=output&subfolder=&url={url_suffix}")}]

    monkeypatch.setattr(rag_store, "_store", lambda *_args: FakeStore())
    monkeypatch.setattr(rag_store, "_dump", lambda *_args: make_rows("http%3A%2F%2F127.0.0.1%3A8188"))
    for verdict in ("ok", "unreachable"):
        monkeypatch.setattr(rag_store.comfyui_client, "probe_view", lambda *a, **k: verdict)
        assert rag_store.prune_missing_generations("repo", rag_backend.EmbedConfig()) == 0
        assert deleted == []


def test_probe_view_maps_status_and_never_raises(monkeypatch):
    """probe_view 三态映射：200→ok、404→missing、其余/网络异常/非法 url→unreachable。"""
    from app.services import comfyui_client

    class FakeResp:
        def __init__(self, status):
            self.status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeSession:
        def __init__(self, behavior):
            self.behavior = behavior
            self.calls = []

        def get(self, url, timeout, stream):
            self.calls.append(url)
            if callable(self.behavior):
                return self.behavior(url)
            return FakeResp(self.behavior)

    monkeypatch.setattr(comfyui_client, "_DIRECT_SESSION", FakeSession(200))
    assert comfyui_client.probe_view(
        "http://127.0.0.1:8188", "a.png") == "ok"
    monkeypatch.setattr(comfyui_client, "_DIRECT_SESSION", FakeSession(404))
    assert comfyui_client.probe_view(
        "http://127.0.0.1:8188", "a.png") == "missing"
    monkeypatch.setattr(comfyui_client, "_DIRECT_SESSION", FakeSession(500))
    assert comfyui_client.probe_view(
        "http://127.0.0.1:8188", "a.png") == "unreachable"

    def explode(_url):
        raise ConnectionError("ComfyUI 未起")

    unreachable_session = FakeSession(explode)
    monkeypatch.setattr(comfyui_client, "_DIRECT_SESSION", unreachable_session)
    assert comfyui_client.probe_view(
        "http://127.0.0.1:8188", "a.png") == "unreachable"

    # 非法 url（不过 ComfyUI 白名单）在探测前拦截，不发起任何请求
    calls_before = len(unreachable_session.calls)
    assert comfyui_client.probe_view("http://evil.com", "a.png") == "unreachable"
    assert len(unreachable_session.calls) == calls_before
