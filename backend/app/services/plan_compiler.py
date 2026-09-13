"""Autopilot P1 计划编译：委派意图识别（零 LLM）→ 意图编译成计划文档 → 校验 → 落盘。

- 委派强命令层只收高置信确定性模式（规模词+资产动作 / 显式计划语言），模糊表达
  交给剧情默认或 supervisor（误判方向见 docs/ROADMAP-AUTOPILOT.md「路由界限」）。
- 编译用 structured_output 统一接缝；校验失败带错误重试一次，仍败如实返回错误，
  不编造计划。执行器（P2）与审批（P3）另立，本模块只产出计划文档。
- 落盘 <作品>/plans/<ts>-<slug>.plan.json（执行真源）+ 姊妹稿 .plan.md（单向渲染）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services import capability_registry, plan_validator, structured_output
from app.services.structured_contracts import GenerationPlan

# 显式计划语言：命中即委派
_EXPLICIT = (
    "做个计划", "做一个计划", "制定计划", "制定一个计划", "编排计划",
    "做个执行计划", "自动完成", "帮我安排", "批处理",
)
# 规模词：批量/全部/... 或 ≥2 的数量词（一张/一个不算——那可能只是单次生成）
_SCALE_RE = re.compile(r"批量|分批|逐批|全部|所有|每个|每张|每条|每款|各套|各个|各张|各条|各款|一批|[2-9\d二三四五六七八九十百千]+\s*(张|个|条|款)")
# 资产/生成动作：与规模词共现才算委派（剧情内单次创作不抢）
_ACTION_RE = re.compile(
    r"出\s*[0-9一二两三四五六七八九十百千]*\s*张|出图|生图|生成图片|生成视频|提交|导入|整理|建仓|"
    r"创建|更新|下载|编排|标注|重命名|删除")
# 疑问信号：真疑问句（为什么/怎么/？）按剧情/对话处理，不做委派分派。
# 2026-09-07 治本：检查/审查/分析/失败/问题是「操作动词」不是疑问信号——
# 「先做密度检查找出不达标条目，再补写」是明确的制作任务，不该被「检查」一票否决。
_QUESTION_RE = re.compile(r"为什么|怎么|如何|？|\?")


def is_delegation_intent(text: str) -> bool:
    """零 LLM 委派强命令判定：高置信才 True（误判方向：模糊不改剧情默认）。"""
    source = (text or "").strip()
    if not source or _QUESTION_RE.search(source):
        return False
    # 「生图提示词」是提示词的定语不是生成动作（2026-09-11 实锤：讨论型请求被
    # 「生图」二字拽进机械计划，撞死在 submit/collect 校验上）。单一属主在
    # plan_validator.strip_prompt_compounds，此处复用同一剥法。
    source = plan_validator.strip_prompt_compounds(source)
    if any(mark in source for mark in _EXPLICIT):
        return True
    return bool(_SCALE_RE.search(source) and _ACTION_RE.search(source))


# 文档交付动词与「文档」的近邻共现（生成文档/整理成文档/汇总成综合文档…）
# 2026-09-09 固化04 场景补强：动词表加合并/整合/集成/汇编/并成，宾语加
# 设定总集/总集/md/markdown/上下文——「把这几份设定合并为一个文档」「整理一份
# 作品设定总集」等自然说法此前落空会掉进 roleplay（污染剧情记忆，实锤：04 用户
# 话术 6 例仅 2 例命中）。仍守两条防线：目标词只收「文档型工件名」（不收裸「设定」
# 与剧情器物如典籍/案卷），疑问句/`？` 仍由 _QUESTION_RE 上游一票否决。
_DOC_DELIVERY_RE = re.compile(
    r"(生成|整理|汇总|写成|输出|导出|合并|整合|集成|汇编|编成|并成)"
    r"[^。！!？?\n]{0,12}(文档|设定文档|设定总集|总集|markdown|md|上下文)"
    r"|[^。！!？?\n]{0,8}(文档|设定总集|总集|资料|上下文)"
    r"[^。！!？?\n]{0,10}(汇总|归档|合并|整合)"
    r"|(整理|合并|整合|汇总)(成|为)?(一份|一个)?[^。！!？?\n]{0,10}"
    r"(设定总集|总集|设定文档|上下文文档|md|markdown)")
# 合集卡/角色卡/世界书整卡交付（固化02/03 场景，2026-09-05）：明确要产出「卡/书」即委派
# 智能编造——与文档交付同语义，卡/书是结构化交付物，由固化知识库规范驱动执行，
# 禁止落入普通对话或编辑 Agent 的受限文件工具。覆盖「根据这本小说制作合集卡…」、
# 「创建角色卡」「把这张卡的内嵌世界书独立出来」等。
# 2026-09-07 治本：动词表补优化类动词（补写/完善/丰富/优化/修改/扩充/细化…）
# ——「内容不够丰富，基于已有卡纲补写」是卡交付任务的继续形态，同样必须进 fabric
# 自由循环，不能掉进无 LLM 的机械计划执行器（plan_tasks partial 卡死实锤）。
# 判定放宽为「同一句内同时出现卡交付动词 + 卡/书名词」：近邻窗口对语序敏感
# （「补写丰富合集卡内容」与「合集卡内容不够丰富，补写」语序不同），同句共现
# 已足够高置信（疑问句在上游已被 _QUESTION_RE 排除）。
_CARD_DELIVERY_RE = re.compile(
    r"(制作|做成|生成|整理成|转成|转换|创建|导入|迁移|更新|补写|完善|丰富|优化|修改|"
    r"扩充|细化|重写|补充|增加|添加|扩写|增补|修订|打磨|精修|提升|加强|深化|补齐|补到|补足|补全)"
    r"[^。！!？?\n]{0,30}"
    r"(合集卡|角色卡|世界书|卡纲|卡内容|卡条目|内嵌世界书|NSFW条目|NSFW)"
    r"|[^。！!？?\n]{0,20}"
    r"(合集卡|角色卡|世界书|卡纲|卡内容|卡条目|NSFW条目|NSFW)"
    r"[^。！!？?\n]{0,30}"
    r"(更新|补写|完善|丰富|优化|修改|扩充|细化|重写|补充|增加|添加|扩写|增补|修订|打磨|精修|提升|加强|深化|补齐|补到|补足|补全)"
    r"|(把|将)[^。！!？?\n]{0,12}(内嵌世界书|世界书)[^。！!？?\n]{0,8}(独立|外拆|拆分|迁移)")


def is_doc_delegation_intent(text: str) -> bool:
    """文档交付强命令判定：明确要求产出文档即委派智能编造。

    与 is_delegation_intent 的区别：允许带图片附件（看图反推→生成套装文档这类
    任务带参考图，不能被「带图不走委派」的防劫持规则挡掉——图片在此是素材而非
    图生图/反推的目标）。疑问句仍不委派。
    """
    source = (text or "").strip()
    if not source or _QUESTION_RE.search(source):
        return False
    return bool(_DOC_DELIVERY_RE.search(source) or _CARD_DELIVERY_RE.search(source))


# 设计讨论型（2026-09-11 实锤）：「参考文档跟我讨论各服装的生图提示词」既不是委派
# 强命令（无产出动词）也该远离剧情扮演（作品绑卡时普通对话会被吸进剧情楼层）——
# 它的正道是智能编造自由循环：LLM 读文档与用户讨论，后续要产出再编排。
# 刻意不做疑问否决：讨论天然以问句出现（「能否参考…」）。
# 接续语（2026-09-11 补）：讨论的下一轮常是「就按这套来，把场景1提示词细化」——
# 没有典型讨论动词，漏判会掉回剧情楼层。扩写/细化与设计话题共现才命中；
# 非设计域的「扩写大纲」不误伤（topic 词表不含 大纲/章节）。
_DISCUSS_RE = re.compile(
    r"讨论|商讨|聊聊|探讨|商量|对个方案|给.{0,6}建议|细化|扩写|按这套|就按这个")
_DESIGN_TOPIC_RE = re.compile(
    r"提示词|服装|穿搭|设计|设定|立绘|配色|画法|文案|文档|角色|世界观")


def is_design_discussion_intent(text: str) -> bool:
    """设计讨论型判定：讨论动词 + 设计话题共现 → 智能编造自由循环（非机械计划、非剧情）。"""
    source = (text or "").strip()
    if not source:
        return False
    return bool(_DISCUSS_RE.search(source) and _DESIGN_TOPIC_RE.search(source))


def mentions_design_topic(text: str) -> bool:
    """是否带设计话题词（任务会话接续判定用；词表收口见 _DESIGN_TOPIC_RE 注）。"""
    return bool(_DESIGN_TOPIC_RE.search((text or "").strip()))


def is_design_discussion_continuation(current: str,
                                      recent_user_texts: list) -> bool:
    """讨论粘性（2026-09-11 实锤）：讨论接续轮不再靠枚举接续词。

    「不用ComfyUI的提示词,我需要的是适配gpt,banana这种ai模型的文字描述内容」
    没有任何讨论动词/接续词 → 三个判定全不命中 → supervisor 掉回 roleplay，
    灵感卡/规范/附件注入整段失效（隔一轮就失忆）。修法：最近几条用户消息里有
    设计讨论、且本轮带设计话题词、且本轮不是其他明确意图 → 视为讨论接续。
    上下文合同 P1 起此判定降为**兜底**：优先查 task_session_store 登记。

    委派/文档交付在调用方（路由条件）里排在前面，天然优先，无需在此重复；
    纯剧情叙述不带设计话题词，不抢道。
    """
    source = (current or "").strip()
    if not source:
        return False
    if is_delegation_intent(source) or is_doc_delegation_intent(source):
        return False
    if not mentions_design_topic(source):
        return False
    return any(is_design_discussion_intent(str(t or "")) for t in (recent_user_texts or [])[-3:])


# 自建工具通道判定（2026-09-10）：交付类任务**默认**把 shell/写脚本挡在能力面外
# （2026-09-06 实锤跑偏：模型用 run_shell 读小说、load 固化01 编排生图）。但用户
# 明确要「让它在对话里自己写脚本/自己跑、自己排错」时，需按需解锁 file.write_text +
# project.run_shell。判定只看显式工具类措辞，避免日常交付任务误开。疑问句不判
# （「为什么要跑命令」是提问不是委托）。durable 语义不变：approval 模式逐次审批。
_TOOLING_RE = re.compile(
    r"写(个|一个|一份)?\s*(脚本|程序|py|python|命令|批处理)|脚本文件|命令行|run_shell|shell|"
    r"跑(个|一下)?\s*(脚本|命令|程序)|执行命令|排错|调试|debug|自动化|"
    r"自己(写|生成|做)(个|一个)?\s*(工具|脚本|程序)|写(个|一个)?\s*(工具|小工具)")


def wants_tooling_intent(text: str) -> bool:
    """交付任务里是否显式要求「写脚本/跑命令/排错」——命中才解锁自建工具能力面。"""
    source = (text or "").strip()
    if not source or _QUESTION_RE.search(source):
        return False
    return bool(_TOOLING_RE.search(source))


# 联网检索通道判定（2026-09-10，链路①+②）：交付任务**默认**只在本地资料上作业
# （世界书快照/角色卡/近期纪要），能力面不给联网——多数制卡/整理任务不需要外网，
# 常开会让模型闲着去搜一圈、白烧 token 还容易被搜索结果带偏。
# 用户明确要「联网/上网/搜索/找参考图」时才把 web.search_materials +
# web.save_material 叠加进能力面。
# 与 _TOOLING_RE 的两点差异（有意为之）：
#   ① 不做疑问句一票否决——「能不能搜一下这个角色的外貌」是委托不是提问，且检索只读无风险；
#   ② 不收裸「素材」——「把 _prep 素材整理成设定总集」是本地中间产物，误命中会白开联网面。
_WEB_MATERIAL_RE = re.compile(
    r"联网|上网|外网|网上|在线搜|"
    r"搜(索|一下|个|搜|图|同款)|找图|配图|参考图|图片素材|素材图|找参考|"
    r"查一下|查一查|查资料|搜资料|找资料|查找资料|"
    r"真实照片|原型图|官方图|剧照|海报图")


def wants_web_material_intent(text: str) -> bool:
    """交付任务里是否显式要求联网检索/找参考图——命中才解锁联网检索与受控下载能力面。"""
    source = (text or "").strip()
    if not source:
        return False
    return bool(_WEB_MATERIAL_RE.search(source))


@dataclass
class CompileOutcome:
    plan: GenerationPlan | None = None
    errors: list[str] = field(default_factory=list)
    raw: str = ""
    strategy: str = ""


def read_user_file(path: str, *, max_chars: int = 60000) -> dict:
    """编译期预读用户消息里明示的本地文本文件（容量封顶；仅此用途，非执行面能力）。"""
    import re as _re
    from pathlib import Path
    path = _re.sub(r"[，。；、！？」』）\s]+$", "", path)  # 消息截取常带尾标点
    target = Path(path).expanduser()
    if not target.is_file():
        raise ValueError(f"文件不存在：{path}")
    text = target.read_bytes().decode("utf-8", errors="replace")[:max_chars]
    return {"path": str(target), "text": text}


def _manifest_lines(capabilities: list[dict]) -> str:
    lines = []
    for item in capabilities:
        avail = "" if item.get("available", True) else "（当前不可用：模型未配置）"
        level = item.get("side_effect_level")
        schema = item.get("params_schema") or {}
        required = set(schema.get("required") or [])
        props = schema.get("properties", {})
        params = "、".join(
            f"{name}*" if name in required else f"{name}(可选)"
            for name in props)
        lines.append(
            f"- {item['operation']}[{level}]{avail}：{item['description']} 参数：{params or '无'}"
            f"（带 * 为必填，标注「可选」的参数可省略）")
    return "\n".join(lines)


def _attachment_brief(text: str, limit: int = 80_000) -> str:
    """编译期给模型**全文**参考文档（P4：模型判断，代码保真）。

    唐柚类文档（43KB）全文进上下文，模型才有语义判断「哪些是套装/哪些是规则」的依据；
    只有极端超长文档才退化为骨架（标题清单），此时模型可写 prompt_section 引用由
    fill_prompt_sections 机械回填兜底。
    """
    if len(text or "") <= limit:
        return text or ""
    lines = (text or "").splitlines()
    headings = [ln for ln in lines if "【" in ln or ln.strip().startswith("#")]
    if headings:
        return "\n".join(headings[:120])
    return text[:limit]


_COMPILE_SYSTEM = (
    "你是 Demiurge 的计划编译器。把用户意图编译成可机械执行的计划文档 JSON。\n"
    "只能使用下方能力清单里的 operation，不得编造能力；每个计划必须带预算 budgets；"
    "步骤数尽量少，超预算请拆多计划。durable/expensive 能力会要求用户审批，不要回避。\n"
    "意图含糊或缺少关键信息时，steps 留空并在 intent 里写清缺什么，禁止猜测硬编。\n"
    "如果用户意图包含出图/生图/提交，steps 必须是包含 workflow.submit_* 与 "
    "media.collect_comfy_outputs 的完整执行计划；缺少关键信息就 steps 留空，禁止只排只读探查步骤。\n"
    "运行环境 ComfyUI 地址：{comfyui_url}（submit/collect 的 url/comfyui_url 参数必须逐字使用这个值）。\n"
    "所有 params 必须写具体值，禁止出现 {{...}}、TO_BE_RESOLVED、PLACEHOLDER 等占位符；"
    "引用前序步骤产物只能写在 inputs_from 里。\n"
    "params 类型必须严格遵循下方能力清单里各参数的 JSON Schema：integer 写数字（如 top_names: 60）、"
    "boolean 写 true/false、array 写数组（如 entries 的 keys 是字符串数组）、object 写对象，"
    "禁止用字符串表达数字/布尔/数组。\n"
    "用户上传的文件（【附件文件】带 path 字段的）在 steps 里引用时，path 参数必须逐字使用"
    "附件给出的真实绝对路径，禁止编造或改写路径；未给出真实路径的文件只能先 file.list_dir/read_text"
    "定位，不得臆造路径。\n"
    "【输出 JSON 合同（字段名必须逐字一致）】\n"
    "{\n"
    '  "intent": "一句话意图",\n'
    '  "repo_id": "作品ID或空串",\n'
    '  "budgets": {"max_steps": 24, "max_gpu_tasks": 32, "max_llm_calls": 8},\n'
    '  "steps": [{"id": "s1", "operation": "清单里的动词.宾语",\n'
    '             "params": {"参数名": "值"}, "inputs_from": [], "outputs": []}],\n'
    '  "approval_required": ["需要审批的 operation"]\n'
    "}\n"
    "steps[].id 是步骤标识（s1/s2…），inputs_from 引用此前步骤的 outputs 键。\n"
    "workflow.list_templates 的产出键是 templates；workflow.read_exposed_fields 的产出键是 template。\n"
    "media.collect_comfy_outputs 的 submit_result/prompt_ids/prompts 由前序 submit 步骤"
    "运行时填充：编译时省略这些键，inputs_from 写「submit步骤id.submit_result」"
    "（每个 submit 步骤一条；submit_result 是虚拟键，执行器会取该步骤整个产出），names 写各产物名。\n"
    "所有落盘/输出目录参数一律用运行环境的 output_dir，禁止使用用户文档所在目录。\n"
    "写文件/建卡/建文档等 durable 步骤后可加一条 file.list_dir 或 file.read_text 只读自检，"
    "确认落盘成功（只读自检不烧 GPU 配额）。\n"
    "用户指定了 LoRA 时，在 submit 参数里写 lora_name（用本机目录里的真实文件名）；"
    "未指定则不写（模板自带 LoRA 配置生效）。\n"
    "附件文档里若有分套装/分段提示词，可以直写完整 prompt 到变体；文档很长时"
    "建议每个变体只写 {\"name\": \"套装名\", \"prompt_section\": \"标题关键词\"}（或只写 name），"
    "编译期会机械回填完整提示词。直写时必须逐字忠于文档段落，禁止混入使用说明、"
    "鞋履/袜子等规则段、本机 LoRA/模板目录内容。变体数量必须等于文档真实套装数。\n"
    "【能力清单（manifest）】\n{manifest}"
)

_COMPILE_USER = "{history}【用户意图】\n{intent}\n\n请输出计划 JSON。"


def compile_plan(*, intent: str, history: str = "", attachments: list[dict] | None = None,
                 repo_id: str = "", comfyui_url: str = "",
                 output_dir: str = "", configured_models: set[str] | frozenset[str] = frozenset(),
                 chat_base: str = "", chat_key: str = "", chat_model: str = "",
                 chat_fn: Callable | None = None, structured_chat_fn: Callable | None = None,
                 temperature: float = 0.2, proxy_kwargs: dict | None = None,
                 prev_batch: dict | None = None,
                 trace: Callable | None = None) -> CompileOutcome:
    """意图 → 校验通过的计划；两次编译都失败时返回带 errors 的 outcome。

    prev_batch：延续批量生图时由调用方注入的上一批变体真源
    （latest_submit_plan 产出）；附件里给模型看，回填用结构化真源。
    """
    capabilities = capability_registry.with_availability(configured_models)
    system = _COMPILE_SYSTEM.replace(
        "{manifest}", _manifest_lines(capabilities)).replace(
        "{comfyui_url}", comfyui_url or "")
    if attachments:
        blocks = "\n\n".join(
            f"【附件文件：{item.get('name', 'file')}】"
            + (f"（真实路径：{item['path']}）" if item.get("path") else "")
            + f"\n{_attachment_brief(item.get('text', ''))}"
            for item in attachments)
        system += "\n\n【用户消息引用的文件内容（编译时已读取，可据此填写精确参数）】\n" + blocks
    if attachments and any(item.get("name") not in CATALOG_ATTACHMENT_NAMES
                           for item in attachments):
        # 输出截断防呆（2026-09-05 实锤）：模型直写 14 套完整 prompt ≈40KB 输出被
        # 网关截断，编译解析失败。文档已全文在上下文里，变体只写段落引用即可，
        # fill_prompt_sections 会机械回填完整提示词。
        system += (
            "\n\n【用户已提供提示词文档】变体一律只写段落引用，每个变体形如 "
            '{"name": "套装名", "prompt_section": "标题关键词"}'
            "（prompt_section 取文档里的【标题】关键词），禁止直写完整 prompt——"
            "直写会让输出超长被截断、整份计划编译失败；编译期会按标题机械回填完整提示词。"
            "变体数量必须等于文档真实套装数。")
    if prev_batch:
        # 延续上一批（方案 A，2026-09-05 实锤）：续轮没有原文档附件，模型凭记忆
        # 重写提示词=幻觉（上一轮实锤编出「套装01-日常校园」伪套装）。改为只写
        # name+改动字段，prompt 与未改动参数由 fill_prev_batch_prompts 机械回填。
        system += (
            "\n\n【延续上一批生图】用户要基于上一批改参数再生成。上一批变体清单在附件"
            f"「{PREV_BATCH_ATTACHMENT_NAME}」里：variants 每项只写 name（从清单逐字复制）"
            "加**要改的**字段（如 latent_width/latent_height/lora_weight），prompt 一律省略，"
            "编译期会按 name 机械回填上一批完整提示词，没写的参数自动沿用上一批；"
            "只改部分变体就只列那几个。"
            "模板与 LoRA 不变时沿用清单里的 template_id/lora_name。"
            "若本轮其实是全新任务（与上一批无关），忽略该清单按新任务编排。")
    user = _COMPILE_USER.format(history=history, intent=intent,
                                repo_id=repo_id or "（未指定）", output_dir=output_dir or "（未指定）")
    call_args = (chat_base, chat_key, chat_model, system, user)
    call_kwargs: dict[str, Any] = {"temperature": temperature, "max_tokens": 32000,
                                   **(proxy_kwargs or {})}
    outcome = CompileOutcome()
    validator_errors: list[str] = []

    for attempt in (1, 2):  # 编译失败带校验错误重试一次（structured_output 现成模式）
        retry_hint = ""
        if any("没有任何 submit/collect" in e for e in validator_errors):
            retry_hint = (
                "\n\n注意：意图要求出图/提交时，steps 必须包含 workflow.submit_* 和 "
                "media.collect_comfy_outputs 完整执行步骤；如果缺少关键信息，"
                "steps 留空并在 intent 里写清缺什么，不要只排只读探查步骤。")
        elif any(("未返回完整 JSON" in e or "结构化输出未通过" in e)
                 for e in validator_errors):
            # 解析失败也要给模型可行动的反馈（2026-09-05 实锤：空反馈导致重试
            # 盲目收敛成只读计划，撞死在意图/步骤不一致校验上）。
            retry_hint = (
                "\n\n注意：上一次输出不是完整可解析的计划 JSON（常见原因：输出超长被截断，"
                "或混入了说明文字/合同示例回显）。请整体只输出一个 JSON 对象，不要输出解释文字；"
                "有文档时变体一律只写 {\"name\": \"套装名\", \"prompt_section\": \"标题关键词\"}"
                " 段落引用，禁止直写完整提示词——编译期会按文档标题机械回填。")
        attempt_user = user if attempt == 1 else (
            user + "\n\n上一次编译未通过校验，请修正：\n" + "\n".join(validator_errors)
            + retry_hint)
        try:
            result = structured_output.invoke(
                GenerationPlan,
                native=(lambda u=attempt_user: structured_chat_fn(
                    *call_args[:4], u, schema=GenerationPlan, **call_kwargs))
                if callable(structured_chat_fn) else None,
                legacy=lambda u=attempt_user: chat_fn(*call_args[:4], u, **call_kwargs),
                trace=trace,
            )
        except Exception as exc:  # noqa: BLE001 - 解析/截断失败也重试一次
            # 解析错误必须回灌进重试消息：validator_errors 在下一轮开头拼重试文本，
            # 留空会让模型「不知道错哪」而盲目重编（2026-09-05 实锤）。
            validator_errors = [str(exc)]
            if attempt == 2:
                outcome.errors = [f"计划编译失败：{exc}"]
                return outcome
            if trace is not None:
                trace("plan.compiled", status="parse_error", attempt=attempt,
                      error=str(exc)[:200])
            continue
        plan = result.value
        outcome.raw = result.raw or plan.model_dump_json()
        outcome.strategy = result.strategy
        # 计划声明的 repo/output 缺失时按调用方上下文补齐（编译器不知道运行环境）
        plan = plan.model_copy(update={
            "repo_id": repo_id,  # 运行环境真源强制（模型编的 repo_id 可能非法当 Chroma 集合名）
        })
        # 模板 ID 归一：模型抄写长 hex ID 易丢位——查不到时按名称/ID 前缀解析为真实 ID
        try:
            from app.services import template_store
            templates = template_store.list_templates()
            for step in plan.steps:
                tid = str(step.params.get("template_id") or "")
                if not tid or template_store.get_template(tid) is not None:
                    continue
                match = next((t for t in templates
                              if t.get("id") == tid or t.get("id", "").startswith(tid)
                              or t.get("name") == tid), None)
                if match is None:
                    # 模型可能写引用表达式/带修饰名：参数串里含已知模板名即归一
                    match = next((t for t in templates
                                  if t.get("name") and t["name"] in tid), None)
                if match is not None:
                    step.params["template_id"] = match["id"]
        except Exception as exc:  # noqa: BLE001 - 模板库不可用时跳过归一，由执行期报错
            if trace is not None:
                trace("plan.compile", status="template_normalize_skipped", error=str(exc))
        # 编译期内回填 variants（P4 保真）：模型只要排出 submit_batch 步骤，空 variants
        # 或 prompt_section 引用都会在**校验前**被文档机械回填/重建；随后 budgets 归一
        # 才能拿到真实 GPU 数，避免「空 variants → max_gpu_tasks 归 0 → 重试被带偏」。
        try:
            filled_early = fill_prompt_sections(plan, attachments or [])
            if filled_early and trace is not None:
                trace("plan.sections_filled", count=filled_early, stage="pre_validate")
        except Exception:  # noqa: BLE001 - 回填失败仍走校验，由 validator 报具体错误
            pass
        if prev_batch:
            try:
                filled_prev = fill_prev_batch_prompts(plan, prev_batch)
                if filled_prev and trace is not None:
                    trace("plan.prev_batch_filled", count=filled_prev, stage="pre_validate")
            except Exception:  # noqa: BLE001 - 回填失败仍走校验，由 validator 报具体错误
                pass
        # 代码保真：同模板的多个 submit_batch 合并为一个（模型可能把「分批」误解为
        # 拆多个提交步骤 → 重复烧 GPU）。合并后 inputs_from 引用自动重写到保留步骤。
        _merge_duplicate_submits(plan)
        _merge_duplicate_collects(plan)
        # 运行环境参数归一：collect 的落盘目标由环境决定，不允许模型占位/编造；
        # approval_required 由编译器确定性汇总（模型只列步骤，不负责汇总）
        from app.services.capability_registry import get as _cap_get
        approval = sorted({step.operation for step in plan.steps
                           if (cap := _cap_get(step.operation)) is not None
                           and cap.side_effect_level in ("durable", "expensive")})
        # budgets 由代码按计划实际内容确定性归一（模型乱填的 18/32/4 不生效）：
        # 步数=实际步骤数；GPU=submit_batch 变体数 + submit_template 计数；
        # LLM=0（执行器无 LLM）。计划卡与执行配额因此永远紧凑真实。
        gpu_tasks = 0
        for step in plan.steps:
            if step.operation == "workflow.submit_batch":
                variants = step.params.get("variants")
                if isinstance(variants, list):
                    gpu_tasks += len(variants)
            elif step.operation == "workflow.submit_template":
                gpu_tasks += 1
        plan = plan.model_copy(update={
            "approval_required": approval,
            "budgets": plan.budgets.model_copy(update={
                "max_steps": max(1, len(plan.steps)),
                # 含 submit 步骤时至少给 1，避免「variants 为空 → GPU 预算 0」误导重试
                "max_gpu_tasks": max(1, gpu_tasks) if gpu_tasks > 0 or any(
                    step.operation.startswith("workflow.submit") for step in plan.steps
                ) else 0,
                "max_llm_calls": 0,
            }),
        })
        for step in plan.steps:
            # 落盘/执行类参数一律环境归一（单一属主 capability_registry.ENV_INJECTED_OPS）：
            # 编译期就写入真实值，计划卡与路径域校验看到的就是执行时的值（2026-09-09 治本）。
            # 覆盖 shell 的 cwd——模型常传 / 等越域值会被路径域校验拦死（实锤「cwd 的路径
            # / 越出作品域」）；cwd 不是写入目标，由运行环境决定既安全又免去模型猜路径。
            capability_registry.inject_env_params(
                step.params, step.operation,
                output_dir=output_dir, repo_id=plan.repo_id, force=True)
        validator_errors = plan_validator.validate(
            plan, capabilities=capabilities,
            configured_models=configured_models, allowed_prefix=output_dir,
        )
        if not validator_errors:
            outcome.plan = plan
            if trace is not None:
                trace("plan.compiled", status="ok", steps=len(plan.steps),
                      strategy=result.strategy)
            return outcome
        if trace is not None:
            trace("plan.compiled", status="invalid", attempt=attempt,
                  errors=validator_errors)
    if not outcome.errors:
        outcome.errors = validator_errors
    if plan.intent.strip() and any("steps 为空" in e for e in outcome.errors):
        # 模型判定意图含糊时，把它的「缺什么」说明如实带给用户（P1 clarify 语义）
        outcome.errors.append(f"模型认为意图缺少以下信息：{plan.intent.strip()}")
    return outcome


def save_plan(output_dir: str, repo_id: str, plan: GenerationPlan) -> str:
    """落盘执行真源 JSON + 人审视图 md；返回 JSON 路径。md 改动不回灌。"""
    from pathlib import Path

    base = Path(output_dir)
    plans = base / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", plan.intent)[:24].strip("-") or "plan"
    ts = time.strftime("%Y%m%d-%H%M%S")
    json_path = plans / f"{ts}-{slug}.plan.json"
    json_path.write_text(
        plan.model_dump_json(indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (plans / f"{ts}-{slug}.plan.md").write_text(render_plan_md(plan), encoding="utf-8")
    return str(json_path)


def render_plan_md(plan: GenerationPlan) -> str:
    """单向 json→md 人审视图；手改 md 不回灌（要改就改 json 或重新对话）。"""
    lines = [f"# 计划：{plan.intent}", "",
             f"- 作品：{plan.repo_id or '（未指定）'}",
             f"- 预算：步数≤{plan.budgets.max_steps}，GPU 任务≤{plan.budgets.max_gpu_tasks}，"
             f"LLM 调用≤{plan.budgets.max_llm_calls}", ""]
    if plan.approval_required:
        lines.append(f"需审批能力：{', '.join(plan.approval_required)}")
        lines.append("")
    read_paths = _declared_read_paths(plan)
    if read_paths:
        lines.append("将读取文件（批准计划即授权访问以下路径）：")
        lines.extend(f"- {path}" for path in read_paths)
        lines.append("")
    for index, step in enumerate(plan.steps, 1):
        lines.append(f"{index}. **{step.operation}**（id={step.id}）")
        if step.params:
            lines.append(f"   - params：`{step.params}`")
        if step.inputs_from:
            lines.append(f"   - inputs_from：{', '.join(step.inputs_from)}")
    lines.append("")
    return "\n".join(lines)


_PATH_LIKE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[/\\])")
_SECTION_SEP_RE = re.compile(r"^={5,}\s*$")

# 编译期注入的机器目录附件名（agent_graph 铸造）：只供模型解析真实 id/文件名，
# 绝不是提示词来源——fill_prompt_sections 抽取段落时必须排除，否则伪段落
# 延伸进目录文本会把 LoRA 清单/红线说明灌进 variant prompt。
LORA_CATALOG_NAME = "本机 LoRA 目录"
TEMPLATE_CATALOG_NAME = "本机工作流模板目录"
RECIPE_CATALOG_NAME = "固化流程预设清单"
KNOWLEDGE_CATALOG_NAME = "智能编造知识库"
PREV_BATCH_ATTACHMENT_NAME = "上一批生图变体"
CATALOG_ATTACHMENT_NAMES = frozenset(
    {LORA_CATALOG_NAME, TEMPLATE_CATALOG_NAME, RECIPE_CATALOG_NAME, KNOWLEDGE_CATALOG_NAME,
     PREV_BATCH_ATTACHMENT_NAME})

# 批量延续信号：重复词与批量词共现才算（「将 latent 换成 1080x720 再生成一批」命中，
# 单张重画/普通批量首编不命中——误注入只多一份附件，误判方向安全）。重复词含
# 「重新」独立形态（「重新分批生成」2026-09-05 实锤漏判）与上轮/上次指代。
_PREV_BATCH_REPEAT_RE = re.compile(
    r"再(?:次|来|出|画|生成|跑|提交)|重新|接着(?:生成|出|画|跑)|继续(?:生成|出|画|跑)"
    r"|上(?:轮|次|一批|一条|一次)")
_PREV_BATCH_SCALE_RE = re.compile(
    r"一批|多批|分批|这批|那批|批量|全套|全部|所有|每套|所有套装"
    r"|[2-9\d二三四五六七八九十]+\s*(张|套)")
# 兜底窗口内的批量出图意图（无延续语也注入上一批真源，见 prev_batch_for）
_PREV_BATCH_IMAGE_RE = re.compile(r"出图|生图|生成图|生成图片|生成视频|提交|出\s*\d+\s*张")
PREV_BATCH_MAX_AGE_SECONDS = 86400


def is_batch_continuation(text: str) -> bool:
    """批量生图延续意图判定（零 LLM）：重复词+批量词共现。"""
    source = (text or "").strip()
    if not source:
        return False
    return bool(_PREV_BATCH_REPEAT_RE.search(source)
                and _PREV_BATCH_SCALE_RE.search(source))


def latest_submit_plan(output_dir: str) -> dict | None:
    """最近的含完整变体提示词的批量计划（延续引用的机械真源，无 LLM）。

    按 mtime 新→旧扫 plans/*.plan.json（最多 5 份）：跳过无 submit_batch 或变体
    缺 prompt 的计划（如整理文档类），返回第一份合格的
    {"template_id","lora_name","variants","plan_path"}；没有则 None。
    """
    from pathlib import Path
    plans = Path(output_dir) / "plans"
    if not plans.is_dir():
        return None
    try:
        candidates = sorted(plans.glob("*.plan.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    except OSError:
        return None
    for path in candidates:
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 损坏计划跳过
            continue
        for step in plan.get("steps") or []:
            if str(step.get("operation")) != "workflow.submit_batch":
                continue
            params = step.get("params") or {}
            variants = [v for v in params.get("variants") or []
                        if isinstance(v, dict) and str(v.get("prompt") or "").strip()]
            if not variants:
                continue
            return {"template_id": str(params.get("template_id") or ""),
                    "lora_name": str(params.get("lora_name") or ""),
                    "variants": variants, "plan_path": str(path),
                    "mtime": path.stat().st_mtime}
    return None


def prev_batch_for(output_dir: str, text: str, *, now: float | None = None) -> dict | None:
    """延续上一批总判定（单一属主，2026-09-05 用户上下文规则实锤后加兜底）。

    同一会话里无论产出多少结果本质都是一条上下文：延续语命中 → 不限时效取最近
    批量计划；未命中但本轮仍是批量出图委派 → 仅取 24h 内的上一批（宁多注入
    不错过——错过会让模型凭记忆幻觉出伪套装，多注入时非延续意图可忽略该附件）。
    """
    prev = latest_submit_plan(output_dir)
    if prev is None:
        return None
    if is_batch_continuation(text):
        return prev
    source = (text or "").strip()
    if not (_PREV_BATCH_IMAGE_RE.search(source) and is_delegation_intent(source)):
        return None
    age = (now if now is not None else time.time()) - float(prev.get("mtime") or 0)
    return prev if 0 <= age <= PREV_BATCH_MAX_AGE_SECONDS else None


def prev_batch_attachment(prev: dict) -> dict:
    """把上一批变体铸成编译附件：模型只写 name+改动字段，prompt 编译期机械回填。"""
    slim = []
    for index, variant in enumerate(prev.get("variants") or []):
        if not isinstance(variant, dict):
            continue
        slim.append({"name": variant.get("name") or f"变体{index + 1}",
                     "prompt": variant.get("prompt"),
                     **{key: val for key, val in variant.items()
                        if key not in ("name", "prompt", "prompt_section",
                                       "positive_prompt")}})
    meta = "、".join(filter(None, [
        f"模板 {prev.get('template_id')}" if prev.get("template_id") else "",
        f"LoRA {prev.get('lora_name')}" if prev.get("lora_name") else "",
    ]))
    header = (
        f"上一批批量生图共 {len(slim)} 个变体{('（' + meta + '）') if meta else ''}。"
        "延续本批（提示词不变）时：variants 每项只写 "
        "{\"name\": \"<下列名称逐字复制>\"} 加**要改的**字段（如 latent_width/latent_height/"
        "lora_weight），prompt 一律省略——编译期会按 name 机械回填上一批完整提示词，"
        "没写的参数自动沿用上一批；只改部分变体就只列那几个；需要重写提示词时才直写。\n"
        "【上一批变体 JSON】\n")
    return {"name": PREV_BATCH_ATTACHMENT_NAME,
            "text": header + json.dumps(slim, ensure_ascii=False)}


def fill_prev_batch_prompts(plan, prev: dict | None) -> int:
    """延续批量时按 name 从上一批机械回填变体 prompt 与未改动参数（确定性，无 LLM）。

    模型为省输出只写 {name, 改动字段}；执行器无 LLM，必须在编译期回填。
    name 精确匹配；计划变体数与上一批相同且全缺 prompt 时按序兜底。
    回填 prompt 的同时，模型没写的注入参数（宽高/权重/嵌套 values 等）从上一批
    变体继承（模型显式写的键优先）——「重新生成这一批」不提尺寸也不会落回模板默认
    （2026-09-05 实锤：参数缺失 → 模板默认 1280×720 兜底，原意图 720×1080 失效）。
    返回回填的变体数。
    """
    if not prev:
        return 0
    prev_variants = [v for v in prev.get("variants") or [] if isinstance(v, dict)]
    by_name = {str(v.get("name") or "").strip(): v
               for v in prev_variants if v.get("name")}
    filled = 0
    for step in plan.steps:
        if step.operation != "workflow.submit_batch":
            continue
        variants = step.params.get("variants")
        if not isinstance(variants, list):
            continue
        same_count = len(variants) == len(prev_variants)
        for index, variant in enumerate(variants):
            if not isinstance(variant, dict):
                continue
            if str(variant.get("prompt") or variant.get("positive_prompt") or "").strip():
                continue
            source = by_name.get(str(variant.get("name") or "").strip())
            if source is None and same_count:
                source = prev_variants[index]  # 按序兜底
            prompt = str((source or {}).get("prompt") or "")
            if not prompt:
                continue
            variant["prompt"] = prompt
            for key, val in source.items():
                if key not in ("name", "prompt", "prompt_section", "positive_prompt",
                               "template_id"):
                    variant.setdefault(key, val)
            filled += 1
    return filled


def _is_section_heading(stripped: str) -> bool:
    """套装标题行：行首（允许 # 前缀）出现【…】标题。正文行中引用【…】（如
    「鞋履规则…【运动·套一/套二】…」）不是章节边界——判定必须锚定行首，
    否则使用说明里的套名引用会被当成新段落开头，吞掉后续说明甚至拼接的目录附件。"""
    if "【" not in stripped or "】" not in stripped:
        return False
    return stripped.startswith("【") or stripped.lstrip("#").lstrip().startswith("【")


def extract_section(doc_text: str, marker: str) -> str:
    """按标题标记机械抽取文档段落（无 LLM）：从含 marker 的行到分隔线/下一标题。"""
    lines = (doc_text or "").splitlines()
    start = next((i for i, ln in enumerate(lines) if marker in ln), None)
    if start is None:
        return ""
    body: list[str] = []
    for ln in lines[start + 1:]:
        stripped = ln.strip()
        if _SECTION_SEP_RE.match(stripped) or stripped.startswith("## ") or _is_section_heading(stripped):
            break
        body.append(ln)
    return "\n".join(ln.strip() for ln in body if ln.strip())


def extract_all_sections(doc_text: str) -> list[tuple[str, str]]:
    """抽取文档全部【标题】段落，返回 [(标题, 正文)]；正文为标题行后到分隔线/下一标题。"""
    sections: list[tuple[str, str]] = []
    lines = (doc_text or "").splitlines()
    current: tuple[str, list[str]] | None = None
    for ln in lines:
        stripped = ln.strip()
        if _SECTION_SEP_RE.match(stripped):
            if current and current[1]:
                sections.append((current[0], "\n".join(s for s in current[1] if s.strip())))
            current = None
            continue
        has_heading = _is_section_heading(stripped)
        if has_heading:
            if current and current[1]:
                sections.append((current[0], "\n".join(s for s in current[1] if s.strip())))
            name = ln.strip().lstrip("#").strip()
            current = (name.strip(" #"), [])
            continue
        if current is not None and stripped and not stripped.startswith(">"):
            current[1].append(stripped)
    if current and current[1]:
        sections.append((current[0], "\n".join(s for s in current[1] if s.strip())))
    # 只保留真正的提示词段（含质量 tag 特征），过滤纯标题/清单/过短段
    return [(n, b) for n, b in sections if len(b) > 200 and "masterpiece" in b]


def _common_variant_extra(variants: list) -> dict:
    """提取所有变体共有且值相同的非内容字段（如 width/height/seed）。

    fill_prompt_sections 重建变体时只写 name/prompt，会丢模型写入的模板注入参数；
    这些参数通常是全批统一的（宽高/种子/批次），提取后合并回每个重建变体。
    """
    extra: dict[str, Any] = {}
    first = True
    for item in variants:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key in ("name", "prompt", "prompt_section", "positive_prompt"):
                continue
            if first:
                extra[key] = value
            elif extra.get(key) != value:
                extra.pop(key, None)
        first = False
    return extra


def fill_prompt_sections(plan, attachments: list[dict]) -> int:
    """把变体里的 prompt_section 引用按预读附件机械回填为完整提示词（确定性，无 LLM）。

    模型为避免爆输出上限会写段落引用；执行器无 LLM，必须在编译期回填。
    返回回填的变体数。
    """
    docs = "\n".join(a.get("text", "") for a in attachments
                     if a.get("name") not in CATALOG_ATTACHMENT_NAMES)
    if not docs.strip():
        return 0
    filled = 0
    for step in plan.steps:
        if not step.operation.startswith("workflow.submit"):
            continue
        variants = step.params.get("variants")
        if not isinstance(variants, list):
            continue
        # 字符串变体（段落名列表）→ 转对象，名称即抽取标记
        variants = [
            {"name": item, "prompt_section": item} if isinstance(item, str) else item
            for item in variants
        ]
        # 全变体都无可用提示词 → 从文档按标题重建全部变体（终极确定性兜底）
        if not any(isinstance(v, dict) and (v.get("prompt") or v.get("positive_prompt"))
                   for v in variants):
            sections = extract_all_sections(docs)
            if sections:
                _common = _common_variant_extra(variants)
                step.params["variants"] = [
                    {"name": name, "prompt": body, **_common} for name, body in sections
                ]
                filled += len(sections)
                continue
        step.params["variants"] = variants
        for item in variants:
            if not isinstance(item, dict):
                continue
            marker = str(item.get("prompt_section") or "").strip()
            if not marker or item.get("prompt") or item.get("positive_prompt"):
                continue
            text = extract_section(docs, marker)
            if text:
                item["prompt"] = text
                filled += 1
        # 文档驱动批量的卫生兜底：模型可能把「使用说明/目录清单」等非套装段落
        # 也塞进 variants（表现为变体数多于文档真实段落数，或 prompt 里混入
        # 本机 LoRA/模板目录说明）。此时以文档标题抽取结果为唯一真源重建变体。
        sections = extract_all_sections(docs)
        if sections:
            junk_markers = ("本机 LoRA 目录", "本机工作流模板目录", "禁止写 TO_BE_RESOLVED",
                            "用户指定模板时优先")
            has_junk = any(
                isinstance(v, dict) and any(m in str(v.get("prompt") or v.get("positive_prompt") or "")
                                           for m in junk_markers)
                for v in variants
            )
            if len(variants) != len(sections) or has_junk:
                _common = _common_variant_extra(variants)
                step.params["variants"] = [
                    {"name": name, "prompt": body, **_common} for name, body in sections
                ]
                filled += len(sections)
    return filled


def _merge_duplicate_collects(plan: GenerationPlan) -> None:
    """代码保真：多个 media.collect_comfy_outputs 合并为一个（模型偶发重复排采集）。

    保留第一个 collect，把其余 collect 的 inputs_from 与 names 去重合并进去，
    后续步骤引用被删除 collect 时重写到保留步骤。
    """
    collects = [s for s in plan.steps if s.operation == "media.collect_comfy_outputs"]
    if len(collects) < 2:
        return
    keep = collects[0]
    replace: dict[str, str] = {}
    for dup in collects[1:]:
        for ref in dup.inputs_from or []:
            if ref not in (keep.inputs_from or []):
                keep.inputs_from = (keep.inputs_from or []) + [ref]
        _names = keep.params.setdefault("names", [])
        for name in dup.params.get("names") or []:
            if name not in _names:
                _names.append(name)
        replace[dup.id] = keep.id
    plan.steps = [s for s in plan.steps if s.id not in replace]
    for step in plan.steps:
        rewritten: list[str] = []
        for ref in step.inputs_from or []:
            head = ref.split(".", 1)[0]
            if head in replace:
                new_ref = replace[head] + ref[len(head):]
                if new_ref not in rewritten:
                    rewritten.append(new_ref)
            elif ref not in rewritten:
                rewritten.append(ref)
        step.inputs_from = rewritten


def _merge_duplicate_submits(plan: GenerationPlan) -> None:
    """代码保真：同模板的多个 submit_batch 合并为一个，防止重复烧 GPU。

    模型可能把「分批」误解为拆多个提交步骤；这里按 template_id 分组，保留每组
    第一个步骤，把其余步骤的 variants 去重合并进去，并把后续步骤的 inputs_from
    引用重写到保留步骤（collect 引用重复时去重）。
    """
    groups: dict[str, list] = {}
    for step in plan.steps:
        if step.operation == "workflow.submit_batch":
            groups.setdefault(str(step.params.get("template_id") or ""), []).append(step)
    merged_groups = [g for g in groups.values() if len(g) > 1]
    if not merged_groups:
        return
    remove_ids: set[str] = set()
    replace: dict[str, str] = {}
    for group in merged_groups:
        keep = group[0]
        seen = {json.dumps(v, ensure_ascii=False, sort_keys=True)
                for v in keep.params.get("variants") or [] if isinstance(v, dict)}
        for dup in group[1:]:
            for v in dup.params.get("variants") or []:
                if not isinstance(v, dict):
                    continue
                key = json.dumps(v, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    keep.params.setdefault("variants", []).append(v)
                    seen.add(key)
            remove_ids.add(dup.id)
            replace[dup.id] = keep.id
    plan.steps = [s for s in plan.steps if s.id not in remove_ids]
    for step in plan.steps:
        rewritten: list[str] = []
        for ref in step.inputs_from or []:
            head = ref.split(".", 1)[0]
            if head in replace:
                tail = ref[len(head):]
                new_ref = replace[head] + tail
                if new_ref not in rewritten:
                    rewritten.append(new_ref)
            elif ref not in rewritten:
                rewritten.append(ref)
        step.inputs_from = rewritten


def _declared_read_paths(plan: GenerationPlan) -> list[str]:
    """readonly 步骤 params 里声明的绝对路径（审批卡明示，批准即授权）。"""
    from app.services.capability_registry import get as _get
    out: list[str] = []
    for step in plan.steps:
        cap = _get(step.operation)
        if cap is None or cap.side_effect_level != "readonly":
            continue
        for value in step.params.values():
            if isinstance(value, str) and _PATH_LIKE_RE.match(value) and value not in out:
                out.append(value)
    return out


def render_plan_card(plan: GenerationPlan, json_path: str) -> str:
    """对话内计划卡：列步骤/预算/审批要求/将读取的文件。"""
    steps = "\n".join(
        f"  {i}. {step.operation}" for i, step in enumerate(plan.steps, 1))
    approval = f"\n- 需审批：{', '.join(plan.approval_required)}" if plan.approval_required else ""
    reads = _declared_read_paths(plan)
    read_note = ("；将读取文件（批准即授权）：" + "；".join(reads)) if reads else ""
    return (
        f"📋 已编译计划：\n"
        f"- 意图：{plan.intent}\n"
        f"- 步骤（{len(plan.steps)}）：\n{steps}\n"
        f"- 预算：步数≤{plan.budgets.max_steps} / GPU≤{plan.budgets.max_gpu_tasks} / "
        f"LLM≤{plan.budgets.max_llm_calls}{approval}{read_note}\n"
        f"- 文档：{json_path}\n"
        f"已投递执行队列：只读步骤直跑，写/烧卡步骤与越域读取等你批准后执行。"
    )
