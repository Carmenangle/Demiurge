"""修复：把 Chroma 里还活着的 generation 元数据，补回资产主表缺失的字段。

背景（2026-10-22 真机）：资产库列表读**主表**，但主表曾对 prompt/description/tags
无条件覆盖，而 Chroma 侧 `index_generation` 是 `add_documents`（同 id 静默忽略）。
于是「带空内容的重入」（资产库「发送至对话框」/仓库同步补录）把主表提示词清成空串，
Chroma 仍留真值 ⇒ 用户在资产库看到「（无提示词记录）」。写入侧已修（空值不覆盖 +
入库前空值继承），本脚本把**已经被清掉的历史数据**从 Chroma 捞回来。

只补空，不覆盖非空：主表有值就以主表为准。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/repair_asset_fields_from_chroma.py          # dry-run
    .venv/Scripts/python.exe scripts/repair_asset_fields_from_chroma.py --apply
    # 只修某仓库 / 只修 prompt：
    ... --repo <repo_uuid> --fields prompt
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

DEFAULT_FIELDS = ("prompt", "description", "tags")
# 字段 → 主表列名（tags 在库里叫 tags_json，别直接 row["tags"] 判空——会永远判成空）
_COLUMN = {"prompt": "prompt", "description": "description", "tags": "tags_json"}


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


def _blank(value, field: str = "") -> bool:
    text = str(value or "").strip()
    if field == "tags":          # tags_json 的「空」还包括空数组字面量
        return text in ("", "[]")
    return not text


def _plan(rows, fields: tuple[str, ...], repo_filter: str) -> list[tuple[dict, dict]]:
    """算出待补清单：[(主表行, {字段: 新值})]。只收「主表空 + Chroma 非空」。"""
    plan: list[tuple[dict, dict]] = []
    for repo_id, doc_id, meta in rows:
        if repo_filter and repo_id != repo_filter:
            continue
        row = asset_store.get_row(doc_id)
        if row is None:      # 主表没有这行 ⇒ 走 backfill_asset_db.py，不在本脚本职责内
            continue
        patch: dict = {}
        for f in fields:
            if not _blank(row.get(_COLUMN[f]), f):
                continue
            new = meta.get(f)
            if _blank(new, f):
                continue
            patch[f] = ([t for t in str(new).split(",") if t.strip()]
                        if f == "tags" else str(new))
        if patch:
            plan.append((row, patch))
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--repo", default="", help="只修这个 repo_id（默认全部）")
    ap.add_argument("--fields", default=",".join(DEFAULT_FIELDS),
                    help="要补的字段，逗号分隔（prompt,description,tags）")
    ap.add_argument("--show", type=int, default=8, help="预览前 N 条")
    args = ap.parse_args()

    fields = tuple(f.strip() for f in args.fields.split(",") if f.strip())
    unknown = [f for f in fields if f not in DEFAULT_FIELDS]
    if unknown:
        raise SystemExit(f"不支持的字段：{unknown}（可选 {DEFAULT_FIELDS}）")

    init_db()
    rows = list(_iter_chroma_generations())
    plan = _plan(rows, fields, args.repo)
    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] Chroma generation {len(rows)} 条，"
          f"待补 {len(plan)} 条（字段={','.join(fields)}）")
    for row, patch in plan[: args.show]:
        detail = ", ".join(
            f"{k}={v if k == 'tags' else str(v)[:40] + '…'}" for k, v in patch.items())
        print(f"  {row['id'][:18]}  repo={row.get('repo_id', '')[:12]}  {detail}")
    if len(plan) > args.show:
        print(f"  ... 其余 {len(plan) - args.show} 条省略")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 执行；幂等可重跑）")
        return

    ok = failed = 0
    for row, patch in plan:
        item = {**row, **patch}
        # tags 以 list 传入（upsert_asset 收 list/str 两种）；tags_json 列由它自己序列化
        try:
            asset_store.upsert_asset(item)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
            failed += 1
            print(f"  写入失败 {row['id']}: {exc}")

    left = _plan(list(_iter_chroma_generations()), fields, args.repo)
    print(f"\n✅ 写入={ok} 失败={failed}；复核剩余待补={len(left)}")
    if left:
        print("  仍有空缺（Chroma 侧同样为空，属源头无数据，只能反推）：",
              [r["id"][:16] for r, _ in left[:5]])
        sys.exit(1)


if __name__ == "__main__":
    main()
