"""③ 内置 Agent 注册表 + 覆盖存储 + 运行时生效。"""
from __future__ import annotations

from app.services import builtin_agents as ba


def test_curator优先更新角色动态且机制默认只读():
    prompt = ba.CURATOR_SYSTEM
    assert "角色条目中的长期剧情动态是最主要" in prompt
    assert "机制、规则、历史背景" in prompt
    assert "默认只读" in prompt


def test_注册表覆盖全部内置角色(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    ids = {a["id"] for a in ba.registry_view()}
    # 图里的内置节点 + 世界 Agent + 裁判 + Recall 检索 + Curator 都应可见
    assert {"supervisor", "roleplay", "answer", "world", "judge",
            "recall", "curator",
            "edit_supervisor", "edit_character_card", "edit_preset_regex",
            "edit_import_adapter", "edit_script", "edit_debug", "edit_general",
            "generate", "img2img", "video", "analyze", "inspire", "tool_agent"} <= ids


def test_编辑专家可覆盖提示词与采样参数(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    ba.save_overrides({
        "edit_character_card": {
            "systemPrompt": "作品专用角色卡规则", "temperature": 0.4,
            "topP": 0.8, "maxTokens": 4096,
        },
    })
    effective = ba.resolved()["edit_character_card"]
    assert effective == {
        "systemPrompt": "作品专用角色卡规则", "temperature": 0.4,
        "topP": 0.8, "maxTokens": 4096,
    }


def test_迁移专家展示确定性转换工具(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    item = next(agent for agent in ba.registry_view() if agent["id"] == "edit_import_adapter")
    assert "外部格式转换" in item["tools"]


def test_llm类可覆盖topP与maxTokens(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    ba.save_overrides({
        "roleplay": {"topP": 0.9, "maxTokens": 2048},
    })
    eff = ba.resolved()
    assert eff["roleplay"]["topP"] == 0.9
    assert eff["roleplay"]["maxTokens"] == 2048


def test_roleplay含骰点规则字段(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    eff = ba.resolved()
    assert "rollInstruction" in eff["roleplay"]
    assert "<roll>" in eff["roleplay"]["rollInstruction"]  # 强制输出格式在默认里
    assert "[PLAYER]" in eff["roleplay"]["rollInstruction"]  # 与角色卡命运骰正则契约一致
    # 用户可清空骰点规则（空串合法）
    ba.save_overrides({"roleplay": {"rollInstruction": ""}})
    assert ba.resolved()["roleplay"]["rollInstruction"] == ""


def test_默认无覆盖时生效值等于默认(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    eff = ba.resolved()
    assert eff["roleplay"]["systemPrompt"] == ba.ROLEPLAY_BASE
    assert eff["judge"]["gateFloor"] == ba.GATE_FLOOR
    assert eff["world"]["temperature"] == ba.WORLD_TEMPERATURE
    recall = next(a for a in ba.registry_view() if a["id"] == "recall")
    assert recall["kind"] == "specialist"
    assert recall["editable"] == []
    assert eff["curator"]["gate"] == 1.0
    assert eff["judge"]["gateBaseRate"] == 1.0


def test_保存覆盖后生效值改变(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    ba.save_overrides({
        "roleplay": {"systemPrompt": "自定义扮演", "temperature": 1.1},
        "judge": {"gateFloor": 20.0, "gateBaseRate": 0.4, "tiers": [-30.0, 30.0]},
    })
    eff = ba.resolved()
    assert eff["roleplay"]["systemPrompt"] == "自定义扮演"
    assert eff["roleplay"]["temperature"] == 1.1
    assert eff["judge"]["gateFloor"] == 20.0
    assert eff["judge"]["tiers"] == [-30.0, 30.0]


def test_保存丢弃未知agent与非法字段(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    saved = ba.save_overrides({
        "unknown_agent": {"systemPrompt": "x"},      # 未知 agent → 丢
        "generate": {"systemPrompt": "x"},           # specialist 无 editable → 丢
        "judge": {"gateFloor": "not-a-number"},      # 类型非法 → 丢
        "roleplay": {"temperature": True},           # bool 不是数值 → 丢
    })
    assert saved == {}


def test_覆盖坏json回退默认(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    (tmp_path / "builtin_agents.json").write_text("{ not json", encoding="utf-8")
    assert ba.load_overrides() == {}
    assert ba.resolved()["roleplay"]["temperature"] == ba.ROLEPLAY_TEMPERATURE


def test_节奏以用户输入为准不把多日计划当已完成():
    """2026-09-22 用户定案（方案乙）：主提示词不得再把「多日/多阶段推进计划」当「一口气推到结局」。

    真机 19:33：用户输入给了三天的节拍表（第一天不动手→第二天装睡动手→第三天装没事），
    正文只覆盖「次日卯初」一个清晨（1847 字）。原【节奏】「单轮聚焦一个场景推进…避免一口气
    推到结局」被模型读成「别把计划走完」⇒ 主动砍掉后两拍。改为**跨度与事件量以用户输入为准**。
    """
    base = ba.ROLEPLAY_BASE
    # 旧的压制口径必须消失
    assert "避免一口气推到结局" not in base
    assert "单轮聚焦一个场景推进，留有余地让用户接话" not in base
    # 新口径：以用户输入为准 + 不得一笔带过 + 转场可跳过 + 单场景降级为默认
    # 做法二（2026-09-22 精简）：一句主口径 ＋ 一句默认值，不写枚举示例、不列转场词表、不打补丁
    assert "以用户本轮输入为准" in base
    assert "用户写明的进展要实写到位" in base
    assert "不得一笔带过" in base
    assert "默认聚焦一个场景推进、留白收尾" in base
    # ⚠ 回归锁（2026-09-22 实锤）：改【节奏】时我的替换区间吃掉了整条【状态栏】规则，
    # 导致状态栏模板丢失。四个分区必须俱在。
    for section in ("【出演准则】", "【禁则】", "【输入性质】", "【节奏】", "【状态栏】"):
        assert section in base, f"ROLEPLAY_BASE 分区丢失：{section}"
    assert "必须在本轮末尾按既定格式续写更新" in base
    # 不再有枚举式示例与转场词表（机械式预设，用户明令精简掉）
    assert "第一天…第二天…第三天…" not in base
    assert "「翌日」" not in base
    assert "时光飞逝" not in base
    assert "不是**把用户已经写明的进展砍掉" not in base


def test_生成字数下限默认关闭且可覆盖(monkeypatch, tmp_path):
    """2026-09-22 用户定案：智能体参数预设→剧情推进「生成字数下限」，默认 0＝关闭。

    低于该字数 ⇒ 判「没写完」并**轮内续写**（同一条消息接着写，非多轮），
    对齐 SillyTavern `auto_continue.target_length`（它用 token，这里用中文字数）。
    """
    monkeypatch.setattr(ba, "DATA_DIR", tmp_path)
    # 默认关闭
    assert ba.resolved()["roleplay"]["storyMinChars"] == 0
    assert "storyMinChars" in ba._BY_ID["roleplay"]["editable"]
    # 合法覆盖生效
    ba.save_overrides({"roleplay": {"storyMinChars": 3000}})
    assert ba.resolved()["roleplay"]["storyMinChars"] == 3000
    # 非法值被丢弃（负数 / bool / 字符串）
    assert ba.save_overrides({"roleplay": {"storyMinChars": -1}}) == {}
    assert ba.save_overrides({"roleplay": {"storyMinChars": True}}) == {}
    assert ba.save_overrides({"roleplay": {"storyMinChars": "3000"}}) == {}


def test_输入性质分两种_说明安排如实描写_操控主人公写后续影响():
    """2026-09-22 用户定案（用户原话）：「一个是根据用户说明的去描写说明的，一个是用户操控
    主人公做事情，查看做事后的剧情影响」。

    真机 20:17 的失败点：用户写「第一天先不动手」它首段就动手、写「第三天装没事」它自行加出
    第 3/4/5 夜 ⇒ **把计划当背景资料，而不是必须照着走的剧本**。故补【输入性质】分区。
    """
    base = ba.ROLEPLAY_BASE
    assert "【输入性质】" in base
    # ① 说明安排：照说明如实描写，节点不增不减，限制照做
    assert "说明安排" in base and "如实描写出来" in base
    assert "节点不增不减" in base and "说明里的限制照做" in base
    # ② 操控主人公：写后续反应与剧情影响，不替他加戏
    assert "操控主人公" in base and "后续反应与剧情影响" in base
    # 2026-09-22 用户定案删除「不替他加戏」：「那么长的剧情不可能就用户描述的那么一个动作限制死」
    # —— ② 里用户给的动作只是**引子**，后续发展不得被这一个动作框死。
    assert "不替他加戏" not in base
    # 靠语境判断（不写机械枚举示例）
    assert "按语境分两种" in base
    assert "第一天" not in base and "第二天" not in base


def test_命运骰点规则_既定现实不掷骰_有争议仍须掷骰():
    """2026-09-22 用户定案：「用户明确说明的既定现实无需掷骰子，但如果是有争议的，还是要
    判断下点数的」。

    原口径「日常相处与既定推进直接叙事，不必掷骰」豁免过宽——用户习惯把结果写死
    （真机 19:29~21:33 连续 8 轮零出骰），且与世界书「绝不由我单方面宣告成功」相冲。
    """
    r = ba.ROLL_INSTRUCTION
    # 新口径：既定现实免掷 + 有争议仍须掷
    assert "明确说明的既定现实" in r
    assert "不必掷骰" in r
    assert "有争议的" in r
    assert "仍须掷骰裁决" in r
    assert "单方面宣告得手" in r
    # 旧豁免不得回潮
    assert "日常相处与既定推进直接叙事，不必掷骰" not in r
    # 判定步骤与输出格式契约未动
    assert "<roll>" in r and "</roll>" in r and "[SKILL]" in r and "[ROLL]" in r


# ── 2026-10-21 世界事件司门：judge.eventsEnabled + World 三类提案协议 ──

def test_judge_eventsEnabled_默认可覆盖且校验():
    spec = next(a for a in ba.REGISTRY if a["id"] == "judge")
    assert "eventsEnabled" in spec["editable"]
    assert spec["defaults"]["eventsEnabled"] == ba.EVENTS_ENABLED == 1.0
    assert ba._valid_override("judge", "eventsEnabled", 0.0) is True
    assert ba._valid_override("judge", "eventsEnabled", True) is False   # 布尔不是合法旋钮
    assert ba._valid_override("judge", "eventsEnabled", "x") is False


def test_world提示词钉住三类提案与来源约束():
    w = ba.WORLD_SYSTEM
    for kind in ("npc", "event", "user_action"):
        assert kind in w, kind
    assert "可登门角色清单" in w and "不得自创" in w
    assert "可用事件锚点" in w
    assert "连续两轮最多一次" in w
    # 旧单类型协议不得回潮：仍须保留原 NPC 行为生成器判据
    assert "死穴/攻略路径/个体机制" in w


def test_用户安排块不再宣告结果绝对():
    """2026-10-21 部分修订的源码锁：结果交判定，冲突时判定结果为准。"""
    head = ba.USER_PRIORITY_INSTRUCTION
    assert "意图与场景安排" in head
    assert "成败与代价不由任何一方单方面决定" in head
    assert "交 D100 检定" in head
    assert "不覆盖" in head and "判定结果为准" in head
    assert "一律让位" not in head
