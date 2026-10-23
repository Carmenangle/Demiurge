"""生成侧搭车块「承诺 ⊆ 登记」一致性（2026-09-15 架构审计 P16）。

背景（真源 `docs/memory/arch-remediation-plan-2026-09-15.md` P16）：prompt 在
`agent_graph` 的【输出结构】与尾部输出纪律里**承诺**模型可以输出 `<roll>/<encounter>/<fate>`，
但三处清洗/瘦身侧清单**都没有 `fate`** ⇒ 模型真输出 `<fate>…</fate>` 时
① 不被剥离，作为「上一轮正文」注入下一轮上下文（污染）；② 裸输出时留在发布正文里（展示泄漏）。

用户 2026-09-15 定案：`fate`（命定预警）是**要保留的正式块类型** ⇒ 补进各清洗侧清单，
并把「生成侧承诺 ⊆ 清洗侧登记」做成断言（与 #17 环境注入的自动一致性断言同族）。
"""
from app.services import (
    agent_graph_roleplay,
    image_prompt_extract,
    prompt_clean,
    roleplay_turn,
    story_history,
)


def test_生成侧承诺的事件块全部登记在各清洗侧清单():
    promised = set(prompt_clean.EVENT_BLOCK_TAGS)
    sides = {
        "CONTEXT_SLIM_TAGS（story_history 上下文瘦身）": prompt_clean.CONTEXT_SLIM_TAGS,
        "BARE_STRIP_TAGS（roleplay_turn 裸输出剥块）": prompt_clean.BARE_STRIP_TAGS,
        "NARRATIVE_STRIP_TAGS（image_prompt_extract 正文提取）": prompt_clean.NARRATIVE_STRIP_TAGS,
    }
    for name, tags in sides.items():
        missing = promised - set(tags)
        assert not missing, f"{name} 缺 {sorted(missing)} ⇒ 该块会漏进正文/上下文"


def test_三处清洗清单确实取自单一属主():
    """断言「引用」而不是「各写一份」：改掉属主常量，三处正则必须跟着变。"""
    for tags, pattern in (
        (prompt_clean.CONTEXT_SLIM_TAGS, story_history._BLOCK_RE.pattern),
        (prompt_clean.BARE_STRIP_TAGS, roleplay_turn._BARE_BLOCK_TAGS.pattern),
        (prompt_clean.NARRATIVE_STRIP_TAGS, image_prompt_extract._CONTROL_OPEN_TAIL_RE.pattern),
    ):
        for tag in tags:
            assert tag in pattern, f"{tag} 未出现在清洗正则里：{pattern}"


def test_prompt_源码里真的点名了承诺的这三个块():
    """属主常量与生成侧 prompt 是**两条防线**，必须同向：常量说承诺 roll/encounter/fate，
    `agent_graph_roleplay` 的 prompt 文本里就得真出现这三个标签（否则常量是自说自话）。"""
    from pathlib import Path

    src = Path(agent_graph_roleplay.__file__).read_text(encoding="utf-8")
    for tag in prompt_clean.EVENT_BLOCK_TAGS:
        assert f"<{tag}>" in src, f"prompt 里没有 <{tag}>，与 EVENT_BLOCK_TAGS 脱节"


def test_fate块被上下文瘦身剥离():
    """改前实锤：`<fate>` 不在 `_TAGS` ⇒ `prose_only` 原样留下，作为「上一轮正文」注入下一轮。"""
    raw = "正文甲\n<fate>第 3 回合将触发命定事件</fate>\n正文乙"
    out = story_history.prose_only(raw)
    assert "命定事件" not in out
    assert "正文甲" in out and "正文乙" in out


def test_fate块被裸输出剥壳剥离且状态栏保留():
    raw = "正文甲\n<fate>命定预警文本</fate>"
    out = roleplay_turn._strip_event_blocks(raw)
    assert "命定预警文本" not in out and "正文甲" in out
    # <status> 状态栏是显示层真身，**不在**剥壳清单里（不能被顺手一起剥掉）
    assert "<status>" in roleplay_turn._strip_event_blocks("正文\n<status>状态栏</status>")


def test_fate块在正文提取时被清除():
    raw = "正文甲<fate>命定</fate>"
    out = image_prompt_extract.protected_narrative_text(raw)
    assert "命定" not in out and "正文甲" in out


def test_事件块在正文提取时被清除():
    """`NARRATIVE_STRIP_TAGS` 收拢时把 roll/encounter 一并纳入（原来只有状态类）——
    骰点与登场锚点文本同样不是画面内容，不该进图像提示词。"""
    raw = "正文甲<roll>D100=73</roll><encounter>门外脚步声</encounter>"
    out = image_prompt_extract.protected_narrative_text(raw)
    assert "D100=73" not in out and "门外脚步声" not in out and "正文甲" in out
