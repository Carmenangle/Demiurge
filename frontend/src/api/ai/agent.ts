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

export interface ChatInitiativeResult {
  messages: { id?: string; speaker?: string; text: string }[];
  skip?: string;
}

/** 角色主动找上门（2026-11-09）：打开私聊时按需调用；不可发起时 messages 为空。 */
export function chatInitiative(request: AgentInvocation): Promise<ChatInitiativeResult> {
  return apiPost<ChatInitiativeResult>("/ai/chat/initiative", agentInvocationBody(request));
}

export interface ChatInitiativeScanItem {
  speaker: string;
  thread_id: string;
  messages: { id?: string; speaker?: string; text: string }[];
}

export interface ChatInitiativeScanResult {
  results: ChatInitiativeScanItem[];
  skip?: string;
}

/** 扫描整份名册：给够格的角色各写一条主动开场到其私聊线程（2026-11-10）。 */
export function chatInitiativeScan(request: AgentInvocation): Promise<ChatInitiativeScanResult> {
  return apiPost<ChatInitiativeScanResult>(
    "/ai/chat/initiative/scan", agentInvocationBody(request),
  );
}

export interface ChatFollowupResult {
  messages: { id?: string; speaker?: string; text: string }[];
  thread_id?: string;
  speaker?: string;
  kind?: "followup" | "reopen" | "";
  skip?: string;
}

/** 用户长时间不回复时的追问/重开（2026-11-10）：后端按节奏与上限决定是否生成。 */
export function chatFollowup(request: AgentInvocation): Promise<ChatFollowupResult> {
  return apiPost<ChatFollowupResult>("/ai/chat/followup", agentInvocationBody(request));
}

export interface ChatReactionResult {
  messages: { id?: string; speaker?: string; text: string }[];
  thread_id?: string;
  speaker?: string;
  skip?: string;
}

/** 角色刚发完照片/换完头像后的补话（2026-11-10）：后端写进私聊线程，前端合并上屏。 */
export function chatReaction(request: AgentInvocation): Promise<ChatReactionResult> {
  return apiPost<ChatReactionResult>("/ai/chat/reaction", agentInvocationBody(request));
}

/** 私聊已读上报（2026-11-10）：可见且滚到底时把最后一条角色消息时间告诉后端。 */
export function markChatRead(threadId: string, lastTs: number) {
  return apiPost<{ ok: boolean }>("/ai/chat/read", {
    thread_id: threadId, last_ts: lastTs,
  });
}

/** 读取私聊已读状态（渲染已读/未读圆点）。 */
export function fetchChatReadState(threadId: string) {
  return apiGet<{ state: { ts?: number; at?: number; unread_s?: number } }>(
    `/ai/chat/read?thread_id=${encodeURIComponent(threadId)}`,
  );
}

export interface CharacterRosterMember {
  name: string;
  uid?: string;
  confidence?: "high" | "medium" | "low" | "manual" | string;
  has_visual?: boolean;
  gender?: string;
}

export interface CharacterRosterCandidate {
  uid: string;
  index: number;
  comment: string;
  name: string;
  confidence: string;
  selected: boolean;
}

export interface CharacterRosterResult {
  source: "cards" | "worldbook";
  /** 当前生效的成员名（私聊/群聊/多元数据插入共用）。 */
  members: string[];
  /** 世界书模式下的角色条目（含置信度/是否有外貌线索）。 */
  entries: CharacterRosterMember[];
  /** 全部启用条目，供「管理角色」面板补选/排除。 */
  candidates: CharacterRosterCandidate[];
  fallback_card: string;
  /** 每个角色的私聊线程 id（后端唯一属主，前端离线时用本地算法兜底）。 */
  member_threads?: Record<string, string>;
  /** 每个角色私聊快照最后写入时间（新→旧排序）。 */
  member_activity?: Record<string, number>;
  error?: string;
}

export interface CharacterRosterRequest {
  repoId: string;
  outputDir?: string;
  characterDir?: string;
  cardName?: string;
  cardNames?: string[];
  openingCardName?: string;
  worldbookDir?: string;
  worldbookName?: string;
  interactionMode?: "chat" | "group_chat";
}

/** 角色名册（2026-11-10）：合集卡从世界书条目识别角色；只读，不调模型。 */
export function fetchCharacterRoster(request: CharacterRosterRequest): Promise<CharacterRosterResult> {
  return apiPost<CharacterRosterResult>("/ai/chat/roster", {
    thread_id: `${request.repoId || "home"}__chat`,
    message: "",
    repo_id: request.repoId,
    output_dir: request.outputDir || "",
    character_dir: request.characterDir || "",
    card_name: request.cardName || "",
    card_names: request.cardNames || [],
    opening_card_name: request.openingCardName || "",
    worldbook_dir: request.worldbookDir || "",
    worldbook_name: request.worldbookName || "",
    interaction_mode: request.interactionMode || "chat",
  });
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
