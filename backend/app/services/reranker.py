"""可选的本地 Cross-Encoder 重排序器。

RAG 的向量/BM25 召回不依赖本模块；只有配置了本地模型目录且安装
sentence-transformers 时才启用精排，失败时返回空让调用方保留原排序。
"""
from __future__ import annotations

import json
import logging
import time
import gc
from pathlib import Path
from threading import Condition, Lock, Thread

_LOG = logging.getLogger(__name__)
_CACHE: dict[str, object] = {}
_LOADING: set[str] = set()
_PRELOAD_DISABLED: set[str] = set()
_LOCK = Lock()
_IDLE = Condition(_LOCK)
_CACHE_GENERATION = 0
_ACTIVE_INFERENCES = 0

_INSTRUCTION = (
    "Rank ComfyUI node candidates for functional compatibility with the workflow request. "
    "Prefer candidates that implement the same capability and have compatible inputs and outputs."
)


def _weights_complete(path: Path) -> bool:
    """只接受完整本地权重，避免 Transformers 对半成品目录发起隐式联网。"""
    single_weight = path / "model.safetensors"
    if single_weight.is_file() and single_weight.stat().st_size > 0:
        return True
    index_path = path / "model.safetensors.index.json"
    if not index_path.is_file():
        return False
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        shards = set((index.get("weight_map") or {}).values())
    except (OSError, ValueError, TypeError):
        return False
    return bool(shards) and all(
        path.joinpath(str(shard)).is_file() and path.joinpath(str(shard)).stat().st_size > 0
        for shard in shards
    )


def weight_status(path: Path) -> tuple[bool, list[str]]:
    if not path.is_dir():
        return False, ["模型目录不存在"]
    single_weight = path / "model.safetensors"
    if single_weight.is_file() and single_weight.stat().st_size > 0:
        return True, []
    index_path = path / "model.safetensors.index.json"
    if not index_path.is_file():
        return False, ["model.safetensors 或 model.safetensors.index.json"]
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        shards = sorted(set((index.get("weight_map") or {}).values()))
    except (OSError, ValueError, TypeError):
        return False, ["model.safetensors.index.json 无法解析"]
    if not shards:
        return False, ["权重索引没有记录分片"]
    missing = [str(name) for name in shards
               if not path.joinpath(str(name)).is_file()
               or path.joinpath(str(name)).stat().st_size <= 0]
    return not missing, missing


def _model(path: str):
    model_path = Path(path).expanduser() if path else None
    if model_path is None or not model_path.is_dir() or not _weights_complete(model_path):
        return None
    path = str(model_path.resolve())
    with _LOCK:
        if path in _CACHE:
            return _CACHE[path]
        generation = _CACHE_GENERATION
    try:
        import inspect
        from sentence_transformers import CrossEncoder
        kwargs = {"max_length": 256}
        if "local_files_only" in inspect.signature(CrossEncoder).parameters:
            kwargs["local_files_only"] = True
        model = CrossEncoder(path, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Reranker unavailable path=%r: %s", path, exc)
        return None
    with _LOCK:
        if generation == _CACHE_GENERATION:
            existing = _CACHE.setdefault(path, model)
        else:
            existing = None
    if existing is model:
        return model
    del model
    _empty_accelerator_cache()
    return existing


def _empty_accelerator_cache() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        mps = getattr(torch, "mps", None)
        if mps is not None and hasattr(mps, "empty_cache"):
            mps.empty_cache()
    except (ImportError, RuntimeError):
        pass


def release_accelerator_memory() -> bool:
    """工作流提交前释放已缓存模型；加载中的旧代模型完成后也不得重新入缓存。"""
    global _CACHE_GENERATION
    with _IDLE:
        _CACHE_GENERATION += 1
        models = list(_CACHE.values())
        _CACHE.clear()
        while _ACTIVE_INFERENCES > 0:
            _IDLE.wait()
    if not models:
        return False
    models.clear()
    _empty_accelerator_cache()
    return True


def _model_key(path: str) -> str:
    model_path = Path(path).expanduser() if path else None
    if model_path is None or not model_path.is_dir() or not _weights_complete(model_path):
        return ""
    return str(model_path.resolve())


def _cached_model(path: str):
    global _ACTIVE_INFERENCES
    key = _model_key(path)
    if not key:
        return None
    with _IDLE:
        model = _CACHE.get(key)
        if model is not None:
            _ACTIVE_INFERENCES += 1
        return model


def _release_inference() -> None:
    global _ACTIVE_INFERENCES
    with _IDLE:
        if _ACTIVE_INFERENCES > 0:
            _ACTIVE_INFERENCES -= 1
        if _ACTIVE_INFERENCES == 0:
            _IDLE.notify_all()


def _accelerator_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    if torch.cuda.is_available():
        return True
    mps = getattr(torch.backends, "mps", None)
    return bool(mps and mps.is_available())


def _preload_worker(key: str) -> None:
    try:
        if not _accelerator_available():
            with _LOCK:
                _PRELOAD_DISABLED.add(key)
            return
        _model(key)
    finally:
        with _LOCK:
            _LOADING.discard(key)


def preload(model_dir: str) -> bool:
    """后台检测加速器并预热；当前检索立即走融合结果。"""
    key = _model_key(model_dir)
    if not key:
        return False
    with _LOCK:
        if key in _CACHE or key in _LOADING or key in _PRELOAD_DISABLED:
            return False
        _LOADING.add(key)
    Thread(
        target=_preload_worker, args=(key,), name="rag-reranker-preload", daemon=True,
    ).start()
    return True


def _interactive_device_supported(model: object) -> bool:
    device = str(getattr(model, "device", "") or "").lower()
    return not device.startswith("cpu")


def rerank(query: str, candidates: list[dict], model_dir: str, k: int,
           instruction: str = _INSTRUCTION) -> list[dict]:
    """对候选节点包精排；模型不可用时返回空，调用方应使用召回顺序。"""
    if not candidates:
        return []
    model = _cached_model(model_dir)
    if model is None:
        preload(model_dir)
        return []
    try:
        if not _interactive_device_supported(model):
            return []
        pool = candidates[:min(len(candidates), max(k, 4))]
        docs = []
        for item in pool:
            node_names = ", ".join(str(n) for n in item.get("node_names", []) or [])
            content = str(item.get("content", "") or "")[:600]
            docs.append(f"title: {item.get('title', '')}\nnodes: {node_names}\n{content}")
        rerank_query = query[:400]
        prompt = f"{instruction.strip()}\nQuery: "
        scores = model.predict(
            [(rerank_query, doc) for doc in docs],
            prompt=prompt,
            batch_size=8,
            show_progress_bar=False,
        )
        ranked = sorted(zip(scores, candidates), key=lambda pair: float(pair[0]), reverse=True)
        return [item for _, item in ranked[:k]]
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Reranker failed: %s", exc)
        return []
    finally:
        _release_inference()


def probe_model(model_dir: str) -> tuple[bool, str]:
    path = Path(model_dir).expanduser() if model_dir else Path()
    if not model_dir:
        return False, "未配置 Reranker 模型目录"
    complete, missing = weight_status(path)
    if not complete:
        return False, "缺少文件：" + "、".join(missing)
    if _model(str(path)) is None:
        return False, "权重完整，但 Cross-Encoder 加载失败；请查看后端日志"
    model = _cached_model(str(path))
    if model is None:
        return False, "模型加载期间已为 ComfyUI 释放，请重新测试"
    try:
        started = time.perf_counter()
        scores = model.predict(
            [("Query: test", "Document: test")],
            batch_size=1,
            show_progress_bar=False,
        )
        latency = time.perf_counter() - started
        if len(scores) != 1:
            return False, "模型已加载，但最小推理未返回分数"
        device = str(getattr(model, "device", "未知设备"))
        warning = "；CPU 仅用于文件验证，交互检索会自动降级" if device.lower().startswith("cpu") else ""
        return True, (
            f"文件完整，最小推理成功并已预热（score={float(scores[0]):.4f}，"
            f"设备={device}，耗时={latency:.2f}s{warning}）"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"模型已加载，但最小推理失败：{exc}"
    finally:
        _release_inference()
