"""能力注册表（Autopilot P0 单一属主）：把「计划可编排」的 services 能力面
变成机器可读清单，供 agent 编排 Demiurge 自己（docs/ROADMAP-AUTOPILOT.md）。

- **显式注册**，不做 AST 自动扫描——description 是人写的中文（做什么+影响什么），
  自动扫只产出垃圾描述；agent 内部协作用的中间服务不进清单。
- handler 只做薄适配：参数透传既有 services 函数，不藏业务；执行面无任意
  shell/MCP 兜底，未知动作拒绝而不是降级执行。
- manifest 由 ``scripts/generate_capability_manifest.py`` 导出（随源码发布），
  ``--check`` 防清单与注册表漂移。
- 首批能力（comfyui 批量模板提交路径）在本模块尾部注册；能力按 category
  分文件注册、导出合并的拆分待扩展批再做。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field

CATEGORY_COMFYUI = "comfyui"
CATEGORIES = (CATEGORY_COMFYUI, "worldbook", "character", "repo", "rag", "media", "asset",
              "novel", "knowledge")

SIDE_EFFECT_READONLY = "readonly"
SIDE_EFFECT_REVERSIBLE = "reversible"
SIDE_EFFECT_DURABLE = "durable"
SIDE_EFFECT_EXPENSIVE = "expensive"
SIDE_EFFECT_LEVELS = (SIDE_EFFECT_READONLY, SIDE_EFFECT_REVERSIBLE,
                      SIDE_EFFECT_DURABLE, SIDE_EFFECT_EXPENSIVE)

CHANNEL_SYNC = "sync"
CHANNEL_QUEUE = "queue"
CHANNELS = (CHANNEL_SYNC, CHANNEL_QUEUE)

NEEDS_MODELS = ("chat", "image", "video", "audio", "embed")


@dataclass(frozen=True)
class Capability:
    operation: str                      # 动词.宾语，全局唯一（对齐 _EVENT_ACTIONS 风格）
    category: str
    description: str                    # 中文，写给人看：做什么+影响什么
    params_schema: dict = field(default_factory=dict)
    needs_model: str | None = None      # None/chat/image/video/audio/embed
    side_effect_level: str = SIDE_EFFECT_READONLY
    channel: str = CHANNEL_SYNC
    handler: str = ""                   # 执行适配器「模块:函数」

    def to_manifest(self) -> dict:
        return {
            "operation": self.operation,
            "category": self.category,
            "description": self.description,
            "params_schema": self.params_schema,
            "needs_model": self.needs_model,
            "side_effect_level": self.side_effect_level,
            "channel": self.channel,
            "handler": self.handler,
        }


_REGISTRY: dict[str, Capability] = {}


def register(capability: Capability) -> None:
    """显式注册一条能力；operation 重复/字段非法立即抛错（注册期闸门）。"""
    op = capability.operation
    if not op or "." not in op:
        raise ValueError(f"operation 必须是「动词.宾语」形式：{op!r}")
    if op in _REGISTRY:
        raise ValueError(f"operation 重复注册：{op}")
    if capability.category not in CATEGORIES:
        raise ValueError(f"{op}: 未知 category {capability.category!r}")
    if capability.side_effect_level not in SIDE_EFFECT_LEVELS:
        raise ValueError(f"{op}: 未知 side_effect_level {capability.side_effect_level!r}")
    if capability.channel not in CHANNELS:
        raise ValueError(f"{op}: 未知 channel {capability.channel!r}")
    if capability.needs_model is not None and capability.needs_model not in NEEDS_MODELS:
        raise ValueError(f"{op}: 未知 needs_model {capability.needs_model!r}")
    if capability.handler and ":" not in capability.handler:
        raise ValueError(f"{op}: handler 必须是「模块:函数」形式：{capability.handler!r}")
    _REGISTRY[op] = capability


def get(operation: str) -> Capability | None:
    return _REGISTRY.get(operation)


def all_capabilities() -> list[Capability]:
    return [_REGISTRY[op] for op in sorted(_REGISTRY)]


def build_manifest() -> dict:
    """导出 manifest（键序稳定，供 --check 逐字节对比）。"""
    return {
        "version": 1,
        "capabilities": [cap.to_manifest() for cap in all_capabilities()],
    }


def validate_handlers() -> list[str]:
    """逐条 import handler 验证「模块:函数」存在且可调用；返回错误列表（空=通过）。"""
    errors: list[str] = []
    for cap in all_capabilities():
        if not cap.handler:
            errors.append(f"{cap.operation}: 缺少 handler")
            continue
        module_name, _, func_name = cap.handler.partition(":")
        try:
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
        except (ImportError, AttributeError) as exc:
            errors.append(f"{cap.operation}: handler 不可导入 {cap.handler}（{exc}）")
            continue
        if not callable(func):
            errors.append(f"{cap.operation}: handler 不可调用 {cap.handler}")
    return errors


def with_availability(configured_models: set[str] | frozenset[str]) -> list[dict]:
    """运行时视图：按四类模型三级代理的已配置集合打 available 标记。

    needs_model 为 None 恒可用；未配置的模型打 available:false，agent 计划阶段即见缺口。
    """
    out: list[dict] = []
    for cap in all_capabilities():
        item = cap.to_manifest()
        item["available"] = cap.needs_model is None or cap.needs_model in configured_models
        out.append(item)
    return out


# ── 首批能力：comfyui 批量模板提交路径（P2 端到端首验「N 变体批量出图」）────────

register(Capability(
    operation="workflow.list_templates",
    category=CATEGORY_COMFYUI,
    description="列出全部工作流模板（只读，产出键 templates=模板列表，不提交任何任务）。",
    params_schema={"type": "object", "properties": {}, "additionalProperties": False},
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:list_templates",
))

register(Capability(
    operation="workflow.read_exposed_fields",
    category=CATEGORY_COMFYUI,
    description="读取单个模板的 exposed 字段定义（字段名/控件/默认值/绑定），"
                "是编排注入变体值前必查的只读步骤；模板不存在会报错而不是返回空。",
    params_schema={
        "type": "object",
        "properties": {"template_id": {"type": "string"}},
        "required": ["template_id"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:read_exposed_fields",
))

register(Capability(
    operation="workflow.submit_template",
    category=CATEGORY_COMFYUI,
    description="把注入值填进模板并提交一次 ComfyUI 队列任务（烧 GPU，受模型租约约束）。",
    params_schema={
        "type": "object",
        "properties": {
            "template_id": {"type": "string"},
            "values": {"type": "object"},
            "prompt": {"type": "string"},
            "url": {"type": "string"},
            "client_id": {"type": "string"},
            "lora_name": {"type": "string"},
        },
        "required": ["template_id", "values", "prompt", "url"],
        "additionalProperties": False,
    },
    needs_model="image",
    side_effect_level=SIDE_EFFECT_EXPENSIVE,
    channel=CHANNEL_QUEUE,
    handler="app.services.workflow_submission:submit_template",
))

register(Capability(
    operation="file.read_text",
    category="repo",
    description="读取一个本地 UTF-8 文本文件并返回内容（只读）。越出作品域的读取路径"
                "会在审批卡上明示，需批准计划后才能执行。",
    params_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:read_text_file",
))

register(Capability(
    operation="lora.resolve",
    category="comfyui",
    description="按名称/触发词模糊解析本机 LoRA（对齐 ComfyUI 已安装枚举与已存触发词元数据），"
                "返回真实文件名与建议权重。计划里 lora_name 可写近似名，由本能力归一。",
    params_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:lora_resolve",
))

register(Capability(
    operation="lora.list",
    category="comfyui",
    description="列出本机全部 LoRA（文件名+触发词+建议权重+备注）。用户说「用 krea2 的」"
                "这类宽泛指向时，先列出候选让用户选择，禁止替用户猜。",
    params_schema={"type": "object", "properties": {}, "additionalProperties": False},
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:lora_list",
))

register(Capability(
    operation="media.collect_comfy_outputs",
    category="media",
    description="轮询 ComfyUI 历史取回已提交任务的图片，落作品文件夹并注册进资产库"
                "（generation RAG，挂提示词与「智能编造计划」标签）。写在作品域内。",
    params_schema={
        "type": "object",
        "properties": {
            "prompt_ids": {"type": "array", "items": {"type": "string"}},
            "submit_result": {"type": "object"},
            "comfyui_url": {"type": "string"},
            "output_dir": {"type": "string"},
            "repo_id": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
            "prompts": {"type": "array", "items": {"type": "string"}},
            "timeout_seconds": {"type": "integer"},
        },
        "required": ["comfyui_url", "output_dir", "repo_id"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_REVERSIBLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:collect_comfy_outputs",
))

register(Capability(
    operation="workflow.submit_batch",
    category=CATEGORY_COMFYUI,
    description="对同一模板按变体值列表批量提交 ComfyUI 队列（每个变体一次任务，"
                "单条失败隔离不中断整批；烧 GPU，受每计划配额约束）。",
    params_schema={
        "type": "object",
        "properties": {
            "template_id": {"type": "string"},
            "variants": {"type": "array", "items": {"type": "object"}, "minItems": 1},
            "prompt": {"type": "string"},
            "url": {"type": "string"},
            "client_id": {"type": "string"},
            "lora_name": {"type": "string"},
        },
        "required": ["template_id", "variants", "url"],
        "additionalProperties": False,
    },
    needs_model="image",
    side_effect_level=SIDE_EFFECT_EXPENSIVE,
    channel=CHANNEL_QUEUE,
    handler="app.services.capability_handlers:submit_batch",
))


# ── P3 通用创作能力（作品域内写，路径域由 plan_validator + 执行期租约兜底）────

register(Capability(
    operation="file.write_text",
    category="repo",
    description="在作品域内写一个 UTF-8 文本文件（绝对路径；已存在默认拒绝，"
                "可声明 overwrite 覆盖）。不能写二进制，不能写目录。",
    params_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:write_text_file",
))

register(Capability(
    operation="file.list_dir",
    category="repo",
    description="列出一个本地目录的条目（名称/类型/大小），不返回文件内容。越域读取需审批。",
    params_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}, "max_entries": {"type": "integer"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:list_dir",
))

# 世界书条目 5 字段契约（固化02 §1/§3.3）：只有这五个字段有效；keys 非空、
# comment 以「角色卡·<名>」开头（视觉画像提取器锚点）。嵌套 schema 供 validator
# 递归校验 + handler 归一容错（模型写 key 等杂字段会被归一/拒绝）。
WORLDBOOK_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "comment": {"type": "string"},
        "keys": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "constant": {"type": "boolean"},
        "enabled": {"type": "boolean"},
    },
    "required": ["content", "comment", "keys"],
    "additionalProperties": False,
}

register(Capability(
    operation="worldbook.upsert_repo",
    category="worldbook",
    description="向当前作品的世界书快照 upsert 条目（同 keys/comment 更新，新条目追加）。"
                "条目只认 content/comment/keys/constant/enabled 五字段：keys 必须非空、"
                "comment 按『角色卡·<名>』前缀写（视觉画像锚点）。"
                "写入路径由执行环境归一注入，模型不得填 base。",
    params_schema={
        "type": "object",
        "properties": {
            "entries": {"type": "array", "items": WORLDBOOK_ENTRY_SCHEMA, "minItems": 1},
            "repo_id": {"type": "string"},
            "base": {"type": "string"},
        },
        "required": ["entries"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:upsert_repo_worldbook",
))

register(Capability(
    operation="character.upsert_repo",
    category="character",
    description="把 JSON 角色卡归一后写入当前作品目录（<作品>/<卡名>/card.json，覆盖式）。"
                "写入路径由执行环境归一注入，模型不得填 base。",
    params_schema={
        "type": "object",
        "properties": {"card": {"type": "object"}, "base": {"type": "string"}},
        "required": ["card"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:upsert_repo_character",
))

register(Capability(
    operation="doc.create_repo",
    category="repo",
    description="在作品目录 docs/ 下创建 Markdown 文档（相对路径，拒绝 .. 穿越与越界）。",
    params_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string"},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean"},
            "base": {"type": "string"},
        },
        "required": ["rel_path", "content"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:create_repo_doc",
))


register(Capability(
    operation="file.edit",
    category="repo",
    description="按 str_replace 语义修改 UTF-8 文本文件（old_str 唯一命中；replace_all 可全替换）。"
                "用于修改代码/配置——本项目有问题时 Agent 可基于项目本身优化。",
    params_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_str", "new_str"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:edit_text_file",
))

register(Capability(
    operation="project.run_shell",
    category="repo",
    description="在工作目录执行一条命令行（cwd 必须显式绝对路径；默认超时 60s；stdout/stderr 截断回传）。"
                "用于操作电脑/运行项目命令；durable，approval 模式需批准。",
    params_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout_seconds": {"type": "integer"},
        },
        "required": ["command", "cwd"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:run_shell",
))


register(Capability(
    operation="plan.instantiate_recipe",
    category="repo",
    description="按固化流程预设（计划配方）整条重放：提交到执行队列，durable/expensive "
                "步骤照常走审批与配额闸门。仅限已保留（saved）的配方。",
    params_schema={
        "type": "object",
        "properties": {
            "recipe_id": {"type": "string"},
            "output_dir": {"type": "string"},
            "repo_id": {"type": "string"},
            "param_overrides": {"type": "object"},
        },
        "required": ["recipe_id"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:instantiate_recipe",
))


register(Capability(
    operation="character.import_source",
    category="character",
    description="从本地文件导入一张 ST/通用角色卡（PNG 内嵌或 JSON）到角色卡源库"
                "（目录由后端配置真源决定）。TavernCard V1/V2/V3 原生兼容；"
                "可选把卡内嵌世界书外拆为独立世界书。",
    params_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "overwrite": {"type": "boolean"},
            "extract_worldbook": {"type": "boolean"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_DURABLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:import_source_card",
))


register(Capability(
    operation="character.migrate_scan",
    category="character",
    description="只读扫描一张 ST/通用卡（PNG 内嵌或 JSON）或独立世界书/预设/正则文件，"
                "产出迁移体检报告：逐条目标注待转写点（注入位语义 order/depth/atDepth、"
                "constant 越权、keys 缺失/过长、渲染层 <status>/<roll>、运行时表格 "
                "<if cell=…>、dict 容器、first_mes 空、视觉画像前缀缺失）。"
                "第二套固定流程（机械+LLM 转写）的机械前置；不写任何文件，"
                "命中项交 LLM 判断转写后经 upsert/import 落盘。",
    params_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:migrate_scan_source",
))


# ── 固化02 脚本辅助层（novel.*）：小说预处理机械工具（长文上下文防爆）─────────

register(Capability(
    operation="novel.extract_epub",
    category="novel",
    description="把 .epub 长篇小说按 OPF spine 顺序抽取为分章纯文本并落盘"
                "（固化02 脚本辅助层 T1）。epub 源可在作品外（只读）；输出路径"
                "out_txt 与 work_dir 二选一——out_txt 显式给全路径（须在作品域/"
                "临时工作区）；或给 work_dir（作品根）+ 可选 book_name（缺省取 epub"
                "文件名），自动落 <work_dir>/_prep/<书名>.full.txt，不用手拼 _prep/。"
                "产出用「===== 章节 =====」标记，供 novel.survey / "
                "novel.charfacts 复用；抽取后禁止再整本读全文（上下文防爆）。",
    params_schema={
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "out_txt": {"type": "string"},
            "work_dir": {"type": "string"},
            "book_name": {"type": "string"},
        },
        "required": ["src"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_REVERSIBLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:novel_extract_epub",
))

register(Capability(
    operation="novel.survey",
    category="novel",
    description="只读清点分章全文：章节标题清单、称呼后缀候选名词频、红线词计数"
                "（固化02 脚本辅助层 T2）。产物是候选角色名单与章节锚点，先给用户确认"
                "转写范围再进素材切段；不写任何文件。红线词只计数不代判（年龄口径交 LLM）。",
    params_schema={
        "type": "object",
        "properties": {
            "full_txt": {"type": "string"},
            "top_names": {"type": "integer"},
        },
        "required": ["full_txt"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:novel_survey",
))

register(Capability(
    operation="novel.charfacts",
    category="novel",
    description="按候选名单从分章全文切素材段，逐名落 <out_dir>/<name>.txt"
                "（固化02 脚本辅助层 T3，上下文防爆核心）。mode: top_n = 全书前 N 段完整"
                "段落；anchor = 首·中·末 320 字锚点窗口。输出目录 out_dir 与 work_dir"
                "二选一——out_dir 显式给；或给 work_dir（作品根）自动落 "
                "<work_dir>/_prep/charfacts/，不用手拼 _prep/。素材是中间产物不是条目："
                "模型只读素材文件后经 worldbook.upsert_repo 分批写条目；零命中名字会明确报告。",
    params_schema={
        "type": "object",
        "properties": {
            "full_txt": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
            "out_dir": {"type": "string"},
            "work_dir": {"type": "string"},
            "mode": {"type": "string"},
            "max_paras": {"type": "integer"},
        },
        "required": ["full_txt", "names"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_REVERSIBLE,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:novel_charfacts",
))

register(Capability(
    operation="novel.scan_anonymity",
    category="novel",
    description="落盘匿名/红线机械扫描（固化02 §3.6 收尾闸门，readonly）：在条目"
                "content/keys/comment 三处查主角名（含姓/名/爱称/后缀粒度，名单由 LLM 从"
                "原作提取）、单花括号 {user} f-string 陷阱、硬禁词零命中；{{user}} 占位计数"
                "缺失给警告。passed=False 阻断交付；台词爱称第二遍语义扫描仍由 LLM 兜底。"
                "取数二选一：显式给 entries，或只给 repo_id（base 由执行环境注入作品根）"
                "机械读作品世界书快照再扫——approval 计划里写后者即可编进闸门步骤。",
    params_schema={
        "type": "object",
        "properties": {
            "entries": {"type": "array", "items": {"type": "object"}},
            "protagonist_names": {"type": "array", "items": {"type": "string"}},
            "repo_id": {"type": "string"},
            "base": {"type": "string"},
        },
        "required": ["protagonist_names"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:novel_scan_anonymity",
))


# ── 固化技能按需装载（三固化 skill 化 A2）：命中触发场景才拉全文 ──────────────

register(Capability(
    operation="knowledge.load_doc",
    category="knowledge",
    description="按名拉取固化技能/知识全文（readonly）。固化技能（frontmatter 带 skill，"
                "见目录注入的【固化技能库】清单）命中触发场景时，必须先调用本能力拉全文"
                "照其结构与质量标准执行，禁止凭目录一句话另搞一套；无 frontmatter 的"
                "普通知识文档由注入常驻，无需调用。",
    params_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    needs_model=None,
    side_effect_level=SIDE_EFFECT_READONLY,
    channel=CHANNEL_SYNC,
    handler="app.services.capability_handlers:knowledge_load_doc",
))
