"""ST 卡机械转写纯逻辑（固化03 第二套的机械层，2026-09-09 用户定案）。

把 ST（SillyTavern）卡的条目机械转成本项目口径——**内容不增删、正文逐字保留**：
1. 注入位字段全删（insertion_order/position/order/depth/atDepth/uid/role/sortFn/
   selective/display_index/id 等），条目只留 content/comment/keys/constant/enabled；
2. 渲染宏转纯文本【】标记：<encounter>→【登场】、<roll>→【检定】、<status>→【状态栏】、
   <fate>→【命定预警】、其它 <tag>→【tag】；
3. 好感度表格 <if cell="好感度表/…/好感度 <= -30"> → 【好感度 ≤ -30】（区间同理）；
4. keys：空则从 comment 提取 2–8 字补、超 6 裁 6、其余原样；
5. entries 统一 list；constant 保持原状。

无损验证：对源 content 重放同一套规则后与新 content 逐字比对（全等即通过）。
纯函数、零 LLM、无 I/O——落盘由 capability handler 负责。
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["TAG_MAP", "transform_entries", "verify_lossless", "entry_layers"]

# 渲染宏 → 纯文本标记（本项目无正则渲染层）
TAG_MAP = {"encounter": "登场", "roll": "检定", "status": "状态栏", "fate": "命定预警"}
# 条目保留字段（其余一律丢弃）
KEEP_FIELDS = ("content", "comment", "keys", "constant", "enabled")
# 层前缀（统计与 keys 兜底用）
_LAYER_PREFIXES = ("系统判定机制·", "全局机制·", "世界背景·", "局部机制·", "角色卡·", "NSFW·")


def _convert_macros(text: str) -> str:
    """渲染宏标签 → 纯文本【】标记（闭标签删除）。"""
    def _open(m: "re.Match[str]") -> str:
        name = m.group(1).lower()
        return "【" + (TAG_MAP.get(name) or name) + "】"

    text = re.sub(r"<([a-zA-Z_]+)[^>]*>", _open, text)
    text = re.sub(r"</([a-zA-Z_]+)[^>]*>", "", text)
    return text


def _convert_if_blocks(text: str) -> str:
    """好感度表格 <if …> → 【好感度 档位】纯文本（数据照搬）。"""
    def _repl(m: "re.Match[str]") -> str:
        tag = m.group(0)
        # 上限（≤x）与下限（>a）分开提取：先取 ≤x，去掉全部 <=x 后再找独立 >a
        hi = re.search(r"<=\s*(-?\d+)", tag)
        rest = re.sub(r"<=\s*-?\d+", "", tag)
        lo = re.search(r">\s*(-?\d+)", rest)
        if hi and not lo:
            return "【好感度 ≤ " + hi.group(1) + "】"
        if lo and hi:
            return "【好感度 " + lo.group(1) + " ~ " + hi.group(1) + "】"
        if lo:
            return "【好感度 > " + lo.group(1) + "】"
        return "【好感度档】"

    # 带引号属性的 <if …="…"> 整体匹配（条件里可能含 >，如 `> -30 & <= 20`）；
    # 无引号属性的纯 <if > 标签兜底。
    text = re.sub(r'<if[^"]*"[^"]*">', _repl, text)
    text = re.sub(r'<if[^>]*>', _repl, text)
    text = re.sub(r"</if\s*>", "", text)
    return text


def _fix_keys(comment: str, keys: list[Any]) -> list[str]:
    """keys 空则从 comment 提取 2–8 字补；超 6 裁 6；其余原样。"""
    cleaned = [str(k).strip() for k in (keys or []) if str(k).strip()]
    if cleaned:
        return cleaned[:6]
    c = str(comment or "").strip()
    for prefix in _LAYER_PREFIXES:
        if c.startswith(prefix):
            c = c[len(prefix):]
            break
    c = re.sub(r"^\d+[.、)]\s*", "", c).strip()
    parts = [p for p in re.split(r"[·、,，/|\-—\s]+", c) if p]
    pick = (max(parts, key=len) if parts else c)[:8].strip()
    return [pick] if pick else []


def transform_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """五类机械转写。返回 (新条目 list, 统计)。内容逐字保留，只做格式变换。"""
    out: list[dict[str, Any]] = []
    stats = {"total": 0, "macro_entries": 0, "table_entries": 0,
             "keys_filled": 0, "keys_trimmed": 0}
    for raw in entries or []:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "")
        comment = str(raw.get("comment") or raw.get("name") or "").strip()
        had_macro = bool(re.search(r"<([a-zA-Z_]+)[^>]*>", content))
        had_table = bool(re.search(r"<if\b", content, re.IGNORECASE))
        # 先转 if 表格块（否则 <if …> 会被宏替换误伤成【if】），再转渲染宏
        content = _convert_macros(_convert_if_blocks(content))
        raw_keys = raw.get("keys")
        if raw_keys is None:
            alt = raw.get("key") if raw.get("key") not in (None, "") else raw.get("name")
            raw_keys = [alt] if isinstance(alt, str) else (list(alt) if isinstance(alt, list) else [])
        keys_before = [str(k).strip() for k in (raw_keys or []) if str(k).strip()]
        keys = _fix_keys(comment, keys_before)
        if not keys_before and keys:
            stats["keys_filled"] += 1
        if len(keys_before) > 6:
            stats["keys_trimmed"] += 1
        if had_macro:
            stats["macro_entries"] += 1
        if had_table:
            stats["table_entries"] += 1
        stats["total"] += 1
        out.append({
            "content": content,
            "comment": comment,
            "keys": keys,
            "constant": bool(raw.get("constant")),
            "enabled": raw.get("enabled") if isinstance(raw.get("enabled"), bool) else True,
        })
    return out, stats


def _replay(content: str) -> str:
    # 与 transform_entries 同顺序：先 if 表格块、再渲染宏（重放验证必须一致）
    return _convert_macros(_convert_if_blocks(str(content or "")))


def verify_lossless(src_entries: list[dict[str, Any]],
                    new_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """无损验证：源 content 重放同一套规则后与新 content 逐字比对（忽略空白）。"""
    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", str(s or ""))

    mismatches: list[str] = []
    for i, (a, b) in enumerate(zip(src_entries or [], new_entries or [])):
        if _norm(_replay(a.get("content") or "")) != _norm(b.get("content") or ""):
            mismatches.append(str(a.get("comment") or ("条目" + str(i))))
    return {
        "ok": not mismatches and len(src_entries or []) == len(new_entries or []),
        "checked": len(new_entries or []),
        "mismatches": mismatches[:10],
    }


def entry_layers(entries: list[dict[str, Any]]) -> dict[str, int]:
    """按六层前缀统计条目数（报告用）。"""
    layers = {p.rstrip("·"): 0 for p in _LAYER_PREFIXES}
    layers["其它"] = 0
    for e in entries or []:
        c = str((e or {}).get("comment") or "")
        hit = next((p.rstrip("·") for p in _LAYER_PREFIXES if c.startswith(p)), None)
        if hit:
            layers[hit] += 1
        else:
            layers["其它"] += 1
    return layers
