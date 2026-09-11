from app.services import prompt_compiler


def _messages():
    return [
        {"role": "system", "content": "head contract"},
        {"role": "user", "content": "history user"},
        {"role": "assistant", "content": "history assistant"},
        {"role": "system", "content": "tail contract"},
        {"role": "user", "content": "current input"},
    ]


def test_openai_profile_preserves_history_after_system_position():
    compiled = prompt_compiler.compile_messages(
        _messages(), provider_profile="openai_compatible",
    )

    assert [message["role"] for message in compiled.messages] == [
        "system", "user", "assistant", "system", "user",
    ]
    assert compiled.messages[3]["content"] == "tail contract"
    assert compiled.manifest[3]["position"] == "history_after"


def test_claude_profile_compiles_history_after_system_into_last_turn_contract():
    compiled = prompt_compiler.compile_messages(
        _messages(), provider_profile="claude_compatible",
    )

    assert [message["role"] for message in compiled.messages] == [
        "system", "user", "assistant", "user",
    ]
    assert "tail contract" not in compiled.messages[0]["content"]
    assert "tail contract" in compiled.messages[-1]["content"]
    assert "current input" in compiled.messages[-1]["content"]
    assert compiled.provider_profile == "claude_compatible"
