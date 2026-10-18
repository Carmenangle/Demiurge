"""一次性数据修正（2026-10-18）：素材资产的「标题」从 description 迁到 prompt。

背景：`index_web_material_asset` 早期把 provenance 的 title 塞进 description，
导致素材详情里的「视觉描述」其实是标题（用户实测「素材只有标签没有描述」）。
新口径：prompt = title（进嵌入文本），description 只放 VLM 视觉描述。

顺带把 Chroma 元数据一并修正（避免再次回填时把标题写回描述），重嵌入这几个文档。
只动 repo_web-materials 且 description 非空、prompt 为空的行。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.documents import Document  # noqa: E402

from app.services import asset_store, rag_store  # noqa: E402


def main() -> None:
    cfg = rag_store.embed_config_from_state()
    coll = rag_store._repo_collection(rag_store.WEB_MATERIALS_REPO_ID)
    moved = 0
    for d in rag_store._dump(coll, cfg):
        if d.get("kind") != "generation":
            continue
        desc = str(d.get("description") or "")
        prompt = str(d.get("prompt") or "")
        if not desc or prompt:
            continue
        meta = dict(d)
        meta.pop("content", None)
        meta["prompt"] = desc
        meta["description"] = ""
        store = rag_store._store(coll, cfg)
        store.update_document(d["id"], Document(page_content=rag_store._content_for(meta), metadata=meta))
        asset_store.upsert_from_generation(rag_store.WEB_MATERIALS_REPO_ID, meta, d["id"])
        moved += 1
        print(f"  迁移 {d['id'][:14]}: title -> prompt（{desc[:30]}）")
    print(f"完成：{moved} 条素材的标题从 description 迁到 prompt")


if __name__ == "__main__":
    main()
