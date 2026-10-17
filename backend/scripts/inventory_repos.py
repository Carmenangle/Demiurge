"""只读盘点：把 outputDir 下每个顶层文件夹分类（真作品 / 存档 / GUID残留 / 重复），
不移动、不删除、不写任何东西。用于人工确认后再决定怎么整理。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ENGINE = {"state.json", "tables.json", "table_progress.json", "worldbook.json",
          "worldbook_session.json", "chronicle.db", "temporal_facts.db",
          "character_beliefs.db", "narrative_ci.db", "visual_preferences.db",
          "visual_ci.db", "canvas.json"}


def card_name_of(state: Path) -> str:
    try:
        return str(json.loads(state.read_text(encoding="utf-8")).get("card_name") or "")
    except Exception:
        return ""


def repo_id_of(folder: Path) -> str:
    try:
        m = json.loads((folder / "_repo.json").read_text(encoding="utf-8"))
        return str(m.get("id") or "") if isinstance(m, dict) else ""
    except Exception:
        return ""


def scan_dir(d: Path) -> dict:
    names = {p.name for p in d.iterdir() if p.is_file()}
    state = d / "state.json"
    chat = d / "chat.json"
    return {
        "chat": (chat.stat().st_size if chat.is_file() else 0),
        "card": card_name_of(state) if state.is_file() else "",
        "engine": len([n for n in names if n in ENGINE]),
        "repo_id": repo_id_of(d),
    }


def main() -> None:
    root = Path(sys.argv[1])
    skip = {"_test", "_bak", "_web_materials", "plans"}
    rows = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name in skip or d.name.startswith("."):
            continue
        info = scan_dir(d)
        # 递归找 SAVE01/子夹里的 chat/state
        subs = [p for p in d.iterdir() if p.is_dir()]
        sub_chat = 0
        sub_card = ""
        for s in subs:
            si = scan_dir(s)
            sub_chat += si["chat"]
            sub_card = sub_card or si["card"]
        rows.append({
            "folder": d.name,
            "kind": "GUID" if len(d.name) == 36 and d.name.count("-") == 4 else "中文",
            "chat_self": info["chat"],
            "chat_sub": sub_chat,
            "card": info["card"] or sub_card,
            "engine": info["engine"],
            "repo_id": info["repo_id"],
            "subdirs": ",".join(s.name for s in subs),
        })
    # 打印
    print(f"{'文件夹':<42}{'类型':<6}{'chat':>7}{'子chat':>7}  {'engine':>6}  card / _repo.id")
    for r in rows:
        print(f"{r['folder'][:40]:<42}{r['kind']:<6}{r['chat_self']:>7}{r['chat_sub']:>7}  {r['engine']:>6}  {r['card'] or '-'} / {r['repo_id'][:8] or '-'}  {('['+r['subdirs']+']') if r['subdirs'] else ''}")
    # 按 card_name 聚类找重复
    from collections import defaultdict
    g: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r["card"]:
            g[r["card"]].append(r["folder"])
    print("\n=== 同名作品（可能重复）===")
    for card, fs in g.items():
        if len(fs) > 1:
            print(f"  {card}: {len(fs)} 份 -> {fs}")


if __name__ == "__main__":
    main()
