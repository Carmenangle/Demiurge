from app.services import agent_graph as ag
from app.services import generation_approval as approval_flow
from app.services import prompt_approval_store as approvals
from app.services.agent_contracts import ModelConfig, RunContext


def _use_tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "prompt-approvals.json")


def _ctx(style_template: str = "") -> dict:
    return {
        "thread_id": "thread-1", "repo_id": "repo-1",
        "chat_base": "cb", "chat_key": "ck", "chat_model": "cm",
        "gen_base": "gb", "gen_key": "gk", "gen_model": "gm",
        "vid_base": "vb", "vid_key": "vk", "vid_model": "vm",
        "embed_base": "eb", "embed_key": "ek", "embed_model": "em",
        "size": "1024x1024", "output_dir": "out", "style_template": style_template,
    }


def _run_context(message: str) -> RunContext:
    return RunContext(
        thread_id="thread-1", message=message, repo_id="repo-1",
        chat=ModelConfig("cb", "ck", "cm"),
        generation=ModelConfig("gb", "gk", "gm"),
        video=ModelConfig("vb", "vk", "vm"),
        embedding=ModelConfig("eb", "ek", "em"),
        output_dir="out",
    )


def _handle(context: RunContext):
    return approval_flow.handle_pending(context, ag._rewrite_for_compatibility)


def test_风格模板只产出待审核稿不直接生图(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    monkeypatch.setattr(ag, "_styled_prompt", lambda ctx, prompt: "结构化后的完整提示词")
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("用户审核前不得调用生图上游")))

    result = ag.generate_node({"_ctx": _ctx("模板结构"), "user_text": "原始全部细节", "trace": []})

    pending = approvals.get("thread-1")
    assert pending["stage"] == "prompt_review"
    assert pending["original_prompt"] == "原始全部细节"
    assert pending["candidate_prompt"] == "结构化后的完整提示词"
    assert "尚未提交" in result["result_text"]


def test_风格模板返回结构化审批卡而不是文字口令(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    monkeypatch.setattr(ag, "_styled_prompt", lambda ctx, prompt: "结构化后的完整提示词")

    result = ag.generate_node({"_ctx": _ctx("模板结构"), "user_text": "原始全部细节", "trace": []})

    assert result["approval"]["prompt"] == "结构化后的完整提示词"
    assert result["approval"]["status"] == "pending"
    assert "确认提交" not in result["result_text"]


def test_同一仓库可以保留多份独立审批(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    first = approvals.set("thread-1", {
        "stage": "prompt_review", "kind": "image", "candidate_prompt": "第一份",
    })
    second = approvals.set("thread-1", {
        "stage": "prompt_review", "kind": "image", "candidate_prompt": "第二份",
    })

    assert first["id"] != second["id"]
    assert approvals.get("thread-1", first["id"])["candidate_prompt"] == "第一份"
    assert approvals.get("thread-1", second["id"])["candidate_prompt"] == "第二份"


def test_未处理的旧审批不阻断新提示词(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    approvals.set("thread-1", {
        "stage": "prompt_review", "kind": "image",
        "original_prompt": "旧提示词", "candidate_prompt": "旧审核稿",
    })

    events = _handle(_run_context("全新的提示词，生成另一张图"))

    assert events is None


def test_直接生图失败后先询问是否修饰(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("upstream moderation blocked")))

    result = ag.generate_node({"_ctx": _ctx(), "user_text": "原始敏感提示词细节", "trace": []})

    pending = approvals.get("thread-1")
    assert pending["stage"] == "rewrite_consent"
    assert pending["candidate_prompt"] == "原始敏感提示词细节"
    assert "尚未自动修改" in result["result_text"]
    assert "请直接在下方选择" in result["result_text"]


def test_交付状态未知不得误导为提示词问题(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: (_ for _ in ()).throw(
        approval_flow.image_gen.UpstreamDeliveryUnknown("上游交付状态未知（request_id=req-1）")))

    result = ag.generate_node({"_ctx": _ctx(), "user_text": "原始提示词", "trace": []})

    pending = approvals.get("thread-1")
    assert pending["stage"] == "delivery_unknown"
    assert "无法确认上游是否创建任务" in result["result_text"]
    assert "提示词" not in result["result_text"]
    assert "自动重试" in result["result_text"]


def test_连接失败标记为请求未发送(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: (_ for _ in ()).throw(
        approval_flow.image_gen.UpstreamRequestNotSent("连接上游超时，请求未发送（request_id=req-2）")))

    result = ag.generate_node({"_ctx": _ctx(), "user_text": "原始提示", "trace": []})

    pending = approvals.get("thread-1")
    assert pending["stage"] == "request_failed"
    assert "请求没有发送到上游" in result["result_text"]
    assert "提示词" not in result["result_text"]


def test_失败审批可用更改按钮转为待审核(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    item = approvals.set("thread-1", {
        "stage": "rewrite_consent", "kind": "image",
        "original_prompt": "原稿", "candidate_prompt": "失败原稿",
        "images": [], "error": "timeout",
    })
    ctx = _run_context("更改提示词")
    ctx.approval_id = item["id"]
    ctx.approval_action = "change"
    ctx.edited_prompt = "用户修改后的提示词"

    events = _handle(ctx)

    pending = approvals.get("thread-1", item["id"])
    assert pending["stage"] == "prompt_review"
    assert pending["candidate_prompt"] == "用户修改后的提示词"
    assert any(e.get("approval", {}).get("status") == "pending" for e in events)


def test_失败审批确认提交会重试原候选稿(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    item = approvals.set("thread-1", {
        "stage": "rewrite_consent", "kind": "image",
        "original_prompt": "原稿", "candidate_prompt": "失败原稿",
        "images": [], "error": "timeout",
    })
    calls = []
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: calls.append(a[3]) or "https://img.test/retry.png")
    monkeypatch.setattr(approval_flow.generation_store, "persist_image", lambda *a, **k: {
        "id": "image-retry", "url": "https://img.test/retry.png",
    })
    ctx = _run_context("确认提交")
    ctx.approval_id = item["id"]
    ctx.approval_action = "submit"

    events = _handle(ctx)

    assert calls == ["失败原稿"]
    assert any(e.get("image") == "https://img.test/retry.png" for e in events)


def test_同意修饰后只展示新稿仍不提交(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    approvals.set("thread-1", {
        "stage": "rewrite_consent", "kind": "image",
        "original_prompt": "原始全部细节", "candidate_prompt": "第一次提交稿",
        "images": [], "error": "blocked",
    })
    monkeypatch.setattr(ag, "_rewrite_for_compatibility",
                        lambda ctx, prompt: "艺术化但细节完整的新稿")
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("修饰稿二次审核前不得调用生图上游")))

    events = _handle(_run_context("同意修饰"))

    pending = approvals.get("thread-1")
    assert pending["stage"] == "prompt_review"
    assert pending["candidate_prompt"] == "艺术化但细节完整的新稿"
    assert any("再次审核" in e.get("delta", "") for e in events)


def test_审核确认后才提交并清除状态(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    approvals.set("thread-1", {
        "stage": "prompt_review", "kind": "image",
        "original_prompt": "原始全部细节", "candidate_prompt": "用户已审核稿",
        "images": [], "reason": "style",
    })
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: "https://img.test/result.png")
    monkeypatch.setattr(approval_flow.generation_store, "persist_image", lambda *a, **k: {
        "id": "image-1", "url": "https://img.test/result.png",
    })

    events = _handle(_run_context("确认提交"))

    assert approvals.get("thread-1") is None
    assert any(e.get("image") == "https://img.test/result.png" for e in events)
    assert any("已生成图片" in e.get("delta", "") for e in events)


def test_图片已落库但审批清理失败不能伪装成生图失败(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    approvals.set("thread-1", {
        "stage": "prompt_review", "kind": "image",
        "original_prompt": "原始全部细节", "candidate_prompt": "用户已审核稿", "images": [],
    })
    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: "https://img.test/late.png")
    monkeypatch.setattr(approval_flow.generation_store, "persist_image", lambda *a, **k: {
        "id": "image-late", "url": "https://img.test/late.png",
    })
    monkeypatch.setattr(approval_flow.prompt_approval_store, "clear",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("审批文件暂时不可写")))

    events = _handle(_run_context("确认提交"))

    assert any(e.get("image") == "https://img.test/late.png" for e in events)
    assert any("已生成图片" in e.get("delta", "") for e in events)


def test_历史审批按id独立修改和提交(tmp_path, monkeypatch):
    _use_tmp_store(tmp_path, monkeypatch)
    first = approvals.set("thread-1", {
        "stage": "prompt_review", "kind": "image", "message_id": "message-1",
        "original_prompt": "原稿一", "candidate_prompt": "候选一", "images": [],
    })
    second = approvals.set("thread-1", {
        "stage": "prompt_review", "kind": "image", "message_id": "message-2",
        "original_prompt": "原稿二", "candidate_prompt": "候选二", "images": [],
    })

    change_ctx = _run_context("更改提示词")
    change_ctx.approval_id = first["id"]
    change_ctx.approval_action = "change"
    change_ctx.edited_prompt = "用户修改后的候选一"
    change_events = _handle(change_ctx)

    assert approvals.get("thread-1", first["id"])["candidate_prompt"] == "用户修改后的候选一"
    assert approvals.get("thread-1", second["id"])["candidate_prompt"] == "候选二"
    assert any(e.get("approval", {}).get("prompt") == "用户修改后的候选一" for e in change_events)

    monkeypatch.setattr(approval_flow.image_gen, "generate", lambda *a, **k: "https://img.test/history.png")
    monkeypatch.setattr(approval_flow.generation_store, "persist_image", lambda *a, **k: {
        "id": "image-history", "url": "https://img.test/history.png",
    })
    submit_ctx = _run_context("确认提交")
    submit_ctx.approval_id = first["id"]
    submit_ctx.approval_action = "submit"
    _handle(submit_ctx)

    assert approvals.get("thread-1", first["id"]) is None
    assert approvals.get("thread-1", second["id"])["candidate_prompt"] == "候选二"


def test_风格改写提示词必须声明保留全部细节(monkeypatch):
    captured = {}

    def fake_chat(*args, **kwargs):
        captured["system"] = args[3]
        return "改写稿"

    monkeypatch.setattr(ag._llm, "chat", fake_chat)
    out = ag._styled_prompt(_ctx("只参考这种结构"), "原始全部细节")

    assert out == "改写稿"
    assert "不得删除" in captured["system"]
    assert "不得改变" in captured["system"]


def test_蒙版图生图把独立mask传给生成接口(monkeypatch):
    captured = {}
    ctx = {**_ctx(), "image_mask": {"image": "original.png", "mask": "mask.png"}}
    monkeypatch.setattr(
        approval_flow.image_gen,
        "generate_with_images",
        lambda *args, **kwargs: captured.update({"images": args[4], **kwargs}) or "result.png",
    )
    monkeypatch.setattr(approval_flow.generation_store, "persist_image", lambda *args, **kwargs: {
        "id": "masked-result", "url": "result.png", "regeneration": args[-1],
    })

    result = ag.img2img_node({
        "_ctx": ctx,
        "user_text": "只修改蒙版区域",
        "images": ["original.png"],
        "trace": [],
    })

    assert captured["images"] == ["original.png"]
    assert captured["mask"] == "mask.png"
    assert result["image_recs"][0]["regeneration"]["imageMask"] == ctx["image_mask"]
