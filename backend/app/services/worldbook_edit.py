"""世界书条目级增删改（纯逻辑，作用于 book dict）。⑤ 可视化 CRUD 的编辑内核。

worldbook.py 只读解析用于检索；本模块负责写回单条条目。两处世界书都用同一格式：
- 独立世界书：worldbookDir/<name>.json（属主 worldbook_store）
- 卡内嵌世界书：characterDir/<card>/worldbook.json（属主 character_store）

entries 容器兼容两种格式（ST 对象 keyed-by-uid / V2 卡数组）；本模块**保留原容器类型**写回，
避免再导入 SillyTavern 时格式漂移。条目按「有序序列里的下标」定位（dict 保持插入序，JSON 往返稳定）。

I/O 交给调用方（本模块不碰磁盘，可纯单测）；调用方侧的「读 → 改 → 写回」编排统一走
`worldbook_store.mutate_entries(load, save, apply)`（2026-09-15 审计 P7 收口），本模块保持纯逻辑。
"""
from __future__ import annotations

import re
from typing import Any

# 条目的编辑字段（前端表单 ↔ ST 条目）。其余原字段（order/位置/概率等）原样保留。
_EDIT_FIELDS = ("content", "comment", "keys", "constant", "enabled")

# ── 新增写入闸：重复检测（2026-10-22 L1）────────────────────────────────────
# 判据经真实世界书（堕落魔戒 69 条）校准：
#   ① 内容归一化后完全相同 —— 逐字重复；
#   ② 归一化共同前缀 ≥ DUP_PREFIX_CHARS —— 同开头改写（韦尔纳真案例 16 字；合法对 0~6 字；
#      全库仅 2 对 ≥12，其中 12 字那对是边界假阳性 ⇒ 阈值 14）；
#   ③ comment 归一化后同名 —— 同标题重加。
# ⛔ 不用字符相似度（difflib）：校准实测真重复对 0.239 < 合法机制对 0.272，判据反序，已否决。
# ⛔ 不用 keys / keys[0] 判重：角色卡与其事件条目共享身份 key 是设计常态。
DUP_PREFIX_CHARS = 14
# 共同前缀里至少要有这么多个**不同字符**才算重复：防退化填充内容误判（测试夹具常用
# `"密"*2300` 这类同字重复，两条的前缀会退化成几百个「密」；真实叙述文本的前缀字符多样）。
DUP_PREFIX_MIN_DISTINCT = 10

# comment 归一化口径与 `capability_handlers._norm_comment_key` 一致（去空白/标点/全半角漂移）。
_NORM_STRIP = re.compile(r"[\s·、。，．,．:：()（）\[\]【】\-—/\\]+")


class DuplicateEntryError(ValueError):
    """新增条目与既有条目重复（调用方转成可读拒绝：UI 409 / curator rejections / 导入 skipped）。

    reason：same_content（内容归一化后相同）/ same_opening（共同前缀 ≥ DUP_PREFIX_CHARS）/
    same_title（comment 归一化后同名）。existing_index 供调用方给出「改为编辑该条」的指引。
    """

    def __init__(self, reason: str, existing_index: int, existing_comment: str, *,
                 prefix_chars: int = 0) -> None:
        self.reason = reason
        self.existing_index = existing_index
        self.existing_comment = existing_comment
        self.prefix_chars = prefix_chars
        label = {
            "same_content": "内容完全相同",
            "same_opening": f"开头 {prefix_chars} 字相同",
            "same_title": "标题相同",
        }.get(reason, reason)
        super().__init__(
            f"已存在同主题条目「{existing_comment or '(无标题)'}」（{label}），"
            f"请改为编辑该条（index={existing_index}），不要新增")


def _norm_text(value: Any) -> str:
    """内容归一：只去空白（保留标点，避免「甲。乙」与「甲乙」被误判相同）。"""
    return "".join(str(value or "").split())


def _norm_comment(value: Any) -> str:
    return _NORM_STRIP.sub("", str(value or ""))


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def find_duplicate(book: dict[str, Any], patch: dict[str, Any]) -> DuplicateEntryError | None:
    """新增前的重复检测（纯逻辑，无 I/O）。命中返回异常实例（调用方 raise），否则 None。

    空内容不参与判重：表单初建的空条目合法（内容填完前无从判重）。
    """
    content = _norm_text(patch.get("content"))
    if not content:
        return None
    comment = _norm_comment(patch.get("comment"))
    for index, entry in enumerate(_ordered(book)):
        if not isinstance(entry, dict):
            continue
        existing = _norm_text(entry.get("content"))
        if not existing:
            continue
        existing_comment = str(entry.get("comment") or "")
        if existing == content:
            return DuplicateEntryError("same_content", index, existing_comment)
        prefix = _common_prefix(content, existing)
        if prefix >= DUP_PREFIX_CHARS and len(set(content[:prefix])) >= DUP_PREFIX_MIN_DISTINCT:
            return DuplicateEntryError("same_opening", index, existing_comment,
                                       prefix_chars=prefix)
        if comment and comment == _norm_comment(existing_comment):
            return DuplicateEntryError("same_title", index, existing_comment)
    return None


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
    """新增一条条目，返回其新 index。保留原容器类型（dict→新数字键；list→append）。

    写入闸（2026-10-22 L1）：与既有条目重复时抛 `DuplicateEntryError`，由调用方转成
    可读拒绝（UI 409 / `apply_repo_ops` 的 rejections / 导入 skipped+note）。
    """
    duplicate = find_duplicate(book, patch)
    if duplicate is not None:
        raise duplicate
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
