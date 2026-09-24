"""用户安排最高优先级块（2026-09-23 用户定案）。

口径：**不改预设/世界书/前情/表格的任何内容**，改为在**离生成点最近**处追加
「本轮用户安排·最高优先级」声明 + 逐字复述用户本轮输入，抬升用户输入的权重。

本文件锁三件事：
① 纯函数行为（空输入→空串；有输入→声明头+逐字原文）；
② 声明头必须钉住**四条**执行口径（要发生的要发生／点名的后续时点必须本轮演到／
   界限与状态要守住／冲突让位）；
③ 尾部顺序契约 —— 该块是**最后一条** system（紧跟输出纪律、紧邻本轮 user），
   且**默认参数下不出现** ⇒ `test_roleplay_seams.py` 的等价矩阵不被破坏。

2026-09-23 深夜（T4）：原口径只覆盖「①用户写明**要发生**的事」，而用户点名的
「**要观察**的后续反应」落在口径外 ⇒ 真机出现「用户说『看看她明天的反应』、正文却
停在『把她醒来会有的神色一并留给了天亮』」的**主动截断**（`<think>` 自称
「明日反应留白：把判断权交给下一轮」）。⇒ 新增第②条把「点名的后续时点/后续反应」
显式纳入「必须本轮演到」，并点名禁止拿「留白／开放结尾／交给下一轮」当借口。
文件被 `test_roleplay_seams.py` 的 seam 白名单收录（`_rp_user_priority_block`）。
"""

from app.services import agent_graph as ag
from app.services import builtin_agents as ba
from app.services import audio_dialogue_extract


# ---------------------------------------------------------------------------
# ① 纯函数行为
# ---------------------------------------------------------------------------
def test_空输入返回空串():
    """无用户输入时整块不注入（既有消息组逐字不变）。"""
    for empty in ("", None, "   ", "\n\t ", "　"):
        assert ag._rp_user_priority_block(empty) == "", repr(empty)


def test_块以声明头开头并逐字含原文():
    text = "先在她的乳头、阴蒂、小穴等敏感部位边缘轻微抚摸徘徊"
    got = ag._rp_user_priority_block(text)
    assert got.startswith(ba.USER_PRIORITY_INSTRUCTION)
    assert got.endswith(text)                       # 原文逐字落在末尾，不得改写/截断
    assert text in got


def test_文本两侧空白被裁掉():
    got = ag._rp_user_priority_block("  \n 试探她 \t ")
    assert got.endswith("试探她")
    assert "  试探她" not in got and "试探她 " not in got


def test_无宏残留():
    """块内容不得含未替换的 {{user}}/{{char}} 字面（会被模型照抄进正文）。"""
    got = ag._rp_user_priority_block("普通输入")
    assert "{{" not in got and "}}" not in got


# ---------------------------------------------------------------------------
# ② 声明头口径（钉住三条，防被「优化」成空话）
# ---------------------------------------------------------------------------
def test_声明头钉住最高优先级与让位():
    head = ba.USER_PRIORITY_INSTRUCTION
    assert "最高优先级" in head
    assert "让位" in head
    for diluted in ("世界书", "命定危机", "前情提要"):
        assert diluted in head, diluted


def test_声明头钉住要发生与界限两条():
    head = ba.USER_PRIORITY_INSTRUCTION
    assert "要发生" in head          # 要发生的必须发生到位
    assert "界限" in head            # 界限与状态必须守住
    assert "台词" in head            # 不得替「被指明不回应者」安排台词


def test_声明头钉住点名的后续时点必演_T4():
    """T4 回归：用户点名的**后续时点/后续反应**必须本轮演到，不得拿留白推后。"""
    head = ba.USER_PRIORITY_INSTRUCTION
    # 正向：把「点名的后续时点/反应」显式纳入必须演到的范围
    assert "后续时点" in head
    assert "后续反应" in head
    assert "本轮正文内演到那个时点" in head
    # 反向：点名禁止的三个借口（真机模型用的就是「留白/交给下一轮」）
    for excuse in ("留白", "开放结尾", "交给下一轮"):
        assert excuse in head, excuse
    # 边界：留白只对**用户没点名**的走向开放，不得外溢
    assert "点名的走向才可以留白" in head


def test_声明头四条口径齐全且编号有序():
    """①②③④ 齐全、编号按序出现（防被「优化」时漏掉某一条或打乱次序）。"""
    head = ba.USER_PRIORITY_INSTRUCTION
    order = ["①", "②", "③", "④"]
    positions = [head.find(mark) for mark in order]
    assert all(pos >= 0 for pos in positions), positions
    assert positions == sorted(positions), positions
    # ④ 是兜底条：与前面冲突者一律让位
    tail = head[positions[3]:]
    assert "让位" in tail


def test_声明头仍以逐字复述引导语收尾():
    """结构未变：声明头末尾仍是那句「用户本轮输入（逐字，以此为准）：」。"""
    head = ba.USER_PRIORITY_INSTRUCTION
    assert head.rstrip().endswith("用户本轮输入（逐字，以此为准）：")


# ---------------------------------------------------------------------------
# ③ 尾部顺序契约
# ---------------------------------------------------------------------------
def test_默认参数下不出现该块():
    """等价矩阵的护栏：不传 user_text 时消息组与改造前逐字一致。"""
    msgs = ag._rp_build_tail_messages({}, "动态块", ["T1"])
    assert [m["role"] for m in msgs] == ["system", "system", "system"]
    assert all("最高优先级" not in m["content"] for m in msgs)


def test_传入user_text时块为最后一条system():
    msgs = ag._rp_build_tail_messages({}, "动态块", ["T1"], "她的乳头")
    assert msgs[-1]["role"] == "system"
    assert msgs[-1]["content"].startswith("【本轮用户安排·最高优先级】")
    assert msgs[-1]["content"].endswith("她的乳头")
    # 输出纪律仍在该块之前（其文案不得被本次改动碰到）
    assert msgs[2]["content"].startswith("[输出纪律]")


def test_顺序契约_动态块_思维链_输出纪律_音频_用户安排():
    msgs = ag._rp_build_tail_messages({"comfy_audio": True}, "D", ["T1", "T2"], "U")
    assert [m["role"] for m in msgs] == ["system"] * 6
    assert msgs[0]["content"] == "D"
    assert [m["content"] for m in msgs[1:3]] == ["T1", "T2"]
    assert msgs[3]["content"].startswith("[输出纪律]")
    assert msgs[4]["content"] == audio_dialogue_extract.build_inline_audio_instruction()
    assert msgs[5]["content"].startswith("【本轮用户安排·最高优先级】")


def test_空user_text时音频分支不受影响():
    msgs = ag._rp_build_tail_messages({"comfy_audio": True}, "", [], "   ")
    assert [m["role"] for m in msgs] == ["system", "system"]
    assert msgs[0]["content"].startswith("[输出纪律]")          # 输出纪律恒在
    assert msgs[1]["content"] == audio_dialogue_extract.build_inline_audio_instruction()
    assert all("最高优先级" not in m["content"] for m in msgs)   # 空输入 ⇒ 不追加
