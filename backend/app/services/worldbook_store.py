"""独立世界书落盘：每本世界书 = worldbookDir 下的一个 <安全名>.json（ST 世界书格式）。

区别于 character_store 的卡内嵌世界书（那随卡落在卡文件夹）：这里是用户单独导入的、
可跨卡复用的独立世界书，落在设置里的「世界书文件夹」(worldbookDir)。

- 名称取自上传文件名（ST 世界书 JSON 内部无 name 字段）。
- 同名冲突：save 时目标已存在且 overwrite=False → 抛 FileExistsError（调用方决定覆盖）。
- 卡内嵌世界书导入若与已存独立世界书同名，也复用同一冲突语义（见 routers）。
- 同步：把**卡目录**的 worldbook.json 覆盖进独立库（`sync_card_worldbook`，旧版自动备份）——
  事务（读设置 → 校验 → mkdir → 备份 → 覆盖）的唯一属主，路由只做 HTTP 翻译（2026-09-15 P8）。

只做文件读写；条目解析/检索是 worldbook.py 的事，格式校验在导入路由。
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import re
import shutil
import time

from app.services import repo_meta
from app.services.pathnames import safe_dir, safe_seg

REPO_WORLDBOOK_FILE = "worldbook.json"


@dataclass
class WorldbookSummary:
    name: str
    file: str
    entries: int


def _path(base: str, name: str) -> Path:
    # safe_dir 保留中文可读性（世界书名常为中文），只挡 Windows 非法字符，与 character_store 一致
    return Path(base) / f"{safe_dir(name)}.json"


def exists(base: str, name: str) -> bool:
    return _path(base, name).is_file()


def _entry_count(book: dict[str, Any]) -> int:
    raw = book.get("entries")
    if isinstance(raw, dict):
        return len(raw)
    if isinstance(raw, list):
        return len(raw)
    return 0


def save(base: str, name: str, book: dict[str, Any], *, overwrite: bool = False) -> WorldbookSummary:
    """把世界书 JSON 写入 <base>/<安全名>.json。同名且 overwrite=False → FileExistsError。"""
    if not base:
        raise ValueError("未设置世界书文件夹路径")
    p = _path(base, name)
    if p.is_file() and not overwrite:
        raise FileExistsError(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return WorldbookSummary(name=name, file=p.name, entries=_entry_count(book))


def list_books(base: str) -> list[WorldbookSummary]:
    root = Path(base)
    if not root.is_dir():
        return []
    out: list[WorldbookSummary] = []
    for child in sorted(root.glob("*.json")):
        try:
            book = json.loads(child.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(book, dict):
            continue
        out.append(WorldbookSummary(name=child.stem, file=child.name, entries=_entry_count(book)))
    return out


def read_book(base: str, name: str) -> dict[str, Any] | None:
    p = _path(base, name)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def delete_book(base: str, name: str) -> bool:
    p = _path(base, name)
    if not p.is_file():
        return False
    p.unlink()
    return not p.is_file()


class WorldbookSyncError(Exception):
    """同步到独立世界书失败的**结果语义**（status + detail 供路由转 HTTPException）。

    与 `collection_artifacts.ArtifactAccessError` 同口径：错误码与中文文案由 service 定、
    路由只做 HTTP 翻译 —— 否则「事务」与「文案」又会裂成两个属主（2026-09-15 P8）。
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class WorldbookSyncResult:
    """同步结果：落点、条目数、本次**真的写出**的备份（无旧版 → None）。"""

    dest: Path
    entries: int
    backup: Path | None = None


def sync_card_worldbook(card_dir: Path) -> WorldbookSyncResult:
    """把**卡目录**的 `worldbook.json` 覆盖同步进独立世界书库（`worldbookDir/<卡名>.json`）。

    2026-09-15 审计 P8：这段事务原先整个摆在 `routers/artifacts.py:294-338` —— 手写
    `json.loads(DATA_DIR / "user_state.json")` 读 `worldbookDir`（**绕过属主**
    `repo_meta.setting_dir_from_state`）、`mkdir`、`copy2` 备份、`copy2` 覆盖、条目计数、
    错误映射，还夹带 `json`/`shutil`/`time`/`DATA_DIR` 四处函数内局部导入。收进本函数后
    事务只有一个属主，路由只剩「域解析 → 调用 → 转 HTTP」。

    **逐字保留**（用户可见，不得抹平）：备份名 `<卡名>.json.bak-<int(time.time())>`、
    400「未配置世界书文件夹（worldbookDir），无法同步」、400「无法从产物路径解析卡目录名」、
    404「卡目录缺少 worldbook.json：<绝对路径>」、500「同步失败：<原因>」
    （含「worldbook.json 缺少 entries」这条业务校验）。

    校验**顺序**同样是契约：配置 → 卡名 → 源文件存在 → 事务 ⇒ 前三级失败**绝不落盘**。
    ⚠ 目标文件名用**卡目录名原文**、不经 `safe_dir` 清洗（与 `save()` 的 `<安全名>.json`
    刻意不同口径）：存量文件名按原文存在，改清洗会让它们对不上。
    """
    wb_dir = repo_meta.setting_dir_from_state("worldbookDir")
    if not wb_dir:
        raise WorldbookSyncError(400, "未配置世界书文件夹（worldbookDir），无法同步")
    card_name = str(card_dir.name or "").strip()
    if not card_name or card_name in (".", ".."):
        raise WorldbookSyncError(400, "无法从产物路径解析卡目录名")
    src = card_dir / REPO_WORLDBOOK_FILE
    if not src.is_file():
        raise WorldbookSyncError(404, f"卡目录缺少 worldbook.json：{src}")
    try:
        book = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(book.get("entries"), (list, dict)):
            raise ValueError("worldbook.json 缺少 entries")
        dst = Path(wb_dir) / f"{card_name}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        if dst.is_file():
            backup = dst.with_name(f"{card_name}.json.bak-{int(time.time())}")
            shutil.copy2(dst, backup)
        shutil.copy2(str(src), str(dst))
        return WorldbookSyncResult(dest=dst, entries=len(book.get("entries") or []), backup=backup)
    except Exception as exc:  # noqa: BLE001 - 统一转「同步失败：<原因>」
        raise WorldbookSyncError(500, f"同步失败：{exc}") from exc


def mutate_entries(
    load: Callable[[], dict[str, Any]],
    save: Callable[[dict[str, Any]], Any],
    apply: Callable[[dict[str, Any]], tuple[Any, bool]],
) -> tuple[Any, bool]:
    """条目级 CRUD 的**唯一事务接缝**：读 → 改一条 → **有变化才写回**。

    2026-09-15 审计 P7：同一形状此前有两套写法——`routers/worldbook.py` 用
    `_read_book`/`_write_book` helper，`routers/narrative.py` 三个 repo 快照端点**内联**
    手写「读 → `worldbook_edit` 改 → 写回」。落盘判定因此有两个属主。收进本接缝后，
    「无变化不落盘」（越界更新/删除因此天然不写盘）只有这一个属主。

    **只收这一条共同不变量**；定位与失败语义留在调用方——独立世界书 / 卡内嵌 /
    仓库快照三种定位的 400·404 文案本就各不相同（见两个 routers），刻意不统一。

    - ``load()`` → book。失败自行 raise，接缝只透传。
    - ``apply(book)`` → ``(result, changed)``。**显式布尔是刻意的**：新增条目返回的
      index 可以是 ``0``，用真值判定会把「首条新增」误判成没变化而不落盘。
    - ``changed`` 为真才 ``save(book)``；``(result, changed)`` 回给调用方复用
      （add 的 index、update/delete 的越界 404）。
    """
    book = load()
    result, changed = apply(book)
    if changed:
        save(book)
    return result, changed


def repo_snapshot_path(base: str, repo_id: str) -> Path:
    """当前小仓库的世界书快照，与 state/tables 一样按 repo_id 物理隔离。"""
    return Path(base) / safe_seg(repo_id, strip=False) / REPO_WORLDBOOK_FILE


def read_repo_snapshot(base: str, repo_id: str) -> dict[str, Any] | None:
    if not (base and repo_id):
        return None
    p = repo_snapshot_path(base, repo_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_repo_snapshot(base: str, repo_id: str, book: dict[str, Any]) -> bool:
    """把 worldbook dict 写回小仓库快照文件。快照不存在 → False。"""
    if not (base and repo_id):
        return False
    p = repo_snapshot_path(base, repo_id)
    if not p.parent.exists():
        return False
    p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _raw_entries(book: dict[str, Any]) -> list[dict[str, Any]]:
    raw = book.get("entries")
    values = raw.values() if isinstance(raw, dict) else raw
    return [deepcopy(item) for item in (values or []) if isinstance(item, dict)]


def ensure_repo_snapshot(base: str, repo_id: str,
                         source_books: list[dict[str, Any]]) -> dict[str, Any] | None:
    """首次使用时合并来源并快照；已有快照永不被源库覆盖。"""
    existing = read_repo_snapshot(base, repo_id)
    if existing is not None:
        return existing
    entries: list[dict[str, Any]] = []
    for book in source_books:
        if isinstance(book, dict):
            entries.extend(_raw_entries(book))
    if not source_books or not (base and repo_id):
        return None
    snapshot = {"entries": entries}
    p = repo_snapshot_path(base, repo_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return deepcopy(snapshot)


def _entry_matches(item: dict[str, Any], query: str) -> bool:
    if not query:
        return False
    terms = [str(item.get("comment") or "").strip()]
    keys = item.get("keys") or item.get("key") or []
    if isinstance(keys, str):
        keys = [keys]
    terms.extend(str(term).strip() for term in keys if str(term).strip())
    return any(term and term in query for term in terms)


def repo_snapshot_context(base: str, repo_id: str, *, query: str = "",
                          max_chars: int = 20_000,
                          allowed_indices: set[int] | frozenset[int] | None = None) -> str:
    """给 Curator 的带稳定 index 条目视图；限制长度避免重复灌入整本大书。"""
    book = read_repo_snapshot(base, repo_id)
    if not book:
        return ""
    from app.services import worldbook_edit
    indexed = worldbook_edit.list_entries(book)
    if allowed_indices is not None:
        indexed = [item for item in indexed if item["index"] in allowed_indices]
    if query:
        indexed.sort(key=lambda item: not _entry_matches(item, query))
    selected: list[dict[str, Any]] = []
    for item in indexed:
        candidate = json.dumps([*selected, item], ensure_ascii=False, separators=(",", ":"))
        if len(candidate) > max_chars:
            if not selected:
                clipped = dict(item)
                clipped["content"] = str(clipped.get("content") or "")[:max(0, max_chars - 256)]
                selected.append(clipped)
            break
        selected.append(item)
    return json.dumps(selected, ensure_ascii=False, separators=(",", ":"))


def repo_visual_profiles(base: str, repo_id: str, query: str, *, max_chars: int = 4_000) -> str:
    """提取本轮命中角色的稳定视觉锚点，供主模型与当前状态合并。"""
    book = read_repo_snapshot(base, repo_id)
    if not book or not query:
        return ""
    from app.services import worldbook_edit
    profiles: list[str] = []
    for item in worldbook_edit.list_entries(book):
        content = str(item.get("content") or "")
        comment = str(item.get("comment") or "")
        if not _entry_matches(item, query) or not ("角色卡·" in comment or "【角色卡·" in content):
            continue
        name = comment.split("角色卡·", 1)[-1].strip() or "角色"
        anchors = re.findall(r"^【(?:外貌|身材|穿着)】[^\r\n]*", content, flags=re.M)
        if not anchors:
            continue
        candidate = f"{name}：" + "；".join(anchors)
        if sum(len(value) for value in profiles) + len(candidate) > max_chars:
            break
        profiles.append(candidate)
    return "\n".join(profiles)


def _merge_character_dynamic(existing: str, update: str) -> str:
    """保留原文底座 + 替换/追加末尾唯一的【剧情进展·动态】区。

    与「角色卡」无关，对所有世界书条目通用（2026-09-12 起 apply_repo_ops 统一走这里）。
    关键性质：底座逐字节不变，只让**末尾**动态区变化——上游前缀缓存的复用点因此位于
    条目末尾而非条目起始。
    """
    marker = "【剧情进展·动态】"
    base = existing.split(marker, 1)[0].rstrip()
    dynamic = update.split(marker, 1)[-1].strip()
    return f"{base}\n\n{marker}\n{dynamic}"


def apply_repo_ops(base: str, repo_id: str, ops: list[dict[str, Any]], *,
                   allowed_update_indices: set[int] | frozenset[int] | None = None,
                   rejections: list[dict[str, Any]] | None = None) -> int:
    """对当前小仓库快照执行 Curator 增改；更新需 evidence，删除类 op 一律拒绝。

    **所有条目**的 worldbook_update 都只允许按剧情进度改正文末尾的动态段
    （_merge_character_dynamic：保留原文底座 + 替换/追加唯一的【剧情进展·动态】区）；
    身份键（comment/keys/constant/enabled）不随 ops 变更并上报 rejections，理由是改名/
    改键会让 repo_visual_profiles 的「角色卡·」+【外貌】锚识别失配断粮（2026-08-31 实锤：
    舞姬恋↔舞柔条目连不上 → 空泛提示词）。

    ⚠ 2026-09-12 修：底座保护原先只对「comment 含『角色卡·』」的条目生效，非角色卡条目走
    `patch = {"content": text}` **整体覆盖**，导致 curator 一次 update 就把机制/设定/地点条目
    的正文顶掉——实测「全局机制·世界自转与事件登门」（constant，1,659 字符，自述「本卡最高级
    驱动机制」）被剧情快照覆盖，原文丢失；且因世界书稳定序列按内容 hash 解析（worldbook.py），
    该槽位整条塌陷、其后条目全部左移，跨轮前缀只剩 9.0%。现口径与角色卡统一。

    拒绝明细写入 rejections（调用方负责 trace），不抛错不中断整批。
    """
    book = read_repo_snapshot(base, repo_id)
    if not book:
        return 0
    from app.services import worldbook_edit
    applied = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "").strip()
        if kind in {"worldbook_delete", "delete", "worldbook_remove", "remove"}:
            if rejections is not None:
                record: dict[str, Any] = {"op": kind, "reason": "delete_forbidden"}
                if op.get("index") is not None:
                    record["index"] = op.get("index")
                rejections.append(record)
            continue
        text = str(op.get("text") or "").strip()
        patch: dict[str, Any] = {"content": text}
        if "title" in op:
            patch["comment"] = str(op.get("title") or "").strip()
        if "keys" in op and isinstance(op.get("keys"), list):
            patch["keys"] = op["keys"]
        if "constant" in op:
            patch["constant"] = bool(op["constant"])
        if "enabled" in op:
            patch["enabled"] = bool(op["enabled"])
        if kind == "worldbook_add" and text:
            worldbook_edit.add_entry(book, patch)
            applied += 1
        elif kind == "worldbook_update" and text and str(op.get("evidence") or "").strip():
            try:
                index = int(op.get("index"))
            except (TypeError, ValueError):
                continue
            if allowed_update_indices is not None and index not in allowed_update_indices:
                continue
            entries = worldbook_edit.list_entries(book)
            if 0 <= index < len(entries):
                current = entries[index]
                content = str(current.get("content") or "")
                # 2026-09-12：底座保护对所有条目生效（原先只对「角色卡·」生效）。
                # 统一口径＝原文底座 + 末尾唯一【剧情进展·动态】区，身份键一律不改。
                merged = _merge_character_dynamic(content, text)
                stripped = sorted(key for key in patch if key != "content")
                patch = {"content": merged}
                if rejections is not None and stripped:
                    rejections.append({
                        "op": kind, "index": index,
                        "reason": "identity_keys_forbidden",
                        "fields": stripped,
                    })
            if worldbook_edit.update_entry(book, index, patch):
                applied += 1
    if applied:
        p = repo_snapshot_path(base, repo_id)
        p.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    return applied
