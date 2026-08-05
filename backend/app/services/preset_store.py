"""偏置预设单一属主：解析 SillyTavern OpenAI 预设 + 组装成扮演 system 串。

ST 预设结构（如 GrayWill）：
    prompts[]        片段：{identifier, name, role, content, marker(bool), system_prompt, ...}
    prompt_order[0].order[]  排序+开关：{identifier, enabled}
    8 个 marker 占位：personaDescription/worldInfoBefore/charDescription/charPersonality/
                     scenario/worldInfoAfter/dialogueExamples/chatHistory
    采样参数：temperature/top_p/... （透传给模型）

**折中注入**（本项目采纳，见 ARCHITECTURE「明确不做的」）：
    Demiurge 的 `_llm.chat` 只收 system+user 两条串，无法完整还原 ST 的每片段独立 role +
    injection_depth 插进历史某深度（那要改 llm 深模块 + 动 8 端点）。故这里按 prompt_order+enabled
    排序，marker 位展开为卡字段/世界书/persona，text 片段按 role 折叠进 system，组装成单 system 串。
    chatHistory marker 跳过（历史由 roleplay_node 的 user 侧带）。片段开关沿用 ST 的 order.enabled。

落盘：presetDir/<安全名>.json。纯解析+组装，不跑 LLM、不读全局 config。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.pathnames import safe_dir

# marker → 用 markers dict 里哪个键填充（chatHistory 特殊：跳过）
_MARKER_KEYS = {
    "personaDescription": "persona",
    "worldInfoBefore": "worldbook",
    "charDescription": "char_description",
    "charPersonality": "char_personality",
    "scenario": "scenario",
    "worldInfoAfter": "worldbook_after",
    "dialogueExamples": "dialogue_examples",
}
_SKIP_MARKERS = {"chatHistory"}

# 导入时剥掉的连接/鉴权字段：ST 预设常内嵌供应商密钥与代理，Demiurge 不用（走设置里
# 的 chatModels），落盘前一律删除，避免明文残留在磁盘。
_STRIP_FIELDS = frozenset({
    "api_key", "apiKey", "reverse_proxy", "proxy_password", "proxy_preset",
    "custom_url", "custom_include_headers", "custom_include_body",
    "custom_exclude_body", "api_url_scale",
})


def sanitize(preset: dict[str, Any]) -> dict[str, Any]:
    """返回剥掉连接/鉴权字段的浅拷贝（原字典不动）。"""
    return {k: v for k, v in preset.items() if k not in _STRIP_FIELDS}


@dataclass
class PresetSummary:
    name: str
    file: str
    prompts: int
    enabled: int


def _path(base: str, name: str) -> Path:
    return Path(base) / f"{safe_dir(name)}.json"


def exists(base: str, name: str) -> bool:
    return _path(base, name).is_file()


def _counts(preset: dict[str, Any]) -> tuple[int, int]:
    prompts = preset.get("prompts") or []
    order = (preset.get("prompt_order") or [{}])
    order_list = (order[0].get("order") if order and isinstance(order[0], dict) else []) or []
    enabled = sum(1 for o in order_list if isinstance(o, dict) and o.get("enabled"))
    return len(prompts), enabled


def substitute_macros(text: str, markers: dict[str, str]) -> str:
    """替换 {{char}}/{{user}} 宏（ST 常用）+ {{lastUserMessage}}/{{lastCharMessage}}，其余留原样。

    {{user}} 缺省名回退「我」（用户没填人设名时，避免字面 {{user}} 漏进提示词/输出）。
    {{char}} 缺省不替换（保留原样，通常卡有名）。
    {{lastUserMessage}} / {{lastCharMessage}}：ST 深度重注入范式配套宏——预设常配「擦除历史最后一条
    用户消息 + 在指定深度重注入 {{lastUserMessage}}」实现越甲。缺省空串（对应 marker 不存在时留原样，
    避免字面宏漏进提示词被模型照抄）。大小写不敏感（ST 宏惯例）。
    """
    if not text:
        return text
    char = markers.get("char_name", "")
    user = markers.get("user_name", "") or "我"
    out = text
    for macro, val in (("{{char}}", char), ("{{user}}", user)):
        if val:
            out = out.replace(macro, val)
    # 末轮消息宏（大小写不敏感）：有对应 marker 才替换（含空串——重注入位应显式清空而非留字面宏）
    for macro_l, key in (("{{lastusermessage}}", "last_user_message"),
                         ("{{lastcharmessage}}", "last_char_message")):
        if key in markers:
            out = _re_ci(macro_l).sub(lambda _m, v=markers[key]: v, out)
    return out


_CI_CACHE: dict[str, "re.Pattern[str]"] = {}


def _re_ci(macro_lower: str) -> "re.Pattern[str]":
    """大小写不敏感的宏字面匹配（转义后缓存）。"""
    pat = _CI_CACHE.get(macro_lower)
    if pat is None:
        pat = re.compile(re.escape(macro_lower), re.IGNORECASE)
        _CI_CACHE[macro_lower] = pat
    return pat


def assemble_system(preset: dict[str, Any], markers: dict[str, str]) -> str:
    """按 prompt_order+enabled 排序，marker 展开 + text 片段折叠 → 单 system 串。

    markers 键：persona/worldbook/worldbook_after/char_description/char_personality/
    scenario/dialogue_examples/char_name/user_name。缺省视为空（该 marker 跳过）。
    """
    prompts = {p.get("identifier"): p for p in (preset.get("prompts") or []) if isinstance(p, dict)}
    order_wrap = preset.get("prompt_order") or []
    order = (order_wrap[0].get("order") if order_wrap and isinstance(order_wrap[0], dict) else []) or []

    parts: list[str] = []
    for entry in order:
        if not (isinstance(entry, dict) and entry.get("enabled")):
            continue
        p = prompts.get(entry.get("identifier"))
        if not p:
            continue
        if p.get("marker"):
            ident = p.get("identifier")
            if ident in _SKIP_MARKERS:
                continue
            key = _MARKER_KEYS.get(ident)
            val = substitute_macros((markers.get(key, "") if key else "").strip(), markers)
            if val:
                parts.append(val)
        else:
            content = substitute_macros((p.get("content") or "").strip(), markers)
            if content:
                parts.append(content)
    return "\n\n".join(parts)


def assemble_messages(
    preset: dict[str, Any], markers: dict[str, str], history: list[dict] | None = None,
) -> list[dict[str, str]]:
    """按 prompt_order+enabled 组装成**多条带 role 的消息**（不折叠成单 system 串）。

    与 `assemble_system` 的区别：保留每个片段自己的 role(system/user/assistant)，
    `chatHistory` marker 处**原位插入历史对话**（实现 ST 的深度注入语义——历史在预设指定的
    位置出现，而非一律塞到 user 侧）。无 chatHistory marker 时历史不自动插入（由调用方决定）。

    返回 [{"role":..,"content":..}]。marker 位展开为卡字段/世界书/persona（均为 system）。
    """
    prompts = {p.get("identifier"): p for p in (preset.get("prompts") or []) if isinstance(p, dict)}
    order_wrap = preset.get("prompt_order") or []
    order = (order_wrap[0].get("order") if order_wrap and isinstance(order_wrap[0], dict) else []) or []
    hist = history or []

    msgs: list[dict[str, str]] = []
    for entry in order:
        if not (isinstance(entry, dict) and entry.get("enabled")):
            continue
        p = prompts.get(entry.get("identifier"))
        if not p:
            continue
        if p.get("marker"):
            ident = p.get("identifier")
            if ident == "chatHistory":
                # 原位插入历史（保留每轮 user/assistant role）
                for h in hist:
                    content = (h.get("content") or "").strip()
                    if content:
                        msgs.append({"role": h.get("role") or "user", "content": content})
                continue
            key = _MARKER_KEYS.get(ident)
            val = substitute_macros((markers.get(key, "") if key else "").strip(), markers)
            if val:
                msgs.append({"role": "system", "content": val})
        else:
            content = substitute_macros((p.get("content") or "").strip(), markers)
            if content:
                # ST role 缺省 system；user/assistant 少样本片段保留自身 role
                msgs.append({"role": (p.get("role") or "system"), "content": content})
    return msgs


def has_history_marker(preset: dict[str, Any]) -> bool:
    """预设的 prompt_order 里是否有启用的 chatHistory marker（决定历史插在预设内还是尾部）。"""
    prompts = {p.get("identifier"): p for p in (preset.get("prompts") or []) if isinstance(p, dict)}
    order_wrap = preset.get("prompt_order") or []
    order = (order_wrap[0].get("order") if order_wrap and isinstance(order_wrap[0], dict) else []) or []
    for entry in order:
        if isinstance(entry, dict) and entry.get("enabled") and entry.get("identifier") == "chatHistory":
            p = prompts.get("chatHistory")
            if p and p.get("marker"):
                return True
    return False


def select_chains(
    preset: dict[str, Any], *, scene: str = "", affinity: float | None = None, turn: int = 0,
) -> tuple[list[str], list[str]]:
    """按真实状态条件从预设的 thinking_chains 选中匹配的推理链，返回 (尾部注入, 头部注入)。

    thinking_chains: [{name, content, position:"tail"|"head", when:{...}}]。when 全部满足才命中：
      - scene: 场景标签相等（dialogue/action/emotion/conflict/nsfw/climax），缺省不限。
      - affinity_lt / affinity_gt: 好感度阈值（affinity 为 None 时该条件视为不满足）。
      - turn_mod: [n, r] → turn % n == r（周期触发），缺省不限。
    条件基于**真状态**（非字符串宏），这是相对 ST 变量系统的优势。无 thinking_chains → ([],[])。
    """
    chains = preset.get("thinking_chains")
    if not isinstance(chains, list):
        return [], []
    tail: list[str] = []
    head: list[str] = []
    for ch in chains:
        if not isinstance(ch, dict):
            continue
        content = (ch.get("content") or "").strip()
        if not content:
            continue
        when = ch.get("when") if isinstance(ch.get("when"), dict) else {}
        if not _chain_matches(when, scene=scene, affinity=affinity, turn=turn):
            continue
        (head if ch.get("position") == "head" else tail).append(content)
    return tail, head


def _chain_matches(when: dict, *, scene: str, affinity: float | None, turn: int) -> bool:
    """判一条链的 when 条件是否全部满足。空 when → 恒真（无条件链，每轮都挂）。"""
    want_scene = (when.get("scene") or "").strip()
    if want_scene and want_scene != scene:
        return False
    lt = when.get("affinity_lt")
    if isinstance(lt, (int, float)) and not (affinity is not None and affinity < lt):
        return False
    gt = when.get("affinity_gt")
    if isinstance(gt, (int, float)) and not (affinity is not None and affinity > gt):
        return False
    tm = when.get("turn_mod")
    if isinstance(tm, (list, tuple)) and len(tm) == 2:
        n, r = tm
        if not (isinstance(n, int) and n > 0 and turn % n == int(r)):
            return False
    return True


def sampling_params(preset: dict[str, Any]) -> dict[str, Any]:
    """从预设取采样参数（供模型透传）。只取常用几项，缺失不返回。"""
    out: dict[str, Any] = {}
    for key in ("temperature", "top_p", "top_k", "frequency_penalty", "presence_penalty"):
        v = preset.get(key)
        if isinstance(v, (int, float)):
            out[key] = v
    return out


# ── 落盘 ──────────────────────────────────────────────

def save(base: str, name: str, preset: dict[str, Any], *, overwrite: bool = False) -> PresetSummary:
    if not base:
        raise ValueError("未设置预设文件夹路径")
    p = _path(base, name)
    if p.is_file() and not overwrite:
        raise FileExistsError(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    preset = sanitize(preset)
    p.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
    n, e = _counts(preset)
    return PresetSummary(name=name, file=p.name, prompts=n, enabled=e)


def list_presets(base: str) -> list[PresetSummary]:
    root = Path(base)
    if not root.is_dir():
        return []
    out: list[PresetSummary] = []
    for child in sorted(root.glob("*.json")):
        try:
            preset = json.loads(child.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(preset, dict) or "prompts" not in preset:
            continue
        n, e = _counts(preset)
        out.append(PresetSummary(name=child.stem, file=child.name, prompts=n, enabled=e))
    return out


def read_preset(base: str, name: str) -> dict[str, Any] | None:
    p = _path(base, name)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def read_regex(base: str, name: str) -> list[dict[str, Any]]:
    """读某预设内嵌的正则脚本（regexScripts 键）。无预设/无键 → 空列表。"""
    preset = read_preset(base, name)
    if not isinstance(preset, dict):
        return []
    scripts = preset.get("regexScripts")
    return [s for s in scripts if isinstance(s, dict)] if isinstance(scripts, list) else []


def write_regex(base: str, name: str, scripts: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """把正则脚本写入某预设的 regexScripts 键（覆盖整份预设落盘）。预设不存在 → None。

    仅动 regexScripts 键，其余片段/顺序原样保留。给缺 id 的脚本补 uuid（与全局库一致）。
    """
    from uuid import uuid4
    preset = read_preset(base, name)
    if not isinstance(preset, dict):
        return None
    clean: list[dict[str, Any]] = []
    for s in scripts or []:
        if not isinstance(s, dict):
            continue
        s = dict(s)
        if not s.get("id"):
            s["id"] = uuid4().hex
        clean.append(s)
    preset["regexScripts"] = clean
    p = _path(base, name)
    p.write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def delete_preset(base: str, name: str) -> bool:
    p = _path(base, name)
    if not p.is_file():
        return False
    p.unlink()
    return not p.is_file()
