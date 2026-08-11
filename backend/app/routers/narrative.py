"""叙事纪要端点：读某作品线的事件纪要 + FTS5 索引重建（RAG 重建口）。

属主是 narrative_store（<base>/<repo_id>/chronicle.db，FTS5 trigram）；本路由薄——校验+转发。
纪要是能动性子图每 N 轮自动抽取落盘的「往事」，此处供只读查看/检索/重建。base 由前端传 output_dir。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import narrative_store
from app.services.narrative_memory import ChronicleEntry

router = APIRouter()


def _dump(e: ChronicleEntry) -> dict[str, object]:
    return {
        "rowid": e.rowid, "text": e.text, "turn_start": e.turn_start,
        "turn_end": e.turn_end, "layer": e.layer, "keywords": e.keywords,
        "overview": e.short_overview(), "dialogue": e.dialogue,
        "characters": e.characters,
    }


@router.get("/")
def list_chronicle(output_dir: str, repo_id: str, k: int = 50) -> dict[str, object]:
    """列出某作品线最近 k 条纪要（时间倒序）。无库 → 空列表，不报错。"""
    items = narrative_store.recent(output_dir, repo_id, k=max(1, min(k, 200)))
    return {"items": [_dump(e) for e in items]}


class SearchRequest(BaseModel):
    output_dir: str
    repo_id: str
    query: str
    k: int = 8


@router.post("/search")
def search_chronicle(req: SearchRequest) -> dict[str, object]:
    """按 trigram 相关性检索纪要（调试/前端展示；对话内部已自动召回注入）。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    items = narrative_store.recall(req.output_dir, req.repo_id, req.query,
                                   k=max(1, min(req.k, 50)))
    return {"items": [_dump(e) for e in items]}


class RebuildRequest(BaseModel):
    output_dir: str
    repo_id: str


@router.post("/rebuild")
def rebuild_index(req: RebuildRequest) -> dict[str, object]:
    """RAG 重建：从已存正文清空并重建 FTS5 索引（分词器变更/索引损坏后重跑）。返回重建条数。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    try:
        n = narrative_store.rebuild(req.output_dir, req.repo_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"重建失败：{e}")
    return {"ok": True, "rebuilt": n}


# ── ⑤ 往事纪要人工增删改 + 导入导出（浏览器可视化 CRUD；底层 append/update/delete 已在 store）──


class EntryPayload(BaseModel):
    text: str
    overview: str = ""
    dialogue: str = ""
    characters: list[str] = []
    turn_start: int = 0
    turn_end: int = 0
    layer: int = 0
    keywords: list[str] = []


def _entry(p: EntryPayload) -> ChronicleEntry:
    return ChronicleEntry(
        text=p.text.strip(), turn_start=p.turn_start, turn_end=p.turn_end,
        layer=max(0, min(p.layer, 2)), keywords=[k for k in p.keywords if k.strip()],
        overview=p.overview.strip(), dialogue=p.dialogue.strip(),
        characters=[name.strip() for name in p.characters if name.strip()],
    )


class AddRequest(BaseModel):
    output_dir: str
    repo_id: str
    entry: EntryPayload


@router.post("/add")
def add_entry(req: AddRequest) -> dict[str, object]:
    """人工新增一条往事纪要。返回其 rowid。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    if not req.entry.text.strip():
        raise HTTPException(status_code=400, detail="纪要正文不能为空")
    rowid = narrative_store.append(req.output_dir, req.repo_id, _entry(req.entry))
    return {"ok": True, "rowid": rowid}


class UpdateRequest(BaseModel):
    output_dir: str
    repo_id: str
    rowid: int
    entry: EntryPayload


@router.post("/update")
def update_entry(req: UpdateRequest) -> dict[str, object]:
    """人工改写某条往事纪要（正文/区间/层/关键词）。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    if not req.entry.text.strip():
        raise HTTPException(status_code=400, detail="纪要正文不能为空")
    ok = narrative_store.update_entry(req.output_dir, req.repo_id, req.rowid, _entry(req.entry))
    if not ok:
        raise HTTPException(status_code=404, detail="未找到该纪要")
    return {"ok": True}


class DeleteRequest(BaseModel):
    output_dir: str
    repo_id: str
    rowids: list[int]


@router.post("/delete")
def delete_entries(req: DeleteRequest) -> dict[str, object]:
    """删除指定 rowid 的往事纪要。返回删除条数。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    n = narrative_store.delete_rows(req.output_dir, req.repo_id, req.rowids)
    return {"ok": True, "deleted": n}


@router.get("/export")
def export_chronicle(output_dir: str, repo_id: str) -> dict[str, object]:
    """导出某作品线全部往事纪要（JSON，供备份/迁移到其它存档）。"""
    items = narrative_store.all_entries(output_dir, repo_id)
    return {"version": 1, "repo_id": repo_id, "items": [_dump(e) for e in items]}


class ImportRequest(BaseModel):
    output_dir: str
    repo_id: str
    items: list[EntryPayload]
    replace: bool = False   # True=先清空该作品线现有纪要再导入；False=追加


@router.post("/import")
def import_chronicle(req: ImportRequest) -> dict[str, object]:
    """导入往事纪要（追加或替换）。返回导入条数。"""
    if not (req.output_dir and req.repo_id):
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 repo_id")
    n = narrative_store.import_entries(
        req.output_dir, req.repo_id, [_entry(item) for item in req.items], replace=req.replace,
    )
    return {"ok": True, "imported": n}


class TemporalFactRequest(BaseModel):
    output_dir: str
    repo_id: str
    subject: str
    predicate: str
    object: str
    valid_from_turn: int
    evidence: str
    source: str = "user"
    supersedes_id: str | None = None


@router.post("/facts")
def add_temporal_fact(req: TemporalFactRequest) -> dict[str, object]:
    """写入有证据的世界事实；替代旧事实必须显式给 supersedes_id。"""
    from app.services import temporal_fact_store

    try:
        fact = temporal_fact_store.record(
            req.output_dir, req.repo_id, subject=req.subject, predicate=req.predicate,
            object_=req.object, valid_from_turn=req.valid_from_turn,
            evidence=req.evidence, source=req.source, supersedes_id=req.supersedes_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "fact": fact}


@router.get("/facts")
def temporal_facts(output_dir: str, repo_id: str, turn: int,
                   subject: str = "") -> dict[str, object]:
    from app.services import temporal_fact_store

    return {"items": temporal_fact_store.as_of(
        output_dir, repo_id, turn, subject=subject,
    )}


@router.get("/facts/conflicts")
def temporal_fact_conflicts(output_dir: str, repo_id: str, turn: int) -> dict[str, object]:
    from app.services import temporal_fact_store

    return {"items": temporal_fact_store.conflicts(output_dir, repo_id, turn)}


class NarrativeCIRequest(BaseModel):
    output_dir: str
    repo_id: str
    text: str
    turn: int


@router.post("/ci/check")
def check_narrative(req: NarrativeCIRequest) -> dict[str, object]:
    """手动运行内容中立的 Narrative CI；只保存诊断，不修改正文。"""
    from app.services import narrative_ci, temporal_fact_store

    facts = temporal_fact_store.as_of(req.output_dir, req.repo_id, req.turn)
    items = narrative_ci.evaluate(req.text, turn=req.turn, facts=facts)
    narrative_ci.save(req.output_dir, req.repo_id, items)
    return {"items": items}


@router.get("/ci")
def narrative_diagnostics(output_dir: str, repo_id: str, status: str = "") -> dict[str, object]:
    from app.services import narrative_ci

    return {"items": narrative_ci.list_diagnostics(output_dir, repo_id, status=status)}


class NarrativeCIResolveRequest(BaseModel):
    output_dir: str
    repo_id: str
    diagnostic_id: str
    status: str


@router.post("/ci/resolve")
def resolve_narrative_diagnostic(req: NarrativeCIResolveRequest) -> dict[str, object]:
    from app.services import narrative_ci

    try:
        ok = narrative_ci.resolve(
            req.output_dir, req.repo_id, req.diagnostic_id, req.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="未找到 Narrative CI 诊断")
    return {"ok": True}


class CharacterBeliefRequest(BaseModel):
    output_dir: str
    repo_id: str
    character: str
    fact_id: str
    claim: str
    stance: str
    confidence: float = 1.0
    witnessed_at: int
    evidence: str
    source: str = "user"
    supersedes_id: str | None = None


@router.post("/beliefs")
def add_character_belief(req: CharacterBeliefRequest) -> dict[str, object]:
    from app.services import character_belief

    try:
        item = character_belief.record(
            req.output_dir, req.repo_id, character=req.character, fact_id=req.fact_id,
            claim=req.claim, stance=req.stance, confidence=req.confidence,
            witnessed_at=req.witnessed_at, evidence=req.evidence, source=req.source,
            supersedes_id=req.supersedes_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.get("/beliefs")
def character_beliefs(output_dir: str, repo_id: str, turn: int,
                      character: str = "") -> dict[str, object]:
    from app.services import character_belief

    return {"items": character_belief.active(
        output_dir, repo_id, turn, characters=[character] if character else None,
    )}
