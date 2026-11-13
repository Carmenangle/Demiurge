// 多 Agent 调用 wire（原 api/ai.ts AgentInvocation 段，2026-10-24 按资源域拆分）：
// 前端 camelCase 调用参数 → 后端 snake_case wire 的唯一序列化点；字段清单受
// generated/wireContracts（AGENT_INVOCATION_WIRE_FIELDS）合同锁约束。
import type { AgentInvocationWire } from "../../generated/wireContracts";
import { workspaceModeForWire, type WorkMode } from "../../lib/viewRouting";
import type { PresetSampling } from "../../stores/settings";
import { chatBody, sseEmbed, type Chat, type Embed } from "./wire";
import type { FileAttachmentMeta } from "./attachments";

// 2026-09-04：智能体参数预设（前端 camelCase）→ wire 字段（后端 snake_case）。
// 后端 _roleplay_sampling 读 ctx.preset_sampling，按键逐字段覆盖 builtin sampling。
function presetSamplingToWire(s: PresetSampling): Record<string, number> {
  return {
    temperature: s.temperature,
    top_p: s.topP,
    top_k: s.topK,
    frequency_penalty: s.frequencyPenalty,
    presence_penalty: s.presencePenalty,
    openai_max_context: s.openaiMaxContext,
    openai_max_tokens: s.openaiMaxTokens,
  };
}

export interface AgentInvocation {
  threadId: string;
  message: string;
  images: string[];
  /** 交互模式：默认 story（剧情）；chat=1:1 角色聊天；group_chat=群聊按成员顺序各回一次。 */
  interactionMode?: "story" | "chat" | "group_chat";
  /** 聊天室成员（角色卡名，顺序即群聊轮转顺序）；空=后端按绑定卡推断。 */
  chatMembers?: string[];
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
  searchProvider?: string; // 联网搜索源（""=自动回落链 bing-cn→ddg，见 lib/searchSource）
  routeModel?: string;
  providerProfile?: "openai_compatible" | "claude_compatible";
  messageId?: string;
  userMessageId?: string;
  storyTurn?: number;   // M1（2026-09-06）：剧情回合号（assistant 楼层绑定），删除/重生成不倒退
  styleTemplate: string;
  agentId: string;
  streamOutput: boolean;
  contextMaxTokens: number;
  compactTriggerPct: number; // 历史占用达有效预算该百分比即自动压缩（0=关闭；2026-09-11 条数废弃）
  selfhealAttempts: number;
  // 聊天室行为上限（设置→聊天室）；缺省由序列化层补默认值，兼容旧调用方。
  chatGuardMaxChars?: number;
  chatDefaultMaxTokens?: number;
  chatFollowupMax?: number;
  chatReopenMax?: number;
  chatInitiativeIdleMinutes?: number;
  chatInitiativeCooldownMinutes?: number;
  chatInitiativeDailyMax?: number;
  chatCadence?: "quiet" | "normal" | "frequent";
  chatActiveWindowMinutes?: number;
  chatMaxBotTurns?: number;
  chatMinAffinity?: number;
  chatQuietStart?: string;
  chatQuietEnd?: string;
  chatGroupActivity?: "quiet" | "normal" | "lively";
  // 2026-09-04 设置整合：智能体参数预设（温度/采样/输入输出上限），权威源=settings。
  presetSampling: PresetSampling;
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
  videoMode?: "climax" | "firstlast" | "shots";
  promptProfile: string;
  // 2026-10-25 提示词模版：promptProfile 为 custom:<id> 时同时报送其内置协议族
  //（模版被删时后端按该 kind 降级内置）；空=后端按默认 anima_tags。
  promptProfileKind?: string;
  // 视频提示词模版 id（builtin:<kind> / custom:<hex> / 空=内置默认骨架）
  videoPromptTemplateId?: string;
  // 视频工作流模版 id（空=未知；后端据此派生尾帧/参考图槽与默认时长）
  videoTemplateId?: string;
  // 全局模式分镜序列总时长预算（秒，0=后端按模板/正文估算）
  videoDurationHint?: number;
  appearanceSource: "worldbook" | "character_card";
  illustrationViewpoint?: "" | "first_person" | "third_person";
  illustrationBatchCount?: number;
  characterBaseImages: Record<string, string>;
  illustrationActorNames: string[];
  styleBaseImage: string;
  attachments?: FileAttachmentMeta[];
  approvalAction?: { approvalId: string; action: "submit" | "change" | "cancel"; editedPrompt?: string };
  routeAction?: { route: import("../../types/chat").AgentRoute; userMessageId: string };
  /** 聊天室：刚发完照片/换头像后的补话（/ai/chat/reaction）。 */
  reactionKind?: "" | "image" | "avatar";
  reactionPrompt?: string;
}

export function agentInvocationBody(request: AgentInvocation): AgentInvocationWire {
  return {
    thread_id: request.threadId,
    workspace_mode: workspaceModeForWire(request.workMode),
    interaction_mode: request.interactionMode || "story",
    chat_members: request.chatMembers || [],
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
    search_provider: request.searchProvider || "",
    route_model: request.routeModel || "",
    provider_profile: request.providerProfile || "openai_compatible",
    message_id: request.messageId || "",
    story_turn: request.storyTurn || 0,
    user_message_id: request.routeAction?.userMessageId || request.userMessageId || "",
    style_template: request.styleTemplate,
    agent_id: request.agentId,
    stream_output: request.streamOutput,
    context_max_tokens: request.contextMaxTokens,
    compact_trigger_pct: request.compactTriggerPct,
    selfheal_attempts: request.selfhealAttempts,
    chat_guard_max_chars: request.chatGuardMaxChars ?? 900,
    chat_default_max_tokens: request.chatDefaultMaxTokens ?? 0,
    chat_followup_max: request.chatFollowupMax ?? 3,
    chat_reopen_max: request.chatReopenMax ?? 2,
    chat_initiative_idle_minutes: request.chatInitiativeIdleMinutes ?? 120,
    chat_initiative_cooldown_minutes: request.chatInitiativeCooldownMinutes ?? 360,
    chat_initiative_daily_max: request.chatInitiativeDailyMax ?? 6,
    chat_cadence: request.chatCadence ?? "normal",
    chat_active_window_minutes: request.chatActiveWindowMinutes ?? 15,
    chat_max_bot_turns: request.chatMaxBotTurns ?? 5,
    chat_min_affinity: request.chatMinAffinity ?? 30,
    chat_quiet_start: request.chatQuietStart ?? "00:30",
    chat_quiet_end: request.chatQuietEnd ?? "08:30",
    chat_group_activity: request.chatGroupActivity ?? "normal",
    // 2026-09-04：智能体参数预设（settings 全局权威）→ wire 字段 preset_sampling。
    // 后端取 ctx.preset_sampling 覆盖 builtin sampling，温度/采样由 settings 决定，
    // 与 preset JSON 解耦。
    preset_sampling: presetSamplingToWire(request.presetSampling) as unknown as Record<string, string>,
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
    // 2026-10-25：falsy 时送 anima_tags（与后端 from_payload 默认一致）；
    // 旧值曾是已下线的 "krea2"（死默认，见提交说明）。
    prompt_profile: request.promptProfile || "anima_tags",
    prompt_profile_kind: request.promptProfileKind || "",
    video_prompt_template_id: request.videoPromptTemplateId || "",
    video_template_id: request.videoTemplateId || "",
    video_duration_hint: request.videoDurationHint || 0,
    appearance_source: request.appearanceSource,
    illustration_viewpoint: request.illustrationViewpoint || "",
    illustration_batch_count: request.illustrationBatchCount || 0,
    character_base_images: request.characterBaseImages,
    illustration_actor_names: request.illustrationActorNames,
    style_base_image: request.styleBaseImage,
    reaction_kind: request.reactionKind || "",
    reaction_prompt: request.reactionPrompt || "",
    attachments: (request.attachments || []).map((a) => ({
      file_id: a.fileId,
      name: a.name,
      mime: a.mime,
      size: a.size,
    })),
  };
}
