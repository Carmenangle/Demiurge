"""条目↔章节覆盖台账（固化02 §3.9 增量链第三步，2026-10-20 用户定案）。

**要解决的问题**：`novel.diff` 只说「第 61-63 章是新增」，但模型还要知道
**「这几章的变化影响哪几条世界书条目」**——没有这条反查，它只能把 40+ 条条目
连同旧世界书全文再读一遍，前面省的 token 又还回去了。

**为什么台账必须是独立文件、不能塞进条目**：条目有效字段是五字段闭集
（`content/comment/keys/constant/enabled`，由 worldbook.upsert_repo 参数校验强制），
加 `source_chapters` 会破契约并可能被下游当脏字段清洗。故溯源外置：
`<作品>/_prep/coverage.json`（与 `novel.index.json`、`novel.scope.json` 同目录）。

**记账时机**：`worldbook.upsert_repo` 成功写入后，按 `source_chapters` 把
「这条条目读了哪几章」记进台账（机械、零 LLM）。同一 comment 多次更新按并集累积。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

LEDGER_VERSION = 1
LEDGER_FILE = "coverage.json"
_PREP_DIR = "_prep"


def ledger_path(work_dir: str) -> Path:
    return Path(str(work_dir)) / _PREP_DIR / LEDGER_FILE


def read_ledger(work_dir: str) -> dict[str, Any]:
    """读台账；不存在/损坏返回空台账（台账是增益信息，读不到不阻断任务）。"""
    empty: dict[str, Any] = {"version": LEDGER_VERSION, "entries": {}}
    if not str(work_dir or "").strip():
        return empty
    p = ledger_path(work_dir)
    if not p.is_file():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return empty
    data.setdefault("version", LEDGER_VERSION)
    return data


def record(work_dir: str, applied: list[dict[str, Any]],
           chapters: list[int] | None = None) -> dict[str, Any]:
    """把「本条条目依据哪几章写成」并入台账。

    applied: [{comment, chars}]（只传**真正写入成功**的条目，别把 skipped 记成已覆盖）
    chapters: 本轮读过的章节序号；空 → 不改章节（只刷新字数）
    同一 comment 章节取并集（条目仍派生自此前的章节，不能因为重写就丢）。
    """
    if not str(work_dir or "").strip():
        return {"path": "", "recorded": 0}
    chs = sorted({int(c) for c in (chapters or [])})
    if not applied:
        return {"path": str(ledger_path(work_dir)), "recorded": 0}
    data = read_ledger(work_dir)
    entries: dict[str, Any] = data["entries"]
    now = time.time()
    recorded = 0
    for item in applied:
        comment = str(item.get("comment") or "").strip()
        if not comment:
            continue
        _prev_raw = entries.get(comment)
        prev: dict[str, Any] = _prev_raw if isinstance(_prev_raw, dict) else {}
        _prev_chs = prev.get("chapters")
        _prev_list = _prev_chs if isinstance(_prev_chs, list) else []
        merged = sorted(set(int(c) for c in _prev_list) | set(chs))
        entries[comment] = {
            "chapters": merged,
            "chars": int(item.get("chars") or 0) or int(prev.get("chars") or 0),
            "updated_at": now,
        }
        recorded += 1
    data["version"] = LEDGER_VERSION
    p = ledger_path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(p), "recorded": recorded, "chapters": chs}


def drop(work_dir: str, comments: list[str]) -> int:
    """条目被删除时同步清台账（防「已删条目仍被当成受影响条目」）。"""
    if not str(work_dir or "").strip() or not comments:
        return 0
    data = read_ledger(work_dir)
    entries: dict[str, Any] = data["entries"]
    removed = 0
    for c in comments:
        if c in entries:
            entries.pop(c, None)
            removed += 1
    if removed:
        p = ledger_path(work_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def report(ledger: dict[str, Any], snapshot_comments: list[str],
           chapters: list[int] | None = None) -> dict[str, Any]:
    """纯函数：台账 × 快照 × 本次变更章节 → 受影响条目清单。

    - affected：台账章节与 `chapters` 有交集的条目（= 本次真正要改的条目）；
    - uncovered：`chapters` 里没有任何条目覆盖的章节（= 提示要新增条目）；
    - unregistered：快照里有、台账里没有的条目（= 台账上线前就存在的条目，
      没来源信息，修订前只能按章节标题人工定位）；
    - stale：台账里有、快照里没有（条目已删，台账未清）。
    """
    _raw_entries = ledger.get("entries")
    entries: dict[str, Any] = _raw_entries if isinstance(_raw_entries, dict) else {}
    want = sorted({int(c) for c in (chapters or [])})
    want_set = set(want)
    snap = [str(c) for c in (snapshot_comments or [])]
    snap_set = set(snap)

    affected: list[dict[str, Any]] = []
    covered: set[int] = set()
    for comment, info in entries.items():
        if comment not in snap_set:
            continue
        row: dict[str, Any] = info if isinstance(info, dict) else {}
        _chs = row.get("chapters")
        chs = sorted({int(c) for c in (_chs if isinstance(_chs, list) else [])})
        covered |= set(chs)
        if want_set and want_set & set(chs):
            affected.append({"comment": comment, "chapters": chs,
                             "chars": int(row.get("chars") or 0)})
    affected.sort(key=lambda x: (-len(want_set & set(x["chapters"])), x["comment"]))
    unregistered = [c for c in snap if c not in entries]
    stale = [c for c in entries if c not in snap_set]
    return {
        "chapters": want,
        "affected": affected,
        "uncovered_chapters": [c for c in want if c not in covered] if want else [],
        "unregistered": unregistered,
        "stale": stale,
        "registered_count": len([c for c in entries if c in snap_set]),
        "entry_count": len(snap),
    }
