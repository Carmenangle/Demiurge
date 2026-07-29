from app.services import node_index


class FakeRag:
    def list_node_packs(self, cfg):
        return [
            {"id": "b", "title": "Zulu", "node_names": ["n1", "n2"], "python_module": "m.b"},
            {"id": "a", "title": "alpha", "node_names": ["n3"], "python_module": "m.a"},
        ]

    def search_node_packs(self, cfg, need, k=8):
        return [{"id": need, "k": k}]

    def search_node_packs_many(self, cfg, needs, k=8, *, dense_indexes=None):
        return [self.search_node_packs(cfg, need, k=k) for need in needs]

    def get_node_pack(self, cfg, pack_id):
        return None if pack_id == "missing" else {"id": pack_id}

    def update_node_pack_content(self, cfg, pack_id, content):
        return pack_id != "missing"


def test_node_index_management_interface(monkeypatch):
    monkeypatch.setattr(node_index, "_rag", FakeRag())
    cfg = object()
    assert node_index.stats(cfg) == {"packs": 2, "nodes": 3}
    assert [item["id"] for item in node_index.list_packs(cfg)] == ["a", "b"]
    assert node_index.get_pack(cfg, "missing") is None
    assert node_index.update_pack_content(cfg, "missing", "x") is False


def test_node_index_search_interface(monkeypatch):
    monkeypatch.setattr(node_index, "_rag", FakeRag())
    assert node_index.search(object(), "need", 3) == [{"id": "need", "k": 24}]


def test_expand_query_anima_includes_separate_loader_terms():
    queries = node_index.expand_query("用 Anima 模型搭建文生图")
    joined = " ".join(queries).lower()
    assert "anima" in joined
    assert "unetloader" in joined
    assert "dualcliploader" in joined or "cliploader" in joined
    assert "vaeloader" in joined


def test_expand_query_scene_replacement_includes_segmentation_and_reference_control():
    queries = node_index.expand_query(
        "用 YOLO 和 SAM2 分割人物背景，扩展遮罩并裁剪，再用 Reference ControlNet 迁移姿势",
    )
    joined = " ".join(queries)

    assert "AILab_YoloV8" in joined
    assert "SAM2Segment" in joined
    assert "ImageCropByMask" in joined
    assert "ACN_AdvancedControlNetApply_v2" in joined
    assert "ACN_ReferenceControlNet" in joined
    assert "ACN_ReferencePreprocessor" in joined


def test_query_plan_prioritizes_capability_expansion():
    branches = node_index.plan_queries("用 Anima 模型搭建文生图")
    original = next(branch for branch in branches if branch.kind == "original")
    capabilities = [branch for branch in branches if branch.kind == "capability"]
    assert all(branch.weight > original.weight for branch in capabilities)
    assert any("UNETLoader" in branch.query for branch in capabilities)


def test_long_workflow_query_has_bounded_short_capability_branches():
    need = (
        "搭建动漫工作流：以 Anima 分离式 UNET、CLIP 和 VAE 为主链，可选 Turbo LoRA；"
        "用模式开关切换文生图空 latent 与图生图 VAE 编码，并切换 denoise；"
        "输入来自 D 站画廊或本地图片，D 站标签、WD14、llama.cpp 反推三路可选；"
        "画师标签单独输出并拼接提示词，最后采样、解码、对比和保存。"
    )

    branches = node_index.plan_queries(need)

    assert len(branches) <= 8
    capabilities = [branch for branch in branches if branch.kind == "capability"]
    assert capabilities
    assert all(need not in branch.query for branch in capabilities)
    assert max(len(branch.query) for branch in capabilities) < len(need)
    assert any("1hew_TextListToString" in branch.query for branch in capabilities)


def test_weighted_fusion_keeps_capability_top_hit_ahead_of_cross_branch_noise():
    noise = {"id": "noise", "title": "云端图片接口", "content": "image model api"}
    core = {"id": "core", "title": "nodes", "content": "UNETLoader VAELoader"}
    groups = [
        [noise, {"id": "other", "content": "other"}],
        [core, noise],
    ]
    hits = node_index._search_fused(groups, 4, weights=[0.35, 2.0], preserve_top=[False, True])
    assert hits[0]["id"] == "core"


def test_fusion_round_robins_preserved_capability_branches():
    groups = [
        [{"id": f"{prefix}{index}", "content": prefix} for index in range(3)]
        for prefix in ("a", "b", "c")
    ]

    hits = node_index._search_fused(groups, 6, preserve_top=[True, True, True])

    assert [hit["id"] for hit in hits] == ["a0", "b0", "c0", "a1", "b1", "c1"]


def test_fusion_tokenizes_each_candidate_once(monkeypatch):
    real_findall = node_index.re.findall
    calls = []

    def counted_findall(pattern, text):
        calls.append(text)
        return real_findall(pattern, text)

    monkeypatch.setattr(node_index.re, "findall", counted_findall)
    candidates = [
        {"id": str(index), "title": f"pack-{index}", "content": f"node {index}"}
        for index in range(20)
    ]

    node_index._search_fused([candidates], 20)

    assert len(calls) == 20


def test_fusion_merges_same_pack_across_query_branches():
    hits = node_index._search_fused([
        [{"id": "core", "content": "VAELoader", "node_names": ["VAELoader"]}],
        [{"id": "core", "content": "UNETLoader", "node_names": ["UNETLoader"]}],
    ], 2)

    assert hits[0]["node_names"] == ["VAELoader", "UNETLoader"]
    assert "VAELoader" in hits[0]["content"]
    assert "UNETLoader" in hits[0]["content"]


def test_capability_branch_prioritizes_explicit_node_names():
    hits = node_index._prioritize_named_nodes([
        {"id": "noise", "node_names": ["OtherLoader"]},
        {"id": "core", "node_names": ["UNETLoader", "VAELoader"]},
    ], "Anima UNETLoader VAELoader")

    assert hits[0]["id"] == "core"


def test_capability_branch_matches_node_names_ignoring_punctuation():
    hits = node_index._prioritize_named_nodes([
        {"id": "generic", "node_names": ["AnySwitch"]},
        {"id": "rgthree", "node_names": ["Any Switch (rgthree)"]},
    ], "Any Switch rgthree ImpactConditionalBranch")

    assert hits[0]["id"] == "rgthree"


def test_search_fuses_multiple_query_hits(monkeypatch):
    class MultiRag(FakeRag):
        def search_node_packs(self, cfg, need, k=8):
            if "unetloader" in need.lower():
                return [{"id": "unet", "title": "UNET", "content": "unetloader"}]
            return [{"id": "base", "title": "基础", "content": "基础出图"}]

    monkeypatch.setattr(node_index, "_rag", MultiRag())
    hits = node_index.search(object(), "Anima 模型", 4)
    assert {h["id"] for h in hits} == {"unet", "base"}


def test_search_reranks_once_after_multi_query_fusion(monkeypatch):
    calls = []

    class MultiRag(FakeRag):
        def search_node_packs(self, cfg, need, k=8):
            calls.append(("retrieve", need))
            return [{"id": need, "content": need}]

    class Cfg:
        reranker_dir = "local-reranker"

    def rerank(query, candidates, model_dir, k):
        calls.append(("rerank", query, len(candidates), model_dir, k))
        return list(reversed(candidates))[:k]

    monkeypatch.setattr(node_index, "_rag", MultiRag())
    monkeypatch.setattr(node_index._reranker, "rerank", rerank)
    out = node_index.search(Cfg(), "ambiguous custom workflow", 4)

    assert sum(call[0] == "rerank" for call in calls) == 1
    assert calls[-1][0] == "rerank"
    assert len(out) == 1


def test_workflow_search_preserves_capability_hits_then_reranks_tail(monkeypatch):
    class BatchRag(FakeRag):
        def search_node_packs_many(self, cfg, needs, k=8, *, dense_indexes=None):
            return [[{"id": str(index), "content": str(index)} for index in range(10)]] * len(needs)

    seen = {}

    def rerank(_query, candidates, _model_dir, k):
        seen.update(pool=len(candidates), k=k)
        return list(reversed(candidates[:k]))

    monkeypatch.setattr(node_index, "_rag", BatchRag())
    monkeypatch.setattr(node_index._reranker, "rerank", rerank)

    out = node_index.search(object(), "Anima 模型", 8)

    assert seen["k"] == 4
    assert len(out) == 8
    assert [item["id"] for item in out[:4]] == ["0", "1", "2", "3"]
    assert [item["id"] for item in out[4:]] == ["7", "6", "5", "4"]


def test_search_only_dense_embeds_one_representative_query(monkeypatch):
    calls = []

    class BatchRag(FakeRag):
        def search_node_packs_many(self, cfg, needs, k=8, *, dense_indexes=None):
            calls.append((list(needs), k, dense_indexes))
            return [[{"id": need, "content": need}] for need in needs]

    monkeypatch.setattr(node_index, "_rag", BatchRag())
    need = "使用 Anima 分离式模型；增加 WD14 反推；最后保存"
    node_index.search(object(), need, 4)

    assert len(calls) == 1
    branches = node_index.plan_queries(need)
    assert len(calls[0][0]) == len(branches)
    dense_index = next(index for index, branch in enumerate(branches) if branch.kind == "subquery")
    assert calls[0][2] == {dense_index}


def test_pack_chunks_preserve_every_node_and_limit_chunk_size():
    pack = {
        "title": "large-pack",
        "python_module": "custom_nodes.large",
        "nodes": [
            {"name": f"Node{i}", "display": f"节点{i}", "desc": "用途", "category": "image/tools"}
            for i in range(25)
        ],
        "categories": {"image"},
    }
    chunks = node_index._pack_chunks(pack, max_nodes=12)
    names = [name for chunk in chunks for name in chunk["node_names"]]
    assert len(chunks) == 3
    assert max(len(chunk["node_names"]) for chunk in chunks) <= 12
    assert names == [f"Node{i}" for i in range(25)]


def test_sync_backfills_chunks_for_unchanged_pack(monkeypatch):
    pack = {
        "title": "pack",
        "python_module": "custom_nodes.pack",
        "nodes": [{"name": "NodeA", "display": "NodeA", "desc": "", "category": "image"}],
        "categories": {"image"},
    }
    calls = []
    monkeypatch.setattr(node_index._rag, "list_node_packs", lambda _cfg: [
        {"id": "pack", "node_names": ["NodeA"]},
    ])
    monkeypatch.setattr(node_index._rag, "node_chunks_ready", lambda _cfg, _pid: False)
    monkeypatch.setattr(node_index._rag, "index_node_pack", lambda *args, **kwargs: calls.append("pack"))
    monkeypatch.setattr(node_index._rag, "index_node_chunks", lambda *args, **kwargs: calls.append("chunks"))
    node_index._do_sync({"pack": pack}, object(), full=False)
    assert calls == ["chunks"]


def test_sync_removes_uninstalled_node_packs(monkeypatch):
    pack = {
        "title": "pack",
        "python_module": "custom_nodes.pack",
        "nodes": [{"name": "NodeA", "display": "NodeA", "desc": "", "category": "image"}],
        "categories": {"image"},
    }
    removed = []
    monkeypatch.setattr(node_index._rag, "list_node_packs", lambda _cfg: [
        {"id": "pack", "node_names": ["NodeA"]},
        {"id": "removed-plugin", "node_names": ["OldNode"]},
    ])
    monkeypatch.setattr(node_index._rag, "delete_node_pack", lambda _cfg, pack_id: removed.append(pack_id))
    monkeypatch.setattr(node_index._rag, "node_chunks_ready", lambda _cfg, _pid: True)

    node_index._do_sync({"pack": pack}, object(), full=False)

    assert removed == ["removed-plugin"]


def test_sync_preserves_manual_pack_content_and_rebuilds_manual_chunks(monkeypatch):
    pack = {
        "title": "pack",
        "python_module": "custom_nodes.pack",
        "nodes": [
            {"name": "NodeA", "display": "NodeA", "desc": "自动说明", "category": "image"},
            {"name": "NodeB", "display": "NodeB", "desc": "新增节点", "category": "image"},
        ],
        "categories": {"image"},
    }
    calls = []
    monkeypatch.setattr(node_index._rag, "list_node_packs", lambda _cfg: [{
        "id": "pack", "node_names": ["NodeA"], "content_source": "manual",
    }])
    monkeypatch.setattr(node_index._rag, "get_node_pack", lambda _cfg, _pid: {
        "id": "pack", "content": "人工用途说明", "content_source": "manual",
    })
    monkeypatch.setattr(node_index._rag, "node_chunks_ready", lambda _cfg, _pid: True)
    monkeypatch.setattr(node_index._rag, "index_node_pack",
                        lambda *args, **kwargs: calls.append(("pack", kwargs)))
    monkeypatch.setattr(node_index._rag, "index_node_chunks",
                        lambda *args, **kwargs: calls.append(("chunks", kwargs)))

    node_index._do_sync({"pack": pack}, object(), full=False)

    assert calls[0][1]["content"] == "人工用途说明"
    assert calls[0][1]["content_source"] == "manual"
    assert calls[1][1]["chunks"][0]["content"] == "人工用途说明"
    assert calls[1][1]["chunks"][0]["node_names"] == ["NodeA", "NodeB"]
    assert calls[1][1]["content_source"] == "manual"
