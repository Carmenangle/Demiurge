"""D2 覆盖层同源：`rollInstruction` 的 [RESULT] 六档词在注入时由 `agency.DEGREE_LABEL` 重渲染。

默认层已由 `test_builtin_agents.py::test_命运骰六档词表与本地档位标签同源` 上锁；
但「设置 → 内置智能体 → 剧情推进 → 命运骰点规则」是可编辑覆盖层，用户改档位词会让
「模型被教的词」与「裁判/指令/CI 读的 DEGREE_LABEL」分叉。这里钉住覆盖层的最后一道渲染：
- 只重写已存在的 `[RESULT]` 行，其余文字逐字节保留；
- 没有该行 / 空文本 ⇒ 原样返回（清空=不掷骰的既有口径，不自行补格式）；
- `_rp_append_static_blocks` 必须真的走渲染（单元函数绿而接线漏掉是这条缺陷的典型形态）。
"""
from app.services import agency, builtin_agents
from app.services import agent_graph_roleplay as agr


CANON = "/".join(agency.DEGREE_LABEL.values())
CANON_LINE = "[RESULT] " + CANON
TAMPERED_LINE = "[RESULT] 完胜/小胜/险胜/和/败/惨败"


def _tampered() -> str:
    return builtin_agents.ROLL_INSTRUCTION.replace(CANON_LINE, TAMPERED_LINE)


def test_默认规则过渲染器逐字节不变():
    out = agency.render_result_line(builtin_agents.ROLL_INSTRUCTION)
    assert out == builtin_agents.ROLL_INSTRUCTION
    assert CANON_LINE in out


def test_覆盖层改档位词只重写RESULT且其余文字零变化():
    text = _tampered()
    assert CANON_LINE not in text and TAMPERED_LINE in text
    out = agency.render_result_line(text)
    assert out == text.replace(TAMPERED_LINE, CANON_LINE)
    assert "完胜" not in out and "惨败" not in out


def test_保留行缩进与CRLF换行():
    text = "  前言\r\n  [RESULT] 甲/乙\r\n后记"
    assert agency.render_result_line(text) == "  前言\r\n  " + CANON_LINE + "\r\n后记"


def test_没有RESULT行时原样返回():
    text = "自定义骰点规则：只在战斗时掷骰。"
    assert agency.render_result_line(text) == text


def test_空文本与纯空白原样返回():
    for text in ("", "   ", "\n\t"):
        assert agency.render_result_line(text) == text


def test_多条RESULT行全部重渲染():
    text = "[RESULT] 甲/乙\n中间\n[RESULT] 丙/丁"
    assert agency.render_result_line(text) == CANON_LINE + "\n中间\n" + CANON_LINE


def test_接线_覆盖层档位词在注入时被重渲染():
    ctx = {"builtin": {"roleplay": {"rollInstruction": "前言\n" + TAMPERED_LINE + "\n后记"}}}
    out = agr._rp_append_static_blocks(ctx, "BASE", [])
    assert out == "BASE前言\n" + CANON_LINE + "\n后记"


def test_接线_清空覆盖等于不注入():
    ctx = {"builtin": {"roleplay": {"rollInstruction": ""}}}
    assert agr._rp_append_static_blocks(ctx, "BASE", []) == "BASE"


def test_接线_无RESULT行不擅自补格式():
    ctx = {"builtin": {"roleplay": {"rollInstruction": "只在战斗时掷骰。"}}}
    assert agr._rp_append_static_blocks(ctx, "BASE", []) == "BASE只在战斗时掷骰。"


def test_接线_默认路径逐字节等于硬编码默认():
    assert agr._rp_append_static_blocks({}, "BASE", []) == "BASE" + builtin_agents.ROLL_INSTRUCTION