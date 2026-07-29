"""触发词字符串的规范化。读写两侧共用，保证存进去和读出来的切分规则一致。"""
from __future__ import annotations

import re

# 显式分隔符：半角/全角逗号、中文顿号、半角/全角分号、换行。
# 不含空格 —— `painterly render style` 是含空格的单个合法触发词。
_EXPLICIT = re.compile(r"[,，、;；\r\n]+")

# 汉字/日文假名区间。用于判断「无显式分隔符的空格串」是否该按空格切。
_CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ]")


def _looks_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def normalize_trigger_words(value: str) -> list[str]:
    """触发词字符串 → 去空去重后的列表（保持原顺序）。

    分隔符收得比「只认半角逗号」宽，因为用户实际会打顿号和全角分号 —— 之前
    `线条动漫、平涂` 会被整条当成一个触发词存下来。

    空格的处理是这里唯一的取舍：`painterly render style` 是一个词，
    `线条动漫 平涂` 是两个。所以空格只在**没有任何显式分隔符**且**片段是 CJK**
    时才当分隔符 —— 中日文触发词里出现空格基本只可能是分隔意图，
    而西文触发词的空格通常是词组内部的。
    """
    if not value:
        return []
    parts = [p.strip() for p in _EXPLICIT.split(value)]
    parts = [p for p in parts if p]
    # 整条只有一个片段、含空白、且是 CJK => 用户大概是用空格分隔的
    if len(parts) == 1 and _looks_cjk(parts[0]):
        by_space = [p for p in parts[0].split() if p]
        if len(by_space) > 1 and all(_looks_cjk(p) for p in by_space):
            parts = by_space
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
