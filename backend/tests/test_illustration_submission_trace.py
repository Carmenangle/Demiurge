from __future__ import annotations

from app.routers import ai_agent
from app.services import run_trace


def test_成功提交记录最终comfy参数(monkeypatch):
    captured: list[tuple[object, str, dict]] = []
    monkeypatch.setattr(
        run_trace,
        "emit",
        lambda ctx, event, **data: captured.append((ctx, event, data)),
    )
    req = ai_agent.IllustrationSubmissionRequest(
        thread_id="SAVE01",
        repo_id="神权大陆",
        turn_id="turn-1",
        message_id="bot-1",
        slot_id="slot-1",
        template_id="tpl-1",
        prompt_id="comfy-1",
        prompt="触发词\n最终提示词",
        prompt_profile="anima_tags",
        lora_name="style.safetensors",
        lora_weight=0.8,
        latent_width=704,
        latent_height=1024,
        value_keys=["18.text", "19.lora_name", "19.strength_model", "20.width", "20.height"],
    )

    assert ai_agent.illustration_submission(req) == {"ok": True}
    ctx, event, data = captured[0]
    assert event == "illustration.submitted"
    assert ctx == {"thread_id": "SAVE01", "repo_id": "神权大陆", "turn_id": "turn-1"}
    assert data["prompt"] == "触发词\n最终提示词"
    assert data["prompt_chars"] == len(req.prompt)
    assert data["lora_weight"] == 0.8
    assert data["latent"] == {"width": 704, "height": 1024}
    assert data["value_keys"] == req.value_keys
