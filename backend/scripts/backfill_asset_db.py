"""一次性回填：把 Chroma 里的 generation 元数据灌进 SQLite 资产主表（P1-1）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/backfill_asset_db.py           # dry-run，只统计
    .venv/Scripts/python.exe scripts/backfill_asset_db.py --apply   # 真正回填 + 对账

安全：
- 只读 Chroma、只写主表（assets / asset_tags / assets_fts 由触发器同步）；
- 幂等 upsert，可重跑；**不复活回收站记录**（upsert 不覆盖 deleted_at）；
- 对账：回填后对比「Chroma generation id 集」与「主表 id 集」，双向 diff 必须为 0
  （PLAN §P1-1 验收「双读期对账」；diff>0 时退出码 1，方便脚本化）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb  # noqa: E402

from app.config import CHROMA_DIR  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services import asset_store  # noqa: E402


def _iter_chroma_generations():
    """产出 (repo_id, doc_id, metadata)：只取 generation 且带 image_url 的记录。"""
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    for coll in client.list_collections():
        if not coll.name.startswith("repo_"):
            continue
        got = client.get_collection(coll.name).get(include=["metadatas"])
        for doc_id, meta in zip(got.get("ids") or [], got.get("metadatas") or []):
            meta = meta or {}
            if meta.get("kind") != "generation" or not meta.get("image_url"):
                continue
            repo_id = str(meta.get("repo_id") or coll.name[len("repo_"):])
            yield repo_id, doc_id, meta


def _chroma_id_set() -> set[str]:
    return {doc_id for _repo, doc_id, _meta in _iter_chroma_generations()}


def _db_id_set() -> set[str]:
    ids: set[str] = set()
    conn = asset_store.get_connection()
    try:
        for row in conn.execute("select id from assets"):
            ids.add(row["id"])
    finally:
        conn.close()
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    init_db()   # 确保 schema（assets/asset_tags/assets_fts/触发器）
    rows = list(_iter_chroma_generations())
    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] Chroma generation 记录：{len(rows)} 条")
    for repo_id, doc_id, meta in rows[:10]:
        print(f"  {doc_id[:14]}  repo={repo_id[:12]}  {str(meta.get('image_url'))[-56:]}")
    if len(rows) > 10:
        print(f"  ... 其余 {len(rows) - 10} 条省略")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 执行；幂等可重跑）")
        return

    ok = failed = 0
    for repo_id, doc_id, meta in rows:
        try:
            asset_store.upsert_from_generation(repo_id, meta, doc_id)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
            failed += 1
            print(f"  upsert 失败 {doc_id}: {exc}")

    chroma_ids = _chroma_id_set()
    db_ids = _db_id_set()
    only_chroma = sorted(chroma_ids - db_ids)
    only_db = sorted(db_ids - chroma_ids)
    print(f"\n✅ upsert={ok} failed={failed}")
    print(f"对账：Chroma={len(chroma_ids)} DB={len(db_ids)} "
          f"仅Chroma={len(only_chroma)} 仅DB={len(only_db)}")
    if only_chroma[:5]:
        print("  仅 Chroma（主表缺，列表会漏）：", only_chroma[:5])
    if only_db[:5]:
        print("  仅 DB（向量库无，多为回收站记录/已删）：", only_db[:5])
    print("对账结论：", "PASS（id diff = 0）" if not only_chroma and not only_db else "DIFF>0，需复查")
    if only_chroma or only_db:
        sys.exit(1)


if __name__ == "__main__":
    main()
