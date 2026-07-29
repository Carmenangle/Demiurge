"""Agent 后台运行：独占 thread、协作取消、断线后继续并统一收尾。

线程准入(谁在跑/能否再开/如何取消)下沉到 thread_admission，本模块只负责
把生成跑在后台线程里并统一收尾。RunAlreadyActive/is_running/cancel 重导出，
对外 API 不变。"""
import queue
import threading
from typing import Iterator

from app.services import agent_graph, chat_memory, generation_store, thread_admission
from app.services.agent_contracts import AgentEvent, RunContext
from app.services.thread_admission import RunAlreadyActive  # noqa: F401  重导出，保调用方不变


def is_running(thread_id: str) -> bool:
    return thread_admission.is_active(thread_id)


def cancel(thread_id: str) -> bool:
    return thread_admission.request_cancel(thread_id)


def run_multi_stream(context: RunContext) -> "queue.Queue":
    """启动 supervisor 多 Agent；同一 thread 只允许一个活动运行。"""
    q: "queue.Queue" = queue.Queue()
    final_text: list[str] = []
    approval_updates: list[dict] = []
    route_choice_updates: list[dict] = []
    admission = thread_admission.admit(context.thread_id, context.cancel_event)

    def worker() -> None:
        interrupted = False
        try:
            for event in agent_graph.stream_multi_agent(context):
                if event.get("interrupted"):
                    interrupted = True
                if event.get("delta"):
                    final_text.append(event["delta"])
                if event.get("approval"):
                    approval_updates.append(event["approval"])
                if event.get("route_choice"):
                    route_choice_updates.append(event["route_choice"])
                q.put(event)
        except Exception as exc:  # noqa: BLE001
            q.put({"error": str(exc)})
        finally:
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
