"""生成审批生命周期：审核状态、已批准执行与结果事件转换。"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.services import generation_store, image_gen, prompt_approval_store, video_gen


def _review_text(*, retry: bool = False) -> str:
    return "已生成艺术化修饰稿，请在下方再次审核；尚未提交生成。" if retry \
        else "已按风格模板的结构整理提示词，请在下方审核；尚未提交生成。"


def _approval_payload(item: dict, status: str = "pending") -> dict:
    return {
        "id": item.get("id", ""),
        "messageId": item.get("message_id", ""),
        "kind": item.get("kind", "image"),
        "originalPrompt": item.get("original_prompt", ""),
        "prompt": item.get("candidate_prompt", ""),
        "status": status,
        "stage": item.get("stage", "prompt_review"),
        "reason": item.get("reason", ""),
    }


def _clear_after_success(thread_id: str, approval_id: str) -> None:
    if not approval_id:
        return
    try:
        prompt_approval_store.clear(thread_id, approval_id)
    except Exception:  # noqa: BLE001
        pass


def save_prompt_review(ctx: Any, kind: str, original: str, candidate: str,
                       images: list[str], reason: str,
                       image_mask: dict[str, str] | None = None) -> dict:
    item = prompt_approval_store.set(ctx["thread_id"], {
        "stage": "prompt_review", "kind": kind, "reason": reason,
        "original_prompt": original, "candidate_prompt": candidate,
        "images": images or [], "image_mask": image_mask,
        "message_id": ctx.get("message_id", ""),
    })
    return {
        "result_text": _review_text(retry=reason == "compatibility"),
        "approval": _approval_payload(item),
        "trace": ["📝 提示词等待用户审核"],
    }


def _save_rewrite_consent(ctx: Any, kind: str, original: str, submitted: str,
                          images: list[str], error: Exception, trace: list[str],
                          approval_id: str = "",
                          image_mask: dict[str, str] | None = None) -> dict:
    error_text = str(error)
    existing = prompt_approval_store.get(ctx["thread_id"], approval_id) if approval_id else None
    item = prompt_approval_store.set(ctx["thread_id"], {
        **(existing or {}),
        **({"id": approval_id} if approval_id else {}),
        "stage": "rewrite_consent", "kind": kind,
        "original_prompt": original, "candidate_prompt": submitted,
        "images": images or [], "image_mask": image_mask, "error": error_text,
        "message_id": (existing or {}).get("message_id", ctx.get("message_id", "")),
    })
    return {
        "result_text": (
            f"上游生成服务未返回成功结果：{error_text}\n\n"
            "我尚未自动修改提示词或重试。请直接在下方选择“确认提交”“更改”或“取消”；"
            "更改后的提示词仍会再次交给你审核。"
        ),
        "approval": _approval_payload(item, "failed"),
        "trace": trace,
    }


def _save_delivery_unknown(ctx: Any, kind: str, original: str, submitted: str,
                           images: list[str], error: Exception, trace: list[str],
                           approval_id: str = "",
                           image_mask: dict[str, str] | None = None) -> dict:
    error_text = str(error)
    existing = prompt_approval_store.get(ctx["thread_id"], approval_id) if approval_id else None
    item = prompt_approval_store.set(ctx["thread_id"], {
        **(existing or {}),
        **({"id": approval_id} if approval_id else {}),
        "stage": "delivery_unknown", "kind": kind, "reason": "delivery_unknown",
        "original_prompt": original, "candidate_prompt": submitted,
        "images": images or [], "image_mask": image_mask, "error": error_text,
        "message_id": (existing or {}).get("message_id", ctx.get("message_id", "")),
    })
    return {
        "result_text": (
            f"{error_text}\n\n"
            "无法确认上游是否创建任务。请先检查上游任务列表；确认没有记录后，再选择重新提交。"
            "系统不会自动重试，以免重复生成或扣费。"
        ),
        "approval": _approval_payload(item, "failed"),
        "trace": trace + ["⚠️ 上游交付状态未知"],
    }


def _save_request_not_sent(ctx: Any, kind: str, original: str, submitted: str,
                           images: list[str], error: Exception, trace: list[str],
                           approval_id: str = "",
                           image_mask: dict[str, str] | None = None) -> dict:
    error_text = str(error)
    existing = prompt_approval_store.get(ctx["thread_id"], approval_id) if approval_id else None
    item = prompt_approval_store.set(ctx["thread_id"], {
        **(existing or {}),
        **({"id": approval_id} if approval_id else {}),
        "stage": "request_failed", "kind": kind, "reason": "upstream_unreachable",
        "original_prompt": original, "candidate_prompt": submitted,
        "images": images or [], "image_mask": image_mask, "error": error_text,
        "message_id": (existing or {}).get("message_id", ctx.get("message_id", "")),
    })
    return {
        "result_text": (
            f"{error_text}\n\n"
            "请求没有发送到上游，也没有创建任务。系统不会自动重试；连接恢复后可手动重新提交。"
        ),
        "approval": _approval_payload(item, "failed"),
        "trace": trace + ["⚠️ 请求未发送到上游"],
    }


def _image_context(ctx: Any):
    return (
        ctx["gen_base"], ctx["gen_key"], ctx["gen_model"], ctx["thread_id"],
        ctx["repo_id"], ctx["output_dir"], ctx["embed_base"], ctx["embed_key"],
        ctx["embed_model"],
    )


def execute_generation(ctx: Any, kind: str, original: str, prompt: str,
                       images: list[str], trace: list[str], approval_id: str = "",
                       image_mask: dict[str, str] | None = None) -> dict:
    try:
        if kind == "video":
            url = video_gen.generate(ctx["vid_base"], ctx["vid_key"], ctx["vid_model"],
                                     prompt, size=ctx.get("size", "1024x1024"))
            rec = generation_store.persist_video(ctx["thread_id"], ctx["repo_id"], prompt,
                                                 url, ctx["output_dir"])
            approved = prompt_approval_store.get(ctx["thread_id"], approval_id) if approval_id else None
            _clear_after_success(ctx["thread_id"], approval_id)
            return {"result_text": f"已生成视频。提示词：{prompt}",
                    "video_recs": [rec], "trace": trace,
                    **({"approval": _approval_payload(approved or {}, "submitted")} if approved else {})}
        if kind == "img2img":
            if not images:
                raise RuntimeError("未找到参考图，无法图生图")
            gb, gk, gm, tid, rid, od, eb, ek, em = _image_context(ctx)
            kwargs = {
                "size": ctx.get("size", "1024x1024"),
                "quality": ctx.get("image_quality", "high"),
            }
            if image_mask:
                kwargs["mask"] = image_mask.get("mask", "")
            url = image_gen.generate_with_images(gb, gk, gm, prompt, images, **kwargs)
            regeneration = {
                "kind": "ai-image", "prompt": prompt, "images": list(images),
                **({"imageMask": image_mask} if image_mask else {}),
                "size": ctx.get("size", "1024x1024"),
                "quality": ctx.get("image_quality", "high"),
                "model": {"baseUrl": gb, "modelName": gm},
            }
            rec = generation_store.persist_image(
                tid, rid, prompt, url, od, eb, ek, em, regeneration)
            approved = prompt_approval_store.get(ctx["thread_id"], approval_id) if approval_id else None
            _clear_after_success(ctx["thread_id"], approval_id)
            return {"result_text": f"已基于 {len(images)} 张参考图生成。提示词：{prompt}",
                    "image_recs": [rec], "trace": trace,
                    **({"approval": _approval_payload(approved or {}, "submitted")} if approved else {})}

        gb, gk, gm, tid, rid, od, eb, ek, em = _image_context(ctx)
        url = image_gen.generate(
            gb, gk, gm, prompt,
            size=ctx.get("size", "1024x1024"),
            quality=ctx.get("image_quality", "high"),
        )
        regeneration = {
            "kind": "ai-image", "prompt": prompt, "images": [],
            "size": ctx.get("size", "1024x1024"),
            "quality": ctx.get("image_quality", "high"),
            "model": {"baseUrl": gb, "modelName": gm},
        }
        rec = generation_store.persist_image(
            tid, rid, prompt, url, od, eb, ek, em, regeneration)
        approved = prompt_approval_store.get(ctx["thread_id"], approval_id) if approval_id else None
        _clear_after_success(ctx["thread_id"], approval_id)
        return {"result_text": f"已生成图片。提示词：{prompt}",
                "image_recs": [rec], "trace": trace,
                **({"approval": _approval_payload(approved or {}, "submitted")} if approved else {})}
    except image_gen.UpstreamRequestNotSent as exc:
        return _save_request_not_sent(
            ctx, kind, original, prompt, images, exc, trace, approval_id, image_mask,
        )
    except image_gen.UpstreamDeliveryUnknown as exc:
        return _save_delivery_unknown(
            ctx, kind, original, prompt, images, exc, trace, approval_id, image_mask,
        )
    except Exception as exc:  # noqa: BLE001
        return _save_rewrite_consent(
            ctx, kind, original, prompt, images, exc, trace, approval_id, image_mask,
        )


def _approval_action(text: str) -> str:
    normalized = re.sub(r"[\s，。！？!?、,.;；：:]", "", (text or "").lower())
    if normalized in ("取消", "不同意", "拒绝", "不提交", "保持原样"):
        return "cancel"
    if normalized in ("同意修饰", "同意优化", "可以修饰", "确认修饰"):
        return "rewrite"
    if normalized in ("更改", "修改提示词", "更改提示词"):
        return "change"
    if normalized in ("确认提交", "同意提交", "批准提交", "确认生成"):
        return "submit"
    return ""


def _result_events(result: dict) -> list[dict]:
    events: list[dict] = []
    for line in result.get("trace") or []:
        events.append({"trace": line})
    for rec in result.get("image_recs") or []:
        events.append({"image": rec.get("url"), "id": rec.get("id"),
                       "regeneration": rec.get("regeneration")})
    for rec in result.get("video_recs") or []:
        events.append({"video": rec.get("url"), "id": rec.get("id")})
    if result.get("approval"):
        events.append({"approval": result["approval"]})
    if result.get("result_text"):
        events.append({"delta": result["result_text"]})
    return events


def handle_pending(context: Any, rewrite_prompt: Callable[[Any, str], str]) -> list[dict] | None:
    action = (context.approval_action or "").strip().lower() or _approval_action(context.message)
    if action not in ("submit", "change", "cancel", "rewrite"):
        return None
    pending = prompt_approval_store.get(context.thread_id, context.approval_id or None)
    if pending is None:
        return [{"delta": "这份提示词审批已失效或不存在。"}]
    approval_id = pending.get("id", "")

    if action == "cancel":
        prompt_approval_store.clear(context.thread_id, approval_id)
        return [{"trace": "🛑 已取消提示词审批"},
                {"approval": _approval_payload(pending, "cancelled")},
                {"delta": "已取消，本次提示词不会提交生成。"}]

    stage = pending.get("stage")
    if stage in ("prompt_review", "rewrite_consent", "delivery_unknown", "request_failed") and action == "change":
        edited = (context.edited_prompt or "").strip()
        if not edited:
            return [{"delta": "修改后的提示词不能为空。"}]
        updated = prompt_approval_store.set(context.thread_id, {
            **pending, "stage": "prompt_review", "reason": "compatibility",
            "candidate_prompt": edited,
        })
        return [{"trace": "✏️ 用户修改了待审核提示词"},
                {"approval": _approval_payload(updated)},
                {"delta": "提示词已更新，仍未提交生成。"}]

    if stage in ("prompt_review", "rewrite_consent", "delivery_unknown", "request_failed") and action == "submit":
        result = execute_generation(
            context, pending.get("kind", "image"),
            pending.get("original_prompt", ""), pending.get("candidate_prompt", ""),
            pending.get("images") or [], ["✅ 用户已审核并确认提交"], approval_id,
            pending.get("image_mask"),
        )
        return _result_events(result)

    if stage == "rewrite_consent" and action == "rewrite":
        try:
            candidate = rewrite_prompt(context, pending.get("candidate_prompt", ""))
        except Exception as exc:  # noqa: BLE001
            return [{"trace": "⚠️ 艺术化修饰失败"},
                    {"delta": f"提示词修饰失败：{exc}。原提示词仍未重新提交，你可以稍后重试或回复“取消”。"}]
        prompt_approval_store.set(context.thread_id, {
            **pending,
            "stage": "prompt_review", "reason": "compatibility",
            "candidate_prompt": candidate,
        })
        updated = prompt_approval_store.get(context.thread_id, approval_id) or pending
        return [{"trace": "📝 艺术化修饰稿等待用户再次审核"},
                {"approval": _approval_payload(updated)},
                {"delta": _review_text(retry=True)}]

    if stage == "prompt_review":
        return [{"delta": "请使用对应提示词卡片上的确认提交、更改或取消按钮。"}]
    return [{"delta": "当前记录正在等待是否进行艺术化修饰。"}]
