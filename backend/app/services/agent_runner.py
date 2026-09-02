"""Agent 后台运行：独占 thread、协作取消、断线后继续并统一收尾。

线程准入(谁在跑/能否再开/如何取消)下沉到 thread_admission，本模块只负责
把生成跑在后台线程里并统一收尾。RunAlreadyActive/is_running/cancel 重导出，
对外 API 不变。"""
import queue
import threading
import time
from typing import Iterator

from app.services import agent_graph, chat_memory, chat_snapshot, generation_store, run_trace, thread_admission
from app.services.agent_contracts import AgentEvent, RunContext
from app.services.thread_admission import RunAlreadyActive  # noqa: F401  重导出，保调用方不变

# 流式期间半成品落盘节流：刷新/换设备后能看到未完成正文（2026-08-31 用户实锤刷新丢正文）。
PARTIAL_PERSIST_INTERVAL_SECONDS = 2.0
PARTIAL_PERSIST_MIN_CHARS = 300


def is_running(thread_id: str) -> bool:
    return thread_admission.is_active(thread_id)


def cancel(thread_id: str) -> bool:
    return thread_admission.request_cancel(thread_id)


def run_multi_stream(context: RunContext) -> "queue.Queue":
    """启动 supervisor 多 Agent；同一 thread 只允许一个活动运行。"""
    q: "queue.Queue" = queue.Queue()
    final_text: list[str] = []
    partial_thinking: list[str] = []
    approval_updates: list[dict] = []
    route_choice_updates: list[dict] = []
    last_partial_persist = 0.0
    last_partial_len = 0

    def persist_partial(now: float) -> None:
        """把当前半成品正文（与思考）节流写入快照：刷新后可恢复，且不重复建气泡。"""
        nonlocal last_partial_persist, last_partial_len
        text = "".join(final_text).strip()
        try:
            # 半成品落盘是尽力而为：失败不得把生成流打成 error（刷新最多退回旧快照）。
            if text:
                generation_store.persist_text(context.thread_id, context.message_id, text)
            if partial_thinking:
                chat_snapshot.merge_fields(
                    context.thread_id, context.message_id,
                    thinking="".join(partial_thinking),
                )
        except Exception:  # noqa: BLE001 - 半成品落盘失败不阻断生成
            pass
        last_partial_persist = now
        last_partial_len = len(text)
    admission = thread_admission.admit(context.thread_id, context.cancel_event)
    generation_store.persist_user_message(
        context.thread_id, context.user_message_id, context.message, context.input_images(),
        context.attachments,
    )
    run_trace.emit(
        context, "turn.started",
        raw_input=context.message,
        image_count=len(context.input_images()),
        message_id=context.message_id,
        user_message_id=context.user_message_id,
        model=context.chat.model,
        route_model=context.route_model or context.chat.model,
        illustrate=context.illustrate,
        comfy_illustrate=context.comfy_illustrate,
        comfy_audio=context.comfy_audio,
        comfy_video=context.comfy_video,
        stream_output=context.stream_output,
    )

    def emit(event: AgentEvent) -> None:
        if event.get("replace") is not None:
            final_text[:] = [event["replace"]]
            generation_store.persist_text(
                context.thread_id, context.message_id, event["replace"],
            )
        elif event.get("delta"):
            final_text.append(event["delta"])
        if event.get("thinking"):
            partial_thinking.append(event["thinking"])
        if event.get("delta") or event.get("thinking"):
            text_len = len("".join(final_text))
            if text_len:
                now = time.monotonic()
                if (last_partial_persist == 0.0
                        or now - last_partial_persist >= PARTIAL_PERSIST_INTERVAL_SECONDS
                        or text_len - last_partial_len >= PARTIAL_PERSIST_MIN_CHARS):
                    persist_partial(now)
        if event.get("illustrate_request"):
            request = event["illustrate_request"]
            generation_store.persist_media_slot(
                context.thread_id,
                context.message_id,
                str(event.get("id") or ""),
                request.get("offset") if isinstance(request, dict) else None,
            )
        if event.get("approval"):
            approval_updates.append(event["approval"])
        if event.get("route_choice"):
            route_choice_updates.append(event["route_choice"])
        q.put(event)

    # 即使正文关闭流式，也要允许 Roleplay 在最终正文就绪后立即发 replace/插画请求；
    # stream_output 只控制模型 delta，不控制后台媒体事件通道。
    context.stream_sink = emit

    def worker() -> None:
        interrupted = False
        try:
            for event in agent_graph.stream_multi_agent(context):
                if event.get("interrupted"):
                    interrupted = True
                emit(event)
        except Exception as exc:  # noqa: BLE001
            run_trace.emit(context, "turn.error", error=str(exc))
            q.put({"error": str(exc)})
        finally:
            try:
                text = "".join(final_text).strip()
                generation_store.persist_text(
                    context.thread_id, context.message_id, text, interrupted=interrupted,
                )
                for approval in approval_updates:
                    generation_store.persist_prompt_approval(context.thread_id, approval)
                for route_choice in route_choice_updates:
                    generation_store.persist_route_choice(context.thread_id, route_choice)
                try:
                    chat_memory.append_turn(
                        context.thread_id, context.message, context.input_images(),
                        text, interrupted=interrupted,
                    )
                except Exception:
                    pass
                run_trace.emit(
                    context, "turn.completed",
                    interrupted=interrupted,
                    assistant_output=text,
                    approval_count=len(approval_updates),
                    route_choice_count=len(route_choice_updates),
                )
            except Exception as exc:  # noqa: BLE001 - 收尾失败也必须释放 thread/SSE
                try:
                    run_trace.emit(context, "turn.finalize_error", error=str(exc))
                except Exception:
                    pass
            finally:
                thread_admission.release(admission)
                q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    return q


def drain(q: "queue.Queue") -> Iterator[AgentEvent]:
    while True:
        event = q.get()
        if event is None:
            return
        yield event
