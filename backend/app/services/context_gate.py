"""上下文利用判断机制（Context Relevance Gate）：按当前任务意图域给历史消息分档注入。

背景（2026-09-06 驱动测试实锤）：历史消息被全量无脑平铺进自由循环/监督节点的
上下文——「无脑等效注入」。20 多条生图历史在篇幅上淹没当前「小说转合集卡」指令，
模型注意力被带偏，把合集卡任务编排出生图步骤。每条历史权重相同，当前指令没有
更高优先级，这正是那次误会的上下文侧根因（能力白名单是另一侧防线）。

本模块是按需利用的判断机制：根据当前任务的意图域，给每条历史消息分档——
  keep    同域/高相关：保留全文（当前指令永远是 keep，最高锚点）；
  summary 跨域（此前做过其他类型任务）：按跨域标签聚合为一行主题摘要（P1）；
  clip    通用对话：截断保留两端（可能承载已确认的约束，不整条丢弃）；
输出为显式分层注入（当前指令锚点 → 同域 → 跨域聚合 → 截断），让模型知道权重差异。

设计约束：
- 判断用轻量规则（意图词表 + 域匹配），不做 LLM 判定——零成本、可预测；
- P3①（2026-09-06）：词表分档之上可叠加**语义相关度增强**——调用方可注入 embed_fn
  （批量文本→向量），与当前指令余弦相似度达标的历史升级 keep（只升不降）；
  嵌入失败/未注入一律静默退回词表结果，语义是增强不是依赖（不预埋硬依赖）；
- 保守策略是降权压缩而非硬删除：信息主体不丢，只是不再淹没当前指令；
- 意图域未识别（如短指令「继续」）时退回原全量拼接，绝不误伤上下文依赖。
"""
from __future__ import annotations

import math
import re
from typing import Any

from app.services import plan_compiler as _pc

# 各意图域的特征词（粗粒度即可——只用于把「同域历史」与「跨域历史」分开）
_CARD_RE = re.compile(
    r"合集卡|角色卡|世界书|内嵌世界书|小说|世界观|机制|势力|地理|重大事件|"
    r"剧情|角色|ST迁移|SillyTavern|移民|移植|固化|规范|说明书"
)
_IMAGE_RE = re.compile(
    r"生图|出图|生成图片|图片|图像|模板|穿搭|构图|画风|姿势|LoRA|lora|皮肤|"
    r"图集|预览|渲染"
)
_DOC_RE = re.compile(r"文档|手册|汇总|归档|报告|整理成|导出")

_DOMAIN_LABEL = {"card": "合集卡/文档交付", "image": "生图", "doc": "文档"}

# 分档类型
KEEP = "keep"
SUMMARY = "summary"
CLIP = "clip"

# P3① 语义增强（2026-09-06）：与当前指令余弦相似度 ≥ 此值的历史升级 keep（只升不降）。
# 阈值经真实 qwen3-embedding 分布校准（M1 审计 #6，本地 ollama 实测 12 条场景样本）：
#   相关设定讨论 0.42~0.71 / 跨域生图 0.31~0.48 / 无关闲聊 0.32~0.44
# 取 0.55：词表漏网的相关设定（≥0.58）能升入，跨域/无关（≤0.48）进不来（间隔 ≥0.07）；
# 词表仍是主防线，语义只兜底——误升级（淹没指令）比漏升级代价高，宁可偏保守。
SEMANTIC_KEEP_THRESHOLD = 0.55
_SEMANTIC_MAX_ITEMS = 32  # 单次语义分档最多嵌入的历史条数（本地嵌入逐条 HTTP，防拖慢装配）


def domain_of(text: str) -> str:
    """意图文本 → 意图域（card / image / doc / ""）。空=通用对话，不强分档。

    卡/文档交付（is_doc_delegation_intent 覆盖两者）用卡词表细分为 card/doc；
    混合指令（如「生成一份文档，整理全局机制」）因命中卡词表归 card——
    卡与文档同为结构化交付域，分档效果等价，不追求精确区分。
    """
    source = (text or "").strip()
    if not source:
        return ""
    if _pc.is_doc_delegation_intent(source):
        return "card" if _CARD_RE.search(source) else "doc"
    if _CARD_RE.search(source):
        return "card"
    if _IMAGE_RE.search(source):
        return "image"
    if _DOC_RE.search(source):
        return "doc"
    return ""


def _domain_hit(text: str, domain: str) -> bool:
    if domain == "card":
        return _CARD_RE.search(text) is not None
    if domain == "image":
        return _IMAGE_RE.search(text) is not None
    if domain == "doc":
        return _DOC_RE.search(text) is not None
    return False


def _cross_domain_label(text: str, domain: str) -> str:
    """消息命中「非当前域」的其他域特征时，返回它自己的域标签（压缩时标注）。"""
    if domain != "image" and _IMAGE_RE.search(text):
        return "生图"
    if domain != "card" and _CARD_RE.search(text):
        return "合集卡/文档"
    if domain != "doc" and _DOC_RE.search(text):
        return "文档"
    return "其他"


def classify_history_items(
    history: list[dict] | None,
    intent_text: str,
    *,
    clip_chars: int = 160,
    embed_fn: Any = None,
    anchor_in_history: bool = True,
) -> list[tuple[dict, str]]:
    """给每条历史消息分档，返回 [(item, tier)]，顺序不变。

    - 意图域未识别：不启用分档（依赖上文的短指令不能被误伤），仅超长消息截断；
    - anchor_in_history=True（默认，兼容无显式锚点的调用）：最后一条用户消息视为
      当前指令锚点，永远 keep；
    - anchor_in_history=False（装配回路实锤修复，2026-09-06）：调用方已显式传入本轮
      指令作锚点（历史不含本轮消息，历史最后一条 user 实为上一轮指令）→ 历史**全部**
      按普通规则分档，上一轮跨域指令该压缩就压缩；
    - 命中当前域特征 → keep；命中其他域特征 → summary；其余 → clip；
    - P3①：注入 embed_fn（callable(list[str]) → list[list[float]]）时，词表分档后
      叠加语义相关度——与当前指令余弦相似 ≥ SEMANTIC_KEEP_THRESHOLD 的非 keep 历史
      升级 keep（只升不降）；嵌入异常静默退回词表结果。
    """
    items = [it for it in (history or [])
             if isinstance(it, dict) and str(it.get("content") or "").strip()]
    if not items:
        return []
    domain = domain_of(intent_text)
    if not domain:
        return [(it, CLIP if len(str(it.get("content") or "")) > clip_chars * 4 else KEEP)
                for it in items]
    n = len(items)
    tiers: list[str] = [CLIP] * n
    for i, it in enumerate(items):
        text = str(it.get("content") or "")
        if anchor_in_history and i == n - 1 and it.get("role") == "user":
            tiers[i] = KEEP  # 当前指令锚点（仅无显式锚点时）
        elif _domain_hit(text, domain):
            tiers[i] = KEEP
        elif _cross_domain_label(text, domain) != "其他":
            tiers[i] = SUMMARY
        else:
            tiers[i] = CLIP
    pairs = list(zip(items, tiers))
    if embed_fn is not None:
        pairs = _semantic_upgrade(pairs, intent_text, embed_fn)
    return pairs


def _cosine(a: list, b: list) -> float:
    """余弦相似度；零向量/维度不等 → 0.0（脏输入按不相关处理）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _semantic_upgrade(
    pairs: list[tuple[dict, str]], intent_text: str, embed_fn: Any,
) -> list[tuple[dict, str]]:
    """P3①：语义相关度升级分档（2026-09-06）。

    只升不降：词表 keep 不动，summary/clip 与当前指令余弦 ≥ 阈值才升 keep。
    嵌入批量一次调用（intent + 历史），超出 _SEMANTIC_MAX_ITEMS 的尾部条目不参与
    语义判定（保持词表档）；任何异常静默退回原分档——语义是增强，不是依赖。
    """
    try:
        texts = [str(it.get("content") or "") for it, _ in pairs]
        expected = 1 + min(len(pairs), _SEMANTIC_MAX_ITEMS)
        vecs = embed_fn([intent_text, *texts[:_SEMANTIC_MAX_ITEMS]])
        # 数量校验（审计残留观察 #2）：embed 部分成功（返回数 ≠ 请求数）会让中间条目
        # 错位到错误的历史上——宁可整体跳过语义升级（退回词表分档），绝不错位升级
        if not isinstance(vecs, list) or len(vecs) != expected:
            return pairs
        intent_vec = vecs[0]
        upgraded: list[tuple[dict, str]] = []
        for idx, (item, tier) in enumerate(pairs):
            if tier != KEEP and idx < len(vecs) - 1:
                if _cosine(vecs[idx + 1], intent_vec) >= SEMANTIC_KEEP_THRESHOLD:
                    tier = KEEP
            upgraded.append((item, tier))
        return upgraded
    except Exception:  # noqa: BLE001 - 嵌入不可用必须退回词表分档，绝不阻断历史装配
        return pairs


def _clip_both(text: str, chars: int) -> str:
    text = text.strip()
    if len(text) <= chars * 2:
        return text
    return text[:chars] + f"\n…（中间省略 {len(text) - chars * 2} 字符）…\n" + text[-chars:]


def _aggregate_head(texts: list[str], *, cap: int = 100, per: int = 20) -> str:
    """跨域聚合的「涉及主题」：逐条取头部字符拼接，总量 ≤cap（纯函数零 LLM）。

    头部字符是设计使然的主题线索（与 summary 档口径一致），不做关键词抽取——
    语义增强留给 P2/P3（importance×recency 加权、embedding 相关度）。
    """
    parts: list[str] = []
    used = 0
    for t in texts:
        head = t.replace("\n", " ").strip()[:per]
        if not head:
            continue
        extra = len(head) + (1 if parts else 0)  # 分隔符「；」
        if used + extra > cap:
            break
        parts.append(head)
        used += extra
    return "；".join(parts)


def history_text_gated(
    ctx: Any,
    intent_text: str,
    *,
    clip_chars: int = 160,
    embed_fn: Any = None,
    anchor_text: str | None = None,
) -> str:
    """带分层标记的历史文本（P1，2026-09-06）：显式分层注入——

    1.【当前指令·最高优先级】本轮真实指令全文（锚点，永远第一层）；
    2.【同域历史·相关保留】keep 档逐条全文；
    3.【跨域历史·聚合摘要】summary 档按跨域标签分组，每组一行主题聚合
      （「此前为『生图』类任务 ×N（涉及主题：…），细节从略」）——
      多条同类历史不再逐条散落，篇幅上不再淹没当前指令；
    4.【通用历史·截断】clip 档两端截断。
    P3①：embed_fn 注入时词表分档叠加语义相关度升级（失败退回词表）。
    意图域未识别时退回 agent_context.history_text 原逻辑（不误伤短指令）。

    anchor_text（实锤修复，2026-09-06 用户四步实锤）：**本轮真实指令全文**（ctx.message）。
    装配回路里历史不含本轮消息——前端 visibleHistory 在 setMessages 前计算、后端
    append_turn 在回合结束 finally 才落——「历史最后一条 user」实为**上一轮**指令；
    不传 anchor_text 时才退回旧行为（历史最后一条 user 当锚点，兼容其它调用方）。
    显式锚点超长时两端截断（附件全文不进锚点层，防 100k 粘贴撑爆第一层）。
    """
    from app.services import agent_context as _ac
    history = ctx.get("history") or []
    if not history:
        return ""
    domain = domain_of(intent_text)
    if not domain:
        return _ac.history_text(ctx)
    anchor = (anchor_text or "").strip() or None
    items = classify_history_items(history, intent_text, clip_chars=clip_chars,
                                   embed_fn=embed_fn,
                                   anchor_in_history=anchor is None)
    anchor_idx = len(items) - 1 if (anchor is None and items[-1][0].get("role") == "user") else -1
    keeps: list[tuple[str, str]] = []
    groups: dict[str, list[str]] = {}
    clips: list[tuple[str, str]] = []
    for i, (it, tier) in enumerate(items):
        role = "用户" if it.get("role") == "user" else "助手"
        text = str(it.get("content") or "")
        if i == anchor_idx:
            continue
        if tier == KEEP:
            keeps.append((role, text))
        elif tier == SUMMARY:
            groups.setdefault(_cross_domain_label(text, domain), []).append(text)
        else:
            clips.append((role, text))
    lines: list[str] = []
    if anchor is not None:
        lines.append(f"[当前指令·最高优先级] 用户：{_clip_both(anchor, 800)}")
    elif anchor_idx >= 0:
        lines.append(f"[当前指令·最高优先级] 用户：{str(items[anchor_idx][0].get('content') or '')}")
    for role, text in keeps:
        lines.append(f"[相关·保留] {role}：{text}")
    for label, texts in groups.items():
        lines.append(
            f"[跨域聚合·{label}] 此前为『{label}』类任务 ×{len(texts)}"
            f"（涉及主题：{_aggregate_head(texts)}），细节从略，与当前任务无关"
        )
    for role, text in clips:
        lines.append(f"[截断] {role}：{_clip_both(text, clip_chars)}")
    header = (
        "【最近对话·已按当前任务相关性分层注入；第一层是当前指令（最高优先级），"
        "历史仅作背景参考，冲突时一律以当前指令为准；已压缩/聚合的历史无需回读】\n"
    )
    return header + "\n".join(lines) + "\n\n"
