"""一次性回填：把已在盘的 _web_materials/ 素材补录为资产库记录（P2-1 完整刀）。

背景：本刀之前保存的素材只有文件 + provenance，没有 RAG 资产记录 ⇒ 资产库看不到。
保存/删除链路已挂接（image_store → rag_store.index_web_material_asset），本脚本
给存量补录，与实时链走同一函数（幂等：同 image_url ⇒ 同 doc_id upsert，可重跑）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/index_web_materials.py <outputDir>            # dry-run，只打印
    .venv/Scripts/python.exe scripts/index_web_materials.py <outputDir> --apply    # 真正补录

安全：
- 只读素材文件与 provenance，**不移动/不删除/不改写**任何文件；
- 只处理 _web_materials/ 顶层图片文件（inspiration/ 等子目录跳过）；
- 无 provenance 的旧文件按最小元数据补录（source=web、标题=文件名），计数单列；
- created_at 取 provenance.downloaded_at（保序）；解析失败回退当前时间。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 保证 backend/ 在 path

from app.services import image_store, rag_store, view_urls  # noqa: E402

IMG = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _created_at_ms(prov: dict) -> int | None:
    raw = str(prov.get("downloaded_at") or "")
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(raw).timestamp() * 1000)
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="outputDir（素材在 <root>/_web_materials/）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    d = image_store.web_materials_dir(str(root))
    if not d.is_dir():
        raise SystemExit(f"素材目录不存在：{d}")

    provenance = image_store._load_provenance(d)
    targets: list[tuple[Path, dict, bool]] = []   # (文件, provenance条目或最小元数据, 有无prov)
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.suffix.lower() not in IMG:
            continue
        prov = provenance.get(f.name)
        if prov is None:
            prov = {"title": f.name}              # 旧文件无溯源 ⇒ 最小元数据补录
            targets.append((f, prov, False))
        else:
            targets.append((f, prov, True))

    with_prov = sum(1 for _, _, ok in targets if ok)
    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] {d}")
    print(f"待补录 {len(targets)} 个素材（带 provenance {with_prov} / 无 provenance {len(targets) - with_prov}）")
    for f, prov, ok in targets[:20]:
        tag = "" if ok else "（无溯源，最小元数据）"
        print(f"  {f.name}  provider={prov.get('provider') or '-'} tags={len(prov.get('tags') or [])} {tag}")
    if len(targets) > 20:
        print(f"  ... 其余 {len(targets) - 20} 个省略")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 执行；幂等可重跑）")
        return

    ok_n = failed = 0
    for f, prov, _has in targets:
        url = view_urls.local_view(str(f))
        if rag_store.index_web_material_asset(url, prov, created_at=_created_at_ms(prov)):
            ok_n += 1
        else:
            failed += 1
    print(f"\n✅ indexed={ok_n} failed={failed}（failed 只留 warning 不抛；可重跑补）")


if __name__ == "__main__":
    main()
