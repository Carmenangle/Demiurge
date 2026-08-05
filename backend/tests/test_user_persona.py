"""用户人设注入（缺口4）：_render_user_persona + _resolve_preset marker 填充。"""
from __future__ import annotations

from app.services import agent_graph as ag
from app.services.agent_contracts import RunContext


def test_名与描述都空返回空():
    assert ag._render_user_persona({}) == ""
    assert ag._render_user_persona({"user_name": "  ", "user_persona": ""}) == ""


def test_只有名也渲染():
    out = ag._render_user_persona({"user_name": "叶凡"})
    assert "叶凡" in out
    assert out.startswith("【用户扮演（叶凡）】")


def test_只有描述无名():
    out = ag._render_user_persona({"user_persona": "一个路过的散修"})
    assert out.startswith("【用户扮演】")
    assert "一个路过的散修" in out


def test_名与描述都有():
    out = ag._render_user_persona({"user_name": "叶凡", "user_persona": "散修，性子冷"})
    assert "叶凡" in out and "散修，性子冷" in out


def test_resolve_preset_填persona与user_name(tmp_path):
    # 预设：一个 personaDescription marker + 一个含 {{user}} 宏的文本片段
    preset = {
        "prompts": [
            {"identifier": "pd", "marker": True},
            {"identifier": "greet", "content": "你面对的是 {{user}}。"},
        ],
        "prompt_order": [{"order": [
            {"identifier": "pd", "enabled": True},
            {"identifier": "greet", "enabled": True},
        ]}],
    }
    # personaDescription 的 identifier 需匹配 _MARKER_KEYS 映射
    preset["prompts"][0]["identifier"] = "personaDescription"
    preset["prompt_order"][0]["order"][0]["identifier"] = "personaDescription"
    (tmp_path / "p.json").write_text(
        __import__("json").dumps(preset, ensure_ascii=False), encoding="utf-8")

    ctx = {
        "preset_dir": str(tmp_path), "preset_name": "p",
        "character_dir": "", "card_name": "",
        "user_name": "叶凡", "user_persona": "散修，性子冷",
    }
    messages, _temp, _has_hist, _ct, _ch = ag._resolve_preset(ctx, "")
    system = "\n\n".join(m["content"] for m in messages)  # 现返回带 role 的多条消息
    assert "散修，性子冷" in system   # persona marker 填入
    assert "叶凡" in system           # {{user}} 宏替换


def test_resolve_preset_思维链宏替换(tmp_path):
    # 思维链（状态栏模板常放这）里的 {{user}} 也须替换，否则被模型照抄进正文（截图里的 bug）
    preset = {
        "prompts": [], "prompt_order": [{"order": []}],
        "thinking_chains": [
            {"name": "状态栏", "position": "tail", "content": "输出 {{user}} 对{{char}}的好感度"},
        ],
    }
    (tmp_path / "p.json").write_text(
        __import__("json").dumps(preset, ensure_ascii=False), encoding="utf-8")
    ctx = {
        "preset_dir": str(tmp_path), "preset_name": "p",
        "character_dir": "", "card_name": "",
        "user_name": "叶凡", "user_persona": "",
    }
    _msgs, _temp, _has_hist, chains_tail, _ch = ag._resolve_preset(ctx, "")
    assert chains_tail and "叶凡" in chains_tail[0]      # {{user}} 已替换
    assert "{{user}}" not in chains_tail[0]              # 无字面残留


def test_resolve_preset_从RunContext重注入本轮完整输入(tmp_path):
    preset = {
        "prompts": [{
            "identifier": "last-user", "role": "user",
            "content": "<user last input>{{lastUserMessage}}</user last input>",
        }],
        "prompt_order": [{"order": [{"identifier": "last-user", "enabled": True}]}],
    }
    (tmp_path / "p.json").write_text(
        __import__("json").dumps(preset, ensure_ascii=False), encoding="utf-8")
    ctx = RunContext(thread_id="t", message="本轮完整用户输入", preset_dir=str(tmp_path), preset_name="p")

    messages, *_ = ag._resolve_preset(ctx, "")

    joined = "\n".join(m["content"] for m in messages)
    assert "本轮完整用户输入" in joined
    assert "{{lastUserMessage}}" not in joined


# ── 前端插画开关（缺口·D 阶段接通）：_build_renderer 按 illustrate + 生图配置构建 ──

def test_build_renderer_开关关返回None():
    assert ag._build_renderer({"gen_base": "http://x/v1", "gen_model": "m"}) is None


def test_build_renderer_开关开但缺生图配置返回None():
    assert ag._build_renderer({"illustrate": True}) is None
    assert ag._build_renderer({"illustrate": True, "gen_base": "http://x/v1"}) is None


def test_build_renderer_开关开且配置全返回renderer():
    r = ag._build_renderer({
        "illustrate": True, "gen_base": "http://x/v1", "gen_key": "k",
        "gen_model": "dall-e-3", "size": "1024x1024", "image_quality": "high"})
    assert callable(r)
