"""提示词模版 store + REST 测试（2026-10-25 工作台 P1）。

覆盖执行版提示词 P1 验收项：内置投影与代码常量逐字一致、CRUD、id 白名单
（拒绝 ../、含斜杠、超长、非 hex）、内置只读（PUT 409 / DELETE 405）、
字段上限 422、section_order 排列 422、红线锚文本锁开关、坏 JSON 跳过、
克隆 seeding、resolve_profile / resolve_video_skeleton 解析与降级。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.services import prompt_template_store as store
from app.services.image_prompt_profile_templates import (
    ANIMA_NEGATIVE_TAGS,
    ANIMA_QUALITY_TAGS,
    _SYSTEMS,
    missing_redline_anchors,
)
from app.services import video_prompt

BASE = "/prompt/templates"


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    """模版目录落 tmp（绝不碰真实 backend/data/prompt_templates）。"""
    root = tmp_path / "prompt_templates"
    monkeypatch.setattr(store, "PROMPT_TEMPLATES_DIR", root)
    return root


@pytest.fixture
def client():
    """迷你 app 挂本路由（不带 lifespan worker；conftest 的 app.main 闸与此无关）。"""
    from fastapi import FastAPI

    from app.routers import ai_prompt_templates

    app = FastAPI()
    app.include_router(ai_prompt_templates.router)
    return TestClient(app)


# ── 内置投影 ─────────────────────────────────────────────────────────────────

def test_builtin_projection_matches_code_constants():
    """三档生图 system/质量/负面与视频骨架默认值与代码常量逐字一致。"""
    for kind in store.IMAGE_KINDS:
        view = store.builtin_view(kind)
        assert view["id"] == f"builtin:{kind}"
        assert view["builtin"] is True
        assert view["system_text"] == _SYSTEMS[kind]
        assert view["extra_instructions"] == ""
        if kind == "anima_tags":
            assert view["quality_tags"] == ANIMA_QUALITY_TAGS
            assert view["negative_tags"] == ANIMA_NEGATIVE_TAGS
        else:
            assert view["quality_tags"] == ""
            assert view["negative_tags"] == ""
        assert view["content_hash"] == view["content_hash"].strip() and len(view["content_hash"]) == 8
    # krea2 不进内置列表（已下线，代码常量保留）
    assert "krea2" not in store.KINDS
    assert len(store.list_builtins()) == 6


def test_builtin_video_projection_matches_defaults():
    for kind, mode in (("video_climax", "climax"), ("video_firstlast", "firstlast"),
                       ("video_transition", "transition")):
        view = store.builtin_view(kind)
        data = video_prompt.default_skeleton_data(mode)
        assert view["section_titles"] == data["section_titles"]
        assert view["section_order"] == data["section_order"]
        assert view["beat_labels"] == data["beat_labels"]
        assert view["default_duration"] == data["default_duration"]
        assert view["negative_extra"] == "" and view["meta_note"] == ""


def test_content_hash_changes_with_content():
    h1 = store.builtin_view("anima_tags")["content_hash"]
    import hashlib

    expect = hashlib.sha256(_SYSTEMS["anima_tags"].encode("utf-8")).hexdigest()[:8]
    assert h1 == expect


# ── CRUD ─────────────────────────────────────────────────────────────────────

def _anima_body(**over):
    body = {
        "kind": "anima_tags", "name": "我的 Anima",
        "system_text": _SYSTEMS["anima_tags"], "extra_instructions": "追加一段",
    }
    body.update(over)
    return body


def test_create_get_update_delete_roundtrip(store_dir, client):
    created = client.post(BASE, json=_anima_body()).json()
    tid = created["id"]
    assert len(tid) == 32 and created["builtin"] is False
    assert created["extra_instructions"] == "追加一段"
    got = client.get(f"{BASE}/{tid}").json()
    assert got["system_text"] == _SYSTEMS["anima_tags"]
    listed = client.get(BASE, params={"kind": "anima_tags"}).json()
    assert [item["id"] for item in listed["items"]] == [tid]
    assert [b["id"] for b in listed["builtins"]] == ["builtin:anima_tags"]
    updated = client.put(f"{BASE}/{tid}", json=_anima_body(name="改名", extra_instructions="")).json()
    assert updated["name"] == "改名" and updated["extra_instructions"] == ""
    assert client.delete(f"{BASE}/{tid}").json() == {"ok": True}
    assert client.get(f"{BASE}/{tid}").status_code == 404


def test_clone_from_builtin_seeds_full_text(store_dir, client):
    created = client.post(BASE, json={
        "kind": "anima_tags", "name": "克隆", "base_id": "builtin:anima_tags",
    }).json()
    assert created["system_text"] == _SYSTEMS["anima_tags"]
    assert created["quality_tags"] == ANIMA_QUALITY_TAGS
    assert created["negative_tags"] == ANIMA_NEGATIVE_TAGS
    assert created["base_id"] == "builtin:anima_tags"
    assert created["base_hash"] == store.builtin_view("anima_tags")["content_hash"]


def test_clone_from_custom_inherits_base_chain(store_dir, client):
    first = client.post(BASE, json={
        "kind": "anima_tags", "name": "一", "base_id": "builtin:anima_tags",
        "system_text": _SYSTEMS["anima_tags"],
    }).json()
    second = client.post(BASE, json={
        "kind": "anima_tags", "name": "二", "base_id": f"custom:{first['id']}",
    }).json()
    assert second["base_id"] == "builtin:anima_tags"
    assert second["base_hash"] == first["base_hash"]


def test_video_template_crud(store_dir, client):
    body = {
        "kind": "video_climax", "name": "我的高潮",
        "section_titles": {"meta": "元信息·改"},
        "camera_band_overrides": {"2": "镜头绕主体旋转180度"},
        "beat_labels": ["起", "承", "转"],
        "default_duration": 8, "negative_extra": "模糊", "meta_note": "备注",
    }
    created = client.post(BASE, json=body).json()
    assert created["section_titles"] == {"meta": "元信息·改"}
    assert created["camera_band_overrides"] == {"2": "镜头绕主体旋转180度"}
    assert created["default_duration"] == 8


# ── id 白名单 / 路径穿越 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "x" * 33, "XYZ", "uppercase", ""])
def test_id_whitelist_rejects_bad_ids(store_dir, bad):
    with pytest.raises(ValueError):
        store.validate_template_id(bad)
    assert store.get_template(bad) is None
    assert store.delete_template(bad) is False


def test_path_traversal_cannot_escape(store_dir):
    """白名单在先：即便构造出越权文件名也拼不进路径。"""
    with pytest.raises(ValueError):
        store._path("../../secrets")
    (store_dir).mkdir(parents=True, exist_ok=True)
    evil = store_dir.parent / "evil.json"
    evil.write_text("{}", encoding="utf-8")
    assert store.get_template("../evil") is None


# ── 内置只读 ─────────────────────────────────────────────────────────────────

def test_builtin_readonly(client):
    assert client.put(f"{BASE}/builtin:anima_tags", json=_anima_body()).status_code == 409
    assert client.delete(f"{BASE}/builtin:anima_tags").status_code == 405
    # GET 内置全文可读
    view = client.get(f"{BASE}/builtin:anima_tags").json()
    assert view["system_text"] == _SYSTEMS["anima_tags"]


# ── 字段上限 / 排列 / 锚文本锁 ───────────────────────────────────────────────

def test_field_limits_422(store_dir, client):
    base = {"kind": "anima_tags", "name": "x", "system_text": _SYSTEMS["anima_tags"]}
    big = _SYSTEMS["anima_tags"] + "x" * 12_000
    assert client.post(BASE, json={**base, "system_text": big}).status_code == 422
    assert client.post(BASE, json={**base, "quality_tags": "y" * 2_001}).status_code == 422
    vbase = {"kind": "video_climax", "name": "x"}
    assert client.post(BASE, json={**vbase, "meta_note": "z" * 501}).status_code == 422
    assert client.post(BASE, json={**vbase, "default_duration": 99}).status_code == 422
    assert client.post(BASE, json={**vbase, "camera_band_overrides": {"9": "x"}}).status_code == 422
    assert client.post(BASE, json={**vbase, "section_titles": {"nope": "x"}}).status_code == 422


def test_section_order_must_be_permutation(store_dir, client):
    vbase = {"kind": "video_climax", "name": "x"}
    good = list(video_prompt.SECTION_KEYS)
    assert client.post(BASE, json={**vbase, "section_order": good}).status_code == 200
    bad = good[:-1]                       # 缺项
    assert client.post(BASE, json={**vbase, "section_order": bad}).status_code == 422
    dup = [*good, good[0]]                # 重复
    assert client.post(BASE, json={**vbase, "section_order": dup}).status_code == 422
    shuffled = list(reversed(good))       # 合法排列（乱序≠非法）
    assert client.post(BASE, json={**vbase, "section_order": shuffled}).status_code == 200


def test_redline_lock_on_and_off(store_dir, client):
    stripped = _SYSTEMS["anima_tags"].replace("【多人分述】", "【某段落】")
    missing = missing_redline_anchors(stripped)
    assert missing and any("多人分述" in m for m in missing)
    # 锁开：缺锚文本 422 且指名缺失段
    resp = client.post(BASE, json={
        "kind": "anima_tags", "name": "坏", "system_text": stripped,
    })
    assert resp.status_code == 422
    assert "多人分述" in resp.json()["detail"]
    # 关锁：放行并留痕
    ok = client.post(BASE, json={
        "kind": "anima_tags", "name": "坏", "system_text": stripped, "redline_lock": False,
    })
    assert ok.status_code == 200
    assert ok.json()["redline_lock"] is False
    assert ok.json()["redline_lock_off_at"] > 0


# ── 坏文件容错 ───────────────────────────────────────────────────────────────

def test_bad_json_file_skipped(store_dir, client):
    good = client.post(BASE, json=_anima_body()).json()
    (store_dir).mkdir(parents=True, exist_ok=True)
    (store_dir / f"{good['id']}.json").write_text("{ 坏 json", encoding="utf-8")
    assert store.get_template(good["id"]) is None
    assert store.list_templates() == []


def test_hand_edited_invalid_file_skipped(store_dir):
    """磁盘 JSON 被手改成超长文本：load 复校拦截，不炸生成链。"""
    store_dir.mkdir(parents=True, exist_ok=True)
    tid = "a" * 32
    (store_dir / f"{tid}.json").write_text(json.dumps({
        "kind": "anima_tags", "name": "x", "system_text": "y" * 99_999,
    }), encoding="utf-8")
    assert store.get_template(tid) is None


# ── 解析入口 ─────────────────────────────────────────────────────────────────

def test_resolve_profile(store_dir, client):
    created = client.post(BASE, json=_anima_body()).json()
    kind, data = store.resolve_profile(f"custom:{created['id']}")
    assert kind == "anima_tags" and data is not None and data["id"] == created["id"]
    # 缺失/坏形态 ⇒ ("", None)，由调用方按预设 kind 降级
    assert store.resolve_profile("custom:" + "b" * 32) == ("", None)
    assert store.resolve_profile("custom:../x") == ("", None)
    # 内置 id 原样透传（含已下线 krea2）
    assert store.resolve_profile("natural_language") == ("natural_language", None)
    assert store.resolve_profile("krea2") == ("krea2", None)
    assert store.resolve_profile("") == ("anima_tags", None)
    assert store.resolve_profile("bogus") == ("anima_tags", None)


def test_resolve_video_skeleton_kind_must_match_mode(store_dir, client):
    created = client.post(BASE, json={
        "kind": "video_climax", "name": "v",
        "section_titles": {"meta": "元信息·改"}, "default_duration": 8,
    }).json()
    sk = store.resolve_video_skeleton(f"custom:{created['id']}", "climax")
    assert sk is not None and sk["default_duration"] == 8
    # 模式不匹配 ⇒ None（走内置行为）
    assert store.resolve_video_skeleton(f"custom:{created['id']}", "firstlast") is None
    assert store.resolve_video_skeleton("", "climax") is None
    assert store.resolve_video_skeleton("builtin:video_climax", "climax") is None
