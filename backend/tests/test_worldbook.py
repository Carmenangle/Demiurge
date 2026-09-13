"""世界书：条目解析、constant/可检索拆分、选择性注入组装（整段注入预算 WORLDBOOK_INJECT_MAX_CHARS=8000，
只裁末尾语义补充条目；传 max_chars=None 关闭）。"""
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


def test_assemble_constant_never_truncated(monkeypatch):
    # 上下文合同：constant（全局机制+系统判定机制）全程恒开、全文注入，无预算截断
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    big = "字" * 500
    entries = [wb.Entry(content=big, constant=True),
               wb.Entry(content="第二条常驻", constant=True)]
    out = wb.assemble("r1", entries, "q", None)
    assert big in out
    assert "第二条常驻" in out


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


def test_assemble_注入预算cap裁尾部语义补充不裁机制(monkeypatch):
    """2026-09-04 成本杠杆 L1-B/L3-B：整段注入 8k 硬上限，keyword/constant 锚点全收，
    只让语义补充段衰减并标注省略数。"""
    entries = [
        wb.Entry(content="关键词命中的角色卡（优先级最高）", constant=True, keys=["命"]),
        wb.Entry(content="常驻机制条目" * 40, constant=True),   # constant：永不裁
        wb.Entry(content="非常驻大段条目甲" * 2000, constant=False),
        wb.Entry(content="非常驻大段条目乙" * 2000, constant=False),
    ]
    # 语义检索命中既有非常驻条目（与其它测试同款手法：命中内容必须存在于快照才进入候选）
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [entries[2].content])
    out = wb.assemble("r1", entries, "命", None, k=8)
    assert out.startswith("【世界设定（相关条目）】")
    assert "关键词命中的角色卡" in out          # keyword 命中保留
    assert "常驻机制条目" * 40 in out           # constant 全收
    assert "省略" in out                        # 语义补充超预算被裁并标注
    assert len(out) <= wb.WORLDBOOK_INJECT_MAX_CHARS + 200  # 预算 + 头标/标注余量


def test_assemble_关闭cap恢复旧行为不截断(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    entries = [wb.Entry("超长常驻条目" * 500, True)]
    out = wb.assemble("r1", entries, "q", None, max_chars=None)
    assert out == "【世界设定（相关条目）】\n- " + "超长常驻条目" * 500


# ── 会话内稳定序列（2026-09-12，P4-roleplay 配套）──────────────────────────
# 修复目标：跨轮注入文本的**字节前缀只增不改**，让上游 prompt 前缀缓存能命中。
# 旧行为（assemble_selection）每轮按 keyword→constant→语义重排，条目集一抖动整块重写，
# 位于世界书之后的历史与尾部合同全部不可复用（实测跨轮 LCP 仅 7.6%）。


def _stable_fixture():
    return [
        wb.Entry(content="甲条目内容", constant=False, keys=["甲"], source_index=0),
        wb.Entry(content="乙条目内容", constant=False, keys=["乙"], source_index=1),
        wb.Entry(content="丙条目内容", constant=False, keys=["丙"], source_index=2),
    ]


def test_会话稳定序列_跨轮前缀只增不改(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    entries = _stable_fixture()
    key = "stable-prefix"
    try:
        r1 = wb.assemble_selection_stable("r", entries, "甲", None, session_key=key)
        r2 = wb.assemble_selection_stable("r", entries, "甲 乙", None, session_key=key)
        r3 = wb.assemble_selection_stable("r", entries, "乙 丙", None, session_key=key)
    finally:
        wb.reset_session(key)

    assert "甲条目内容" in r1.text
    assert "乙条目内容" not in r1.text
    # 动态区外置（2026-09-13）：锚点段会话内**双冻结**——中途激活的锚点（乙、丙）登记进
    # 序列但走动态块，不追加进锚点段文本（追加会把分叉点拉到锚点段末尾，≈15k token，
    # 仍够不到 16,384 缓存粒度）。「在场」的语义不变：乙丙每轮仍注入，只是位置在尾部动态块。
    assert r2.text == r1.text
    assert r3.text == r1.text
    assert "甲条目内容" in r3.text                   # 首轮激活的条目不被本轮重排挤掉
    assert "乙条目内容" in r3.dynamic_text
    assert "丙条目内容" in r3.dynamic_text
    assert len(r3.indices) == 3


def test_重排版每轮重排_不保前缀_对照(monkeypatch):
    """对照：无状态 assemble_selection 第三轮丢掉首轮条目 → 不存在前缀关系。"""
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    entries = _stable_fixture()

    o1 = wb.assemble_selection("r", entries, "甲", None)
    o3 = wb.assemble_selection("r", entries, "乙 丙", None)

    assert "甲条目内容" not in o3.text
    assert not o3.text.startswith(o1.text)


def test_会话稳定序列_裁剪只在尾部且结果恒为前缀(monkeypatch):
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    entries = [
        wb.Entry(content="锚点条目", constant=False, keys=["甲"], source_index=0),
        wb.Entry(content="补充条目" * 300, constant=False, source_index=1),
    ]
    # 语义检索命中第二条（非锚点 → 预算压力下从尾部裁）
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [entries[1].content])
    key = "stable-clip"
    try:
        wide = wb.assemble_selection_stable(
            "r", entries, "甲", None, session_key=key, max_chars=8000)
        assert "补充条目" in wide.recall_text      # 召回段单独外带（不进世界书块）
        assert "补充条目" not in wide.text
        assert wide.dropped == 0
        wb.reset_session(key)
        tight = wb.assemble_selection_stable(
            "r", entries, "甲", None, session_key=key, max_chars=200)
    finally:
        wb.reset_session(key)

    assert "锚点条目" in tight.text             # 锚点永不裁
    assert "补充条目" not in tight.text         # 超预算的尾部补充被裁
    assert tight.dropped == 1
    assert "省略" not in tight.text             # 省略标注会破坏前缀，稳定序列刻意不加
    assert wide.text.startswith(tight.text)     # 裁剪结果 = 宽预算文本的前缀


def test_会话稳定序列_会话键隔离(monkeypatch):
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    entries = [
        wb.Entry(content="甲条目内容", constant=False, keys=["甲"], source_index=0),
        wb.Entry(content="乙条目内容", constant=False, keys=["乙"], source_index=1),
    ]
    try:
        a = wb.assemble_selection_stable("r", entries, "甲", None, session_key="stable-A")
        b = wb.assemble_selection_stable("r", entries, "乙", None, session_key="stable-B")
    finally:
        wb.reset_session("stable-A")
        wb.reset_session("stable-B")

    assert "甲条目内容" in a.text
    assert "甲条目内容" not in b.text
    assert "乙条目内容" in b.text


def test_条目正文被改写后槽位不塌陷_其后条目不左移(monkeypatch):
    """回归（2026-09-12）：curator 改写条目正文时，序列按**稳定身份键**解析。

    改前序列按内容 hash 记序：curator 一次 worldbook_update（底座保留 + 末尾
    【剧情进展·动态】区变化，见 worldbook_store._merge_character_dynamic）就会让旧 hash
    解析不到 ⇒ 该槽位整条丢弃 ⇒ 其后条目全部左移 ⇒ 前缀断在条目**起始**（真机实测跨轮
    LCP 只剩 9.0%）。改后按 id/comment 记序，条目仍在原位、其后条目不动。
    2026-09-13 升级（动态区外置）：动态区不再进锚点段文本（进 dynamic_text），锚点段
    跨轮**逐字节不变**——前缀不再「延伸到动态区之前」而是覆盖整个锚点段。
    """
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "stable-rewrite"
    before = [
        wb.Entry(content="甲条目底座", constant=True, source_index=0, uid="1"),
        wb.Entry(content="乙条目内容", constant=True, source_index=1, uid="2"),
        wb.Entry(content="丙条目内容", constant=True, source_index=2, uid="3"),
    ]
    # curator 改写第一条正文：底座逐字保留，末尾追加动态区
    after = [
        wb.Entry(content="甲条目底座\n\n【剧情进展·动态】\n本轮新进展",
                 constant=True, source_index=0, uid="1"),
        before[1], before[2],
    ]
    try:
        r1 = wb.assemble_selection_stable("r", before, "", None, session_key=key)
        seq_before = list(wb._SESSION_SEQ[key])
        r2 = wb.assemble_selection_stable("r", after, "", None, session_key=key)
        seq_after = list(wb._SESSION_SEQ[key])
    finally:
        wb.reset_session(key)

    # 序列按身份键记序，且改写没有新增槽位（不塌陷、不膨胀）
    assert seq_before == ["i:1", "i:2", "i:3"]
    assert seq_after == ["i:1", "i:2", "i:3"]
    # 三条都仍在场，顺序不变
    assert r2.text.index("甲条目底座") < r2.text.index("乙条目内容") < r2.text.index("丙条目内容")
    assert r2.text.count("甲条目底座") == 1
    # 动态区外置：锚点段不含动态区，动态区进 dynamic_text（调用方放尾部动态块）
    assert "本轮新进展" in r2.dynamic_text
    assert "本轮新进展" not in r2.text
    # 锚点段跨轮逐字节不变（动态区外置后覆盖整段，不再只是「到动态区之前」）
    assert r2.text == r1.text
    assert r2.text.startswith("【世界设定（相关条目）】\n- 甲条目底座")


def test_身份键回退_无id时用comment_再无则用内容hash():
    assert wb.entry_identity(wb.Entry("正文", False, uid="7")) == "i:7"
    assert wb.entry_identity(wb.Entry("正文", False, comment="角色卡·甲")) == "c:角色卡·甲"
    assert wb.entry_identity(wb.Entry("正文", False)) == f"h:{wb._hid('正文')}"


def test_parse_entries_兼容两种容器的身份键():
    """ST 对象形式（keyed-by-uid）与 V2 卡数组（id 字段）都要拿到稳定 uid。"""
    as_list = wb.parse_entries({"entries": [{"content": "甲", "id": 11}]})
    as_dict = wb.parse_entries({"entries": {"3": {"content": "乙"}}})
    assert [e.uid for e in as_list] == ["11"]
    assert [e.uid for e in as_dict] == ["3"]


# ── 锚点轨 / 召回轨分离（2026-09-12）────────────────────────────────────────
# 修复目标：预算被 constant 锚点吃满（真机 13 条 21,936 字符 ≫ 预算 8,000）导致语义召回
# 第一条就被 break、召回事实上全废（dropped 逐轮 7→24），注入集在「最后一个锚点入场后」
# 被冻结。分轨后 max_chars 语义 = 「召回段上限」，且召回不登记进稳定序列以免被冻结。


def test_锚点不占召回预算_超预算锚点下召回仍能注入(monkeypatch):
    anchor = wb.Entry(content="常驻机制条目" * 1500, constant=True, source_index=0, uid="1")
    recall = wb.Entry(content="本轮语义召回条目", constant=False, source_index=1, uid="2")
    entries = [anchor, recall]
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [recall.content])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "recall-budget"
    try:
        r1 = wb.assemble_selection_stable(
            "r", entries, "", None, session_key=key, max_chars=8000)
        seq = list(wb._SESSION_SEQ[key])
        r2 = wb.assemble_selection_stable(
            "r", entries, "", None, session_key=key, max_chars=8000)
    finally:
        wb.reset_session(key)

    assert len(anchor.content) > 8000            # 锚点单独已远超预算
    assert "常驻机制条目" in r1.text
    # ⚠ 2026-09-12 分两段：召回进 r.recall_text（调用方放尾部动态块），不进世界书块。
    assert "本轮语义召回条目" not in r1.text
    assert "本轮语义召回条目" in r1.recall_text   # 改前会被锚点吃光预算 → 一条都进不来
    assert r1.dropped == 0
    assert seq == ["i:1"]                        # 召回条目不进稳定序列（否则会被冻结）
    assert r2.recall_text == r1.recall_text
    assert r2.text == r1.text


def test_召回段独立预算_超上限只裁召回并且序列不膨胀(monkeypatch):
    anchor = wb.Entry(content="常驻机制", constant=True, source_index=0, uid="1")
    big = wb.Entry(content="召回大段" * 500, constant=False, source_index=1, uid="2")
    entries = [anchor, big]
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [big.content])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "recall-clip"
    try:
        tight = wb.assemble_selection_stable(
            "r", entries, "", None, session_key=key, max_chars=100)
        seq = list(wb._SESSION_SEQ[key])
    finally:
        wb.reset_session(key)

    assert "常驻机制" in tight.text              # 锚点不受召回预算影响
    assert "召回大段" not in tight.text
    assert tight.recall_text == ""               # 超上限的召回被裁（不占锚点段）
    assert tight.dropped == 1
    assert seq == ["i:1"]                        # 序列只含锚点 ⇒ 不再膨胀


def test_锚点段与召回段分开返回_indices仍含两段(monkeypatch):
    """契约（2026-09-12）：`text` 只放锚点段、`recall_text` 放召回段。

    理由：锚点段跨轮稳定、留在世界书块（history 之前）可复用前缀；召回段每轮重排，
    必须由调用方放进**尾部动态块**，否则它一变就把其后的历史与全部预设片段打掉
    （真机相邻轮字节 LCP 78% → 62%）。`indices` 仍含两段 —— curator 更新白名单按它取。
    """
    anchor = wb.Entry(content="常驻机制条目", constant=True, source_index=0, uid="1")
    recall = wb.Entry(content="本轮语义召回条目", constant=False, source_index=1, uid="2")
    entries = [anchor, recall]
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [recall.content])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "recall-split"
    try:
        sel = wb.assemble_selection_stable("r", entries, "", None, session_key=key)
    finally:
        wb.reset_session(key)

    assert sel.text.startswith("【世界设定（相关条目）】")
    assert "常驻机制条目" in sel.text
    assert "本轮语义召回条目" not in sel.text
    assert sel.recall_text.startswith("【世界设定·本轮召回")
    assert "本轮语义召回条目" in sel.recall_text
    assert "常驻机制条目" not in sel.recall_text
    assert sel.recall_indices == [1]
    assert sel.indices == [0, 1]


def test_无状态版预算同样只作用于召回段(monkeypatch):
    """assemble_selection 与稳定版同口径（此前从 sum(锚点) 起算 ⇒ 召回恒被裁）。"""
    anchor = wb.Entry(content="常驻机制条目" * 1500, constant=True, source_index=0)
    recall = wb.Entry(content="本轮语义召回条目", constant=False, source_index=1)
    entries = [anchor, recall]
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [recall.content])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])

    sel = wb.assemble_selection("r", entries, "", None, max_chars=8000)
    assert "常驻机制条目" in sel.text
    assert "本轮语义召回条目" in sel.text


# ── 动态区外置（2026-09-13）────────────────────────────────────────────────
# 真机三次 cached=0 的分叉点全在世界书锚点块内：①curator update 追加/替换条目末尾
# 【剧情进展·动态】区；②curator worldbook_add 新条目插进锚点段。外置后锚点段会话内
# 双冻结（内容快照 + 集合），curator 产出全部改道尾部动态块。


def test_动态区外置_首轮自带动态区的条目底座进锚点段(monkeypatch):
    """会话基线构建时：底座进锚点段、动态区直接进 dynamic_text（不进锚点段文本）。"""
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "dyn-initial"
    entries = [
        wb.Entry(content="宗门设定底座\n\n【剧情进展·动态】\n开场即有的动态",
                 constant=True, source_index=0, uid="1"),
    ]
    try:
        sel = wb.assemble_selection_stable("r", entries, "", None, session_key=key)
    finally:
        wb.reset_session(key)

    assert "宗门设定底座" in sel.text
    assert "开场即有的动态" not in sel.text
    assert "开场即有的动态" in sel.dynamic_text
    assert sel.dynamic_text.startswith("【世界设定·动态更新")


def test_中途新增constant条目进动态块不进锚点段(monkeypatch):
    """真机 11:26 实锤场景（curator add【媚体炉鼎】）：新锚点不追加进锚点段文本，
    走动态块——否则分叉点=锚点段末尾（≈15k token），仍够不到 16,384 缓存粒度。"""
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "dyn-late"
    base = [wb.Entry(content="原有常驻条目", constant=True, source_index=0, uid="1")]
    try:
        r1 = wb.assemble_selection_stable("r", base, "", None, session_key=key)
        # curator worldbook_add：新增一条 constant 条目
        grown = base + [
            wb.Entry(content="【媚体炉鼎】新增设定", constant=True, source_index=1, uid="2")]
        r2 = wb.assemble_selection_stable("r", grown, "", None, session_key=key)
        # 第三轮：新条目持续在场，锚点段依旧不动
        r3 = wb.assemble_selection_stable("r", grown, "", None, session_key=key)
    finally:
        wb.reset_session(key)

    assert "【媚体炉鼎】新增设定" not in r2.text
    assert "【媚体炉鼎】新增设定" in r2.dynamic_text
    assert "原有常驻条目" in r2.text
    # 锚点段跨轮逐字节不变（集合冻结 + 内容冻结）
    assert r2.text == r1.text
    assert r3.text == r1.text
    assert "【媚体炉鼎】新增设定" in r3.dynamic_text
    # 新条目仍进 indices（curator 更新白名单按 indices 取）
    assert 1 in r2.indices


def test_动态区取条目最新值_锚点段仍不变(monkeypatch):
    """锚点条目动态区被 curator 二次更新：dynamic_text 取最新版，锚点段依旧逐字节不变。"""
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "dyn-latest"
    try:
        v1 = [wb.Entry(content="角色卡·甲底座\n\n【剧情进展·动态】\n第一版进展",
                       constant=True, source_index=0, uid="1")]
        r1 = wb.assemble_selection_stable("r", v1, "", None, session_key=key)
        v2 = [wb.Entry(content="角色卡·甲底座\n\n【剧情进展·动态】\n第二版进展",
                       constant=True, source_index=0, uid="1")]
        r2 = wb.assemble_selection_stable("r", v2, "", None, session_key=key)
    finally:
        wb.reset_session(key)

    assert r2.text == r1.text
    assert "第一版进展" in r1.dynamic_text
    assert "第二版进展" in r2.dynamic_text
    assert "第一版进展" not in r2.dynamic_text


def test_会话状态持久化_重启后锚点段与late集恢复(tmp_path, monkeypatch):
    """state_path 落盘：进程重启（内存清空）后从盘恢复——锚点段不重排、late 条目仍走
    动态块，前缀跨重启延续（否则重启即 miss 一轮且服务端旧前缀作废）。"""
    monkeypatch.setattr(wb, "_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(wb, "_sparse_retrieve", lambda *a, **k: [])
    key = "dyn-persist"
    state_path = str(tmp_path / "worldbook_session.json")
    base = [wb.Entry(content="原有常驻条目", constant=True, source_index=0, uid="1")]
    grown = base + [
        wb.Entry(content="【媚体炉鼎】新增设定", constant=True, source_index=1, uid="2")]
    try:
        r1 = wb.assemble_selection_stable(
            "r", base, "", None, session_key=key, state_path=state_path)
        r2 = wb.assemble_selection_stable(
            "r", grown, "", None, session_key=key, state_path=state_path)
    finally:
        pass  # 故意不 reset_session：模拟进程重启（内存态保留在这里会影响下方，需手动清）

    # 模拟重启：清空全部内存会话态
    wb._SESSION_SEQ.clear()
    wb._SESSION_ANCHOR.clear()
    wb._SESSION_CONTENT.clear()
    wb._SESSION_LATE.clear()
    # 读盘断言必须在 reset 之前（reset_session 带 state_path 会删持久化文件）
    import json as _json
    saved = _json.loads((tmp_path / "worldbook_session.json").read_text(encoding="utf-8"))
    assert saved["seq"] == ["i:1", "i:2"]
    assert "i:2" in saved["late"]                     # 中途新锚点记账
    assert saved["content"]["i:1"] == "原有常驻条目"   # 底座快照
    try:
        r3 = wb.assemble_selection_stable(
            "r", grown, "", None, session_key=key, state_path=state_path)
    finally:
        wb.reset_session(key, state_path=state_path)
    assert not (tmp_path / "worldbook_session.json").exists()  # reset 顺带删文件
    # 恢复后锚点段与重启前逐字节一致；late 条目仍走动态块
    assert r3.text == r2.text == r1.text
    assert "【媚体炉鼎】新增设定" not in r3.text
    assert "【媚体炉鼎】新增设定" in r3.dynamic_text


def test_reset_session_带state_path时删除持久化文件(tmp_path):
    key = "dyn-reset-file"
    state_path = tmp_path / "worldbook_session.json"
    state_path.write_text("{}", encoding="utf-8")
    wb.reset_session(key, state_path=str(state_path))
    assert not state_path.exists()
