"""S2 活人感通审单测：采样闸门 / 结构化判定落 Narrative CI / 失败静默降级。"""
from __future__ import annotations

import json

from app.services import narrative_ci, prose_style, style_review

LONG_BODY = ("雨下到后半夜，檐水连成了线。她把伞往他那边偏了偏，自己半边肩膀很快洇湿。"
             "「前面就是渡口。」她说。他没有应声，只把包袱又往上提了提。"
             "渡口的老艄公披着蓑衣打盹，船板被雨水泡得发黑，踩上去咯吱作响。"
             "她先跳上船，回头伸手拉他，掌心冰凉，却握得很稳。"
             "船离岸时，艄公醒了，看了他们一眼，什么也没问，只把橹摇得更慢了些。")


def test_采样闸门():
    cfg = {"enabled": True, "review_every": 5}
    assert not style_review.should_review(cfg, turn=1, text_len=len(LONG_BODY))   # 未到轮
    assert style_review.should_review(cfg, turn=5, text_len=len(LONG_BODY))
    assert not style_review.should_review(cfg, turn=5, text_len=10)               # 太短
    assert not style_review.should_review(cfg | {"review_every": 0}, turn=5, text_len=len(LONG_BODY))
    assert not style_review.should_review(cfg | {"enabled": False}, turn=5, text_len=len(LONG_BODY))


def test_到轮时调LLM并落诊断(tmp_path):
    calls = []

    def chat_fn(*args, **kwargs):
        calls.append(args)
        return json.dumps({
            "alive_score": 82,
            "opening_specificity": "直接进雨景与动作，无套路开场",
            "rhythm": "长短句交错",
            "colloquial": "对白自然",
            "detail_support": "船板发黑、掌心冰凉等有具体细节",
            "summary": "整体像人写的，无重大问题。",
        }, ensure_ascii=False)

    called = style_review.maybe_review(
        cfg={"enabled": True, "review_every": 5}, text=LONG_BODY, turn=10,
        output_dir=str(tmp_path), repo_id="work",
        chat_base="", chat_key="", chat_model="", chat_fn=chat_fn)
    assert called and len(calls) == 1

    items = narrative_ci.list_diagnostics(str(tmp_path), "work")
    living = [i for i in items if i["code"] == prose_style.CODE_STYLE_LIVING_REVIEW]
    assert len(living) == 1
    assert "82/100" in living[0]["message"]
    assert living[0]["severity"] == "info"
    assert living[0]["status"] == "open"


def test_LLM失败静默降级不落盘(tmp_path):
    def broken(*args, **kwargs):
        raise RuntimeError("上游 502")

    called = style_review.maybe_review(
        cfg={"enabled": True, "review_every": 5}, text=LONG_BODY, turn=5,
        output_dir=str(tmp_path), repo_id="work",
        chat_base="", chat_key="", chat_model="", chat_fn=broken)
    assert called  # 调了但失败
    assert narrative_ci.list_diagnostics(str(tmp_path), "work") == []


def test_未到轮不调LLM(tmp_path):
    calls = []

    def chat_fn(*args, **kwargs):
        calls.append(args)
        return "{}"

    called = style_review.maybe_review(
        cfg={"enabled": True, "review_every": 5}, text=LONG_BODY, turn=3,
        output_dir=str(tmp_path), repo_id="work",
        chat_base="", chat_key="", chat_model="", chat_fn=chat_fn)
    assert not called and not calls
