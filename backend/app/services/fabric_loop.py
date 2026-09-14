"""智能编造 Agent 自由循环（P6）：模型逐步决定调用哪个能力，结果回填，直到完成。

对标 DeepSeek Harness 的 agent loop：
- 一个 step = 一次模型决策 + 可选一次工具执行 + 结果回填；
- 模型看到能力清单（工具 schema），自由选择下一步调用什么；
- 审批/沙盒在工具执行前拦截（capability_sandbox 租约，approval/full 两档）；
- 不再要求"编译成固定计划后机械执行"——模型可观察结果并自行修正。

第一版边界：full 模式跑完整自由循环；approval 模式遇到 durable/expensive
且无租约时暂停返回 awaiting_approval（批准后由调用方继续）。

带图任务（如看图反推外貌→生成套装文档）：images 非空时首条 user 消息变为
多模态内容块（text + image_url），模型调用走 chat_messages 多消息通道；
structured 原生通道传输的是 JSON 字符串，载不动图片，带图时跳过。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from app.services import capability_registry, capability_sandbox, structured_output


class FabricDecision(BaseModel):
    """模型每步的决策：调用一个能力，或宣布完成。"""

    tool: str = ""              # 要调用的 operation；done=true 时可为空
    params: dict[str, Any] = field(default_factory=dict)
    done: bool = False          # 模型认为任务已完成
    reply: str = ""             # 完成时给用户的最终回复
    thinking: str = ""          # 本步判断说明（2026-09-06：展示给用户看思考内容）


@dataclass
class FabricOutcome:
    status: str = "done"        # done | awaiting_approval | error | step_limit
    reply: str = ""
    steps: list[dict] = field(default_factory=list)   # 每步 {tool, params, ok, result/error}
    error: str = ""
    lease_id: str = ""          # 需要批准时，批准端点要用的租约（subject）
    pending_tool: str = ""      # 等待批准的工具
    messages: list[dict] = field(default_factory=list)  # 内部对话历史（断点续跑 checkpoint）


def _manifest_lines(capabilities: list[dict]) -> str:
    lines = []
    for item in capabilities:
        if not item.get("available", True):
            continue
        schema = item.get("params_schema") or {}
        required = set(schema.get("required") or [])
        props = schema.get("properties", {})
        params = "、".join(
            f"{name}*" if name in required else f"{name}(可选)" for name in props)
        lines.append(
            f"- {item['operation']}：{item['description']} 参数：{params or '无'}")
    return "\n".join(lines)


_SYSTEM = (
    "你是 Demiurge 的智能编造 Agent。你可以自由调用工具完成用户目标：\n"
    "每步只输出一个 JSON 对象，格式二选一：\n"
    "1) 调用工具：{\"tool\": \"清单里的 operation\", \"params\": {具体参数}}\n"
    "2) 宣布完成：{\"done\": true, \"reply\": \"给用户的最终回复\"}\n"
    "每个决策都附带 thinking 字段（1-2 句中文，说明这一步的判断依据/为什么这么做），"
    "供用户查看执行过程；输出保持紧凑 JSON，禁止解释文字。\n"
    "调用工具后，你会收到工具结果；观察结果，如果失败就换方案或修复，不要重复同一个失败调用。\n"
    "所有参数必须写具体值，禁止 {{...}}、TO_BE_RESOLVED 等占位符。\n"
    "【交付前置自检（宣布 done 前必过）】\n"
    "逐项盘点用户目标里尚未拍板的细节（参考图用哪张/要不要配图、场景与范围取舍、"
    "风格与命名取向、数量与规格等）。判定标准：该细节缺失会导致成品返工，"
    "且无法从用户历史消息里可靠推断 → 属于关键缺口。\n"
    "- 有关键缺口：不产出成品。回复改为「待确认清单」：逐项列出问题 + 你的建议默认值"
    " + 一句理由，引导用户逐项确认后再执行交付；用户口头带过（如「都行」「不强求」）"
    "不等于细节已定，仍须列入清单让用户过目追认。\n"
    "- 无关键缺口：直接交付，并在回复末尾注明本次代用户做的假设，便于追认。\n"
    "【可用工具清单】\n__MANIFEST__\n"
    "【当前输出目录】__OUTPUT_DIR__\n"
    "【用户目标】\n__INTENT__"
)


def _progress_message(intent: str, steps: list[dict]) -> dict:
    """任务进度卡（2026-09-06 治本；2026-09-12 P1 改**尾部临时消息**）。

    为什么原样保留内容、只挪位置：模型长期自由循环几十步后容易迷失任务（实锤：固化02
    转卡中途被历史生图带偏去 load 固化01/list_templates/lora.resolve），进度卡必须每步可见。
    但**位置**决定前缀缓存能不能命中——原先每步重写 `messages[0]`，请求前缀从第 1 个字符
    就变了，后面全部按全价计费。改为不进 `messages` 的尾部临时消息后：
    - `messages[:-1]`（真实对话）逐字节稳定、append-only；
    - 动态内容落在**尾部**，正是三家缓存机制与 dsh 官方的一致做法（dynamic-last）。
    **信息一字不少，只是位置从「头」挪到「尾」。**
    """
    progress = "、".join(
        f"{i + 1}.{s.get('tool')}" for i, s in enumerate(steps))
    return {"role": "user", "content": (
        f"【任务进度】已完成 {len(steps)} 步：{progress or '（尚未执行）'}。"
        f"\n当前任务：{intent}。"
        "\n继续按已加载的固化流程执行当前任务；禁止调用与当前任务无关的能力"
        "（如生图/工作流提交/编辑等），禁止中途更换任务。")}


def _usage_sink(trace: Callable | None, model: str) -> Callable[[dict], None]:
    """自由循环的模型调用 usage 落 trace（2026-09-12 P0，成本与缓存命中率观测）。

    为什么非要它：自由循环每步的**决策调用不落 run_trace**（见技术手册 B-09
    「取轨迹的方法」），于是「这个任务跑了多少步、输入 token 花了多少、前缀缓存命中
    多少」永远算不出来——P0 的观测基线正是缺这一环。这里把 usage 包成 `model.usage`
    事件推给同一条 trace 通道，与 roleplay 侧（`agent_graph._chat_with_optional_stream`）
    **同名同结构**，`trace_replay` 既有聚合不必改动即可吃到。

    失败静默：观测不得影响循环本身（trace 回调由调用方提供，可能已失效）。
    未覆盖：混合模式的本地模型降级调用（`_local_decision_call` 走裸 urllib，不产生 usage）。
    """
    if trace is None:
        return lambda _stats: None

    def sink(stats: dict) -> None:
        try:
            trace("model.usage", model=model, usage=stats)
        except Exception:  # noqa: BLE001 - 观测失败不影响循环
            pass
    return sink


def _chat_messages(base_url: str, api_key: str, model: str,
                   messages: list[dict], **kwargs) -> str:
    """带图自由循环的默认模型通道：多消息列表直发（content 允许多模态内容块）。"""
    from app.services import llm
    return llm.chat_messages(base_url, api_key, model, messages, **kwargs)


_READ_SUMMARY_MARK = "【分卷已读摘要】"

# 2026-09-06 用户定案 A+B：内容类参数敏感词拦截（网关 400 兜底）。
# 背景：NSFW 小说转卡时模型在卡纲/条目输出里带露骨词汇 → xtoken 网关
# 400 LITELLM_ERROR「输入或生成内容可能包含不安全或敏感内容」，fabric.retry
# 重试仍败（22:53 换输出成功=输出侧触发、23:00 重试仍败=输入侧叠加）。
# 代码层兜底：写内容能力执行前检查 params 字符串字段，命中即不执行、
# 回填「按现有 NSFW 卡机制命名风格改写」让模型重写。
# 词表参照现有可过审卡的用词风格（「女尊采补与男畜逆袭」「灌精受孕与洗脑调教」
# 能落盘=这类机制命名词安全）；具体性行为词汇一律拦截。
_NSFW_BLOCK_WORDS = (
    "肉棒", "鸡巴", "龟头", "阳具", "阴茎", "肏", "操逼", "淫水", "淫液",
    "精液", "射精", "内射", "小穴", "阴道", "阴蒂", "子宫", "宫颈", "乳房",
    "乳头", "乳尖", "奶子", "高潮", "做爱", "交合", "插入", "抽插", "口交",
    "肛交", "女奴", "性奴", "娼妇", "奸污", "凌辱", "肉便器", "生殖机器",
    "淫荡", "骚货", "淫娃", "浪叫", "肉缝", "穴口", "爱液", "春药", "媚药",
    "迷奸", "轮奸", "群交", "双飞", "母狗", "调教奴",
)
_NSFW_BLOCK_CAPABILITIES = ("doc.create_repo", "worldbook.upsert_repo",
                            "character.upsert_repo", "character.import_source",
                            "file.write_text", "file.edit")


_NSFW_SKIP_KEYS = frozenset(
    ("path", "base", "rel_path", "repo_id", "output_dir", "out_dir",
     "work_dir", "full_txt", "out_txt", "file", "file_name", "dir",
     "workdir", "avatar", "image", "img"))


def _nsfw_blocked_params(params: dict) -> str | None:
    """递归检查 params 的字符串字段是否含敏感词。命中返回提示文案，否则 None。

    只检查「内容写入类」字段（content/正文/条目/keys），路径/文件类字段跳过
    （文件路径含「女奴」等词不应误伤）。
    """
    hits: list[str] = []
    stack: list = list((params or {}).items())
    while stack:
        key, value = stack.pop()
        if key in _NSFW_SKIP_KEYS:
            continue
        if isinstance(value, str):
            for w in _NSFW_BLOCK_WORDS:
                if w in value:
                    hits.append(w)
                    break
        elif isinstance(value, dict):
            stack.extend(value.items())
        elif isinstance(value, list):
            for it in value:
                stack.append(("", it))
        if len(hits) >= 3:
            break
    if not hits:
        return None
    return (f"输出内容包含敏感词（{'/'.join(hits)}），会触发网关内容审核 400。"
            "请按现有 NSFW 合集卡的机制命名风格改写（如「全局机制·女尊采补与男畜逆袭」"
            "「全局机制·灌精受孕与洗脑调教」），条目正文用设定化描述，"
            "禁止出现具体性行为词汇。")





def _local_chat_config() -> dict | None:
    """从 user_state 找本地 ollama 聊天模型配置（baseUrl 含 localhost/11434）。

    用户定案（2026-09-06 混合模式）：敏感内容交本地模型处理（无审核），
    主流程用云端模型；没有本地模型 → 返回 None（回退云端重写提示）。
    """
    try:
        from pathlib import Path

        p = Path(__file__).resolve().parents[1] / "data" / "user_state.json"
        st = json.loads(p.read_text(encoding="utf-8"))
        for m in (st.get("settings") or {}).get("chatModels") or []:
            base = str(m.get("baseUrl") or "")
            if "11434" in base or "localhost" in base:
                return {"baseUrl": base.rstrip("/"),
                        "apiKey": str(m.get("apiKey") or "ollama"),
                        "model": str(m.get("modelName") or "")}
    except Exception:  # noqa: BLE001 - 配置不可读静默回退
        return None
    return None


def _local_rewrite(text: str, local: dict | None = None) -> str | None:
    """本地模型 NSFW 转写助手：把露骨内容改写为设定化条目。

    立场与口径同「制卡师立场」：NSFW 是正当创作，坦然专业设定化处理；
    保留设定信息（角色关系/体质/机制/好感度），用机制命名风格
    （如「全局机制·女尊采补与男畜逆袭」），禁止具体性行为词汇。
    返回改写文本；本地不可用/调用失败/无输出 → None。
    """
    if local is None:
        local = _local_chat_config()
    if not local or not local.get("model"):
        return None
    import urllib.error
    import urllib.request

    system = (
        "你是专业的 NSFW 合集卡设定转写助手。把用户给的露骨内容改写为设定化条目文本。"
        "保留全部设定信息（角色关系/主仆契约/体质机制/好感度），用机制命名风格"
        "（如「全局机制·女尊采补与男畜逆袭」「全局机制·灌精受孕与洗脑调教」），"
        "条目正文是设定描述，禁止出现具体性行为词汇（肉棒/肏/淫水/精液/小穴等）。"
        "只输出改写后的文本，不要解释。"
    )
    body = {
        "model": local["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "stream": False, "max_tokens": 2000, "temperature": 0.3,
    }
    try:
        req = urllib.request.Request(
            local["baseUrl"] + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=120)
        out = json.loads(resp.read())
        content = str((out.get("message") or {}).get("content") or "").strip()
        return content or None
    except Exception:  # noqa: BLE001 - 本地不可用/超时静默回退
        return None


def _local_rewrite_params(params: dict, local: dict | None = None) -> dict | None:
    """本地模型改写 params 中含敏感词的字符串字段，返回改写后的 params。

    任一字段改写失败/改写结果仍含敏感词 → None（回退云端重写提示）。
    元数据字段（path/base/rel_path 等）跳过不改。
    """

    def rewrite_node(v: Any) -> Any:
        if isinstance(v, str):
            if any(w in v for w in _NSFW_BLOCK_WORDS):
                out = _local_rewrite(v, local)
                if out is None or _nsfw_blocked_params({"content": out}):
                    return None
                return out
            return v
        if isinstance(v, dict):
            out: dict = {}
            for k, val in v.items():
                if k in _NSFW_SKIP_KEYS:
                    out[k] = val
                else:
                    r = rewrite_node(val)
                    if r is None:
                        return None
                    out[k] = r
            return out
        if isinstance(v, list):
            out = []
            for it in v:
                r = rewrite_node(it)
                if r is None:
                    return None
                out.append(r)
            return out
        return v

    try:
        rewritten = rewrite_node(params)
    except Exception:  # noqa: BLE001
        return None
    if rewritten is None:
        return None
    if _nsfw_blocked_params(rewritten):
        return None
    return rewritten


def _local_decision_call(messages: list[dict], local: dict | None = None):
    """本地模型生成决策 JSON（无审核，NSFW 上下文也能输出）。

    混合模式核心（2026-09-06 用户定案）：云端决策被网关 400（输入/输出内容审核）
    时，自动降级用本地模型生成决策——云端失败不白跑、回合不崩。
    返回 FabricDecision；本地不可用/调用失败/解析失败 → None（回退云端重试）。
    """
    if local is None:
        local = _local_chat_config()
    if not local or not local.get("model"):
        return None
    import urllib.error
    import urllib.request

    body = {
        "model": local["model"],
        "messages": [
            {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
            for m in messages
        ],
        "stream": False, "max_tokens": 4000, "temperature": 0.2,
    }
    try:
        req = urllib.request.Request(
            local["baseUrl"] + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=180)
        out = json.loads(resp.read())
        content = str((out.get("message") or {}).get("content") or "").strip()
        if not content:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        return FabricDecision.model_validate_json(cleaned)
    except Exception:  # noqa: BLE001 - 本地不可用/超时/解析失败静默回退
        return None


# ── 压缩改 append-only：surface 替换声明 + 投影（2026-09-12 P4） ───────────────
#
# 设计真源：`docs/memory/prompt-cache-design-2026-09-12.md` §五 P4 / §十 10.3。
#
# 问题（P4 前）：`_compress_read_chunks` / `_compress_upsert_results` 直接**就地改写**
# `messages[i]["content"]`。历史消息被改写 = 该条之后的请求前缀每步重算，上游 KV
# 缓存全部失效（三家缓存铁律第 ② 条明确要求 append-only）；且历史真源被就地篡改，
# 断点续跑/重放看到的已是压缩后的内容，不可复现（AGENTS「会话快照是历史真源」）。
#
# 改法（对齐 DeepSeek 官方 harness 的 `surfaceOp: {op:"replace"}`，
# `dsh-compaction-basic\lib\index.js:586-619`）：
#   ① `messages` 是**原始日志**，永不改写；压缩只在**尾部追加一条替换声明**；
#   ② 发模型前用 `_project_messages` 投影出「表面视图」——声明条目被摘掉、被声明的
#      目标条目换成摘要。压缩效果（省 token）由投影保证，日志完整性不受影响。
# 收益：日志逐字节保留 → 断点续跑可重放；每条目标至多被替换一次（raw→摘要单调），
# 声明本身是追加的 → 前缀失效点收敛且可预期。
_SURFACE_OP_KEY = "_fabric_surface_op"


def _surface_op_of(message: object) -> dict | None:
    """该条是否为「替换声明」；不是则 None（普通消息照常参与对话）。

    先用子串快筛再 json 解析——投影/幂等判定每步都会扫全表，不能每条都真解析。
    """
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or _SURFACE_OP_KEY not in content:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or _SURFACE_OP_KEY not in payload:
        return None
    return payload


def _declared_targets(messages: list[dict]) -> set[int]:
    """已被声明替换的原始下标集合 —— 压缩的幂等判据。

    注意：原文现在**永不改写**，所以不能再靠「原文里有没有摘要标记」判幂等
    （那是就地改写时代的判据），只能靠「该下标有没有被声明过」。
    """
    targets: set[int] = set()
    for message in messages:
        op = _surface_op_of(message)
        if op is not None and isinstance(op.get("target"), int):
            targets.add(op["target"])
    return targets


def _append_surface_replace(messages: list[dict], target: int, replacement: dict) -> None:
    """尾部追加一条「把第 target 条换成 replacement」的声明（原文一字不动）。"""
    messages.append({"role": "system", "content": json.dumps({
        _SURFACE_OP_KEY: "replace",
        "target": target,
        "message": replacement,
    }, ensure_ascii=False)})


def _project_messages(messages: list[dict]) -> list[dict]:
    """原始日志 → 表面视图（surface）：应用全部替换声明，摘掉声明本身。

    投影后消息的**条数位置与角色**与压缩前一致（只换内容），故工具调用与其结果的
    配对结构不被破坏；同一目标被声明多次时以**最后一条**声明为准。无声明时返回
    浅拷贝（同一批 dict 对象，零额外开销）。
    """
    op_indexes: set[int] = set()
    replacements: dict[int, dict] = {}
    for index, message in enumerate(messages):
        op = _surface_op_of(message)
        if op is None:
            continue
        op_indexes.add(index)
        target = op.get("target")
        replacement = op.get("message")
        if isinstance(target, int) and isinstance(replacement, dict):
            replacements[target] = replacement
    if not op_indexes:
        return list(messages)
    return [replacements.get(index, message)
            for index, message in enumerate(messages) if index not in op_indexes]


def _compress_read_chunks(messages: list[dict]) -> None:
    """分卷读上下文治理（2026-09-06 A 方案）：同一文件递增 offset 的 file.read_text
    已读卷压缩为一行摘要，只保留最新一卷原文。

    背景：模型分卷读 24 万字小说（12 卷 × 20k），tool.result 全文全部累积在
    messages，每步决策全量重发 → 上下文爆炸，读完书后决策退化（第 15 步无效
    决策 error 实锤：既没调工具也没宣布完成）。压缩后模型仍知道「已读
    0-220000/241709」，需要细节可再 read_text 重读指定段。幂等：已声明的不重复声明。

    2026-09-12 P4：不再就地改写历史 tool_result，改为**尾部追加替换声明**，
    发送前由 `_project_messages` 投影（省 token 的效果不变，日志完整性保住）。
    """
    from collections import defaultdict

    declared = _declared_targets(messages)
    calls: list[tuple[int, int, str, int, str]] = []  # (ai_idx, user_idx, path, offset, raw)
    i = 0
    n = len(messages)
    while i < n - 1:
        ai = messages[i]
        us = messages[i + 1]
        if ai.get("role") == "assistant" and us.get("role") == "user":
            try:
                call = json.loads(ai["content"])
            except (TypeError, ValueError, KeyError):
                i += 1
                continue
            if call.get("tool") == "file.read_text":
                params = call.get("params") or {}
                path = str(params.get("path") or "")
                offset = params.get("offset")
                if path and isinstance(offset, int):
                    calls.append((i, i + 1, path, offset, str(us.get("content") or "")))
        i += 1
    if not calls:
        return
    by_path: dict[str, list] = defaultdict(list)
    for ai_idx, us_idx, path, offset, raw in calls:
        by_path[path].append((ai_idx, us_idx, offset, raw))
    for path, items in by_path.items():
        if len(items) <= 1:
            continue
        items.sort(key=lambda x: x[2])
        max_offset = items[-1][2]
        for _ai_idx, us_idx, offset, raw in items:
            if offset == max_offset:
                continue  # 最新一卷保留原文
            if us_idx in declared:
                continue  # 幂等：已声明替换
            chars = 0
            total = ""
            try:
                outer = json.loads(raw)
                inner = json.loads(outer.get("tool_result") or "{}")
                chars = int(inner.get("chars_read") or 0)
                total = str(inner.get("total") or "")
            except (TypeError, ValueError, KeyError):
                pass
            summary = (
                f"{_READ_SUMMARY_MARK} path={path} offset={offset} "
                f"已读约 {chars} 字符（{'全书 ' + total if total else '总量未知'}），原文已压缩；"
                "需要该段细节时用 file.read_text offset 重读。"
            )
            _append_surface_replace(messages, us_idx, {
                "role": "user",
                "content": json.dumps({"tool_result": summary}, ensure_ascii=False)})

_UPSERT_SUMMARY_KEEP = 3


def _compress_upsert_results(messages: list[dict]) -> None:
    """2026-09-07 治本：压缩 messages 里旧的 worldbook.upsert_repo 决策参数——
    assistant 消息每次带 1-2k 字条目（entries），几十步累积 32 万字符（23:00 实锤
    131 步/268 条），续跑全量重发导致模型决策退化。旧 upsert 决策的 entries 替换为
    comment 摘要，只保留最近 _UPSERT_SUMMARY_KEEP 条原文。幂等。

    2026-09-12 P4：同 `_compress_read_chunks` —— 改**尾部追加替换声明**、原文保留；
    幂等判据由「原文里有没有 UPSERT_ENTRIES 标记」改为「该下标有没有被声明过」。
    """
    declared = _declared_targets(messages)
    ids: list[tuple[int, str]] = []  # (ai_idx, comment)
    i = 0
    n = len(messages)
    while i < n:
        ai = messages[i]
        if ai.get("role") != "assistant":
            i += 1
            continue
        try:
            call = json.loads(ai["content"])
        except (TypeError, ValueError, KeyError):
            i += 1
            continue
        if call.get("tool") != "worldbook.upsert_repo":
            i += 1
            continue
        if i in declared:
            i += 1  # 已声明替换
            continue
        try:
            params = call.get("params") or {}
            entries = params.get("entries") or []
            comments = "、".join(
                str(e.get("comment") or "?")[:18]
                for e in entries if isinstance(e, dict))
        except Exception:
            comments = "?"
        ids.append((i, comments))
        i += 1
    keep_from = max(0, len(ids) - _UPSERT_SUMMARY_KEEP)
    for idx, (ai_idx, comments) in enumerate(ids):
        if idx >= keep_from:
            continue
        try:
            call = json.loads(messages[ai_idx]["content"])
            params = call.get("params") or {}
            entries = params.get("entries") or []
            count = len(entries) if isinstance(entries, list) else 0
            if count == 0:
                continue
        except Exception:
            continue
        params["entries"] = ("UPSERT_ENTRIES 已写 " + str(count) + " 条：" + comments
                             + "（原文已压缩；需要细节可查看作品 worldbook.json 快照）")
        _append_surface_replace(messages, ai_idx, {
            "role": "assistant",
            "content": json.dumps(
                {"tool": "worldbook.upsert_repo", "params": params}, ensure_ascii=False)})



def _merge_read_intervals(intervals: list) -> list:
    """合并已读区间 [(start, end, step, head)]：相邻/重叠合并，保留最早 step 与首个 head。

    2026-09-09 已读段缓存：读了 0-20000 与 20000-40000 后合并为 [0,40000)，
    模型再读 0-10000 也能命中「已读」拦截，而不是只拦完全相同的 offset。
    """
    if not intervals:
        return []
    merged = sorted(intervals, key=lambda x: (x[0], x[1]))
    out: list = []
    for item in merged:
        s, e, step, head = item
        if out and s <= out[-1][1]:
            prev = out[-1]
            out[-1] = (prev[0], max(prev[1], e), prev[2], prev[3])
        else:
            out.append(item)
    return out


def _dispatch(operation: str, params: dict) -> dict:
    cap = capability_registry.get(operation)
    if cap is None:
        raise ValueError(f"能力清单里没有「{operation}」")
    if not cap.handler:
        raise ValueError(f"能力「{operation}」未注册 handler")
    module_name, _, func_name = cap.handler.partition(":")
    import importlib
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    # 只透传 schema 声明的参数（薄适配纪律）
    allowed = set((cap.params_schema or {}).get("properties") or {})
    filtered = {k: v for k, v in params.items() if k in allowed}
    result = func(**filtered)
    return result if isinstance(result, dict) else {"result": result}


def run_loop(*, intent: str, history: str = "", capabilities: list[dict] | None = None,
             access_mode: str = capability_sandbox.ACCESS_APPROVAL,
             lease_id: str = "", subject: str = "",
             output_dir: str = "", repo_id: str = "",
             configured_models: set[str] | frozenset[str] = frozenset(),
             chat_base: str = "", chat_key: str = "", chat_model: str = "",
             chat_fn: Callable | None = None, structured_chat_fn: Callable | None = None,
             resume: dict | None = None,
             images: list[str] | None = None,
             chat_messages_fn: Callable | None = None,
             temperature: float = 0.2, proxy_kwargs: dict | None = None,
             max_steps: int = 48, trace: Callable | None = None,
             system_extra: str = "",
             work_brief: str = "",
             search_proxy: str = "",
             checkpoint_fn: Callable | None = None) -> FabricOutcome:
    """自由循环。返回 FabricOutcome。images 非空时走多模态消息通道。

    system_extra（2026-09-06）：调用方按任务注入的执行纪律（如大 txt 附件禁止整本
    read_text），进 system 提示而非 history——模型每步决策都可见，遵循度更高。
    search_proxy（2026-09-10）：本应用的「联网代理」（RunContext.proxy_url），环境归一
    注入给 web.search_materials（模型不得自选代理）；空 = 直连。与 proxy_kwargs 的
    聊天代理分开——两者在设置里是不同字段。
    """
    caps = capabilities or capability_registry.with_availability(configured_models)
    images = [u for u in (images or []) if str(u).strip()]
    system = (_SYSTEM.replace("__MANIFEST__", _manifest_lines(caps))
              .replace("__OUTPUT_DIR__", output_dir or "（未指定）")
              .replace("__INTENT__", intent))
    if str(system_extra or "").strip():
        system += "\n\n" + str(system_extra).strip()
    if str(work_brief or "").strip():
        # 2026-09-07 重发的资本：前序产出档案进 system（每步决策可见）——
        # 模型开局即知已有卡纲/素材/条目/主卡，在其上继续，禁止重读重建
        system += "\n\n" + str(work_brief).strip()
    if images:
        system += (f"\n【附图】本轮随消息附带 {len(images)} 张图片（在首条用户消息里），"
                   "需要看图的任务（如反推外貌特征）直接观察图片内容，不要声称看不到图。")
    messages: list[dict] = [{"role": "system", "content": system}]
    # ⚠前缀缓存铁律（2026-09-12 P1）：`messages[0]` 是**逐字节静态**的请求头，任何动态内容
    #（进度卡/时间戳）都不得写回这里——写回一次，本 run 之后每一步的请求前缀从头就变了，
    # 上游缓存全部失效（量化见 docs/memory/prompt-cache-design-2026-09-12.md §三）。
    # 动态内容一律走尾部临时消息（`_progress_message`），不进 `messages`。
    if images:
        # 多模态首条消息：图片必须以 image_url 内容块直达模型，不能进 JSON 字符串协议
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": history or "（用户目标见系统提示，请结合所附图片完成。）",
        }]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        messages.append({"role": "user", "content": content})
    elif history:
        messages.append({"role": "user", "content": history})
    outcome = FabricOutcome()
    # 2026-09-09：记录本 run 开始时间——产物卡只收本轮实际写入的产物（collect since 过滤），
    # 避免上一轮/历史作品的产物串到本轮（实锤：御仙任务显示玫瑰与繁花）。
    setattr(outcome, "_started_at", time.time())
    call_kwargs: dict[str, Any] = {"temperature": temperature, "max_tokens": 16000,
                                   **(proxy_kwargs or {}), "on_usage": _usage_sink(trace, chat_model)}
    call_args = (chat_base, chat_key, chat_model, system)
    active_lease = lease_id

    # 断点续跑（2026-09-06 approval 自由循环）：resume 带上次 awaiting_approval 的
    # messages（模型决策对话历史）与 steps（已完成轨迹），从该状态继续循环，
    # 不重跑已完成步骤；首次调用 resume=None 走正常初始化。
    if resume is not None:
        messages = list(resume.get("messages") or messages)
        outcome.steps = list(resume.get("steps") or [])

    _step_base = len(outcome.steps)
    for step_index in range(1, max_steps + 1):
        # 任务进度卡（2026-09-06 治本）：每步决策前把「已完成步骤 + 当前目标 + 范围锁定」
        # 给模型看——长期自由循环几十步后容易迷失任务（实锤：固化02 转卡中途被历史生图
        # 带偏去 load 固化01/list_templates/lora.resolve）。
        # 2026-09-12 P1：内容不变，位置改为**尾部临时消息**（见 _progress_message 注释）。
        decision: FabricDecision | None = None
        progress_msg = _progress_message(intent, outcome.steps)
        # 请求体：真实对话（不含第 0 条 system，system 由独立的 system 通道承载——
        # 此前 dump 里再带一份 = system 每步双投喂，纯浪费）+ 尾部动态进度卡。
        # 2026-09-12 P4：先投影（应用压缩替换声明、摘掉声明条目），发的是表面视图；
        # `messages` 原始日志保持 append-only。
        wire_messages = _project_messages(messages)[1:] + [progress_msg]
        try:
            if images:
                # 带图：structured 原生通道传 JSON 字符串载不动图片，直接走多消息文本通道
                sender = chat_messages_fn if callable(chat_messages_fn) else _chat_messages
                # 多消息通道里 messages[0] 就是真 system（不经 dump），故整表 + 尾部进度卡
                result = structured_output.validate_text(
                    sender(chat_base, chat_key, chat_model,
                           _project_messages(messages) + [progress_msg],
                           **call_kwargs),
                    FabricDecision, trace=trace,
                )
            else:
                result = structured_output.invoke(
                    FabricDecision,
                    native=(lambda u=json.dumps(wire_messages, ensure_ascii=False):
                            structured_chat_fn(*call_args[:4], u, schema=FabricDecision, **call_kwargs))
                    if callable(structured_chat_fn) else None,
                    legacy=lambda u=json.dumps(wire_messages, ensure_ascii=False):
                        chat_fn(*call_args[:4], u, **call_kwargs),
                    trace=trace,
                )
            decision = result.value
            if trace is not None:
                # 2026-09-06：决策思考推给前端（detail=thinking，执行过程「思考」面板可见）
                trace("model.request", thinking=(decision.thinking or "").strip()[:400])
        except Exception as exc:  # noqa: BLE001 - 模型决策失败：截断/解析失败重试一次
            # 2026-09-06 实锤：长上下文（小说全文进 messages）下模型决策 JSON 输出
            # 被网关截断，直接判 error 会让整轮白跑。重试一次，回灌「紧凑输出」提示。
            outcome.error = f"第 {step_index} 步模型决策失败：{exc}"
            if trace is not None:
                trace("fabric.retry", step=step_index, reason=str(exc)[:120])
            # 2026-09-06 混合模式核心：云端被网关 400（内容审核）时，先自动降级
            # 用本地模型生成决策（无审核）——本地成功则回合继续，云端失败不白跑。
            decision = _local_decision_call(_project_messages(messages) + [progress_msg])
            if decision is None:
                # 本地不可用/失败 → 云端紧凑重试（旧逻辑兜底）
                retry_user = json.dumps(wire_messages, ensure_ascii=False)
                retry_sys = ("你的上一条决策输出未通过 JSON 解析（可能被网关截断）。"
                             "请重新只输出一个紧凑 JSON 决策对象：{\"tool\": \"...\", \"params\": {...}} "
                             "或 {\"done\": true, \"reply\": \"...\"}。不要输出解释文字、不要美化换行"
                             "或缩进，禁止重复之前已完成的操作。")
                try:
                    retry_result = structured_output.invoke(
                        FabricDecision,
                        legacy=lambda u=retry_user: chat_fn(
                            chat_base, chat_key, chat_model, retry_sys, u, **call_kwargs),
                        trace=trace,
                    )
                    decision = retry_result.value
                except Exception as exc2:  # noqa: BLE001 - 重试仍败如实返回
                    outcome.status = "error"
                    outcome.error = f"第 {step_index} 步模型决策失败（重试仍败）：{exc2}"
                    outcome.messages = messages
                    return outcome

        if decision.done:
            # 2026-09-07 完成前验证（代码层强制，防模型跳验收/内嵌）：
            # 合集卡交付任务写了 worldbook/character 条目但没跑 check_density → 拒绝 done，
            # 回填验收要求；跑了 check_density 但没 B 态内嵌（character.upsert_repo）→ 提示内嵌。
            _ops = {str(s.get("tool") or "") for s in outcome.steps}
            _wrote_entries = bool(_ops & {"worldbook.upsert_repo", "character.upsert_repo"})
            # 2026-09-08 治本：B 态内嵌自动兜底——模型调了 character.upsert_repo 但传的
            # card 常常漏 character_book（13:09 实锤：card 只有 name/description，
            # has_worldbook=false）。这里 done 前自动读 worldbook 快照，把全部条目
            # 内嵌进主卡 card.character_book.entries 再落盘，不再依赖模型正确传参。
            if _wrote_entries and "worldbook.upsert_repo" in _ops and output_dir and repo_id:
                try:
                    from app.services import character_card, character_store, worldbook_store
                    _snap = worldbook_store.read_repo_snapshot(output_dir, repo_id) or {}
                    _wb_entries = list(_snap.get("entries") or [])
                    if _wb_entries:
                        import json as _json
                        from pathlib import Path as _Path
                        # 通用：扫描作品根下所有 card.json，把世界书条目内嵌进每个主卡
                        _card_files = list(_Path(output_dir).glob("*/card.json"))
                        for _card_file in _card_files:
                            try:
                                _existing = _json.loads(_card_file.read_text(encoding="utf-8"))
                            except (OSError, ValueError):
                                continue
                            _cbook = dict(_existing.get("character_book") or {})
                            _emb = list(_cbook.get("entries") or [])
                            if len(_emb) < len(_wb_entries):
                                _cbook["entries"] = _wb_entries
                                _existing["character_book"] = _cbook
                                _norm = character_card.normalize_card(_existing)
                                character_store.save_card(output_dir, _norm, overwrite=True)
                                if trace is not None:
                                    trace("tool.result", operation="auto_embed",
                                          ok=True, result=f"自动内嵌 {len(_wb_entries)} 条世界书条目进主卡")
                except Exception:  # noqa: BLE001 - 内嵌兜底失败不阻断 done
                    pass
            # 2026-09-09 条目数闸门（固化02 §4 下限 40 + 全项目 18 卡实测）：
            # check_density 只查「每条字数」不查「条目数」——26 条全达标也能 done（玫瑰与繁花实锤）。
            # 写了条目的合集卡任务，快照条目数 <40 一律拒绝 done，自动回填「继续补写」指令，
            # 循环内继续跑（这就是 harness 自动化：未达标不结束，自动再次发起补写请求）。
            if _wrote_entries and output_dir and repo_id:
                try:
                    from app.services import worldbook_store as _wbs
                    _snap_n = len(list((_wbs.read_repo_snapshot(output_dir, repo_id) or {}).get("entries") or []))
                except Exception:  # noqa: BLE001 - 读不到快照不拦 done（已跑的验收兜底）
                    _snap_n = 0
                if 0 < _snap_n < 40:
                    _deny_count = (
                        f"条目数闸门：当前世界书快照 {_snap_n} 条，未达合集卡下限 40 条"
                        "（固化02 §4 + 18 张卡实测：系统判定 8 条骨架 + 编号层 11-31 条 + 角色层 3-19 条）。"
                        "禁止宣布完成，自动继续补写：按编号层骨架补【1. 地理·主舞台/敌对地带 → 势力与种族 → "
                        "体系/规则 → 大事件·命定时间线总表 → 各节点大事件（按时间/人物/地点拆条，"
                        "每条 ≥600 字）→ <作品>·角色速览表】，再补角色层（每可攻略角色 1 条 ≥1800 字，"
                        "主角用【主角定位】）与 NSFW· 独立层（8-14 条 §3.6b，每条 ≥400 字，"
                        "必须带【边界】仅限成年声明）。先列出条目清单（按类型统计现有+缺口）"
                        "再按清单补写到 ≥40 条，随后密度验收。")
                    messages.append({"role": "assistant", "content": json.dumps(
                        {"done": True, "reply": decision.reply.strip()}, ensure_ascii=False)})
                    messages.append({"role": "user", "content": json.dumps(
                        {"tool_error": _deny_count}, ensure_ascii=False)})
                    continue
            # 2026-09-09 密度闸门 + 豁免（用户定案：90% 线 + 3% 低收益）：
            # 机械读快照按类型分类查字数（角色 1800 / 机制 800 / 编号 600 / NSFW 400），
            # 取代「必须调用 check_density」的软约束——不达标且未豁免的条目列出清单拒绝 done，
            # 模型按清单补写；「连续两轮增量 < 下限3% 且 ≥ 下限90%」的条目加入豁免名单，
            # 不再进入下轮补写。全部达标或豁免才放行。
            _stats = getattr(outcome, "_density_stats", None)
            if _stats is None:
                _stats = {}
                outcome._density_stats = _stats
            if _wrote_entries and output_dir and repo_id:
                try:
                    import re as _re
                    from app.services import worldbook_store as _wbs3
                    _snap_all = list((_wbs3.read_repo_snapshot(output_dir, repo_id) or {}).get("entries") or [])
                    _LIMITS = {"角色": 1800, "机制": 800, "背景/事件": 600, "NSFW": 400}

                    def _kind_of(c: str) -> str:
                        if c.startswith("角色卡·"):
                            return "角色"
                        if c.startswith(("系统判定机制·", "全局机制·", "局部机制·")):
                            return "机制"
                        if c.startswith("NSFW·"):
                            return "NSFW"
                        if c.startswith("世界背景·") or bool(_re.match(r"^\d+\.\s*", c)):
                            return "背景/事件"
                        return ""

                    def _norm_c(c: str) -> str:
                        return _re.sub(r"[\s·、。，．,．:：()（）\[\]【】\-—/\\]+", "", c or "")

                    _deny_list: list[dict] = []
                    _exempt_list: list[str] = []
                    for _e in _snap_all:
                        if not isinstance(_e, dict):
                            continue
                        _c = str(_e.get("comment") or "")
                        _k = _kind_of(_c)
                        if not _k:
                            continue
                        _min = _LIMITS[_k]
                        _ln = len(str(_e.get("content") or ""))
                        if _ln >= _min:
                            continue
                        _nk = _norm_c(_c)
                        _hist = _stats.setdefault(_nk, [])
                        _hist.append(_ln)
                        # 豁免：≥3 次采样且最近两次增量均 < 下限3%，且当前 ≥ 下限90%
                        _exempt = False
                        if len(_hist) >= 3 and _ln >= _min * 0.9:
                            _g1 = _hist[-1] - _hist[-2]
                            _g2 = _hist[-2] - _hist[-3]
                            if _g1 < _min * 0.03 and _g2 < _min * 0.03:
                                _exempt = True
                        if _exempt:
                            _exempt_list.append(f"{_c}（{_ln}字，≥{_min}的90%={int(_min*0.9)}且两轮增量<{int(_min*0.03)}，豁免）")
                        else:
                            _deny_list.append({"comment": _c, "chars": _ln, "min": _min, "kind": _k})
                    if _deny_list:
                        _lines = "\n".join(
                            f"· {d['comment']}：{d['chars']}字 < {d['min']}字（{d['kind']}，缺口 {d['min']-d['chars']} 字）"
                            for d in _deny_list[:15])
                        _exempt_note = ("\n以下条目已豁免（连续两轮增量<下限3% 且已≥下限90%），无需再补："
                                        + "\n".join(_exempt_list[:10])) if _exempt_list else ""
                        _deny_density = (
                            f"密度闸门：{len(_deny_list)} 条未达标（角色 ≥1800 / 机制 ≥800 / 编号 ≥600 / NSFW ≥400）：\n"
                            f"{_lines}\n请按清单逐条补写（从正文/素材提取内容丰富，不要凭空编造）；"
                            f"已到 90% 线且两轮无收益的条目会豁免，不要死磕。{_exempt_note}")
                        messages.append({"role": "assistant", "content": json.dumps(
                            {"done": True, "reply": decision.reply.strip()}, ensure_ascii=False)})
                        messages.append({"role": "user", "content": json.dumps(
                            {"tool_error": _deny_density}, ensure_ascii=False)})
                        continue
                except Exception:  # noqa: BLE001 - 机械密度检查失败不阻断 done（check_density 工具兜底）
                    pass
            # 2026-09-09 清单落实 + 重复检查闸门（用户定案：流程本身要避免「待写」与同主题拆条）：
            # ① 清单核对：docs/条目清单-*.md 列出的条目名必须都落盘（缺失=待写未完成 → 拒绝 done）；
            # ② 重复检查：comment 归一重复 / 内容高度互相包含（同主题拆条残留 → 提示合并）。
            if _wrote_entries and output_dir and repo_id:
                try:
                    import re as _re4
                    from pathlib import Path as _Path4
                    from app.services import worldbook_store as _wbs4
                    _snap_e = [e for e in ((_wbs4.read_repo_snapshot(output_dir, repo_id) or {}).get("entries") or [])
                               if isinstance(e, dict)]
                    _comments = [str(e.get("comment") or "").strip() for e in _snap_e]

                    def _nc4(x: str) -> str:
                        return _re4.sub(r"[\s·、。，．,．:：()（）\[\]【】\-—/\\]+", "", x or "")

                    _nc_set = {_nc4(c) for c in _comments if c}
                    # ① 清单核对（清单里列了但快照没有 → 待写未落实）
                    _pending: list[str] = []
                    for _doc in sorted(_Path4(output_dir).glob("docs/条目清单-*.md")):
                        try:
                            _txt = _doc.read_text(encoding="utf-8")
                        except OSError:
                            continue
                        for _line in _txt.splitlines():
                            _m = _re4.match(r"^\s*(?:\d+[.、)]|[-*])\s*(.+?)\s*(?:——|—|--|$)", _line)
                            if not _m:
                                continue
                            _name = _m.group(1).strip().strip("*` ")
                            if not _name or len(_name) < 4:
                                continue
                            _nn = _nc4(_name)
                            if not _nn:
                                continue
                            if not any(_nn in _x or _x in _nn for _x in _nc_set):
                                _pending.append(_name)
                    if _pending:
                        _deny_pending = (
                            "清单落实闸门：条目清单里以下条目尚未落盘（待写未完成）：\n"
                            + "\n".join(f"· {x}" for x in _pending[:15])
                            + "\n请把它们写入（或从清单中删除确实不做的条目）后再宣布完成——"
                              "禁止清单留「待写」就收尾。")
                        messages.append({"role": "assistant", "content": json.dumps(
                            {"done": True, "reply": decision.reply.strip()}, ensure_ascii=False)})
                        messages.append({"role": "user", "content": json.dumps(
                            {"tool_error": _deny_pending}, ensure_ascii=False)})
                        continue
                    # ② 重复 / 同主题拆条检查
                    _dups: list[str] = []
                    for _i in range(len(_comments)):
                        for _j in range(_i + 1, len(_comments)):
                            _a, _b = _comments[_i], _comments[_j]
                            if not _a or not _b:
                                continue
                            if _nc4(_a) == _nc4(_b):
                                _dups.append(f"「{_a}」与「{_b}」comment 完全重复")
                                continue
                            _ca = str(_snap_e[_i].get("content") or "")
                            _cb = str(_snap_e[_j].get("content") or "")
                            if len(_ca) > 200 and len(_cb) > 200:
                                _short, _long = (_ca, _cb) if len(_ca) <= len(_cb) else (_cb, _ca)
                                if len(_short) >= len(_long) * 0.8 and _short[:150] in _long:
                                    _dups.append(
                                        f"「{_a}」与「{_b}」内容高度重复（短版是长版的子集）")
                    if _dups:
                        _deny_dup = (
                            "重复/同主题拆条闸门：\n" + "\n".join(f"· {x}" for x in _dups[:10])
                            + "\n请合并为一条（固化02 §3.2 相关性融合优先：同一主体一条，"
                              "如药剂/道具/体质类不拆成多条），或用 upsert 更新保留一条、删除其余，再宣布完成。")
                        messages.append({"role": "assistant", "content": json.dumps(
                            {"done": True, "reply": decision.reply.strip()}, ensure_ascii=False)})
                        messages.append({"role": "user", "content": json.dumps(
                            {"tool_error": _deny_dup}, ensure_ascii=False)})
                        continue
                except Exception:  # noqa: BLE001 - 机械核对失败不阻断 done
                    pass
            if _wrote_entries and "character.upsert_repo" not in _ops:
                # 2026-09-08 治本：B 态是用户定案的硬性交付，从「提示一次放行」改为「强制内嵌」——
                # 写了 worldbook 条目却没 character.upsert_repo 内嵌主卡就拒绝 done，直到内嵌完成
                # （12:44 实锤：模型 done 但 card.json character_book=null，交付物不合格）。
                _deny_embed = ("B 态内嵌强制：已写 worldbook 条目但主卡还没内嵌，禁止宣布完成。"
                               "请调用 character.upsert_repo 把全部条目内嵌进主卡"
                               "（card.data.character_book.entries = 全部条目 list，card.json 约 240KB，"
                               "first_mes 非空）后再宣布完成；A 态（多卡+世界书快照）可跳过，但单卡合集必须内嵌。")
                messages.append({"role": "assistant", "content": json.dumps(
                    {"done": True, "reply": decision.reply.strip()}, ensure_ascii=False)})
                messages.append({"role": "user", "content": json.dumps(
                    {"tool_error": _deny_embed}, ensure_ascii=False)})
                continue
            outcome.status = "done"
            outcome.reply = decision.reply.strip() or "已完成。"
            outcome.messages = messages
            return outcome
        operation = (decision.tool or "").strip()
        if not operation:
            if (decision.reply or "").strip():
                # 2026-09-06 B 方案：没调工具但给了最终回复 → 视为完成。
                # 兜底防上下文爆炸下模型决策退化时整轮白跑（第 15 步 error 实锤）。
                outcome.status = "done"
                outcome.reply = decision.reply.strip()
                outcome.messages = messages
                return outcome
            # 2026-09-07 实锤：长上下文下模型输出空决策（tool/done/reply 全空，
            # 第 19 步 error）。不直接白跑——重试一次，提示必须给 tool 或 done。
            if trace is not None:
                trace("fabric.retry", step=step_index, reason="空决策：既没调工具也没宣布完成")
            try:
                empty_retry = structured_output.invoke(
                    FabricDecision,
                    legacy=lambda u=json.dumps(wire_messages, ensure_ascii=False): chat_fn(
                        chat_base, chat_key, chat_model,
                        ("你上一条输出是空决策（tool/done/reply 全空），无效。必须输出一个有效决策："
                         + "要么调用工具（给出 tool 与 params），要么宣布完成（给出 done 与 reply）。"
                         + "不要空输出。"),
                        u, **call_kwargs),
                    trace=trace,
                )
                decision = empty_retry.value
                operation = (decision.tool or "").strip()
                if not operation and (decision.reply or "").strip():
                    outcome.status = "done"
                    outcome.reply = decision.reply.strip()
                    outcome.messages = messages
                    return outcome
                if not operation:
                    outcome.status = "error"
                    outcome.error = f"第 {step_index} 步既没调用工具也没宣布完成（重试仍空）"
                    return outcome
            except Exception as exc2:  # noqa: BLE001
                outcome.status = "error"
                outcome.error = f"第 {step_index} 步既没调用工具也没宣布完成（重试失败：{exc2}）"
                outcome.messages = messages
                return outcome

        params = decision.params or {}
        step_record = {"tool": operation, "params": params, "ok": False, "result": None}
        # 审批/沙盒闸门（工具执行前拦截）
        cap = capability_registry.get(operation)
        if cap is None:
            step_record["error"] = f"能力清单里没有「{operation}」"
            outcome.steps.append(step_record)
            messages.append({"role": "assistant", "content": json.dumps(
                {"tool": operation, "params": params}, ensure_ascii=False)})
            messages.append({"role": "user", "content": json.dumps(
                {"tool_result": step_record["error"]}, ensure_ascii=False)})
            continue
        level = cap.side_effect_level
        if level in ("durable", "expensive"):
            try:
                capability_sandbox.authorize(active_lease, operation, path=output_dir)
            except PermissionError:
                outcome.status = "awaiting_approval"
                outcome.pending_tool = operation
                outcome.error = f"工具 {operation} 需要批准"
                outcome.messages = messages
                return outcome
        # 2026-09-06 用户定案混合模式：云端自然输出 → 内容含敏感词（会被网关 400）时，
        # **不是机械拦截拒绝**，而是把该部分**交给本地模型改写**（本地无审核，能坦然处理
        # NSFW 设定转写）→ 改写干净则放行落盘（云端跳过这部分、继续下一步）；
        # 本地模型不可用/改写失败才回填提示让云端重写。
        if operation in _NSFW_BLOCK_CAPABILITIES:
            blocked = _nsfw_blocked_params(params)
            if blocked:
                rewritten = _local_rewrite_params(params)
                if rewritten is not None:
                    params = rewritten  # 本地改写干净 → 用改写后的内容执行
                    if trace is not None:
                        trace("tool.result", operation=operation, ok=True,
                              result="敏感内容已交本地模型改写为设定化条目")
                else:
                    step_record["error"] = blocked
                    outcome.steps.append(step_record)
                    messages.append({"role": "assistant", "content": json.dumps(
                        {"tool": operation, "params": params}, ensure_ascii=False)})
                    messages.append({"role": "user", "content": json.dumps(
                        {"tool_error": blocked}, ensure_ascii=False)})
                    continue
        # 配方重放的落盘域同样环境归一（handler 内还有配置真源等值校验兜底）
        if operation == "plan.instantiate_recipe" and not str(params.get("output_dir") or "").strip():
            params["output_dir"] = output_dir or ""
        # 落盘/执行类参数一律环境归一，**不是「缺省才注入」**（2026-09-07 治本）：
        # 实锤模型把作品名/卡目录当 repo_id 传（base=pictures\玫瑰与繁花、
        # repo_id=玫瑰与繁花），缺省才兜底会让新条目写到 <作品名>/worldbook.json
        # 与真实快照 <repo_id>/worldbook.json 分叉。清单单一属主 =
        # capability_registry.ENV_INJECTED_OPS（含 base/repo_id/cwd/output_dir）：
        # - base = output_dir（作品根，doc 落 docs/、卡落 <卡名>/、书落 <repo_id>/）
        # - repo_id = 运行会话的 repo_id（worldbook 快照按它建子目录）
        # - cwd = output_dir（shell 工作区，模型传 / 等越域值会被路径域校验拦死）
        # - search_proxy = 本应用的联网代理（web.search_materials 用；空=直连）
        # 2026-09-09 治本：新增能力只改注册表一处，不再逐处漏加
        # （character.embed_worldbook / workspace.export_to_library 漏加实锤）。
        capability_registry.inject_env_params(
            params, operation, output_dir=output_dir or "", repo_id=repo_id or "",
            search_proxy=search_proxy or "",
            chat_base=chat_base or "", chat_key=chat_key or "",
            chat_model=chat_model or "")
        # 2026-09-07 治本（用户定案「按原文关键词扩写，防偏差」）：素材已读门禁——
        # 实锤：模型反复凭记忆重写短版（22:24 一轮跑完零 read_text，戴茂 949/1424/1453
        # 全不到 2200），指令层纪律拦不住。代码层强制：worldbook.upsert_repo 前
        # 检查本 run 是否读过对应 _prep/charfacts 素材；没读 → 拒绝并回填「先读素材」，
        # 让模型带着原文细节去写，而不是凭空重写。
        if operation == "worldbook.upsert_repo":
            _read_paths = {
                str((s.get("params") or {}).get("path") or "")
                for s in outcome.steps
                if s.get("tool") == "file.read_text"
                and isinstance(s.get("params"), dict)
            }
            _read_any_charfacts = any(
                "_prep" in p and "charfacts" in p for p in _read_paths if p)
            _entries_list = params.get("entries") or []
            if not isinstance(_entries_list, list):
                step_record["error"] = (
                    "worldbook.upsert_repo 的 entries 必须是条目数组（list），"
                    "当前收到字符串。请重新构造 entries：每条是一个 dict，"
                    "含 comment/content/keys 字段。")
                outcome.steps.append(step_record)
                messages.append({"role": "assistant", "content": json.dumps(
                    {"tool": operation, "params": params}, ensure_ascii=False)})
                messages.append({"role": "user", "content": json.dumps(
                    {"tool_error": step_record["error"]}, ensure_ascii=False)})
                continue
            # 2026-09-09 清单先行门禁（方向优先，用户定案）：首次 worldbook.upsert_repo 前
            # 必须已用 doc.create_repo 产出「条目清单/卡纲/转写计划」类文档并展示确认——
            # 先列完整目标条目清单（六层全列 + 状态：保留/修订/新增）再写，禁止边写边发明
            # 条目（防重复、保条目间联动一致）。检查本 run 步骤里是否已写过该类文档。
            _list_done = any(
                (s.get("tool") == "doc.create_repo"
                 and any(_kw in str((s.get("params") or {}).get("rel_path") or "")
                         for _kw in ("条目清单", "卡纲", "转写计划", "目标方案")))
                for s in outcome.steps)
            if not _list_done:
                step_record["error"] = (
                    "清单先行门禁：写条目前必须先用 doc.create_repo 产出完整目标条目清单"
                    "（docs/条目清单-<作品名>.md，按系统判定机制/全局机制/编号层（地理·势力·体系·"
                    "大事件·速览表）/局部机制/角色卡/NSFW 六层列出全部条目名，每条标注状态："
                    "保留/修订/新增），并在回复中展示请用户确认。先列清单、确认后再写条目——"
                    "禁止边写边发明条目（防重复、保联动一致）。")
                outcome.steps.append(step_record)
                messages.append({"role": "assistant", "content": json.dumps(
                    {"tool": operation, "params": params}, ensure_ascii=False)})
                messages.append({"role": "user", "content": json.dumps(
                    {"tool_error": step_record["error"]}, ensure_ascii=False)})
                continue
            if not _read_any_charfacts and _entries_list:
                _need_material = False
                for _ent in _entries_list:
                    if not isinstance(_ent, dict):
                        continue
                    _c = str(_ent.get("comment") or "")
                    if (_c.startswith("角色卡·") or _c.startswith("NSFW·")
                            or _c.startswith(("系统判定机制·", "全局机制·", "局部机制·"))
                            or _c.startswith("世界背景·")):
                        _need_material = True
                        break
                if _need_material:
                    step_record["error"] = (
                        "素材已读门禁拒绝本次写入：本 run 尚未读取任何 "
                        "_prep/charfacts 素材（无法从原文提取细节）。"
                        "先 file.read_text 读对应素材段（如 _prep/charfacts/<角色或主题名>.txt，"
                        "分卷 offset 递增直到读完），从原文提取具体事件/对话/关系/外貌/"
                        "机制细节后再 upsert；禁止凭记忆重写。")
                    outcome.steps.append(step_record)
                    messages.append({"role": "assistant", "content": json.dumps(
                        {"tool": operation, "params": params}, ensure_ascii=False)})
                    messages.append({"role": "user", "content": json.dumps(
                        {"tool_error": step_record["error"]}, ensure_ascii=False)})
                    continue
        # 2026-09-09 已读段缓存：file.read_text 请求区间完全落在已读区间内 → 拦截
        # 重复完整读取（循环补写时模型常重读同一段，白烧 token）。命中返回提示 + 该段
        # 首行摘要，模型基于已有内容写或缩小 max_chars/offset 定位；读全文/新段不受影响。
        if operation == "file.read_text":
            _cache = getattr(outcome, "_read_cache", None)
            if _cache is None:
                _cache = {}
                setattr(outcome, "_read_cache", _cache)
            _rp = str((params or {}).get("path") or "")
            try:
                _off = int((params or {}).get("offset") or 0)
                _mc = int((params or {}).get("max_chars") or 20000)
            except (TypeError, ValueError):
                _off, _mc = 0, 20000
            _ent = _cache.get(_rp)
            if _rp and _ent:
                for _s, _e, _step, _head in _ent:
                    if _s <= _off and (_off + _mc) <= _e:
                        step_record["error"] = (
                            f"已读缓存：该段（offset={_off}，约 {_mc} 字符）已在第 {_step} 步完整读入"
                            f"（首行：{_head[:60]}）。禁止重复完整读取以节约 token——基于上下文已有内容写；"
                            f"确需原文细节请缩小 max_chars（≤3000）或调整 offset 定位，不要整段重读。")
                        outcome.steps.append(step_record)
                        messages.append({"role": "assistant", "content": json.dumps(
                            {"tool": operation, "params": params}, ensure_ascii=False)})
                        messages.append({"role": "user", "content": json.dumps(
                            {"tool_error": step_record["error"]}, ensure_ascii=False)})
                        continue
        if trace is not None:
            trace("tool.call", operation=operation, params=params)
        try:
            result_payload = _dispatch(operation, params)
            step_record["ok"] = True
            step_record["result"] = result_payload
            result_text = json.dumps(result_payload, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 - 单步失败回填给模型，让它换方案
            step_record["error"] = str(exc)
            result_text = json.dumps({"tool_error": str(exc)}, ensure_ascii=False)
        # 记录已读区间（读成功才记；合并连续区间，拦截覆盖更准）
        if operation == "file.read_text" and step_record.get("ok"):
            _cache = getattr(outcome, "_read_cache", None)
            if _cache is None:
                _cache = {}
                setattr(outcome, "_read_cache", _cache)
            _rp2 = str((params or {}).get("path") or "")
            try:
                _off2 = int((params or {}).get("offset") or 0)
                _mc2 = int((params or {}).get("max_chars") or 20000)
            except (TypeError, ValueError):
                _off2, _mc2 = 0, 20000
            _txt2 = str((result_payload or {}).get("text") or "")
            _cache.setdefault(_rp2, [])
            _cache[_rp2] = _merge_read_intervals(
                _cache[_rp2] + [(_off2, _off2 + _mc2, step_index, _txt2[:80])])
        if trace is not None:
            trace("tool.result", operation=operation, ok=step_record["ok"],
                  result=step_record.get("result") or step_record.get("error"))
        outcome.steps.append(step_record)
        messages.append({"role": "assistant", "content": json.dumps(
            {"tool": operation, "params": params}, ensure_ascii=False)})
        messages.append({"role": "user", "content": json.dumps(
            {"tool_result": result_text}, ensure_ascii=False)})
        _compress_read_chunks(messages)
        _compress_upsert_results(messages)
        # 2026-09-07 重试的资本：每步保存断点（status=running），中断后可从这恢复
        if checkpoint_fn is not None:
            try:
                checkpoint_fn(step_index, messages, outcome.steps, "running")
            except Exception:  # noqa: BLE001 - 保存失败不阻断循环
                pass

    outcome.status = "step_limit"
    outcome.error = f"达到最大步数 {max_steps}，仍未宣布完成"
    outcome.messages = messages
    return outcome
