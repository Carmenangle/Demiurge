#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cleanup_canvas_groups.py — 清理画布历史布局漂移（可复用、幂等、带备份）

背景（2026-08-21）：
  - 多个仓库 canvas.json 存在「重复建组」：同一 label（世界书条目/作品名）被建了 N 个
    group（实测 19 label × 10 个 = 190 个），y 坐标从 0 纵贯到 46076+，导致：
      1. fitView 把所有 group 纳入视野 → 主群节点被缩到极小 → 节点/预设卡看起来"离得远"
      2. 预设卡（inspiration_cards）被"带"到超远位置（y=54036）
  - 现状代码不会再生（createGroup 仅右键菜单手动触发），属历史数据漂移。

清理策略（保守、可回滚）：
  1. 对每个 label 的 group：保留 y 绝对值最小（最靠近主群）的 1 个，删除其余重复。
  2. 删除前备份原 canvas.json → <path>.bak-<ts>（与 project 数据同目录）。
  3. 仅清理 label 重复的 group；无 label / 唯一 label 的 group 一律不动。
  4. 预设卡（inspiration_cards）若 y 超出主群范围（> 5000），移回主群右侧 x=1000,y=24。

用法：
  python scripts/cleanup_canvas_groups.py              # 扫描全部仓库并执行
  python scripts/cleanup_canvas_groups.py --dry-run    # 只报告不修改
  python scripts/cleanup_canvas_groups.py --path <abs> # 只处理指定 canvas.json

幂等性：重复执行不会二次删除（已清理的 label 只剩 1 个 group）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

# 主群范围（画布坐标）：超出此 y 的预设卡视为被"带远"
MAIN_GROUP_Y_LIMIT = 5000
# 预设卡移回位置（主群节点 x 范围约 -300~900，放右侧）
CARD_X = 1000
CARD_Y = 24


def scan_canvas(path: Path) -> dict:
    """读取 canvas.json；损坏/缺失返回空结构。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_dup_groups(data: dict):
    """返回 {label: [group_key...]}——label 重复的 group 列表（按 y 绝对值排序，首项为保留项）。"""
    nodes = data.get("nodes") or {}
    by_label: dict[str, list[tuple[str, dict]]] = {}
    for key, v in nodes.items():
        if not key.startswith("group-"):
            continue
        label = str(v.get("label") or "").strip()
        if not label:
            continue
        by_label.setdefault(label, []).append((key, v))
    dups: dict[str, list[tuple[str, dict]]] = {}
    for label, items in by_label.items():
        if len(items) > 1:
            # 按 |y| 升序，保留最靠近主群（y≈0）的 1 个
            items.sort(key=lambda kv: abs(kv[1].get("y", 0)))
            dups[label] = items
    return dups


def far_cards(data: dict) -> list[dict]:
    """y 超出主群范围的预设卡。"""
    return [
        c for c in (data.get("inspiration_cards") or [])
        if isinstance(c, dict) and c.get("y", 0) > MAIN_GROUP_Y_LIMIT
    ]


def compress_layout(data: dict) -> bool:
    """压缩散落远处的布局回主群附近（独立于去重，永远执行）。

    返回是否有任何节点被移动（供调用方判断是否需要备份/写盘）。
    - group：按 y 顺序纵向重排到主群下方（y 从 700 起，保持 x），消除纵贯几万像素的长蛇阵。
    - 单个投影节点（img-/video-/audio-）：远离主群中位数 >5000 的，拉回主群右侧网格。
    - 预设卡：y > 5000 的移回主群右侧。
    """
    nodes = data.get("nodes") or {}
    moved = False

    # group 横向两列排布：组宽 ~1856，每行 2 组（x 错开 1900），按 y 顺序填入，
    # 纵向累计 = ceil(n/2) × max(h)，比单列（Σh）紧凑数倍，fitView 视野不再被拉巨远。
    kept_groups = sorted(
        ((k, v) for k, v in nodes.items() if k.startswith("group-")),
        key=lambda kv: kv[1].get("y", 0),
    )
    GROUP_COLS = 2
    COL_GAP = 1900
    ROW_GAP = 48
    cursor_row_y = 700
    for i, (k, v) in enumerate(kept_groups):
        col = i % GROUP_COLS
        h = v.get("h") or 3000
        if col == 0:
            if i > 0:
                cursor_row_y += row_max_h + ROW_GAP
            row_max_h = h
        else:
            row_max_h = max(row_max_h, h)
        v["x"] = 24 + col * COL_GAP
        v["y"] = cursor_row_y
        moved = True

    # 单个投影节点拉回主群
    ng_ys = [v.get("y", 0) for k, v in nodes.items() if not k.startswith("group-")]
    if ng_ys:
        ng_sorted = sorted(ng_ys)
        main_y = ng_sorted[len(ng_sorted) // 2]
        max_x = max((v.get("x", 0) for k, v in nodes.items() if not k.startswith("group-")), default=0)
        col = 0
        for k, v in list(nodes.items()):
            if (k.startswith("img-") or k.startswith("video-") or k.startswith("audio-")) \
                    and abs(v.get("y", 0) - main_y) > MAIN_GROUP_Y_LIMIT:
                v["x"] = max_x + 120 + col * 260
                v["y"] = main_y
                col += 1
                moved = True

    # 预设卡移回主群
    for c in data.get("inspiration_cards") or []:
        if isinstance(c, dict) and c.get("y", 0) > MAIN_GROUP_Y_LIMIT:
            c["x"] = CARD_X
            c["y"] = CARD_Y
            moved = True

    return moved


def cleanup(path: Path, dry_run: bool) -> dict:
    """清理单个 canvas.json；返回 {file, removed_groups, moved_cards, backed_up}。"""
    data = scan_canvas(path)
    if not data:
        return {"file": str(path), "error": "empty/corrupt", "removed_groups": 0, "moved_cards": 0}
    dups = find_dup_groups(data)

    removed = sum(len(v) - 1 for v in dups.values())

    # dry-run：先去重评估，再压缩评估（压缩可能因去重后的 group 变化而有差异，dry-run 以最终态为准）
    if dry_run:
        # 克隆数据模拟最终态
        import copy
        sim = copy.deepcopy(data)
        # 去重（仅当存在重复时才过滤，防误删全部 group）
        if dups:
            sim_nodes = sim.get("nodes") or {}
            keep = set()
            for label, items in dups.items():
                keep.add(items[0][0])
            sim_nodes = {k: v for k, v in sim_nodes.items() if not (k.startswith("group-") and k not in keep)}
            sim["nodes"] = sim_nodes
        layout_moved = compress_layout(sim)
        detail = "; ".join(f"{label} x{len(v)}->1" for label, v in dups.items())
        return {
            "file": str(path), "dry_run": True,
            "removed_groups": removed, "moved_cards": 0,
            "layout_compressed": layout_moved,
            "detail": (detail or "布局压缩")[:300],
        }

    # 备份
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_suffix(f"{path.suffix}.bak-{ts}")
    shutil.copy2(path, bak)

    # 1) 删除重复 group（保留每个 label 的 [0]）——仅当存在重复时才过滤！
    #    ★ 防误删：keep 为空（无重复）时若执行过滤，`k not in keep` 恒真 → 全部 group 被删。
    nodes = data.get("nodes") or {}
    if dups:
        keep = set()
        for label, items in dups.items():
            keep.add(items[0][0])
        nodes = {k: v for k, v in nodes.items() if not (k.startswith("group-") and k not in keep)}
        data["nodes"] = nodes

    # 2) 压缩布局（group 纵向重排 / 远投影节点拉回 / 预设卡移回）
    layout_moved = compress_layout(data)

    path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "file": str(path), "removed_groups": removed, "moved_cards": 0,
        "layout_compressed": layout_moved,
        "backed_up": str(bak),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="清理画布重复 group 与远距预设卡")
    ap.add_argument("--dry-run", action="store_true", help="只报告不修改")
    ap.add_argument("--path", type=str, default=None, help="只处理指定 canvas.json")
    args = ap.parse_args()

    if args.path:
        files = [Path(args.path)]
    else:
        root = Path(__file__).resolve().parent.parent / "userdata"
        if not root.exists():
            print(f"[!] userdata 目录不存在: {root}")
            return 1
        files = sorted(root.rglob("canvas.json"))

    if not files:
        print("[!] 未找到 canvas.json")
        return 1

    total_removed = 0
    total_moved = 0
    for f in files:
        r = cleanup(f, args.dry_run)
        if r.get("clean"):
            print(f"[=] {r['file']}: 干净")
        elif r.get("error"):
            print(f"[!] {r['file']}: {r['error']}")
        elif r.get("dry_run"):
            print(f"[*] {r['file']}: 将删 {r['removed_groups']} 组、移回 {r['moved_cards']} 卡 | {r.get('detail','')}")
        else:
            total_removed += r["removed_groups"]
            total_moved += r["moved_cards"]
            print(f"[✓] {r['file']}: 删 {r['removed_groups']} 组、移 {r['moved_cards']} 卡 | 备份 {r.get('backed_up','')}")

    print(f"\n合计: {'(dry-run)' if args.dry_run else ''} 删除 {total_removed} 个重复 group、移回 {total_moved} 张预设卡")
    return 0


if __name__ == "__main__":
    sys.exit(main())
