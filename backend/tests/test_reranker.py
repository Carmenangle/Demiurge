from threading import Event, Thread
from types import SimpleNamespace

from app.services import reranker


def test_local_reranker_reorders_candidates(monkeypatch, tmp_path):
    tmp_path.joinpath("model.safetensors").write_bytes(b"test")
    seen = {}

    class FakeCrossEncoder:
        def __init__(self, path, max_length):
            assert path == str(tmp_path)

        def predict(self, pairs, prompt, batch_size, show_progress_bar):
            seen.update(pairs=pairs, prompt=prompt)
            return [0.1, 0.9]

    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers",
                        SimpleNamespace(CrossEncoder=FakeCrossEncoder))
    reranker._CACHE.clear()
    reranker._model(str(tmp_path))
    candidates = [{"id": "low", "content": "基础节点"}, {"id": "high", "content": "目标节点"}]
    out = reranker.rerank("Anima UNET", candidates, str(tmp_path), 2)
    assert [item["id"] for item in out] == ["high", "low"]
    assert [pair[0] for pair in seen["pairs"]] == ["Anima UNET", "Anima UNET"]
    assert seen["prompt"].startswith("Rank ComfyUI node candidates")


def test_missing_reranker_dir_falls_back():
    reranker._CACHE.clear()
    assert reranker.rerank("query", [{"id": "x"}], "", 1) == []


def test_incomplete_sharded_model_falls_back_without_loading(monkeypatch, tmp_path):
    tmp_path.joinpath("model.safetensors.index.json").write_text(
        '{"weight_map":{"layer":"model-00001-of-00002.safetensors"}}', encoding="utf-8",
    )
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", None)
    reranker._CACHE.clear()
    assert reranker.rerank("query", [{"id": "x"}], str(tmp_path), 1) == []


def test_cold_reranker_falls_back_and_preloads_in_background(monkeypatch):
    scheduled = []
    monkeypatch.setattr(reranker, "_cached_model", lambda _path: None)
    monkeypatch.setattr(reranker, "preload", lambda path: scheduled.append(path))

    assert reranker.rerank("query", [{"id": "x"}], "model-dir", 1) == []
    assert scheduled == ["model-dir"]


def test_cpu_reranker_is_not_used_on_interactive_query(monkeypatch):
    model = SimpleNamespace(
        device="cpu",
        predict=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CPU 不应精排")),
    )
    monkeypatch.setattr(reranker, "_cached_model", lambda _path: model)

    assert reranker.rerank("query", [{"id": "x"}], "model-dir", 1) == []


def test_preload_skips_without_accelerator(monkeypatch):
    monkeypatch.setattr(reranker, "_model_key", lambda _path: "model-dir")
    monkeypatch.setattr(reranker, "_accelerator_available", lambda: False)
    monkeypatch.setattr(
        reranker, "_model",
        lambda _path: (_ for _ in ()).throw(AssertionError("CPU 不应后台加载")),
    )

    reranker._LOADING.add("model-dir")
    reranker._preload_worker("model-dir")

    assert "model-dir" not in reranker._LOADING
    assert "model-dir" in reranker._PRELOAD_DISABLED


def test_reranker_only_scores_bounded_fusion_pool(monkeypatch, tmp_path):
    tmp_path.joinpath("model.safetensors").write_bytes(b"test")
    seen = {}

    class FakeCrossEncoder:
        def __init__(self, path, max_length):
            seen["max_length"] = max_length

        def predict(self, pairs, prompt, batch_size, show_progress_bar):
            seen["pairs"] = pairs
            seen["prompt"] = prompt
            return list(range(len(pairs)))

    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers",
                        SimpleNamespace(CrossEncoder=FakeCrossEncoder))
    reranker._CACHE.clear()
    reranker._model(str(tmp_path))
    candidates = [
        {"id": str(index), "content": ("x" * 2000) + f"tail-{index}"}
        for index in range(40)
    ]

    out = reranker.rerank("q" * 2000, candidates, str(tmp_path), 12)

    assert seen["max_length"] == 256
    assert len(seen["pairs"]) == 12
    assert seen["prompt"].startswith("Rank ComfyUI node candidates")
    assert all("tail-" not in document for _, document in seen["pairs"])
    assert len(out) == 12


def test_release_accelerator_memory_clears_cached_model(monkeypatch):
    model = SimpleNamespace(device="cuda:0")
    reranker._CACHE["model-dir"] = model
    emptied = []
    monkeypatch.setattr(reranker, "_empty_accelerator_cache", lambda: emptied.append(True))

    assert reranker.release_accelerator_memory() is True
    assert reranker._CACHE == {}
    assert emptied == [True]


def test_model_loaded_before_release_cannot_reenter_cache(monkeypatch, tmp_path):
    tmp_path.joinpath("model.safetensors").write_bytes(b"test")

    class FakeCrossEncoder:
        def __init__(self, path, max_length):
            reranker.release_accelerator_memory()

    monkeypatch.setitem(
        __import__("sys").modules,
        "sentence_transformers",
        SimpleNamespace(CrossEncoder=FakeCrossEncoder),
    )
    monkeypatch.setattr(reranker, "_empty_accelerator_cache", lambda: None)
    reranker._CACHE.clear()

    assert reranker._model(str(tmp_path)) is None
    assert reranker._CACHE == {}


def test_release_waits_for_active_rerank_before_returning(monkeypatch, tmp_path):
    tmp_path.joinpath("model.safetensors").write_bytes(b"test")
    entered = Event()
    allow_finish = Event()
    released = Event()

    class ActiveModel:
        device = "cuda:0"

        def predict(self, pairs, **kwargs):
            entered.set()
            assert allow_finish.wait(2)
            return [1.0]

    key = str(tmp_path.resolve())
    reranker._CACHE.clear()
    reranker._CACHE[key] = ActiveModel()
    monkeypatch.setattr(reranker, "_empty_accelerator_cache", lambda: None)
    query = Thread(
        target=lambda: reranker.rerank("query", [{"id": "x"}], key, 1),
        daemon=True,
    )
    query.start()
    assert entered.wait(1)
    release = Thread(
        target=lambda: (reranker.release_accelerator_memory(), released.set()),
        daemon=True,
    )
    release.start()
    assert not released.wait(0.05)
    allow_finish.set()
    query.join(2)
    release.join(2)
    assert released.is_set()
