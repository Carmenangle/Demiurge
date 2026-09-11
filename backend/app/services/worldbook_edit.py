"""世界书条目级增删改（纯逻辑，作用于 book dict）。⑤ 可视化 CRUD 的编辑内核。

worldbook.py 只读解析用于检索；本模块负责写回单条条目。两处世界书都用同一格式：
- 独立世界书：worldbookDir/<name>.json（属主 worldbook_store）
- 卡内嵌世界书：characterDir/<card>/worldbook.json（属主 character_store）

entries 容器兼容两种格式（ST 对象 keyed-by-uid / V2 卡数组）；本模块**保留原容器类型**写回，
避免再导入 SillyTavern 时格式漂移。条目按「有序序列里的下标」定位（dict 保持插入序，JSON 往返稳定）。

I/O 交给调用方（路由读文件→改 dict→写文件），本模块不碰磁盘，可纯单测。
"""
from __future__ import annotations

from typing import Any

# 条目的编辑字段（前端表单 ↔ ST 条目）。其余原字段（order/位置/概率等）原样保留。
_EDIT_FIELDS = ("content", "comment", "keys", "constant", "enabled")


def _ordered(book: dict[str, Any]) -> list[Any]:
    """按有序序列取 entries 的值列表（dict 取 values，list 直接用）。"""
    raw = book.get("entries")
    if isinstance(raw, dict):
        return list(raw.values())
    if isinstance(raw, list):
        return list(raw)
    return []


def list_entries(book: dict[str, Any]) -> list[dict[str, Any]]:
    """返回条目列表（含 index，供前端定位）。非 dict 条目跳过。"""
    out: list[dict[str, Any]] = []
    for i, e in enumerate(_ordered(book)):
        if not isinstance(e, dict):
            continue
        keys = e.get("keys") or e.get("key") or []
        out.append({
            "index": i,
            "content": str(e.get("content") or ""),
            "comment": str(e.get("comment") or ""),
            "keys": [str(k) for k in keys] if isinstance(keys, list) else [],
            "constant": bool(e.get("constant")),
            # enabled 缺省 True；disable=True 明确关闭（与 worldbook.parse_entries 一致）
            "enabled": not (e.get("enabled") is False or e.get("disable") is True),
        })
    return out


def _normalize_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """把前端字段归一到 ST 条目字段（keys 转字符串列表，enabled 落到 enabled + disable 双写兼容）。"""
    out: dict[str, Any] = {}
    if "content" in patch:
        out["content"] = str(patch["content"] or "")
    if "comment" in patch:
        out["comment"] = str(patch["comment"] or "")
    if "keys" in patch:
        ks = patch["keys"]
        out["keys"] = [str(k).strip() for k in ks if str(k).strip()] if isinstance(ks, list) else []
    if "constant" in patch:
        out["constant"] = bool(patch["constant"])
    if "enabled" in patch:
        en = bool(patch["enabled"])
        out["enabled"] = en
        out["disable"] = not en   # 双写：兼容按 disable 判定的读取方
    return out


def add_entry(book: dict[str, Any], patch: dict[str, Any]) -> int:
    """新增一条条目，返回其新 index。保留原容器类型（dict→新数字键；list→append）。"""
    entry = {"content": "", "comment": "", "keys": [], "constant": False, "enabled": True}
    entry.update(_normalize_patch(patch))
    raw = book.get("entries")
    if isinstance(raw, dict):
        # ST uid 键：取现有数字键最大值+1
        nums = [int(k) for k in raw.keys() if str(k).lstrip("-").isdigit()]
        uid = (max(nums) + 1) if nums else 0
        entry.setdefault("uid", uid)
        raw[str(uid)] = entry
        return len(raw) - 1
    if isinstance(raw, list):
        raw.append(entry)
        return len(raw) - 1
    book["entries"] = [entry]
    return 0


def _entry_at(book: dict[str, Any], index: int) -> dict[str, Any] | None:
    seq = _ordered(book)
    if 0 <= index < len(seq) and isinstance(seq[index], dict):
        return seq[index]
    return None


def update_entry(book: dict[str, Any], index: int, patch: dict[str, Any]) -> bool:
    """就地更新第 index 条条目的可编辑字段。越界/非 dict → False。"""
    target = _entry_at(book, index)
    if target is None:
        return False
    target.update(_normalize_patch(patch))
    return True


def delete_entry(book: dict[str, Any], index: int) -> bool:
    """删除第 index 条条目（dict 删对应键，list 删对应位）。越界 → False。"""
    raw = book.get("entries")
    if isinstance(raw, dict):
        keys = list(raw.keys())
        if 0 <= index < len(keys):
            del raw[keys[index]]
            return True
        return False
    if isinstance(raw, list):
        if 0 <= index < len(raw):
            raw.pop(index)
            return True
    return False
