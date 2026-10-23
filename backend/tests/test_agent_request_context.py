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


def test_采样预设与剧情回合号必须能经_ctx_get_读到():
    """2026-10-20 实锤（19:03/19:04 两轮 `finish_reason=length`、输出恰好 4096）：

    `RunContext` **有** `preset_sampling` / `story_turn` 字段、`from_payload` 也**传了**，
    但 `_legacy()` 映射表漏了这两键 ⇒ `ctx.get()` 恒返回 None
    ⇒ `agent_graph._roleplay_sampling` 读到的 preset_sampling 永远是空字典
    ⇒ 用户在「采样参数」里设的单次输出（600000）**从未生效** ⇒ 请求不带 max_tokens
    ⇒ 中转默认 4096 ⇒ 每轮长生成必在 4096 截断 ⇒ 自愈连环重掷白烧钱。

    回归锁：这两键必须能经 `ctx.get()` 读到（`_legacy()` 与 dataclass 字段保持一致）。
    """
    from app.services import agent_graph

    context = from_payload({
        "thread_id": "repo-1",
        "message": "下一幕",
        "model": "claude-opus-4-8",
        "story_turn": 8,
        "preset_sampling": {
            "temperature": 1.0,
            "openai_max_tokens": 600000,
            "openai_max_context": 2000000,
        },
    })

    assert context.get("preset_sampling") == {
        "temperature": 1.0,
        "openai_max_tokens": 600000,
        "openai_max_context": 2000000,
    }
    assert context.get("story_turn") == 8

    # 端到端：预设额度必须真的进到请求参数（正文额度 + 4000 控制块预留）
    sampling = agent_graph._roleplay_sampling(context)
    assert sampling.get("max_tokens") == 604000
    assert sampling.get("temperature") == 1.0


def test_legacy映射不得漏掉任何可从ctx读的字段():
    """`_legacy()` 是 `ctx.get()` 的唯一后端 ⇒ 漏键＝「声明了但永远读不到」的静默失效。

    2026-10-20 就是这样丢了 `preset_sampling` 与 `story_turn` 两个真在用的键。
    这里做结构审计：除白名单（嵌套配置 / 改名映射 / 走 extras 的瞬态键）外，
    `RunContext` 的每个 dataclass 字段都必须出现在 `_legacy()` 里。
    """
    import dataclasses

    from app.services.agent_contracts import RunContext

    # 白名单：要么由别的键承载（chat_base/chat_key/chat_model…），要么是内部容器
    allowed_absent = {
        "chat", "generation", "video", "embedding",     # 拆成 *_base/*_key/*_model
        "chat_proxy_url", "gen_proxy_url", "video_proxy_url", "embed_proxy_url",
        "proxy_url",                                     # 拆成 proxy / *_proxy（video 映射为 vid_proxy）
        "images",                                        # 由 input_images() 合成
        "extras",                                        # 瞬态键容器，本就另走一路
    }
    fields = {f.name for f in dataclasses.fields(RunContext)}
    mapped = set(RunContext(thread_id="t", message="m")._legacy().keys())
    missing = sorted(fields - mapped - allowed_absent)
    assert missing == [], f"这些字段 ctx.get() 永远读不到：{missing}"
