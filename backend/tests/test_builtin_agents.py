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
