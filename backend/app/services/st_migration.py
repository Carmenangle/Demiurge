"""ST 迁移第二套固定流程的机械前置层：只读体检，产出「待转写点清单」。

背景：第一套（character.import_source）是纯机械 1:1，只覆盖 §4.4「能机械 1:1」的内容；
五类「机械做不干净」的内容（注入位语义 / 渲染层 / 运行时表格 / 双存储同步习惯 /
keys 质量）必须由 LLM 判断转写。第二套固定流程 = 机械解析搬运 + LLM 转写 +
机械校验兜底。本模块就是第二套的机械层：解析入料 → 逐条目标注待转写点，
全部确定性、可单测。LLM 只做语义判断与改写，不重复机械核对。

本模块只读（analyze 不写任何文件、不改任何目录）；转写产物经既有能力落盘
（worldbook.upsert_repo / character.upsert_repo / character.import_source），
落盘形态与执行流程见 agent_knowledge《固化03-ST迁移规范》§3.5。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services import character_card

MAX_SOURCE_BYTES = 20 * 1024 * 1024  # 20MB：防误读大文件（图片头像原图上限）

# ST 注入位/排序/概率触发字段——本项目无注入位语义（§4.4 ①），转写产物不应携带
ST_INJECTION_FIELDS = (
    "insertion_order", "order", "position", "depth", "atDepth", "role", "sortFn",
    "uid", "selectiveLogic", "useProbability", "probability", "delayUntilRecursion",
)

# 渲染层特征（§4.4 ②）：ST 正则/卡片式输出约定，本项目无正则渲染层，须转纯文本段式。
# 保守高召回：命中只代表「疑似」，最终由 LLM 判断改写（本模块是检测清单不是裁决）。
RENDER_LAYER_MARKERS = (
    "<status", "<roll", "<encounter", "<hp", "<mp", "<gold", "<mood", "<energy",
    "{{roll::", "{{random::", "{{getvar::",
)

# 运行时表格特征（§4.4 ③）：ST 状态表 `<if cell=…>` 条件语法，本项目无表格插件渲染。
TABLE_MARKER = "cell="

# 常驻预算红线（§3.3）：全部 constant 条目字数建议上限。
CONSTANT_BUDGET_CHARS = 20_000


class MigrationScanError(ValueError):
    pass


def _excerpt(text: Any, limit: int = 80) -> str:
    raw = "" if text is None else str(text)
    flat = " ".join(raw.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _keys_of(item: dict[str, Any]) -> tuple[list[str], str | None]:
    """取条目的 keys（兼容 key 单数/字符串），返回 (keys, 来源字段名或 None)。"""
    if "keys" in item:
        value = item["keys"]
        source = "keys"
    elif "key" in item:
        value = item["key"]
        source = "key"
    else:
        return [], None
    if isinstance(value, str):
        keys = [value] if value.strip() else []
    elif isinstance(value, list):
        keys = [str(v).strip() for v in value if str(v).strip()]
    else:
        keys = []
    return keys, source


def _issue(code: str, label: str, severity: str, evidence: str, advice: str) -> dict[str, str]:
    return {"code": code, "label": label, "severity": severity,
            "evidence": evidence, "advice": advice}


def _constant_policy_issue(item: dict[str, Any], comment: str) -> dict[str, str] | None:
    """constant=true 但 comment 前缀不在系统判定/全局层 → 需按 §3.2 重判（LLM 裁决）。"""
    if item.get("constant") is not True:
        return None
    if comment.startswith(("系统判定", "全局机制")):
        return None
    return _issue(
        "constant_policy", "constant 越权（非系统判定/全局层常驻）", "mid",
        f"comment={comment!r} 且 constant=true",
        "按 §3.2 六层判据复核：确属系统判定/全局机制层才保留 true，否则降 false 并补 keys",
    )


def _keys_issues(item: dict[str, Any]) -> list[dict[str, str]]:
    keys, source = _keys_of(item)
    issues: list[dict[str, str]] = []
    if source is None or not keys:
        issues.append(_issue(
            "keys_empty", "keys 缺失或全空", "high",
            f"{source or '无 keys/key 字段'} → 空",
            "按 §3.3 补 2–8 字、去停用词、附世界/势力名的短 key（上限 6 个）；空 keys 条目等于不存在",
        ))
        return issues
    if len(keys) > 6:
        issues.append(_issue(
            "keys_too_many", "keys 超过 6 个", "mid",
            f"keys={keys}（{len(keys)} 个）",
            "按 §3.3 收敛到 ≤6 个：去停用词、去低频近义项，保留能稳定命中的短 key",
        ))
    for key in keys:
        if len(key) > 8 or any(ch.isspace() for ch in key):
            issues.append(_issue(
                "keys_too_long", "key 过长或含空白", "mid",
                f"key={key!r}",
                "子串命中粒度：切到 2–8 字短名/爱称（长全名在对话里很少整串出现）",
            ))
            break
    return issues


def _content_issues(content: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    lowered = content.casefold()
    if TABLE_MARKER in lowered and ("<if" in lowered or "<cell" in lowered):
        issues.append(_issue(
            "runtime_table", "含运行时表格语法（cell 条件）", "high",
            _excerpt(next((ln for ln in content.splitlines()
                           if TABLE_MARKER in ln.casefold()), "")),
            "本项目无表格插件：把 <if cell=…> 条件改写为【好感分阶】纯文本段（§3.5 五阈值）",
        ))
    hits = [marker for marker in RENDER_LAYER_MARKERS if marker in lowered]
    if hits:
        issues.append(_issue(
            "render_layer", "含 ST 渲染层标签/宏", "high",
            f"命中 {hits}；{_excerpt(content)}",
            "本项目无正则渲染层：把 <status>/<roll>/<encounter> 等改写为回复最开头的"
            "【状态栏】…纯文本段式约定（§3.4），别把玩法押在正则上",
        ))
    return issues


def _entry_issues(index: int, item: dict[str, Any]) -> dict[str, Any]:
    content = str(item.get("content") or "")
    comment = str(item.get("comment") or "")
    keys, _source = _keys_of(item)
    issues: list[dict[str, str]] = []
    dead = [field for field in ST_INJECTION_FIELDS if field in item]
    if dead:
        issues.append(_issue(
            "injection_fields", "携带 ST 注入位/排序字段", "low",
            f"{dead}",
            "本项目无注入位：转写产物删除这些字段，注入语义只由 constant + keys 表达",
        ))
    if "disable" in item:
        issues.append(_issue(
            "disable_field", "携带 ST disable 开关", "low",
            "disable 存在",
            "本项目用 enabled（enabled = not disable），转写时归一为 enabled",
        ))
    issues.extend(_keys_issues(item))
    policy = _constant_policy_issue(item, comment)
    if policy:
        issues.append(policy)
    issues.extend(_content_issues(content))
    # 视觉画像契约（§3.2 第 6 层 / worldbook_store.repo_visual_profiles 特判）
    if any(anchor in content for anchor in ("【外貌】", "【身材】", "【穿着】")) \
            and not comment.startswith("角色卡·"):
        issues.append(_issue(
            "visual_anchor", "含外貌/身材/穿着锚点但 comment 缺「角色卡·」前缀", "mid",
            f"comment={comment!r}",
            "把 comment 前缀归一为「角色卡·<角色名>」，正文保留【外貌】/【身材】/【穿着】"
            "行首锚点以激活视觉画像提取",
        ))
    return {
        "index": index,
        "comment": comment,
        "keys": keys,
        "constant": bool(item.get("constant")),
        "issues": issues,
    }


def _entries_list(book: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    raw = book.get("entries")
    if isinstance(raw, dict):
        return list(raw.values()), True
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)], False
    return [], False


def analyze_worldbook(book: dict[str, Any]) -> dict[str, Any]:
    """独立世界书或卡内嵌 character_book 的条目体检（entries 容器 dict/list 均兼容）。"""
    entries, was_dict = _entries_list(book)
    findings = [
        _entry_issues(index, item)
        for index, item in enumerate(entries)
    ]
    constant_chars = sum(
        len(str(item.get("content") or ""))
        for item in entries
        if item.get("constant") is True
    )
    notes = [
        "容器已按插入序归一为 list；落盘重建时保持 list（ST dict 容器 = 0 条坑，§4.2）",
        "comment 前缀按 §3.2 六层归一（系统判定机制·/全局机制·/世界背景·/局部机制·/角色卡·）",
        "注入语义只由 constant + keys 表达；条目级 issues 命中即待 LLM 转写点，不命中可机械直通",
    ]
    return {
        "total_entries": len(entries),
        "dict_container_normalized": was_dict,
        "constant_chars": constant_chars,
        "entries": findings,
        "notes": notes,
    }


def analyze_card(card: character_card.NormalizedCard) -> dict[str, Any]:
    """归一后角色卡的顶层体检 + 内嵌世界书条目体检。"""
    issues: list[dict[str, str]] = []
    if not card.first_mes:
        issues.append(_issue(
            "first_mes_empty", "first_mes 为空", "mid",
            "first_mes=''",
            "按 §3.1 必须非空：A 态给角色卡补开场，B 态 top-level 与 data 双写",
        ))
    profile: dict[str, Any] = {
        "name": card.name,
        "spec": card.spec,
        "first_mes_present": bool(card.first_mes),
        "has_worldbook": card.has_worldbook,
        "has_regex": card.has_regex,
        "issues": issues,
    }
    book_section = analyze_worldbook(card.character_book) if card.has_worldbook else None
    return {
        "card": profile,
        "worldbook": book_section,
        "regex_scripts": len(card.regex_scripts),
    }


def analyze_preset(obj: dict[str, Any]) -> dict[str, Any]:
    prompts = obj.get("prompts") if isinstance(obj.get("prompts"), list) else []
    regex = obj.get("regexScripts") if isinstance(obj.get("regexScripts"), list) else []
    leaks = [field for field in ("api_key", "apiKey", "reverse_proxy", "reverseProxy",
                                 "proxy", "authorization", "auth")
             if field in obj]
    issues: list[dict[str, str]] = []
    if leaks:
        issues.append(_issue(
            "preset_credentials", "含连接/鉴权字段", "high",
            f"{leaks}",
            "sanitize 会自动剥离这些字段；转写产物不得回写（§4.3）",
        ))
    if not prompts:
        issues.append(_issue(
            "preset_empty_prompts", "prompts 为空", "mid",
            "prompts 缺失或空",
            "预设若无 prompts 片段则无可启用内容，需与用户核对源文件",
        ))
    return {
        "prompt_count": len(prompts),
        "regex_scripts": len(regex),
        "issues": issues,
        "notes": ["采样参数直通；连接/鉴权字段会被 sanitize 剥除（§4.3）",
                  "预设迁移落点 = 项目预设库（用户 UI 导入），智能编造不直接写预设文件"],
    }


def analyze_regex(scripts: list[dict[str, Any]]) -> dict[str, Any]:
    without_id = [i for i, s in enumerate(scripts) if not s.get("id")]
    issues: list[dict[str, str]] = []
    if without_id:
        issues.append(_issue(
            "regex_missing_id", "正则缺稳定 id", "mid",
            f"下标 {without_id}",
            "转换时机械层会回填 uuid5 稳定 id；转写产物保留即可",
        ))
    return {
        "regex_count": len(scripts),
        "issues": issues,
        "notes": ["本项目不渲染正则：可原样保留作装饰，但玩法逻辑不能押在它上面（§3.4）"],
    }


def _kind_of(obj: Any) -> str:
    """入料类型推断（与 edit_import_adapter._infer 语义对齐，避免双份口径漂移）。"""
    if isinstance(obj, list):
        return "regex"
    if not isinstance(obj, dict):
        raise MigrationScanError("JSON 根必须是对象或数组")
    if isinstance(obj.get("prompts"), list):
        return "preset"
    if isinstance(obj.get("entries"), (list, dict)):
        return "worldbook"
    if "regexScripts" in obj or "regex_scripts" in obj:
        return "regex"
    raise MigrationScanError("无法判断入料类型：需为角色卡 / 世界书（entries）/ 预设（prompts）/ 正则")


def _count_issues(sections: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    stack: list[Any] = list(sections)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("issues"), list):
                for issue in node["issues"]:
                    code = str(issue.get("code") or "unknown")
                    counts[code] = counts.get(code, 0) + 1
            stack.extend(
                value for value in node.values()
                if isinstance(value, (dict, list))
            )
        elif isinstance(node, list):
            stack.extend(
                value for value in node
                if isinstance(value, (dict, list))
            )
    return dict(sorted(counts.items()))


def analyze_source(path: str) -> dict[str, Any]:
    """对 ST 生态文件做只读迁移体检：解析入料 → 返回待转写点清单报告。

    支持 PNG 角色卡 / JSON 角色卡 / 独立世界书 / 预设 / 正则。
    不写任何文件；落盘由转写产物经既有能力完成。
    """
    raw_path = str(path or "").strip().strip('"')
    if not raw_path:
        raise MigrationScanError("path 必须是 ST 文件（PNG/JSON）的绝对路径")
    target = Path(raw_path).expanduser()
    if not target.is_file():
        raise MigrationScanError(f"文件不存在：{raw_path}")
    data = target.read_bytes()
    if len(data) > MAX_SOURCE_BYTES:
        raise MigrationScanError(f"文件过大（>{MAX_SOURCE_BYTES} 字节），请确认是卡文件而非原图")
    filename = target.name
    report: dict[str, Any] = {
        "source": raw_path,
        "kind": "",
        "summary": "",
    }
    if data.startswith(character_card.PNG_SIGNATURE) or filename.lower().endswith(".png"):
        try:
            card = character_card.parse_card_bytes(data, filename)
        except character_card.CardParseError as exc:
            raise MigrationScanError(f"PNG 卡解析失败：{exc}") from exc
        report.update(analyze_card(card))
        report["kind"] = "character_card"
    else:
        text = data.decode("utf-8-sig", "replace")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MigrationScanError(
                f"JSON 非法：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}",
            ) from exc
        # 角色卡优先判定（与 parse_card_json 同一语义）；失败回退其它类型
        if isinstance(obj, dict) and not isinstance(obj.get("entries"), (list, dict)):
            try:
                card = character_card.parse_card_json(text)
                report.update(analyze_card(card))
                report["kind"] = "character_card"
            except character_card.CardParseError:
                report["kind"] = _kind_of(obj)
                if report["kind"] == "worldbook":
                    report.update(analyze_worldbook(obj))
                elif report["kind"] == "preset":
                    report.update(analyze_preset(obj))
                else:
                    scripts = obj.get("regexScripts") or obj.get("regex_scripts") \
                        if isinstance(obj, dict) else obj
                    report.update(analyze_regex([s for s in scripts if isinstance(s, dict)]))
        else:
            report["kind"] = _kind_of(obj)
            if report["kind"] == "worldbook":
                report.update(analyze_worldbook(obj))
            elif report["kind"] == "preset":
                report.update(analyze_preset(obj))
            else:
                scripts = obj.get("regexScripts") or obj.get("regex_scripts") \
                    if isinstance(obj, dict) else obj
                report.update(analyze_regex([s for s in scripts if isinstance(s, dict)]))

    # 常驻预算提醒（§3.3）：book_section 同时兼容卡内嵌（report["worldbook"]）
    # 与独立世界书（report 顶层即 worldbook 结构）两种形态。
    book_section: dict[str, Any] | None = report.get("worldbook")
    if not isinstance(book_section, dict) and report["kind"] == "worldbook":
        book_section = report
    constant_chars = int(book_section.get("constant_chars") or 0) \
        if isinstance(book_section, dict) else 0
    notes: list[str] = [
        "本报告是检测清单不是裁决：命中的 issue 交给 LLM 判断转写（§4.5 阶段二）",
        "成品卡/世界书导入后需按 §3.6 主角匿名红线做第二遍扫描（含台词爱称），机械无法代劳",
    ]
    if isinstance(book_section, dict) and book_section.get("entries") is not None:
        notes.append(
            f"常驻预算：{constant_chars}/{CONSTANT_BUDGET_CHARS} 字符"
            + ("（超预算，按 §3.3 把非硬机制降为非常驻 + keys）" if constant_chars
               > CONSTANT_BUDGET_CHARS else "（预算内）"),
        )
    report["report_notes"] = notes
    sections = [value for key, value in report.items()
                if key not in ("source", "kind", "summary", "report_notes")]
    report["issue_counts"] = _count_issues(sections)
    total = sum(report["issue_counts"].values())
    report["summary"] = (
        f"{report['kind']} 体检完成：{total} 个待转写点"
        f"（{report['issue_counts']}）"
    )
    return report
