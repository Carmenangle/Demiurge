"""场景分类纯逻辑：标签规整 + 中文别名 + 兜底。"""
from __future__ import annotations

from app.services import scene_classify as sc


def test_合法标签直通():
    for s in sc.SCENES:
        assert sc.normalize_scene(s) == s


def test_大小写与标点清洗():
    assert sc.normalize_scene(" NSFW. ") == "nsfw"
    assert sc.normalize_scene("`Climax`") == "climax"


def test_中文别名归一():
    assert sc.normalize_scene("对话") == "dialogue"
    assert sc.normalize_scene("战斗") == "action"
    assert sc.normalize_scene("情色") == "nsfw"
    assert sc.normalize_scene("转折") == "climax"


def test_含子串句子():
    assert sc.normalize_scene("这是nsfw场景") == "nsfw"
    assert sc.normalize_scene("偏向dialogue") == "dialogue"


def test_识别不了返回空():
    assert sc.normalize_scene("") == ""
    assert sc.normalize_scene("xyz无关") == ""


def test_角色卡直达时用纯规则保守分类():
    assert sc.infer_scene("两人开始激烈争吵") == "conflict"
    assert sc.infer_scene("她拔剑迎战") == "action"
    assert sc.infer_scene("继续刚才的对话") == "dialogue"


def test_明确成人剧情请求不会被误判为普通对话():
    text = "第二天,描写冷倾雪的饥渴难耐与完全征服收为己用,描写2000字,肉戏尽可能的丰富"

    assert sc.infer_scene(text) == "nsfw"
