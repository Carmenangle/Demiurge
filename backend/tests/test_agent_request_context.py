from app.services.agent_request_context import from_payload


def test_live_and_queued_payload_share_one_context_contract():
    history = [{"role": "user", "content": "继续"}]
    context = from_payload({
        "thread_id": "repo-1",
        "message": "下一幕",
        "base_url": "chat-url",
        "api_key": "chat-key",
        "model": "chat-model",
        "card_name": "Cecilia",
        "card_names": ["Nozomi", "Cecilia", "Nozomi"],
        "opening_card_name": "",
        "history": history,
        "stream_output": True,
        "appearance_source": "character_card",
        "character_base_images": {"Cecilia": "portrait.png"},
    })

    assert context.thread_id == "repo-1"
    assert context.chat.model == "chat-model"
    assert context.card_names == ["Nozomi", "Cecilia"]
    assert context.card_name == "Cecilia"
    assert context.opening_card_name == "Cecilia"
    assert context.history_override == history
    assert context.stream_output is True
    assert context.appearance_source == "character_card"
    assert context.character_base_images == {"Cecilia": "portrait.png"}


def test_explicit_empty_history_is_not_replaced_by_checkpoint_fallback():
    context = from_payload({"thread_id": "repo-1", "message": "重开", "history": []})
    assert context.history_override == []


def test_missing_history_keeps_legacy_fallback_available():
    context = from_payload({"thread_id": "repo-1", "message": "继续"})
    assert context.history_override is None


def test_batch_image_count_defaults_to_zero_when_absent():
    # 未提供 ⇒ 0（后端按 1 张处理，单张路径逐字不变）。
    context = from_payload({"thread_id": "repo-1", "message": "继续"})
    assert context.illustration_batch_count == 0


def test_batch_image_count_passthrough_and_negative_clamped():
    assert from_payload({
        "thread_id": "repo-1", "message": "继续", "illustration_batch_count": 5,
    }).illustration_batch_count == 5
    # 脏值（负数）按 0 收敛，绝不透传负数量。
    assert from_payload({
        "thread_id": "repo-1", "message": "继续", "illustration_batch_count": -3,
    }).illustration_batch_count == 0
    assert from_payload({
        "thread_id": "repo-1", "message": "继续", "illustration_batch_count": None,
    }).illustration_batch_count == 0
