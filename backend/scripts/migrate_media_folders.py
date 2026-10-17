"""一次性迁移：把仓库各目录里平铺的媒体产出，按类型移入同级 pictures/videos/audios。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/migrate_media_folders.py <outputDir>            # dry-run，只打印
    .venv/Scripts/python.exe scripts/migrate_media_folders.py <outputDir> --apply    # 真正移动

安全：
- 只移动媒体扩展名文件；应用数据（json/db/md/txt）原样不动。
- 跳过 _test/_bak/_web_materials/_prep/plans 与点目录（工具/资产/缓存）。
- 目标已存在同名则跳过（不覆盖）。
- 幂等：已在 pictures/videos/audios 里的不再动。
"""
from __future__ import annotations

import argparse
import shutil
from collections import Counter
from pathlib import Path

IMG = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VID = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
AUD = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
KIND_DIRS = {"pictures", "videos", "audios"}
SKIP_TOP = {"_test", "_bak", "_web_materials", "_prep", "plans"}


def kind_of(ext: str) -> str | None:
    if ext in IMG:
        return "pictures"
    if ext in VID:
        return "videos"
    if ext in AUD:
        return "audios"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"不是目录：{root}")

    moves: list[tuple[Path, Path, str]] = []
    for d in sorted(root.rglob("*")):
        if not d.is_dir():
            continue
        rel_parts = d.relative_to(root).parts
        if any(p.startswith(".") for p in rel_parts) or (rel_parts and rel_parts[0] in SKIP_TOP):
            continue
        if d.name in KIND_DIRS:          # 已经在类型夹里
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            k = kind_of(f.suffix.lower())
            if not k:
                continue
            moves.append((f, d / k / f.name, k))

    by_kind = Counter(k for _, _, k in moves)
    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] root={root}")
    print(f"拟移动 {len(moves)} 个媒体文件：{dict(by_kind)}")
    for f, dest, k in moves[:30]:
        print(f"  {k:<8} {f.relative_to(root)}")
    if len(moves) > 30:
        print(f"  ... 其余 {len(moves) - 30} 个省略")

    if not args.apply:
        print("\n（dry-run，未改动。加 --apply 执行）")
        return

    moved = skipped = 0
    for f, dest, _k in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            skipped += 1
            continue
        shutil.move(str(f), str(dest))
        moved += 1
    print(f"\n✅ moved={moved} skipped(exists)={skipped}")


if __name__ == "__main__":
    main()
