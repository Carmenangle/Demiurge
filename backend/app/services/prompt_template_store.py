"""提示词模版持久化（2026-10-25）：内置默认模版只读投影 + 用户自定义模版 CRUD。

**分层原则**（本特性的架构合同）：协议族（kind）决定代码侧校验器/归一化/兜底
（`image_prompt_profiles` / `video_prompt`），内置不可改；本 store 只拥有**数据层**——
指令文本、质量/负面词、区块标题文案、per-band 运镜覆盖、节奏名、默认时长等。
生图/视频纯函数模块**不 import 本 store**（保持零磁盘 IO、可独立单测）；模版数据
由编排层（agency_scene_comfy / ai_text 路由）解析后以参数传入。

协议族七值：
- 生图 `anima_tags` / `natural_language` / `niji_sections` / `moss_caption`
  （2026-10-30 新增 MOSS 档；krea2 已下线，不进内置列表）；
- 视频 `video_climax` / `video_firstlast` / `video_transition`（transition 无用户选择器，
  由 firstlast 段桥派生，但仍可编辑模版）。

**安全基线**（对照 `template_store` 的既有弱点加固，见完成报告「发现未动」）：
- id 白名单 `^[a-f0-9]{32}$`（uuid4().hex），每个入口先验再拼路径（禁 `../` 穿越）；
- 保存时校验 + 加载时复校（磁盘 JSON 可被手改），坏文件跳过并记 trace；
- `builtin:` 前缀只读：不落盘、由代码常量现算（单一真源仍在 templates/video_prompt）。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import PROMPT_TEMPLATES_DIR
from app.services.image_prompt_profile_templates import (
    ANIMA_NEGATIVE_TAGS,
    ANIMA_QUALITY_TAGS,
    _SYSTEMS,
    missing_redline_anchors,
)
from app.services import video_prompt

# ── 协议族 ────────────────────────────────────────────────────────────────────
IMAGE_KINDS: tuple[str, ...] = (
    "anima_tags", "natural_language", "niji_sections", "moss_caption",
)
VIDEO_KINDS: tuple[str, ...] = ("video_climax", "video_firstlast", "video_transition")
KINDS: tuple[str, ...] = IMAGE_KINDS + VIDEO_KINDS

_KIND_NAMES: dict[str, str] = {
    "anima_tags": "Anima（质量行 + 内容 tags / 英文描述）",
    "natural_language": "自然语言（GPT Image / Banana）",
    "niji_sections": "Niji（主体 / 风格 / 附加 / 后缀）",
    "moss_caption": "MOSS（200–300 词英文 caption）",
    "video_climax": "视频·高潮点动作代入",
    "video_firstlast": "视频·首尾帧剧情影片",
    "video_transition": "视频·转场短桥段",
}

# id 白名单：uuid4().hex（32 位小写十六进制）。store 每个入口先过这里再拼路径。
_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_CUSTOM_PREFIX = "custom:"
_BUILTIN_PREFIX = "builtin:"

# 字段上限（保存 + 加载共用同一套；路由层 Pydantic 同步约束，双保险）
_MAX_NAME = 100
_MAX_SYSTEM = 12_000        # system_text + extra_instructions 合计
_MAX_EXTRA = 6_000
_MAX_QUALITY = 2_000        # quality_tags / negative_tags
_MAX_SECTION_TITLE = 200
_MAX_CAMERA_BAND = 120
_MAX_BEAT_LABEL = 50
_MAX_BEAT_LABELS = 6
_MAX_NEGATIVE_EXTRA = 1_000
_MAX_META_NOTE = 500

_SECTION_KEYS = video_prompt.SECTION_KEYS
# 对外公开的区块键集合（路由层 Pydantic 校验用；与 video_prompt 单一真源）
SECTION_KEYS_PUBLIC = _SECTION_KEYS


def validate_template_id(template_id: str) -> str:
    """校验自定义模版 id（白名单）；非法抛 ValueError（路由层映射 400/404）。"""
    tid = str(template_id or "").strip()
    if not _ID_RE.fullmatch(tid):
        raise ValueError("模版 id 不合法（须为 32 位小写十六进制）")
    return tid


def is_custom_id(value: str) -> bool:
    return str(value or "").startswith(_CUSTOM_PREFIX)


def custom_id_of(value: str) -> str:
    """`custom:<hex>` → `<hex>`；非 custom 形态返回空串。"""
    raw = str(value or "").strip()
    return raw[len(_CUSTOM_PREFIX):] if raw.startswith(_CUSTOM_PREFIX) else ""


def _video_mode_of(kind: str) -> str:
    return kind[len("video_"):] if kind.startswith("video_") else "climax"


def _content_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _ensure_dir() -> None:
    PROMPT_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _path(template_id: str) -> Path:
    """仅接受过白名单的 id（防 `../` 穿越）。"""
    return PROMPT_TEMPLATES_DIR / f"{validate_template_id(template_id)}.json"


# ── 内置模版投影（只读；不落盘）───────────────────────────────────────────────

def builtin_view(kind: str) -> dict:
    """内置默认模版全文投影（供工作台展示/克隆；内容由代码常量现算）。"""
    if kind not in KINDS:
        raise ValueError(f"未知模版协议族：{kind}")
    view: dict[str, Any] = {
        "id": f"{_BUILTIN_PREFIX}{kind}",
        "builtin": True,
        "kind": kind,
        "name": _KIND_NAMES[kind],
        "base_id": "",
        "base_hash": "",
        "redline_lock": True,
    }
    if kind in IMAGE_KINDS:
        system = _SYSTEMS[kind]
        view.update({
            "system_text": system,
            "extra_instructions": "",
            "quality_tags": ANIMA_QUALITY_TAGS if kind == "anima_tags" else "",
            "negative_tags": ANIMA_NEGATIVE_TAGS if kind == "anima_tags" else "",
            "redline_lock_off_at": 0.0,
            "content_hash": _content_hash(system),
        })
    else:
        data = video_prompt.default_skeleton_data(_video_mode_of(kind))
        view.update({
            "section_titles": data["section_titles"],
            "section_order": data["section_order"],
            "camera_band_overrides": data["camera_band_overrides"],
            "beat_labels": data["beat_labels"],
            "default_duration": data["default_duration"],
            "negative_extra": data["negative_extra"],
            "meta_note": data["meta_note"],
            "content_hash": _content_hash(
                json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        })
    return view


def list_builtins(kind: str = "") -> list[dict]:
    """全部内置模版投影（可按 kind 过滤）。"""
    kinds = (kind,) if kind else KINDS
    return [builtin_view(k) for k in kinds if k in KINDS]


# ── 校验（保存 + 加载共用）────────────────────────────────────────────────────

def _validate_record(record: dict) -> dict:
    """归一化并校验一条自定义模版记录；非法抛 ValueError（消息可直接给用户）。"""
    if not isinstance(record, dict):
        raise ValueError("模版记录必须是对象")
    kind = str(record.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError(f"未知模版协议族：{kind or '（空）'}")
    name = str(record.get("name") or "").strip()
    if not name:
        raise ValueError("模版名称不能为空")
    if len(name) > _MAX_NAME:
        raise ValueError(f"模版名称过长（≤{_MAX_NAME} 字）")
    base_id = str(record.get("base_id") or "").strip()
    if base_id and not (
        base_id.startswith(_BUILTIN_PREFIX) and base_id[len(_BUILTIN_PREFIX):] in KINDS
    ) and not (
        base_id.startswith(_CUSTOM_PREFIX) and _ID_RE.fullmatch(base_id[len(_CUSTOM_PREFIX):])
    ):
        raise ValueError("克隆基准 id 不合法")
    base_hash = str(record.get("base_hash") or "").strip()
    if len(base_hash) > 16:
        raise ValueError("克隆基准 hash 过长")

    out: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "base_id": base_id,
        "base_hash": base_hash,
        "redline_lock": bool(record.get("redline_lock", True)),
        "redline_lock_off_at": float(record.get("redline_lock_off_at") or 0.0),
    }
    if kind in IMAGE_KINDS:
        system_text = str(record.get("system_text") or "")
        extra = str(record.get("extra_instructions") or "")
        quality = str(record.get("quality_tags") or "")
        negative = str(record.get("negative_tags") or "")
        if len(system_text) + len(extra) > _MAX_SYSTEM:
            raise ValueError(
                f"指令文本过长：system_text + extra_instructions 合计 ≤{_MAX_SYSTEM} 字符")
        if len(extra) > _MAX_EXTRA:
            raise ValueError(f"追加指令过长（≤{_MAX_EXTRA} 字符）")
        if len(quality) > _MAX_QUALITY or len(negative) > _MAX_QUALITY:
            raise ValueError(f"质量词/负面词过长（各 ≤{_MAX_QUALITY} 字符）")
        if out["redline_lock"]:
            missing = missing_redline_anchors(system_text)
            if missing:
                raise ValueError(
                    "红线条款锁已开启：指令文本缺少必保段落——" + "、".join(missing)
                    + "（补回该段，或在高级选项里显式关锁）")
        out.update({
            "system_text": system_text,
            "extra_instructions": extra,
            "quality_tags": quality,
            "negative_tags": negative,
        })
    else:
        titles_raw = record.get("section_titles") or {}
        if not isinstance(titles_raw, dict):
            raise ValueError("区块标题必须是对象")
        titles: dict[str, str] = {}
        for key, value in titles_raw.items():
            k = str(key or "").strip()
            v = str(value or "").strip().rstrip("：:")
            if k not in _SECTION_KEYS:
                raise ValueError(f"未知区块键：{k or '（空）'}")
            if not v:
                raise ValueError(f"区块「{k}」标题不能为空")
            if len(v) > _MAX_SECTION_TITLE:
                raise ValueError(f"区块标题过长（≤{_MAX_SECTION_TITLE} 字）")
            titles[k] = v
        order_raw = record.get("section_order") or []
        if not isinstance(order_raw, list):
            raise ValueError("区块顺序必须是数组")
        order = [str(item or "").strip() for item in order_raw]
        if order:
            if sorted(order) != sorted(_SECTION_KEYS) or len(set(order)) != len(_SECTION_KEYS):
                raise ValueError("区块顺序必须是六个区块键的排列（缺项/重复/乱键都不允许）")
        bands_raw = record.get("camera_band_overrides") or {}
        if not isinstance(bands_raw, dict):
            raise ValueError("运镜覆盖必须是对象")
        bands: dict[str, str] = {}
        for key, value in bands_raw.items():
            k = str(key or "").strip()
            if k not in ("0", "1", "2", "3"):
                raise ValueError(f"运镜档位键不合法：{k or '（空）'}（只允许 0/1/2/3）")
            v = str(value or "").strip()
            if not v:
                raise ValueError(f"运镜档位 {k} 文案不能为空")
            if len(v) > _MAX_CAMERA_BAND:
                raise ValueError(f"运镜文案过长（≤{_MAX_CAMERA_BAND} 字）")
            bands[k] = v
        if len(bands) > 4:
            raise ValueError("运镜覆盖最多 4 个档位")
        beats_raw = record.get("beat_labels") or []
        if not isinstance(beats_raw, list):
            raise ValueError("节奏名必须是数组")
        beats = [str(item or "").strip() for item in beats_raw]
        if any(not b for b in beats):
            raise ValueError("节奏名不能为空项")
        if len(beats) > _MAX_BEAT_LABELS:
            raise ValueError(f"节奏名过多（≤{_MAX_BEAT_LABELS} 条）")
        if any(len(b) > _MAX_BEAT_LABEL for b in beats):
            raise ValueError(f"节奏名过长（各 ≤{_MAX_BEAT_LABEL} 字）")
        duration_raw = record.get("default_duration") or 0
        if not isinstance(duration_raw, int) or isinstance(duration_raw, bool):
            raise ValueError("默认时长必须是整数（秒）")
        if duration_raw and not (2 <= duration_raw <= 20):
            raise ValueError("默认时长须在 2–20 秒之间（0=不覆盖）")
        negative_extra = str(record.get("negative_extra") or "")
        meta_note = str(record.get("meta_note") or "")
        if len(negative_extra) > _MAX_NEGATIVE_EXTRA:
            raise ValueError(f"负面追加词过长（≤{_MAX_NEGATIVE_EXTRA} 字符）")
        if len(meta_note) > _MAX_META_NOTE:
            raise ValueError(f"元信息备注过长（≤{_MAX_META_NOTE} 字符）")
        out.update({
            "section_titles": titles,
            "section_order": order,
            "camera_band_overrides": bands,
            "beat_labels": beats,
            "default_duration": duration_raw,
            "negative_extra": negative_extra,
            "meta_note": meta_note,
        })
    return out


# ── CRUD ─────────────────────────────────────────────────────────────────────

def get_template(template_id: str) -> dict | None:
    """读取一条模版；`builtin:<kind>` 返回内置投影，坏文件/不存在返回 None。"""
    raw = str(template_id or "").strip()
    if raw.startswith(_BUILTIN_PREFIX):
        kind = raw[len(_BUILTIN_PREFIX):]
        if kind not in KINDS:
            return None
        try:
            return builtin_view(kind)
        except ValueError:
            return None
    tid = custom_id_of(raw) or raw
    if not tid:
        return None
    try:
        tid = validate_template_id(tid)
    except ValueError:
        return None
    p = _path(tid)
    if not p.exists():
        return None
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
        validated = _validate_record(record)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        # 磁盘 JSON 可被手改：坏文件静默跳过（不炸生成链），由 trace 暴露。
        try:
            from app.services import run_trace

            run_trace.emit(
                {"thread_id": "", "turn_id": "", "repo_id": ""},
                "prompt_template.bad_file", template_id=tid,
            )
        except Exception:  # noqa: BLE001 遥测 best-effort
            pass
        return None
    validated.update({
        "id": tid,
        "builtin": False,
        "created_at": float(record.get("created_at") or 0.0),
        "updated_at": float(record.get("updated_at") or 0.0),
    })
    return validated


def list_templates(kind: str = "") -> list[dict]:
    """全部自定义模版（按更新时间倒序；可按 kind 过滤；坏文件跳过）。"""
    _ensure_dir()
    items: list[dict] = []
    for p in sorted(PROMPT_TEMPLATES_DIR.glob("*.json")):
        tid = p.stem
        if not _ID_RE.fullmatch(tid):
            continue
        record = get_template(tid)
        if record is None:
            continue
        if kind and record.get("kind") != kind:
            continue
        items.append(record)
    items.sort(key=lambda t: t.get("updated_at", 0.0), reverse=True)
    return items


def save_template(data: dict, template_id: str | None = None) -> dict:
    """新建/更新自定义模版（内置不可写：调用方先判 builtin 前缀）。"""
    _ensure_dir()
    now = time.time()
    if template_id:
        tid = validate_template_id(template_id)
        existing = get_template(tid)
        if existing is None:
            raise ValueError("模版不存在")
        created = existing.get("created_at", now)
    else:
        tid = uuid.uuid4().hex
        created = now
    payload = {**data, "id": tid}
    # 克隆基准是内置时补算内容 hash（供前端「有更新」比对；路由克隆路径已带，
    # 这里兜底直存场景）
    if (not str(payload.get("base_hash") or "").strip()
            and str(payload.get("base_id") or "").startswith(_BUILTIN_PREFIX)):
        base_kind = str(payload["base_id"])[len(_BUILTIN_PREFIX):]
        if base_kind in KINDS:
            payload["base_hash"] = str(builtin_view(base_kind).get("content_hash") or "")
    validated = _validate_record(payload)
    record = {
        **validated,
        "id": tid,
        "builtin": False,
        "created_at": created,
        "updated_at": now,
    }
    _path(tid).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def delete_template(template_id: str) -> bool:
    """删除自定义模版；内置/不存在返回 False。"""
    try:
        tid = validate_template_id(template_id)
    except ValueError:
        return False
    p = _path(tid)
    if p.exists():
        p.unlink()
        return True
    return False


# ── 生成链接线解析（编排层专用；纯函数模块不 import 本 store）─────────────────

def image_overrides(kind: str, record: dict | None) -> dict:
    """自定义生图模版 ⇒ 生成侧 override（system_override / quality_override /
    negative_override）。record 为空 ⇒ 全 None（内置现行为）。

    - system_override = system_text + extra_instructions（视角条款仍由代码注入）；
    - quality/negative 仅 anima 档消费（其余档的这两个字段在 UI 不开放）。
    """
    if not record:
        return {"system_override": None, "quality_override": None, "negative_override": None}
    system_text = str(record.get("system_text") or "")
    extra = str(record.get("extra_instructions") or "").strip()
    combined = (system_text + ("\n\n" + extra if extra else "")).strip()
    anima = kind == "anima_tags"
    return {
        "system_override": combined or None,
        "quality_override": (str(record.get("quality_tags") or "") or None) if anima else None,
        "negative_override": (str(record.get("negative_tags") or "") or None) if anima else None,
    }


def resolve_profile(raw_profile: str) -> tuple[str, dict | None]:
    """把请求侧的 profile 取值解析成 (内置 kind, 自定义模版记录|None)。

    - `custom:<hex>` 且模版存在 ⇒ (模版 kind, 模版记录)；
    - `custom:<hex>` 但模版缺失/坏文件 ⇒ ("", None)——调用方按预设 kind 降级内置；
    - 内置 id（含已下线的 krea2）⇒ 原样透传，行为逐字不变；
    - 未知值 ⇒ ("anima_tags", None)（与 normalize_inline 的降级口径一致）。
    """
    raw = str(raw_profile or "").strip()
    if raw.startswith(_CUSTOM_PREFIX):
        record = get_template(raw)
        if record is not None:
            return str(record.get("kind") or ""), record
        return "", None
    from app.services import image_prompt_profiles

    if raw in image_prompt_profiles.PROFILE_IDS:
        return raw, None
    return "anima_tags", None


def resolve_video_skeleton(template_id: str, mode: str) -> dict | None:
    """按当前视频模式取自定义骨架数据；kind 不匹配/缺失/坏文件 ⇒ None（内置行为）。

    mode ∈ climax/firstlast/transition；模板 kind 必须为 `video_<mode>` 才对得上。
    """
    raw = str(template_id or "").strip()
    if not raw.startswith(_CUSTOM_PREFIX):
        return None
    record = get_template(raw)
    if record is None:
        return None
    if record.get("kind") != f"video_{mode}":
        return None
    return {
        "section_titles": record.get("section_titles") or {},
        "section_order": record.get("section_order") or [],
        "camera_band_overrides": record.get("camera_band_overrides") or {},
        "beat_labels": record.get("beat_labels") or [],
        "default_duration": record.get("default_duration") or 0,
        "negative_extra": record.get("negative_extra") or "",
        "meta_note": record.get("meta_note") or "",
    }
