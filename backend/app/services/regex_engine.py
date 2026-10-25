"""正则引擎单一属主：对标 SillyTavern extensions/regex/engine.js 的纯逻辑。

一条脚本 = ST RegexScriptData：
    findRegex, replaceString, trimStrings[], placement[](见 Placement),
    disabled, markdownOnly, promptOnly, runOnEdit, substituteRegex, minDepth, maxDepth

三档行为（对标 ST getRegexedString 的过滤）：
    - markdownOnly=True  → 只在「显示层」跑（is_markdown 时）。改的是给人看的文本，不落库不入提示。
    - promptOnly=True    → 只在「发给模型」时跑（is_prompt 时）。不落显示、不改存储源。
    - 两者都 False       → 改「存储源」（既不是 markdown 也不是 prompt 的默认场景），落库+后续都带。

placement 决定作用对象（用户输入/AI输出/快捷命令/世界信息/推理）；depth 决定深度门控。

JS→Python 方言差异（本模块负责吸收，纯函数可单测）：
    - 命名组 (?<name>)  → (?P<name>)；命名反查 \\k<name> → (?P=name)
    - flag：g 无对应（Python 用 re.sub 全替）；i→IGNORECASE；s→DOTALL；m→MULTILINE；u/y 忽略
    - 替换里 $1/$<name>/{{match}} 自己解析（ST 语义），不用 Python 的 \\1

不做 I/O、不读设置、不碰 LLM。脚本来源（全局库/卡内 regex.json）由调用方注入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class Placement:
    """正则作用范围（对标 ST regex_placement）。"""
    MD_DISPLAY = 0        # 已废弃，保留数字兼容
    USER_INPUT = 1
    AI_OUTPUT = 2
    SLASH_COMMAND = 3
    WORLD_INFO = 5
    REASONING = 6
    IMAGE_PROMPT = 7      # 出图提示词提取：破甲标记还原 + 清洗成干净 booru 串（Demiurge 扩展）


class SubstituteMode:
    NONE = 0
    RAW = 1
    ESCAPED = 2


@dataclass
class RegexScript:
    """归一后的正则脚本。从卡 regex_scripts / 全局库 JSON 解析。"""
    find_regex: str
    replace_string: str = ""
    trim_strings: list[str] = field(default_factory=list)
    placement: list[int] = field(default_factory=list)
    disabled: bool = False
    markdown_only: bool = False
    prompt_only: bool = False
    run_on_edit: bool = True
    min_depth: int | None = None
    max_depth: int | None = None
    script_name: str = ""
    substitute_regex: int = 0  # 查找时的宏替换：0 不替换 / 1 原始 / 2 转义（对标 ST substituteRegex）


def from_st_dict(d: dict[str, Any]) -> RegexScript:
    """把 ST 格式的脚本 dict（camelCase）归一到 RegexScript。缺字段取安全默认。"""
    def _num(v: Any) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    placement = d.get("placement")
    return RegexScript(
        find_regex=str(d.get("findRegex") or ""),
        replace_string=str(d.get("replaceString") or ""),
        trim_strings=[str(t) for t in (d.get("trimStrings") or []) if str(t)],
        placement=[int(p) for p in placement if isinstance(p, (int, float))] if isinstance(placement, list) else [],
        disabled=bool(d.get("disabled")),
        markdown_only=bool(d.get("markdownOnly")),
        prompt_only=bool(d.get("promptOnly")),
        run_on_edit=bool(d.get("runOnEdit", True)),
        min_depth=_num(d.get("minDepth")),
        max_depth=_num(d.get("maxDepth")),
        script_name=str(d.get("scriptName") or d.get("id") or ""),
        substitute_regex=_num(d.get("substituteRegex")) or 0,
    )


# ── JS 正则 → Python re 转换 ──────────────────────────────────────────

_JS_FLAG_TO_RE = {"i": re.IGNORECASE, "s": re.DOTALL, "m": re.MULTILINE, "x": re.VERBOSE}

# ── ReDoS 防护（2026-10-24 二轮审计）：确定性启发，不是沙箱 ──────────────
# findRegex 是第三方内容（社区角色卡/预设导入），会在**每条消息**上自动执行；Python re
# 无执行超时，一条指数回溯模式即可挂死剧情回合线程。三道确定性防线：
# ① pattern/输入/脚本数上限；② 「组内含无界量词化原子 + 组自身被无界量词修饰」的指数
#    经典形态拒编译（返回 None → 跳过脚本，与「编译失败→None→跳过」的既有 fail-open
#    语义一致；edit_artifacts 的导入编译校验因此对坏卡 fail-closed）；
# ③ 已知局限（登记而非假装解决）：纯交替歧义（(a|aa)+）与多项式形态不在拦截之列——
#    要拦截得换带超时的执行引擎，属另立刀。双端合同：frontend/src/lib/regexEngine.ts
#    是同逻辑 JS 版，本段常量与判定必须两边同步改（各有测试锁值）。
MAX_PATTERN_CHARS = 2000
MAX_INPUT_CHARS = 400_000
MAX_SCRIPTS_PER_RUN = 64


def _is_quant(body: str, i: int) -> bool:
    """body[i] 起是否量词 token：+ * ? 或 {n}/{n,m}/{n,}。落单 { 按字面量（re 同语义）。"""
    if i >= len(body):
        return False
    c = body[i]
    if c in "+*?":
        return True
    if c == "{":
        close = body.find("}", i + 1)
        if close != -1 and close - i <= 32:
            inner = body[i + 1:close]
            return any(ch.isdigit() for ch in inner) and all(
                ch.isdigit() or ch == "," for ch in inner)
    return False


def _quant_unbounded(body: str, i: int) -> bool:
    """量词是否有界：+ * {n,} 无界（回溯可指数）；? {n,m} 有界（回溯上限常数/多项式有界）。"""
    c = body[i]
    if c in "+*":
        return True
    if c == "{":
        close = body.find("}", i + 1)
        if close != -1:
            upper = body[i + 1:close].split(",", 1)
            return len(upper) == 2 and not upper[1].strip().isdigit()
    return False


def _skip_quant(body: str, i: int) -> int:
    """跳过量词本体（{..} 整段）与惰性 ? 后缀，返回原子终点之后的下标。"""
    j = i + 1
    if body[i] == "{":
        j = body.find("}", i + 1)
        j = len(body) if j == -1 else j + 1
    if j < len(body) and body[j] == "?":
        j += 1
    return j


def _has_nested_unbounded_quant(body: str) -> bool:
    """检测 (a+)+ 家族：某层的组被无界量词修饰、且组内顶层已有无界量词化原子。

    有界外量词（(a+)?、(?:https?://)?）放行——真实卡里大量存在且不构成指数回溯。
    扫描跳过转义、字符类与 (?# 注释；锚点 ^$ 跟量词本就 re.error，误登记无害。
    """
    n = len(body)
    i = 0
    depth = 0
    level_unbounded: list[bool] = [False]  # 每层组：顶层是否出现「原子 + 无界量词」
    while i < n:
        c = body[i]
        if c == "\\":
            i += 2
        elif c == "[":
            j = i + 1
            if j < n and body[j] == "^":
                j += 1
            if j < n and body[j] == "]":
                j += 1
            while j < n and body[j] != "]":
                j += 2 if body[j] == "\\" else 1
            i = j + 1
        elif c == "(":
            if body.startswith("(?#", i):
                end = body.find(")", i + 3)
                i = n if end == -1 else end + 1
                continue
            depth += 1
            level_unbounded.append(False)
            i += 1
            continue
        elif c == ")":
            i += 1
            if depth > 0:
                inner = level_unbounded.pop()
                depth -= 1
                if _is_quant(body, i):
                    unbounded = _quant_unbounded(body, i)
                    if inner and unbounded:
                        return True
                    if unbounded:
                        level_unbounded[depth] = True
                    i = _skip_quant(body, i)
            continue
        else:
            i += 1
        if i < n and _is_quant(body, i) and _quant_unbounded(body, i):
            level_unbounded[depth] = True
            i = _skip_quant(body, i)
    return False


def _js_body_flags(pattern: str) -> tuple[str, str]:
    """拆 /body/flags 形式；非该形式则整串作 body、无 flags。"""
    if len(pattern) >= 2 and pattern.startswith("/"):
        last = pattern.rfind("/")
        if last > 0:
            return pattern[1:last], pattern[last + 1:]
    return pattern, ""


def _translate_named_groups(body: str) -> str:
    """(?<name>) → (?P<name>)；\\k<name> → (?P=name)。跳过 (?<= 和 (?<! 环视。"""
    body = re.sub(r"\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>", r"(?P<\1>", body)
    body = re.sub(r"\\k<([a-zA-Z_][a-zA-Z0-9_]*)>", r"(?P=\1)", body)
    return body


def compile_js_regex(pattern: str) -> re.Pattern[str] | None:
    """把 JS 风格正则（可含 /.../flags）编译成 Python Pattern。失败返回 None（对标 ST regexFromString 容错）。

    ReDoS 防线在此收口：超长 pattern 与嵌套无界量词形态一律 None（调用方按既有
    语义跳过该脚本；edit_artifacts 的导入校验因此对坏卡拒绝）。
    """
    if not pattern:
        return None
    if len(pattern) > MAX_PATTERN_CHARS:
        return None
    body, flags = _js_body_flags(pattern)
    if _has_nested_unbounded_quant(body):
        return None
    body = _translate_named_groups(body)
    re_flags = 0
    for ch in flags:
        re_flags |= _JS_FLAG_TO_RE.get(ch, 0)
    try:
        return re.compile(body, re_flags)
    except re.error:
        return None


def _filter_match(matched: str, trim_strings: list[str]) -> str:
    """从匹配里去掉 trimStrings（对标 ST filterString）。"""
    out = matched
    for t in trim_strings:
        if t:
            out = out.replace(t, "")
    return out


def _build_replacement(script: RegexScript, m: re.Match[str]) -> str:
    """按 ST 语义组装单次替换结果：{{match}}→$0，$1/$<name> 取组并过 trim。"""
    repl = script.replace_string.replace("{{match}}", "$0")

    def sub(mo: re.Match[str]) -> str:
        num, name = mo.group(1), mo.group(2)
        try:
            if num is not None:
                val = m.group(0) if int(num) == 0 else m.group(int(num))
            else:
                val = m.group(name)  # 组不存在 → IndexError
        except IndexError:
            val = None
        if not val:
            return ""
        return _filter_match(val, script.trim_strings)

    return re.sub(r"\$(\d+)|\$<([^>]+)>", sub, repl)


def _substitute_find_macros(find: str, mode: int, markers: dict[str, str] | None) -> str:
    """substituteRegex：把 find 里的 {{char}}/{{user}} 宏按当前值替换后再编译（对标 ST）。

    mode 0=不替换（原样）；1=原始值；2=转义值（值里的正则元字符转义，当字面量匹配）。
    markers 缺失或 mode=0 → 原样返回（本项目宏仅 char/user）。
    """
    if mode == SubstituteMode.NONE or not markers:
        return find
    char = markers.get("char_name", "")
    user = markers.get("user_name", "") or "我"
    def _val(v: str) -> str:
        return re.escape(v) if mode == SubstituteMode.ESCAPED else v
    return find.replace("{{char}}", _val(char)).replace("{{user}}", _val(user))


def run_script(script: RegexScript, text: str, *, markers: dict[str, str] | None = None) -> str:
    """在 text 上跑单条脚本。disabled/空 find/空 text → 原样返回（对标 runRegexScript）。

    markers：{char_name,user_name} 供 substituteRegex 替换 find 里的宏（缺省不替换）。
    """
    if script.disabled or not script.find_regex or not text:
        return text
    find = _substitute_find_macros(script.find_regex, script.substitute_regex, markers)
    pattern = compile_js_regex(find)
    if pattern is None:
        return text
    return pattern.sub(lambda m: _build_replacement(script, m), text)


def _applies(script: RegexScript, *, is_markdown: bool, is_prompt: bool,
             is_edit: bool, depth: int | None) -> bool:
    """对标 ST getRegexedString 的三档 + edit + depth 过滤（不含 placement，那单独判）。"""
    # 三档：markdownOnly 仅 markdown；promptOnly 仅 prompt；两者皆非 仅非 md 非 prompt
    if not ((script.markdown_only and is_markdown)
            or (script.prompt_only and is_prompt)
            or (not script.markdown_only and not script.prompt_only and not is_markdown and not is_prompt)):
        return False
    if is_edit and not script.run_on_edit:
        return False
    if depth is not None:
        if script.min_depth is not None and script.min_depth >= -1 and depth < script.min_depth:
            return False
        if script.max_depth is not None and script.max_depth >= 0 and depth > script.max_depth:
            return False
    return True


def run_scripts(text: str, placement: int, scripts: list[RegexScript], *,
                is_markdown: bool = False, is_prompt: bool = False,
                is_edit: bool = False, depth: int | None = None,
                skip_depth_gated: bool = False) -> str:
    """对标 ST getRegexedString：按顺序跑所有命中 placement 且通过三档/edit/depth 过滤的脚本。

    text 非串 / placement 未指定 → 原样返回。脚本无 placement 视为不限（全 placement 生效）。

    skip_depth_gated：跳过设了 minDepth/maxDepth 的脚本。深度门控语义针对**历史楼层**（如「删掉
    history 中最后一条用户消息」maxDepth=1）；而本轮刚输入、尚未入历史的实时消息在本架构里不属于
    历史楼层，不应被历史级删除/改写脚本波及。处理 live 当前轮时置真，避免实时输入被历史正则误擦。
    """
    if not isinstance(text, str) or not text:
        return text
    # ReDoS 上限（2026-10-24）：超长输入整轮跳过、脚本数截到前 N（保 ST 顺序优先级）。
    # 形态级拦截在 compile_js_regex；这里的上限兜住「合法但慢」的剩余面。
    if len(text) > MAX_INPUT_CHARS:
        return text
    scripts = scripts[:MAX_SCRIPTS_PER_RUN]
    out = text
    for script in scripts:
        if skip_depth_gated and (script.min_depth is not None or script.max_depth is not None):
            continue
        if not _applies(script, is_markdown=is_markdown, is_prompt=is_prompt,
                        is_edit=is_edit, depth=depth):
            continue
        if script.placement and placement not in script.placement:
            continue
        out = run_script(script, out)
    return out
