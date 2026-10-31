// 聊天会话引擎：从 ChatView 抽出的「持久化 + 生成编排」深簇。
// 拥有 messages / 生成生命周期 reducer / 各类 ref 与加载·落盘 effect，
// 对外只暴露渲染所需的状态与动作句柄；UI 局部态（模型选择/面板开关/尺寸）留在 ChatView。
// 接口即测试面：整台生成引擎集中一处，不必渲染千行组件即可推演其行为。
import { type MutableRefObject, useEffect, useReducer, useRef, useState } from "react";
import type { AgentRoute, ChatMessage, MsgPart, PromptApproval, RegenerationSnapshot, RouteChoice, WorkflowRegeneration } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings } from "../stores/settings";
import { activeUserPersona } from "../stores/settings";
import type { RichContent } from "../components/RichInput";
import { emitRagStatus } from "../components/RagToast";
import type { Template } from "../api/workflows";
import type { PromptTemplate } from "../api/ai/promptTemplates";
import { promptTemplateInstructions } from "./promptTemplates";
import {
  comfyStatus, startComfy, submitGraph, submitWorkflow, interruptComfy,
  finalizeGeneration as persistWorkflowGeneration,
  moveComfyOutputToInput, uploadRemoteImageToInput,
  type GenResult,
} from "../api/comfyui";
import { listLoras } from "../api/loras";
import {
  multiAgent, sealMemory,
  saveSnapshot, fetchSnapshot, fetchAgentRunning, cancelAgent,
  fetchInspiration, regenerateImage as replayImageGeneration,
  claimIllustrationSubmission, reportIllustrationFailure, reportIllustrationSubmission,
  editMessageRemote,
  genFramePrompts, genProfilePrompt, listGenerations,
  type AgentInvocation,
} from "../api/ai";
import { runVisualCiDiagnostic } from "../api/visualCi";
import { refreshChatBackgroundActivities } from "./chatBackgroundActivity";
import type { ChatStreamEvent, IllustrationSceneSpec, VideoParams } from "../api/chatStreamProtocol";
import {
  reduce as reduceGen, initialGenState,
  blocksDialogueSubmission, streamingBotId, needsConfirm, runningPromptId,
} from "./generationLifecycle";
import { useWorkflowOrchestration } from "./workflowOrchestration";
import { subscribeProgress } from "./comfyProgress";
import { joinForeground, leaveForeground } from "./foregroundRunQueue";
import {
  needsImageInput, hasImageProvided, pickBestText,
  promptHistory,
  prepareConversationRegeneration, resolveLoraPromptMetadata, resolveGenerationPrompt,
  resolveVideoBaseImageRef, resolvePrevTailDesc,
  resolveTransitionBaseImage,
} from "./chatGeneration";
import { waitForAgentRelease } from "./agentReleaseWait";
import {
  durableFinalizeSucceeded, getRuntime, threadKey, pollWorkflowResult,
  type PendingGeneration, type WorkflowWatchObserver,
} from "./workflowGenerationRuntime";
import {
  applyProfileLoraTriggers, ensureAnimaIllustrationStyle, illustrationTemplateValues, normalizePromptProfile,
  replacePromptQualityLine, latentSizeFor, workflowFieldBinding,
  isCustomPromptProfile,
} from "./imagePromptProfiles";
import {
  illustrationLoraConfigurationError, illustrationRequestMedia, illustrationWorkflowMedia,
  resolveIllustrationActors, resolveVideoMode, resolveVideoTemplateChoice,
  planFirstlastFrameTasks, firstlastFrameValues, firstlastSlotLayout, transitionVideoValues,
} from "./illustrationMedia";
import {
  applyRouteChoice, appendImageSlot, applyInterruptedStop, bindMediaSlotPrompt, markMediaSlotFailed, appendTransitionSlot,
  failMediaSlot, reduceChatStreamEvent, resetMediaSlotForRetry, resolveMediaSlot,
  upsertMessages, workflowMessages, type SlotPlacement,
} from "./chatSessionEvents";
// C4-A Step 6：媒体种类推导（审计 R8）——工作流出图/出视频的超时合同由抓取图节点类名决定，
// 硬编码 "image" 会让视频模板吃图片的 20 分钟硬上限。纯函数，无副作用、无循环依赖。
import { deriveWorkflowMediaType } from "./canvasWorkflowRun";
import { recoverAgentRun, shouldRecoverAgentRun } from "./agentRecovery";
import { deletedMessageTombstones } from "./deletedMessageTombstones";
import { nextStoryTurnNo, rememberStoryTurn, resolveStoryTurn, sealTurnsOf, syncStoryTurnHighWater } from "./storyTurn";

import { releaseAgentStream, type ActiveAgentStream } from "./agentStreamLifecycle";
import type { ImageQuality, WorkMode } from "./viewRouting";
import { useIllustrationChain } from "./useIllustrationChain";
import { useAudioTracks } from "./useAudioTracks";
import { usePlanTaskFeed } from "./usePlanTaskFeed";
import { useWorkflowResume } from "./useWorkflowResume";
import { useSessionLoad } from "./useSessionLoad";
import { useChatMaintenance } from "./useChatMaintenance";
import { useSessionPersist } from "./useSessionPersist";
import { useWfProgressShard } from "./useWfProgressShard";
import { clearHomeDraft, writeHomeDraft } from "./homeDraft";
import {
  comfyRegenerationUrl, legacyGenerationPrompt, overrideRegenerationPrompt,
  resolveImageRegenerationModel, templateRegenerationSnapshot, workflowRegenerationSnapshot,
  workflowGenMetadata,
} from "./regeneration";
import { effectiveGlobalProxyUrl, globalProxyAddress, resolveEndpointProxy, resolveModelProxy, type ProxyMode } from "./modelProxy";
import { searchProviderWireValue } from "./searchSource";
import {
  ConversationHistoryRuntime, type ConversationCheckpoint,
} from "./conversationHistoryRuntime";
import { useChatAgentQueue } from "./useChatAgentQueue";

export type Model = { baseUrl: string; apiKey: string; modelName: string; proxyMode?: ProxyMode; providerProfile?: "openai_compatible" | "claude_compatible" };

export type Checkpoint = ConversationCheckpoint;

// 斜杠指令大小写兼容：只把开头的指令词转小写，参数（模板名/主题）保留原样。
const normCmd = (text: string): string => {
  if (!text.startsWith("/")) return text;
  const sp = text.indexOf(" ");
  return sp === -1 ? text.toLowerCase() : text.slice(0, sp).toLowerCase() + text.slice(sp);
};

export interface ChatSessionDeps {
  repo?: Repo;
  settings: ReturnType<typeof useSettings>["settings"];
  setGeneratedCover: (id: string, cover: string) => void;
  chat: Model;                                   // 当前对话模型（智能体大脑+反推）
  genModel: Model;                               // 当前生图模型
  videoModel?: Model;                            // 当前视频模型（videoModels）
  workMode: WorkMode;                            // 编辑模式由后端直达受限作品文件 Agent
  size: string;                                  // 生图尺寸 "宽x高"
  imageQuality: ImageQuality;                    // GPT Image 质量档；不支持的模型由后端省略
  templates: Template[];
  // 2026-10-25 提示词模版：全局选中的生图模版决定 styleTemplate（自定义=追加段）；
  // 与工作流模板同款——由 ChatView 加载后注入，本 hook 不自己拉。
  promptTemplates?: PromptTemplate[];
  setShowPicker: (v: boolean) => void;           // 与 /w 选择浮层共享
  atBottomRef: MutableRefObject<boolean>;        // 与滚动跟随 UI 共享
  onNotify?: (msg: string, kind?: "info" | "error" | "success") => void; // 轻提示（对齐 WorkflowCard onNotify）
}

/**
 * 自动插画提交参数（2026-09-14 收口，架构审计 C7）。
 *
 * 此前是 **17 个位置参数** + 失败槽快照按签名序存数组 + 重试用魔术下标 `args[7]="manual"`
 * 翻 `source`——签名插一个参数就静默错位，且 TS 不会报错（数组按 `Parameters<...>` 展开）。
 * 现收成一个对象：调用点自解释，重试快照就是本对象本身，`source` 按名覆盖。
 */
export interface IllustrationSubmit {
  prompt: string;
  motion?: number;
  actors?: string[];
  messageId: string;
  slotId: string;
  sceneSpec?: IllustrationSceneSpec;
  turnId?: string;
  source?: "automatic" | "manual";
  eventVideoMode?: string;
  firstFrameDesc?: string;
  lastFrameDesc?: string;
  prevTailDesc?: string;
  lastFrameUrl?: string;
  videoPrompt?: string;
  videoParams?: VideoParams;
  transition?: string;
  transitionVideoPrompt?: string;
  transitionVideoParams?: VideoParams;
  // 2026-10-18 试跑成稿直送（用户定案）：试跑（promptOnly）上报时把**编译成稿**写进
  // 快照，「重新生成」据此逐字提交 ComfyUI（不再重编译）。普通失败槽无这两键 ⇒ 保持
  // 2026-08-30「manual 重试必须重编译」语义（编译链升级能生效）。
  // finalPrompt＝主图/正片成稿；finalFramePrompts＝首尾帧双帧成稿（首尾帧模式主槽不产成稿）。
  finalPrompt?: string;
  finalFramePrompts?: { first?: string; last?: string };
  // 高潮点「图 + 视频」两段链（2026-10-31 用户定案「应该支持重新生成这两个……对应视频的
  // 槽位就在图片下方」）：主槽只出图，出图成功后在其正下方追加 `:video` 副槽，再用
  // `videoOnly` 递归提交视频——两个槽各有自己的重试快照，互不牵连。
  // videoOnly=true ⇒ 本次只提交视频（提示词逐字用 videoPrompt，不再走 Profile 编译）；
  // videoBaseUrl=首帧图的可取回地址（成图的 ComfyUI view URL，交底图上传段搬到视频机）。
  videoOnly?: boolean;
  videoBaseUrl?: string;
  // 首帧底图来源槽（= 主图槽）：视频入库的 derived_from 用它，不再反查消息
  // （反查会撞上「主槽还没回填 ready」的时序，把视频记到上一楼层的成图头上）。
  videoBaseSlotId?: string;
  // 第 74 刀（2026-10-31 用户定案「前面的图片放首帧、后面的图片放尾帧生成视频」）：
  // 首尾帧多批次时，后端按**正文序**为每张桥图标注「上一张画面的槽」，随事件下发。
  // 桥视频 = 图片1(首帧)←上一张成图、图片2(尾帧)←本槽成图。
  // bridgePrevSlotId＝上一张画面的槽 id（只主槽第一次提交带，重试快照随之保留）；
  // bridgeVideo＝本次提交是「段桥衔接视频」（视频槽自己的递归，提示词逐字用转场成稿）；
  // bridgePrevImageUrl＝已取回到的上一张成图地址（递归时直接带上，避免二次等待）。
  bridgePrevSlotId?: string;
  bridgeVideo?: boolean;
  bridgePrevImageUrl?: string;
}

// PLACEHOLDER_BODY

export function useChatSession(deps: ChatSessionDeps) {
  const {
    repo, settings, setGeneratedCover, chat, genModel, videoModel, workMode, size, imageQuality,
    templates, setShowPicker, atBottomRef, onNotify,
    promptTemplates = [],
  } = deps;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messagesRef = useRef<ChatMessage[]>(messages);
  const scenarioSnapshotTurnRef = useRef(-1);
  messagesRef.current = messages;
  // 破坏性操作确认弹窗：由 UI 渲染 ConfirmModal，用户选择后 resolve 这个 promise
  const [confirmReq, setConfirmReq] = useState<{ message: string; resolve: (ok: boolean) => void } | null>(null);
  const askConfirm = (message: string) =>
    new Promise<boolean>((resolve) => {
      setConfirmReq({
        message,
        resolve: (ok) => { setConfirmReq(null); resolve(ok); },
      });
    });
  // 生成生命周期单一真相源（reducer）：取代原 streamingId/wfRunning/imgStartedRef/pendingPromptRef + queueRef 影子镜像。
  const [gen, dispatch] = useReducer(reduceGen, initialGenState);
  // 只读派生别名
  const streamingId = streamingBotId(gen);             // 正在流式的 bot 气泡 id（渲染转圈用）
  const wfRunning = runningPromptId(gen) !== null;      // /s 工作流进行中；与 Agent 通道正交
  // 多元数据插入：插画开 且 本作品已预设图片/视频 ComfyUI 模板 → 高潮点走异步 ComfyUI 闭环（后端发 illustrate_request）
  const mediaPreset = settings.mediaInsert?.[repo?.id || ""];
  const comfyIllustrate = !!(settings.illustrate && (mediaPreset?.templateId || mediaPreset?.videoTemplateId));
  // 音频对白配音：剧情自动生成开 且 预设了音频模板（IndexTTS）→ 后端发 audio_request，前端逐角色配音
  const comfyAudio = !!(settings.illustrate && mediaPreset?.enableAudio && mediaPreset?.audioTemplateId);
  // 视频链独立开关：预设了视频工作流模板 → 后端才编译 video_request（含动作提取 LLM 调用）；
  // 未配模板（mediaPreset 无 videoTemplateId）→ 不发请求、不调 LLM，零 token（与图/音链同构）。
  const comfyVideo = !!(settings.illustrate && mediaPreset?.videoTemplateId);
  const promptProfile = normalizePromptProfile(mediaPreset?.promptProfile);
  // 2026-10-25 提示词模版：custom 模版的内置协议族（模版被删时后端按它降级）。
  // 选择时随预设落盘（promptProfileKind）；已加载列表里能查到就以后者为准。
  const promptProfileKind = isCustomPromptProfile(promptProfile)
    ? (promptTemplates.find((t) => `custom:${t.id}` === promptProfile)?.kind
       || mediaPreset?.promptProfileKind || "")
    : "";
  // 视频提示词模版（按模式派发：kind 不匹配 ⇒ 后端用内置默认骨架）
  const videoPromptTemplateId = mediaPreset?.videoPromptTemplateId || "";
  const cardNames = repo?.cardNames?.length ? repo.cardNames : (repo?.cardName ? [repo.cardName] : []);
  const openingCardName = repo?.openingCardName || repo?.cardName || cardNames[0] || "";
  const appearanceSource = mediaPreset?.appearanceSource === "character_card" ? "character_card" : "worldbook";
  // 画面人称手选（2026-09-20）：预设里存 first_person / third_person；空 = 交给后端自动判定。
  const illustrationViewpoint = mediaPreset?.viewpoint === "first_person"
    || mediaPreset?.viewpoint === "third_person" ? mediaPreset.viewpoint : "";
  // 高潮点批量生图数目（2026-09-24）：面板最小 1、无上限；未设/非法 ⇒ 0（后端按 1 张处理）。
  // 2026-10-31 用户定案：首尾帧也走多批次（每张与前一张接成段桥视频）⇒ **两种模式都透传**。
  const illustrationBatchCount = Number.isFinite(mediaPreset?.batchImageCount)
    && (mediaPreset?.batchImageCount as number) >= 1
    ? Math.floor(mediaPreset?.batchImageCount as number) : 0;
  const modelProxies = {
    chatProxyUrl: resolveEndpointProxy(chat.baseUrl, chat.proxyMode, globalProxyAddress(settings), settings.proxyEnabled),
    genProxyUrl: resolveModelProxy(genModel.proxyMode, globalProxyAddress(settings), settings.proxyEnabled),
    videoProxyUrl: resolveModelProxy(videoModel?.proxyMode, globalProxyAddress(settings), settings.proxyEnabled),
    embedProxyUrl: resolveEndpointProxy(
      settings.embedModel.baseUrl, settings.embedModel.proxyMode,
      globalProxyAddress(settings), settings.proxyEnabled,
    ),
  };
  const embedModel = { ...settings.embedModel, proxyUrl: modelProxies.embedProxyUrl };
  // ⑥ 云端（gpt-image 系）出图无 ComfyUI 模板：把角色→底图映射传后端，按在场角色取底图锁一致性。
  const { characterBaseImages, illustrationActorNames, styleBaseImage } =
    illustrationRequestMedia(mediaPreset, cardNames);
  // 有效用户人设：仓库绑定了 personaId 则用该档（并标 personaBound，后端不被作品快照覆盖）；否则用全局选中档。
  const boundPersona = repo?.personaId
    ? (settings.userPersonas || []).find((p) => p.id === repo.personaId)
    : undefined;
  const effectivePersona = boundPersona || activeUserPersona(settings);
  const personaBound = !!boundPersona;
  // 绑定的独立世界书（worldbookDir 下的 .json 名）：与卡内嵌世界书合并注入（后端处理）。
  const worldbookDir = settings.worldbookDir || "";
  const worldbookName = repo?.worldbookName || "";
  // 对话线 id 单点推导（P17）：仓库 id；首页（无仓库）统一 "home"。
  // 后端按此落盘多轮记忆与 RAG 知识库；runtime / 任务恢复 / 后台面板的线程键也是它。
  // 必须在下面的分片 hook 之前声明——首次挂载就要按当前线程读分片（P18）。
  const threadId = threadKey(repo?.id);
  // 工作流进度的「跨导航分片」状态（T2.2-B4-a 外移 → lib/useWfProgressShard.ts）：分片 Map / 两条 useState /
  // 包装 setter / 切线程重对齐 effect 逐字随体迁入；调用点＝原 `useState` 位置（hook 调用序＝状态初始化序）。
  const { wfProgress, wfNode, updateWfProgress, updateWfNode } = useWfProgressShard(threadId);
  const [regeneratingIds, setRegeneratingIds] = useState<Set<string>>(new Set());
  const wsUnsubRef = useRef<(() => void) | null>(null);  // 当前进度 WS 退订
  const activePromptQueue = useRef<string[]>([]);  // FIFO 队列：首个 = 当前显示进度条；多个 = 排队中
  const abortRef = useRef<ActiveAgentStream | null>(null);  // 仅显式停止才中断；导航离开保持后台运行
  // React reducer 要到下一次渲染才可见；同步 ref 封住连续点击/回车的同一帧竞态。
  const agentBusyRef = useRef(false);
  const bgRunningRef = useRef(false);  // 后台任务进行中：此时后端拥有快照写权，前端不抢写以免覆盖
  // 慢守望阶段（releaseBusy 后仍在轮询）：wfRunning 已 false，但仍需显示停止键
  const [slowWatchPromptId, setSlowWatchPromptId] = useState<string | null>(null);
  // 工作流上传/提交阶段（点击「运转工作流」→ submitGraph 返回前）：按钮显示「上传中…」
  const [uploadingWf, setUploadingWf] = useState(false);
  // 切换仓库/线程时重置上传状态（submitGraph 挂起后切线程不会残留 true）
  useEffect(() => { setUploadingWf(false); }, [threadId]);
  // 队列任务完成回调：headless 执行的回复落盘后，用它刷新对话区（定义在下方 reloadFromSnapshot，
  // 用 ref 转发避免 hook 顺序问题）。
  const reloadFromSnapshotRef = useRef<(() => void) | undefined>(undefined);
  const {
    queued, enqueue: enqueueQueued, remove: removeQueued,
  } = useChatAgentQueue(threadId, reloadFromSnapshotRef);
  const createAgentInvocation = (
    message: string,
    images: string[],
    history: AgentInvocation["history"],
    overrides: Partial<AgentInvocation> = {},
  ): AgentInvocation => ({
    threadId,
    message,
    images,
    workMode,
    chat,
    gen: genModel,
    video: videoModel,
    embed: embedModel,
    size,
    imageQuality,
    outputDir: settings.outputDir,
    repoId: repo?.id || threadId,
    proxyUrl: effectiveGlobalProxyUrl(settings),
    ...modelProxies,
    searchProvider: searchProviderWireValue(settings.searchProvider),
    // 2026-10-25：旧「风格存档」由提示词模版接管——选中自定义模版时其追加段作为
    // styleTemplate 透传（AI 模仿其组织形态）；内置/未选 = 空（按模型家族自动判）。
    styleTemplate: promptTemplateInstructions(settings.promptTemplateId || "", promptTemplates),
    agentId: settings.activeAgentId || "",
    streamOutput: settings.streamOutput,
    contextMaxTokens: settings.contextMaxTokens,
    compactTriggerPct: settings.compactTriggerPct,
    selfhealAttempts: settings.selfhealAttempts,
    // 2026-09-04 设置整合：智能体参数预设（settings 全局权威，温度/采样/输入输出上限）
    presetSampling: settings.presetSampling,
    // 2026-10-20 方案 C：主对话此前**从不传代理**（`chatProxyUrl` 算了却被丢掉），
    // 而后端 `build_model(proxy="")` 走「完全默认构造」⇒ 看起来像"自由切换"，
    // 实际是"从未使用"。填表/插画 profile 却照传 ⇒ 两条路径行为割裂
    // （实锤：同一模型对话能通、填表 `Connection error`）。
    // 现在三路共用 `modelProxies.chatProxyUrl` 这一个来源。
    chatProxyUrl: modelProxies.chatProxyUrl,
    providerProfile: chat.providerProfile || "openai_compatible",
    history,
    characterDir: settings.characterDir,
    cardName: openingCardName,
    cardNames,
    openingCardName,
    presetDir: settings.presetDir,
    presetName: settings.activePresetName,
    userName: effectivePersona.name,
    userPersona: effectivePersona.content,
    personaBound,
    worldbookDir,
    worldbookName,
    illustrate: settings.illustrate,
    comfyIllustrate,
    comfyAudio,
    comfyVideo,
    // 视频模式由「首尾帧生成」选项推导（用户定稿 2026-08-28）；旧预设无该字段时退 videoMode
    videoMode: mediaPreset?.firstlast ? "firstlast" : mediaPreset?.videoMode,
    promptProfile,
    promptProfileKind,
    videoPromptTemplateId,
    appearanceSource,
    illustrationViewpoint,
    illustrationBatchCount,
    characterBaseImages,
    illustrationActorNames,
    styleBaseImage,
    ...overrides,
  });
  // 每个 threadId 唯一实例（`getRuntime` 内部按 threadId 缓存）：画布运转标记与对话恢复
  // 读写同一个 `laf_pending_gen_<threadId>`，必须共享同一份 finalized/cancelled 去重集合，
  // 否则同一个任务会被归档两次（2026-09-15 C4-A Step 2）。
  // P17（2026-09-15）：`threadId` 已由 `threadKey` 单点推导（见上方）——首页 = "home"，
  // 与画布/弹窗的键一致，故首页在途任务也能被这里恢复、并进后台活动面板（面板只扫非 home 键，属既有约定）。
  const workflowRuntime = getRuntime(threadId);
  const activeThreadRef = useRef(threadId);
  activeThreadRef.current = threadId;
  const recoveryTokenRef = useRef(0);
  const recoveryActiveRef = useRef(false);
  const chatKey = `laf_chat_${threadId}`;
  const historyRuntimeRef = useRef<{
    threadId: string;
    runtime: ConversationHistoryRuntime;
  } | null>(null);
  if (historyRuntimeRef.current?.threadId !== threadId) {
    historyRuntimeRef.current = {
      threadId,
      runtime: new ConversationHistoryRuntime(threadId, localStorage),
    };
  }
  const historyRuntime = historyRuntimeRef.current.runtime;
  historyRuntime.bind({
    current: () => messagesRef.current,
    publish: (next) => {
      messagesRef.current = next;
      setMessages(next);
    },
    persist: (next) => {
      if (threadId === "home") {
        writeHomeDraft(next);
        return;
      }
      try { localStorage.setItem(chatKey, JSON.stringify(next)); } catch { /* ignore quota */ }
      void saveSnapshot(threadId, next).catch(() => {});
    },
  });
  const loadedRef = useRef(false);  // 标记本仓库消息已加载，避免初始空数组覆盖已存记录
  const snapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);  // 后端快照防抖
  const pushBot = (text: string) =>
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: "assistant", text, system: true }]);
  // 通用：追加一条任意消息（多 Agent 模式用，可带 user 角色 / 图片）
  const pushMsg = (msg: Partial<ChatMessage>) =>
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: "assistant", text: "", ...msg } as ChatMessage]);

  const startAgentRecovery = (targetThread = threadId, targetRepoId = repo?.id) => {
    if (!shouldRecoverAgentRun(targetThread)) return;
    if (activeThreadRef.current !== targetThread || recoveryActiveRef.current) return;
    const token = ++recoveryTokenRef.current;
    recoveryActiveRef.current = true;
    agentBusyRef.current = true;
    bgRunningRef.current = true;
    const knownMedia = new Set(messages.flatMap((message) => [message.image, message.video].filter(Boolean)));
    let recoveredMedia = "";

    void recoverAgentRun({
      fetchSnapshot: () => fetchSnapshot(targetThread) as Promise<{ items: ChatMessage[] }>,
      fetchRunning: () => fetchAgentRunning(targetThread),
      isActive: () => activeThreadRef.current === targetThread && recoveryTokenRef.current === token,
      onSnapshot: (rawItems) => {
        // ★ 墓碑过滤：恢复轮询与删除落库存在竞态，旧快照晚到会把刚删的消息 upsert 回
        //   状态（画布楼层复活/对话消息删不掉）。已删消息不得复活（架构合同）。
        const items = deletedMessageTombstones.filterDeleted(targetThread, rawItems);
        for (const message of items) {
          const media = message.image || message.video;
          if (media && !knownMedia.has(media)) recoveredMedia = media;
        }
        setMessages((current) => upsertMessages(current, items));
      },
    }).then((settled) => {
      if (!settled || activeThreadRef.current !== targetThread) return;
      if (recoveredMedia && targetRepoId) setGeneratedCover(targetRepoId, recoveredMedia);
      if (recoveredMedia) {
        window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: targetThread }));
      }
    }).finally(() => {
      if (recoveryTokenRef.current !== token) return;
      recoveryActiveRef.current = false;
      agentBusyRef.current = false;
      bgRunningRef.current = false;
    });
  };

  const cancelPendingSnapshot = () => {
    if (!snapTimer.current) return;
    clearTimeout(snapTimer.current);
    snapTimer.current = null;
  };
  const {
    compact, compacting, clearCache: clearCacheAction,
    contextReminder, dismissContextReminder,
  } = useChatMaintenance({
    threadId,
    messages,
    setMessages,
    isBusy: !!streamingId || wfRunning,
    isStreaming: !!streamingId,
    chat,
    embed: embedModel,
    outputDir: settings.outputDir,
    reminderTokens: settings.contextReminderTokens,
    askConfirm,
    cancelPendingSnapshot,
  });

  // 进入仓库/切换时加载消息（T2.2-B3-b 外移 → lib/useSessionLoad.ts）：effect 体与注释逐字随体迁入，
  // 闭包符号以同名 deps 注入；依赖数组 `[threadId]` 不变（重订阅时机＝行为面），调用点＝原 effect 位置。
  useSessionLoad({
    threadId, chatKey, repo, setMessages, loadedRef, messagesRef, recoveryActiveRef,
    activeThreadRef, recoveryTokenRef, agentBusyRef, bgRunningRef, abortRef,
    scenarioSnapshotTurnRef, snapTimer, wsUnsubRef, workflowRuntime, startAgentRecovery,
    settings, openingCardName, effectivePersona, dispatch,
  });
  // APPEND_HERE

  // 计划采集产物「逐张实时上屏」订阅（T2.2-B1 外移 → lib/usePlanTaskFeed.ts）：effect 体与注释逐字随体迁入，
  // 闭包符号以同名 deps 注入；依赖数组 `[threadId]` 不变（重订阅时机＝行为面），调用点＝原 effect 位置。
  usePlanTaskFeed({ threadId, recoveryActiveRef, agentBusyRef, activeThreadRef, setMessages });

  // 消息持久化链（T2.2-B4-b 外移 → lib/useSessionPersist.ts）：`persistDataUri` / `slimSnapshot` /
  // 600ms 防抖持久化 effect 与注释逐字随体迁入，闭包符号以**同名 deps** 注入；依赖数组逐字不变
  //（重订阅时机＝行为面），调用点＝原 effect 位置（hook 调用序＝ effect 执行序）。
  useSessionPersist({
    threadId, chatKey, repo, settings, messages, setMessages, loadedRef, messagesRef,
    activeThreadRef, agentBusyRef, bgRunningRef, snapTimer, scenarioSnapshotTurnRef,
    streamingId, regeneratingIds,
  });

  // 选中模板 → 在对话流插入工作流节点卡（卡内嵌锁定画布）
  const pickTemplate = (t: Template) => {
    const card: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      text: "",
      workflow: {
        templateId: t.id,
        templateName: t.name,
        draftGraph: null,
        capturedGraph: null,
        done: false,
      },
    };
    setMessages((m) => [...m, card]);
    setShowPicker(false);
    // ★ 无论 /w 命令还是模板选择器都派发：画布（挂载时由 CanvasStageFlow 消费，未挂载由 ChatView 兜底
    //   写 globalPendingToolCreates，切画布时消费）同步创建 workflow-tool 工具卡——否则对话模式 /w
    //   建的卡在画布模式永远看不到（根因：事件只在选择器路径派发过）。
    try {
      window.dispatchEvent(new CustomEvent("laf-canvas-workflow-tool", {
        detail: {
          templateId: t.id,
          templateName: t.name,
          estimatedNodeCount: (t.node_order || []).length,
        },
      }));
    } catch { /* 非关键事件，失败不影响对话流 */ }
  };

  const updateCardDraft = (msgId: string, draftGraph: unknown) =>
    setMessages((messages) => messages.map((message) =>
      message.id === msgId && message.workflow
        ? { ...message, workflow: { ...message.workflow, draftGraph } }
        : message,
    ));

  // 「选择完毕」：原子存下最终 UI 草稿和原生 API prompt，并标记完成
  const markCardDone = (msgId: string, draftGraph: unknown, capturedGraph: unknown) =>
    setMessages((ms) =>
      ms.map((m) =>
        m.id === msgId && m.workflow
          ? { ...m, workflow: { ...m.workflow, draftGraph, capturedGraph, done: true } }
          : m,
      ),
    );

  // 「更改」：把已确认的卡重置为未完成
  const markCardReopen = (msgId: string) =>
    setMessages((ms) =>
      ms.map((m) =>
        m.id === msgId && m.workflow
          ? { ...m, workflow: { ...m.workflow, done: false } }
          : m,
      ),
    );

  // 按名称选模板（/w 名称）
  const pickByName = (name: string) => {
    const t = templates.find((x) => x.name === name) || templates.find((x) => x.name.includes(name));
    if (t) pickTemplate(t);
    else pushBot(`没找到名为「${name}」的模板。输入 /w 查看可选模板。`);
  };
  // APPEND2_HERE

  // 把一次已完成的生成结果交给后端统一留存，再投影为消息。返回是否产出了内容。
  const finalizeGeneration = async (
    r: GenResult, pendingItem: PendingGeneration,
  ): Promise<boolean> => {
    const best = pickBestText(r.texts);
    if ((r.images?.length || 0) === 0 && (r.videos?.length || 0) === 0
        && (r.audios?.length || 0) === 0 && !best) return false;
    const promptId = pendingItem.prompt_id;
      const owner = pendingItem.owner || {
        threadId, repoId: repo?.id || "home", outputDir: settings.outputDir,
      };
      const savedRegeneration = pendingItem?.regeneration;
      const target = pendingItem?.target;
      const generationPrompt = resolveGenerationPrompt(pendingItem?.prompt, savedRegeneration, best);
      const regeneration = savedRegeneration?.kind === "workflow" || savedRegeneration?.kind === "template"
        ? { ...savedRegeneration, prompt: generationPrompt }
        : savedRegeneration;
      // 归档要回**提交那台机**取产物（`pendingItem.comfyuiUrl` 为提交时固化）；
      // 旧 pending / 画布路径没有该字段 → 退回快照地址、再退回入口 0。
      const comfyuiUrl = pendingItem?.comfyuiUrl
        || comfyRegenerationUrl(regeneration) || settings.comfyuiUrl;
      const result = await persistWorkflowGeneration({
        threadId: owner.threadId,
        repoId: owner.repoId,
        promptId,
        prompt: generationPrompt,
        images: r.images || [],
        videos: r.videos || [],
        audios: r.audios || [],
        outputDir: owner.outputDir,
        comfyuiUrl,
        embed: embedModel,
        chat,
        regeneration,
        templateName: savedRegeneration?.kind === "workflow" ? savedRegeneration.templateName : undefined,
        modelName: savedRegeneration?.kind === "workflow" ? savedRegeneration.modelName : undefined,
        loraNames: savedRegeneration?.kind === "workflow"
          ? savedRegeneration.loraNames
          : savedRegeneration?.kind === "template"
            ? savedRegeneration.loras?.map((l) => l.name)
            : undefined,
        target,
        baseSlotRef: pendingItem.baseSlotRef,
      });
      if (!durableFinalizeSucceeded(result)) {
        throw new Error("生成已完成，但原图或会话槽尚未持久化，将自动重试归档");
      }
      if (target && result.target?.url) {
        const firstGen = result.images?.[0]?.message_id || "";
        setMessages((current) => resolveMediaSlot(
          current, target.messageId, target.slotId, result.target!.url,
          result.target!.media_type, regeneration, firstGen,
        ));
        if (result.target.media_type === "image" && owner.repoId !== "home") {
          setGeneratedCover(owner.repoId, result.target.url);
        }
        // 自动插画落库后触发 Visual CI 验收诊断（非阻断，fire-and-forget）
        if (firstGen && result.target.media_type === "image" && owner.repoId !== "home") {
          // 角色回归参考图：取当前作品角色底图的第一张作为相似度基准（顾凝霜/苏映晚等）
          const referenceImageForCi =
            (Object.values(characterBaseImages || {}).find((url) => !!url) as string) || "";
          // VLM 用设置中的「视觉大模型」配置；未配置时跳过 VLM 检测（仅机械检查）
          const vlm = settings.vlmModel;
          const vlmConfig = vlm && (
            vlm.mode === "local"
              ? (vlm.ollamaName ? {
                  base_url: "http://localhost:11434/v1",
                  api_key: "ollama",
                  model: vlm.ollamaName,
                  proxy: "",
                } : null)
              : (vlm.baseUrl && vlm.modelName ? {
                  base_url: vlm.baseUrl,
                  api_key: vlm.apiKey || "",
                  model: vlm.modelName,
                  // 视觉大模型可选本机 Ollama：回环端点必须直连（与对话/嵌入同一口径）。
                  proxy: resolveEndpointProxy(vlm.baseUrl, vlm.proxyMode, globalProxyAddress(settings), settings.proxyEnabled),
                } : null)
          );
          void runVisualCiDiagnostic({
            generationId: firstGen,
            repoId: owner.repoId,
            outputDir: owner.outputDir,
            turnId: "",
            // 关键：必须带图片 URL，VLM 才能读取生成图做语义审计
            generationRecord: {
              prompt: generationPrompt,
              url: result.target!.url,
              display_url: result.target!.url,
            },
            referenceImageUrl: referenceImageForCi,
            // 未配置视觉大模型时不传 chat → 后端跳过 VLM，仅机械 Trace 检查
            chat: vlmConfig || undefined,
          }).catch(() => undefined);
        }
        if (result.durable && result.images.some((image) => image.indexed)) {
          window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: owner.threadId }));
        }
        return true;
      }
      const blocks = workflowMessages(result.messages);
      // ★ 墓碑过滤：工作流回灌消息若曾被用户删除（如删除后同一 generation 重 finalize），不得复活
      setMessages((current) => upsertMessages(current, deletedMessageTombstones.filterDeleted(threadId, blocks)));
      const firstImage = blocks.find((message) => message.image)?.image;
      if (firstImage && owner.repoId !== "home") setGeneratedCover(owner.repoId, firstImage);
      if (result.durable && result.images.some((image) => image.indexed)) {
        window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: owner.threadId }));
      }
      return blocks.length > 0;
  };

  // 轮询某次生成的结果，拿到图片/视频后插入对话流
  // 收尾进度 WS：退订 + 清进度条
  const stopProgress = () => {
    wsUnsubRef.current?.();
    wsUnsubRef.current = null;
    updateWfProgress(null);
    updateWfNode("");
  };

  // 取消一个前台运转任务：标记 + 移除 pending，**并且出队**（2026-09-15 P22）。
  //
  // 为什么必须自己出队：`workflowRuntime.cancel` 只做 `cancelled.add` + `pending.remove`，
  // **不派发任何终态回调** —— 被取消的任务随后在 runtime 里走 `notFound`（pending 已不在列表
  // ⇒ 静默 `return`）或停顿守卫的同款静默 `return`，5 个终态回调一个都不跑。
  // 而「出队」原本只挂在终态回调上 ⇒ 被取消的 pid 永远留在 `activePromptQueue`，
  // 下一次前台任务 `joinForeground` 便得到 `first=false`、**不再订阅进度 WS**
  // （实锤症状：停止一次之后，后续出图全程没有进度，直到完成才跳到结果）。
  //
  // `leaveForeground` 对「不在队列里的 id」是幂等的（filter 不变、`idle` 按队列现状返回）：
  // 慢守望阶段 / 后台 / 恢复期任务的 pid 本就不在队列里，走这里安全。
  // ⚠ 本函数只做「取消 + 出队」；`idle` 之后要不要停进度条、清慢守望、派发 `workflowDone`，
  //   由调用方决定（三处调用点的收尾本就不同），不要在这里顺手统一。
  const cancelForegroundRun = (promptId: string): void => {
    workflowRuntime.cancel(promptId);
    const left = leaveForeground(activePromptQueue.current, promptId);
    activePromptQueue.current = left.queue;
  };

  const discardFailedIllustration = (
    messageId: string, slotId: string, stage: string, error: string, promptId = "",
    retryArgs?: unknown,   // 不透明快照：生产者决定形状（插画为 IllustrationSubmit 对象）
  ) => {
    console.error("[auto-illustration]", {
      threadId: repo?.id || "home", messageId, slotId, stage, error, promptId,
    });
    // 失败槽不再删除：保留为 failed 态 + 错误原因 + 重试参数快照，楼层提供「重新生成」
    // （2026-08-29 用户需求：停止/失败后应包含重新生图）。
    setMessages((current) => markMediaSlotFailed(current, messageId, slotId, stage, error, retryArgs));
    void reportIllustrationFailure({
      threadId: repo?.id || "home",
      repoId: repo?.id || "home",
      messageId, slotId, stage, error, promptId,
      comfyuiUrl: settings.comfyuiUrl,
    }).catch((reportError) => {
      console.error("[auto-illustration] failure log upload failed", reportError);
    });
  };

  const workflowObserver: WorkflowWatchObserver = {
    finalize: finalizeGeneration,
    completed: (result, pending, produced) => {
      if (!produced && (result.images?.length || 0) === 0
          && (result.videos?.length || 0) === 0 && (result.audios?.length || 0) === 0
          && !pickBestText(result.texts)) {
        if (pending.target) discardFailedIllustration(
          pending.target.messageId, pending.target.slotId, "completed_without_media",
          "生成完成但没有媒体输出", pending.prompt_id,
        );
        else pushBot("生成完成，但没有输出（工作流未含 SaveImage / 视频合成 / 文字输出节点）。");
      }
      if (!pending.target?.background) {
        // 出队（队列判定归 `lib/foregroundRunQueue` 单一属主，P12）
        const left = leaveForeground(activePromptQueue.current, pending.prompt_id);
        activePromptQueue.current = left.queue;
        if (left.idle) {
          stopProgress();
          setSlowWatchPromptId(null);
          dispatch({ t: "workflowDone", promptId: pending.prompt_id });
        } else {
          // 切换到下一个排队的任务（`!idle` ⇒ 队列非空 ⇒ 队首必存在）
          const nextId = left.head!;
          const nextPending = workflowRuntime.list().find(p => p.prompt_id === nextId);
          if (nextPending) {
            const nextUrl = nextPending.comfyuiUrl
              || comfyRegenerationUrl(nextPending.regeneration) || settings.comfyuiUrl;
            const nextGraph = nextPending.regeneration?.kind === "workflow"
              ? (nextPending.regeneration as WorkflowRegeneration).graph : null;
            const nextLabel = (nid: string): string => {
              try {
                const node = (nextGraph as Record<string, { class_type?: string }>)?.[nid];
                return node?.class_type ? `${node.class_type} (#${nid})` : `节点 #${nid}`;
              } catch { return `节点 #${nid}`; }
            };
            wsUnsubRef.current?.();
            wsUnsubRef.current = subscribeProgress(nextUrl, nextId, {
              onProgress: (pct) => updateWfProgress(pct),
              onNode: (nid) => updateWfNode(nextLabel(nid)),
            });
          }
        }
      }
    },
    failed: (pending, stage, error) => {
      if (pending.target) discardFailedIllustration(
        pending.target.messageId, pending.target.slotId, stage, error, pending.prompt_id,
      );
      if (!pending.target?.background) {
        const left = leaveForeground(activePromptQueue.current, pending.prompt_id);
        activePromptQueue.current = left.queue;
        if (left.idle) {
          stopProgress();
          setSlowWatchPromptId(null);
          dispatch({ t: "workflowDone", promptId: pending.prompt_id });
        }
        pushBot(stage === "task_not_found"
          ? "⚠️ 出图任务已丢失（ComfyUI 可能已重启或队列被清空）。如需重新生图，请点工作流卡片的「运转工作流」。"
          : `生成失败：${error}`);
      }
      refreshChatBackgroundActivities();
    },
    released: (pending) => {
      if (pending.target?.background) return;
      const left = leaveForeground(activePromptQueue.current, pending.prompt_id);
      activePromptQueue.current = left.queue;
      if (left.idle) {
        stopProgress();
        dispatch({ t: "workflowDone", promptId: pending.prompt_id });
      }
      setSlowWatchPromptId(pending.prompt_id);
      pushBot("生成较复杂、仍在后台进行，出图后会自动载入（也可在 ComfyUI 面板看进度）。");
    },
    timedOut: (pending) => {
      if (pending.target) discardFailedIllustration(
        pending.target.messageId, pending.target.slotId, "poll_timeout",
        "后台出图等待超时，可刷新后继续恢复", pending.prompt_id,
      );
      else {
        const left = leaveForeground(activePromptQueue.current, pending.prompt_id);
        activePromptQueue.current = left.queue;
        if (left.idle) setSlowWatchPromptId(null);
      }
    },
    stalled: (pending, error) => {
      // 停顿守卫：任务一直卡在排队、从未观察到节点开始运转，早停而非死等到硬超时
      if (pending.target) {
        discardFailedIllustration(
          pending.target.messageId, pending.target.slotId, "stalled",
          error, pending.prompt_id,   // P13：文案由 runtime 经第二参转发（原为同值硬编码，值不变）
        );
        refreshChatBackgroundActivities();
        return;
      }
      const left = leaveForeground(activePromptQueue.current, pending.prompt_id);
      activePromptQueue.current = left.queue;
      if (left.idle) {
        stopProgress();
        setSlowWatchPromptId(null);
        dispatch({ t: "workflowDone", promptId: pending.prompt_id });
      }
      pushBot("⚠️ 出图任务长时间未开始运转（ComfyUI 队列未加载节点），已停止等待，可稍后重试。");
    },
  };

  const pollResult = (
    promptId: string,
    outputNodeIds: string[] = [],
    regeneration?: RegenerationSnapshot,
    target?: PendingGeneration["target"],
    prompt = "",
    mediaType: "image" | "video" | "audio" = "image",
    baseSlotRef?: PendingGeneration["baseSlotRef"],
    // 地址簿按模态选机后，过渡视频 / 配音这两条**没有 regeneration 快照可推导**地址；
    // 不显式传就是入口 0 那台，轮询会去问错机器（永远拿不到结果）。
    comfyuiUrlOverride?: string,
  ) => {
    const comfyuiUrl = comfyuiUrlOverride || comfyRegenerationUrl(regeneration) || settings.comfyuiUrl;
    if (!target?.background) dispatch({ t: "workflowStart", promptId });
    // 节点 id → 类型名映射（用 capturedGraph，API 格式 {id:{class_type,inputs}}）
    const graph = regeneration?.kind === "workflow" ? regeneration.graph : null;
    const nodeLabel = (id: string): string => {
      try {
        const node = (graph as Record<string, { class_type?: string }>)?.[id];
        return node?.class_type ? `${node.class_type} (#${id})` : `节点 #${id}`;
      } catch { return `节点 #${id}`; }
    };
    // 实时进度：直连 ComfyUI /ws（完成判定仍以下方轮询为准，WS 只驱动进度条）
    // 队列：仅首个任务驱动进度条，后续任务排队（等前一个完成后再切换）
    if (!target?.background) {
      // 入队判定归 `lib/foregroundRunQueue` 单一属主（P12）
      const joined = joinForeground(activePromptQueue.current, promptId);
      activePromptQueue.current = joined.queue;
      if (joined.first) {
        // 第一个任务：订阅 WS 驱动进度条
        stopProgress();
        updateWfProgress(0);
        updateWfNode("");
        wsUnsubRef.current = subscribeProgress(comfyuiUrl, promptId, {
          onProgress: (pct) => updateWfProgress(pct),
          onNode: (id) => updateWfNode(nodeLabel(id)),
        });
      }
      // 非首个任务：不抢进度条，仅加入队列
    }
    workflowRuntime.start({
      promptId, comfyuiUrl, outputNodeIds, regeneration, target, prompt,
      owner: { threadId, repoId: repo?.id || "home", outputDir: settings.outputDir },
      mediaType, baseSlotRef,
    }, workflowObserver);
  };

  // 自动插画/视频提交链（T2.2-A 外移 → lib/useIllustrationChain.ts）：函数体逐字随体迁入，
  // hook 闭包符号以同名 deps 注入；调用点（handleAgentStreamEvent / regenerateResult / retryIllustration）留在视图。
  const { submitIllustration } = useIllustrationChain({
    settings, templates, repo, threadId, cardNames, promptProfile, characterBaseImages,
    chat, modelProxies, effectivePersona, messagesRef, setMessages,
    pollResult, discardFailedIllustration,
  });
  // 模板是否定义了图像输入口 / 图值是否已填 → 见 lib/chatGeneration（纯逻辑，已抽出可测）

  // 剧情对白音频化链（T2.2-C1 外移 → lib/useAudioTracks.ts）：submitAudio / mergeAudioTracks
  // 函数体与注释逐字随体迁入，闭包符号以同名 deps 注入；调用点（handleAgentStreamEvent / 对外 return）留在视图。
  const { submitAudio, mergeAudioTracks } = useAudioTracks({
    settings, repo, templates, onNotify, threadId, setMessages, messagesRef, pollResult,
  });

  // /s 启动：取最近一张已确认的工作流卡，用抓取到的画布工作流提交生成
  // 支持队列：不锁运转按钮，多个任务依次提交到 ComfyUI 队列排队执行
  const runWorkflow = async (cardId?: string) => {
    const card = cardId
      ? messages.find((m) => m.id === cardId && m.workflow?.done)
      : [...messages].reverse().find((m) => m.workflow?.done);
    if (!card || !card.workflow) {
      pushBot("没有已确认的工作流。先用 /w 选模板，在画布里调好后点「选择完毕」，再 /s 启动。");
      return;
    }
    const wf = card.workflow;
    if (!wf.capturedGraph) {
      pushBot("没抓到画布内容，请重新点「选择完毕」。");
      return;
    }
    const tpl = templates.find((t) => t.id === wf.templateId);
    if (tpl && needsImageInput(tpl) && !hasImageProvided(wf.capturedGraph, tpl)) {
      pushBot("这是图生图工作流，需要输入图。请点「更改」在画布的图像节点里提供输入图，再 /s 启动。");
      return;
    }
    let comfyUp = false;
    try {
      const st = await comfyStatus(settings.comfyuiUrl);
      comfyUp = !!st.running;
    } catch { comfyUp = false; }
    if (!comfyUp) {
      if (settings.comfyuiPath) {
        pushBot("ComfyUI 未启动，正在尝试自动拉起，请稍候 20~40 秒后重试 /s …");
        startComfy(
          settings.comfyuiPath, settings.comfyuiUrl, settings.comfyuiPython,
        ).catch(() => {});
      } else {
        pushBot("ComfyUI 未启动（8188 无响应）。请先启动 ComfyUI，或在「设置」填写 ComfyUI 目录后由工具自动启动。");
      }
      return;
    }
    try {
      setUploadingWf(true);  // 点击后立即反馈：上传/提交阶段
      const r = await submitGraph(wf.capturedGraph, settings.comfyuiUrl);
      // 用 Toast 提示，不阻塞对话
      const queueHint = wfRunning ? "（已加入 ComfyUI 队列，前序任务完成后自动执行）" : "";
      pushBot(`已提交到 ComfyUI 生成（prompt_id: ${r.prompt_id}，${r.node_count} 个节点），正在运转工作流…${queueHint}`);
      const outputNodeIds = tpl?.primary_output_node_id ? [tpl.primary_output_node_id] : [];
      // 真实提示词从画布工作流图提取（采样器 positive 链上的 CLIPTextEncode 文本），
      // 不再用模板名当提示词——模板名/主模型/LoRA 是元数据，不塞进 prompt 字段。
      const wfMeta = workflowGenMetadata(wf.templateName || "", wf.capturedGraph);
      const wfPrompt = wfMeta.prompt || card.text || "";
      const regeneration = workflowRegenerationSnapshot(
        wf.capturedGraph, settings.comfyuiUrl, outputNodeIds, wfPrompt,
        wfMeta.templateName, wfMeta.modelName, wfMeta.loraNames);
      // R8（C4-A Step 6）：工作流出图的超时合同必须按抓取图的节点类名推导——
      // 改前此处省略 mediaType ⇒ 默认 "image"，视频模板（VHS_VideoCombine 等）也吃图片的
      // 20 分钟硬上限，长视频会在 still_running 前被提前判定超时。帧路径（:1398/:1404）保持
      // "image" 不变：那里生成的确定是帧图。
      if (r.prompt_id) pollResult(
        r.prompt_id, outputNodeIds, regeneration, undefined, wfPrompt,
        deriveWorkflowMediaType(wf.capturedGraph),
      );
    } catch (e) {
      pushBot(`启动失败：${(e as Error).message}`);
    } finally {
      setUploadingWf(false);  // 上传/提交结束（无论成败）；pollResult 已置 wfRunning
    }
  };

  // 进入仓库/切回时恢复「进行中的生图任务」（T2.2-B2 外移 → lib/useWorkflowResume.ts）：
  // resumedRef 与 effect 体逐字随体迁入，闭包符号以同名 deps 注入；依赖数组 `[threadId]` 不变（重订阅时机＝行为面）。
  useWorkflowResume({
    threadId, loadedRef, workflowRuntime, workflowObserver, settings,
    discardFailedIllustration, cancelForegroundRun,
  });
  // APPEND4_HERE

  // 发送入口：只在 Agent 正生成正文时排队；ComfyUI 运行不占用对话通道。
  const send = (content: RichContent) => {
    const text = content.text.trim();
    if (!text && content.images.length === 0 && !content.maskedImage && !(content.attachments || []).length) return;
    atBottomRef.current = true;  // 用户主动发送时强制跟随到底
    if (agentBusyRef.current || blocksDialogueSubmission(gen) || queued.length > 0) {
      enqueue(content);
      return;
    }
    dispatchSend(content);
  };

  // /w 选模板、/s 出图的公共前缀路由。命中并处理则返回 true。
  const routeWorkflowCmd = (raw: string): boolean => {
    const text = normCmd(raw);
    if (text === "/w") { setShowPicker(true); return true; }
    if (text.startsWith("/w ")) {
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: "user", text: raw }]);
      pickByName(text.slice(3).trim());
      return true;
    }
    if (text === "/s") {
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: "user", text: raw }]);
      runWorkflow();
      return true;
    }
    return false;
  };

  // 真正执行一条发送（已确保当前无进行中生成）
  const dispatchSend = (content: RichContent) => {
    const raw = content.text.trim();
    const text = normCmd(raw);  // 指令词大小写归一，参数保持原样
    if (!raw && content.images.length === 0 && !content.maskedImage && !(content.attachments || []).length) return;
    if (routeWorkflowCmd(raw)) return;
    // /压缩 或 /compact：压缩当前对话上下文（AI 触发也可在对话里说"压缩上下文"再点确认）
    if (text === "/压缩" || text === "/compact") { compact(); return; }
    // /find 主题：联网找灵感 → 提炼成提示词灵感卡
    if (text === "/find" || text.startsWith("/find ")) {
      const q = text.slice(5).trim();
      if (!q) { setMessages((m) => [...m, { id: crypto.randomUUID(), role: "user", text: raw }]); pushBot("请在 /find 后写要找的灵感主题，如 /find 哥特萝莉裙"); return; }
      runFindInspiration(q, content);
      return;
    }
    // /a 模板名 [需求]：显式请求编排 → 强制编排，跳过意图判定。
    if (text === "/a" || text.startsWith("/a ")) {
      const rest = text.slice(2).trim();  // "模板名 需求"
      const found = findWorkflowCardByName(rest);
      if (!found || found.matchedName === "__unconfigured__") {
        setMessages((m) => [...m, { id: crypto.randomUUID(), role: "user", text: raw }]);
        pushBot(
          found?.matchedName === "__unconfigured__"
            ? "这个工作流还没配「输入/输出节点」，AI 编排没法知道该往哪个口填。请到「工作流模板 → 编写能力描述」里，用「在画布选择节点」指定替换输入/输出节点后再来编排。"
            : rest
              ? `没找到匹配「${rest}」的工作流卡。请先用 /w 选择该工作流，或点卡片上的「AI 编排」。`
              : "请在 /a 后写工作流模板名（或点工作流卡上的「AI 编排」按钮），再补充你的编排需求。");
        return;
      }
      const { card, matchedName } = found;
      const scene = rest.slice(matchedName.length).trim();  // 去掉模板名，剩余为自然语言需求
      planWorkflowOps(card, scene, { ...content, text: scene }, true);  // force 编排
      return;
    }
    // 智能路由：有可编排工作流卡时先让 AI 判断是否编排意图，否则转对话
    const orchCard = findWorkflowCardByName("");
    if (orchCard && orchCard.card) {
      planWorkflowOps(orchCard.card, raw, content, false);  // force=false：带意图判定
      return;
    }
    // 其余一律交给多 Agent（Supervisor 编排，复用同一生命周期）
    runFreeText(raw, content);
  };

  // 把消息加入后端持久化队列：worker 在前一条结束后串行认领执行（离开页面/刷新仍继续）。
  // 后端只存 multiAgent 执行参数；UI 编辑/引导所需的原始 RichContent 另存本地映射。
  const enqueue = (content: RichContent) => {
    if (threadId === "home") return;  // 首页临时草稿区不进后端队列
    const text = content.text.trim();
    const images = content.images || [];
    const visibleHistory = promptHistory(messagesRef.current);
    enqueueQueued(content, createAgentInvocation(text, images, visibleHistory, {
      imageMask: content.maskedImage
        ? { image: content.maskedImage.image, mask: content.maskedImage.mask } : null,
      ...(content.attachments?.length ? { attachments: content.attachments } : {}),
    }));
  };

  // 取消队列里的某条（后端删除 + 清本地内容映射）
  const cancelQueued = (id: string) => {
    removeQueued(id);
  };

  // AI 建议按钮点击：执行单条指令（/w 选模板、/s 出图）。其余走智能体。
  const runCommand = (cmd: string) => {
    const raw = cmd.trim();
    if (!raw) return;
    if (routeWorkflowCmd(raw)) return;
    runFreeText(raw);
  };
  // APPEND5_HERE

  // 同步应用一个流事件到 ref + state：此前只用 setMessages 函数式更新，messagesRef 要等
  // 下一次 render 才同步。illustrate_request 之后 submitIllustration 立即用 messagesRef
  // 保存快照去认领，旧 ref 里没有刚插入的槽 → 覆盖服务端按 offset 建好的槽 → 兜底在
  // 末尾重建（2026-09-01 用户实锤：图总在末尾）。这里强制同步，杜绝该竞态。
  const applyStreamEvent = (
    botId: string, event: ChatStreamEvent, placement?: SlotPlacement,
  ) => {
    const next = reduceChatStreamEvent(messagesRef.current, botId, event, placement);
    messagesRef.current = next;
    setMessages(next);
  };

  const handleAgentStreamEvent = (botId: string, event: ChatStreamEvent) => {
    if (event.type === "image" || event.type === "video") {
      dispatch({ t: "agentImage", botId });
      if (event.type === "image" && abortRef.current?.botId === botId && repo?.id) {
        setGeneratedCover(repo.id, event.url);
      }
      window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: threadId }));
    }
    // 剧情高潮点出图请求：按本作品预设提交 ComfyUI 异步出图（不产气泡，出图完成后由 pollResult 补入）
    if (event.type === "illustrate_request") {
      const slotId = event.id || crypto.randomUUID();
      // 第 34 刀：首尾帧＝**固定落点**，前端按正文自算（零 wire 改动）。这里用与插画链
      // （useIllustrationChain）**同一套** `resolveVideoMode` 解析后告知 reducer —— 独立图片
      // 模式（选了首尾帧但没配视频模板）事件里不带 `video_mode`，reducer 自己猜不出来。
      const firstlast = resolveVideoMode(mediaPreset, event.videoMode) === "firstlast";
      applyStreamEvent(botId, { ...event, id: slotId }, firstlast ? { firstlast } : undefined);
      const illustrationSubmit: IllustrationSubmit = {
        prompt: event.prompt, motion: event.motion, actors: event.actors,
        messageId: botId, slotId, sceneSpec: event.sceneSpec, turnId: event.turnId,
        source: "automatic", eventVideoMode: event.videoMode,
        firstFrameDesc: event.firstFrameDesc, lastFrameDesc: event.lastFrameDesc,
        prevTailDesc: event.prevTailDesc, lastFrameUrl: event.lastFrameUrl,
        videoPrompt: event.videoPrompt, videoParams: event.videoParams,
        transition: event.transition,
        transitionVideoPrompt: event.transitionVideoPrompt,
        transitionVideoParams: event.transitionVideoParams,
        // 第 74 刀：段桥的「上一张画面」槽，随快照落盘 ⇒ 桥视频槽的「重新生成」也认得上一张。
        bridgePrevSlotId: event.bridgePrevSlotId,
      };
      void submitIllustration(illustrationSubmit).catch((error) => {
        // 提交链上任何未被 failSlot 覆盖的异常都落到失败槽（可重新生成），
        // 不再让 pending 槽静默（2026-08-31 晚实锤：槽 pending 却无图、无报错）。
        discardFailedIllustration(
          botId, slotId, "submission",
          error instanceof Error ? error.message : "自动插画提交异常",
          "", illustrationSubmit,
        );
      });
      return;
    }
    // 剧情对白配音请求：逐角色提交 IndexTTS（不入气泡流，音频完成后按角色分条聚合）
    if (event.type === "audio_request") {
      void submitAudio(event.lines, botId);
      return;
    }
    // RAG 记忆库创建状态：右下角轻提示（创建中/成功/失败），不入气泡流
    if (event.type === "rag_status") {
      emitRagStatus({ state: event.state, kind: event.kind, count: event.count });
      return;
    }
    applyStreamEvent(botId, event);
  };

  // 自由文本 → 多 Agent（Supervisor/LangGraph 编排，多轮上下文）：主管分派→生图/反推/灵感/工具专家。
  // 复用同一套生命周期（消息/图片/状态/落盘），是"前端生命周期与后端 agent 解耦"的体现。
  // skipUserMsg：用户气泡已由调用方（如编排判定前）提前 push，这里只补 bot 气泡，避免重复。
  // userMsgId：复用调用方预 push 的用户消息 id，保证后端 userMessageId 关联一致。
  // M1 记忆封口（2026-09-06 用户定案）：删除/重生成回合的派生记忆级联封口——
  // 「当前会话展示内容就是完整的记忆与上下文」。封口失败不阻断操作（降级为旧行为）。
  const sealTurns = async (removed: readonly ChatMessage[], mode: "delete" | "regenerate" = "delete") => {
    const turns = sealTurnsOf(removed);
    if (turns.length === 0) return;
    try {
      const result = await sealMemory({
        outputDir: settings.outputDir,
        repoId: repo?.id || threadId,
        cardName: openingCardName,
        cardNames,
        turns,
        mode,
      });
      if (result.state_incomplete?.length) {
        // M1 审计 #2：这些字段的审计已截断，无法证明精确还原（保守保留现值）
        console.warn("[memory-seal] 以下状态字段无法精确回滚（审计窗口截断）：",
          result.state_incomplete);
      }
    } catch (err) {
      console.warn("[memory-seal] 记忆封口失败（不影响删除/重生成本身）", err);
    }
  };

  const runFreeText = (
    t: string, content?: RichContent, skipUserMsg = false, userMsgId?: string,
    historyMessages: readonly ChatMessage[] = messagesRef.current,
    storyTurn?: number,
  ) => {
    const visibleHistory = promptHistory(historyMessages);
    // 回合号持久化：重生成复用被删回合号（同回合幂等重写），新消息取链尾+1。
    // 第 58 刀：新回合再与**高位水印**取大 —— 「最高回合的消息被删/重生成」时 max+1 会复用
    // 旧号 ⇒ 槽 id 撞车（真机：19:09 与 19:52 两轮同为 54 ⇒ 新图不插入/混叠）。
    // 2026-10-21：显式 storyTurn 不取大、不抬水印（否则反复重试把复用号顶高：9 层虚到 16）；
    // 先 sync 校准水印再 resolve（删末尾回合后水印停高位 ⇒ 跳号：9 轮会话出 turnNo=17）。
    const resolved = resolveStoryTurn(
      storyTurn, historyMessages, syncStoryTurnHighWater(threadId, historyMessages),
    );
    const turnNo = resolved.turnNo;
    if (resolved.remember) rememberStoryTurn(threadId, turnNo);
    const images = content?.images || [];
    const imageMask = content?.maskedImage
      ? { image: content.maskedImage.image, mask: content.maskedImage.mask }
      : undefined;
    const attachments = content?.attachments || [];
    // D1 历史回放：userMsg parts 持久化 file part（file_id 真源，base64 不落消息）
    const fileParts: MsgPart[] = attachments.map((a) => ({
      type: "file" as const,
      fileId: a.fileId,
      name: a.name,
      mime: a.mime,
      size: a.size,
    }));
    const userMsg: ChatMessage = {
      id: userMsgId || crypto.randomUUID(),
      role: "user",
      text: t,
      parts: content?.parts || (images.length > 0 || fileParts.length > 0 ? [
        ...(t ? [{ type: "text" as const, text: t }] : []),
        ...images.map((url) => ({ type: "image" as const, url })),
        ...fileParts,
      ] : undefined),
      ...(content?.inspirationAttachments?.length
        ? { inspirationAttachments: content.inspirationAttachments }
        : {}),
    };
    const botId = crypto.randomUUID();
    agentBusyRef.current = true;
    setMessages((m) => [
      ...m,
      ...(skipUserMsg ? [] : [userMsg]),
      { id: botId, role: "assistant", text: "", turnNo },
    ]);
    dispatch({ t: "agentStart", botId });  // 进入 agent 态（未出图）
    const onDone = (err?: string) => {
      agentBusyRef.current = false;
      dispatch({ t: "agentDone", botId });
      if (abortRef.current?.botId === botId) abortRef.current = null;
      if (err) {
        handleAgentStreamEvent(botId, { type: "error", message: err });
      }
      startAgentRecovery();
    };
    // 多 Agent（Supervisor 编排）：trace（主管分派→专家执行）作为过程行 append 进 bot 文本，其余回调复用。
    // 单 agent 对外入口已下线，其大脑降级为多 Agent 的 tool_agent 专家节点（承接 MCP/工具串联）。
    const abort = multiAgent(
      createAgentInvocation(t, images, visibleHistory, {
        imageMask: imageMask || null,
        messageId: botId,
        userMessageId: userMsg.id,
        storyTurn: turnNo,
        ...(attachments.length > 0 ? { attachments } : {}),
      }),
      {
        onEvent: (event) => handleAgentStreamEvent(botId, event),
        onDone,
      },
    );
    abortRef.current = { botId, abort };
  };

  const regenerateMessage = async (messageId: string) => {
    if (streamingId || wfRunning) return;
    const replay = prepareConversationRegeneration(messagesRef.current, messageId);
    if (!replay) return;
    const discardedMessages = messagesRef.current.slice(replay.retained.length);
    const discarded = discardedMessages.length;
    if (discarded > 1 && !await askConfirm(`重新生成将删除此消息后的 ${discarded} 条消息，是否继续？`)) {
      return;
    }
    // M1 记忆封口：被丢弃楼层的派生记忆（纪要/事实/状态）按回合级联封口，
    // 旧结果不再影响重新生成；同回合重写（回合号持久化，不倒退）。
    await sealTurns(discardedMessages, "regenerate");
    const reusedTurn = sealTurnsOf(discardedMessages)[0]
      ?? nextStoryTurnNo(replay.history);
    historyRuntime.replace(replay.retained);
    atBottomRef.current = true;
    runFreeText(
      replay.content.text.trim(), replay.content, true, messageId, replay.history,
      reusedTurn,
    );
  };

  const actOnPromptApproval = (
    approval: PromptApproval,
    action: "submit" | "change" | "cancel",
    editedPrompt?: string,
  ): Promise<void> => new Promise((resolve) => {
    const visibleHistory = promptHistory(messagesRef.current);
    const botId = crypto.randomUUID();
    const actionText = action === "submit" ? "确认提交" : action === "change" ? "更改提示词" : "取消";
    setMessages((messages) => [
      ...messages,
      { id: botId, role: "assistant", text: "" },
    ]);
    dispatch({ t: "agentStart", botId });
    const onDone = (err?: string) => {
      dispatch({ t: "agentDone", botId });
      if (abortRef.current?.botId === botId) abortRef.current = null;
      if (err) handleAgentStreamEvent(botId, { type: "delta", text: `操作失败：${err}` });
      startAgentRecovery();
      resolve();
    };
    const abort = multiAgent(
      createAgentInvocation(actionText, [], visibleHistory, {
        messageId: botId,
        approvalAction: { approvalId: approval.id, action, editedPrompt },
      }),
      {
        onEvent: (event) => handleAgentStreamEvent(botId, event),
        onDone,
      },
    );
    abortRef.current = { botId, abort };
  });

  const actOnRouteChoice = (
    choice: RouteChoice,
    route: AgentRoute,
  ): Promise<void> => new Promise((resolve) => {
    const visibleHistory = promptHistory(messagesRef.current);
    const source = messages.find((message) => message.id === choice.userMessageId);
    if (!source) {
      pushBot("原始消息已不存在，无法继续执行这次选择。请重新发送需求。");
      resolve();
      return;
    }
    const sourceImages = (source.parts || [])
      .filter((part) => part.type === "image" && part.url)
      .map((part) => part.url!);
    const sourceMaskedPart = (source.parts || []).find(
      (part) => part.type === "masked-image" && part.image && part.mask,
    );
    const sourceImageMask = sourceMaskedPart
      ? { image: sourceMaskedPart.image!, mask: sourceMaskedPart.mask! }
      : undefined;
    const selected: RouteChoice = { ...choice, status: "selected", selectedRoute: route };
    setMessages((current) => applyRouteChoice(current, selected));

    const botId = crypto.randomUUID();
    setMessages((current) => [...current, { id: botId, role: "assistant", text: "", route }]);
    dispatch({ t: "agentStart", botId });
    const onDone = (err?: string) => {
      dispatch({ t: "agentDone", botId });
      if (abortRef.current?.botId === botId) abortRef.current = null;
      if (err) {
        handleAgentStreamEvent(botId, { type: "delta", text: `操作失败：${err}` });
        setMessages((current) => applyRouteChoice(current, {
          ...choice, status: "pending", selectedRoute: undefined,
        }));
      }
      startAgentRecovery();
      resolve();
    };
    const abort = multiAgent(
      createAgentInvocation(source.text, sourceImages, visibleHistory, {
        imageMask: sourceImageMask || null,
        messageId: botId,
        routeAction: { route, userMessageId: source.id },
      }),
      {
        onEvent: (event) => handleAgentStreamEvent(botId, event),
        onDone,
      },
    );
    abortRef.current = { botId, abort };
  });

  // 工作流输入口编排（见 lib/workflowOrchestration）：依赖 runFreeText，故声明其后。
  const { findWorkflowCardByName, planWorkflowOps, applyWorkflowOps, ignoreWorkflowOps, editWorkflowOp } =
    useWorkflowOrchestration({
      messages, setMessages, templates, chat,
      comfyuiUrl: settings.comfyuiUrl, imageStyle: "",
      styleTemplate: promptTemplateInstructions(settings.promptTemplateId || "", promptTemplates),
      repoId: threadId, pushBot, runFreeText,
    });

  // /find：联网找灵感 → 灵感卡（显式指令路径，不走 agent）
  const runFindInspiration = async (query: string, content?: RichContent) => {
    setMessages((m) => [...m, {
      id: crypto.randomUUID(), role: "user",
      text: content?.text?.trim() || `/find ${query}`, parts: content?.parts,
    }]);
    const loadId = crypto.randomUUID();
    setMessages((m) => [...m, { id: loadId, role: "assistant", text: `正在联网搜索「${query}」的灵感…` }]);
    try {
      const card = await fetchInspiration(
        query, chat, effectiveGlobalProxyUrl(settings), searchProviderWireValue(settings.searchProvider),
      );
      setMessages((ms) => ms.map((m) => m.id === loadId
        ? { id: m.id, role: "assistant", text: "",
            inspiration: { title: card.title, content: card.content, sources: card.sources || [],
                           images: card.images || [], selected: card.selected || [] } }
        : m));
    } catch (e) {
      setMessages((ms) => ms.map((m) => m.id === loadId
        ? { ...m, text: `找灵感失败：${(e as Error).message}` } : m));
    }
  };

  // 中断必须打在**提交那台机**上：地址簿按模态选机后，`settings.comfyuiUrl` 可能已换机，
  // 且过渡视频 / 配音本就不带 regeneration 快照 —— 打在入口 0 上等于没停（那边仍在跑）。
  // 在途登记里没有该 prompt（如文本生成的 promptId）时退回旧口径，中断失败照旧忽略。
  const comfyUrlForPrompt = (promptId: string): string => {
    const pending = workflowRuntime.list().find((p) => p.prompt_id === promptId);
    return pending?.comfyuiUrl
      || comfyRegenerationUrl(pending?.regeneration) || settings.comfyuiUrl;
  };

  // 真正停止后台生成
  const hardCancel = async (promptId: string | null): Promise<void> => {
    try { await cancelAgent(threadId); } catch { /* 后端未起忽略 */ }
    if (promptId) {
      try { await interruptComfy(comfyUrlForPrompt(promptId), promptId); } catch { /* 忽略 */ }
    }
    abortRef.current = releaseAgentStream(abortRef.current, "stop");
    // 2026-10-14：后端 `request_cancel` 只置取消事件、不释放准入（release 等 worker
    // finally）⇒ 若同步阻塞段（压缩摘要 / 上传）无人消费取消信号，暂停后立即重发会撞
    // 409「该对话已有生成任务正在运行」。这里等准入真正释放再解禁发送键（A 方案）。
    const released = await waitForAgentRelease(threadId);
    if (!released) {
      pushBot("后端仍在收尾生成任务，请稍等几秒再发消息");
    }
  };

  // 中断当前生成（「停止」按钮）——兼容快轮询阶段（wfRunning）和慢守望阶段（slowWatchPromptId）
  const stopGenerating = async () => {
    if (needsConfirm(gen)) {
      const ok = await askConfirm(
        "正在生成图片 / 运转工作流。强行停止会中止本次生成（工作流任务也会停止，已发起的云端调用可能作废）。确定停止吗？",
      );
      if (!ok) return;
    }
    const sid = streamingId;
    const pid = runningPromptId(gen) ?? slowWatchPromptId;  // 慢守望阶段 gen 里已无 promptId
    dispatch({ t: "stop" });
    // ⚠ 不在此处置 `agentBusyRef.current = false`：否则发送键在准入释放前就解禁，
    // 重发会直连后端撞 409「该对话已有生成任务正在运行」（2026-10-14 实测）。
    // 必须等 `hardCancel` 里 `waitForAgentRelease` 完成后再解禁（见下方）。
    stopProgress();
    setSlowWatchPromptId(null);  // 清慢守望状态，停止键消失
    // P22：`cancel` 不派发终态 ⇒ 必须同时出队，否则残留 id 会让下一次前台任务
    // 拿到 `first=false`、进度条不再订阅（见 `cancelForegroundRun`）。
    if (pid) cancelForegroundRun(pid);
    refreshChatBackgroundActivities();
    await hardCancel(pid);
    // hardCancel 已等到准入释放（或超时提示）⇒ 此刻解禁发送键才安全
    agentBusyRef.current = false;
    if (sid) {
      // 2026-09-04 收口：半成品文本补「（已打断）」标记（下轮后端 _resume_interrupted_messages
      // 依此从断点续写）；空文本沿用「（已停止生成）」提示。reducer interrupted 事件同函数幂等。
      setMessages((ms) => applyInterruptedStop(ms, sid));
    }
  };

  // 停止单个媒体槽的生成（图片生成位置下方的「停止」键）：中断 ComfyUI + 取消守望 + 槽位标失败。
  const stopSlotGeneration = async (messageId: string, slotId: string) => {
    const msg = messagesRef.current.find((m) => m.id === messageId);
    const part = msg?.parts?.find((p) => p.slotId === slotId);
    const promptId = part?.promptId || "";
    if (promptId) {
      // P22：同 `stopGenerating` —— 取消必须出队（该槽的 prompt 可能正是前台首任务）
      cancelForegroundRun(promptId);
      try { await interruptComfy(comfyUrlForPrompt(promptId), promptId); } catch { /* 中断失败忽略，槽位照常标停 */ }
    }
    setMessages((current) => failMediaSlot(current, messageId, slotId, "已停止生成"));
    refreshChatBackgroundActivities();
  };

  // 队列条「引导」：把该排队消息以「打断+合并」方式立即执行。
  // 内容取自后端队列项（本地内容映射优先，缺失用文本兜底）；先从后端队列删除再本地即时发送。
  const guideQueued = async (id: string) => {
    const item = queued.find((q) => q.id === id);
    if (!item) return;
    if (needsConfirm(gen)) {
      const ok = await askConfirm(
        "当前正在云端生图 / 运转工作流。\n\n" +
        "打断会中止本次生成：已发起的云端调用可能作废且不退费，工作流任务也会停止。\n\n" +
        "确定要打断并让 AI 结合已生成内容继续处理这条消息吗？",
      );
      if (!ok) return;  // 用户取消 → 保留在队列
    }
    const sid = streamingId;
    const pid = runningPromptId(gen);
    removeQueued(id);                         // 从后端队列删除，避免 worker 再跑
    dispatch({ t: "stop" });              // 停当前生成（保留半成品）
    await hardCancel(pid);
    if (sid) {
      // 半成品补「（已打断）」标记：dispatchSend 新一轮上传的历史带后缀 → 后端命中
      // _resume_interrupted_messages → AI 带上下文从断点续写 = 合并（与 stopGenerating 同语义）
      setMessages((ms) => applyInterruptedStop(ms, sid));
    }
    dispatchSend(item.content);  // 同 thread 新一轮：AI 带上下文续写 = 合并
  };

  const regenerateResult = async (messageId: string, slotId?: string, promptOverride?: string) => {
    const message = messages.find((item) => item.id === messageId);
    const part = slotId
      ? message?.parts?.find((item) => item.slotId === slotId)
      : undefined;
    const baseSnapshot = slotId
      ? part?.regeneration
      : message?.regeneration;
    // 「重新生图（含提示词）」：用户编辑后的提示词写回快照。
    // ⚠ 三种快照的真源不同（ai-image=prompt 字段 / template=values 里的 prompt 键 /
    // workflow=图里的文本节点），由 overrideRegenerationPrompt 按 kind 分流——只改
    // prompt 字段在 template/workflow 下**不影响出图**。
    const snapshot = baseSnapshot && promptOverride
      ? overrideRegenerationPrompt(
        baseSnapshot,
        promptOverride,
        templates.find((t) => t.id === (baseSnapshot.kind === "template" ? baseSnapshot.templateId : ""))?.exposed,
      )
      : baseSnapshot;
    if (!snapshot && !(slotId && part?.url)) {
      pushBot("这张历史图片生成时尚未保存完整参数，无法保证准确重生成。");
      return;
    }
    if (streamingId || wfRunning || regeneratingIds.size > 0) {
      pushBot("当前已有生成任务，请等待完成后再重新生图。");
      return;
    }
    setRegeneratingIds((current) => new Set(current).add(messageId));
    try {
      if (!snapshot) {
        const generations = await listGenerations(repo?.id || "home", embedModel);
        const found = legacyGenerationPrompt(part?.url || "", generations.items || []);
        const prompt = (promptOverride || "").trim() || found;
        if (!prompt) throw new Error("资产库中未找到这张旧图的原提示词");
        await submitIllustration({ prompt, motion: 0, actors: [], messageId, slotId: slotId!, source: "manual" });
        return;
      }
      if (snapshot.kind === "ai-image") {
        const model = resolveImageRegenerationModel(snapshot, settings.imageModels);
        if (!model) {
          throw new Error(
            `原生图模型已不存在：${snapshot.model.modelName}（${snapshot.model.baseUrl}）`,
          );
        }
        const rec = await replayImageGeneration(snapshot, {
          apiKey: model.apiKey,
          proxyUrl: resolveModelProxy(model.proxyMode, globalProxyAddress(settings), settings.proxyEnabled),
        }, {
          threadId,
          repoId: repo?.id || "home",
          outputDir: settings.outputDir,
          embed: embedModel,
          embedProxyUrl: modelProxies.embedProxyUrl,
        });
        // 原位替换（不新发消息）：把重新生成的图放回原剧情对话里那条消息，
        // 而不是用 upsert 追加一条新消息（用户拍板 2026-08-27）。
        setMessages((current) => current.map((m) => (m.id === messageId
          ? { ...m, image: rec.url, regeneration: rec.regeneration || snapshot }
          : m)));
        if (repo?.id) setGeneratedCover(repo.id, rec.url);
        window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: threadId }));
        return;
      }

      const status = await comfyStatus(snapshot.comfyuiUrl);
      if (!status.running) throw new Error(`原 ComfyUI 地址未运行：${snapshot.comfyuiUrl}`);
      const submitted = snapshot.kind === "template"
        ? await submitWorkflow(
          snapshot.templateId, snapshot.values, snapshot.comfyuiUrl, snapshot.prompt,
          snapshot.loras || [], snapshot.loraMode || "single",
        )
        : await submitGraph(snapshot.graph, snapshot.comfyuiUrl);
      if (!submitted.prompt_id) throw new Error("ComfyUI 未返回 prompt_id");
      const target = slotId
        ? { messageId, slotId, background: true as const }
        : undefined;
      // R8（C4-A Step 6）：重生成同样要按快照推导媒体种类。工作流快照（含视频模板正片）
      // 能从 graph 判定；template / ai-image 快照结构里没有媒体种类字段，保持 image 现状。
      pollResult(
        submitted.prompt_id, snapshot.outputNodeIds, snapshot, target, snapshot.prompt,
        snapshot.kind === "workflow" ? deriveWorkflowMediaType(snapshot.graph) : "image",
      );
    } catch (error) {
      pushBot(`重新生图失败：${(error as Error).message}`);
    } finally {
      setRegeneratingIds((current) => {
        const next = new Set(current);
        next.delete(messageId);
        return next;
      });
    }
  };

  // 首页(home)临时草稿区手动清空：清当前显示 + 模块级 homeDraft。仅首页有意义（右上角按钮触发）。
  const clearHome = () => {
    clearHomeDraft();
    setMessages([]);
  };

  // ===== ④ AI 消息就地编辑 =====
  // 改 text；若有 parts 则按「槽前文本尾部锚」在新文本中重定位媒体槽（保留图片相对位置）。
  // 旧实现把全部媒体槽无条件下挪到消息末尾——图片还在生成中时点编辑保存，图片就漂到
  // 消息尾部、不在原来段落下方（2026-09-12 用户实锤）。
  const editMessage = (id: string, text: string) => {
    let edited: ChatMessage | null = null;
    historyRuntime.replace(messagesRef.current.map((m) => {
      if (m.id !== id) return m;
      if (m.parts && m.parts.length > 0) {
        const slots = m.parts.filter((p) => p.type !== "text");
        if (slots.length === 0) {
          return { ...m, text, parts: text ? [{ type: "text" as const, text }] : [] };
        }
        // 为每个槽计算新文本中的插入点：取该槽之前累计文本的尾部片段（40/24/12 字逐级缩短）
        // 在新文本中向后搜索；找不到锚的槽按原顺序追加到末尾（退化为旧行为，不丢槽）。
        const positioned: { slot: typeof slots[number]; at: number }[] = [];
        const fallback: typeof slots[number][] = [];
        let accText = "";
        for (const p of m.parts) {
          if (p.type === "text") {
            accText = (accText + (p.text || "")).slice(-64);
            continue;
          }
          const tail = accText.replace(/\s+$/g, "");
          let at = -1;
          for (const size of [40, 24, 12]) {
            const probe = tail.slice(-size);
            if (probe.length < 6) continue;
            const idx = text.indexOf(probe);
            if (idx >= 0) { at = idx + probe.length; break; }
          }
          if (at >= 0 && at <= text.length) positioned.push({ slot: p, at });
          else fallback.push(p);
        }
        positioned.sort((a, b) => a.at - b.at);
        const parts: MsgPart[] = [];
        let cursor = 0;
        for (const { slot, at } of positioned) {
          const cut = Math.max(cursor, Math.min(text.length, at));
          if (cut > cursor) parts.push({ type: "text" as const, text: text.slice(cursor, cut) });
          parts.push(slot);
          cursor = cut;
        }
        if (cursor < text.length) parts.push({ type: "text" as const, text: text.slice(cursor) });
        for (const slot of fallback) parts.push(slot);
        if (parts.length === 0 && text) parts.push({ type: "text" as const, text });
        edited = { ...m, text, parts };
        return edited;
      }
      edited = { ...m, text };
      return edited;
    }));
    // 编辑原子落库（2026-09-13 实锤：只靠全量快照保存，静默失败后下一轮生成会把
    // 编辑打回——用户删掉的「（正文到此…）」在新生成后重新出现）。失败必须可见。
    if (edited) {
      editMessageRemote(threadId, edited)
        .then((r) => {
          if (!r.ok || !r.updated) throw new Error(r.updated ? "rejected" : "message not found");
        })
        .catch(() => {
          pushBot("⚠️ 编辑保存失败：本轮修改只在本机生效，重新生成后可能被旧内容覆盖。请重试编辑或检查后端连接。");
        });
    }
  };

  // ===== ④ 检查点（回滚点）：当前小仓库内，快照到某条为止的消息，可回滚 =====
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  useEffect(() => {
    setCheckpoints(historyRuntime.loadCheckpoints());
  }, [historyRuntime]);
  const createCheckpoint = (id: string) => {
    setCheckpoints(historyRuntime.createCheckpoint(id));
  };
  const restoreCheckpoint = (ckptId: string) => {
    historyRuntime.restoreCheckpoint(ckptId);
  };
  const deleteCheckpoint = (ckptId: string) =>
    setCheckpoints(historyRuntime.deleteCheckpoint(ckptId));

  // ④ 分支：返回到某条为止的消息切片（新小仓库拷贝这段起头，创建+跳转在 App 层）
  const messagesUpTo = (id: string): ChatMessage[] => historyRuntime.messagesThrough(id);

  // 删除单条消息（用户/AI 均可）。立即更新 ref + 后端快照；下次请求同时显式上传该可见历史。
  // ★ 记录墓碑：删除落库与 agent 恢复轮询/快照回灌存在竞态，回灌路径须过滤墓碑防复活。
  const deleteMessage = (id: string) => {
    // M1 记忆封口：被删 AI 楼层的派生记忆按回合级联封口（等效记忆清除，失败不阻断删除）
    const removed = messagesRef.current.find((m) => m.id === id);
    if (removed && agentBusyRef.current && sealTurnsOf([removed]).length > 0
        && !window.confirm("生成任务进行中：现在删除无法立即封口该楼层的记忆（后台任务收尾可能把旧结果写回）。确定继续？")) {
      return;
    }
    if (removed) void sealTurns([removed]);
    deletedMessageTombstones.record(threadId, id);
    historyRuntime.deleteMessage(id);
  };

  // 会话导入后重载：从后端快照重新拉取消息流覆盖当前视图（导入端点已落盘为真源）。
  const reloadFromSnapshot = async () => {
    try {
      const snap = await fetchSnapshot(threadId);
      // ★ 墓碑过滤：onGenerated 回读与删除落库竞态时，旧快照不得复活刚删的消息。
      //   导入会话路径由 ChatView 先 clear 墓碑（显式以后端为真源）。
      historyRuntime.replace(deletedMessageTombstones.filterDeleted(threadId, (snap.items || []) as ChatMessage[]), false);
    } catch { /* 拉取失败保持当前视图，用户可刷新 */ }
  };
  reloadFromSnapshotRef.current = reloadFromSnapshot;

  // 失败槽「重新生成」（2026-08-29 用户需求）：从槽位快照恢复参数重调 submitIllustration，
  // source 翻转为 manual 跳过自动 claim（重试是用户显式动作，不存在同槽重复消费）。
  const retryIllustration = (messageId: string, slotId: string) => {
    const message = messagesRef.current.find((m) => m.id === messageId);
    const part = message?.parts?.find((p) => p.slotId === slotId) as
      | { retryArgs?: IllustrationSubmit }
      | undefined;
    const snapshot = part?.retryArgs;
    if (!snapshot) return;
    // 重试即时反馈：槽位先回 pending（重试要走帧编译+提交，秒级无反馈会被当成「点了没反应」）；
    // 再次失败时 failSlot 会带新错误与原快照，重新变回 failed+按钮。
    setMessages((current) => resetMediaSlotForRetry(current, messageId, slotId));
    // source 按名覆盖为 manual 跳过自动 claim——不再有 `args[7]` 这种位置耦合（审计 C7）。
    void submitIllustration({ ...snapshot, source: "manual" });
  };
  return {
    messages, streamingId, wfRunning, uploadingWf, slowWatchPromptId, wfProgress, wfNode, queued, regeneratingIds,
    send, runCommand, pushBot, pushMsg,
    retryIllustration,
    actOnPromptApproval, actOnRouteChoice, regenerateResult,
    pickTemplate, runWorkflow, updateCardDraft, markCardDone, markCardReopen,
    applyWorkflowOps, ignoreWorkflowOps, editWorkflowOp,
    stopGenerating, stopSlotGeneration, guideQueued, cancelQueued,
    confirmReq, compact, compacting,
    contextReminder, dismissContextReminder,
    clearHome, clearCache: clearCacheAction, reloadFromSnapshot,
    editMessage, deleteMessage, regenerateMessage,
    checkpoints, createCheckpoint, restoreCheckpoint, deleteCheckpoint, messagesUpTo,
    mergeAudioTracks,
    /** 对话侧的归档链（= `workflowObserver.finalize`），暴露给画布复用。
     *  2026-09-15 C4-A 第 3 步：画布搬到 `runtime.start(observer)` 后必须**走同一条** finalize 链，
     *  否则画布那条归档仍在 runtime 的判重域之外（`finalized`/`cancelled` 集合），
     *  同一条 pending 会出现两条互不知情的归档路径（审计 R4）。
     *  注意用 ref 持有：它每次渲染都是新函数，进画布投影 effect 的依赖会让整图重投影（R6）。 */
    finalizeWorkflowRun: finalizeGeneration,
  };
}
