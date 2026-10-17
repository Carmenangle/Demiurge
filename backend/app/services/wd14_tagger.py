"""反推打标引擎（WD14 家族，可插拔：支持多模型目录/输入尺寸/布局/阈值）。

定位：反推打标链的**本地主力**（pixAI 备选不实现）。与 danbooru 素材源标签同体系
（Danbooru tag），输出直接喂 `set_doc_tags` 合并 ⇒ 标签统计/搜索/视觉索引/语义搜索零改动复用。

**可插拔（2026-10-17）**：本文件只实现「WD14 家族」推理（SmilingWolf v3 系：swinv2 / eva02-large /
vit-large / convnext…，同预处理/同 CSV/同 NHWC 输入）。具体用哪个模型、输入尺寸、阈值由
`tagger_registry` 的 `TaggerSpec` 决定；「丢模型即可用」的清单约定见 `tagger_registry.py`。

默认模型目录 `DATA_DIR/models/wd14-tagger/`（HF `SmilingWolf/wd-swinv2-tagger-v3`：
`model.onnx` + `selected_tags.csv`）。`model_available()` 严格校验；未就绪时端点回 503 引导下载。

推理参数（对齐 ComfyUI-WD14-Tagger / SmilingWolf 参考实现）：
- 预处理：RGB → 白底 pad 正方形 → BICUBIC resize N×N → /255 → **NHWC** → batch；
- onnx 输出视为概率（sigmoid 已在模型内）；
- 阈值：general(category 0) 0.35 ／ character(category 4) 0.75 ／ rating(category 9) 0.5；
- rating(category 9) 默认剔除（是分级不是画面标签；`include_rating=True` 才保留）；
- 输出按分数降序；CSV 的 name 即最终 tag 形态（下划线连接由上游保证）。
"""
from __future__ import annotations

import csv
import logging
import threading
from pathlib import Path

from app.config import DATA_DIR

_log = logging.getLogger("uvicorn.error")

MODEL_DIR = DATA_DIR / "models" / "wd14-tagger"
_INPUT_SIZE = 448
# v3 selected_tags.csv 的 category：**0=general（真实标签）、4=character、9=rating**。
# ⚠ 2026-10-17 实测修正：原实现把 0/9 语义写反了（注释也写反），导致「默认剔除 category 0」
# 把所有 general 标签都剔掉、反而把 rating 当普通标签保留。正确阈值：general 0.35 / character 0.75 / rating 0.5。
_THRESHOLDS = {0: 0.35, 4: 0.75, 9: 0.5}
_DEFAULT_THRESHOLD = 0.35
_RATING_CATEGORY = 9

_lock = threading.Lock()
_sessions: dict[str, object] = {}       # model_dir → InferenceSession（多模型共存）
_tags_cache: dict[str, list[tuple[str, int | None]]] = {}  # model_dir → [(name, category)]


def _dir(model_dir: Path | str | None) -> Path:
    return Path(model_dir) if model_dir else MODEL_DIR


def model_dir() -> Path:
    return MODEL_DIR


def model_available(model_dir: Path | str | None = None) -> bool:
    """模型文件存在且非空（宽容校验）。

    大小阈值只挡 0 字节/空文件；**真正的完整性校验在加载层**——LFS 指针、下载中断的
    残缺文件都会让 InferenceSession 构造失败并转可读错误（见 _load_session），
    引导用户重新下载。不用大小猜完整性（合法最小 onnx 与 LFS 指针只差 2 倍，猜不准）。
    """
    d = _dir(model_dir)
    onnx = d / "model.onnx"
    tags = d / "selected_tags.csv"
    return (onnx.is_file() and onnx.stat().st_size > 64
            and tags.is_file() and tags.stat().st_size > 8)


def _load_session(model_dir: Path | str | None = None):
    """延迟加载推理 session（CPU provider，按模型目录缓存）；导入失败抛可读错误。"""
    d = _dir(model_dir)
    key = str(d)
    with _lock:
        sess = _sessions.get(key)
        if sess is not None:
            return sess
        if not model_available(d):
            raise RuntimeError(
                "WD14 模型未安装：请将 model.onnx 与 selected_tags.csv 放到 "
                f"{d}（HF: SmilingWolf/wd-swinv2-tagger-v3）"
            )
        import onnxruntime

        opts = onnxruntime.SessionOptions()
        opts.log_severity_level = 3  # 静默推理日志
        try:
            sess = onnxruntime.InferenceSession(
                str(d / "model.onnx"), opts,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:  # noqa: BLE001 残缺/损坏的 onnx 转可读错误
            raise RuntimeError(
                f"WD14 模型加载失败（文件可能下载不完整或损坏，请重新下载）：{exc}"
            ) from exc
        _sessions[key] = sess
        return sess


def _load_tags(model_dir: Path | str | None = None) -> list[tuple[str, int | None]]:
    """解析 selected_tags.csv → [(name, category)]，与模型输出维度同序（按目录缓存）。

    兼容三列（tag_id,name,category，v3）与两列（tag_id,name，旧版）；
    跳过表头与非数字行；category 缺失按 general 处理（None）。
    """
    d = _dir(model_dir)
    key = str(d)
    cached = _tags_cache.get(key)
    if cached is not None:
        return cached
    rows: list[tuple[str, int | None]] = []
    with (d / "selected_tags.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[0].strip().isdigit():
                continue  # 表头/空行/脏行
            if row[0].strip() == "9999999":
                continue  # 占位行（category=9 的 "general" 标记），不是真标签
            name = row[1].strip()
            if not name:
                continue
            category = int(row[2]) if len(row) >= 3 and row[2].strip().lstrip("-").isdigit() else None
            rows.append((name, category))
    if not rows:
        raise RuntimeError("selected_tags.csv 无有效标签行")
    _tags_cache[key] = rows
    return rows


def _preprocess(image_path: Path, input_size: int = _INPUT_SIZE, layout: str = "NHWC"):
    """图片 → [1,N,N,3] 或 [1,3,N,N] float32（白底 pad 正方形 → BICUBIC → /255 → batch）。"""
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(image_path) as opened:
        img = ImageOps.exif_transpose(opened).convert("RGB")
        w, h = img.size
        if w != h:
            img = ImageOps.pad(img, (max(w, h), max(w, h)),
                               color=(255, 255, 255), centering=(0.5, 0.5))
        img = img.resize((input_size, input_size), Image.BICUBIC)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    if layout == "NCHW":
        arr = arr.transpose(2, 0, 1)
    # 默认 NHWC：wd-swinv2-tagger-v3 的 ONNX 输入是 (1,448,448,3)，不是 NCHW。
    return np.expand_dims(arr, axis=0)


def predict(image_path: Path, *, model_dir: Path | str | None = None,
            input_size: int = _INPUT_SIZE, layout: str = "NHWC",
            thresholds: dict[int, float] | None = None,
            rating_category: int = _RATING_CATEGORY,
            threshold_general: float = 0.35, threshold_character: float = 0.75,
            include_rating: bool = False) -> list[tuple[str, float]]:
    """反推 Danbooru 标签 → [(tag, score)] 按分数降序。模型未就绪/坏图抛 RuntimeError。

    model_dir/input_size/layout/thresholds 让不同的 WD14 家族模型共用同一推理（可插拔）。
    """
    session = _load_session(model_dir)
    tags = _load_tags(model_dir)
    try:
        batch = _preprocess(image_path, input_size, layout)
    except Exception as exc:  # noqa: BLE001 坏图/不支持的格式统一转可读错误
        raise RuntimeError(f"WD14 预处理失败（图片可能损坏）：{exc}") from exc
    input_name = session.get_inputs()[0].name
    probs = session.run(None, {input_name: batch})[0]
    probs = probs.reshape(-1)
    thr = dict(_THRESHOLDS)
    if thresholds:
        thr.update({int(k): float(v) for k, v in thresholds.items()})
    thr[0] = threshold_general
    thr[4] = threshold_character
    out: list[tuple[str, float]] = []
    for (name, category), prob in zip(tags, probs):  # zip 截断防维度错位
        if category == rating_category and not include_rating:
            continue
        if float(prob) >= thr.get(category, _DEFAULT_THRESHOLD):
            out.append((name, float(prob)))
    out.sort(key=lambda item: item[1], reverse=True)
    return out


def reverse_tags(image_path: Path, limit: int = 32, *,
                 model_dir: Path | str | None = None) -> list[str]:
    """资产打标便捷包装：只返回标签名（按分降序，截 limit）。"""
    return [name for name, _score in predict(image_path, model_dir=model_dir)[:limit]]


def release(model_dir: Path | str | None = None) -> bool:
    """卸载推理 session 回收内存。model_dir 为空＝卸载全部。返回此前是否已加载。"""
    with _lock:
        if model_dir is None:
            existed = bool(_sessions)
            _sessions.clear()
        else:
            existed = _sessions.pop(str(_dir(model_dir)), None) is not None
        return existed
