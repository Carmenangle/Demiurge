"""execute_generation 的取消事件透传（2026-10-14 A+C 之 C2）。

生图/图生图节点的取消信号必须穿透 approval 执行层到达 image_gen 入口检查，
否则暂停只断 SSE 流，后台线程仍在向上游扣费生成。
"""
from app.services import generation_approval, image_gen
from app.services.agent_contracts import ModelConfig, RunContext


def _ctx() -> RunContext:
    return RunContext(
        thread_id="t", message="m", output_dir="/tmp/out", repo_id="t",
        generation=ModelConfig(base_url="https://img.test/v1", api_key="k", model="m"),
        embedding=ModelConfig(base_url="https://emb.test/v1", api_key="ek", model="e"),
    )


def test_执行生图透传取消事件(monkeypatch):
    seen: dict = {}

    def fake_generate(_base, _key, _model, _prompt, **kw):
        seen["cancel_event"] = kw.get("cancel_event")
        return "https://img.test/out.png"

    monkeypatch.setattr(image_gen, "generate", fake_generate)
    monkeypatch.setattr(
        generation_approval.generation_store, "persist_image",
        lambda *a, **k: {"id": "r1", "url": "https://img.test/out.png"},
    )
    monkeypatch.setattr(
        generation_approval.prompt_approval_store, "get", lambda *a, **k: None,
    )
    monkeypatch.setattr(generation_approval, "_clear_after_success", lambda *a, **k: None)
    ctx = _ctx()

    result = generation_approval.execute_generation(ctx, "image", "orig", "prompt", [], [])

    assert seen["cancel_event"] is ctx.cancel_event
    assert result["image_recs"][0]["url"] == "https://img.test/out.png"


def test_执行图生图透传取消事件(monkeypatch):
    seen: dict = {}

    def fake_edits(_base, _key, _model, _prompt, _images, **kw):
        seen["cancel_event"] = kw.get("cancel_event")
        return "https://img.test/out.png"

    monkeypatch.setattr(image_gen, "generate_with_images", fake_edits)
    monkeypatch.setattr(
        generation_approval.generation_store, "persist_image",
        lambda *a, **k: {"id": "r2", "url": "https://img.test/out.png"},
    )
    monkeypatch.setattr(
        generation_approval.prompt_approval_store, "get", lambda *a, **k: None,
    )
    monkeypatch.setattr(generation_approval, "_clear_after_success", lambda *a, **k: None)
    ctx = _ctx()

    result = generation_approval.execute_generation(
        ctx, "img2img", "orig", "prompt", ["https://img.test/ref.png"], [],
    )

    assert seen["cancel_event"] is ctx.cancel_event
    assert result["image_recs"][0]["url"] == "https://img.test/out.png"


def test_入口取消时执行生图按请求未发送收尾(monkeypatch):
    """image_gen 入口抛 UpstreamRequestNotSent（已取消）⇒ 走 request_failed 分支，
    不 persist、不插 approval 成功态。"""
    monkeypatch.setattr(
        image_gen, "generate",
        lambda *a, **k: (_ for _ in ()).throw(
            image_gen.UpstreamRequestNotSent("已取消：请求未发送到上游")),
    )
    monkeypatch.setattr(
        generation_approval.generation_store, "persist_image",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("取消后不得落盘")),
    )
    saved: dict = {}
    monkeypatch.setattr(
        generation_approval.prompt_approval_store, "set",
        lambda tid, item: saved.update(item) or item,
    )
    ctx = _ctx()
    ctx.cancel_event.set()

    result = generation_approval.execute_generation(ctx, "image", "orig", "prompt", [], [])

    assert "已取消" in result["result_text"]
    assert saved.get("stage") == "request_failed"
    assert saved.get("reason") == "upstream_unreachable"
