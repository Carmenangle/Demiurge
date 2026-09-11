"""配方相似度判定层（`recipe_match`）——事前固化询问的零 LLM 判定（2026-09-10）。

这一层的取向是**宁可漏问，不可多问**：误判「已有」只是少问一次（无害），
误判「没有」则每一条同类请求都被打断一次。所以边界断言比命中断言更重要。
"""
from app.services import recipe_match

_TASK = ("帮我把世界书里所有角色条目、角色卡外貌与近期纪要整理合并成一个设定文档，"
         "写到作品 docs/ 目录，文件名用「设定总集.md」")
_OTHER = "把小说正文整理成合集卡，批量生成角色卡与世界书"


def _recipes(*items: dict) -> dict:
    return {str(i["id"]): i for i in items}


def test_同一请求自身相似度为1():
    hit, score = recipe_match.similar_recipe(
        _TASK, _recipes({"id": "r1", "status": "saved", "intent": _TASK}))
    assert hit is not None and hit["id"] == "r1"
    assert score == 1.0


def test_不同任务不命中():
    hit, score = recipe_match.similar_recipe(
        _TASK, _recipes({"id": "r1", "status": "saved", "intent": _OTHER}))
    assert hit is None
    assert score < recipe_match.MATCH_THRESHOLD


def test_草稿配方不参与匹配():
    """草稿未经用户确认：既不该拦截用户走新流程，也不该被硬重放。"""
    hit, _ = recipe_match.similar_recipe(
        _TASK, _recipes({"id": "r1", "status": "draft", "intent": _TASK}))
    assert hit is None


def test_短句不判命中():
    """shingle 太少的短句 Jaccard 极不稳定 → 一律不判（宁可漏问）。"""
    hit, score = recipe_match.similar_recipe(
        "写个文档", _recipes({"id": "r1", "status": "saved", "intent": "写个文档"}))
    assert hit is None
    assert score == 0.0


def test_空清单不命中():
    assert recipe_match.similar_recipe(_TASK, {}) == (None, 0.0)


def test_清单不可用时不拦截对话(monkeypatch):
    from app.services import plan_tasks

    def _boom():
        raise RuntimeError("store down")

    monkeypatch.setattr(plan_tasks, "list_recipes", _boom)
    assert recipe_match.similar_recipe(_TASK) == (None, 0.0)


def test_可固化意图只认交付与自建工具():
    assert recipe_match.is_solidifiable(_TASK) is True
    assert recipe_match.is_solidifiable("写个脚本把目录里的图批量重命名") is True
    # 日常对话、疑问句都不打扰
    assert recipe_match.is_solidifiable("今天心情怎么样，随便聊聊") is False
    assert recipe_match.is_solidifiable("为什么要这样做？") is False
    assert recipe_match.is_solidifiable("") is False


def test_长文本参数判为骨架复用():
    """内容型配方不能硬重放：固化把 content 原样存下，重放只会再写一遍旧内容。"""
    skeleton = {"plan": {"steps": [{"params": {"content": "x" * 300}}]}}
    assert recipe_match.replay_safety(skeleton) == "skeleton"


def test_无长文本参数允许硬重放():
    hard = {"plan": {"steps": [{"params": {"rel_path": "a.md", "template": "t1"}}]}}
    assert recipe_match.replay_safety(hard) == "hard"


def test_指纹同文同值异文异值():
    assert recipe_match.fingerprint(_TASK) == recipe_match.fingerprint(_TASK)
    assert recipe_match.fingerprint(_TASK) != recipe_match.fingerprint(_OTHER)


def test_指纹容忍首尾标点空白():
    """重发时首尾多打空格/句号不该改变指纹（否则会重复打扰）。"""
    assert recipe_match.fingerprint("整理世界书成文档") == \
        recipe_match.fingerprint("  整理世界书成文档。 ")
