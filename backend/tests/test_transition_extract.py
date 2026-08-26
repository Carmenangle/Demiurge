"""transition_extract 单测：转场判定搭车块的注入指令 + 剥离解析。"""

from app.services import transition_extract as te


def test_extract_reuse():
    clean, decision = te.extract_transition("三人围坐面馆，举杯同框。<transition>reuse</transition>")
    assert clean == "三人围坐面馆，举杯同框。"
    assert decision == "reuse"


def test_extract_regenerate():
    clean, decision = te.extract_transition("次日清晨，她在车站送别。<transition>regenerate</transition>")
    assert clean == "次日清晨，她在车站送别。"
    assert decision == "regenerate"


def test_extract_missing_block_returns_none():
    # 漏块（主模型违约）→ decision=None，不抛错（回退 L0）
    clean, decision = te.extract_transition("三人围坐面馆，举杯同框。")
    assert clean == "三人围坐面馆，举杯同框。"
    assert decision is None


def test_extract_invalid_value_returns_none():
    # 值非法（不是 reuse/regenerate）→ None
    _, decision = te.extract_transition("正文。<transition>maybe</transition>")
    assert decision is None


def test_extract_unterminated_open_tag_truncates():
    # 只开不闭：截断开尾，正文不丢，decision=None
    clean, decision = te.extract_transition("正文第一段。\n\n正文第二段。<transition>reuse")
    assert "正文第一段" in clean
    assert "正文第二段" in clean
    assert decision is None


def test_extract_case_and_whitespace_tolerant():
    # 大小写/空白容错
    _, decision = te.extract_transition("正文。<transition> Reuse </transition>")
    assert decision == "reuse"
    _, decision = te.extract_transition("正文。<transition>REGENERATE</transition>")
    assert decision == "regenerate"


def test_extract_multiple_blocks_uses_last():
    # 多块取最后一个；块间正文「多余」保留，只剥掉 transition 块本身
    clean, decision = te.extract_transition(
        "正文。<transition>reuse</transition>多余<transition>regenerate</transition>"
    )
    assert decision == "regenerate"
    assert clean == "正文。多余"


def test_instruction_mentions_contract():
    # 注入指令含判定依据与格式（供主生成使用）
    instr = te.build_inline_transition_instruction()
    assert "<transition>reuse</transition>" in instr
    assert "<transition>regenerate</transition>" in instr
    assert "上一轮对话的结尾" in instr
