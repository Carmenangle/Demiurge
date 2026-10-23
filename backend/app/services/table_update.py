"""通用表上下文、独立维护提示词与旧控制块清洗（纯逻辑）。

主 Roleplay 只读取表格上下文。剧情正文发出后，独立维护调用只返回 JSON ops；旧版
<表格更新> 仅作兼容清洗。本模块不碰 I/O、不碰 LLM、不碰存储（import-linter 强制）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.services import prompt_clean, table_store

_TAG_OPEN = "<表格更新>"
_TAG_CLOSE = "</表格更新>"
_BLOCK_RE = re.compile(re.escape(_TAG_OPEN) + r"(.*?)" + re.escape(_TAG_CLOSE), re.DOTALL)
_OPEN_TAIL_RE = re.compile(re.escape(_TAG_OPEN) + r"[\s\S]*\Z")


def table_context(tables: list[dict[str, Any]]) -> str:
    """给主 Roleplay 的只读数据表上下文，不要求模型在正文中输出维护数据。"""
    if not tables:
        return ""
    return (
        "\n\n【当前数据表（只读剧情上下文）】\n"
        + table_store.render_tables_block(tables)
        + "\n这些数据只用于保持剧情事实一致；不要在正文中输出表格更新、JSON 或维护说明。"
    )


# ── 维护协议片段（旧数组协议与 2026-09-18 对象协议共用；拼接结果逐字等于旧文案）──
_OPS_FORMAT = (
    '[{"op":"insert","table":"表名","values":{"列名":"值"}},'
    '{"op":"update","table":"表名","row":0,"values":{"列名":"新值"}},'
    '{"op":"delete","table":"表名","key":"身份列值"}]'
)
_OPS_RULES = (
    "。有身份列的表优先用 key 定位，无身份列才用 0 基 row。values 的键必须是列名。"
    "全局数据表必须输出完整现值；单卡表只能保留一行。重要角色同名更新、新角色新增；"
    "技能废除只标不可用，任务完成只更新状态，二者禁止删除；只有背包用尽或丢失时删除。"
    "没有变化时输出 []。"
    # 2026-10-22「方案 C」：累积型字段只写当前状态，把剧情经过还给纪要/时序事实。
    # 真机实测这些字段被写成整段复述（进展 263~531 字、世界状态 528 字），
    # 占读侧全表 4,003 字的 47%，且内容与纪要重复 ⇒ 每轮白付 token。
    "【字段长度·硬约束】累积型字段只写「当前状态 + 下一步」，**不写过程**（过程归纪要）："
    "进展≤120 字、世界状态≤220 字、在场状态≤120 字、当前状态≤120 字、"
    "过往经历≤180 字、人际关系≤120 字、备注≤100 字。"
    "超出会被引擎截断，等于白写——宁可短，不要复述剧情。"
)
_DELTA_RULES = (
    "state_deltas 为本轮剧情导致的状态变化数组："
    '[{"field":"数值/角色名·好感度","op":"add","value":2,"evidence":"本轮证据"}]。'
    "增量相对当前现值（add 是加多少，不是改后的总数）。单主角好感度用 field=\"数值/好感度\"；"
    "态度/心情/所在等用 field=\"叙事/xxx\" op=\"set\"；同场有多名角色时，每个字段必须写成 "
    "field=\"数值/角色名·好感度\" 或 field=\"叙事/角色名·身体状态\"，"
    "用中点明确角色归属，禁止把身体、精神等状态类别拼进角色名。"
    "字段名必须稳定复用已有字段（好感度、心情、所在、身体状态等），禁止换说法或另建同义字段。"
    "【重要·好感度初值】状态表中尚没有某角色的好感度字段时（无论其是否已经登场），禁止默认从 0 起算："
    "必须按世界观与剧情因果，综合与该角色相关的各项因素，给出该角色当前应有的好感度"
    "（含登场前与其本人/其势力的间接影响），用 op=\"set\" 播种初值（引擎仅在字段不存在时采纳，已存在则忽略）。"
    "判断方法：先列出与该角色好感相关的因素——既有关系、直接或间接接触、其自身的好恶与忌讳、"
    "{{user}} 的言行举止与仪表、所属势力与传闻、场合与时机等，以上仅为举例、不限于此、也不设固定权重——"
    "逐项判断方向与强弱，再合成一个值；不要只凭单一因素定值，也不要被单场戏的强弱牵着走。"
    "该角色自身的偏好与忌讳优先于通用关系参考：其条目写明的喜恶若与关系亲疏相冲，以其喜恶为准"
    "（例如她厌恶俊美男子，则 {{user}} 的容貌优势反而减分）。关系类别只作起点参考"
    "（亲缘/旧识/恩人/师徒/深情既有人物偏高 55-90；素不相识的中立 20-55；{{user}} 或其势力损害过其利益、"
    "敌对仇视出身偏低 -30 到 -10），可以按其人设与经历越出这一区间。"
    "虽未见面但因所属势力/听闻事迹/共同关系而事先有好恶，无论是否已经登场都按因果关系定值、不要一律 0。"
    "首次为某角色建表时按人设与既有关系把基准定准。"
    "字段一旦存在，后续发展只报 add 增量、以注入的现值为锚循序渐进，不要重定基准；"
    "增量的依据同样按上述方法综合判断，并在 evidence 里写出「哪些因素、各起什么作用」"
    "（例如「其厌恶男子近身（−）；今日替她解围（＋）」）。"
    "角色名必须是剧情角色本人：禁止用角色卡名称/卡面标题代替角色名；"
    "第一人称的「我/主角/你/玩家」不是剧情角色，主角自身的伤势、情绪等状态只写进状态栏。"
    "每条必须带 evidence 引用本轮剧情依据；本轮无变化则输出 []。"
)
_SNAPSHOT_RULES = (
    "status_snapshot 为本轮完整状态栏文本（一个多行字符串，\\n 表示换行）："
    "依据【上轮状态栏】与本轮剧情推导，栏目与格式和上轮保持一致；必须包含 [好感] 行，"
    "好感数字沿用上轮状态栏现值（系统会用引擎现值覆盖）；"
    "其他数值以上轮状态栏为基叠加本轮变化，禁止整栏重编。"
)
# 选项表专栏口径（2026-09-23 F2）：它是唯一一张「描述未来」的表，最容易被写成既成事实。
# 真机实锤——表里预写的「她终于开口以'明早得换件新中衣'点破」被当成已发生剧情照抄，
# 选项第 1 条的动作被逐字执行。措辞只约束该表，不影响其他表。
_OPTIONS_RULES = (
    "选项表只许写**尚未发生**的候选走向：每条是「可做什么」或「若怎样则怎样」，"
    "严禁写成已经发生的既成事实（不得出现「她终于开口…了」这类完成态叙述，也不得预写台词成品）；"
    "「推导依据」列只允许引用**本轮正文里已经发生**的剧情，禁止把候选本身当成依据。"
    "该表不进正文轮的剧情上下文、仅供玩家挑选，写错会被后续轮次当成既定剧情。"
)
# 叙事列事实边界（2026-09-23 用户定案）。真机实锤：表里记下的台词成品
# （「她终于开口以'明早得换件新中衣'点破」）被后续轮次当成既定剧情，于是**本该沉默
# 的角色又开了口**；同源还有「不可过激以免引发反抗」这类把角色反应提前写死的断言。
# ⇒ 表格只承载「已经发生」的客观事实，口白归正文，角色反应不许提前预演。
# 措辞刻意避开「尚未发生」「既成事实」二词（那两句是选项表专栏口径的锁定串）。
_NARRATIVE_RULES = (
    "叙事类列（当前状态/在场状态/世界状态/进展/备注/过往经历/人际关系/当前目标等）"
    "只写**已经发生**的客观事实，用陈述句概括即可；"
    "禁止把这些列写成角色的台词或口白——台词只属于剧情正文，表格里不要出现引号对白；"
    "禁止预演角色**还没发生**的反应（例如写她「将要反抗」「将要开口」这类未来指向），"
    "也不要复述备选走向。"
    "写动作就只写动作本身（谁碰了哪里、用了什么力道、对方有无退让），"
    "**不要为动作追加名目**——不要写「以『某某』为名」「以…为由」「以…为借口」「以…名义」"
    "这类给动作补一个说法的表述；角色台词里说过的说法，也不得在表格里升格"
    "为长期名分或事件名称。"
    "**施受方向必须忠实**（2026-09-23 用户定案）：写清是**谁对谁**做了这件事——"
    "主角主动做的动作不得记成对方做的，对方做的也不得记成主角做的；"
    "不得因为两人身份高低、或谁看起来更主动，就把方向调换过来写。"
    "**不要给身体接触换功法名目**：抚摸、按揉、贴靠、搂抱这类接触照原样写，"
    "不得改写成「捋经脉」「按某穴」「推动某处气机」「灌顶施治」这类功法或穴位操作的说法；"
    "是否算功法操作以剧情正文里真写过的为准，正文里没有的不要替它补上。"
)

# 2026-09-23 续十四（用户定案「彻底拆解」）：设定类列的写入口径。
# 起因＝`全局数据表·世界规则`（alwaysFill）里被写了 734 字符：既把凌若冰的**个人体质**
# 「冰凤经脉感知」升格成全局规则，又混入模型自造的临时机制（「碾磨方向翻转会导致掌控权丧失」
# 「射精后不应期敏感度翻三倍」），还复述了用户本轮输入（「敏感部位需循序渐进」）
# ⇒ 模型每轮读到「经脉感知＝性刺激传导机制」，任何场景都能顺手挂上「经脉」＝语义棘轮的齿。
# 措辞同样避开「尚未发生」「既成事实」两个锁定串。
_SETTING_RULES = (
    "设定类列（世界规则等）只写**世界书设定条目里已经成立**的长期规则"
    "（门规、体系、势力关系、功法机制），写成精炼的陈述句即可；"
    "不要把某一个角色的**个人体质或状态**、以及其**关系与人际往来经历**"
    "（谁和谁之间发生过什么）升格为全局规则，也不要写入本轮或近期"
    "演绎中临时推出来的机制；用户本轮输入里的一次性要求不是设定，不要写进设定类列。"
)


def _narrative_rules(tables: list[dict[str, Any]]) -> str:
    """有表时才附的叙事列事实边界；无表 → 空串。"""
    return _NARRATIVE_RULES if tables else ""


def _options_rules(tables: list[dict[str, Any]]) -> str:
    """表列表含选项表时才附的专栏口径；无该表 → 空串（旧文案逐字不变）。"""
    return _OPTIONS_RULES if any(
        t.get("name") == table_store.OPTIONS_TABLE for t in tables
    ) else ""


def _setting_rules(tables: list[dict[str, Any]]) -> str:
    """表列表含设定类列（世界规则）时才附；无该列 → 空串（旧文案逐字不变）。"""
    return _SETTING_RULES if any(
        "世界规则" in [str(c) for c in (t.get("columns") or [])] for t in tables
    ) else ""


def maintenance_instruction(
    tables: list[dict[str, Any]], *,
    extract_deltas: bool = False, extract_snapshot: bool = False,
) -> str:
    """独立表格维护调用的 system；输出只有 JSON，不与剧情正文共用响应。

    2026-09-18 兜底状态抽取：extract_deltas / extract_snapshot 任一开启时改收 JSON 对象
    {"ops","state_deltas","status_snapshot"}——主协议（<状态更新>/<status>）失守的轮次，
    由本轮本就在跑的维护调用顺带补齐状态写回，0 额外 LLM。开关全关时输出旧协议文案
    （拼接逐字不变，test_table_store 锁定）；无表且无抽取返回空串（上游据此跳过）。
    """
    extract = extract_deltas or extract_snapshot
    if not tables and not extract:
        return ""
    if not extract:
        return (
            "你是剧情数据表维护助手。根据本轮用户输入与已经生成完毕的剧情正文，更新下列数据表。\n"
            + table_store.render_tables_block(tables)
            + "\n\n只输出 JSON 数组，不要解释、不要代码块、不要标签。操作格式："
            + _OPS_FORMAT + _OPS_RULES + _options_rules(tables) + _narrative_rules(tables)
            + _setting_rules(tables)
        )
    if tables:
        head = (
            "你是剧情数据表维护助手。根据本轮用户输入与已经生成完毕的剧情正文，更新下列数据表。\n"
            + table_store.render_tables_block(tables)
            + "\n\n只输出一个 JSON 对象，不要解释、不要代码块、不要标签："
            + '{"ops":[…表格操作…],"state_deltas":[…状态增量…],"status_snapshot":"…状态栏文本…"}。'
            + "ops 为表格操作数组，操作格式：" + _OPS_FORMAT + _OPS_RULES
            + _options_rules(tables) + _narrative_rules(tables) + _setting_rules(tables)
        )
    else:
        head = (
            "你是剧情状态维护助手。根据本轮用户输入与已经生成完毕的剧情正文，抽取本轮状态变化。\n"
            "本轮没有数据表，ops 固定输出 []。\n"
            "只输出一个 JSON 对象，不要解释、不要代码块、不要标签："
            + '{"ops":[…],"state_deltas":[…状态增量…],"status_snapshot":"…状态栏文本…"}。'
        )
    tail = [
        _DELTA_RULES if extract_deltas
        else "本轮状态增量已由主协议取得，state_deltas 固定输出 []。",
        _SNAPSHOT_RULES if extract_snapshot
        else "本轮状态栏已由主协议取得，status_snapshot 固定输出空串 \"\"。",
    ]
    return head + "\n" + "\n".join(tail)


def sanitize_ops(ops: list[Any]) -> list[Any]:
    """对维护 ops 的文本值做破甲还原（2026-09-13 用户定案）。

    模型按防拦截预设写正文，ops 的 values / key 引用正文时会把 `@` 拆字标记一并带出；
    表格文本**不参与网关检测** ⇒ 落库必须是可读原文。只清模型输出侧——
    用户在前端手动编辑的表格不经过这里。
    """
    cleaned: list[Any] = []
    for item in ops:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        entry = dict(item)
        values = entry.get("values")
        if isinstance(values, dict):
            entry["values"] = {
                key: prompt_clean.sanitize_table_text(val) if isinstance(val, str) else val
                for key, val in values.items()
            }
        key_value = entry.get("key")
        if isinstance(key_value, str):
            entry["key"] = prompt_clean.sanitize_table_text(key_value)
        cleaned.append(entry)
    return cleaned


def _strip_json_fence(raw: str) -> str:
    value = (raw or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    return value


def _sanitize_state_deltas(deltas: list[Any]) -> list[Any]:
    """state_deltas 只清模型输出侧文本（field/evidence 破甲还原，同 sanitize_ops 口径）；
    非字典项丢弃（状态增量结构非法时宁缺勿滥，交由 parse_deltas 兜底）。"""
    cleaned: list[Any] = []
    for item in deltas:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        for key in ("field", "evidence"):
            value = entry.get(key)
            if isinstance(value, str):
                entry[key] = prompt_clean.sanitize_table_text(value)
        cleaned.append(entry)
    return cleaned


def parse_maintenance_payload(raw: str) -> tuple[list[Any] | None, list[Any], str]:
    """解析独立维护响应，兼容两种形态（2026-09-18 兜底状态抽取扩展）。

    - JSON 数组（旧协议）→ (ops, [], "")
    - JSON 对象（扩展协议）{"ops","state_deltas","status_snapshot"} → 三元组，缺键取默认；
      status_snapshot 必须含 "[好感]" 行才采纳（防散文污染快照真源）。
    首位 None 表示截断/坏格式/顶层标量；对象内 ops 键非数组按无表格操作处理
    （state_deltas / status_snapshot 照常生效，状态安全优先于报错）。
    """
    try:
        data = json.loads(_strip_json_fence(raw))
    except (json.JSONDecodeError, ValueError):
        return None, [], ""
    if isinstance(data, list):
        return sanitize_ops(data), [], ""
    if isinstance(data, dict):
        ops = data.get("ops")
        ops = sanitize_ops(ops) if isinstance(ops, list) else []
        deltas = data.get("state_deltas")
        deltas = _sanitize_state_deltas(deltas) if isinstance(deltas, list) else []
        snapshot = data.get("status_snapshot")
        snapshot = snapshot.strip() if isinstance(snapshot, str) else ""
        if "[好感]" not in snapshot:
            snapshot = ""
        return ops, deltas, snapshot
    return None, [], ""


def parse_table_block(reply: str) -> tuple[str, list[Any]]:
    """从叙述里剥离 <表格更新> 块，返回（去块正文, 原始 ops 列表）。

    缺块 → 原文 + []；块内 JSON 坏 → 去块正文 + []（叙述照常，不因解析失败丢内容）。
    """
    m = _BLOCK_RE.search(reply or "")
    if not m:
        tail = _OPEN_TAIL_RE.search(reply or "")
        if tail:
            return (reply or "")[:tail.start()].strip(), []
        return reply, []
    clean = _BLOCK_RE.sub("", reply).strip()
    try:
        raw = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return clean, []
    return clean, sanitize_ops(raw) if isinstance(raw, list) else []


def has_table_block(reply: str) -> bool:
    value = reply or ""
    return _BLOCK_RE.search(value) is not None or _OPEN_TAIL_RE.search(value) is not None
