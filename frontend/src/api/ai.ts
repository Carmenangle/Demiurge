import { apiGet, apiPost, apiUpload, apiUrl } from "./client";
import {
  decodeChatStreamEvent,
  type ChatStreamEvent,
  type IllustrationSceneSpec,
  type StreamInspirationCard,
} from "./chatStreamProtocol";
import { openSSE } from "./sse";
import type { AiImageRegeneration, ChatMessage } from "../types/chat";
import { workspaceModeForWire, type WorkMode } from "../lib/viewRouting";
import {
  WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS,
  WORKFLOW_BUILD_PLAN_TIMEOUT_MS,
} from "../lib/workflowBuildExecution";
import type { AgentInvocationWire } from "../generated/wireContracts";

// 嵌入模型配置（设置 → 嵌入模型），单一属主。
export type Embed = {
  mode?: "remote" | "local";
  baseUrl: string;
  apiKey: string;
  modelName: string;
  modelDir?: string;
  rerankerDir?: string;
  proxyUrl?: string;
};
// 对话模型三元组配置。
export type Chat = { baseUrl: string; apiKey: string; modelName: string; proxyUrl?: string; providerProfile?: "openai_compatible" | "claude_compatible" };

// 对话附件元信息（file_id 真源；文件字节在会话级附件存储，前端只持元信息）。
export interface FileAttachmentMeta {
  fileId: string;
  name: string;
  mime: string;
  size: number;
}

// 上传文件为对话附件（会话级存储），返回元信息供渲染卡片与随消息透传。
// 后端响应是 snake_case {file_id,...}，这里显式映射为前端 camelCase——apiUpload 原样透传，
// 不映射会导致 meta.fileId === undefined，占位卡替换后渲染 `fileId.startsWith` 崩溃黑屏（2026-09-01 实锤）。
export async function uploadAttachment(threadId: string, file: File): Promise<FileAttachmentMeta> {
  const form = new FormData();
  form.append("thread_id", threadId);
  form.append("file", file);
  const res = await apiUpload<{ ok: boolean; file_id: string; name: string; mime: string; size: number }>(
    "/attachments/upload",
    form,
    120_000,
  );
  return { fileId: res.file_id, name: res.name, mime: res.mime, size: res.size };
}

// 附件下载 URL（历史回放只读卡片点击下载 / 媒体栏预览）。
export function attachmentUrl(fileId: string): string {
  return apiUrl(`/attachments/${encodeURIComponent(fileId)}`);
}

// wire 格式序列化器（收口三元组，各调用方不再逐字段手拆）：
// - chatBody：对话端点用 base_url/api_key/model
// - ragEmbed：RAG POST 端点用 base_url/api_key/embed_model
// - sseEmbed：SSE 端点用 embed_base_url/embed_api_key/embed_model（默认模型 embedding-3）
// 两者都显式透传 remote/local 模式；本地目录不随代码发布包上传。
function chatBody(chat: Chat) {
  return {
    base_url: chat.baseUrl,
    api_key: chat.apiKey,
    model: chat.modelName,
    proxy: chat.proxyUrl || "",
  };
}
function ragEmbed(embed?: Embed) {
  return {
    base_url: embed?.baseUrl || "",
    api_key: embed?.apiKey || "",
    embed_model: embed?.modelName || "",
    embed_mode: embed?.mode || "remote",
    embed_model_dir: embed?.modelDir || "",
    reranker_model_dir: embed?.rerankerDir || "",
    proxy_url: embed?.proxyUrl || "",
  };
}
function sseEmbed(embed?: Embed) {
  return {
    embed_base_url: embed?.baseUrl || "",
    embed_api_key: embed?.apiKey || "",
    embed_model: embed?.modelName || "embedding-3",
    embed_mode: embed?.mode || "remote",
    embed_model_dir: embed?.modelDir || "",
    reranker_model_dir: embed?.rerankerDir || "",
    embed_proxy_url: embed?.proxyUrl || "",
  };
}


export interface GenPromptResult {
  prompt: string;
  negative_prompt?: string;
  strategy?: "direct" | "repaired" | "fallback";
  validation_errors?: string[];
}

// 根据场景描述生成出图提示词（用设置里的对话模型）
export function genPrompt(
  scene: string,
  chat: Chat,
) {
  return apiPost<GenPromptResult>("/ai/prompt", {
    scene,
    ...chatBody(chat),
  });
}

// 反推：看图生成提示词（/r）。需视觉模型，复用对话模型配置。
export function describeImage(
  images: string[],
  chat: Chat,
  hint = "",
) {
  return apiPost<GenPromptResult>("/ai/describe-image", {
    images,
    hint,
    ...chatBody(chat),
  });
}

// 翻译（可选润色），用于模型介绍的多语言翻译
export function translateText(
  text: string,
  targetLang: string,
  chat: Chat,
  polish = false,
) {
  return apiPost<{ text: string }>("/ai/translate", {
    text,
    target_lang: targetLang,
    polish,
    ...chatBody(chat),
  });
}

export interface DescribeResult {
  description: string;
}

// 根据工作流节点结构 AI 生成一句能力描述（模板描述弹窗）
export function describeWorkflow(
  name: string,
  nodes: { id: string; type: string; title: string }[],
  chat: Chat,
) {
  return apiPost<DescribeResult>("/ai/describe-workflow", {
    name,
    nodes,
    ...chatBody(chat),
  });
}

// 基于已输入的能力描述文本润色，使其更便于 AI 理解
export function polishDescription(
  text: string,
  chat: Chat,
) {
  return apiPost<DescribeResult>("/ai/polish-description", {
    text,
    ...chatBody(chat),
  });
}

// 工作流输入口编排：AI 根据需求 + 选中节点的输入口结构，规划如何填充各输入口。
// 返回操作计划（不执行，前端确认后再 apply）。
export interface PortOp {
  node_id: string;
  input: string;
  output?: string;             // replace_output：输出口名
  action: "set_widget" | "set_image" | "replace_output";
  value?: string | number | boolean;
  image_index?: number;        // set_image / 图像 replace_output：用第几张用户图（从 1 开始）
  kind?: "image" | "text";     // replace_output：替换源类型
  reason?: string;
}
export interface PortsPlan {
  summary: string;
  ops: PortOp[];
  is_orchestration?: boolean;   // false=AI 判定这句不是编排意图，前端应转普通对话
}

export function workflowPorts(
  scene: string,
  imageCount: number,
  nodeSchema: unknown[],
  modelName: string,
  chat: Chat,
  force = false,
  style = "",
  styleTemplate = "",
  repoId = "",
) {
  return apiPost<PortsPlan>("/ai/workflow-ports", {
    scene,
    image_count: imageCount,
    node_schema: nodeSchema,
    model_name: modelName,
    ...chatBody(chat),
    force,
    style,
    style_template: styleTemplate,
    repo_id: repoId,   // 后端据此取该仓库的当前色彩约束
  });
}

export interface ChatTurn {
  role: "user" | "assistant" | "system";
  content: string;
  images?: string[];   // 该条消息附带的图片（dataURI 或 URL）
}

// 灵感卡：联网搜主题 → 整理成「标题+内容」中文总结
export type Inspiration = StreamInspirationCard;

// 联网找灵感：搜索源抓取 + 对话模型整理成「标题+内容」中文总结。/find 指令用。
export function fetchInspiration(
  query: string,
  chat: Chat,
  proxyUrl = "",
) {
  return apiPost<Inspiration>("/ai/inspiration", {
    query,
    ...chatBody(chat),
    proxy_url: proxyUrl,
  });
}

// 记录用户勾选的图片 URL 到灵感卡消息（M1.2 选中持久化）
export function selectInspiration(threadId: string, messageId: string, urls: string[]) {
  return apiPost<{ ok: boolean; selected: string[] }>("/ai/inspiration/select", {
    thread_id: threadId,
    message_id: messageId,
    urls,
  });
}

// 外网图片代理 URL（M1.2：缩略图走后端中转，防浏览器直连外网图床被墙/防盗链）
export function proxyImageUrl(url: string, proxy = "") {
  return apiUrl(`/ai/image-proxy?url=${encodeURIComponent(url)}&proxy=${encodeURIComponent(proxy)}`);
}

// 单 agent 对外入口（imageAgentStream / POST /ai/image-agent）已下线：其大脑降级为多 Agent 的
// tool_agent 专家节点（见后端 agent_graph.tool_agent_node）。自由文本一律走 multiAgent（Supervisor 编排）。
// 注：/ai/image-agent/running 与 /ai/image-agent/cancel 仍保留——它们是后台化的共用机制，多 Agent 同用。

// 拉取某仓库已落盘的对话历史（刷新/进入仓库时回填）
export function fetchHistory(threadId: string) {  return apiGet<{ items: ChatTurn[] }>(
    `/ai/chat/history?thread_id=${encodeURIComponent(threadId)}`,
  );
}

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

// 清除缓存：清对话线+前端快照+删本仓库 reference/ 上传参考图。资产库与知识库不动。返回删了几张参考图。
export function clearCache(threadId: string, outputDir: string) {
  return apiPost<{ ok: boolean; removed: number }>(
    "/ai/chat/clear-cache",
    { thread_id: threadId, output_dir: outputDir },
  );
}

// 压缩完整会话：旧消息替换为摘要文本和最后成果图；知识库与资产库不动。
export function compactHistory(
  threadId: string,
  chat: { baseUrl: string; apiKey: string; modelName: string },
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{
    ok: boolean;
    summary: string;
    image_count: number;
    message: Pick<ChatMessage, "id" | "role" | "text"> & Partial<Pick<ChatMessage, "image">>;
  }>(
    "/ai/chat/compact",
    { thread_id: threadId, ...chatBody(chat), ...sseEmbed(embed) },
  );
}

let lastSnapshotRevision = Date.now();

// 落盘前端完整消息流快照（含工作流卡/反推卡等非对话消息），作为可靠真源。
// 单调版本保证较早发出的防抖请求即使晚到，也不能覆盖后发的删除结果。
export function saveSnapshot(threadId: string, messages: unknown[]) {
  lastSnapshotRevision = Math.max(Date.now(), lastSnapshotRevision + 1);
  return apiPost<{ ok: boolean; saved: boolean }>("/ai/chat/snapshot/save", {
    thread_id: threadId,
    messages,
    revision: lastSnapshotRevision,
  });
}

export interface AgentInvocation {
  threadId: string;
  message: string;
  images: string[];
  imageMask?: { image: string; mask: string } | null;
  workMode: WorkMode;
  chat: Chat;
  gen: Chat;
  video?: Chat;
  embed?: Embed;
  size: string;
  imageQuality: "auto" | "low" | "medium" | "high";
  outputDir: string;
  repoId: string;
  proxyUrl: string;
  chatProxyUrl: string;
  genProxyUrl: string;
  videoProxyUrl: string;
  embedProxyUrl: string;
  routeModel?: string;
  providerProfile?: "openai_compatible" | "claude_compatible";
  messageId?: string;
  userMessageId?: string;
  styleTemplate: string;
  agentId: string;
  streamOutput: boolean;
  contextMaxTokens: number;
  historyPerRole: number;
  selfhealAttempts: number;
  history: { role: "user" | "assistant"; content: string }[];
  characterDir: string;
  cardName: string;
  cardNames: string[];
  openingCardName: string;
  presetDir: string;
  presetName: string;
  userName: string;
  userPersona: string;
  personaBound: boolean;
  worldbookDir: string;
  worldbookName: string;
  illustrate: boolean;
  comfyIllustrate: boolean;
  comfyAudio: boolean;
  comfyVideo: boolean;
  videoMode?: "climax" | "firstlast";
  promptProfile: string;
  appearanceSource: "worldbook" | "character_card";
  characterBaseImages: Record<string, string>;
  illustrationActorNames: string[];
  styleBaseImage: string;
  attachments?: FileAttachmentMeta[];
  approvalAction?: { approvalId: string; action: "submit" | "change" | "cancel"; editedPrompt?: string };
  routeAction?: { route: import("../types/chat").AgentRoute; userMessageId: string };
}

export function agentInvocationBody(request: AgentInvocation): AgentInvocationWire {
  return {
    thread_id: request.threadId,
    workspace_mode: workspaceModeForWire(request.workMode),
    message: request.message,
    images: request.images,
    image_mask: request.imageMask || null,
    ...chatBody(request.chat),
    gen_base_url: request.gen.baseUrl,
    gen_api_key: request.gen.apiKey,
    gen_model: request.gen.modelName,
    video_base_url: request.video?.baseUrl || "",
    video_api_key: request.video?.apiKey || "",
    video_model: request.video?.modelName || "",
    size: request.size,
    image_quality: request.imageQuality,
    output_dir: request.outputDir,
    repo_id: request.repoId,
    ...sseEmbed(request.embed),
    proxy_url: request.proxyUrl,
    chat_proxy_url: request.chatProxyUrl,
    gen_proxy_url: request.genProxyUrl,
    video_proxy_url: request.videoProxyUrl,
    embed_proxy_url: request.embedProxyUrl,
    route_model: request.routeModel || "",
    provider_profile: request.providerProfile || "openai_compatible",
    message_id: request.messageId || "",
    user_message_id: request.routeAction?.userMessageId || request.userMessageId || "",
    style_template: request.styleTemplate,
    agent_id: request.agentId,
    stream_output: request.streamOutput,
    context_max_tokens: request.contextMaxTokens,
    history_per_role: request.historyPerRole,
    selfheal_attempts: request.selfhealAttempts,
    history: request.history,
    approval_id: request.approvalAction?.approvalId || "",
    approval_action: request.approvalAction?.action || "",
    edited_prompt: request.approvalAction?.editedPrompt || "",
    forced_route: request.routeAction?.route || "",
    character_dir: request.characterDir,
    card_name: request.cardName,
    card_names: request.cardNames,
    opening_card_name: request.openingCardName || request.cardName,
    preset_dir: request.presetDir,
    preset_name: request.presetName,
    user_name: request.userName,
    user_persona: request.userPersona,
    persona_bound: request.personaBound,
    worldbook_dir: request.worldbookDir,
    worldbook_name: request.worldbookName,
    illustrate: request.illustrate,
    comfy_illustrate: request.comfyIllustrate,
    comfy_audio: request.comfyAudio,
    comfy_video: request.comfyVideo,
    video_mode: request.videoMode || "",
    prompt_profile: request.promptProfile || "krea2",
    appearance_source: request.appearanceSource,
    character_base_images: request.characterBaseImages,
    illustration_actor_names: request.illustrationActorNames,
    style_base_image: request.styleBaseImage,
    attachments: (request.attachments || []).map((a) => ({
      file_id: a.fileId,
      name: a.name,
      mime: a.mime,
      size: a.size,
    })),
  };
}

export function genProfilePrompt(
  profile: string,
  scene: IllustrationSceneSpec,
  chat: Chat,
  preset: { presetDir?: string; presetName?: string; userName?: string } = {},
) {
  return apiPost<GenPromptResult & { profile: string }>("/ai/prompt/profile", {
    profile,
    scene,
    preset_dir: preset.presetDir || "",
    preset_name: preset.presetName || "",
    user_name: preset.userName || "",
    ...chatBody(chat),
  }, 120_000);
}

export interface FramePromptItem {
  prompt: string;
  negative_prompt: string;
  strategy: string;
  validation_errors: string[];
  field_ledger: Record<string, unknown>;
}

// 首尾帧提示词：与高潮点同构——后端先做时点提取（首/尾各自英文结构化 action/visual_facts），
// 再逐帧走同一 Profile 编译器，一次调用出两帧成品。
export function genFramePrompts(
  profile: string,
  scene: IllustrationSceneSpec,
  frames: { first?: string; last?: string },
  chat: Chat,
  preset: { presetDir?: string; presetName?: string; userName?: string } = {},
) {
  return apiPost<{ frames: Partial<Record<"first" | "last", FramePromptItem>>; profile: string }>(
    "/ai/prompt/profile/frames",
    {
      profile,
      scene,
      frames: { first: frames.first || "", last: frames.last || "" },
      preset_dir: preset.presetDir || "",
      preset_name: preset.presetName || "",
      user_name: preset.userName || "",
      ...chatBody(chat),
    },
    240_000,
  );
}

export function getProfilePromptDefaults(profile: string, rating = "nsfw") {
  return apiGet<{ quality_prompt: string; negative_prompt: string }>(
    `/ai/prompt/profile/defaults?profile=${encodeURIComponent(profile)}&rating=${encodeURIComponent(rating)}`,
  );
}

// 读取某仓库的消息流快照（localStorage 缺失时回填，关浏览器/清端口不丢）
export function fetchSnapshot(threadId: string) {
  return apiGet<{ items: unknown[] }>(
    `/ai/chat/snapshot?thread_id=${encodeURIComponent(threadId)}`,
  );
}

// 把一条已生成的消息（提示词/图片/配方文本）直接落盘到目标 thread，不调模型。
// 用于「发送至对话框 / 发送至对话」：从资产库或画布把内容送到指定作品对话，刷新后保留。
export function chatAppend(
  threadId: string, role: "user" | "assistant", text: string, images?: string[],
) {
  return apiPost<{ ok: boolean }>("/ai/chat/append", {
    thread_id: threadId,
    role,
    text,
    images: images || [],
  });
}

// 导出某作品的完整会话记录（剧情模式常用：备份/搬到别处）
export function exportSnapshot(threadId: string) {
  return apiGet<{ thread_id: string; messages: unknown[] }>(
    `/ai/chat/snapshot/export?thread_id=${encodeURIComponent(threadId)}`,
  );
}

// 导入会话记录到某作品：replace=整体覆盖，否则按消息 id 合并
export function importSnapshot(threadId: string, messages: unknown[], replace = true) {
  return apiPost<{ ok: boolean; count: number }>("/ai/chat/snapshot/import", {
    thread_id: threadId,
    messages,
    replace,
  });
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
  return apiGet<{ threads: string[] }>("/ai/image-agent/running-threads");
}

// ---- 仓库对话排队消息：后端持久化队列（离开页面/刷新后仍继续）----
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

export function reportIllustrationFailure(payload: {
  threadId: string; repoId: string; messageId: string; slotId: string;
  stage: string; error: string; promptId?: string; comfyuiUrl?: string;
}) {
  return apiPost<{ ok: boolean; removed: boolean; cancelled?: { deleted: boolean; interrupted: boolean } }>("/ai/image-agent/illustration-failure", {
    thread_id: payload.threadId,
    repo_id: payload.repoId,
    message_id: payload.messageId,
    slot_id: payload.slotId,
    stage: payload.stage,
    error: payload.error,
    prompt_id: payload.promptId || "",
    comfyui_url: payload.comfyuiUrl || "",
  });
}

export function claimIllustrationSubmission(payload: {
  threadId: string; messageId: string; slotId: string;
}) {
  return apiPost<{ ok: boolean; claimed: boolean }>("/ai/image-agent/illustration-claim", {
    thread_id: payload.threadId,
    message_id: payload.messageId,
    slot_id: payload.slotId,
  });
}

export function reportIllustrationSubmission(payload: {
  threadId: string; repoId: string; turnId?: string; messageId: string; slotId: string;
  templateId: string; promptId: string; prompt: string; promptProfile: string;
  loraName?: string; loraWeight?: number; latentWidth: number; latentHeight: number;
  loraMode?: "none" | "single" | "multi"; loraNames?: string[];
  valueKeys: string[];
  source?: "automatic" | "manual";
}) {
  return apiPost<{ ok: boolean }>("/ai/image-agent/illustration-submission", {
    thread_id: payload.threadId,
    repo_id: payload.repoId,
    turn_id: payload.turnId || "",
    message_id: payload.messageId,
    slot_id: payload.slotId,
    template_id: payload.templateId,
    prompt_id: payload.promptId,
    prompt: payload.prompt,
    prompt_profile: payload.promptProfile,
    lora_name: payload.loraName || "",
    lora_weight: payload.loraWeight,
    lora_mode: payload.loraMode || "single",
    lora_names: payload.loraNames || [],
    latent_width: payload.latentWidth,
    latent_height: payload.latentHeight,
    value_keys: payload.valueKeys,
    source: payload.source || "automatic",
  });
}

export function reportAudioSubmission(payload: {
  threadId: string; repoId: string; turnId?: string; messageId: string; slotId: string;
  speaker: string; text: string; voiceRef: string; templateId: string; promptId: string;
  emotion?: Record<string, number>; valueKeys: string[];
  source?: "automatic" | "manual";
}) {
  return apiPost<{ ok: boolean }>("/ai/image-agent/audio-submission", {
    thread_id: payload.threadId,
    repo_id: payload.repoId,
    turn_id: payload.turnId || "",
    message_id: payload.messageId,
    slot_id: payload.slotId,
    speaker: payload.speaker,
    text: payload.text,
    voice_ref: payload.voiceRef,
    template_id: payload.templateId,
    prompt_id: payload.promptId,
    emotion: payload.emotion || {},
    value_keys: payload.valueKeys,
    source: payload.source || "automatic",
  });
}

export function ensureAudioSlot(payload: {
  threadId: string; messageId: string; slotId: string;
  speaker?: string; seq?: number; total?: number;
}) {
  return apiPost<{ ok: boolean }>("/ai/image-agent/ensure-audio-slot", {
    thread_id: payload.threadId,
    message_id: payload.messageId,
    slot_id: payload.slotId,
    speaker: payload.speaker || "",
    seq: payload.seq,
    total: payload.total,
  });
}
export function listChatQueueTasks(threadId = "") {
  return apiGet<{ tasks: ChatQueueTask[] }>(`/ai/chat-queue?thread_id=${encodeURIComponent(threadId)}`);
}
export function cancelChatQueueTask(taskId: string) {
  return apiPost<{ task: ChatQueueTask }>("/ai/chat-queue/cancel", { task_id: taskId });
}

// ---- AI 搭工作流：节点知识库 + 自动搭建 ----

type ChatCfg = { baseUrl: string; apiKey: string; modelName: string };

// 启动后台同步：扫描 ComfyUI 已装节点入库。立即返回总包数，进度经 syncProgress 轮询。
export function syncNodes(embed: Embed, comfyUrl: string, full = false) {
  return apiPost<{ total_packs: number; already_running: boolean }>(
    "/ai/nodes/sync",
    { ...sseEmbed(embed), comfy_url: comfyUrl, full },
  );
}

export interface SyncProgress {
  running: boolean; done: number; total: number; current: string;
  synced: number; skipped: number; failed: number; failures: string[];
  error: string; finished: boolean;
}
// 同步进度快照（轮询）
export function syncProgress() {
  return apiGet<SyncProgress>("/ai/nodes/sync-progress");
}

// 节点知识库现状（包数 + 节点数）
export function nodeStats(embed: Embed) {
  return apiPost<{ packs: number; nodes: number }>("/ai/nodes/stats", sseEmbed(embed));
}

export interface NodePackItem { id: string; title: string; node_count: number; python_module: string; }
export interface NodePackDetail extends NodePackItem { content: string; node_names: string[]; categories: string[]; }

// 全部节点包列表（管理页）
export function listNodePacks(embed: Embed) {
  return apiPost<{ packs: NodePackItem[] }>("/ai/nodes/packs", sseEmbed(embed));
}
// 单个包完整内容（含用途正文）
export function getNodePack(embed: Embed, packId: string) {
  return apiPost<NodePackDetail>("/ai/nodes/pack", { ...sseEmbed(embed), pack_id: packId });
}
// 修订某包用途正文并重嵌入
export function updateNodePackContent(embed: Embed, packId: string, content: string) {
  return apiPost<{ ok: boolean }>("/ai/nodes/pack/update", { ...sseEmbed(embed), pack_id: packId, content });
}

export interface BuildResult {
  ok: boolean;
  path: string;
  graph: Record<string, unknown>;
  errors: string[];
  warnings?: string[];   // 非阻断提示（如断链孤岛：节点还没接进主链）
  missing_nodes?: string[];  // 本机没装、已从图里移除的节点类型（供「去安装」按钮）
  alternatives?: Record<string, string[]>;  // {缺失节点: [本机同类平替...]}（供「用平替重搭」）
}

export type BuildTurn = { role: "user" | "assistant"; text: string };

// 按需求自动搭工作流：检索节点→AI 生成→校验重试→（可选）落盘到 workflowDir
// currentGraph 非空=在当前画布基础上增量改；save=false 只回图不落盘（多轮迭代中途）
export function buildWorkflow(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string; workflowDir: string; name?: string;
  currentGraph?: Record<string, unknown>; save?: boolean; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<BuildResult>("/ai/build", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, workflow_dir: args.workflowDir, name: args.name || "",
    current_graph: args.currentGraph || {}, save: args.save !== false, history: args.history || [],
  }, WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS, args.signal);
}

// 分模块增量搭建：冻结当前图，AI 只出新模块+锚点，后端合并进整图。返回合并后完整图，前端写回画布。
export function buildModule(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string;
  currentGraph: Record<string, unknown>; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<BuildResult>("/ai/build/module", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, current_graph: args.currentGraph, history: args.history || [],
  }, WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS, args.signal);
}

// 精简直连：信任强模型(Opus)一次到位，只调 1 次模型，不 audit 自修/不重写/不回喂重试。最快。
export function buildDirect(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string;
  currentGraph?: Record<string, unknown>; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<BuildResult>("/ai/build/direct", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, current_graph: args.currentGraph || {}, history: args.history || [],
  }, WORKFLOW_BUILD_EXECUTE_TIMEOUT_MS, args.signal);
}

// 顾问模式：只产出给人看的中文方案文本，不改画布。用户确认后再走 build/module 执行。
export function buildPlan(args: {
  need: string; chat: ChatCfg; embed: Embed; comfyUrl: string;
  currentGraph?: Record<string, unknown>; history?: BuildTurn[]; proxy?: string; signal?: AbortSignal;
}) {
  return apiPost<{ plan: string }>("/ai/build/plan", {
    base_url: args.chat.baseUrl, api_key: args.chat.apiKey, model: args.chat.modelName, proxy: args.proxy || "",
    ...sseEmbed(args.embed),
    need: args.need, comfy_url: args.comfyUrl, current_graph: args.currentGraph || {}, history: args.history || [],
  }, WORKFLOW_BUILD_PLAN_TIMEOUT_MS, args.signal);
}

// 把前端手改后的画布 graph 直接落盘（不经 AI）
export function saveWorkflow(args: {
  graph: Record<string, unknown>; embed: Embed; workflowDir: string; name?: string;
}) {
  return apiPost<{ ok: boolean; path: string }>("/ai/build/save", {
    ...sseEmbed(args.embed),
    graph: args.graph, workflow_dir: args.workflowDir, name: args.name || "",
  });
}

// —— 骨架底座：AI 搭工作流的正确起点 ——
export interface Skeleton {
  id: string; name: string; desc: string; kind: string;
  source: "builtin" | "file"; node_count: number; path: string;
}
// 列出骨架候选（内置 + 工作流文件夹里的 .json）
export function listSkeletons(workflowDir: string) {
  return apiPost<{ skeletons: Skeleton[] }>("/ai/skeletons", { workflow_dir: workflowDir });
}
// 取某骨架的 graph（load 进画布用；文件只读不改）
export function skeletonGraph(skeletonId: string, workflowDir: string) {
  return apiPost<{ graph: Record<string, unknown> }>("/ai/skeleton/graph", {
    skeleton_id: skeletonId, workflow_dir: workflowDir,
  });
}

// —— 搭建会话：进度保存 + 多开 ——
export interface BuildSessionMeta { id: string; name: string; updated_at: number; node_count: number; msg_count: number; }
export interface BuildSessionFull { id: string; name: string; msgs: unknown[]; graph: Record<string, unknown>; skeleton_id: string; updated_at: number; }

export function listBuildSessions() {
  return apiGet<{ sessions: BuildSessionMeta[] }>("/ai/build/sessions");
}
export function getBuildSession(id: string) {
  return apiGet<BuildSessionFull>(`/ai/build/session?id=${encodeURIComponent(id)}`);
}
export function saveBuildSession(args: {
  id?: string; name: string; msgs: unknown[]; graph: Record<string, unknown>; skeletonId?: string;
}) {
  return apiPost<{ id: string; name: string; updated_at: number }>("/ai/build/session/save", {
    id: args.id || "", name: args.name, msgs: args.msgs, graph: args.graph, skeleton_id: args.skeletonId || "",
  });
}
export function deleteBuildSession(id: string) {
  return apiPost<{ ok: boolean }>("/ai/build/session/delete", { id });
}

export type WorkflowBuildTaskStatus = "queued" | "running" | "done" | "error" | "cancelled";
export interface WorkflowBuildTask {
  id: string; session_id: string; mode: "direct" | "module" | "workflow" | "plan";
  need: string; status: WorkflowBuildTaskStatus; result?: BuildResult | { plan: string };
  error?: string; created_at: number; updated_at: number;
}
export function enqueueWorkflowBuildTask(payload: {
  sessionId: string; mode: WorkflowBuildTask["mode"]; need: string;
  chat: Chat; embed: Embed; comfyUrl: string; workflowDir: string;
  currentGraph: Record<string, unknown>; history: BuildTurn[];
}) {
  return apiPost<WorkflowBuildTask>("/ai/build/tasks", {
    session_id: payload.sessionId, mode: payload.mode, need: payload.need,
    ...chatBody(payload.chat), comfy_url: payload.comfyUrl, workflow_dir: payload.workflowDir,
    ...sseEmbed(payload.embed), current_graph: payload.currentGraph, history: payload.history,
  });
}
export function listWorkflowBuildTasks(sessionId = "") {
  return apiGet<{ tasks: WorkflowBuildTask[] }>(`/ai/build/tasks?session_id=${encodeURIComponent(sessionId)}`);
}
export function cancelWorkflowBuildTask(id: string) {
  return apiPost<WorkflowBuildTask>("/ai/build/task/cancel", { id });
}

// 生图完成后把这次生成的提示词/标签/图片入全局 RAG 知识库
export function indexGeneration(
  threadId: string,
  data: { prompt?: string; tags?: string; image_url?: string },
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ ok: boolean }>("/rag/index-generation", {
    thread_id: threadId,
    prompt: data.prompt || "",
    tags: data.tags || "",
    image_url: data.image_url || "",
    ...ragEmbed(embed),
  });
}

// 手动上传参考资料入全局 RAG 知识库
export function indexDocument(
  threadId: string,
  text: string,
  title: string,
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ ok: boolean; chunks: number }>("/rag/index-document", {
    thread_id: threadId,
    text,
    title,
    ...ragEmbed(embed),
  });
}

// ⑤ 批量导入参考资料（从导出 JSON 恢复 / 迁移仓库）
export function importDocuments(
  threadId: string,
  docs: { text: string; title: string }[],
  embed: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ ok: boolean; documents: number; chunks: number }>("/rag/import-documents", {
    thread_id: threadId,
    docs,
    ...ragEmbed(embed),
  });
}

export interface RagDoc {
  id: string;
  content: string;
  kind: string;       // system | document | generation
  title: string;
  locked: boolean;    // 系统指令条目，不可删改
  image_url: string;
}

// 列出「系统库 + 本仓库库」所有条目（顺带幂等播种系统指令）
export function listDocs(repoId: string, embed: Embed) {
  return apiPost<{ items: RagDoc[] }>("/rag/list", {
    repo_id: repoId,
    ...ragEmbed(embed),
  });
}

export interface Generation {
  id: string;
  repo_id?: string;
  prompt: string;
  description?: string;
  image_url: string;
  tags: string[];
  /** 工作流生成元数据：模板名、主模型、LoRA（逗号分隔） */
  template_name?: string;
  model_name?: string;
  lora_names?: string;
  created_at?: number;   // 入库毫秒时间戳（权威排序键；历史记录可能为 0/缺失）
  /** M2.1/M2.2：媒体类型（image|video），视频资产按 video 渲染/播放 */
  mediaType?: "image" | "video";
  /** M2.1：派生链弱引用（视频→首帧底图槽），只读展示不级联 */
  derivedFrom?: Array<{
    media_slot_ref?: { message_id: string; slot_id: string };
    asset_id?: string;
    turn_id?: string;
    kind?: string;
  }>;
}

// 列出某仓库的生成记录（图片+提示词+标签），供仓库详情页图片网格
export function listGenerations(repoId: string, embed: Embed) {
  return apiPost<{ items: Generation[] }>("/rag/generations", {
    repo_id: repoId,
    ...ragEmbed(embed),
  });
}

export function searchGenerations(
  repoIds: string[], query: string, embed: Embed, k = 64, outputDir = "",
) {
  return apiPost<{ items: Generation[] }>("/rag/search-generations", {
    repo_ids: repoIds, query, k, output_dir: outputDir, ...ragEmbed(embed),
  });
}

export type VisualPreferenceReason =
  "character" | "action" | "composition" | "lighting" | "color" | "quality" | "other";

export function recordVisualPreference(
  outputDir: string, repoId: string, winnerId: string, loserId: string,
  reason: VisualPreferenceReason,
) {
  return apiPost<{ ok: boolean; winner_score: number; loser_score: number }>(
    "/rag/visual-preference",
    { output_dir: outputDir, repo_id: repoId, winner_id: winnerId, loser_id: loserId, reason },
  );
}

export function setGenerationDescription(
  id: string, repoId: string, description: string, embed: Embed,
) {
  return apiPost<{ ok: boolean }>("/rag/set-generation-description", {
    id, repo_id: repoId, description, ...ragEmbed(embed),
  });
}

export function indexVisualGenerations(repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean; indexed: number; skipped: number }>(
    "/rag/index-visual-generations", { repo_id: repoId, ...ragEmbed(embed) },
  );
}

// 清理僵尸记录：指向本机留存图但磁盘文件已不存在的条目（手动删文件留下的裂图）。返回删除条数。
export function pruneGenerations(repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean; removed: number }>("/rag/prune-generations", {
    repo_id: repoId,
    ...ragEmbed(embed),
  });
}

// ===== 上网素材：联网搜索下载的图片 =====

export interface WebMaterial {
  path: string;
  url: string;         // local-view URL
  source_url: string;  // 来源网页
  title: string;
  filename: string;
  content?: string;    // 灵感卡内容（M1.4 统一展示用；图片素材为空）
}

export function listWebMaterials(outputDir: string) {
  return apiPost<{ items: WebMaterial[] }>("/comfyui/web-materials/list", {
    output_dir: outputDir,
  });
}

export function saveWebMaterial(outputDir: string, src: string, sourceUrl = "", title = "", threadId = "") {
  return apiPost<WebMaterial>("/comfyui/web-materials/save", {
    output_dir: outputDir,
    src,
    source_url: sourceUrl,
    title,
    thread_id: threadId,
  });
}

export function deleteWebMaterial(outputDir: string, filename: string) {
  return apiPost<{ ok: boolean }>("/comfyui/web-materials/delete", {
    output_dir: outputDir,
    filename,
  });
}

// ===== 灵感卡资产库（M1.4）：会话灵感卡升级为资产库可管理成员 =====

export interface InspirationCardImage {
  full_url: string;
  source_url: string;
  title?: string;
}

export interface InspirationCardAsset {
  id: string;
  title: string;
  content: string;
  sources: { title: string; url: string }[];
  images: { url: string; source_url: string; title: string }[];
  cover_url: string;
  created_at: string;
}

export function saveInspirationCard(
  outputDir: string,
  data: {
    cardId?: string;
    title: string;
    content: string;
    sources?: { title: string; url: string }[];
    images?: InspirationCardImage[];
    threadId?: string;
  },
) {
  return apiPost<InspirationCardAsset>("/comfyui/web-materials/inspiration/save", {
    output_dir: outputDir,
    card_id: data.cardId || "",
    title: data.title,
    content: data.content,
    sources: data.sources || [],
    images: (data.images || []).map((img) => ({
      full_url: img.full_url,
      source_url: img.source_url,
      title: img.title || "",
    })),
    thread_id: data.threadId || "",
  });
}

export function listInspirationCards(outputDir: string) {
  return apiPost<{ items: InspirationCardAsset[] }>("/comfyui/web-materials/inspiration/list", {
    output_dir: outputDir,
  });
}

export function getInspirationCard(outputDir: string, cardId: string) {
  return apiPost<InspirationCardAsset>("/comfyui/web-materials/inspiration/get", {
    output_dir: outputDir,
    card_id: cardId,
  });
}

export function updateInspirationCard(
  outputDir: string,
  data: {
    cardId: string;
    title?: string;
    content?: string;
    removeImageUrls?: string[];
  },
) {
  return apiPost<InspirationCardAsset>("/comfyui/web-materials/inspiration/update", {
    output_dir: outputDir,
    card_id: data.cardId,
    title: data.title,
    content: data.content,
    remove_image_urls: data.removeImageUrls || [],
  });
}

export function deleteInspirationCard(outputDir: string, cardId: string) {
  return apiPost<{ ok: boolean }>("/comfyui/web-materials/inspiration/delete", {
    output_dir: outputDir,
    card_id: cardId,
  });
}

// ===== 参考图：聊天上传到 <repo>/reference/ 的图片，供画布自动导入 =====

export interface ReferenceImage {
  path: string;
  url: string;
  title: string;
  filename: string;
}

export function listReferenceImages(outputDir: string, repoId: string) {
  return apiPost<{ items: ReferenceImage[] }>("/comfyui/reference-images/list", {
    output_dir: outputDir,
    repo_id: repoId,
  });
}

// 聚合仓库集合的标签→图片数量（按量降序），供加标签/搜索的输入补全
export interface TagStat { tag: string; count: number; }
export function tagStats(repoIds: string[], embed: Embed) {
  return apiPost<{ items: TagStat[] }>("/rag/tag-stats", {
    repo_ids: repoIds,
    ...ragEmbed(embed),
  });
}

// 覆盖某资产条目的标签（手动增删 / AI 打标落库）
export function setTags(id: string, repoId: string, tags: string[], embed: Embed) {
  return apiPost<{ ok: boolean }>("/rag/set-tags", {
    id, repo_id: repoId, tags,
    ...ragEmbed(embed),
  });
}

// 把提示词轻量切分成关键词标签（纯文本，非反推，省 token）
export function extractKeywords(
  text: string,
  chat: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ tags: string[] }>("/ai/extract-keywords", {
    text, ...chatBody(chat),
  });
}

// 删除单条（系统条目后端会拒绝）
export function deleteDoc(id: string, repoId: string, embed: Embed, removeFile = false) {
  return apiPost<{ ok: boolean }>("/rag/delete", {
    id, repo_id: repoId, remove_file: removeFile,
    ...ragEmbed(embed),
  });
}

// 编辑单条（系统条目后端会拒绝）
export function updateDoc(id: string, text: string, title: string, repoId: string, embed: Embed) {
  return apiPost<{ ok: boolean }>("/rag/update", {
    id, text, title, repo_id: repoId,
    ...ragEmbed(embed),
  });
}

// 右下角 AI 客服：检索（系统库 + 指定仓库库）流式回答。返回中止函数。
export function supportStream(
  message: string,
  repoId: string,
  chat: Embed,
  embed: Embed,
  onDelta: (text: string) => void,
  onDone: (err?: string) => void,
): () => void {
  return openSSE("/ai/support", {
    message, repo_id: repoId,
    ...chatBody(chat),
    embed_base_url: embed.baseUrl, embed_api_key: embed.apiKey, embed_model: embed.modelName,
  }, (obj) => {
    const event = decodeChatStreamEvent(obj);
    if (event.type === "delta") onDelta(event.text);
    else if (event.type === "error") throw new Error(event.message);
  }, onDone);
}
