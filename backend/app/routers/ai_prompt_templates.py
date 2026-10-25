"""/api/ai/prompt/templates 提示词模版 CRUD（2026-10-25）。

薄路由：校验与业务全在 `services/prompt_template_store`，本文件只做
Pydantic 入参、HTTP 语义（内置只读 409/405、坏 id 400/404）与克隆 seeding。
挂到 `routers/ai.py` 聚合器（/api/ai 前缀下），不改 main.py。
"""
from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.services import prompt_template_store as store

router = APIRouter(prefix="/prompt/templates", tags=["ai"])

TemplateKind = Literal[
    "anima_tags", "natural_language", "niji_sections",
    "video_climax", "video_firstlast", "video_transition",
]

# 克隆种子键（内置/自定义投影里同名字段原样搬来）
_SEED_KEYS = (
    "system_text", "extra_instructions", "quality_tags", "negative_tags",
    "section_titles", "section_order", "camera_band_overrides",
    "beat_labels", "default_duration", "negative_extra", "meta_note",
)


class TemplateBody(BaseModel):
    """新建/更新入参。字段按 kind 分组消费（生图字段/视频字段），另一组忽略。"""

    kind: TemplateKind
    name: str = Field(default="", max_length=100)
    # 生图字段
    system_text: str = ""
    extra_instructions: str = ""
    quality_tags: str = ""
    negative_tags: str = ""
    redline_lock: bool = True
    # 视频字段
    section_titles: dict[str, str] = Field(default_factory=dict)
    section_order: list[str] = Field(default_factory=list)
    camera_band_overrides: dict[str, str] = Field(default_factory=dict)
    beat_labels: list[str] = Field(default_factory=list)
    default_duration: int = 0
    negative_extra: str = ""
    meta_note: str = ""
    # 克隆基准：builtin:<kind>（内置克隆）或 custom:<hex>（复制我的模版）
    base_id: str = ""

    @model_validator(mode="after")
    def _check_limits(self) -> "TemplateBody":
        if len(self.system_text) + len(self.extra_instructions) > 12_000:
            raise ValueError("system_text + extra_instructions 合计不得超过 12000 字符")
        if len(self.quality_tags) > 2_000 or len(self.negative_tags) > 2_000:
            raise ValueError("质量词/负面词各不得超过 2000 字符")
        if len(self.negative_extra) > 1_000:
            raise ValueError("负面追加词不得超过 1000 字符")
        if len(self.meta_note) > 500:
            raise ValueError("元信息备注不得超过 500 字符")
        if self.default_duration and not (2 <= self.default_duration <= 20):
            raise ValueError("默认时长须在 2–20 秒之间（0=不覆盖）")
        for key, value in self.section_titles.items():
            if key not in store.SECTION_KEYS_PUBLIC:
                raise ValueError(f"未知区块键：{key}")
            if len(value) > 200:
                raise ValueError("区块标题不得超过 200 字")
        if self.section_order and sorted(self.section_order) != sorted(store.SECTION_KEYS_PUBLIC):
            raise ValueError("区块顺序必须是六个区块键的排列")
        for key, value in self.camera_band_overrides.items():
            if key not in ("0", "1", "2", "3"):
                raise ValueError("运镜档位键只允许 0/1/2/3")
            if len(value) > 120:
                raise ValueError("运镜文案不得超过 120 字")
        if len(self.beat_labels) > 6:
            raise ValueError("节奏名最多 6 条")
        return self


def _seed_from_base(body: TemplateBody) -> tuple[dict, str, str]:
    """克隆 seeding：base_id 为内置 ⇒ 取其全文投影；为自定义 ⇒ 复制该记录。

    返回 (种子字段, 继承的 base_id, 继承的 base_hash)。
    """
    base_id = (body.base_id or "").strip()
    if not base_id:
        return {}, "", ""
    base = store.get_template(base_id)
    if base is None:
        raise HTTPException(status_code=400, detail=f"克隆基准不存在：{base_id}")
    seed = {key: base.get(key, "") for key in _SEED_KEYS}
    if base.get("builtin"):
        # 克隆内置：基准=该内置（哈希现算，供「有更新」比对）
        return seed, base_id, str(base.get("content_hash") or "")
    # 克隆自定义模版：继承其克隆基准，保留「有更新」比对链
    return seed, str(base.get("base_id") or base_id), str(base.get("base_hash") or "")


def _body_record(body: TemplateBody, seed: dict, base_id: str, base_hash: str) -> dict:
    """请求体覆盖克隆种子（空值=用种子）。"""
    record = {
        "kind": body.kind,
        "name": body.name.strip() or "未命名模版",
        "base_id": base_id,
        "base_hash": base_hash,
        "redline_lock": body.redline_lock,
    }
    for key in _SEED_KEYS:
        value = getattr(body, key, "")
        record[key] = value if value else seed.get(key, "")
    return record


@router.get("")
def list_templates(kind: str = "") -> dict:
    """模版列表：items=我的模版（新→旧），builtins=内置默认（只读全文）。"""
    if kind and kind not in store.KINDS:
        raise HTTPException(status_code=400, detail=f"未知模版协议族：{kind}")
    return {"items": store.list_templates(kind), "builtins": store.list_builtins(kind)}


@router.get("/{template_id}")
def get_template(template_id: str) -> dict:
    """模版详情；`builtin:<kind>` 返回内置全文投影（供查看默认模版）。"""
    record = store.get_template(template_id)
    if record is None:
        raise HTTPException(status_code=404, detail="模版不存在或已损坏")
    return record


@router.post("")
def create_template(body: TemplateBody) -> dict:
    """新建/克隆自定义模版（base_id 为内置即「克隆默认模版」）。"""
    seed, base_id, base_hash = _seed_from_base(body)
    record = _body_record(body, seed, base_id, base_hash)
    # 关锁留痕（与 PUT 同口径）
    record["redline_lock_off_at"] = 0.0 if body.redline_lock else time.time()
    try:
        return store.save_template(record)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{template_id}")
def update_template(template_id: str, body: TemplateBody) -> dict:
    """更新自定义模版；内置只读（409）。kind 不可改（防协议族形状漂移）。"""
    raw = (template_id or "").strip()
    if raw.startswith("builtin:"):
        raise HTTPException(status_code=409, detail="内置模版只读，请克隆后再改")
    existing = store.get_template(raw)
    if existing is None:
        raise HTTPException(status_code=404, detail="模版不存在或已损坏")
    if existing.get("kind") != body.kind:
        raise HTTPException(status_code=400, detail="模版协议族不可更改")
    record = _body_record(body, {}, str(existing.get("base_id") or ""),
                          str(existing.get("base_hash") or ""))
    # 关锁留痕：开→关时打时间戳；关→开/持续关时重置（避免陈旧时间戳误示）
    record["redline_lock_off_at"] = 0.0 if body.redline_lock else time.time()
    try:
        return store.save_template(record, existing["id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{template_id}")
def delete_template(template_id: str) -> dict:
    """删除自定义模版；内置不可删（405）。"""
    raw = (template_id or "").strip()
    if raw.startswith("builtin:"):
        raise HTTPException(status_code=405, detail="内置模版不可删除")
    if not store.delete_template(raw):
        raise HTTPException(status_code=404, detail="模版不存在")
    return {"ok": True}
