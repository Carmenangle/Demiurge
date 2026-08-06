"""世界书：条目解析、constant/可检索拆分、注入组装与预算封顶。"""
import json

from app.services import worldbook as wb
from app.services import character_card as cc
from app.services import character_store as cs


def test_parse_entries_skips_disabled_and_empty():
    book = {"entries": [
        {"keys": ["a"], "content": "启用条目", "constant": True},
        {"keys": ["b"], "content": "关闭", "enabled": False},
        {"keys": ["c"], "content": "  ", "constant": False},   # 空内容
        {"keys": ["d"], "content": "普通", "disable": True},    # 明确 disable
        {"keys": ["e"], "content": "可检索", "constant": False},
    ]}
    entries = wb.parse_entries(book)
    assert [e.content for e in entries] == ["启用条目", "可检索"]
    assert entries[0].constant is True
    assert entries[1].constant is False


def test_parse_entries_object_format():
    # 独立世界文件是 uid 为键的 object 格式，也要兼容
    book = {"entries": {"0": {"keys": ["k"], "content": "对象格式", "constant": True}}}
    entries = wb.parse_entries(book)
    assert len(entries) == 1 and entries[0].content == "对象格式"


def test_parse_empty():
    assert wb.parse_entries(None) == []
    assert wb.parse_entries({"entries": []}) == []


def test_assemble_constant_always_included(monkeypatch):
    # 检索置空 → 只剩 constant
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    entries = [wb.Entry(content="世界常驻设定", constant=True),
               wb.Entry(content="非常驻", constant=False)]
    out = wb.assemble("r1", entries, "任意", None, k=4)  # cfg 不用（检索被 mock）
    assert "世界常驻设定" in out
    assert "非常驻" not in out  # 未被检索命中则不带
    assert out.startswith("【世界设定")


def test_assemble_merges_retrieved_and_dedups(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: ["检索命中A", "世界常驻设定"])
    entries = [wb.Entry(content="世界常驻设定", constant=True),
               wb.Entry(content="检索命中A", constant=False)]
    out = wb.assemble("r1", entries, "q", None)
    # constant + 检索A，且重复的"世界常驻设定"只出现一次
    assert out.count("世界常驻设定") == 1
    assert "检索命中A" in out


def test_assemble_budget_caps(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    big = "字" * 500
    entries = [wb.Entry(content=big, constant=True),
               wb.Entry(content="第二条常驻", constant=True)]
    out = wb.assemble("r1", entries, "q", None, budget=50)
    # 预算很小：第一条保留，第二条被截断
    assert big in out
    assert "第二条常驻" not in out


def test_assemble_empty_returns_blank(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    assert wb.assemble("r1", [], "q", None) == ""


def test_assemble_selection_returns_original_indices_for_all_activation_paths(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: ["语义命中"])
    entries = wb.parse_entries({"entries": [
        {"content": "已关闭", "constant": True, "enabled": False},
        {"content": "关键词命中", "keys": ["冷倾雪"]},
        {"content": "常驻设定", "constant": True},
        {"content": "语义命中"},
        {"content": "未注入条目"},
    ]})

    selection = wb.assemble_selection("r1", entries, "冷倾雪醒来", None)

    assert selection.indices == [1, 2, 3]
    assert selection.keyword_indices == [1]
    assert "关键词命中" in selection.text
    assert "常驻设定" in selection.text
    assert "语义命中" in selection.text
    assert "未注入条目" not in selection.text


def test_assemble_uses_in_memory_sparse_retrieval_while_index_is_empty(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    entries = [
        wb.Entry(content="塞西莉亚是幽影帝国的统治者", constant=False),
        wb.Entry(content="奥萝拉掌控碧海航路", constant=False),
    ]

    selection = wb.assemble_selection("repo", entries, "拒绝塞西莉亚的收养", None)

    assert "塞西莉亚是幽影帝国的统治者" in selection.text
    assert "奥萝拉掌控碧海航路" not in selection.text


def test_中文稀疏召回不因常见单字污染其他角色条目(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *args, **kwargs: [])
    entries = wb.parse_entries({"entries": [
        {"content": "帝国通用规则：夜间实行宵禁。", "constant": True},
        {"content": "露娜负责王城路线与贵族礼仪。", "keys": ["露娜"]},
        {"content": "米拉负责边境诊疗与药材鉴定。", "keys": ["米拉"]},
    ]})

    selection = wb.assemble_selection("repo", entries, "让米拉检查药材", None)

    assert selection.indices == [2, 0]
    assert selection.keyword_indices == [2]
    assert "米拉负责边境诊疗" in selection.text
    assert "露娜负责王城路线" not in selection.text


def test_load_entries_from_saved_card(tmp_path):
    base = str(tmp_path)
    card = cc.parse_card_json(json.dumps({
        "data": {"name": "WB", "character_book": {"entries": [
            {"keys": ["k"], "content": "内嵌世界书条目", "constant": True}]}}}))
    cs.save_card(base, card)
    entries = wb.load_entries(base, "WB")
    assert len(entries) == 1 and entries[0].content == "内嵌世界书条目"


def test_load_entries_no_book(tmp_path):
    base = str(tmp_path)
    cs.save_card(base, cc.parse_card_json(json.dumps({"data": {"name": "NoBook"}})))
    assert wb.load_entries(base, "NoBook") == []


def test_ensure_indexed_only_embeds_changed_entries(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.rows = {
                "kept-random-id": ("未变化条目", {"kind": "worldbook"}),
                "removed-random-id": ("已被替换条目", {"kind": "worldbook"}),
                wb._WB_MARK: ("", {"kind": "_wb_mark", "hash": "old"}),
            }
            self.added: list[str] = []
            self.deleted: list[str] = []

        def get(self, ids=None):
            selected = self.rows.items() if ids is None else (
                (item_id, self.rows[item_id]) for item_id in ids if item_id in self.rows
            )
            rows = list(selected)
            return {
                "ids": [item_id for item_id, _ in rows],
                "documents": [value[0] for _, value in rows],
                "metadatas": [value[1] for _, value in rows],
            }

        def add_documents(self, docs, ids):
            for item_id, doc in zip(ids, docs):
                self.added.append(doc.page_content)
                self.rows[item_id] = (doc.page_content, doc.metadata)

        def delete(self, ids):
            self.deleted.extend(ids)
            for item_id in ids:
                self.rows.pop(item_id, None)

    store = FakeStore()
    monkeypatch.setattr(wb.rag_backend, "store", lambda *_args, **_kwargs: store)
    entries = [
        wb.Entry(content="未变化条目", constant=False),
        wb.Entry(content="新条目", constant=False),
        wb.Entry(content="常驻条目", constant=True),
    ]

    assert wb.ensure_indexed("repo", entries, None) is True
    assert store.added == ["新条目"]
    assert set(store.deleted) == {"removed-random-id", wb._WB_MARK}
    assert store.rows["kept-random-id"][0] == "未变化条目"

    store.added.clear()
    store.deleted.clear()
    assert wb.ensure_indexed("repo", entries, None) is False
    assert store.added == []
    assert store.deleted == []


def test_schedule_index_does_not_block_caller(monkeypatch):
    pending = []
    indexed = []

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            assert name.startswith("worldbook-index-")
            assert daemon is True
            self.target = target

        def start(self):
            pending.append(self.target)

    cfg = wb.rag_backend.EmbedConfig(base_url="http://embed", embed_model="model")
    monkeypatch.setattr(wb.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        wb, "ensure_indexed", lambda repo_id, entries, _cfg: indexed.append((repo_id, entries)),
    )
    monkeypatch.setattr(wb, "_index_delta", lambda *_args: ([], [wb.Entry("条目", False)], []))

    assert wb.schedule_index("repo", [wb.Entry("条目", False)], cfg) is True
    assert indexed == []
    assert wb.schedule_index("repo", [wb.Entry("条目", False)], cfg) is False

    pending.pop()()
    assert indexed[0][0] == "repo"
    assert wb.schedule_index("repo", [wb.Entry("条目", False)], cfg) is True
    pending.pop()()


def test_schedule_index_only_notifies_for_initial_missing_entries(monkeypatch):
    pending = []
    notices = []

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            pending.append(self.target)

    cfg = wb.rag_backend.EmbedConfig(base_url="http://embed", embed_model="model")
    monkeypatch.setattr(wb.threading, "Thread", DeferredThread)
    monkeypatch.setattr(wb.rag_backend, "store", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(wb, "ensure_indexed", lambda *_args: True)
    monkeypatch.setattr(
        wb, "_index_delta",
        lambda *_args: ([], [wb.Entry("新条目", False), wb.Entry("另一条", False)], []),
    )

    assert wb.schedule_index("new", [wb.Entry("新条目", False)], cfg,
                             on_initial=lambda count: notices.append(count)) is True
    assert notices == [2]
    pending.pop()()

    monkeypatch.setattr(wb, "_index_delta", lambda *_args: (["existing"], [], []))
    assert wb.schedule_index("ready", [wb.Entry("已有条目", False)], cfg,
                             on_initial=lambda count: notices.append(count)) is False
    assert notices == [2]
