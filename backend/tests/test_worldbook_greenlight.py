"""世界书绿灯决策（P1，2026-10-22）：条目元数据兜底（skillify）+ 每轮绿灯选择。

测试口径：
- `_llm_call` 是唯一 LLM 出口，全部 monkeypatch 掉，**绝不真发 HTTP**；
- 写回一律落 tmp_path，绝不碰生产 worldbook.json；
- 决策/兜底失败不得抛（调用方靠 ran/error 判断退回 BM25）。
"""
import dataclasses
import json
import re
from pathlib import Path

from app.services import worldbook as wb
from app.services import worldbook_greenlight as wbg


def _entry(content="角色卡正文", **kw):
    """默认带**齐全元数据**的条目（绿灯候选）；meta_src_hash 按正文自动算。"""
    fields = {"content": content, "constant": False, "comment": "角色卡·武藏",
              "keys": ["武藏"], "description": "武藏用途", "trigger_when": "涉及武藏时",
              "tk": 100, "meta_src_hash": ""}
    fields.update(kw)
    e = wb.Entry(
        content=fields["content"], constant=fields["constant"], comment=fields["comment"],
        keys=fields["keys"], description=fields["description"],
        trigger_when=fields["trigger_when"], tk=fields["tk"],
        meta_src_hash=fields["meta_src_hash"],
    )
    if not e.meta_src_hash:
        e = dataclasses.replace(e, meta_src_hash=wbg.static_base_hash(e))
    return e


def _bare(content="无元数据正文", **kw):
    """无元数据条目（绿灯候选之外；兜底待补）。"""
    fields = {"content": content, "comment": "无元数据条目", "keys": ["x"]}
    fields.update(kw)
    return wb.Entry(content=fields["content"], constant=False, comment=fields["comment"],
                    keys=fields["keys"])


# ── needs_metadata（幂等判定）───────────────────────────────────────────────

def test_needs_metadata_缺字段或静态底座变过才补():
    done = _entry(description="用途", trigger_when="时机", tk=100,
                  meta_src_hash=wbg.static_base_hash(_entry()))
    assert wbg.needs_metadata(_bare()) is True                      # 全缺
    assert wbg.needs_metadata(_bare(description="用途")) is True    # 缺 trigger_when
    assert wbg.needs_metadata(_bare(description="用途", trigger_when="时机", tk=0)) is True
    assert wbg.needs_metadata(done) is False                        # 齐全且 hash 匹配
    stale = _entry(description="用途", trigger_when="时机", tk=100, meta_src_hash="旧hash")
    assert wbg.needs_metadata(stale) is True                        # 静态底座变过
    assert wbg.needs_metadata(_entry(constant=True)) is False       # constant 不补


def test_needs_metadata_动态区变化不算变():
    """curator 只追加【剧情进展·动态】区 ⇒ 静态底座 hash 不变 ⇒ 不重生成。"""
    base = "静态底座内容"
    with_dyn = base + "\n\n【剧情进展·动态】\n第 3 轮：武藏抵达港区"
    e = _entry(content=with_dyn, description="用途", trigger_when="时机", tk=len(with_dyn),
               meta_src_hash=wbg.static_base_hash(_entry(content=base)))
    assert wbg.needs_metadata(e) is False


# ── 决策上下文 ─────────────────────────────────────────────────────────────

def test_decision_context_取最近两条全文不截断():
    """长消息开头的实体必须可见（历史漏选根因：检索窗口只取尾 1800 字）。"""
    long_head = "武藏" + "。" * 5000                       # 实体在 5000+ 字消息的开头
    history = [
        {"role": "user", "content": "很早以前的话"},
        {"role": "assistant", "content": long_head},
        {"role": "user", "content": "上一轮输入"},
    ]
    ctx_text = wbg.decision_context(history, "本轮输入")
    assert "武藏" in ctx_text                                # 开头实体不被截断
    assert "本轮输入" in ctx_text
    assert "很早以前的话" not in ctx_text                    # 只取最近 2 条
    assert "上一轮输入" in ctx_text


# ── 绿灯选择 ───────────────────────────────────────────────────────────────

def _patch_llm(monkeypatch, reply):
    calls = []

    def fake(base_url, api_key, model, messages):
        calls.append({"base_url": base_url, "messages": messages})
        return reply if isinstance(reply, str) else reply(calls)

    monkeypatch.setattr(wbg, "_llm_call", fake)
    return calls


def test_绿灯选择_严格JSON与越界index拒绝(monkeypatch):
    entries = [_entry(), _entry(comment="角色卡·信浓", keys=["信浓"], content="信浓正文")]
    _patch_llm(monkeypatch, '{"picked":[{"index":99,"reason":"越界"},'
                            '{"index":0,"reason":"合法"},{"index":0,"reason":"重复"}]}')
    res = wbg.greenlight_select(entries, [{"role": "user", "content": "武藏来访"}], "武藏来访",
                                base_url="b", api_key="k", model="m")
    assert res.ran is True and res.error == ""
    assert res.positions == [0]                 # 越界被拒、重复去重
    assert res.reasons[0] == "合法"
    # 整体不是 JSON ⇒ 报错不抛
    _patch_llm(monkeypatch, "我不是JSON")
    res2 = wbg.greenlight_select(entries, [], "q", base_url="b", api_key="k", model="m")
    assert res2.ran is True and res2.error and res2.positions == []


def test_绿灯选择_无元数据条目不进候选(monkeypatch):
    entries = [_entry(), _bare(comment="无元数据条目")]
    calls = _patch_llm(monkeypatch, '{"picked":[]}')
    wbg.greenlight_select(entries, [{"role": "user", "content": "武藏来访"}], "武藏来访",
                          base_url="b", api_key="k", model="m")
    prompt = calls[0]["messages"][-1]["content"]
    assert "共 1 条" in prompt                    # 无元数据条目被排除
    assert "无元数据条目" not in prompt


def test_绿灯选择_预算封顶丢末尾(monkeypatch):
    entries = [_entry(tk=6000, description="甲用途", trigger_when="甲时机"),
               _entry(comment="角色卡·信浓", keys=["信浓"], content="乙正文", tk=6000,
                      description="乙用途", trigger_when="乙时机")]
    _patch_llm(monkeypatch, '{"picked":[{"index":0,"reason":"a"},{"index":1,"reason":"b"}]}')
    res = wbg.greenlight_select(entries, [], "q", base_url="b", api_key="k",
                                model="m", max_chars=8000)
    assert res.positions == [0]                   # 第二条超预算被丢
    assert res.dropped == 1
    assert res.chars == 6000


def test_绿灯选择_无凭证不调LLM(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("无凭证不应调 LLM")
    monkeypatch.setattr(wbg, "_llm_call", _boom)
    res = wbg.greenlight_select([_entry()], [], "q", base_url="", api_key="", model="")
    assert res.ran is False and res.positions == []


def test_绿灯选择_LLM失败带error不抛(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("网关 502")
    monkeypatch.setattr(wbg, "_llm_call", _boom)
    res = wbg.greenlight_select([_entry()], [{"role": "user", "content": "x"}], "q",
                                base_url="b", api_key="k", model="m")
    assert res.ran is True and res.error and res.positions == []


def test_绿灯选择_选空是合法结果(monkeypatch):
    _patch_llm(monkeypatch, "```json\n{\"picked\":[]}\n```")
    res = wbg.greenlight_select([_entry()], [{"role": "user", "content": "x"}], "q",
                                base_url="b", api_key="k", model="m")
    assert res.ran is True and res.error == "" and res.positions == []


# ── 元数据兜底（skillify + 写回）───────────────────────────────────────────

def test_skillify_按输入tk覆盖且解析围栏(monkeypatch):
    todo = [_entry(), _entry(comment="角色卡·信浓", keys=["信浓"], content="信浓正文" * 3)]

    def fake(_b, _k, _m, messages):
        assert "items" in messages[0]["content"]
        return ("```json\n{\"items\":["
                "{\"index\":0,\"description\":\"甲用途\",\"triggerWhen\":\"甲时机\",\"tk\":999},"
                "{\"index\":1,\"description\":\"乙用途\",\"triggerWhen\":\"乙时机\",\"tk\":1}"
                "]}\n```")

    monkeypatch.setattr(wbg, "_llm_call", fake)
    out = wbg.skillify_entries(todo, base_url="b", api_key="k", model="m")
    assert out[0]["tk"] == len(todo[0].content)        # tk 以输入为准，防模型改写
    assert out[0]["description"] == "甲用途"
    assert out[1]["trigger_when"] == "乙时机"
    assert out[0]["meta_src_hash"] == wbg.static_base_hash(todo[0])


def test_绿灯选择_全部index越界才算失败(monkeypatch):
    """一条合法不剩 ⇒ 整体失败（退回 BM25 安全网）；混着合法 ⇒ 只丢越界条。"""
    entries = [_entry(), _entry(comment="角色卡·信浓", keys=["信浓"], content="信浓正文")]
    _patch_llm(monkeypatch, '{"picked":[{"index":99,"reason":"越界"},{"index":7,"reason":"也越界"}]}')
    res = wbg.greenlight_select(entries, [], "q", base_url="b", api_key="k", model="m")
    assert res.ran is True and res.error and res.positions == []
    _patch_llm(monkeypatch, '{"picked":[{"index":99,"reason":"越界"},{"index":1,"reason":"合法"}]}')
    res2 = wbg.greenlight_select(entries, [], "q", base_url="b", api_key="k", model="m")
    assert res2.error == "" and res2.positions == [1]


def test_skillify_批次失败不影响其他批次(monkeypatch):
    todo = [_entry(content=f"条目{i}") for i in range(3)]
    state = {"n": 0}

    def fake(_b, _k, _m, messages):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("首批炸")
        # 只回本批问到的序号（从 prompt 里抓 [N] 标记）
        asked = [int(x) for x in re.findall(r"\[(\d+)\] 名称", messages[-1]["content"])]
        return json.dumps({"items": [
            {"index": i, "description": f"用途{i}", "triggerWhen": f"时机{i}", "tk": 7}
            for i in asked]}, ensure_ascii=False)

    monkeypatch.setattr(wbg, "_llm_call", fake)
    out = wbg.skillify_entries(todo, base_url="b", api_key="k", model="m", batch=2)
    assert out[0] is None and out[1] is None            # 失败批次留空
    assert out[2] is not None                           # 后续批次照常
    assert out[2]["description"] == "用途2"


def test_skillify_单条坏元素不废整批(monkeypatch):
    """模型偶发回坏元素/越界序号 ⇒ 只丢该条，同批合法条目照常收。"""
    todo = [_entry(), _entry(comment="角色卡·信浓", keys=["信浓"], content="信浓正文")]
    monkeypatch.setattr(wbg, "_llm_call", lambda *_a, **_k: json.dumps({"items": [
        "我不是对象",
        {"index": 99, "description": "越界", "triggerWhen": "x", "tk": 1},
        {"index": 0, "description": "甲用途", "triggerWhen": "甲时机", "tk": 1},
        {"index": 1, "description": "", "triggerWhen": "乙时机", "tk": 1},
    ]}, ensure_ascii=False))
    out = wbg.skillify_entries(todo, base_url="b", api_key="k", model="m")
    assert out[0] is not None and out[0]["description"] == "甲用途"
    assert out[1] is None                              # 空 description 的那条被丢


def _write_book(path: Path, entries: list[dict], *, crlf=True) -> None:
    text = json.dumps({"entries": entries}, ensure_ascii=False, indent=2)
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))


def test_backfill_写回只加四键且保留格式(tmp_path, monkeypatch):
    book = {"entries": [
        {"id": 7, "comment": "角色卡·武藏", "keys": ["武藏"], "content": "武藏正文",
         "position": 0, "extensions": {"depth": 1}},
        {"id": 8, "comment": "角色卡·信浓", "keys": ["信浓"], "content": "信浓正文",
         "constant": False},
    ]}
    p = tmp_path / "worldbook.json"
    _write_book(p, book["entries"])
    raw_before = p.read_bytes()
    entries = wb.parse_entries(json.loads(raw_before.decode("utf-8")))
    assert all(wbg.needs_metadata(e) for e in entries)
    monkeypatch.setattr(wbg, "_llm_call", lambda *_a, **_k: json.dumps({"items": [
        {"index": i, "description": f"用途{i}", "triggerWhen": f"时机{i}", "tk": 99}
        for i in range(2)]}, ensure_ascii=False))
    n = wbg.backfill_repo_metadata(p, entries, base_url="b", api_key="k", model="m")
    assert n == 2
    after = json.loads(p.read_bytes().decode("utf-8"))
    item = after["entries"][0]
    assert item["description"] == "用途0" and item["trigger_when"] == "时机0"
    assert item["tk"] == len("武藏正文")          # tk 以输入为准，防模型改写（LLM 回的 99 被覆盖）
    assert item["meta_src_hash"]
    assert item["position"] == 0 and item["extensions"] == {"depth": 1}   # 其余字段不动
    raw_after = p.read_bytes()
    assert raw_after.count(b"\r\n") == raw_after.count(b"\n")             # CRLF 保持
    assert not raw_after.endswith(b"\n")                                  # 无尾随换行
    # 幂等：再跑一次无需补
    entries2 = wb.parse_entries(after)
    assert not any(wbg.needs_metadata(e) for e in entries2)
    assert wbg.backfill_repo_metadata(p, entries2, base_url="b", api_key="k", model="m") == 0


def test_backfill_按uid定位不串条目(tmp_path, monkeypatch):
    """同名 comment 两条（脏数据）：uid 命中正确那条，另一条不动。"""
    book = {"entries": [
        {"id": "", "comment": "人物·雷纳", "keys": [], "content": "重复条目"},
        {"id": 66, "comment": "人物·雷纳", "keys": ["雷纳"], "content": "正式条目"},
    ]}
    p = tmp_path / "worldbook.json"
    _write_book(p, book["entries"])
    entries = wb.parse_entries(json.loads(p.read_bytes().decode("utf-8")))
    target = next(e for e in entries if e.uid == "66")
    monkeypatch.setattr(wbg, "_llm_call", lambda *_a, **_k: json.dumps({"items": [
        {"index": 0, "description": "巡察使用途", "triggerWhen": "稽查时", "tk": 5}]},
        ensure_ascii=False))
    n = wbg.backfill_repo_metadata(p, [target], base_url="b", api_key="k", model="m")
    assert n == 1
    after = json.loads(p.read_bytes().decode("utf-8"))
    assert "description" not in after["entries"][0]        # uid 空的那条不匹配
    assert after["entries"][1]["description"] == "巡察使用途"


def test_backfill_缺凭证不写文件(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("缺凭证不应调 LLM")
    monkeypatch.setattr(wbg, "_llm_call", _boom)
    p = tmp_path / "worldbook.json"
    _write_book(p, [{"comment": "角色卡·武藏", "keys": ["武藏"], "content": "正文"}])
    entries = wb.parse_entries(json.loads(p.read_bytes().decode("utf-8")))
    before = p.read_bytes()
    assert wbg.backfill_repo_metadata(p, entries, base_url="", api_key="", model="") == 0
    assert p.read_bytes() == before


# ── schedule_index 元数据兜底探测 ──────────────────────────────────────────

def test_schedule_index_元数据待补也起线程(monkeypatch):
    """索引已满（无 missing/stale）但条目缺元数据 ⇒ 仍起后台线程跑兜底。"""
    pending, backfills = [], []

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            pending.append(self.target)

    cfg = wb.rag_backend.EmbedConfig(base_url="http://embed", embed_model="model")
    monkeypatch.setattr(wb.threading, "Thread", DeferredThread)
    monkeypatch.setattr(wb, "ensure_indexed", lambda *_args: True)
    monkeypatch.setattr(wb, "_index_delta", lambda *_args: ([], [], []))   # 索引已满
    entry = wb.Entry("条目", False)                                          # 无元数据 ⇒ 待补
    assert wb.schedule_index("repo-gl", [entry], cfg,
                             on_backfill=lambda: backfills.append(1)) is True
    assert backfills == []
    # 线程在飞 ⇒ 去重生效（第二发直接 False，不排队）
    assert wb.schedule_index("repo-gl", [entry], cfg,
                             on_backfill=lambda: backfills.append(1)) is False
    pending.pop()()
    assert backfills == [1]
    # 兜底没真写元数据（本测试的闭包只记账）⇒ 仍待补 ⇒ 还能再起线程
    assert wb.schedule_index("repo-gl", [entry], cfg,
                             on_backfill=lambda: backfills.append(1)) is True
    pending.pop()()
    assert backfills == [1, 1]


def test_schedule_index_无待补且无索引增量则不起线程(monkeypatch):
    started = []

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            started.append(self.target)

    cfg = wb.rag_backend.EmbedConfig(base_url="http://embed", embed_model="model")
    monkeypatch.setattr(wb.threading, "Thread", DeferredThread)
    monkeypatch.setattr(wb, "ensure_indexed", lambda *_args: True)
    monkeypatch.setattr(wb, "_index_delta", lambda *_args: ([], [], []))
    done = _entry(description="用途", trigger_when="时机", tk=10,
                  meta_src_hash=wbg.static_base_hash(_entry()))
    assert wb.schedule_index("repo-gl-2", [done], cfg,
                             on_backfill=lambda: started.append("bf")) is False
    assert started == []


# ── ST 式扩展：递归激活 + 配额兜底（2026-10-22，对标 ST Recursion / Min Activations）────

def _recursion_entries():
    return [
        _entry(comment="E6 出使重樱", keys=["出使重樱"], content="出使重樱，武藏设美人计相迎。"),
        _entry(comment="角色卡·武藏", keys=["武藏"], content="武藏角色卡正文。"),
        _entry(comment="角色卡·信浓", keys=["信浓"], content="信浓角色卡正文。"),
        _entry(comment="全局机制·A", keys=["机制"], content="机制正文", constant=True),
    ]


def test_递归激活_选中条目正文提到的条目连带激活(monkeypatch):
    """ST 递归等价物：选中条目**正文**提到别的条目 key ⇒ 连带激活（确定性、零 LLM）。"""
    entries = _recursion_entries()
    _patch_llm(monkeypatch, '{"picked":[{"index":0,"reason":"出使"}]}')
    res = wbg.greenlight_select(entries, [{"role": "user", "content": "继续推进"}], "继续推进",
                                base_url="b", api_key="k", model="m")
    assert res.positions[0] == 0
    assert res.recursion == [1] and 1 in res.positions     # 武藏被递归带进
    assert res.reasons[1] == "递归"
    assert 2 not in res.positions                          # 信浓与正文无关
    assert 3 not in res.positions                          # constant 不进召回轨


def test_递归激活_不重选已选条目(monkeypatch):
    entries = _recursion_entries()
    _patch_llm(monkeypatch, '{"picked":[{"index":0,"reason":"a"},{"index":1,"reason":"b"}]}')
    res = wbg.greenlight_select(entries, [], "q", base_url="b", api_key="k", model="m")
    assert res.positions == [0, 1]
    assert res.recursion == []                             # 已选的武藏不重复计入


def test_递归激活_预算不足则丢弃(monkeypatch):
    entries = [
        _entry(comment="大条目", keys=["大"], content="大" * 100 + "提到武藏",
               description="大用途", trigger_when="大时机", tk=7000),
        _entry(comment="角色卡·武藏", keys=["武藏"], content="武" * 6000,
               description="武用途", trigger_when="武时机", tk=6000),
    ]
    _patch_llm(monkeypatch, '{"picked":[{"index":0,"reason":"a"}]}')
    res = wbg.greenlight_select(entries, [], "q", base_url="b", api_key="k",
                                model="m", max_chars=8000)
    assert res.positions == [0]
    assert res.recursion == [] and res.dropped >= 1        # 递归条目吃不下 ⇒ 丢


def test_配额兜底_少于下限时宽窗补齐(monkeypatch):
    """LLM 只选 1 条；「埃姆登」只出现在**决策窗口外**的旧消息里 ⇒ 宽窗 keyword 补齐。"""
    entries = [
        _entry(comment="甲", keys=["甲"], content="甲正文", description="甲用途", trigger_when="甲时机"),
        _entry(comment="角色卡·埃姆登", keys=["埃姆登"], content="埃姆登正文",
               description="埃用途", trigger_when="埃时机"),
    ]
    history = [
        {"role": "assistant", "content": "很久以前：埃姆登登场。"},
        {"role": "assistant", "content": "中间消息一。"},
        {"role": "assistant", "content": "中间消息二。"},
        {"role": "user", "content": "最近输入。"},
    ]
    _patch_llm(monkeypatch, '{"picked":[{"index":0,"reason":"甲"}]}')
    res = wbg.greenlight_select(entries, history, "最近输入", base_url="b", api_key="k", model="m")
    assert res.positions[0] == 0
    assert 1 in res.topped_up and res.reasons[1] == "配额兜底"


def test_配额兜底_达标不触发(monkeypatch):
    entries = _recursion_entries()
    _patch_llm(monkeypatch, '{"picked":[{"index":0,"reason":"a"},{"index":2,"reason":"b"}]}')
    res = wbg.greenlight_select(entries, [], "q", base_url="b", api_key="k", model="m")
    assert res.topped_up == [] and len(res.positions) >= 2
