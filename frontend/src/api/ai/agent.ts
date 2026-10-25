// 多 Agent 编排与后台生成任务（原 api/ai.ts agent 段，2026-10-24 按资源域拆分）：
// Supervisor SSE 编排入口 + 后台 agent 运行状态 + 图片重生成。
// 单 agent 对外入口（imageAgentStream / POST /ai/image-agent）已下线：其大脑降级为多 Agent 的
// tool_agent 专家节点（见后端 agent_graph.tool_agent_node）。自由文本一律走 multiAgent（Supervisor 编排）。
// 注：/ai/image-agent/running 与 /ai/image-agent/cancel 仍保留——它们是后台化的共用机制，多 Agent 同用。
import { apiGet, apiPost } from "../client";
import {
  decodeChatStreamEvent,
  type ChatStreamEvent,
} from "../chatStreamProtocol";
import { openSSE } from "../sse";
import type { AiImageRegeneration } from "../../types/chat";
import { POLL_TIMEOUT_MS } from "./wire";
import type { AgentInvocation } from "./agentInvocation";
import { agentInvocationBody } from "./agentInvocation";

// Supervisor 多 Agent：走 LangGraph 编排端点。onTrace 透出节点流转（主管分派→专家执行）供展示协作过程。
export function multiAgent(
  request: AgentInvocation,
  cbs: {
    onEvent: (event: ChatStreamEvent) => void;
    onDone: (err?: string) => void;
  },
): () => void {
  return openSSE("/ai/multi-agent", agentInvocationBody(request), (obj) => {
    const event = decodeChatStreamEvent(obj);
    if (event.type === "error") throw new Error(event.message);
    cbs.onEvent(event);
  }, cbs.onDone);
}

export function regenerateImage(
  snapshot: AiImageRegeneration,
  model: { apiKey: string; proxyUrl?: string },
  persist: {
    threadId: string;
    repoId: string;
    outputDir: string;
    embed: { baseUrl: string; apiKey: string; modelName: string };
    embedProxyUrl?: string;
  },
) {
  return apiPost<{
    ok: boolean;
    id: string;
    url: string;
    regeneration: AiImageRegeneration;
  }>("/ai/regenerate-image", {
    thread_id: persist.threadId,
    repo_id: persist.repoId,
    prompt: snapshot.prompt,
    images: snapshot.images,
    image_mask: snapshot.imageMask || null,
    gen_base_url: snapshot.model.baseUrl,
    gen_api_key: model.apiKey,
    gen_model: snapshot.model.modelName,
    gen_proxy_url: model.proxyUrl || "",
    size: snapshot.size,
    image_quality: snapshot.quality,
    output_dir: persist.outputDir,
    embed_base_url: persist.embed.baseUrl,
    embed_api_key: persist.embed.apiKey,
    embed_model: persist.embed.modelName,
    embed_proxy_url: persist.embedProxyUrl || "",
  }, 960000);
}

// 该仓库是否有后台生成任务在跑（切回/刷新时据此轮询快照等落盘）
export function fetchAgentRunning(threadId: string) {
  return apiGet<{ running: boolean }>(
    `/ai/image-agent/running?thread_id=${encodeURIComponent(threadId)}`,
  );
}

// 打断该仓库的后台生成（半成品文本会落盘并补进记忆供下一轮续写=合并）
export function cancelAgent(threadId: string) {
  return apiPost<{ ok: boolean; running: boolean }>("/ai/image-agent/cancel", {
    thread_id: threadId,
  });
}

// 当前有后台生成任务在跑的所有仓库 thread（后台活动面板据此列出正在跑的仓库对话）
export function fetchRunningChatThreads() {
  return apiGet<{ threads: string[] }>("/ai/image-agent/running-threads", POLL_TIMEOUT_MS);
}
