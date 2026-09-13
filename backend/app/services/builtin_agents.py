"""内置 Agent 注册表 + 覆盖存储。

图里的内置角色（supervisor / roleplay / answer / 世界 Agent / 裁判 / 各生图专家）默认参数
原本硬编码埋在 agent_graph / roleplay_agency，前端看不见也改不了。本模块把这些默认抽成**单一属主**
的注册表，并读 data/builtin_agents.json 的用户覆盖，供前端「内置智能体」面板展示 + 编辑。

依赖方向：本模块是叶子（只 import app.config）。agent_graph / roleplay_agency 反过来 import 本模块
取默认常量与运行时 resolved()，不构成环，也不违反 import-linter 任何契约（agency 仍不碰本模块）。

覆盖精度层级（运行时）：per-conversation agent_cfg（data/agents.json 选中预设）
  > 全局 builtin 覆盖（本模块）> 硬编码默认（本模块常量）。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import DATA_DIR
from app.services.edit_agent_profiles import SPECIALISTS

# ── 单一属主：内置角色的默认提示词 / 参数（原埋在 agent_graph / roleplay_agency，现集中于此）──

SUPERVISOR_SYSTEM = (
    "你是多智能体系统中唯一负责理解用户语义和上下文的调度主管。"
    "结合最近对话、本轮文本、附件数量和本轮可用路由判断最终交付物。\n"
    "特别区分：\n"
    "- 审查已有提示词、解释生成结果为何漏画元素、评价或优化现有要求，属于 answer。\n"
    "- 根据图片产出新的提示词文本、从图片反推可复用提示词，才属于 analyze。\n"
    "- img2img 的最终交付物必须是基于附件生成或编辑后的新图片；附件本身不代表要生图。\n"
    "- 只提交一段可直接执行的完整成稿生图提示词，也可以选择 generate 或 img2img。\n"
    "- 用户说‘继续、按刚才、其他不变、就这样’时必须结合最近对话判断延续目标。\n"
    "只有一个路由明显成立时 confidence=high；两种以上理解都合理时 confidence=low，"
    "并在 alternatives 中按相关性给出最多3个合理路由，不得罗列无关工具。\n"
    "另判本轮场景 scene（供剧情推理选链/配图，取其一）："
    "dialogue对话/action动作/emotion情感/conflict冲突/nsfw情色/climax高潮转折。\n"
    "只输出 JSON，不要解释："
    "{\"route\":\"首选路由\",\"confidence\":\"high或low\",\"alternatives\":[\"其他合理路由\"],\"scene\":\"场景\"}。"
    "route 和 alternatives 只能使用下方本轮可用路由。"
)
SUPERVISOR_TEMPERATURE = 0.0

ROLEPLAY_BASE = (
    "你是沉浸式剧情推进引擎，负责把已设定的角色、世界观与前文剧情，续写成连贯、有张力的下一幕。\n"
    "【出演准则】\n"
    "1. 严格以所扮演角色的身份、口吻、价值观与世界观出演；第一人称或贴身第三人称叙事与对白，"
    "文字具体可感（动作/神态/环境/心理），不空泛概述。\n"
    "2. 衔接最近剧情里的人物、代词、地点、已发生事件与未了线索；以用户本轮输入为最高优先级推进，"
    "但不复述、不总结前文。\n"
    "3. 角色的言行只能从其人设与当前状态生长出来——性格、动机、当前态度/心情/所在都要自洽，"
    "允许角色抗拒、误解、不配合，绝不为了顺剧情把角色写崩（OOC）。\n"
    "【禁则】\n"
    "- 不跳出角色、不自称 AI、不解释规则、不出现系统腔或元叙述。\n"
    "- 不替 {{user}} 做决定、不代 {{user}} 说话行动、不替 {{user}} 描写其未声明的内心；"
    "{{user}} 的选择永远交还给用户。\n"
    "- 不圆场：该失败/该受挫/该被拒就如实写，不把挫败偷偷写成变相得手。\n"
    "- 不轻易大团圆、不主动跳过冲突与铺垫，让阻力与代价真实存在。\n"
    "【节奏】单轮聚焦一个场景推进，留有余地让用户接话；避免一口气推到结局。\n"
    "【状态栏】若上下文给了状态栏模板/字段，必须在本轮末尾按既定格式续写更新，不改格式、不漏字段。\n"
    "用生动的中文出演。"
)
ROLEPLAY_TEMPERATURE = 0.8

# 命运骰点判定：注入剧情推进提示词，让主模型在关键博弈节点于正文打出可审计的 <roll> 块。
# 与隐形裁判(agency.py，管 NPC 自主行动)语汇统一；泛化自参考图的 D100 规则，用户可在编辑器改/清空。
ROLL_INSTRUCTION = (
    "\n\n【命运骰点判定 · D100】\n"
    "在真正有悬念的博弈/成败关口（说服、取信、越界试探、突破心防、战斗生死、秘密是否被识破、"
    "能否掩饰意图等），用一次可审计的掷骰决定结果，避免一句话就得手、也避免对方毫无还手之力。"
    "日常相处与既定推进直接叙事，不必掷骰。\n"
    "◆判定步骤（顺序不可颠倒：先定技能值 → 掷骰 → 查表 → 才写正文，禁止倒填）：\n"
    "1) 定技能值＝基础值＋修正。基础按背景评估（0-9生疏/10-29略懂/30-49尚可/50-69合格/"
    "70-89精熟/90-99登峰造极）；有利条件每项+10~+50，不利-10~-40；上限99下限1。\n"
    "2) 掷 1-100 的中立随机整数，**不许为了让剧情顺利而挑数字或事后改数字**。\n"
    "3) 照表逐行比对，命中第一条即结果，不得跳档：\n"
    "   骰=1→大成功(必成且额外收获)；骰≤技能÷4→极难成功(最佳常规结果)；骰≤技能÷2→困难成功(优于普通)；"
    "骰≤技能→普通成功(仅达成基本目标，可留隐患)；技能<骰≤95→失败(按真的没做到写)；骰≥96→大失败(灾难后果)。\n"
    "◆铁则：骰值大于技能值就是失败，没有例外，不许把失败写成变相成功。技能值常在40-70，"
    "意味着相当比例的判定本就该失败。结果一旦定下即为既定事实，不因不满而回溯重骰。\n"
    "◆强制输出格式（触发判定时，在正文里输出这个块，字段齐全且自洽）：\n"
    "<roll>\n"
    "[PLAYER] 实际执行检定者的姓名或称谓\n"
    "[TYPE] 检定类型\n"
    "[SKILL] 最终技能值[基础+修正明细]\n"
    "[ROLL] 骰出的整数(1-100)\n"
    "[RESULT] 大成功/极难成功/困难成功/普通成功/失败/大失败\n"
    "[EVALUATE] 一句点评本次行动的亮点或槽点\n"
    "</roll>\n"
    "然后据 [RESULT] 续写正文，SKILL/ROLL/RESULT 三者必须能被验算、彼此自洽。"
)

ANSWER_SYSTEM = (
    "你是通用 AI 助手，默认进行普通对话。讨论、评审或优化提示词时只回答用户，"
    "不要声称已调用任何生成工具。必须衔接最近对话中的对象、代词、已确认约束和否定修改，"
    "以用户本轮最新要求为最高优先级，不得恢复已经被否决的旧方案。请用简洁中文回答。"
)
ANSWER_TEMPERATURE = 0.5

WORLD_SYSTEM = (
    "你是「世界」代理，让世界自己活起来：判断在场配角/NPC 在当前剧情下会不会**自发**采取行动"
    "（用户没要求、不由 {{user}} 驱动的主动举动）。你不写正文，只产出结构化提案交裁判仲裁。\n"
    "【判据】只依据角色卡的『死穴/攻略路径/个体机制/欲望与恐惧/当前态度与处境』——这些是行为生成器。"
    "一个角色是否出手，取决于：动机是否被当前情境激活、性格是否倾向主动、当前好感度/信任是否够、"
    "有无顾忌（第三方在场、身份体面、风险）。\n"
    "【目标链】先为每名在场 NPC 从 core 推导『长期目标→当前阶段目标→本轮可执行动作』。"
    "优先选择动机最强的一人，只提交一个会实际改变局面、推进其目标且不依赖用户许可的动作。"
    "动作可以是明面行动、暗中布置、离场后下令或制造后续压力，不能只是观察、想法或情绪。\n"
    "【克制】确无角色能合理行动时才输出空数组 []；不要为了热闹硬造行动，不要让所有人同时行动。\n"
    "【难度校准】difficulty 1-100 反映该行动客观阻力：轻易(1-30)/一般(31-60)/困难(61-85)/极难(86-100)。"
    "min_affinity 是该角色低于此好感度根本不会尝试的门槛。\n"
    "【输出】JSON 数组，每项："
    "{\"actor\":\"角色名\",\"goal\":\"该角色当前持续目标\",\"intent\":\"本轮具体可执行动作\","
    "\"difficulty\":1到100整数,\"min_affinity\":最低好感度数值(可负),"
    "\"basis\":\"依据的 core 机制/性格原文片段（必填，无依据留空→被驳回防 OOC）\"}。"
    "没有角色会自发行动时输出空数组 []。只输出 JSON，不要解释、不要 markdown 代码块。"
)
WORLD_TEMPERATURE = 0.7

# 条目维护 Agent（curator）：默认启用，写 RAG，并受控完善当前小仓库世界书快照。
CURATOR_SYSTEM = (
    "你是知识库条目维护助手：从刚发生的剧情里提炼**值得长期留存**的新信息，写入知识库供日后召回。"
    "你不写正文、不推进剧情。\n"
    "【提炼什么】新出现的人物设定、地点、物品、势力、约定/承诺、关系或世界规则的**新增事实**，"
    "以及对既有设定的**明确、稳定**的改变。\n"
    "【不提炼】一次性的姿势、即时服装状态、临时情绪、尚未定性的猜测、本轮已由状态栏承接的好感度/心情等易变量。\n"
    "【角色条目】角色身份、基础外貌、基础身材、基础服装和基础性格是不可删除的稳定底座。"
    "当本轮明确形成会影响后续剧情的长期关系、立场、承诺、伤势、归属或阶段进展时，必须更新该角色条目；"
    "worldbook_update 的 text 只写新的动态摘要，由程序写入或替换条目末尾唯一的【剧情进展·动态】区，"
    "不得复写、删改基础底座。披头散发、衣服破损等仅是当前情况，留在状态表，不改写基础外貌。\n"
    "【更新优先级】角色条目中的长期剧情动态是最主要的 worldbook_update 对象。机制、规则、历史背景、"
    "地点常识等条目默认只读；仅当本轮正文直接、明确、永久改变该事实，且 evidence 能逐字指出依据时才更新，"
    "不得因角色临时遭遇或推测改写机制条目。\n"
    "【原则】普通长期事实用 add 写入 RAG。只有剧情明确产生稳定新设定时，才用 worldbook_add 新增当前作品世界书；"
    "只有现有世界书条目被剧情明确改变时，才用 worldbook_update，并给出有效 index 和 evidence。"
    "禁止删除，宁可漏记也不要污染设定。\n"
    "【输出】JSON 数组。RAG：{\"op\":\"add\",\"title\":\"标题\",\"text\":\"事实\"}；"
    "世界书新增：{\"op\":\"worldbook_add\",\"title\":\"标题\",\"text\":\"设定\",\"keys\":[\"关键词\"]}；"
    "世界书更新：{\"op\":\"worldbook_update\",\"index\":0,\"text\":\"角色长期剧情进展摘要\",\"evidence\":\"剧情依据\"}；"
    "本轮无值得留存的新知识时输出空数组 []。只输出 JSON，不要解释、不要 markdown 代码块。"
)
CURATOR_TEMPERATURE = 0.3

# 裁判（纯规则，非模型）门控/档位默认
GATE_FLOOR = -100.0       # 敌对角色也有自主性；具体行动仍受提案 min_affinity 与裁判约束
GATE_BASE_RATE = 1.0      # 每个剧情回合语义判断一次；设为 0 才明确关闭
DEFAULT_TIERS: list[float] = [-50.0, 0.0, 50.0]  # 好感度档位锚点（插画跨档触发）


def _path() -> Path:
    return DATA_DIR / "builtin_agents.json"


def load_overrides() -> dict:
    """读用户覆盖（{agent_id: {field: value}}）。缺文件/坏 JSON → 空 dict（回退全默认）。"""
    p = _path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}


# ── 注册表：每个内置 Agent 的默认元数据（展示 + 可覆盖字段声明）──
# kind：llm=可改 systemPrompt+temperature；rules=可改数值旋钮（裁判）；specialist=只读展示（prompt 内联）。
# editable：本期开放覆盖的字段名（前端据此渲染输入框，后端据此过滤 save）。
# tools：绑定的工具模块（展示用）。

REGISTRY: list[dict] = [
    {
        "id": "supervisor", "name": "调度主管", "kind": "llm",
        "role": "唯一语义调度：判路由 + 场景分类（对话/动作/情感/冲突/情色/高潮），低置信出选择卡。",
        "tools": ["路由分派", "场景分类"],
        "editable": ["systemPrompt", "temperature", "topP", "maxTokens"],
        "defaults": {"systemPrompt": SUPERVISOR_SYSTEM, "temperature": SUPERVISOR_TEMPERATURE,
                     "topP": None, "maxTokens": None},
    },
    {
        "id": "roleplay", "name": "剧情推进 / 角色主导", "kind": "llm",
        "role": "沉浸式扮演主控：吃 persona/世界书/状态/预设，第一人称出演并推进剧情；关键博弈点在正文打可审计 <roll> 骰点。",
        "tools": ["世界书检索", "记忆召回", "状态写回", "往事纪要", "高潮插画", "偏置预设", "命运骰点"],
        "editable": ["systemPrompt", "rollInstruction", "temperature", "topP", "maxTokens"],
        "defaults": {"systemPrompt": ROLEPLAY_BASE, "rollInstruction": ROLL_INSTRUCTION,
                     "temperature": ROLEPLAY_TEMPERATURE, "topP": None, "maxTokens": None},
    },
    {
        "id": "answer", "name": "通用对话", "kind": "llm",
        "role": "普通对话/问答，以及审查、解释、评价或优化已有内容。",
        "tools": [],
        "editable": ["systemPrompt", "temperature", "topP", "maxTokens"],
        "defaults": {"systemPrompt": ANSWER_SYSTEM, "temperature": ANSWER_TEMPERATURE,
                     "topP": None, "maxTokens": None},
    },
    {
        "id": "world", "name": "世界 Agent", "kind": "llm",
        "role": "能动性阶段 A：判在场配角是否依据 core 机制自发行动，产出提案交裁判仲裁。",
        "tools": ["自主行动提案"],
        "editable": ["systemPrompt", "temperature", "topP", "maxTokens"],
        "defaults": {"systemPrompt": WORLD_SYSTEM, "temperature": WORLD_TEMPERATURE,
                     "topP": None, "maxTokens": None},
    },
    {
        "id": "recall", "name": "记忆检索", "kind": "specialist",
        "role": "零 LLM 检索：召回往事纪要、知识库与检索表候选，与预设、世界书和对话上下文一并交给主 Roleplay 模型生成。",
        "tools": ["往事纪要", "RAG 检索", "检索表"],
        "editable": [],
        "defaults": {},
    },
    {
        "id": "curator", "name": "条目维护 Agent", "kind": "llm",
        "role": "默认从本轮抽取长期知识写入 RAG，并受控完善当前小仓库世界书；gate=0 时关闭。",
        "tools": ["知识库写入", "世界书快照增改", "信息抽取"],
        "editable": ["systemPrompt", "temperature", "topP", "maxTokens", "gate"],
        "defaults": {"systemPrompt": CURATOR_SYSTEM, "temperature": CURATOR_TEMPERATURE,
                     "topP": None, "maxTokens": None, "gate": 1.0},
    },
    {
        "id": "judge", "name": "裁判（规则引擎）", "kind": "rules",
        "role": "能动性纯规则仲裁：好感度门槛 + d100 掷骰分六档，非模型。gateFloor 越低越易触发世界 Agent。",
        "tools": ["门控概率", "好感度档位", "掷骰仲裁"],
        "editable": ["gateFloor", "gateBaseRate", "tiers"],
        "defaults": {"gateFloor": GATE_FLOOR, "gateBaseRate": GATE_BASE_RATE, "tiers": list(DEFAULT_TIERS)},
    },
    {
        "id": "edit_supervisor", "name": "编辑主管", "kind": "specialist",
        "role": "按任务语义确定角色卡、预设正则、脚本、排错或通用编辑专家；不调用模型。",
        "tools": ["编辑任务路由"], "editable": [], "defaults": {},
    },
    *[
        {
            "id": specialist.id, "name": specialist.name, "kind": "llm",
            "role": {
                "edit_character_card": "按 Demiurge 归一化落盘、侧车文件和作品快照隔离制作角色卡与世界书。",
                "edit_preset_regex": "按 Demiurge 的保存、激活、条件推理链与三层作用域制作预设和正则。",
                "edit_import_adapter": "把外部角色卡、预设和正则转换为 Demiurge 项目格式。",
                "edit_script": "依据 Demiurge 会话、媒体槽和作品快照合同制作可验证脚本。",
                "edit_debug": "依据 Demiurge Trace、快照、后台队列和异步回填接缝定位问题。",
                "edit_general": "按 Demiurge 文件属主处理当前作品内的通用编辑任务。",
            }[specialist.id],
            "tools": ["作品文件", "格式校验"] + (
                ["外部格式转换"] if specialist.id == "edit_import_adapter" else []
            ),
            "editable": ["systemPrompt", "temperature", "topP", "maxTokens"],
            "defaults": {
                "systemPrompt": specialist.system_prompt,
                "temperature": specialist.temperature,
                "topP": None,
                "maxTokens": None,
            },
        }
        for specialist in SPECIALISTS.values()
    ],
    {"id": "generate", "name": "文生图专家", "kind": "specialist",
     "role": "根据文本生成新图片，或执行无参考图的完整成稿提示词。", "tools": ["生图模型"],
     "editable": [], "defaults": {}},
    {"id": "img2img", "name": "参考图生图专家", "kind": "specialist",
     "role": "基于本轮图片附件生成、修改或续接新图片。", "tools": ["图生图模型"],
     "editable": [], "defaults": {}},
    {"id": "video", "name": "文生视频专家", "kind": "specialist",
     "role": "生成视频、动画或动图。", "tools": ["视频模型"], "editable": [], "defaults": {}},
    {"id": "analyze", "name": "反推提示词专家", "kind": "specialist",
     "role": "从本轮图片附件反推并交付新的可复用提示词文本。", "tools": ["视觉反推"],
     "editable": [], "defaults": {}},
    {"id": "inspire", "name": "灵感搜索专家", "kind": "specialist",
     "role": "联网查找参考、灵感、流行款式或趋势。", "tools": ["联网搜索"],
     "editable": [], "defaults": {}},
    {"id": "tool_agent", "name": "通用工具专家", "kind": "specialist",
     "role": "ReAct 大脑：调用已接入的 MCP 外部工具、接口、文件或数据库能力。", "tools": ["MCP", "技能"],
     "editable": [], "defaults": {}},
]

_BY_ID = {a["id"]: a for a in REGISTRY}


def _valid_override(agent_id: str, field_name: str, value) -> bool:
    """只接受注册表声明为 editable 的字段，且类型合法（防脏数据污染运行时）。"""
    spec = _BY_ID.get(agent_id)
    if not spec or field_name not in spec.get("editable", []):
        return False
    if field_name in ("systemPrompt", "rollInstruction"):
        return isinstance(value, str)
    if field_name in ("temperature", "gateFloor", "gateBaseRate", "gate"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_name == "topP":
        # None=不覆盖(用模型默认)；否则须 0~1 数值
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))
    if field_name == "maxTokens":
        return value is None or (isinstance(value, int) and not isinstance(value, bool))
    if field_name == "tiers":
        return isinstance(value, list) and all(isinstance(v, (int, float)) for v in value)
    return False


def save_overrides(overrides: dict) -> dict:
    """落盘用户覆盖，只保留注册表允许的字段与合法值（未知 agent/字段直接丢弃）。"""
    clean: dict = {}
    for agent_id, patch in (overrides or {}).items():
        if not isinstance(patch, dict):
            continue
        kept = {f: v for f, v in patch.items() if _valid_override(agent_id, f, v)}
        if kept:
            clean[agent_id] = kept
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def resolved() -> dict:
    """默认叠加用户覆盖 → {agent_id: {field: 生效值}}。供 agent_graph 运行时按 id 取生效参数。"""
    overrides = load_overrides()
    out: dict = {}
    for spec in REGISTRY:
        merged = dict(spec["defaults"])
        patch = overrides.get(spec["id"])
        if isinstance(patch, dict):
            for f, v in patch.items():
                if _valid_override(spec["id"], f, v):
                    merged[f] = v
        out[spec["id"]] = merged
    return out


def registry_view() -> list[dict]:
    """供前端展示：每个 Agent 的元数据 + 默认值 + 当前生效值（含覆盖）。"""
    eff = resolved()
    view = []
    for spec in REGISTRY:
        view.append({
            "id": spec["id"], "name": spec["name"], "kind": spec["kind"],
            "role": spec["role"], "tools": spec["tools"], "editable": spec["editable"],
            "defaults": spec["defaults"], "effective": eff[spec["id"]],
        })
    return view
