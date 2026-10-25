// 仓库对话排队消息（原 api/ai.ts 排队段，2026-10-24 按资源域拆分）：后端持久化队列（离开页面/刷新后仍继续）。
import { apiGet, apiPost } from "../client";
import { POLL_TIMEOUT_MS } from "./wire";
import { agentInvocationBody, type AgentInvocation } from "./agentInvocation";

export type ChatQueueStatus = "queued" | "running" | "done" | "error" | "cancelled";
export interface ChatQueueTask {
  id: string; thread_id: string; need: string;
  status: ChatQueueStatus; error?: string; created_at: number; updated_at: number;
  images?: string[]; message?: string;
}

// multiAgent 完整参数落后端队列；worker 在前一条结束后串行认领执行。
export function enqueueChatQueueTask(invocation: AgentInvocation) {
  return apiPost<{ task: ChatQueueTask }>(
    "/ai/chat-queue/enqueue",
    agentInvocationBody(invocation),
  );
}

export function listChatQueueTasks(threadId = "") {
  return apiGet<{ tasks: ChatQueueTask[] }>(
    `/ai/chat-queue?thread_id=${encodeURIComponent(threadId)}`, POLL_TIMEOUT_MS,
  );
}
export function cancelChatQueueTask(taskId: string) {
  return apiPost<{ task: ChatQueueTask }>("/ai/chat-queue/cancel", { task_id: taskId });
}
