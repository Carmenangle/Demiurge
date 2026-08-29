"""prose_style S1 单测：用户态配置 / 生效词表 / 注入段编译 / 开关逐字节一致。"""
from __future__ import annotations

import json

import pytest

from app.services import narrative_ci, prose_style


@pytest.fixture()
def style_config_file(tmp_path, monkeypatch):
    path = tmp_path / "prose_style.json"

    def write(data: dict | None):
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(prose_style, "_config_path", lambda: path)
    return write


# ── load_config ──────────────────────────────────────────────────────────────

def test_缺文件回退内置默认(style_config_file):
    style_config_file(None)
    assert prose_style.load_config() == {"enabled": True, "extra": [], "removed": [], "review_every": 5}


def test_坏JSON回退内置默认(style_config_file):
    prose_style._config_path().write_text("{broken", encoding="utf-8")
    assert prose_style.load_config()["enabled"] is True


def test_坏字段类型按缺省处理(style_config_file):
    style_config_file({"enabled": "yes", "extra": "赋能", "removed": 3})
    cfg = prose_style.load_config()
    assert cfg == {"enabled": True, "extra": [], "removed": [], "review_every": 5}


# ── effective_phrases：增删合一，lint 与生成侧共用 ────────────────────────────

def test_用户增词生效(style_config_file):
    style_config_file({"extra": ["就这么定了"], "removed": []})
    cfg = prose_style.load_config()
    assert "就这么定了" in prose_style.effective_phrases(cfg)
    result = prose_style.lint("就这么定了，他转身离开。", banned_phrases=prose_style.effective_phrases(cfg))
    assert prose_style.CODE_STYLE_BANNED_PHRASE in [item["code"] for item in result]


def test_用户删词生效(style_config_file):
    style_config_file({"removed": ["赋能"]})
    cfg = prose_style.load_config()
    assert "赋能" not in prose_style.effective_phrases(cfg)
    assert prose_style.lint("他被赋能了。", banned_phrases=prose_style.effective_phrases(cfg)) == []


def test_与内置重复的增词去重(style_config_file):
    style_config_file({"extra": ["赋能", "新词"], "removed": []})
    phrases = prose_style.effective_phrases(prose_style.load_config())
    assert phrases.count("赋能") == 1


# ── 注入段编译 ───────────────────────────────────────────────────────────────

def test_默认注入段含文风要求与词表():
    seg = prose_style.style_prompt_segment()
    assert "【文风要求】" in seg
    assert "「赋能」" in seg
    assert len(seg) < 600  # 约束段控制在短量级，不吃正文额度


def test_开关关闭注入段为空(style_config_file):
    style_config_file({"enabled": False})
    assert prose_style.style_prompt_segment(prose_style.load_config()) == ""


def test_注入段跟随用户增删(style_config_file):
    style_config_file({"extra": ["自创套路"], "removed": ["赋能"]})
    seg = prose_style.style_prompt_segment(prose_style.load_config())
    assert "「自创套路」" in seg
    assert "「赋能」" not in seg


# ── evaluate 与开关贯通 ──────────────────────────────────────────────────────

def test_evaluate开关关闭跳过全部文风检测():
    body = "这计划简直是完美的闭环。难道就这样算了？不，绝不。"
    result = narrative_ci.evaluate(body, turn=1, style_config={"enabled": False})
    assert not [item for item in result if item["source"] == "prose_style"]
    # 默认（无 style_config）仍然检测
    result_default = narrative_ci.evaluate(body, turn=1)
    assert [item for item in result_default if item["source"] == "prose_style"]


# ── save_config（API 属主侧）─────────────────────────────────────────────────

def test_save_config落盘并可回读(style_config_file):
    saved = prose_style.save_config({"enabled": False, "extra": ["自创词"], "removed": ["赋能"], "hacker": 1})
    assert saved == {"enabled": False, "extra": ["自创词"], "removed": ["赋能"], "review_every": 5}
    assert prose_style.load_config() == saved


def test_save_config坏值回退不炸(style_config_file):
    saved = prose_style.save_config({"extra": "不是列表", "removed": None})
    assert saved == {"enabled": True, "extra": [], "removed": [], "review_every": 5}
