// 聊天会话引擎：从 ChatView 抽出的「持久化 + 生成编排」深簇。
// 拥有 messages / 生成生命周期 reducer / 各类 ref 与加载·落盘 effect，
// 对外只暴露渲染所需的状态与动作句柄；UI 局部态（模型选择/面板开关/尺寸）留在 ChatView。
// 接口即测试面：整台生成引擎集中一处，不必渲染千行组件即可推演其行为。
import { type MutableRefObject, useEffect, useReducer, useRef, useState } from "react";
import type { AgentRoute, ChatMessage, MsgPart, PromptApproval, RegenerationSnapshot, RouteChoice, WorkflowRegeneration } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings } from "../stores/settings";
import { activeStyleTemplate, activeUserPersona } from "../stores/settings";
import type { RichContent } from "../components/RichInput";
import { emitRagStatus } from "../components/RagToast";
import type { Template } from "../api/workflows";
import {
  comfyStatus, startComfy, submitGraph, submitWorkflow, interruptComfy,
  saveLocalSrc, localViewUrl, uploadImage, finalizeGeneration as persistWorkflowGeneration,
  mergeAudio, moveComfyOutputToInput, uploadRemoteImageToInput,
  type GenResult,
} from "../api/comfyui";
import { listLoras } from "../api/loras";
import {
  fetchHistory, multiAgent,
  saveSnapshot, fetchSnapshot, fetchAgentRunning, cancelAgent,
  fetchInspiration, regenerateImage as replayImageGeneration,
  claimIllustrationSubmission, reportIllustrationFailure, reportIllustrationSubmission,
  reportAudioSubmission, ensureAudioSlot,
  genFramePrompts, genProfilePrompt, listGenerations,
  type AgentInvocation,
} from "../api/ai";
import { createScenarioSnapshot } from "../api/scenario";
import { runVisualCiDiagnostic } from "../api/visualCi";
import { refreshChatBackgroundActivities } from "./chatBackgroundActivity";
import { substituteMacros } from "./chatMacros";
import type { ChatStreamEvent, IllustrationSceneSpec, AudioDialogueLine, VideoParams } from "../api/chatStreamProtocol";
import { normalizeInspirationCard } from "../api/chatStreamProtocol";
import {
  reduce as reduceGen, initialGenState,
  blocksDialogueSubmission, streamingBotId, needsConfirm, runningPromptId,
} from "./generationLifecycle";
import { useWorkflowOrchestration } from "./workflowOrchestration";
import { subscribeProgress } from "./comfyProgress";
import {
  needsImageInput, hasImageProvided, pickBestText,
  slimSnapshot as slimSnapshotPure, promptHistory,
  prepareConversationRegeneration, resolveLoraPromptMetadata, resolveGenerationPrompt,
  acceptSlimmedMessages, canCommitSnapshot, resolveVideoBaseImageRef, resolvePrevTailDesc,
  resolveTransitionBaseImage,
} from "./chatGeneration";
import {
  durableFinalizeSucceeded, WorkflowGenerationRuntime, pollWorkflowResult,
  type PendingGeneration, type WorkflowWatchObserver,
} from "./workflowGenerationRuntime";
import {
  applyProfileLoraTriggers, ensureAnimaIllustrationStyle, illustrationTemplateValues, normalizePromptProfile,
  replacePromptQualityLine, latentSizeFor, workflowFieldBinding,
} from "./imagePromptProfiles";
import {
  illustrationLoraConfigurationError, illustrationRequestMedia, illustrationWorkflowMedia,
  resolveIllustrationActors, resolveVideoMode, resolveVideoTemplateChoice,
  planFirstlastFrameTasks, firstlastFrameValues, firstlastSlotLayout, transitionVideoValues,
} from "./illustrationMedia";
import {
  audioTemplateValues, resolvableAudioLines, skippedAudioSpeakers, voiceReferenceFor,
} from "./audioGeneration";
import {
  applyRouteChoice, appendImageSlot, bindMediaSlotPrompt, dropMediaSlot, markMediaSlotFailed, appendAudioSlot, appendTransitionSlot,
  failMediaSlot, pruneUnsubmittedMediaSlots, reduceChatStreamEvent, resetMediaSlotForRetry, resolveMediaSlot,
  restoreSubmittedMediaSlots, upsertMessages, workflowMessages,
} from "./chatSessionEvents";
import { recoverCompactedSummaryImage } from "./contextManagement";
import { characterDetail } from "../api/characters";
import { recoverAgentRun, shouldRecoverAgentRun } from "./agentRecovery";
import { deletedMessageTombstones } from "./deletedMessageTombstones";
import { releaseAgentStream, type ActiveAgentStream } from "./agentStreamLifecycle";
import type { ImageQuality, WorkMode } from "./viewRouting";
import { useChatMaintenance } from "./useChatMaintenance";
import {
  comfyRegenerationUrl, legacyGenerationPrompt, resolveImageRegenerationModel,
  templateRegenerationSnapshot, workflowRegenerationSnapshot, workflowGenMetadata,
} from "./regeneration";
import { resolveEndpointProxy, resolveModelProxy, type ProxyMode } from "./modelProxy";
import {
  ConversationHistoryRuntime, resolveInitialHistory, type ConversationCheckpoint,
} from "./conversationHistoryRuntime";
import { useChatAgentQueue } from "./useChatAgentQueue";

type Model = { baseUrl: string; apiKey: string; modelName: string; proxyMode?: ProxyMode; providerProfile?: "openai_compatible" | "claude_compatible" };

export type Checkpoint = ConversationCheckpoint;

// 首页(home)=临时草稿区：草稿存模块级内存变量，随浏览器进程存活——
// 页面刷新(进程重开)即重置为空，但应用运行期间切走首页再回来仍保留。不落 localStorage / 后端快照。
let homeDraft: ChatMessage[] = [];

// 卡即作品开场白：读关联角色卡的 first_mes，作首条 bot 消息，替换 {{char}}/{{user}} 宏。读不到/无返回空串。
async function loadOpeningMessage(
  characterDir: string, cardName: string, userName: string,
): Promise<string> {
  try {
    const card = await characterDetail(characterDir, cardName);
    const first = card?.first_mes;
    const raw = typeof first === "string" ? first.trim() : "";
    return raw ? substituteMacros(raw, cardName, userName) : "";
  } catch {
    return "";
  }
}

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
  setShowPicker: (v: boolean) => void;           // 与 /w 选择浮层共享
  atBottomRef: MutableRefObject<boolean>;        // 与滚动跟随 UI 共享
  onNotify?: (msg: string, kind?: "info" | "error" | "success") => void; // 轻提示（对齐 WorkflowCard onNotify）
}

// PLACEHOLDER_BODY

// 跨导航持久化：切换页面时进度条和提示词参数不丢失
const persistedWfProgress: { current: number | null } = { current: null };
const persistedWfNode: { current: string } = { current: "" };
const persistedWfPromptParams: { current: Record<string, unknown> } = { current: {} };

export function useChatSession(deps: ChatSessionDeps) {
  const {
    repo, settings, setGeneratedCover, chat, genModel, videoModel, workMode, size, imageQuality,
    templates, setShowPicker, atBottomRef, onNotify,
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
  const cardNames = repo?.cardNames?.length ? repo.cardNames : (repo?.cardName ? [repo.cardName] : []);
  const openingCardName = repo?.openingCardName || repo?.cardName || cardNames[0] || "";
  const appearanceSource = mediaPreset?.appearanceSource === "character_card" ? "character_card" : "worldbook";
  const modelProxies = {
    chatProxyUrl: resolveModelProxy(chat.proxyMode, settings.proxyUrl, settings.proxyEnabled),
    genProxyUrl: resolveModelProxy(genModel.proxyMode, settings.proxyUrl, settings.proxyEnabled),
    videoProxyUrl: resolveModelProxy(videoModel?.proxyMode, settings.proxyUrl, settings.proxyEnabled),
    embedProxyUrl: resolveEndpointProxy(
      settings.embedModel.baseUrl, settings.embedModel.proxyMode,
      settings.proxyUrl, settings.proxyEnabled,
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
  const [wfProgress, setWfProgress] = useState<number | null>(persistedWfProgress.current);  // 工作流实时进度%（WS，null=无），跨导航持久化
  const [wfNode, setWfNode] = useState<string>(persistedWfNode.current);  // 当前执行的节点显示名（WS executing 消息），跨导航持久化
  // 包装 setter：同时更新持久化 ref，确保切换页面回来后进度不丢失
  const updateWfProgress = (v: number | null) => { persistedWfProgress.current = v; setWfProgress(v); };
  const updateWfNode = (v: string) => { persistedWfNode.current = v; setWfNode(v); };
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
  // 对话线 id = 仓库 id（首页用 "home"）：后端按此落盘多轮记忆与 RAG 知识库
  const threadId = repo?.id || "home";
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
    proxyUrl: settings.proxyEnabled ? settings.proxyUrl : "",
    ...modelProxies,
    styleTemplate: activeStyleTemplate(settings),
    agentId: settings.activeAgentId || "",
    streamOutput: settings.streamOutput,
    contextMaxTokens: settings.contextMaxTokens,
    historyPerRole: settings.historyPerRole,
    selfhealAttempts: settings.selfhealAttempts,
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
    appearanceSource,
    characterBaseImages,
    illustrationActorNames,
    styleBaseImage,
    ...overrides,
  });
  const workflowRuntimeRef = useRef<{ threadId: string; runtime: WorkflowGenerationRuntime } | null>(null);
  if (workflowRuntimeRef.current?.threadId !== threadId) {
    workflowRuntimeRef.current = { threadId, runtime: new WorkflowGenerationRuntime(threadId) };
  }
  const workflowRuntime = workflowRuntimeRef.current.runtime;
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
        homeDraft = next;
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

  // 进入仓库/切换时加载消息，三级兜底：本地 localStorage → 后端消息流快照 → langgraph 对话历史。
  useEffect(() => {
    let alive = true;
    loadedRef.current = false;
    let shownLocal = false;
    // 首页(home)=临时草稿区：从模块级 homeDraft 恢复（进程内切走切回保留，页面刷新即空）。
    // 不读 localStorage / 后端快照 / 后端历史。仅保留后台轮询——生成中切回来仍要看到进度。
    const isHome = threadId === "home";
    const local = isHome ? null : localStorage.getItem(chatKey);
    if (local) {
      try {
        const arr = JSON.parse(local) as ChatMessage[];
        if (arr.length > 0) {
          setMessages(arr);
          shownLocal = true;
        }
      } catch { /* 本地损坏则走后端兜底 */ }
    }
    // 切回/刷新时若后台仍有生成任务在跑，轮询快照等其落盘后自动回显。
    // 必须无论快照是否已有内容都执行——正常对话后快照必然非空，若放在
    // 加载兜底的 early-return 之后就永远跑不到，等于后台化失效。
    const maybeStartBgPoll = async () => {
      try {
        if (!shouldRecoverAgentRun(threadId)) return;
        if (!alive || recoveryActiveRef.current) return;
        const st = await fetchAgentRunning(threadId);
        if (!alive || !st.running) return;
        startAgentRecovery(threadId, repo?.id);
      } catch { /* 状态接口失败，忽略 */ }
    };
    (async () => {
      // 首页临时草稿区：从 homeDraft 恢复（进程内切回保留，刷新即空），仅接后台轮询（生成中切回可见进度）。
      if (isHome) {
        if (homeDraft.length > 0) setMessages(homeDraft);
        loadedRef.current = true;
        await maybeStartBgPoll();
        return;
      }
      try {
        const snap = await fetchSnapshot(threadId);
        if (!alive) return;
        if (snap.items && snap.items.length > 0) {
          const snapshotMessages = restoreSubmittedMediaSlots(
            (snap.items as ChatMessage[]).map((m) => m.inspiration
              ? { ...m, inspiration: normalizeInspirationCard(m.inspiration as unknown as Record<string, unknown>) }
              : m),
            workflowRuntime.list(),
          );
          const pruned = pruneUnsubmittedMediaSlots(snapshotMessages);
          let restoredMessages = pruned.messages;
          for (const item of pruned.removed) {
            void reportIllustrationFailure({
              threadId, repoId: repo?.id || threadId,
              messageId: item.messageId, slotId: item.slotId,
              stage: "resume_unsubmitted", error: "页面恢复时发现未提交到 ComfyUI 的孤儿插画槽",
            }).catch(() => undefined);
          }
          const needsSummaryImage = snapshotMessages.length === 1
            && snapshotMessages[0].text.startsWith("【历史摘要】")
            && !snapshotMessages[0].image
            && !snapshotMessages[0].parts?.some((part) => part.type === "image");
          if (needsSummaryImage) {
            try {
              const history = await fetchHistory(threadId);
              restoredMessages = recoverCompactedSummaryImage(snapshotMessages, history.items || []);
            } catch { /* 旧摘要修复失败时仍显示原摘要 */ }
          }
          setMessages(restoredMessages);
          if (restoredMessages !== snapshotMessages) {
            try { localStorage.setItem(chatKey, JSON.stringify(restoredMessages)); } catch { /* ignore */ }
            saveSnapshot(threadId, restoredMessages).catch(() => {});
          }
          loadedRef.current = true;
          await maybeStartBgPoll();
          return;
        }
        // 成功返回的空快照是当前 UUID 的历史真源；仅请求失败时才允许本地缓存兜底。
        const initialHistory = resolveInitialHistory(
          snap.items as ChatMessage[], shownLocal ? messagesRef.current : [],
        );
        if (initialHistory.length === 0) {
          shownLocal = false;
          setMessages([]);
          try { localStorage.removeItem(chatKey); } catch { /* ignore */ }
        }
      } catch { /* 快照接口失败，继续兜底 */ }
      if (shownLocal) { loadedRef.current = true; await maybeStartBgPoll(); return; }  // 本地已渲染、后端无快照
      try {
        const r = await fetchHistory(threadId);
        if (!alive) return;
        const historyMessages = (r.items || []).map((m) => {
          const imgs = m.images || [];
          const parts: MsgPart[] = [];
          if (m.content) parts.push({ type: "text", text: m.content });
          for (const u of imgs) parts.push({ type: "image", url: u });
          return {
            id: crypto.randomUUID(),
            role: m.role === "assistant" ? "assistant" : "user",
            text: m.content,
            parts: parts.length > 0 ? parts : undefined,
          } as ChatMessage;
        });
        // 卡即作品：全无历史(本地/快照/后端皆空)且关联角色卡 → 用卡的 first_mes 作开场白首条。
        // 落一次后进快照，下次从快照恢复，不再重复注入（幂等）。
        if (historyMessages.length === 0 && openingCardName && settings.characterDir) {
          const opening = await loadOpeningMessage(settings.characterDir, openingCardName, effectivePersona.name || "");
          if (!alive) return;
          if (opening) {
            setMessages([{ id: crypto.randomUUID(), role: "assistant", text: opening }]);
          } else {
            setMessages(historyMessages);
          }
        } else {
          setMessages(historyMessages);
        }
      } catch { /* 后端未起/无历史，保持空 */ }
      finally { if (alive) loadedRef.current = true; }
      await maybeStartBgPoll();
    })();
    return () => {
      scenarioSnapshotTurnRef.current = -1;
      alive = false;
      activeThreadRef.current = "";
      recoveryTokenRef.current += 1;
      recoveryActiveRef.current = false;
      agentBusyRef.current = false;
      bgRunningRef.current = false;
      abortRef.current = releaseAgentStream(abortRef.current, "navigation");
      if (snapTimer.current) { clearTimeout(snapTimer.current); snapTimer.current = null; }
      wsUnsubRef.current?.(); wsUnsubRef.current = null;  // 关进度 WS，避免切仓库泄漏连接
      dispatch({ t: "reset" });  // 清空生成状态 + 待发队列，避免串到新仓库
    };
  }, [threadId]);
  // APPEND_HERE

  // 存快照前给图片瘦身：把用户上传的 data:URI 大图落盘转 local-view 小地址。
  // data:URI 只来自用户上传的参考图 → 落 reference/ 子夹，与生成图（仓库根目录）分开。
  const persistDataUri = async (src: string): Promise<string> => {
    if (typeof src === "string" && src.startsWith("data:") && settings.outputDir && repo?.id) {
      try {
        const s = await saveLocalSrc({ src, repoId: repo.id, outputDir: settings.outputDir, subdir: "reference" });
        return localViewUrl(s.path);
      } catch { /* 保留原图 */ }
    }
    return src;
  };
  const slimSnapshot = (msgs: ChatMessage[]) => slimSnapshotPure(msgs, persistDataUri);

  // 消息变化时持久化：本地快取 + 后端快照统一防抖；本地也写瘦身后内容，避免 dataURI 大图撑爆 localStorage。
  // 首页(home)=临时草稿区：只写模块级 homeDraft（进程内切走切回保留，刷新即空），不落 localStorage / 后端快照。
  useEffect(() => {
    if (!loadedRef.current) return;  // 加载完成前不写，防止空数组覆盖
    if (threadId === "home") { homeDraft = messages; return; }  // 首页草稿区：仅存内存
    if (snapTimer.current) clearTimeout(snapTimer.current);
    const tid = threadId;
    const original = messages;
    snapTimer.current = setTimeout(async () => {
      if (bgRunningRef.current || streamingId || regeneratingIds.size > 0) return;
      if (!canCommitSnapshot(
        messagesRef.current, original, activeThreadRef.current, tid,
      )) return;
      // 流式正文或重生成尚未终态时不能创建世界状态快照，否则会把半截正文与未完成写回固化。
      const full = await slimSnapshot(original);
      // 图片落盘可能耗时；期间若追加了用户消息、开始新 Agent 或切换仓库，
      // 旧任务连 localStorage/后端都不得写，不能只保护 React 内存。
      if (agentBusyRef.current || bgRunningRef.current || !canCommitSnapshot(
        messagesRef.current, original, activeThreadRef.current, tid,
      )) return;
      // localStorage 只存轻量快取：去掉 capturedGraph，parts / portsPlan 里的 dataURI 已被 slimSnapshot 转成本地 URL。
      const slim = full.map((m) =>
        m.workflow ? { ...m, workflow: { ...m.workflow, capturedGraph: null } } : m,
      );
      try {
        localStorage.setItem(chatKey, JSON.stringify(slim));
      } catch { /* 超额等忽略 */ }
      saveSnapshot(tid, full).then(() => {
        const turn = full.filter((message) => message.role === "assistant"
          && Boolean((message.text || "").trim())).length;
        if (settings.outputDir && repo?.id && turn > scenarioSnapshotTurnRef.current) {
          scenarioSnapshotTurnRef.current = turn;
          void createScenarioSnapshot({
            output_dir: settings.outputDir, repo_id: repo.id, turn,
            label: "自动回合快照", dedupe_key: `turn:${turn}`,
          }).catch(() => { scenarioSnapshotTurnRef.current = turn - 1; });
        }
      }).catch(() => {});  // 后端未起则忽略本地仍在
      if (tid === threadId && JSON.stringify(full) !== JSON.stringify(original)) {
        setMessages((current) => acceptSlimmedMessages(current, original, full));
      }
    }, 600);
  }, [messages, chatKey, threadId, settings.outputDir, repo?.id, streamingId, regeneratingIds.size]);

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
      const comfyuiUrl = comfyRegenerationUrl(regeneration) || settings.comfyuiUrl;
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
          // 角色回归参考图：取当前作品角色底图的第一张作为相似度基准（冷倾雪/虞妙玥等）
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
                  proxy: resolveModelProxy(vlm.proxyMode, settings.proxyUrl, settings.proxyEnabled),
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

  const discardFailedIllustration = (
    messageId: string, slotId: string, stage: string, error: string, promptId = "",
    retryArgs?: unknown[],
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
        // 出队
        activePromptQueue.current = activePromptQueue.current.filter(id => id !== pending.prompt_id);
        if (activePromptQueue.current.length === 0) {
          stopProgress();
          setSlowWatchPromptId(null);
          dispatch({ t: "workflowDone", promptId: pending.prompt_id });
        } else {
          // 切换到下一个排队的任务
          const nextId = activePromptQueue.current[0];
          const nextPending = workflowRuntime.list().find(p => p.prompt_id === nextId);
          if (nextPending) {
            const nextUrl = comfyRegenerationUrl(nextPending.regeneration) || settings.comfyuiUrl;
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
        activePromptQueue.current = activePromptQueue.current.filter(id => id !== pending.prompt_id);
        if (activePromptQueue.current.length === 0) {
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
      activePromptQueue.current = activePromptQueue.current.filter(id => id !== pending.prompt_id);
      if (activePromptQueue.current.length === 0) {
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
        activePromptQueue.current = activePromptQueue.current.filter(id => id !== pending.prompt_id);
        if (activePromptQueue.current.length === 0) setSlowWatchPromptId(null);
      }
    },
    stalled: (pending) => {
      // 停顿守卫：任务一直卡在排队、从未观察到节点开始运转，早停而非死等到硬超时
      if (pending.target) {
        discardFailedIllustration(
          pending.target.messageId, pending.target.slotId, "stalled",
          "长时间未开始加载节点（ComfyUI 队列未运转），已停止", pending.prompt_id,
        );
        refreshChatBackgroundActivities();
        return;
      }
      activePromptQueue.current = activePromptQueue.current.filter(id => id !== pending.prompt_id);
      if (activePromptQueue.current.length === 0) {
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
  ) => {
    const comfyuiUrl = comfyRegenerationUrl(regeneration) || settings.comfyuiUrl;
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
      activePromptQueue.current.push(promptId);
      if (activePromptQueue.current.length === 1) {
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

  // 模板是否定义了图像输入口 / 图值是否已填 → 见 lib/chatGeneration（纯逻辑，已抽出可测）

  // 剧情高潮点异步出图/出视频：后端发来 booru 提示词 + motion 动态强度，按本作品预设组 values 提交 ComfyUI，
  // 走 pollResult 现成异步闭环（徽记显示进度 + 离开继续 + 点击返回）。未预设模板 → 静默跳过。
  // 智能模态：smartVideo 开 且 motion>=2 且 预设了视频模板 → 用视频模板，否则用图片模板。
  const submitIllustration = async (
    prompt: string, motion = 0, actors: string[] = [], messageId: string, slotId: string,
    sceneSpec?: IllustrationSceneSpec, turnId = "", source: "automatic" | "manual" = "automatic",
    eventVideoMode?: string, firstFrameDesc = "", lastFrameDesc = "",
    prevTailDesc = "", lastFrameUrl = "", videoPrompt = "", transition = "",
    transitionVideoPrompt = "", transitionVideoParams?: VideoParams,
  ) => {
    // 重试参数快照（按签名顺序）：失败槽「重新生成」按钮直接展开重调（source 翻转为 manual 跳过 claim）。
    const retrySnapshot: unknown[] = [
      prompt, motion, actors, messageId, slotId, sceneSpec, turnId, source,
      eventVideoMode, firstFrameDesc, lastFrameDesc, prevTailDesc, lastFrameUrl,
      videoPrompt, transition, transitionVideoPrompt, transitionVideoParams,
    ];
    const failSlot = (stage: string, error: string) =>
      discardFailedIllustration(messageId, slotId, stage, error, "", retrySnapshot);
    const preset = settings.mediaInsert?.[repo?.id || ""];
    if (!preset) { failSlot("configuration", "当前作品没有配置自动插画模板"); return; }
    if (source === "automatic") {
      // 防抖快照可能尚未把 pending 槽落库，认领在快照里找不到槽会返回 False。
      // 2026-08-31 晚实锤「新的任务请求没有生图」：认领失败曾静默 return → 槽永远
      // pending、无 submitted/failed 可查。现在刷新快照后重试一次，仍失败就把槽
      // 标记为失败（可见 + 可重新生成），绝不再静默。
      await saveSnapshot(threadId, messagesRef.current).catch(() => undefined);
      let claim = { claimed: false };
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          claim = await claimIllustrationSubmission({ threadId, messageId, slotId });
          if (claim.claimed) break;
        } catch (error) {
          failSlot("submission_claim", error instanceof Error ? error.message : "自动插画提交认领失败");
          return;
        }
        if (attempt === 0) {
          await new Promise((resolve) => setTimeout(resolve, 400));
          await saveSnapshot(threadId, messagesRef.current).catch(() => undefined);
        }
      }
      if (!claim.claimed) {
        failSlot("submission_claim", "自动插画提交认领失败（槽位未就绪或已被认领）");
        return;
      }
    }
    if (!prompt.trim() && !sceneSpec?.narrative.trim()) {
      failSlot("prompt", "剧情出图提示词为空");
      return;
    }
    // V1.5/B1：事件 videoMode 优先，其次 preset.videoMode，缺省 climax
    const videoMode = resolveVideoMode(preset, eventVideoMode);
    // V1.5/B3（R4）：firstlast 楼层触发不看 motion；climax 维持 smartVideo && motion>=2
    const useVideo = resolveVideoTemplateChoice(preset, videoMode, motion);
    // V1.6/P5+（2026-08-28 用户拍板）：首尾帧是独立图片模式——只配图片模板即生效；
    // 视频模式跟随该选项推导，有视频模板才追加视频任务。
    const useFirstlastImages = videoMode === "firstlast" && !!preset.templateId;
    const chosenId = useVideo ? preset.videoTemplateId! : preset.templateId;
    if (!chosenId) {
      failSlot("configuration", "当前图/视频模式没有配置工作流模板");
      return;
    }
    const tpl = templates.find((t) => t.id === chosenId);
    if (!tpl) {
      failSlot("configuration", "已保存的工作流模板不存在，请重新选择模板");
      return;
    }
    // ⑥ 按高潮真实角色挑 LoRA/底图。single 命中时串联角色 LoRA，无命中才回退风格；
    // multi 固定加载默认风格并叠加全部在场角色 LoRA。旧 preset.loraName 只作风格兼容值。
    const knownActorNames = preset.appearanceSource === "character_card"
      ? cardNames : Object.keys(preset.characterLoras || {});
    const resolvedActors = resolveIllustrationActors(
      actors, sceneSpec?.subjects, knownActorNames,
    );
    const workflowMedia = illustrationWorkflowMedia(preset, resolvedActors, cardNames);
    const { loras, loraName, loraWeight, baseImage, characterLora } = workflowMedia;
    // 角色 LoRA 生图：主角名 = 首个配置了角色 LoRA 的在场角色（后端据此做
    // 「角色_轮次_序号」可读命名；非角色 LoRA/兜底风格留空 → 时间戳命名）
    const characterLoraActor = characterLora
      ? (resolvedActors.find((name) => preset.characterLoras?.[name]?.loraName) || "")
      : "";
    const loraConfigurationError = illustrationLoraConfigurationError(preset, workflowMedia);
    if (loraConfigurationError) {
      failSlot("configuration", loraConfigurationError);
      return;
    }
    // V1.1 视频首帧底图：优先取「已完成插画」（本槽 > 最近一次），否则回退用户手动指定；
    // 模板无图像口 → 纯文生视频（resolveVideoBaseImage 返回 undefined 且不拦截）。
    // M2.1：ref 版本同时返回底图来源槽引用，视频入库时记录 derived_from。
    const videoBaseRef = useVideo
      ? resolveVideoBaseImageRef({
          tpl, messageId, slotId,
          messages: messagesRef.current,
          manualBaseImage: baseImage || undefined,
        })
      : undefined;
    // V1.5/W2 首帧复用（坑C：有图前提）：transition=reuse 且反查到上尾帧图 → 首帧底图用
    // 上尾帧图（视觉延续）。无尾帧图资产 → reuse 作废，维持原路径（独立生成首帧）。
    // 仅 firstlast 消费（climax 无首帧复用）；reuse 之外（regenerate/ambiguous/空）不复用。
    const baseImageForUse = resolveTransitionBaseImage({
      videoMode, transition, messages: messagesRef.current,
      fallback: videoBaseRef?.url ?? baseImage,
    }) || "";
    let negativePrompt = preset.negativePrompt?.trim() || sceneSpec?.negative_prompt || "";
    // LoRA 触发词查表与机械前置（对齐 /a 编排 lora_inject 思路）：主图与首尾帧图共用。
    // 生效 LoRA 的触发词必须逐字精确（大脑会漏写/改写 → LoRA 不生效却看不出），按文件名查触发词表
    // 前置到正向提示词；已在提示词里出现的词不重复注入（大小写不敏感）。查表失败硬失败（配置问题要暴露）。
    let loraTriggerItems: Awaited<ReturnType<typeof listLoras>>["items"] = [];
    if (loras.length) {
      try {
        loraTriggerItems = (await listLoras()).items || [];
        for (const lora of loras) {
          const metadata = resolveLoraPromptMetadata(loraTriggerItems, lora.name);
          if (!metadata.found) {
            throw new Error(`实际加载的 LoRA 没有精确元数据记录：${lora.name}`);
          }
        }
      } catch (error) {
        failSlot(
          "lora_metadata",
          error instanceof Error ? error.message : "LoRA触发词与建议提示词读取失败",
        );
        return;
      }
    }
    const withLoraTriggers = (text: string): string => {
      let out = text;
      for (const lora of loras) {
        const metadata = resolveLoraPromptMetadata(loraTriggerItems, lora.name);
        out = applyProfileLoraTriggers(out, promptProfile, metadata.additions);
      }
      return out;
    };
    // V1.6/P5+：首尾帧独立图片模式跳过高潮 Profile 渲染（帧提示词在生成循环内走同一 Profile 编译链单独编译）。
    if (!useFirstlastImages) {
    // 重新生成（manual）必须重编译：重试快照里的 profile_prompt 是当初链路的产物——
    // 编译链升级（措辞收敛/预设瘦身/LoRA 绑定修正）后旧成稿不会自愈，复用=永远提交污染提示词
    //（2026-08-30 用户实锤：重试反复提交带旧措辞的成稿）。automatic 路径仍复用 produce 已编译结果。
    const storedProfile = sceneSpec?.profile_prompt;
    const storedProfileUsable = !!storedProfile
      && sceneSpec.profile === promptProfile
      && source !== "manual";
    if (storedProfileUsable && sceneSpec) {
      prompt = storedProfile;
    } else if (sceneSpec) {
      try {
        const rendered = await genProfilePrompt(
          promptProfile,
          {
            ...sceneSpec, actors: resolvedActors, character_lora: characterLora,
            repo_id: repo?.id || "", thread_id: repo?.id || "", turn_id: turnId,
          },
          { ...chat, proxyUrl: modelProxies.chatProxyUrl },
          {
            presetDir: settings.presetDir,
            presetName: settings.activePresetName,
            userName: effectivePersona.name,
          },
        );
        prompt = rendered.prompt;
        negativePrompt = preset.negativePrompt?.trim() || rendered.negative_prompt || negativePrompt;
      } catch (error) {
        failSlot(
          "prompt_profile",
          error instanceof Error ? error.message : "智能提示词生成失败",
        );
        return;
      }
    }
    if (!prompt.trim()) {
      failSlot("prompt_profile", "智能提示词结果为空");
      return;
    }
    if (promptProfile === "anima_tags") {
      prompt = replacePromptQualityLine(
        prompt, preset.qualityPrompt || "", sceneSpec?.rating || "sfw",
      );
      prompt = ensureAnimaIllustrationStyle(
        prompt, loras.some((lora) => !lora.character),
      );
    }
    prompt = withLoraTriggers(prompt);
    }
    // V1.6/P5+（2026-08-29 用户拍板）：首尾帧提示词与高潮点生图完全同构——后端
    // /ai/prompt/profile/frames 先做「时点提取」（首/尾各自时点的英文结构化 action/visual_facts，
    // 帧描述走 @(…)@ 防拦截保护），再逐帧走同一 Profile 编译器（同一校验/带因重写/兜底），
    // 一次调用出两帧成品。触发词由 withLoraTriggers 统一前置。
    // 2026-08-29 验收「生成垃圾图」实锤：编译失败时把中文帧描述原文当正向提示词提交
    // （首帧正向只有 8 字符对白「儿子听娘的。」）——中文原文永远不是合法 Krea2 提示词，
    // 这里禁止静默降级：编译失败必须显式失败（失败槽可见、可重新生成），绝不提交原文。
    const frameCompiled: Partial<Record<"first" | "last", string>> = {};
    const compileFramePrompt = (frame: "first" | "last", frameDesc: string): string => {
      const compiled = frameCompiled[frame];
      if (!compiled || !compiled.trim()) {
        throw new Error(
          `帧提示词编译失败（${frame === "first" ? "首" : "尾"}帧），已阻止把中文原文直接提交给 ComfyUI`,
        );
      }
      return withLoraTriggers(compiled);
    };
    // 底图需先上传到 ComfyUI input 目录，取回可供 LoadImage 引用的文件名
    let uploadedImage = "";
    const needsImage = tpl.exposed.some((f) => workflowFieldBinding(f) === "base_image");
    // V1.1 视频分支：模板声明图像口但拿不到底图 → 拦截（不空图提交图生视频工作流）
    if (useVideo && needsImage && !baseImageForUse) {
      failSlot("image_required", "视频模板需要首帧底图，但当前没有可用的已完成插画，请先出图或用预设指定底图");
      return;
    }
    if (needsImage && baseImageForUse) {
      try {
        const blob = await (await fetch(localViewUrl(baseImageForUse))).blob();
        const file = new File([blob], baseImageForUse.split(/[\\/]/).pop() || "base.png", { type: blob.type || "image/png" });
        uploadedImage = (await uploadImage(file, settings.comfyuiUrl)).name;
      } catch {
        // 底图上传失败：图片分支退化为纯文生图；视频分支按文档终态失败（不重试乘法）
        if (useVideo) {
          failSlot("upload", "首帧底图上传 ComfyUI 失败");
          return;
        }
      }
    }
    // V1.5/B3 firstlast 双帧：事件携带尾帧图（lastFrameUrl）→ 上传为 last_frame_image；
    // 上传失败/无尾帧图 → 降级为首帧单图提交（后端 firstlast 缺尾帧有 warning，不挂死槽）。
    let uploadedLastFrameImage = "";
    if (useVideo && videoMode === "firstlast" && lastFrameUrl) {
      try {
        const blob = await (await fetch(localViewUrl(lastFrameUrl))).blob();
        const file = new File([blob], lastFrameUrl.split(/[\\/]/).pop() || "last.png", { type: blob.type || "image/png" });
        uploadedLastFrameImage = (await uploadImage(file, settings.comfyuiUrl)).name;
      } catch {
        uploadedLastFrameImage = "";
      }
    }
    // 按 exposed 的隐藏 binding 组 values；提交 key 始终是“节点id.原字段名”。
    const latentSize = latentSizeFor(
      sceneSpec?.aspect_ratio || "2:3",
      preset.latentLongEdge === 2048 || preset.latentLongEdge === 4096
        ? preset.latentLongEdge : 1024,
    );
    // V1.5/B2：prevTailDesc 兜底——事件没带时反查最近一条已完成视频槽的尾帧描述
    const prevTailDescForUse = prevTailDesc
      || resolvePrevTailDesc(messagesRef.current)?.lastFrameDesc || "";
    // V1.6/P5 首尾帧顺序链：先出首尾帧图（图片模板生图），双图 ready 再提视频。
    // 决策 A（2026-08-26 拍板）：首尾帧生图复用 preset.templateId，prompt=事件
    // firstFrameDesc/lastFrameDesc；reuse 免首帧生图（W2 已把上尾帧图复用为底图）；
    // 尾帧有事件图直接用。首帧生图失败 → 明确失败（视频必有首帧）；尾帧生图失败 →
    // 降级首帧单图（不挂死，后端 firstlast 缺尾帧有 warning）。
    let frameFirstImage = uploadedImage;
    let frameLastImage = uploadedLastFrameImage;
    let firstFrameGenerated = false; // W3：首帧是否独立生图成功（决定要不要发转场任务）
    let frameFirstPromptId = "";
    let frameLastPromptId = "";
    // 编译后帧提示词：提交 ComfyUI 用它，回填入库/上报 trace 必须同源（2026-08-29 验收：
    // 资产库显示帧描述原文的根因=入库链传了 firstFrameDesc/lastFrameDesc 原文）。
    let frameFirstPrompt = "";
    let frameLastPrompt = "";
    let frameFirstValueKeys: string[] = [];
    let frameLastValueKeys: string[] = [];
    let frameFirstMedia: ReturnType<typeof illustrationWorkflowMedia> | undefined;
    let frameLastMedia: ReturnType<typeof illustrationWorkflowMedia> | undefined;
    if (useFirstlastImages) {
      const frameTpl = templates.find((t) => t.id === preset.templateId);
      if (!frameTpl) {
        failSlot("configuration", "firstlast 首尾帧生图需要配置图片工作流模板");
        return;
      }
      const plan = planFirstlastFrameTasks({
        transition,
        prevTailUrl: resolvePrevTailDesc(messagesRef.current)?.lastFrameUrl,
        firstFrameDesc, lastFrameDesc, lastFrameUrl,
      });
      if (useVideo && !plan.canGenerateVideo) {
        failSlot("image_required", "firstlast 视频需要首帧图：既无上尾帧图可复用，也无首帧画面描述可生成");
        return;
      }
      if (!useVideo && !plan.tasks.length) {
        failSlot("image_required", "首尾帧生图缺少画面：既无上尾帧图可复用，也无首帧/尾帧画面描述");
        return;
      }
      // 逐帧 LoRA（2026-08-30 用户反馈实锤：首帧画面是凌若冰、尾帧是舞姬恋，
      // 却整单都挂了状态栏在场角色的 LoRA）。一轮请求只解析一次 actors，但首/尾帧
      // 画面可能各是不同角色——按帧描述原文逐帧命中已配置角色；未命中回退请求级 actors。
      const frameMediaFor = (desc: string) => {
        const hits = knownActorNames.filter((name) => name && desc.includes(name));
        return illustrationWorkflowMedia(preset, hits.length ? hits : resolvedActors, cardNames);
      };
      const buildFrameValues = (desc: string, media = workflowMedia) => firstlastFrameValues(
        frameTpl.exposed, desc,
        {
          negativePrompt: preset.negativePrompt,
          loraName: media.loraName,
          loraWeight: media.loraWeight,
          baseImage: media.baseImage,
        },
        latentSize,
      );
      // 帧提示词同构编译：一次调用出两帧成品（时点提取+同一 Profile 编译器）。
      if (sceneSpec) {
        const frameDescs: { first?: string; last?: string } = {};
        for (const task of plan.tasks) {
          if (task.kind === "generate") frameDescs[task.frame] = task.desc;
        }
        if (frameDescs.first || frameDescs.last) {
          try {
            const compiled = await genFramePrompts(
              promptProfile,
              {
                ...sceneSpec, actors: resolvedActors, character_lora: characterLora,
                repo_id: repo?.id || "", thread_id: repo?.id || "", turn_id: turnId,
              },
              frameDescs,
              { ...chat, proxyUrl: modelProxies.chatProxyUrl },
              {
                presetDir: settings.presetDir,
                presetName: settings.activePresetName,
                userName: effectivePersona.name,
              },
            );
            for (const frame of ["first", "last"] as const) {
              const item = compiled.frames[frame];
              if (item?.prompt?.trim()) frameCompiled[frame] = item.prompt;
            }
          } catch (error) {
            console.warn("[auto-video] 帧提示词同构编译失败，降级帧描述原文", error);
          }
        }
      }
      for (const task of plan.tasks) {
        if (task.kind === "reuse" || task.kind === "existing") continue; // 现成图已在上传段处理
        const isFirst = task.frame === "first";
        try {
          const framePrompt = compileFramePrompt(task.frame, task.desc);
          const frameMedia = frameMediaFor(task.desc);
          const frameLoras = frameMedia.loras.map(({ name, weight }) => ({ name, weight }));
          const frameValues = buildFrameValues(framePrompt, frameMedia);
          const recordIds = (res: { prompt_id: string }) => {
            if (isFirst) { frameFirstPromptId = res.prompt_id; frameFirstPrompt = framePrompt; frameFirstValueKeys = Object.keys(frameValues).sort(); frameFirstMedia = frameMedia; }
            else { frameLastPromptId = res.prompt_id; frameLastPrompt = framePrompt; frameLastValueKeys = Object.keys(frameValues).sort(); frameLastMedia = frameMedia; }
          };
          const runOnce = async (): Promise<{ prompt_id: string }> => {
            const res = await submitWorkflow(
              preset.templateId, frameValues, settings.comfyuiUrl,
              framePrompt, frameLoras, preset.loraMode || "single",
            );
            const pid = res.prompt_id;
            if (!pid) throw new Error("ComfyUI 没有返回任务 ID");
            recordIds({ prompt_id: pid });
            return { prompt_id: pid };
          };
          // 提交阶段自愈（对齐 stalled 自愈，2026-08-29 验收「新对话没有触发生图」）：
          // 首次提交失败（后端挂起超时/ComfyUI 瞬断）→ 中断可能残留的任务后自动重提一次；
          // 再失败才进外层 failSlot（失败槽可见+可重新生成）。上限 1 次防循环。
          let r: { prompt_id: string };
          try {
            r = await runOnce();
          } catch {
            await interruptComfy(settings.comfyuiUrl).catch(() => undefined);
            r = await runOnce();
          }
          let outcome = await pollWorkflowResult(r.prompt_id, settings.comfyuiUrl, "image");
          if (outcome.kind === "stalled") {
            // 卡死自愈（2026-08-29 用户需求）：队列无节点运转 → 删除坏死任务（清队列+中断），
            // 自动重新提交一次；再卡死才按失败处理。上限 1 次防循环。
            await interruptComfy(settings.comfyuiUrl, r.prompt_id).catch(() => undefined);
            r = await runOnce();
            outcome = await pollWorkflowResult(r.prompt_id, settings.comfyuiUrl, "image");
          }
          if (outcome.kind !== "complete" || !outcome.result.images[0]) {
            throw new Error(outcome.kind === "failed" || outcome.kind === "stalled"
              ? outcome.error : "生图未产出图像");
          }
          const inputName = await moveComfyOutputToInput(outcome.result.images[0], settings.comfyuiUrl);
          if (isFirst) { frameFirstImage = inputName; firstFrameGenerated = true; }
          else frameLastImage = inputName;
        } catch (error) {
          const message = error instanceof Error ? error.message : "首尾帧生图失败";
          if (isFirst) { failSlot("frame_gen", `首帧生图失败：${message}`); return; }
          // 尾帧生图失败：视频模式降级首帧单图（视频还能出，不挂死）；独立图片模式尾帧=楼层
          // 唯一新画面，静默降级会让主槽 pending 悬挂（刷新后变孤儿槽被清理，用户完全无感
          // ——2026-08-29 验收「什么图片都没有」实锤），必须显式失败。
          if (!useVideo) { failSlot("frame_gen", `尾帧生图失败：${message}`); return; }
          console.warn("[auto-video] 尾帧生图失败，降级首帧单图", { message });
        }
      }
      // V1.6/P5+ 独立图片模式回填（2026-08-28 拍板）：双帧图经 pollResult 走既有回填+入库全链。
      // useVideo 时主槽留给视频正片（避免同槽双 pollResult 竞态），双帧图走 :first/:last 副槽；
      // 仅图片模式时主槽=本楼层新画面（layout.main），尾帧新图进 :last。
      const layout = firstlastSlotLayout(plan);
      if (layout) {
        const lastSlotId = `${slotId}:last`;
        const frameTarget = (sid: string) => ({ messageId, slotId: sid, background: true as const });
        const reportFrame = (
          promptId: string, sid: string, framePrompt: string, keys: string[],
          media: ReturnType<typeof illustrationWorkflowMedia> = workflowMedia,
        ) => {
          void reportIllustrationSubmission({
            threadId, repoId: repo?.id || threadId, turnId, messageId, slotId: sid,
            templateId: preset.templateId, promptId, prompt: framePrompt, promptProfile,
            loraName: media.loraName, loraWeight: media.loraWeight,
            latentWidth: latentSize.width, latentHeight: latentSize.height,
            loraMode: preset.loraMode || "single",
            loraNames: media.loras.map(({ name }) => name),
            valueKeys: keys,
            source,
          }).catch(() => undefined);
        };
        if (useVideo) {
          // 视频模式：主槽=正片（C 段提交），双帧图入 :first/:last 副槽（reuse 首帧复用上楼图，无新图不建槽）
          if (layout.main === "first_prompt" && frameFirstPromptId) {
            setMessages((current) => appendImageSlot(current, messageId, `${slotId}:first`, frameFirstPrompt));
            pollResult(frameFirstPromptId, [], undefined, frameTarget(`${slotId}:first`), frameFirstPrompt, "image");
            reportFrame(frameFirstPromptId, `${slotId}:first`, frameFirstPrompt, frameFirstValueKeys, frameFirstMedia);
          }
          if (frameLastPromptId) {
            setMessages((current) => appendImageSlot(current, messageId, lastSlotId, frameLastPrompt));
            pollResult(frameLastPromptId, [], undefined, frameTarget(lastSlotId), frameLastPrompt, "image");
            reportFrame(frameLastPromptId, lastSlotId, frameLastPrompt, frameLastValueKeys, frameLastMedia);
          }
        } else {
          // 独立图片模式：主槽=本楼层新画面，尾帧新图进 :last
          if (layout.main === "first_prompt" && frameFirstPromptId) {
            pollResult(frameFirstPromptId, [], undefined, frameTarget(slotId), frameFirstPrompt, "image");
            reportFrame(frameFirstPromptId, slotId, frameFirstPrompt, frameFirstValueKeys, frameFirstMedia);
          } else if (layout.main === "last_prompt" && frameLastPromptId) {
            pollResult(frameLastPromptId, [], undefined, frameTarget(slotId), frameLastPrompt, "image");
            reportFrame(frameLastPromptId, slotId, frameLastPrompt, frameLastValueKeys, frameLastMedia);
          } else if (layout.main === "last_frame_url" && lastFrameUrl) {
            setMessages((current) => resolveMediaSlot(current, messageId, slotId, lastFrameUrl, "image"));
          } else if (layout.main === "prev_tail_url") {
            const url = resolvePrevTailDesc(messagesRef.current)?.lastFrameUrl;
            if (url) setMessages((current) => resolveMediaSlot(current, messageId, slotId, url, "image"));
          }
          if (layout.lastSlot && frameLastPromptId) {
            setMessages((current) => appendImageSlot(current, messageId, lastSlotId, lastFrameDesc));
            pollResult(frameLastPromptId, [], undefined, frameTarget(lastSlotId), lastFrameDesc, "image");
            reportFrame(frameLastPromptId, lastSlotId, lastFrameDesc, frameLastValueKeys);
          }
        }
      }
    }
    // W3 转场任务（2 任务排队）：firstlast + 首帧独立生成（非 reuse）+ 后端下发转场提示词 →
    // 先提交转场视频（图片1=上尾帧、图片2=当前首帧），正片随后提交（ComfyUI 队列顺序执行）。
    // 坑F：无上尾帧图 → 降级文字转场（不拦截）；转场失败/提交失败 → 不挂死正片（仅槽位标失败）。
    if (useVideo && videoMode === "firstlast" && transition !== "reuse"
        && transitionVideoPrompt && firstFrameGenerated) {
      const transitionSlotId = `${slotId}:transition`;
      const prevTailRef = resolvePrevTailDesc(messagesRef.current);
      let prevTailInput = "";
      if (prevTailRef?.lastFrameUrl) {
        try {
          prevTailInput = await uploadRemoteImageToInput(
            localViewUrl(prevTailRef.lastFrameUrl), settings.comfyuiUrl,
          );
        } catch (error) {
          console.warn("[auto-video] 上尾帧图上传失败，转场降级文字转场", error);
        }
      }
      setMessages((current) => appendTransitionSlot(
        current, messageId, transitionSlotId, transitionVideoPrompt, transitionVideoParams,
      ));
      const transitionValues = transitionVideoValues(
        tpl.exposed, transitionVideoPrompt,
        { negativePrompt: preset.negativePrompt, loraName, loraWeight },
        latentSize,
        {
          transitionDurationHint: preset.transitionDurationHint,
          camera: preset.videoCamera,
          prevTailDesc: prevTailDescForUse,
          firstFrameDesc,
          prevTailImage: prevTailInput || undefined,
          firstFrameImage: frameFirstImage,
        },
      );
      try {
        const rt = await submitWorkflow(
          preset.videoTemplateId!, transitionValues, settings.comfyuiUrl,
          transitionVideoPrompt, loras.map(({ name, weight }) => ({ name, weight })),
          preset.loraMode || "single",
        );
        if (rt.prompt_id) {
          setMessages((current) => bindMediaSlotPrompt(
            current, messageId, transitionSlotId, rt.prompt_id!,
          ));
          const transitionOutputNodeIds = tpl.primary_output_node_id
            ? [tpl.primary_output_node_id] : [];
          pollResult(rt.prompt_id, transitionOutputNodeIds, undefined,
            { messageId, slotId: transitionSlotId, background: true as const },
            transitionVideoPrompt, "video");
        }
      } catch (error) {
        discardFailedIllustration(messageId, transitionSlotId, "transition",
          error instanceof Error ? error.message : "转场视频提交失败");
        console.warn("[auto-video] 转场视频提交失败，正片照常", error);
      }
    }
    // V1.6/P5+：首尾帧独立图片模式（无视频）不出高潮主图/正片——双帧图已回填，主槽语义已被占用。
    if (!(useFirstlastImages && !useVideo)) {
    const values = illustrationTemplateValues(tpl.exposed, {
      prompt, negativePrompt, loraName, loraWeight, baseImage: uploadedImage,
      latentSize,
      // V1.2 视频最小事实：时长/镜头 = 用户预设值（模板无 exposed binding 时自然忽略）；motion 已由后端透传
      videoDuration: useVideo && preset.videoDurationHint ? preset.videoDurationHint : undefined,
      videoCamera: useVideo ? preset.videoCamera : undefined,
      // V1.5/B1 视频模式 + 首尾帧描述：有值才传（模板无 exposed binding 时自然忽略）
      videoMode: useVideo ? videoMode : undefined,
      firstFrameDesc: useVideo && firstFrameDesc ? firstFrameDesc : undefined,
      lastFrameDesc: useVideo && lastFrameDesc ? lastFrameDesc : undefined,
      prevTailDesc: useVideo && prevTailDescForUse ? prevTailDescForUse : undefined,
      lastFrameUrl: useVideo && lastFrameUrl ? lastFrameUrl : undefined,
      // V1.5/B3 双帧图：V1.6/P5 首尾帧顺序链后，首帧=生成的图（兜底底图），尾帧=生成的图（兜底事件图）
      firstFrameImage: useVideo && frameFirstImage ? frameFirstImage : undefined,
      lastFrameImage: useVideo && frameLastImage ? frameLastImage : undefined,
      // V1.5 默认开放：后端编译的 climax 视频提示词（仅视频分支注入；无模板时仍留在槽位供测试核对）
      videoPrompt: useVideo && videoPrompt ? videoPrompt : undefined,
    });
    try {
      const st = await comfyStatus(settings.comfyuiUrl);
      if (!st.running) {
        failSlot("comfyui_status", "ComfyUI 尚未启动");
        return;
      }
      // V1.1 红线：视频模板 LoRA 不自动接线（loader 结构各异），只认模板自身暴露字段；
      // 图片分支维持既有 loraStack 注入合同。
      const loraStack = useVideo ? [] : loras.map(({ name, weight }) => ({ name, weight }));
      const loraMode = useVideo ? "none" : (preset.loraMode || "single");
      const r = await submitWorkflow(
        chosenId, values, settings.comfyuiUrl, prompt, loraStack, loraMode,
      );
      if (r.prompt_id) {
        void reportIllustrationSubmission({
          threadId, repoId: repo?.id || threadId, turnId, messageId, slotId,
          templateId: chosenId, promptId: r.prompt_id, prompt, promptProfile,
          loraName, loraWeight, latentWidth: latentSize.width, latentHeight: latentSize.height,
          loraMode, loraNames: loraStack.map((item) => item.name),
          valueKeys: Object.keys(values).sort(),
          source,
        }).catch(() => undefined);
        const outputNodeIds = tpl.primary_output_node_id ? [tpl.primary_output_node_id] : [];
        const target = { messageId, slotId, background: true as const };
        const regeneration = templateRegenerationSnapshot(
          chosenId, values, settings.comfyuiUrl, outputNodeIds, prompt, loraStack, loraMode,
          characterLoraActor,
        );
        setMessages((current) => bindMediaSlotPrompt(current, messageId, slotId, r.prompt_id!));
        const baseSlotRef = useVideo && videoBaseRef?.sourceMessageId && videoBaseRef?.sourceSlotId
          ? { messageId: videoBaseRef.sourceMessageId, slotId: videoBaseRef.sourceSlotId }
          : undefined;
        pollResult(r.prompt_id, outputNodeIds, regeneration, target, prompt, useVideo ? "video" : "image", baseSlotRef);
      } else {
        failSlot("submit", "ComfyUI 没有返回任务 ID");
      }
    } catch (error) {
      failSlot("submit", error instanceof Error ? error.message : "自动插画提交失败");
    }
    }
  };
  // 剧情对白音频化：后端发来台词分段（含 8 维情感向量），逐角色提交 IndexTTS 工作流。
  // 每段台词独立一次运转（模型一次只认一个音色），完成后按角色分条聚合到楼层气泡。
  const submitAudio = async (lines: AudioDialogueLine[], messageId: string) => {
    const preset = settings.mediaInsert?.[repo?.id || ""];
    const audioTemplateId = preset?.audioTemplateId;
    if (!audioTemplateId) {
      console.warn("[auto-audio] 跳过：未配置音频模板", { repoId: repo?.id, mediaInsert: settings.mediaInsert });
      return;
    }
    const tpl = templates.find((t) => t.id === audioTemplateId);
    if (!tpl) {
      console.warn("[auto-audio] 跳过：模板不存在", { audioTemplateId, templates: templates.map((t) => t.id) });
      return;
    }
    // 未配置参考音轨的角色台词会静默跳过——明确提示一次，避免用户以为配音漏了
    const skipped = skippedAudioSpeakers(preset, lines);
    if (skipped.length > 0) {
      const configured = Object.keys(preset?.characterVoices || {});
      console.warn("[auto-audio] 跳过未配置音轨的角色", {
        skipped, configuredVoices: configured, lines: lines.map((l) => `${l.speaker}:${l.text}`),
      });
      onNotify?.(
        `未配置音轨，已跳过配音：${skipped.join("、")}（已配置：${configured.join("、") || "无"}，在「媒体插入」设置里为角色配置音色）`,
        "info",
      );
    }
    const resolvable = resolvableAudioLines(preset, lines);
    if (!resolvable.length) {
      console.warn("[auto-audio] 无可配音台词行", {
        lines: lines.map((l) => ({ speaker: l.speaker, text: l.text })),
      });
      return;
    }
    const outputNodeIds = tpl.primary_output_node_id ? [tpl.primary_output_node_id] : [];

    let index = 0;
    for (const line of resolvable) {
      const seq = index + 1;
      const slotId = `audio-${messageId}-${index}`;
      index += 1;
      const voiceRef = voiceReferenceFor(preset, line.speaker)!;
      // 音轨先上传 ComfyUI input，取回 LoadAudio 引用的文件名
      let uploadedRef = "";
      try {
        const blob = await (await fetch(localViewUrl(voiceRef))).blob();
        const file = new File(
          [blob], voiceRef.split(/[\\/]/).pop() || "voice.wav",
          { type: blob.type || "audio/wav" },
        );
        uploadedRef = (await uploadImage(file, settings.comfyuiUrl)).name;
      } catch {
        console.error("[auto-audio]", { threadId, messageId, stage: "upload_voice", speaker: line.speaker, voiceRef });
        continue; // 单角色音轨上传失败不阻断其它角色
      }
      const values = audioTemplateValues(tpl.exposed, {
        text: line.text,
        reference: uploadedRef,
        emotion: line.emotion,
      });
      // 追加按角色分条的 audio 槽（生成中占位：角色名 + 第几条/总数），完成后由 pollResult 填充音频 URL。
      // appendAudioSlot 会把纯文本消息先转成 text part，避免正文被槽位顶掉（图片插槽同款处理）。
      setMessages((current) => appendAudioSlot(current, messageId, slotId, line.speaker, seq, resolvable.length));
      // 槽位补写后端快照：finalize 时 resolve_media_slot 需在快照里命中该 slot，
      // 否则 snapshotted=false → durableFinalizeSucceeded=false → 前端不填充 → 音频不在对话。
      if (threadId && threadId !== "home") {
        void ensureAudioSlot({
          threadId, messageId, slotId, speaker: line.speaker, seq, total: resolvable.length,
        }).catch(() => undefined);
      }
      try {
        const r = await submitWorkflow(audioTemplateId, values, settings.comfyuiUrl, line.text, [], "none");
        if (r.prompt_id) {
          console.info("[auto-audio]", {
            threadId, messageId, slotId, speaker: line.speaker,
            promptId: r.prompt_id, valueKeys: Object.keys(values).sort(),
          });
          // 持久化音频生成日志（对齐插画 submitted trace），便于排查音色/情感/台词
          void reportAudioSubmission({
            threadId, repoId: repo?.id || threadId, messageId, slotId,
            speaker: line.speaker, text: line.text, voiceRef,
            templateId: audioTemplateId, promptId: r.prompt_id,
            emotion: line.emotion, valueKeys: Object.keys(values).sort(),
          }).catch(() => undefined);
          pollResult(r.prompt_id, outputNodeIds, undefined, { messageId, slotId, background: true }, line.text, "audio");
        } else {
          console.error("[auto-audio]", { threadId, messageId, stage: "submit", speaker: line.speaker, error: "ComfyUI 没有返回任务 ID" });
          setMessages((current) => dropMediaSlot(current, messageId, slotId));
        }
      } catch (error) {
        console.error("[auto-audio]", { threadId, messageId, stage: "submit", speaker: line.speaker, error });
        setMessages((current) => dropMediaSlot(current, messageId, slotId));
      }
    }
  };

  // 音频分条按顺序拼接完整版：后端 ffmpeg concat + 落盘回写快照，
  // 成功后移除分条音频、只保留 merged（刷新后从快照恢复，仍只显示完整版）。
  const mergeAudioTracks = async (messageId: string) => {
    const msg = messagesRef.current.find((m) => m.id === messageId);
    const tracks = (msg?.parts || []).filter(
      (p) => p.type === "audio" && p.url && !(p.slotId || "").startsWith("merged-"),
    );
    if (tracks.length < 2) {
      console.warn("[merge-audio] 分条音频不足，跳过", { messageId, count: tracks.length });
      return;
    }
    // 已有旧完整版（如早前 -c copy 时长错误产物）时强制覆盖重拼
    const hasMerged = (msg?.parts || []).some(
      (p) => p.type === "audio" && (p.slotId || "").startsWith("merged-"),
    );
    try {
      const r = await mergeAudio({ threadId, messageId, force: hasMerged });
      if (r.ok && r.url) {
        setMessages((current) => current.map((m) => m.id === messageId ? {
          ...m,
          // 移除所有分条音频（type=audio 且非 merged-），只保留完整版一个
          parts: [
            ...(m.parts || []).filter(
              (p) => !(p.type === "audio" && !(p.slotId || "").startsWith("merged-")),
            ),
            {
              type: "audio" as const, url: r.url, slotId: `merged-${messageId}`,
              status: "ready" as const, kind: "audio" as const, speaker: "完整版",
            },
          ],
        } : m));
        onNotify?.("完整版音频已生成", "success");
      } else {
        console.error("[merge-audio] 后端返回异常", { threadId, messageId, r });
        onNotify?.("音频拼接失败", "error");
      }
    } catch (error) {
      console.error("[merge-audio]", { threadId, messageId, error });
      // 透出后端原因（如「拼接产物时长异常…已丢弃」）；分条未动 → 按钮仍在，可重试
      const reason = error instanceof Error && error.message ? error.message : "音频拼接失败";
      onNotify?.(reason, "error");
    }
  };
  // APPEND3_HERE

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
      if (r.prompt_id) pollResult(r.prompt_id, outputNodeIds, regeneration, undefined, wfPrompt);
    } catch (e) {
      pushBot(`启动失败：${(e as Error).message}`);
    } finally {
      setUploadingWf(false);  // 上传/提交结束（无论成败）；pollResult 已置 wfRunning
    }
  };

  // 进入仓库/切回时，恢复"进行中的生图任务"。
  const resumedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    let alive = true;
    const resume = async () => {
      for (let i = 0; i < 40 && !loadedRef.current; i++) await new Promise((r) => setTimeout(r, 100));
      if (!alive) return;
      const list = workflowRuntime.list();
      for (const p of list) {
        const action = workflowRuntime.recoveryAction(p, resumedRef.current);
        if (action === "skip") continue;  // 本会话已处理过，不重复
        resumedRef.current.add(p.prompt_id);
        const comfyuiUrl = comfyRegenerationUrl(p.regeneration) || settings.comfyuiUrl;
        if (action === "expire") {
          // 超过 30 分钟的遗留任务先查一次 ComfyUI history：任务其实已完成时
          // 直接归档回填（2026-08-30 21:25 轮实锤：图 3 分钟就出完了，页面回来
          // 时却按年龄判死丢弃）。只有 history 查不到（watching→not_found 终态）
          // 才真正交给失败槽，不再凭年龄静默丢弃已完成的图。
          const outcome = await workflowRuntime.inspect(p, comfyuiUrl, workflowObserver);
          if (!alive) return;
          if (outcome !== "watching") continue;
          if (p.target) discardFailedIllustration(
            p.target.messageId, p.target.slotId, "resume_expired",
            "后台出图任务已过期", p.prompt_id,
          );
          workflowRuntime.cancel(p.prompt_id);
          continue;
        }
        await workflowRuntime.inspect(p, comfyuiUrl, workflowObserver);
        if (!alive) return;
      }
    };
    resume();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);
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
  const applyStreamEvent = (botId: string, event: ChatStreamEvent) => {
    const next = reduceChatStreamEvent(messagesRef.current, botId, event);
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
      applyStreamEvent(botId, { ...event, id: slotId });
      const illustrationRetrySnapshot = [
        event.prompt, event.motion, event.actors, botId, slotId, event.sceneSpec, event.turnId,
        "automatic", event.videoMode, event.firstFrameDesc, event.lastFrameDesc,
        event.prevTailDesc, event.lastFrameUrl, event.videoPrompt, event.transition,
        event.transitionVideoPrompt, event.transitionVideoParams,
      ];
      void submitIllustration(
        event.prompt, event.motion, event.actors, botId, slotId, event.sceneSpec, event.turnId,
        "automatic", event.videoMode, event.firstFrameDesc, event.lastFrameDesc,
        event.prevTailDesc, event.lastFrameUrl, event.videoPrompt, event.transition,
        event.transitionVideoPrompt, event.transitionVideoParams,
      ).catch((error) => {
        // 提交链上任何未被 failSlot 覆盖的异常都落到失败槽（可重新生成），
        // 不再让 pending 槽静默（2026-08-31 晚实锤：槽 pending 却无图、无报错）。
        discardFailedIllustration(
          botId, slotId, "submission",
          error instanceof Error ? error.message : "自动插画提交异常",
          "", illustrationRetrySnapshot,
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
  const runFreeText = (
    t: string, content?: RichContent, skipUserMsg = false, userMsgId?: string,
    historyMessages: readonly ChatMessage[] = messagesRef.current,
  ) => {
    const visibleHistory = promptHistory(historyMessages);
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
      { id: botId, role: "assistant", text: "" },
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
    const discarded = messagesRef.current.length - replay.retained.length;
    if (discarded > 1 && !await askConfirm(`重新生成将删除此消息后的 ${discarded} 条消息，是否继续？`)) {
      return;
    }
    historyRuntime.replace(replay.retained);
    atBottomRef.current = true;
    runFreeText(
      replay.content.text.trim(), replay.content, true, messageId, replay.history,
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
      comfyuiUrl: settings.comfyuiUrl, imageStyle: "", styleTemplate: activeStyleTemplate(settings),
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
      const card = await fetchInspiration(query, chat, settings.proxyEnabled ? settings.proxyUrl : "");
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

  // 真正停止后台生成
  const hardCancel = async (promptId: string | null): Promise<void> => {
    try { await cancelAgent(threadId); } catch { /* 后端未起忽略 */ }
    if (promptId) {
      try { await interruptComfy(settings.comfyuiUrl, promptId); } catch { /* 忽略 */ }
    }
    abortRef.current = releaseAgentStream(abortRef.current, "stop");
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
    agentBusyRef.current = false;
    stopProgress();
    setSlowWatchPromptId(null);  // 清慢守望状态，停止键消失
    if (pid) workflowRuntime.cancel(pid);
    refreshChatBackgroundActivities();
    await hardCancel(pid);
    if (sid) {
      setMessages((ms) =>
        ms.map((m) => (m.id === sid && !m.text && !m.image ? { ...m, text: "（已停止生成）" } : m)),
      );
    }
  };

  // 停止单个媒体槽的生成（图片生成位置下方的「停止」键）：中断 ComfyUI + 取消守望 + 槽位标失败。
  const stopSlotGeneration = async (messageId: string, slotId: string) => {
    const msg = messagesRef.current.find((m) => m.id === messageId);
    const part = msg?.parts?.find((p) => p.slotId === slotId);
    const promptId = part?.promptId || "";
    if (promptId) {
      workflowRuntime.cancel(promptId);
      try { await interruptComfy(settings.comfyuiUrl, promptId); } catch { /* 中断失败忽略，槽位照常标停 */ }
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
      setMessages((ms) =>
        ms.map((m) => (m.id === sid ? { ...m, text: (m.text || "") + "（已打断）" } : m)),
      );
    }
    dispatchSend(item.content);  // 同 thread 新一轮：AI 带上下文续写 = 合并
  };

  const regenerateResult = async (messageId: string, slotId?: string) => {
    const message = messages.find((item) => item.id === messageId);
    const part = slotId
      ? message?.parts?.find((item) => item.slotId === slotId)
      : undefined;
    const snapshot = slotId
      ? part?.regeneration
      : message?.regeneration;
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
        const prompt = legacyGenerationPrompt(part?.url || "", generations.items || []);
        if (!prompt) throw new Error("资产库中未找到这张旧图的原提示词");
        await submitIllustration(prompt, 0, [], messageId, slotId!, undefined, "", "manual");
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
          proxyUrl: resolveModelProxy(model.proxyMode, settings.proxyUrl, settings.proxyEnabled),
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
      pollResult(submitted.prompt_id, snapshot.outputNodeIds, snapshot, target, snapshot.prompt);
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
    homeDraft = [];
    setMessages([]);
  };

  // ===== ④ AI 消息就地编辑 =====
  // 改 text；若有 parts 则同步重建文本块（保留图片块）。
  const editMessage = (id: string, text: string) => {
    historyRuntime.replace(messagesRef.current.map((m) => {
      if (m.id !== id) return m;
      if (m.parts && m.parts.length > 0) {
        const nonText = m.parts.filter((p) => p.type !== "text");
        const parts = text ? [...nonText, { type: "text" as const, text }] : nonText;
        return { ...m, text, parts };
      }
      return { ...m, text };
    }));
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
      | { retryArgs?: unknown[] }
      | undefined;
    const snapshot = part?.retryArgs;
    if (!snapshot?.length) return;
    const args = [...snapshot];
    args[7] = "manual";
    // 重试即时反馈：槽位先回 pending（重试要走帧编译+提交，秒级无反馈会被当成「点了没反应」）；
    // 再次失败时 failSlot 会带新错误与原快照，重新变回 failed+按钮。
    setMessages((current) => resetMediaSlotForRetry(current, messageId, slotId));
    void submitIllustration(...(args as Parameters<typeof submitIllustration>));
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
  };
}
