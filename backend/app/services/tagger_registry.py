"""可插拔反推打标注册表：**丢模型目录 + 清单文件即可出现在可用打标器里**（2026-10-17）。

约定（与视觉 Embedding 模型同一思路，但这里是多模型可切换）：
- 内置家族：WD14 v3 系（SmilingWolf），各自一个目录：
  - 默认 `DATA_DIR/models/wd14-tagger/`（wd-swinv2-tagger-v3，当前在用的）
  - 其它 `DATA_DIR/models/taggers/<id>/`（eva02-large / vit-large / convnext …）
- **丢模型**：把模型放到 `DATA_DIR/models/taggers/<任意名>/`，内含
  `model.onnx` + `selected_tags.csv` + 可选 `tagger.json`：
    {
      "id": "my-tagger", "name": "我的打标器", "family": "wd14",
      "input_size": 448, "layout": "NHWC",
      "thresholds": {"0": 0.35, "4": 0.75, "9": 0.5}, "rating_category": 9
    }
  不写 tagger.json 时按 id=目录名、默认 WD14 参数处理 ⇒ **纯丢文件也能被识别**。
- 只支持 `family="wd14"` 的推理（同预处理/同 CSV/同 NHWC）。新家族（如 ML-Danbooru 那种不同
  预处理/不同标签表）需在 `wd14_tagger` 旁加一个同签名引擎并在 `predict()` 里按 family 分派。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import DATA_DIR
from app.services import wd14_tagger

_log = logging.getLogger("uvicorn.error")

TAGGERS_DIR = DATA_DIR / "models" / "taggers"
DEFAULT_ID = "wd-swinv2-v3"
_DEFAULT_THRESHOLDS = {0: 0.35, 4: 0.75, 9: 0.5}


@dataclass(frozen=True)
class TaggerSpec:
    id: str
    name: str
    dir: Path
    family: str = "wd14"
    input_size: int = 448
    layout: str = "NHWC"
    thresholds: dict[int, float] = field(default_factory=lambda: dict(_DEFAULT_THRESHOLDS))
    rating_category: int = 9
    source: str = "builtin"   # builtin | dropin


# 内置清单（未安装的文件 model_available 返回 False，UI 置灰即可）
BUILTIN_SPECS: tuple[TaggerSpec, ...] = (
    TaggerSpec("wd-swinv2-v3", "WD SwinV2 v3（均衡，默认）", DATA_DIR / "models" / "wd14-tagger"),
    TaggerSpec("wd-eva02-large-v3", "WD EVA02-Large v3（最高精度·~1.6G）",
               TAGGERS_DIR / "wd-eva02-large-v3"),
    TaggerSpec("wd-vit-large-v3", "WD ViT-Large v3（高精度·~1.1G）",
               TAGGERS_DIR / "wd-vit-large-v3"),
    TaggerSpec("wd-convnext-v3", "WD ConvNeXt v3（对照）",
               TAGGERS_DIR / "wd-convnext-v3"),
)


def _spec_from_manifest(folder: Path) -> TaggerSpec | None:
    mf = folder / "tagger.json"
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
    except Exception as exc:  # noqa: BLE001 清单坏了不影响其它打标器
        _log.warning("tagger.json 解析失败 %s: %s", mf, exc)
        return None
    if not isinstance(manifest, dict):
        manifest = {}
    thr_raw = manifest.get("thresholds") or {}
    try:
        thresholds = {int(k): float(v) for k, v in thr_raw.items()} or dict(_DEFAULT_THRESHOLDS)
    except Exception:  # noqa: BLE001
        thresholds = dict(_DEFAULT_THRESHOLDS)
    return TaggerSpec(
        id=str(manifest.get("id") or folder.name),
        name=str(manifest.get("name") or folder.name),
        dir=folder,
        family=str(manifest.get("family") or "wd14"),
        input_size=int(manifest.get("input_size") or 448),
        layout=str(manifest.get("layout") or "NHWC"),
        thresholds=thresholds,
        rating_category=int(manifest.get("rating_category", 9)),
        source="dropin",
    )


def _dropin_specs() -> list[TaggerSpec]:
    if not TAGGERS_DIR.is_dir():
        return []
    out: list[TaggerSpec] = []
    for folder in sorted(TAGGERS_DIR.iterdir()):
        if folder.is_dir() and not folder.name.startswith("."):
            spec = _spec_from_manifest(folder)
            if spec is not None:
                out.append(spec)
    return out


def _registry() -> dict[str, TaggerSpec]:
    d: dict[str, TaggerSpec] = {}
    for spec in _dropin_specs():
        d[spec.id] = spec
    for spec in BUILTIN_SPECS:
        d[spec.id] = spec          # 内置优先（同 id 时内置覆盖 dropin）
    return d


def list_taggers() -> list[dict]:
    """可用打标器列表（含 available 标记，供前端下拉/设置）。"""
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "family": spec.family,
            "available": wd14_tagger.model_available(spec.dir),
            "source": spec.source,
        }
        for spec in _registry().values()
    ]


def resolve(tagger_id: str | None) -> TaggerSpec:
    """按 id 取 spec；找不到回落到默认（不抛错，保证打标总能跑）。"""
    reg = _registry()
    if tagger_id and tagger_id in reg:
        return reg[tagger_id]
    return reg[DEFAULT_ID]


def predict(tagger_id: str | None, image_path: Path, *, include_rating: bool = False,
            limit: int = 0) -> list[tuple[str, float]]:
    """按打标器 id 反推标签。目前 family 全走 WD14 引擎；未安装/坏图抛 RuntimeError。"""
    spec = resolve(tagger_id)
    tags = wd14_tagger.predict(
        image_path,
        model_dir=spec.dir,
        input_size=spec.input_size,
        layout=spec.layout,
        thresholds=spec.thresholds,
        rating_category=spec.rating_category,
        include_rating=include_rating,
    )
    return tags[:limit] if limit else tags
