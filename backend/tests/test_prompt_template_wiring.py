"""提示词模版生成链接线测试（2026-10-25 P2）。

覆盖编排层接线：custom:<id> 解析为内置 kind（scene_spec/校验链只认 kind）、
模版缺失/kind 不匹配降级 + trace、override 提取、视频骨架按 mode 匹配。
真源：agency_scene_comfy._resolve_prompt_template / _template_overrides /
_video_skeleton_for + prompt_template_store.resolve_*。
"""
from __future__ import annotations

import pytest

from app.services import agency_scene_comfy as asc
from app.services import prompt_template_store as store


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    root = tmp_path / "prompt_templates"
    monkeypatch.setattr(store, "PROMPT_TEMPLATES_DIR", root)
    return root


def _save(kind: str, name: str, **fields) -> dict:
    base = {"kind": kind, "name": name}
    if kind in store.IMAGE_KINDS:
        from app.services.image_prompt_profile_templates import _SYSTEMS

        base.setdefault("system_text", _SYSTEMS[kind])
    base.update(fields)
    return store.save_template(base)


# ── 生图：解析与降级 ─────────────────────────────────────────────────────────

def test_resolve_builtin_profile_passthrough(store_dir):
    kind, record, missing = asc._resolve_prompt_template({"prompt_profile": "natural_language"})
    assert (kind, record, missing) == ("natural_language", None, False)
    # 已下线 krea2 原样透传（下游行为不变）
    assert asc._resolve_prompt_template({"prompt_profile": "krea2"})[0] == "krea2"
    # 空/未知 ⇒ anima_tags（与 normalize_inline 降级口径一致）
    assert asc._resolve_prompt_template({})[0] == "anima_tags"
    assert asc._resolve_prompt_template({"prompt_profile": "bogus"})[0] == "anima_tags"


def test_resolve_custom_profile_to_kind(store_dir):
    tpl = _save("natural_language", "我的自然语言")
    ctx = {"prompt_profile": f"custom:{tpl['id']}", "prompt_profile_kind": "natural_language"}
    kind, record, missing = asc._resolve_prompt_template(ctx)
    assert kind == "natural_language"      # scene_spec["profile"] 只落内置 kind
    assert record is not None and record["id"] == tpl["id"]
    assert missing is False


def test_resolve_custom_missing_degrades_by_preset_kind(store_dir):
    ctx = {"prompt_profile": "custom:" + "b" * 32, "prompt_profile_kind": "niji_sections"}
    kind, record, missing = asc._resolve_prompt_template(ctx)
    assert kind == "niji_sections" and record is None and missing is True
    # 预设也没存 kind（旧数据）⇒ 降级 anima_tags，不抛错
    assert asc._resolve_prompt_template({"prompt_profile": "custom:" + "c" * 32})[0] == "anima_tags"


def test_template_overrides_shape(store_dir):
    tpl = _save("anima_tags", "a", extra_instructions="追加段",
                quality_tags="best quality, mine", negative_tags="模糊")
    ov = asc._template_overrides("anima_tags", tpl)
    assert ov["system_override"].endswith("\n\n追加段")
    assert ov["quality_override"] == "best quality, mine"
    assert ov["negative_override"] == "模糊"
    # 非 anima 档不消费质量/负面词
    tpl2 = _save("natural_language", "n", quality_tags="x", negative_tags="y")
    ov2 = asc._template_overrides("natural_language", tpl2)
    assert ov2["quality_override"] is None and ov2["negative_override"] is None
    # 无模版 ⇒ 全 None（内置现行为）
    assert asc._template_overrides("anima_tags", None) == {
        "system_override": None, "quality_override": None, "negative_override": None}


# ── 视频：骨架按 mode 匹配 ───────────────────────────────────────────────────

def test_video_skeleton_matches_mode(store_dir):
    tpl = _save("video_climax", "v", section_titles={"meta": "元信息·改"}, default_duration=8)
    ctx = {"video_prompt_template_id": f"custom:{tpl['id']}"}
    sk = asc._video_skeleton_for(ctx, "climax")
    assert sk is not None and sk.default_duration == 8
    assert sk.section_titles == {"meta": "元信息·改"}
    # 模式不匹配 ⇒ None（内置骨架），不抛错
    assert asc._video_skeleton_for(ctx, "firstlast") is None
    # 缺失/空/内置 ⇒ None
    assert asc._video_skeleton_for({"video_prompt_template_id": "custom:" + "d" * 32}, "climax") is None
    assert asc._video_skeleton_for({"video_prompt_template_id": ""}, "climax") is None
    assert asc._video_skeleton_for({"video_prompt_template_id": "builtin:video_climax"}, "climax") is None


def test_video_skeleton_missing_emits_trace(store_dir):
    """缺失降级必须留痕（trace illustration.video_template resolved=false）。"""
    from app.services import run_trace

    events: list[tuple] = []
    original = run_trace.emit

    def _spy(ctx, event, **data):
        events.append((event, data))

    run_trace.emit = _spy
    try:
        asc._video_skeleton_for({"video_prompt_template_id": "custom:" + "e" * 32}, "climax")
    finally:
        run_trace.emit = original
    assert events and events[0][0] == "illustration.video_template"
    assert events[0][1]["resolved"] is False and events[0][1]["reason"] == "missing"


# ── ai_text 端点：custom id 消费与悬空降级（HTTP 级回归） ──────────────────────

@pytest.fixture
def text_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import ai_text

    app = FastAPI()
    app.include_router(ai_text.router)
    return TestClient(app)


def test_profile_defaults_endpoint_custom_and_dangling(store_dir, text_client):
    """defaults 端点吃 custom:<id>；模版删后降级内置（200，不 400/500）。"""
    tpl = _save("anima_tags", "端点冒烟", quality_tags="q_only", negative_tags="n_only")
    ok = text_client.get("/prompt/profile/defaults",
                         params={"profile": f"custom:{tpl['id']}", "rating": "nsfw"})
    assert ok.status_code == 200
    assert ok.json() == {"quality_prompt": "q_only", "negative_prompt": "n_only"}
    # 悬空引用：删除后仍 200，回退内置 anima 默认（真机 2026-10-25 修过 400 回归）
    store.delete_template(tpl["id"])
    dangling = text_client.get("/prompt/profile/defaults",
                               params={"profile": f"custom:{tpl['id']}"})
    assert dangling.status_code == 200
    assert dangling.json()["quality_prompt"].startswith("masterpiece")
    # 非 anima 自定义模版：质量/负面走内置（空）
    tpl2 = _save("natural_language", "n")
    nat = text_client.get("/prompt/profile/defaults",
                          params={"profile": f"custom:{tpl2['id']}"})
    assert nat.status_code == 200
    assert nat.json() == {"quality_prompt": "", "negative_prompt": ""}
