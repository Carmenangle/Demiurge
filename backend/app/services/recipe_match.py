"""固化流程复用匹配 + 事前固化询问判定（2026-09-10）。

零 LLM、零 token：纯本地文本比对，供 plan 节点在**启动自由循环之前**回答一个
问题——「这次的请求，在已保留的固化流程清单里有没有同类？」

设计取向刻意偏保守：**宁可漏问，不可多问**。
- 误判「已有」→ 少问一次，无害（下次同请求仍会在 done 时拿到草稿卡）；
- 误判「没有」→ **每一条**同类请求都被打断一次，是噪声，且与「省 token」的初衷相悖。

中文没有分词器可用，所以用**字符 2-gram（shingle）+ 英文/数字词元**做集合、
Jaccard 算相似度。廉价但稳定。阈值取得高（默认 0.55），且样本过短
（< `_MIN_SHINGLES` 个 shingle）一律不判命中——短句的 Jaccard 极不稳定。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# 相似度命中阈值：取高（宁可漏，不可多问）。
MATCH_THRESHOLD = 0.55
# 样本下限：shingle 太少的短句不参与判定（Jaccard 不稳定，易误命中）。
_MIN_SHINGLES = 6

_ASCII_RE = re.compile(r"[a-z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _shingles(text: str) -> set[str]:
    """关键词集合：英文/数字词元（小写） + 中文连续段切 2-gram。"""
    source = (text or "").lower()
    out: set[str] = set(_ASCII_RE.findall(source))
    for run in _CJK_RE.findall(source):
        if len(run) == 1:
            out.add(run)
            continue
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def fingerprint(text: str) -> str:
    """意图指纹（shingle 集合排序后哈希）。

    用途：**同一个请求只打扰一次**——用户没有理会询问、又原样重发同一条指令时，
    闸门靠指纹认出「已经问过」，直接放行去跑，不再重复弹问。
    """
    items = sorted(_shingles(text))
    joined = "|".join(items)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def is_solidifiable(judge_text: str) -> bool:
    """这一轮是否属于「值得固化」的交付类任务。

    只认文档/卡/世界书交付与显式自建工具两类——与固化产出的实际来源一致
    （只有这两类轨迹会带上 durable/expensive 步骤）。日常对话、批量生图不打扰。
    """
    from app.services import plan_compiler
    source = (judge_text or "").strip()
    if not source:
        return False
    return bool(plan_compiler.is_doc_delegation_intent(source)
                or plan_compiler.wants_tooling_intent(source))


# 参数里超过这个长度的文本视为「内容型参数」（如 doc.create_repo 的 content）。
_LONG_PARAM_CHARS = 200


def replay_safety(recipe: dict[str, Any]) -> str:
    """这条配方能不能**硬重放**：返回 `"hard"` 或 `"skeleton"`。

    固化（`plan_tasks.solidify_steps`）把步骤参数原样存下——**内容型参数也在里面**。
    内容型配方硬重放，只会把当时生成的那份内容再原样写一遍；本轮素材一变就写错了。
    所以只有「参数里没有长文本」的配方（生成/回收类确定性流程）才允许硬重放；
    含长文本的走骨架复用：不重放，交给自由循环按本次素材重新生成。
    """
    plan = recipe.get("plan") if isinstance(recipe, dict) else None
    for step in ((plan or {}).get("steps") or []):
        for value in (step.get("params") or {}).values():
            if isinstance(value, str) and len(value) > _LONG_PARAM_CHARS:
                return "skeleton"
    return "hard"


def similar_recipe(judge_text: str, recipes: dict[str, dict] | None = None,
                   threshold: float = MATCH_THRESHOLD) -> tuple[dict[str, Any] | None, float]:
    """在**已保留（saved）**的固化流程里找与本轮请求同类的一条。

    返回 `(命中的配方, 相似度)`；未命中时第一个元素为 None，第二个是最高的相似度
    （供 trace / 排障用）。

    只看 `saved`：草稿尚未经用户确认，既不该拦截用户走新流程，也不该被硬重放。
    """
    if recipes is None:
        from app.services import plan_tasks
        try:
            recipes = plan_tasks.list_recipes()
        except Exception:  # noqa: BLE001 - 清单不可用时不拦截对话
            return None, 0.0
    want = _shingles(judge_text)
    if len(want) < _MIN_SHINGLES:
        return None, 0.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    for recipe in (recipes or {}).values():
        if not isinstance(recipe, dict):
            continue
        if str(recipe.get("status") or "saved") != "saved":
            continue
        have = _shingles(str(recipe.get("intent") or recipe.get("name") or ""))
        if len(have) < _MIN_SHINGLES:
            continue
        score = _jaccard(want, have)
        if score > best_score:
            best, best_score = recipe, score
    if best is not None and best_score >= threshold:
        return best, best_score
    return None, best_score
