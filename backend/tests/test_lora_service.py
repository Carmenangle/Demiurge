"""触发词字符串的切分规则。

背景：用户填 `线条动漫、平涂` 时只存下了一个词 —— 原实现只认半角逗号。
"""
import pytest

from app.services.lora_service import normalize_trigger_words as n


@pytest.mark.parametrize("raw,want", [
    ("线条动漫、平涂", ["线条动漫", "平涂"]),      # 中文顿号
    ("线条动漫，平涂", ["线条动漫", "平涂"]),      # 全角逗号
    ("线条动漫,平涂", ["线条动漫", "平涂"]),
    ("线条动漫;平涂", ["线条动漫", "平涂"]),
    ("线条动漫；平涂", ["线条动漫", "平涂"]),
    ("线条动漫\n平涂", ["线条动漫", "平涂"]),
    ("线条动漫 平涂", ["线条动漫", "平涂"]),       # CJK 用空格分隔
    ("a, b、c;d", ["a", "b", "c", "d"]),          # 混用
])
def test_splits_on_all_separators(raw, want):
    assert n(raw) == want


def test_western_phrase_with_spaces_stays_one_word():
    """关键回归：`painterly render style` 是一个合法触发词，不能按空格切开。"""
    assert n("painterly render style") == ["painterly render style"]
    assert n("Ogipote style") == ["Ogipote style"]


def test_western_phrases_split_on_comma_only():
    assert n("Ogipote style, painterly render style") == [
        "Ogipote style", "painterly render style"]


def test_dedupes_case_insensitively_keeping_first():
    assert n("Cat, cat, CAT") == ["Cat"]


def test_strips_and_drops_empties():
    assert n("  a ,, b  ,") == ["a", "b"]


@pytest.mark.parametrize("raw", ["", "   ", ",,,", "、；"])
def test_empty_inputs(raw):
    assert n(raw) == []


def test_single_word_untouched():
    assert n("c0lorl1nes") == ["c0lorl1nes"]
