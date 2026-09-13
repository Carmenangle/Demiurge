"""narrative 路由的展示契约：卡号必须带回合位序（直测路由函数，无需起 TestClient）。

2026-09-13 用户实锤：界面点「整理索引」后最早区间仍显示 `T1-13`。根因不是排序，而是
`GET /narrative/` 漏调 `narrative_store.with_sequence` —— 只改了 store/service 层、
单测全绿也发现不了。故这里按**路由出口**断言，锁住「列表 / 检索 / 导出都回填位序」。
"""
from __future__ import annotations

from app.routers import narrative as narrative_router
from app.services import narrative_store as ns
from app.services.narrative_memory import ChronicleEntry


def _e(text: str, ts: int, te: int, layer: int = 0) -> ChronicleEntry:
    return ChronicleEntry(text=text, turn_start=ts, turn_end=te, layer=layer)


def test_列表端点回填回合位序卡号(tmp_path):
    base = str(tmp_path)
    # 故意让「最早的回合」rowid 最大：模拟整理/补跑后的真实形态
    ns.append(base, "r1", _e("第10到12回合", 10, 12))
    ns.append(base, "r1", _e("第4到6回合", 4, 6))
    ns.append(base, "r1", _e("第1到3回合", 1, 3))

    got = narrative_router.list_chronicle(output_dir=base, repo_id="r1", k=50)

    by_range = {(item["turn_start"], item["turn_end"]): item["card_id"] for item in got["items"]}
    assert by_range == {(1, 3): "T1-1", (4, 6): "T1-2", (10, 12): "T1-3"}


def test_列表端点分页仍用全库序号(tmp_path):
    """`k` 只是分页：只看到最新一条时也必须叫 T1-3，不能因为页内排第一就成 T1-1。"""
    base = str(tmp_path)
    ns.append(base, "r1", _e("第1到3回合", 1, 3))
    ns.append(base, "r1", _e("第4到6回合", 4, 6))
    ns.append(base, "r1", _e("第7到9回合", 7, 9))

    got = narrative_router.list_chronicle(output_dir=base, repo_id="r1", k=1)

    assert len(got["items"]) == 1
    assert got["items"][0]["card_id"] == "T1-3"


def test_检索端点回填回合位序卡号(tmp_path):
    """倒序落盘让 rowid 与回合序相反：rowid=1 是第 7–9 回合（位序 3）。"""
    base = str(tmp_path)
    # 两条都含连续 trigram「雪山救 / 山救援」，保证同一查询能把它们一起召回
    ns.append(base, "r1", _e("雪山救援后续", 7, 9))     # rowid=1 → 位序 3 → T1-3
    ns.append(base, "r1", _e("舞会下药", 4, 6))         # rowid=2 → 位序 2 → T1-2
    ns.append(base, "r1", _e("雪山救援开端", 1, 3))     # rowid=3 → 位序 1 → T1-1

    req = narrative_router.SearchRequest(
        output_dir=base, repo_id="r1", query="雪山救援", k=10)
    got = narrative_router.search_chronicle(req)

    by_range = {(item["turn_start"], item["turn_end"]): item["card_id"] for item in got["items"]}
    assert by_range == {(1, 3): "T1-1", (7, 9): "T1-3"}   # 位序，不是 rowid（3 / 1）


def test_导出端点按全量序且保留封口条目(tmp_path):
    base = str(tmp_path)
    old = ns.append(base, "r1", _e("被让位的重复条", 1, 3))
    ns.append(base, "r1", _e("在位条", 1, 3))
    ns.append(base, "r1", _e("第4到6回合", 4, 6))
    ns.normalize_apply(base, "r1", seal_rowids=[old])

    got = narrative_router.export_chronicle(output_dir=base, repo_id="r1")

    assert len(got["items"]) == 3                       # 封口仍导出（可审计）
    seqs = sorted(item["card_id"] for item in got["items"])
    assert seqs == ["T1-1", "T1-2", "T1-3"]             # 全量集合 1..n 连续


# ── 缺口检测的层数来源（2026-09-13 用户实锤「看不到针对空缺建立索引的功能」）──


def test_整理预览_未传层数时从会话快照取层数(tmp_path, monkeypatch):
    """前端 `previewNormalizeChronicle` 不传 totalTurns（默认 0）⇒ 缺口恒为空、补跑入口
    永不触发。路由必须以会话快照为真源补上真实层数。"""
    from app.services import manual_table_fill as mtf

    base = str(tmp_path)
    ns.append(base, "r1", _e("只覆盖了前三回合", 1, 3))
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo: [
        {"role": "user", "text": "u"}, {"role": "assistant", "text": "a"},
    ] * 4)

    got = narrative_router.normalize_preview(
        narrative_router.NormalizeRequest(output_dir=base, repo_id="r1", total_turns=0))

    assert got["total_turns"] == 4          # 不再是 0
    assert got["gaps"] == [[4, 4]]          # 第 4 层没被覆盖 → 补跑入口可触发


def test_整理预览_显式层数优先且快照不可读时不猜(tmp_path, monkeypatch):
    from app.services import manual_table_fill as mtf

    base = str(tmp_path)
    ns.append(base, "r1", _e("前三回合", 1, 3))
    monkeypatch.setattr(mtf.chat_snapshot, "load", lambda _repo: (_ for _ in ()).throw(OSError()))

    explicit = narrative_router.normalize_preview(
        narrative_router.NormalizeRequest(output_dir=base, repo_id="r1", total_turns=9))
    assert explicit["total_turns"] == 9
    assert explicit["gaps"] == [[4, 9]]

    fallback = narrative_router.normalize_preview(
        narrative_router.NormalizeRequest(output_dir=base, repo_id="r1", total_turns=0))
    assert fallback["total_turns"] == 0 and fallback["gaps"] == []   # 不猜层数
