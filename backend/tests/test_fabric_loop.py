"""智能编造自由循环单测：工具调用→结果回填→完成；审批暂停；失败换方案。"""
from __future__ import annotations

import json

from app.services import capability_sandbox, fabric_loop


def _fake_chat(decisions):
    calls = {"n": 0}

    def fake(base, key, model, system, user, **kw):
        idx = calls["n"]
        calls["n"] += 1
        return json.dumps(decisions[min(idx, len(decisions) - 1)], ensure_ascii=False)
    return fake


def test_自由循环调用工具后完成(tmp_path):
    # 模型第 1 步调 file.list_dir，看到结果后第 2 步宣布完成
    # 路径用 tmp_path：CI 是 Linux runner，不能依赖 Windows 盘符存在
    chat = _fake_chat([
        {"tool": "file.list_dir", "params": {"path": str(tmp_path)}},
        {"done": True, "reply": "目录已确认，任务完成"},
    ])
    outcome = fabric_loop.run_loop(
        intent="看看目录", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "done"
    assert outcome.reply == "目录已确认，任务完成"
    assert len(outcome.steps) == 1
    assert outcome.steps[0]["ok"] is True


def test_approval模式durable工具暂停():
    chat = _fake_chat([
        {"tool": "file.write_text", "params": {"path": "D:/tmp/x.txt", "content": "x"}},
    ])
    outcome = fabric_loop.run_loop(
        intent="写文件", configured_models={"chat", "image"},
        access_mode=capability_sandbox.ACCESS_APPROVAL, lease_id="",
        output_dir="D:/tmp",
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "awaiting_approval"
    assert outcome.pending_tool == "file.write_text"


def test_full模式durable工具直接执行(tmp_path):
    lease = capability_sandbox.grant(
        "fabric:test", [], mode=capability_sandbox.ACCESS_FULL)
    target = tmp_path / "note.txt"
    chat = _fake_chat([
        {"tool": "file.write_text",
         "params": {"path": str(target), "content": "自由模式写入"}},
        {"done": True, "reply": "写好了"},
    ])
    outcome = fabric_loop.run_loop(
        intent="写个文件", configured_models={"chat", "image"},
        access_mode=capability_sandbox.ACCESS_FULL, lease_id=lease["id"],
        output_dir=str(tmp_path),
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is True
    assert target.read_text(encoding="utf-8") == "自由模式写入"
    capability_sandbox.revoke(lease["id"])




# ── 2026-09-06 A 方案：分卷读上下文治理 ─────────────────────────────────────


def _read_pair(path: str, offset: int, text: str, total: int = 300000) -> list[dict]:
    """构造一轮 file.read_text 的 assistant 调用 + user 结果对。"""
    result = json.dumps({"path": path, "text": text, "offset": offset,
                         "total": total, "chars_read": len(text), "has_more": True},
                        ensure_ascii=False)
    return [
        {"role": "assistant", "content": json.dumps(
            {"tool": "file.read_text", "params": {"path": path, "offset": offset,
                                                  "max_chars": 20000}},
            ensure_ascii=False)},
        {"role": "user", "content": json.dumps(
            {"tool_result": result}, ensure_ascii=False)},
    ]


def test_compress_read_chunks_旧卷压缩最新保留():
    """A 方案 × P4(append-only)：旧卷压缩为摘要、最新卷保留原文。

    P4（2026-09-12）口径变化：压缩不再**就地改写**历史 tool_result，而是尾部追加
    一条替换声明；「压缩生效」体现在**投影视图**（`_project_messages`）里，
    「原文不丢」体现在**原始日志**里——两者必须同时成立，缺一条就是回退。
    """
    path = "D:/novel.txt"
    messages = (
        _read_pair(path, 0, "卷一内容" * 1000)
        + _read_pair(path, 20000, "卷二内容" * 1000)
        + _read_pair(path, 40000, "卷三内容" * 1000)
        + [{"role": "user", "content": "继续"}]
    )
    raw_head = [dict(m) for m in messages]  # 压缩前逐条快照

    fabric_loop._compress_read_chunks(messages)

    # ① 原始日志 append-only：原 7 条一字不差，压缩只追加声明（2 条旧卷 → 2 条声明）
    assert messages[:len(raw_head)] == raw_head, "就地改写了历史消息（违反 append-only）"
    assert len(messages) == len(raw_head) + 2

    # ② 投影视图：旧卷被摘要替换、最新卷原文保留、声明条目不进视图
    surface = fabric_loop._project_messages(messages)
    assert len(surface) == len(raw_head), "投影后条数应回到压缩前（声明被摘掉）"
    assert [m["role"] for m in surface] == [m["role"] for m in raw_head], "角色/配对结构被破坏"
    assert fabric_loop._READ_SUMMARY_MARK in surface[1]["content"]
    assert fabric_loop._READ_SUMMARY_MARK in surface[3]["content"]
    assert "卷一内容" not in surface[1]["content"]
    assert "卷二内容" not in surface[3]["content"]
    assert "卷三内容" in surface[5]["content"]  # 最新卷(offset=40000)原文保留

    # ③ 投影必须真的变小——否则「压缩」就退化成纯留痕
    assert (len(json.dumps(surface, ensure_ascii=False))
            < len(json.dumps(raw_head, ensure_ascii=False)))


def test_compress_read_chunks_幂等():
    """幂等：再跑一次既不再追加声明，投影视图也逐字节不变。"""
    path = "D:/novel.txt"
    messages = _read_pair(path, 0, "卷一内容" * 1000) + _read_pair(path, 20000, "卷二内容" * 1000)
    fabric_loop._compress_read_chunks(messages)
    first_log = json.dumps(messages, ensure_ascii=False)
    first_surface = json.dumps(fabric_loop._project_messages(messages), ensure_ascii=False)
    assert fabric_loop._READ_SUMMARY_MARK in first_surface
    fabric_loop._compress_read_chunks(messages)  # 再跑一次
    assert json.dumps(messages, ensure_ascii=False) == first_log  # 日志不再增长
    assert json.dumps(fabric_loop._project_messages(messages), ensure_ascii=False) == first_surface


def test_compress_read_chunks_非分卷读不动():
    """只读一卷(无递增 offset)或非 read_text 调用不压缩，且不追加任何声明。"""
    path = "D:/novel.txt"
    messages = _read_pair(path, 0, "单卷内容" * 1000) + [
        {"role": "assistant", "content": json.dumps(
            {"tool": "doc.create_repo", "params": {"rel_path": "x.md"}}, ensure_ascii=False)},
        {"role": "user", "content": json.dumps({"tool_result": "ok"}, ensure_ascii=False)},
    ]
    fabric_loop._compress_read_chunks(messages)
    assert len(messages) == 4, "不该产生替换声明"
    surface = fabric_loop._project_messages(messages)
    assert fabric_loop._READ_SUMMARY_MARK not in surface[1]["content"]  # 单卷不压
    assert "ok" in surface[3]["content"]  # 非 read_text 不动


def _upsert_pair(n: int) -> list[dict]:
    """构造一轮 worldbook.upsert_repo 的 assistant 决策 + user 结果对。"""
    return [
        {"role": "assistant", "content": json.dumps(
            {"tool": "worldbook.upsert_repo",
             "params": {"entries": [{"comment": f"条目{n}"}]}}, ensure_ascii=False)},
        {"role": "user", "content": json.dumps({"tool_result": "ok"}, ensure_ascii=False)},
    ]


def test_compress_upsert_results_旧条目压缩留最近三条():
    """P4：旧 upsert 决策的 entries 换摘要（投影生效），原始日志保留（append-only）。"""
    messages = [{"role": "system", "content": "s"}]
    for n in range(5):
        messages += _upsert_pair(n)
    raw_head = [dict(m) for m in messages]

    fabric_loop._compress_upsert_results(messages)

    assert messages[:len(raw_head)] == raw_head, "就地改写了历史 assistant 消息"
    assert len(messages) == len(raw_head) + 2, "5 条留最近 3 条 → 压 2 条、追加 2 条声明"

    surface = fabric_loop._project_messages(messages)
    assert len(surface) == len(raw_head)
    for idx in (1, 3):  # 最早两条 upsert 决策 → 已压缩
        assert "UPSERT_ENTRIES" in surface[idx]["content"]
        assert '"comment"' not in surface[idx]["content"], "条目明细没收进摘要"
    for idx in (5, 7, 9):  # 最近三条 → 原文保留
        assert "UPSERT_ENTRIES" not in surface[idx]["content"]
        assert '"comment"' in surface[idx]["content"]


def test_surface_replace_保留角色与配对结构():
    """替换声明只换单条内容、不改 role/位置 → tool_call 与 tool_result 的配对不破。"""
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": '{"tool":"X","params":{"entries":[1]}}'},
        {"role": "user", "content": '{"tool_result":"ok"}'},
    ]
    fabric_loop._append_surface_replace(messages, 1, {
        "role": "assistant",
        "content": '{"tool":"X","params":{"entries":"UPSERT_ENTRIES 已写 1 条：条目0"}}'})

    assert len(messages) == 4, "原始三条 + 一条追加声明"
    surface = fabric_loop._project_messages(messages)
    assert [m["role"] for m in surface] == ["system", "assistant", "user"]
    assert "UPSERT_ENTRIES" in surface[1]["content"]
    assert surface[2]["content"] == '{"tool_result":"ok"}', "结果条目被牵连改动了"


def test_project_messages_无声明时原样返回():
    """没有压缩声明时投影必须零改动（不能顺手改 list/丢消息）。"""
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "u"}]
    assert fabric_loop._project_messages(messages) == messages


# ── 2026-09-06 B 方案：决策容错（reply 即完成） ─────────────────────────────


def test_决策没调工具但给reply视为完成():
    """B 方案：上下文爆炸下模型决策退化(无 tool 无 done 但有 reply) → 按 done 兜底，不白跑。"""
    chat = _fake_chat([
        {"reply": "全书已读完，正在归纳机制与角色。完成情况总结如下……"},
    ])
    outcome = fabric_loop.run_loop(
        intent="读小说", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "done"
    assert "全书已读完" in outcome.reply




# ── 2026-09-06 B 方案：敏感词拦截（网关 400 兜底） ──────────────────────────


def test_nsfw_blocked_params_命中与放行():
    assert fabric_loop._nsfw_blocked_params({"content": "女尊采补与男畜逆袭的机制"}) is None  # 设定化命名安全
    assert fabric_loop._nsfw_blocked_params({"content": "肉棒插入小穴"}) is not None  # 露骨词拦截
    assert fabric_loop._nsfw_blocked_params({"entries": [{"content": "灌精受孕与洗脑调教"}]}) is None
    assert fabric_loop._nsfw_blocked_params({"entries": [{"content": "含精液描写"}]}) is not None  # 嵌套列表也查
    assert fabric_loop._nsfw_blocked_params({"path": "D:/女奴.txt"}) is None  # 路径类不误伤


def test_写内容能力敏感词拦截回填重写(tmp_path):
    """B 方案：doc.create_repo content 含敏感词 → 不执行 → 回填 → 模型改写成功。"""
    from app.services import capability_sandbox as _sb
    lease = _sb.grant("fabric:nsfw-test", [], mode=_sb.ACCESS_FULL)
    try:
        import json as _json
        calls = {"n": 0}
        def fake(base, key, model, system, user, **kw):
            idx = calls["n"]
            calls["n"] += 1
            if idx == 0:
                return _json.dumps({"tool": "doc.create_repo",
                                    "params": {"base": str(tmp_path), "rel_path": "卡纲.md",
                                               "content": "角色黛绮丝与戴茂肉棒交合"}})
            return _json.dumps({"done": True, "reply": "卡纲已改写为设定化条目"})
        outcome = fabric_loop.run_loop(
            intent="写卡纲", configured_models={"chat", "image"},
            access_mode=_sb.ACCESS_FULL, lease_id=lease["id"], output_dir=str(tmp_path),
            chat_base="", chat_key="", chat_model="m", chat_fn=fake,
            max_steps=6,
        )
        assert outcome.status == "done"
        assert outcome.steps[0]["ok"] is False  # 第一次被拦截
        assert "敏感词" in outcome.steps[0]["error"]
        assert len(outcome.steps) == 1  # 拦截不算成功步
    finally:
        _sb.revoke(lease["id"])




def test_写内容敏感词路由本地改写成功放行(tmp_path, monkeypatch):
    """混合模式（2026-09-06 用户定案）：云端输出含敏感词 → 交本地模型改写 → 改写干净放行落盘，
    云端继续下一步（本地改写不算失败步）。"""
    from app.services import capability_sandbox as _sb

    # 本地改写成功：把露骨内容转成设定化条目
    def fake_local_rewrite(text, local=None):
        return "角色黛绮丝与戴茂存在主仆契约与肉体关系（设定化描述，机制条目见全局机制层）"
    monkeypatch.setattr(fabric_loop, "_local_rewrite", fake_local_rewrite)

    lease = _sb.grant("fabric:nsfw-mix", [], mode=_sb.ACCESS_FULL)
    try:
        import json as _json
        calls = {"n": 0}
        def fake(base, key, model, system, user, **kw):
            idx = calls["n"]
            calls["n"] += 1
            if idx == 0:
                return _json.dumps({"tool": "doc.create_repo",
                                    "params": {"base": str(tmp_path), "rel_path": "卡纲.md",
                                               "content": "角色黛绮丝与戴茂肉棒交合"}})
            return _json.dumps({"done": True, "reply": "完成"})
        outcome = fabric_loop.run_loop(
            intent="写卡纲", configured_models={"chat", "image"},
            access_mode=_sb.ACCESS_FULL, lease_id=lease["id"], output_dir=str(tmp_path),
            chat_base="", chat_key="", chat_model="m", chat_fn=fake,
            max_steps=6,
        )
        assert outcome.status == "done"
        assert outcome.steps[0]["ok"] is True  # 本地改写后放行执行成功
        assert len(outcome.steps) == 1
        # 落盘的是改写后的设定化内容
        written = (tmp_path / "docs" / "卡纲.md").read_text(encoding="utf-8")
        assert "主仆契约" in written
        assert "肉棒" not in written
    finally:
        _sb.revoke(lease["id"])


def test_写内容敏感词本地不可用回填重写(tmp_path, monkeypatch):
    """本地模型不可用（改写返回 None）→ 回填提示让云端重写（旧行为兜底）。"""
    from app.services import capability_sandbox as _sb

    monkeypatch.setattr(fabric_loop, "_local_rewrite", lambda text, local=None: None)

    lease = _sb.grant("fabric:nsfw-mix2", [], mode=_sb.ACCESS_FULL)
    try:
        import json as _json
        calls = {"n": 0}
        def fake(base, key, model, system, user, **kw):
            idx = calls["n"]
            calls["n"] += 1
            if idx == 0:
                return _json.dumps({"tool": "doc.create_repo",
                                    "params": {"base": str(tmp_path), "rel_path": "卡纲.md",
                                               "content": "肉棒交合描写"}})
            return _json.dumps({"done": True, "reply": "完成"})
        outcome = fabric_loop.run_loop(
            intent="写卡纲", configured_models={"chat", "image"},
            access_mode=_sb.ACCESS_FULL, lease_id=lease["id"], output_dir=str(tmp_path),
            chat_base="", chat_key="", chat_model="m", chat_fn=fake,
            max_steps=6,
        )
        assert outcome.status == "done"
        assert outcome.steps[0]["ok"] is False  # 本地不可用 → 拦截回填
        assert "敏感词" in outcome.steps[0]["error"]
    finally:
        _sb.revoke(lease["id"])


def test_决策既没工具也没reply仍报错():
    """真正的无效决策(无 tool 无 done 无 reply)仍如实报 error，不吞。"""
    chat = _fake_chat([
        {"tool": "", "done": False, "reply": ""},
    ])
    outcome = fabric_loop.run_loop(
        intent="x", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "error"




def test_每步保存checkpoint_重试资本(tmp_path):
    """2026-09-07：run_loop 每步调 checkpoint_fn(status=running)——中断后有断点可恢复。"""
    import json as _json
    calls = {"cp": 0, "n": 0}
    def fake(base, key, model, system, user, **kw):
        idx = calls["n"]
        calls["n"] += 1
        if idx == 0:
            return _json.dumps({"tool": "file.list_dir", "params": {"path": str(tmp_path)}})
        return _json.dumps({"done": True, "reply": "完成"})
    cp_records = []
    def cp_fn(step, msgs, steps, status):
        calls["cp"] += 1
        cp_records.append((step, status, len(steps)))
    outcome = fabric_loop.run_loop(
        intent="测试", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=fake,
        max_steps=6, checkpoint_fn=cp_fn,
    )
    assert outcome.status == "done"
    assert calls["cp"] >= 1  # 每步保存
    assert cp_records[0][1] == "running"


def test_工具失败回填后模型换方案():
    # 第 1 步调用不存在的能力 → 失败回填 → 第 2 步模型完成
    chat = _fake_chat([
        {"tool": "ghost.action", "params": {}},
        {"done": True, "reply": "能力不存在，我改用直接回复"},
    ])
    outcome = fabric_loop.run_loop(
        intent="测试失败换方案", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    assert outcome.status == "done"
    assert outcome.steps[0]["ok"] is False
    assert "能力清单里没有" in outcome.steps[0]["error"]


def test_步数上限():
    chat = _fake_chat([{"tool": "file.list_dir", "params": {"path": "D:/"}}])
    outcome = fabric_loop.run_loop(
        intent="永远不完成", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=3,
    )
    assert outcome.status == "step_limit"
    assert len(outcome.steps) == 3


# ── 带图自由循环（看图反推→生成文档）────────────────────────────────────────

def test_带图自由循环走多模态消息():
    # 图片必须以 image_url 内容块进首条用户消息，不得走 JSON 字符串通道
    captured = {}

    def fake_multimodal(base, key, model, messages, **kw):
        captured["messages"] = messages
        return json.dumps({"done": True, "reply": "已结合图片完成"}, ensure_ascii=False)

    def must_not_call(*_args, **_kwargs):
        raise AssertionError("带图时不得走纯文本 chat_fn 通道")

    outcome = fabric_loop.run_loop(
        intent="反推外貌并生成套装文档", history="上文：讨论了角色发型。",
        images=["data:image/png;base64,AAA"],
        configured_models={"chat"},
        chat_base="", chat_key="", chat_model="m",
        chat_fn=must_not_call, chat_messages_fn=fake_multimodal,
        max_steps=4,
    )
    assert outcome.status == "done"
    system, first_user = captured["messages"][0], captured["messages"][1]
    assert first_user["role"] == "user"
    assert [part["type"] for part in first_user["content"]] == ["text", "image_url"]
    assert "上文：讨论了角色发型。" in first_user["content"][0]["text"]
    assert first_user["content"][1]["image_url"]["url"] == "data:image/png;base64,AAA"
    assert "【附图】" in system["content"]


def test_带图自由循环读穿搭文档落盘套装文档(tmp_path):
    # 场景1端到端（模型决策 mock）：看图 → 读时尚文档 → 在作品 docs/ 落盘套装文档
    fashion = tmp_path / "时尚穿搭.md"
    fashion.write_text("## 春季\n风衣配长裙", encoding="utf-8")
    lease = capability_sandbox.grant(
        "fabric:img", [], mode=capability_sandbox.ACCESS_FULL)
    decisions = [
        {"tool": "file.read_text", "params": {"path": str(fashion)}},
        # 2026-09-10（A2）：doc.create_repo 的 rel_path 只收**文件名**（文档一律平铺在
        # docs/ 下，带目录会被拒——白名单只认恰一层 docs/*.md）。
        {"tool": "doc.create_repo", "params": {
            "base": str(tmp_path), "rel_path": "唐柚-四季穿搭.md",
            "content": "# 四季套装\n春·套一：风衣配长裙"}},
        {"done": True, "reply": "套装文档已生成"},
    ]
    calls = {"n": 0}

    def fake(base, key, model, messages, **kw):
        idx = min(calls["n"], len(decisions) - 1)
        calls["n"] += 1
        return json.dumps(decisions[idx], ensure_ascii=False)

    outcome = fabric_loop.run_loop(
        intent="看图反推外貌，结合穿搭文档生成四季套装文档",
        images=["data:image/png;base64,AAA"], output_dir=str(tmp_path),
        configured_models={"chat"}, access_mode=capability_sandbox.ACCESS_FULL,
        lease_id=lease["id"],
        chat_base="", chat_key="", chat_model="m",
        chat_messages_fn=fake, max_steps=8,
    )
    assert outcome.status == "done"
    assert len(outcome.steps) == 2 and all(s["ok"] for s in outcome.steps)
    written = tmp_path / "docs" / "唐柚-四季穿搭.md"
    assert written.is_file()
    assert "风衣配长裙" in written.read_text(encoding="utf-8")
    capability_sandbox.revoke(lease["id"])


def test_落盘域强制归一_模型传错base_repo_id被覆盖(tmp_path):
    """2026-09-07 治本：worldbook.upsert_repo 的 base/repo_id 强制取运行环境。

    实锤：模型把作品名/卡目录当 repo_id 传（base=<作品名>、repo_id=作品名），
    旧「缺省才注入」归一放行 → 新条目写到 <作品名>/worldbook.json，与真实
    快照 <repo_id>/worldbook.json 分叉。现在一律覆盖为环境值，分叉根除。
    """
    lease = capability_sandbox.grant(
        "fabric:norm", [], mode=capability_sandbox.ACCESS_FULL)
    repo_id = "repo-123"
    # 模型故意/无意传错 base 与 repo_id（把作品名当成 repo 标识）
    # 2026-09-09 清单先行门禁：先写「条目清单」文档再 upsert（否则门禁拦截）
    decisions = [
        {"tool": "doc.create_repo", "params": {
            "rel_path": "条目清单-测试.md",
            "content": "测试条目清单"}},
        {"tool": "worldbook.upsert_repo", "params": {
            "base": str(tmp_path / "错误作品目录"),
            "repo_id": "错误作品名",
            "entries": [{"comment": "归一测试条目", "content": "x" * 120}]}},
        {"done": True, "reply": "写好了"},
    ]
    chat = _fake_chat(decisions)
    outcome = fabric_loop.run_loop(
        intent="写条目", configured_models={"chat", "image"},
        access_mode=capability_sandbox.ACCESS_FULL, lease_id=lease["id"],
        output_dir=str(tmp_path), repo_id=repo_id,
        chat_base="", chat_key="", chat_model="m", chat_fn=chat,
        max_steps=6,
    )
    # 条目必须落在 <tmp>/<repo_id>/worldbook.json（环境归一），而不是错误作品目录
    snapshot = tmp_path / repo_id / "worldbook.json"
    assert snapshot.is_file(), "条目未写入环境归一的目标快照"
    text = snapshot.read_text(encoding="utf-8")
    assert "归一测试条目" in text
    assert not (tmp_path / "错误作品目录").exists()
    # 模型传错的 base/repo_id 在执行参数里已被覆盖（步骤记录可见）
    upsert = next(s for s in outcome.steps if s.get("tool") == "worldbook.upsert_repo")
    assert upsert["params"]["base"] == str(tmp_path)
    assert upsert["params"]["repo_id"] == repo_id
    capability_sandbox.revoke(lease["id"])


def test_自由循环每步usage落trace():
    """P0（2026-09-12）：自由循环每步决策的 usage 必须能落 trace。

    自由循环此前**不落任何** run_trace 事件，于是「这个任务跑了 15 步、输入 token 多少、
    前缀缓存命中多少」永远算不出来（技术手册 B-09 已记「每步调用不落 run_trace」）。
    """
    seen: dict = {}

    def fake(base, key, model, system, user, **kw):
        seen["on_usage"] = kw.get("on_usage")
        return json.dumps({"done": True, "reply": "完成"}, ensure_ascii=False)

    events: list[tuple] = []

    def trace(event, **fields):
        events.append((event, fields))

    fabric_loop.run_loop(
        intent="看看目录", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=fake,
        max_steps=2, trace=trace,
    )

    assert callable(seen.get("on_usage")), "自由循环没给模型通道接 on_usage"
    seen["on_usage"]({"prompt_tokens": 1000, "completion_tokens": 5,
                      "cached_tokens": 800, "uncached_prompt_tokens": 200,
                      "total_tokens": 1005, "cache_hit_ratio": 0.8})
    usage_events = [fields for event, fields in events if event == "model.usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["model"] == "m"
    assert usage_events[0]["usage"]["cached_tokens"] == 800


def test_自由循环无trace时usage回调不炸():
    """trace=None（部分调用点）时，usage 回调必须是可调用的空实现，不得抛。"""
    seen: dict = {}

    def fake(base, key, model, system, user, **kw):
        seen["on_usage"] = kw.get("on_usage")
        return json.dumps({"done": True, "reply": "完成"}, ensure_ascii=False)

    fabric_loop.run_loop(
        intent="看看目录", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=fake,
        max_steps=2,
    )
    assert callable(seen.get("on_usage"))
    seen["on_usage"]({"prompt_tokens": 1, "completion_tokens": 1})  # 不得抛


# ── P1（2026-09-12）请求前缀稳定化 ────────────────────────────────────────────

_PROGRESS_HEAD = '{"role": "user", "content": "【任务进度】'


def test_P1前缀逐字节稳定且system不双投喂():
    """请求前缀必须逐字节稳定，否则上游 prompt 前缀缓存永远不命中（每步全价）。

    同时锁死三件事：
    1. system 通道每步**完全相同**，且不含动态进度卡（写回它 = 前缀从第 1 个字符就变）；
    2. 文本通道的 dump 里**不再重复** system（此前 system 每步双投喂，纯浪费体积）；
    3. 除尾部动态进度卡外请求体是 append-only——上一步的体是下一步体的前缀。
    """
    seen: list[tuple[str, str]] = []
    decisions = [
        {"tool": "file.list_dir", "params": {"path": "."}},
        {"tool": "file.list_dir", "params": {"path": "."}},
        {"done": True, "reply": "完成"},
    ]

    def fake(base, key, model, system, user, **kw):
        seen.append((system, user))
        return json.dumps(
            decisions[min(len(seen) - 1, len(decisions) - 1)], ensure_ascii=False)

    fabric_loop.run_loop(
        intent="看看目录", configured_models={"chat", "image"},
        chat_base="", chat_key="", chat_model="m", chat_fn=fake,
        max_steps=4,
    )

    assert len(seen) == 3
    assert len({system for system, _user in seen}) == 1, \
        "system 通道每步都变了 → 请求前缀从第一个字符起就失效"
    assert "【任务进度】" not in seen[0][0], "动态进度卡又写回静态前缀了"
    for _system, user in seen:
        assert '"role": "system"' not in user, "dump 里重复投喂了 system"
        assert user.count("【任务进度】") == 1, "进度卡应恰好一份、且在尾部"

    heads = [user.split(_PROGRESS_HEAD)[0] for _system, user in seen]
    assert len(set(heads)) == 3, "切分点无效（体没在增长）——断言会恒真"
    for earlier, later in zip(heads, heads[1:]):
        assert later.startswith(earlier), "请求体不再 append-only：前缀被打断"


def test_P1带图通道messages0不被进度卡改写():
    """带图走真多消息通道：`messages[0]` 是请求头，进度卡只能追加在尾部。"""
    captured: dict = {}

    def fake_multimodal(base, key, model, messages, **kw):
        captured.setdefault("messages", messages)
        return json.dumps({"done": True, "reply": "完成"}, ensure_ascii=False)

    fabric_loop.run_loop(
        intent="反推外貌", history="上文。",
        images=["data:image/png;base64,AAA"],
        configured_models={"chat"},
        chat_base="", chat_key="", chat_model="m",
        chat_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("带图不得走纯文本通道")),
        chat_messages_fn=fake_multimodal, max_steps=2,
    )

    sent = captured["messages"]
    assert sent[0]["role"] == "system"
    assert "【任务进度】" not in sent[0]["content"], "进度卡被写进了请求头"
    assert "【任务进度】" in sent[-1]["content"], "进度卡没出现在尾部"


# ── P3/P4（2026-09-12）压缩改 append-only：端到端 ─────────────────────────────


def test_自由循环读多卷_日志append_only且投影生效(tmp_path):
    """P4 端到端：读第二卷时旧卷压缩生效于**请求体**，而**原始日志**一字不丢。

    这是 P4 的核心红线——此前压缩就地改写 `messages[i]`，历史真源被篡改（断点续跑
    不可复现）且请求前缀从该条起每步重算（上游缓存全失效）。改后：
    发出去的请求是投影视图（旧卷已摘要），`outcome.messages` 是原始日志（含声明）。
    """
    novel = tmp_path / "novel.txt"
    novel.write_text("卷" * 30000, encoding="utf-8")
    sent: list[str] = []
    decisions = [
        {"tool": "file.read_text",
         "params": {"path": str(novel), "offset": 0, "max_chars": 20000}},
        {"tool": "file.read_text",
         "params": {"path": str(novel), "offset": 20000, "max_chars": 20000}},
        {"done": True, "reply": "读完了"},
    ]

    def fake(base, key, model, system, user, **kw):
        sent.append(user)
        return json.dumps(decisions[min(len(sent) - 1, len(decisions) - 1)],
                          ensure_ascii=False)

    outcome = fabric_loop.run_loop(
        intent="读书", configured_models={"chat", "image"},
        output_dir=str(tmp_path),
        chat_base="", chat_key="", chat_model="m", chat_fn=fake,
        max_steps=4,
    )

    assert outcome.status == "done"
    assert len(sent) == 3
    # 第 2 步请求：只读过第一卷 → 还没压缩
    assert fabric_loop._READ_SUMMARY_MARK not in sent[1]
    # 第 3 步请求：已读两卷 → 第一卷压缩进请求体（投影生效）
    assert fabric_loop._READ_SUMMARY_MARK in sent[2]

    # 原始日志：追加了替换声明，但历史 tool_result 一字不丢
    assert any(fabric_loop._SURFACE_OP_KEY in str(m.get("content") or "")
               for m in outcome.messages)
    surface = fabric_loop._project_messages(outcome.messages)
    assert len(surface) < len(outcome.messages)
    assert sum(len(str(m.get("content") or "")) for m in surface) \
        < sum(len(str(m.get("content") or "")) for m in outcome.messages)


def test_system提示词含交付前置自检纪律():
    """2026-09-14 实锤：文档交付完成但参考图/场景等关键细节未确认——
    _SYSTEM 必须含交付前置自检：关键缺口先列待确认清单引导补全，不擅自假定。"""
    s = fabric_loop._SYSTEM
    assert "交付前置自检" in s
    assert "待确认清单" in s
    assert "建议默认值" in s
    # 用户口头带过 ≠ 细节已定（「都行」「不强求」仍须列入清单追认）
    assert "口头带过" in s
    assert "便于追认" in s
