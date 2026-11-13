"""私聊/群聊的滚动上下文摘要（2026-11-12）。

问题：`agent_context.recent_history` 的自动压缩只在单轮内生效，摘要不落盘；
下一轮前端仍把完整可见历史发上来，历史超过触发线时会每轮重复摘要旧段。

方案：线程级滚动摘要缓存。只把放不进预算窗口的最旧对话折进摘要；缓存记录
「已覆盖条数 + 覆盖段尾部 hash」，下一次只把新增旧段合并进已有摘要，不重压全文。
可见快照不改——摘要只用于模型上下文，不影响用户看到的历史。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Callable

from app.config import DATA_DIR
from app.services import llm as _llm
from app.services import run_trace
from app.services.agent_contracts import CtxLike
from app.services.pathnames import safe_seg

_DIR = DATA_DIR / "chat_context_summaries"
_LOCK = threading.Lock()
PROMPT_VERSION = "chat-summary-v1"


def _path(thread_id: str, scope: str):
    thread = safe_seg(thread_id or "home", "home", strip=False)
    scope_name = safe_seg(scope or "chat", "chat", strip=False)
    return _DIR / f"{thread}__{scope_name}.json"


def load(thread_id: str, scope: str) -> dict | None:
    """读取该线程/范围的摘要缓存；损坏/缺字段按无缓存处理。"""
    try:
        raw = json.loads(_path(thread_id, scope).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    summary = str(raw.get("summary") or "").strip()
    try:
        covered = int(raw.get("covered") or 0)
    except (TypeError, ValueError):
        return None
    tail_hash = str(raw.get("tail_hash") or "")
    if not summary or covered < 0 or not tail_hash:
        return None
    return {
        "summary": summary,
        "covered": covered,
        "tail_hash": tail_hash,
        "updated_at": float(raw.get("updated_at") or 0.0),
    }


def save(
    thread_id: str, scope: str, *, covered: int, tail_hash: str, summary: str,
) -> None:
    """原子写入摘要缓存；失败只影响下一轮是否重压，不阻断聊天。"""
    text = (summary or "").strip()
    if not text:
        return
    path = _path(thread_id, scope)
    record = {
        "version": PROMPT_VERSION,
        "covered": max(0, int(covered)),
        "tail_hash": tail_hash,
        "summary": text,
        "updated_at": time.time(),
    }
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass  # 缓存写失败不阻断本轮；最坏下一轮再压一次


def digest(items: list[dict]) -> str:
    """历史前缀指纹：只取 role/content，编辑/删除会改变 hash 并触发重压。"""
    payload = [
        [str(item.get("role") or ""), str(item.get("content") or "")]
        for item in items
        if isinstance(item, dict)
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear_thread(thread_id: str) -> None:
    """清空该线程所有模式的摘要缓存（清空/压缩/恢复会话后调用）。"""
    thread = safe_seg(thread_id or "home", "home", strip=False)
    with _LOCK:
        for path in _DIR.glob(f"{thread}__*.json"):
            try:
                path.unlink()
            except OSError:
                pass


def _summary_char_limit(ctx: CtxLike) -> int:
    try:
        budget = int(ctx.get("context_max_tokens") or 0)
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        return 2_000
    return max(600, min(4_000, budget // 8))


def _summarize(
    ctx: CtxLike, old: list[dict], prior: str, *, limit: int,
) -> str | None:
    base = str(ctx.get("chat_base") or "")
    key = str(ctx.get("chat_key") or "")
    model = str(ctx.get("chat_model") or "")
    if not (base and model):
        return None
    transcript = "\n".join(
        ("用户" if item.get("role") == "user" else "角色") + "：" +
        str(item.get("content") or "").strip()
        for item in old
        if str(item.get("content") or "").strip()
    )
    if not transcript:
        return prior.strip() or None
    system = (
        "你在为一段角色聊天做长期记忆归档。把旧对话压成给角色自己看的中文摘要，"
        "供后续聊天当背景。\n"
        "必须保留：角色与用户的关系、称呼、亲密程度；用户明确说过的重要偏好、禁忌、"
        "拒绝过的事；角色做过的承诺、约定、未完成话题；重要事件（纪念日/婚礼/装扮/"
        "第一次/重大冲突）；当前情绪或未解决的矛盾。\n"
        "不要保留：临时寒暄、重复撒娇、技术或系统说明、图片生成细节、与角色关系"
        "无关的琐事。\n"
        f"若提供【已有摘要】，把新增内容合并进去，保留仍然有效的事实，不要重复。"
        f"只输出摘要正文，不超过 {limit} 字。"
    )
    user = ""
    if prior.strip():
        user += f"【已有摘要】\n{prior.strip()}\n\n"
    user += f"【新增旧对话】\n{transcript}"
    try:
        out = _llm.chat(
            base, key, model, system, user,
            temperature=0.2, retries=1, timeout=60,
            proxy=str(ctx.get("chat_proxy") or ""),
        )
    except Exception as exc:  # noqa: BLE001 [EXC-留痕] 摘要失败退回机械裁剪/旧摘要
        run_trace.emit(ctx, "chat.context_summary", outcome="error",
                       error=str(exc)[:200], scope=PROMPT_VERSION)
        return prior.strip() or None
    text = str(out or "").strip()
    if not text:
        return prior.strip() or None
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def make_summarizers(
    ctx: CtxLike,
) -> tuple[Callable[[list[dict]], str | None] | None,
           Callable[[list[dict], str], str | None] | None]:
    """返回 (全量摘要, 增量摘要) 回调；模型配置缺失时两者都是 None。"""
    if not (str(ctx.get("chat_base") or "") and str(ctx.get("chat_model") or "")):
        return None, None
    limit = _summary_char_limit(ctx)

    def full(old: list[dict]) -> str | None:
        return _summarize(ctx, old, "", limit=limit)

    def incremental(old: list[dict], prior: str) -> str | None:
        return _summarize(ctx, old, prior, limit=limit)

    return full, incremental
