"""偏置预设解析/组装/落盘测试。"""
from __future__ import annotations

import pytest

from app.services import preset_store


def _preset(order_enabled: list[tuple[str, bool]], prompts: list[dict], **extra) -> dict:
    return {
        "prompts": prompts,
        "prompt_order": [{"character_id": 1, "order": [
            {"identifier": ident, "enabled": en} for ident, en in order_enabled
        ]}],
        **extra,
    }


def test_组装按order排序且跳过未启用():
    preset = _preset(
        [("a", True), ("b", False), ("c", True)],
        [
            {"identifier": "a", "content": "片段A", "marker": False},
            {"identifier": "b", "content": "片段B", "marker": False},
            {"identifier": "c", "content": "片段C", "marker": False},
        ],
    )
    out = preset_store.assemble_system(preset, {})
    assert "片段A" in out and "片段C" in out
    assert "片段B" not in out
    assert out.index("片段A") < out.index("片段C")


def test_marker展开卡字段与世界书():
    preset = _preset(
        [("desc", True), ("wi", True), ("scen", True)],
        [
            {"identifier": "desc", "marker": True},
            {"identifier": "wi", "marker": True},
            {"identifier": "scen", "marker": True},
        ],
    )
    # 用真实 marker identifier
    preset["prompts"][0]["identifier"] = "charDescription"
    preset["prompts"][1]["identifier"] = "worldInfoBefore"
    preset["prompts"][2]["identifier"] = "scenario"
    preset["prompt_order"][0]["order"] = [
        {"identifier": "charDescription", "enabled": True},
        {"identifier": "worldInfoBefore", "enabled": True},
        {"identifier": "scenario", "enabled": True},
    ]
    out = preset_store.assemble_system(preset, {
        "char_description": "冷酷帝主", "worldbook": "女尊世界", "scenario": "孤儿院门前",
    })
    assert "冷酷帝主" in out and "女尊世界" in out and "孤儿院门前" in out


def test_chatHistory_marker跳过():
    preset = _preset(
        [("chatHistory", True), ("x", True)],
        [
            {"identifier": "chatHistory", "marker": True},
            {"identifier": "x", "content": "尾部", "marker": False},
        ],
    )
    out = preset_store.assemble_system(preset, {})
    assert out.strip() == "尾部"  # 历史 marker 不进 system


def test_宏替换char_user():
    preset = _preset(
        [("a", True)],
        [{"identifier": "a", "content": "以{{char}}身份回应{{user}}", "marker": False}],
    )
    out = preset_store.assemble_system(preset, {"char_name": "塞西莉亚", "user_name": "主角"})
    assert out == "以塞西莉亚身份回应主角"


def test_宏替换user缺省回退我():
    # user_name 空 → {{user}} 替换成「我」，不留字面宏
    assert preset_store.substitute_macros("你好{{user}}", {"char_name": "塞西莉亚"}) == "你好我"
    assert preset_store.substitute_macros("你好{{user}}", {"user_name": ""}) == "你好我"


def test_marker值也做宏替换():
    # 卡描述里含 {{user}} → marker 展开时也替换（此前 marker 分支漏了替换，字面漏进提示词）
    preset = _preset(
        [("desc", True)],
        [{"identifier": "charDescription", "marker": True}],
    )
    preset["prompts"][0]["identifier"] = "charDescription"
    preset["prompt_order"][0]["order"] = [{"identifier": "charDescription", "enabled": True}]
    out = preset_store.assemble_system(preset, {"char_description": "守护{{user}}的骑士", "user_name": "叶凡"})
    assert out == "守护叶凡的骑士"
    msgs = preset_store.assemble_messages(preset, {"char_description": "守护{{user}}的骑士", "user_name": "叶凡"})
    assert msgs == [{"role": "system", "content": "守护叶凡的骑士"}]


def test_采样参数提取():
    preset = _preset([], [], temperature=0.9, top_p=0.95, wrap_in_quotes=False)
    params = preset_store.sampling_params(preset)
    assert params["temperature"] == 0.9 and params["top_p"] == 0.95
    assert "wrap_in_quotes" not in params


# ── 条件选链（P1）：按真状态 scene/affinity/turn 选思维链 ──

def test_select_chains_无字段返回空():
    assert preset_store.select_chains({"prompts": []}) == ([], [])


def test_select_chains_无条件链恒命中():
    preset = {"thinking_chains": [{"name": "基础", "content": "先推理后落笔"}]}
    tail, head = preset_store.select_chains(preset)
    assert tail == ["先推理后落笔"] and head == []


def test_select_chains_场景条件():
    preset = {"thinking_chains": [
        {"name": "对话链", "content": "潜台词", "when": {"scene": "dialogue"}},
        {"name": "动作链", "content": "动作连贯", "when": {"scene": "action"}},
    ]}
    assert preset_store.select_chains(preset, scene="dialogue")[0] == ["潜台词"]
    assert preset_store.select_chains(preset, scene="action")[0] == ["动作连贯"]
    assert preset_store.select_chains(preset, scene="nsfw")[0] == []  # 都不命中


def test_select_chains_好感度阈值():
    preset = {"thinking_chains": [
        {"name": "冷淡", "content": "疏离", "when": {"affinity_lt": 30}},
        {"name": "亲密", "content": "亲昵", "when": {"affinity_gt": 70}},
    ]}
    assert preset_store.select_chains(preset, affinity=10)[0] == ["疏离"]
    assert preset_store.select_chains(preset, affinity=80)[0] == ["亲昵"]
    assert preset_store.select_chains(preset, affinity=50)[0] == []
    assert preset_store.select_chains(preset, affinity=None)[0] == []  # 无好感度 → 阈值条件不满足


def test_select_chains_turn周期与head位置():
    preset = {"thinking_chains": [
        {"name": "每3轮", "content": "反重复", "when": {"turn_mod": [3, 0]}},
        {"name": "框架", "content": "整体框架", "position": "head"},
    ]}
    tail, head = preset_store.select_chains(preset, turn=6)
    assert "反重复" in tail and head == ["整体框架"]
    tail2, _ = preset_store.select_chains(preset, turn=5)
    assert "反重复" not in tail2  # 5%3!=0


def test_assemble_messages_保留片段role不折叠():
    # system + user + assistant 少样本片段，各自 role 应保留，而非折叠成单 system
    preset = _preset(
        [("sys", True), ("u", True), ("a", True)],
        [
            {"identifier": "sys", "role": "system", "content": "你是灰魂", "marker": False},
            {"identifier": "u", "role": "user", "content": "示例问", "marker": False},
            {"identifier": "a", "role": "assistant", "content": "示例答", "marker": False},
        ],
    )
    msgs = preset_store.assemble_messages(preset, {})
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert [m["content"] for m in msgs] == ["你是灰魂", "示例问", "示例答"]


def test_assemble_messages_缺省role为system():
    preset = _preset([("a", True)], [{"identifier": "a", "content": "无role片段", "marker": False}])
    msgs = preset_store.assemble_messages(preset, {})
    assert msgs == [{"role": "system", "content": "无role片段"}]


def test_assemble_messages_chatHistory原位插历史():
    # chatHistory marker 处插入真实多轮历史，保留 user/assistant role；尾部 system 片段在历史之后
    preset = _preset(
        [("intro", True), ("chatHistory", True), ("phi", True)],
        [
            {"identifier": "intro", "role": "system", "content": "开场", "marker": False},
            {"identifier": "chatHistory", "marker": True},
            {"identifier": "phi", "role": "system", "content": "尾部指令", "marker": False},
        ],
    )
    history = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "在的"}]
    msgs = preset_store.assemble_messages(preset, {}, history)
    assert msgs == [
        {"role": "system", "content": "开场"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在的"},
        {"role": "system", "content": "尾部指令"},
    ]
    assert preset_store.has_history_marker(preset) is True


def test_assemble_messages_无历史marker不自动插():
    preset = _preset([("a", True)], [{"identifier": "a", "content": "片段", "marker": False}])
    msgs = preset_store.assemble_messages(preset, {}, [{"role": "user", "content": "hi"}])
    assert msgs == [{"role": "system", "content": "片段"}]  # 无 chatHistory marker → 历史不插
    assert preset_store.has_history_marker(preset) is False


def test_assemble_messages_宏替换():
    preset = _preset(
        [("a", True)],
        [{"identifier": "a", "role": "system", "content": "以{{char}}回应{{user}}", "marker": False}],
    )
    msgs = preset_store.assemble_messages(preset, {"char_name": "灰魂", "user_name": "主角"})
    assert msgs == [{"role": "system", "content": "以灰魂回应主角"}]


def test_落盘往返与同名覆盖(tmp_path):
    base = str(tmp_path)
    p = _preset([("a", True)], [{"identifier": "a", "content": "x", "marker": False}])
    s = preset_store.save(base, "灰魂", p)
    assert s.prompts == 1 and s.enabled == 1
    assert preset_store.read_preset(base, "灰魂") is not None
    with pytest.raises(FileExistsError):
        preset_store.save(base, "灰魂", p)
    preset_store.save(base, "灰魂", p, overwrite=True)  # 允许
    assert {x.name for x in preset_store.list_presets(base)} == {"灰魂"}
    assert preset_store.delete_preset(base, "灰魂") is True


def test_落盘剥掉连接鉴权字段(tmp_path):
    base = str(tmp_path)
    p = _preset(
        [("a", True)],
        [{"identifier": "a", "content": "x", "marker": False}],
        api_key="sk-secret",
        reverse_proxy="https://proxy.example/v1",
        proxy_password="pw",
        custom_url="https://x",
        temperature=0.8,
    )
    preset_store.save(base, "带密钥", p)
    saved = preset_store.read_preset(base, "带密钥")
    assert saved is not None
    assert "api_key" not in saved and "reverse_proxy" not in saved
    assert "proxy_password" not in saved and "custom_url" not in saved
    assert saved["temperature"] == 0.8  # 非鉴权字段保留
    assert p.get("api_key") == "sk-secret"  # 原字典不被改动


def test_导入路由校验(tmp_path):
    from app.routers.preset import _parse_preset
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _parse_preset(b"bad")
    with pytest.raises(HTTPException):
        _parse_preset(b'{"foo":1}')


def test_预设级正则读写(tmp_path):
    base = str(tmp_path)
    preset_store.save(base, "p1", _preset([("main", True)], [{"identifier": "main", "content": "hi"}]))
    assert preset_store.read_regex(base, "p1") == []  # 初始无
    saved = preset_store.write_regex(base, "p1", [{"findRegex": "/a/", "replaceString": "b"}])
    assert saved is not None and saved[0]["id"]  # 补了 uuid
    got = preset_store.read_regex(base, "p1")
    assert len(got) == 1 and got[0]["findRegex"] == "/a/"
    # 其余片段原样保留
    p = preset_store.read_preset(base, "p1")
    assert p and p["prompts"][0]["content"] == "hi"
    # 预设不存在 → None
    assert preset_store.write_regex(base, "nope", []) is None


def test_预设正则兼容SillyTavern_extensions位置(tmp_path):
    base = str(tmp_path)
    preset = _preset([("main", True)], [{"identifier": "main", "content": "hi"}])
    preset["extensions"] = {
        "regex_scripts": [{"findRegex": "/<content>/g", "replaceString": ""}],
    }
    preset_store.save(base, "st", preset)

    assert preset_store.read_regex(base, "st") == preset["extensions"]["regex_scripts"]

    saved = preset_store.write_regex(
        base, "st", [{"findRegex": "/<status>/g", "replaceString": "<div>"}],
    )
    assert saved is not None and saved[0]["id"]
    updated = preset_store.read_preset(base, "st")
    assert updated is not None
    assert "regexScripts" not in updated
    assert updated["extensions"]["regex_scripts"] == saved
