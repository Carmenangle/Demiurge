"""2026-09-07 改名回归 + 剧情模式恒 1 语义测试。

上下文合同：剧情模式注入的历史与「上下文条数」设置无关，恒为上一次剧情轮
（工作流/生图/状态消息不进 prompt 历史，last_story_round 只取上一剧情轮）。
"""

from __future__ import annotations

from app.services import chat_snapshot, story_history


def _story_with_workflow_steps():
    """剧情 → /w 工作流 → 剧情，验证锚点仍是最新剧情轮。"""
    return [
        {"id": "u1", "role": "user", "text": "走进酒馆"},
        {"id": "a1", "role": "assistant", "text": "酒馆里喧闹声扑面而来。<status>x</status>", "route": "roleplay"},
        # /w 工作流：选择模板卡（无正文）+ 顶层媒体气泡（带提示词）都不进 prompt 历史
        {"id": "card", "role": "assistant", "text": "", "workflow": {"templateName": "anime"}},
        {"id": "gen", "role": "assistant", "text": "1girl, portrait", "image": "/local-view?path=a.png", "route": "generate"},
        {"id": "toast", "role": "assistant", "text": "已提交到 ComfyUI 生成（prompt_id: 1），正在运转工作流…", "system": True},
        # 用户继续剧情
        {"id": "u2", "role": "user", "text": "继续"},
        {"id": "a2", "role": "assistant", "text": "她转身看向你。<status>y</status>", "route": "roleplay"},
    ]


def test_工作流消息不打断剧情锚点():
    prompt_history = chat_snapshot.to_prompt_history(_story_with_workflow_steps())
    rounds = story_history.last_story_round(prompt_history)
    assert [m["role"] for m in rounds] == ["user", "assistant"]
    assert rounds[-1]["content"] == "她转身看向你。"
    assert all("1girl" not in m["content"] for m in rounds)
    assert all("ComfyUI" not in m["content"] for m in rounds)


def test_剧情模式只进上一次剧情轮与条数设置无关():
    history = [
        {"role": "user", "content": "第一轮输入"},
        {"role": "assistant", "content": "第一轮正文。"},
        {"role": "user", "content": "第二轮输入"},
        {"role": "assistant", "content": "第二轮正文。"},
        {"role": "user", "content": "第三轮输入"},
        {"role": "assistant", "content": "第三轮正文。"},
    ]
    # 即使上下文条数被设为 6，剧情模式历史也恒为上一次剧情轮（2 条：user+assistant）
    rounds = story_history.last_story_round(history)
    assert len(rounds) == 2
    assert rounds[0] == {"role": "user", "content": "第三轮输入"}
    assert rounds[1] == {"role": "assistant", "content": "第三轮正文。"}
