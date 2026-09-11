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
    """A 方案：同一文件递增 offset，旧卷压缩为摘要、最新卷保留原文。"""
    path = "D:/novel.txt"
    messages = (
        _read_pair(path, 0, "卷一内容" * 1000)
        + _read_pair(path, 20000, "卷二内容" * 1000)
        + _read_pair(path, 40000, "卷三内容" * 1000)
        + [{"role": "user", "content": "继续"}]
    )
    fabric_loop._compress_read_chunks(messages)
    # 最新卷(offset=40000)原文保留
    assert "卷三内容" in messages[5]["content"]
    # 旧卷被压缩为摘要
    assert fabric_loop._READ_SUMMARY_MARK in messages[1]["content"]
    assert fabric_loop._READ_SUMMARY_MARK in messages[3]["content"]
    assert "卷一内容" not in messages[1]["content"]
    assert "卷二内容" not in messages[3]["content"]


def test_compress_read_chunks_幂等():
    path = "D:/novel.txt"
    messages = _read_pair(path, 0, "卷一内容" * 1000) + _read_pair(path, 20000, "卷二内容" * 1000)
    fabric_loop._compress_read_chunks(messages)
    first = messages[1]["content"]
    fabric_loop._compress_read_chunks(messages)  # 再跑一次
    assert messages[1]["content"] == first  # 不变


def test_compress_read_chunks_非分卷读不动():
    """只读一卷(无递增 offset)或非 read_text 调用不压缩。"""
    path = "D:/novel.txt"
    messages = _read_pair(path, 0, "单卷内容" * 1000) + [
        {"role": "assistant", "content": json.dumps(
            {"tool": "doc.create_repo", "params": {"rel_path": "x.md"}}, ensure_ascii=False)},
        {"role": "user", "content": json.dumps({"tool_result": "ok"}, ensure_ascii=False)},
    ]
    fabric_loop._compress_read_chunks(messages)
    assert fabric_loop._READ_SUMMARY_MARK not in messages[1]["content"]  # 单卷不压
    assert "ok" in messages[3]["content"]  # 非 read_text 不动


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
