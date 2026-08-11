// 聊天会话引擎：从 ChatView 抽出的「持久化 + 生成编排」深簇。
// 拥有 messages / 生成生命周期 reducer / 各类 ref 与加载·落盘 effect，
// 对外只暴露渲染所需的状态与动作句柄；UI 局部态（模型选择/面板开关/尺寸）留在 ChatView。
// 接口即测试面：整台生成引擎集中一处，不必渲染千行组件即可推演其行为。
import { type MutableRefObject, useEffect, useReducer, useRef, useState } from "react";
import type { AgentRoute, ChatMessage, MsgPart, PromptApproval, RegenerationSnapshot, RouteChoice } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings } from "../stores/settings";
import { activeStyleTemplate, activeUserPersona } from "../stores/settings";
import type { RichContent } from "../components/RichInput";
import { emitRagStatus } from "../components/RagToast";
import type { Template } from "../api/workflows";
import {
  comfyStatus, startComfy, submitGraph, submitWorkflow, interruptComfy,
  saveLocalSrc, localViewUrl, uploadImage, finalizeGeneration as persistWorkflowGeneration,
  type GenResult,
} from "../api/comfyui";
import { listLoras } from "../api/loras";
import {
  fetchHistory, multiAgent,
  saveSnapshot, fetchSnapshot, fetchAgentRunning, cancelAgent,
  fetchInspiration, regenerateImage as replayImageGeneration,
  claimIllustrationSubmission, reportIllustrationFailure, reportIllustrationSubmission,
  genProfilePrompt, listGenerations,
  type AgentInvocation,
} from "../api/ai";
import { createScenarioSnapshot } from "../api/scenario";
import { refreshChatBackgroundActivities } from "./chatBackgroundActivity";
import { substituteMacros } from "./chatMacros";
import type { ChatStreamEvent, IllustrationSceneSpec } from "../api/chatStreamProtocol";
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
} from "./chatGeneration";
import {
  durableFinalizeSucceeded, WorkflowGenerationRuntime,
  type PendingGeneration, type WorkflowWatchObserver,
} from "./workflowGenerationRuntime";
import {
  applyProfileLoraTriggers, ensureAnimaIllustrationStyle, illustrationTemplateValues, normalizePromptProfile,
  replacePromptQualityLine, latentSizeFor, workflowFieldBinding,
} from "./imagePromptProfiles";
import {
  illustrationLoraConfigurationError, illustrationRequestMedia, illustrationWorkflowMedia,
  resolveIllustrationActors,
} from "./illustrationMedia";
import {
  agentImageMessage, applyRouteChoice, bindMediaSlotPrompt, dropMediaSlot,
  pruneUnsubmittedMediaSlots, reduceChatStreamEvent, resolveMediaSlot,
  restoreSubmittedMediaSlots, upsertMessages, workflowMessages,
} from "./chatSessionEvents";
import { recoverCompactedSummaryImage } from "./contextManagement";
import { characterDetail } from "../api/characters";
import { recoverAgentRun, shouldRecoverAgentRun } from "./agentRecovery";
import { releaseAgentStream, type ActiveAgentStream } from "./agentStreamLifecycle";
import type { ImageQuality, WorkMode } from "./viewRouting";
import { useChatMaintenance } from "./useChatMaintenance";
import {
  comfyRegenerationUrl, legacyGenerationPrompt, resolveImageRegenerationModel,
  templateRegenerationSnapshot, workflowRegenerationSnapshot,
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
}

// PLACEHOLDER_BODY

export function useChatSession(deps: ChatSessionDeps) {
  const {
    repo, settings, setGeneratedCover, chat, genModel, videoModel, workMode, size, imageQuality,
    templates, setShowPicker, atBottomRef,
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
  const [wfProgress, setWfProgress] = useState<number | null>(null);  // 工作流实时进度%（WS，null=无）
  const [wfNode, setWfNode] = useState<string>("");  // 当前执行的节点显示名（WS executing 消息）
  const [regeneratingIds, setRegeneratingIds] = useState<Set<string>>(new Set());
  const wsUnsubRef = useRef<(() => void) | null>(null);  // 当前进度 WS 退订
  const abortRef = useRef<ActiveAgentStream | null>(null);  // 仅显式停止才中断；导航离开保持后台运行
  const bgRunningRef = useRef(false);  // 后台任务进行中：此时后端拥有快照写权，前端不抢写以免覆盖
  // 慢守望阶段（releaseBusy 后仍在轮询）：wfRunning 已 false，但仍需显示停止键
  const [slowWatchPromptId, setSlowWatchPromptId] = useState<string | null>(null);
  // 对话线 id = 仓库 id（首页用 "home"）：后端按此落盘多轮记忆与 RAG 知识库
  const threadId = repo?.id || "home";
  const {
    queued, enqueue: enqueueQueued, remove: removeQueued,
  } = useChatAgentQueue(threadId);
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
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: "assistant", text }]);
  // 通用：追加一条任意消息（多 Agent 模式用，可带 user 角色 / 图片）
  const pushMsg = (msg: Partial<ChatMessage>) =>
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: "assistant", text: "", ...msg } as ChatMessage]);

  const startAgentRecovery = (targetThread = threadId, targetRepoId = repo?.id) => {
    if (!shouldRecoverAgentRun(targetThread)) return;
    if (activeThreadRef.current !== targetThread || recoveryActiveRef.current) return;
    const token = ++recoveryTokenRef.current;
    recoveryActiveRef.current = true;
    bgRunningRef.current = true;
    const knownMedia = new Set(messages.flatMap((message) => [message.image, message.video].filter(Boolean)));
    let recoveredMedia = "";

    void recoverAgentRun({
      fetchSnapshot: () => fetchSnapshot(targetThread) as Promise<{ items: ChatMessage[] }>,
      fetchRunning: () => fetchAgentRunning(targetThread),
      isActive: () => activeThreadRef.current === targetThread && recoveryTokenRef.current === token,
      onSnapshot: (items) => {
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
            snap.items as ChatMessage[], workflowRuntime.list(),
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
      // 流式正文或重生成尚未终态时不能创建世界状态快照，否则会把半截正文与未完成写回固化。
      const full = await slimSnapshot(original);
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
        setMessages(full);
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
    if ((r.images?.length || 0) === 0 && (r.videos?.length || 0) === 0 && !best) return false;
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
        outputDir: owner.outputDir,
        comfyuiUrl,
        embed: embedModel,
        chat,
        regeneration,
        target,
      });
      if (!durableFinalizeSucceeded(result)) {
        throw new Error("生成已完成，但原图或会话槽尚未持久化，将自动重试归档");
      }
      if (target && result.target?.url) {
        setMessages((current) => resolveMediaSlot(
          current, target.messageId, target.slotId, result.target!.url,
          result.target!.media_type, regeneration,
        ));
        if (result.target.media_type === "image" && owner.repoId !== "home") {
          setGeneratedCover(owner.repoId, result.target.url);
        }
        if (result.durable && result.images.some((image) => image.indexed)) {
          window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: owner.threadId }));
        }
        return true;
      }
      const blocks = workflowMessages(result.messages);
      setMessages((current) => upsertMessages(current, blocks));
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
    setWfProgress(null);
    setWfNode("");
  };

  const discardFailedIllustration = (
    messageId: string, slotId: string, stage: string, error: string, promptId = "",
  ) => {
    console.error("[auto-illustration]", {
      threadId: repo?.id || "home", messageId, slotId, stage, error, promptId,
    });
    setMessages((current) => dropMediaSlot(current, messageId, slotId));
    void reportIllustrationFailure({
      threadId: repo?.id || "home",
      repoId: repo?.id || "home",
      messageId, slotId, stage, error, promptId,
    }).catch((reportError) => {
      console.error("[auto-illustration] failure log upload failed", reportError);
    });
  };

  const workflowObserver: WorkflowWatchObserver = {
    finalize: finalizeGeneration,
    completed: (result, pending, produced) => {
      if (!produced && (result.images?.length || 0) === 0
          && (result.videos?.length || 0) === 0 && !pickBestText(result.texts)) {
        if (pending.target) discardFailedIllustration(
          pending.target.messageId, pending.target.slotId, "completed_without_media",
          "生成完成但没有媒体输出", pending.prompt_id,
        );
        else pushBot("生成完成，但没有输出（工作流未含 SaveImage / 视频合成 / 文字输出节点）。");
      }
      if (!pending.target?.background) {
        stopProgress();
        setSlowWatchPromptId(null);
        dispatch({ t: "workflowDone", promptId: pending.prompt_id });
      }
    },
    failed: (pending, stage, error) => {
      if (pending.target) discardFailedIllustration(
        pending.target.messageId, pending.target.slotId, stage, error, pending.prompt_id,
      );
      if (!pending.target?.background) {
        stopProgress();
        setSlowWatchPromptId(null);
        dispatch({ t: "workflowDone", promptId: pending.prompt_id });
        pushBot(stage === "task_not_found"
          ? "⚠️ 出图任务已丢失（ComfyUI 可能已重启或队列被清空）。如需重新生图，请点工作流卡片的「运转工作流」。"
          : `生成失败：${error}`);
      }
      refreshChatBackgroundActivities();
    },
    released: (pending) => {
      if (pending.target?.background) return;
      stopProgress();
      dispatch({ t: "workflowDone", promptId: pending.prompt_id });
      setSlowWatchPromptId(pending.prompt_id);
      pushBot("生成较复杂、仍在后台进行，出图后会自动载入（也可在 ComfyUI 面板看进度）。");
    },
    timedOut: (pending) => {
      if (pending.target) discardFailedIllustration(
        pending.target.messageId, pending.target.slotId, "poll_timeout",
        "后台出图等待超时，可刷新后继续恢复", pending.prompt_id,
      );
      else setSlowWatchPromptId(null);
    },
  };

  const pollResult = (
    promptId: string,
    outputNodeIds: string[] = [],
    regeneration?: RegenerationSnapshot,
    target?: PendingGeneration["target"],
    prompt = "",
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
    if (!target?.background) {
      stopProgress();
      setWfProgress(0);
      setWfNode("");
      wsUnsubRef.current = subscribeProgress(comfyuiUrl, promptId, {
        onProgress: (pct) => setWfProgress(pct),
        onNode: (id) => setWfNode(nodeLabel(id)),
      });
    }
    workflowRuntime.start({
      promptId, comfyuiUrl, outputNodeIds, regeneration, target, prompt,
      owner: { threadId, repoId: repo?.id || "home", outputDir: settings.outputDir },
    }, workflowObserver);
  };

  // 模板是否定义了图像输入口 / 图值是否已填 → 见 lib/chatGeneration（纯逻辑，已抽出可测）

  // 剧情高潮点异步出图/出视频：后端发来 booru 提示词 + motion 动态强度，按本作品预设组 values 提交 ComfyUI，
  // 走 pollResult 现成异步闭环（徽记显示进度 + 离开继续 + 点击返回）。未预设模板 → 静默跳过。
  // 智能模态：smartVideo 开 且 motion>=2 且 预设了视频模板 → 用视频模板，否则用图片模板。
  const submitIllustration = async (
    prompt: string, motion = 0, actors: string[] = [], messageId: string, slotId: string,
    sceneSpec?: IllustrationSceneSpec, turnId = "", source: "automatic" | "manual" = "automatic",
  ) => {
    const failSlot = (stage: string, error: string) =>
      discardFailedIllustration(messageId, slotId, stage, error);
    const preset = settings.mediaInsert?.[repo?.id || ""];
    if (!preset) { failSlot("configuration", "当前作品没有配置自动插画模板"); return; }
    if (source === "automatic") {
      try {
        const claim = await claimIllustrationSubmission({ threadId, messageId, slotId });
        if (!claim.claimed) return;
      } catch (error) {
        failSlot("submission_claim", error instanceof Error ? error.message : "自动插画提交认领失败");
        return;
      }
    }
    if (!prompt.trim() && !sceneSpec?.narrative.trim()) {
      failSlot("prompt", "剧情出图提示词为空");
      return;
    }
    const useVideo = !!(preset.smartVideo && preset.videoTemplateId && motion >= 2);
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
    const loraConfigurationError = illustrationLoraConfigurationError(preset, workflowMedia);
    if (loraConfigurationError) {
      failSlot("configuration", loraConfigurationError);
      return;
    }
    let negativePrompt = preset.negativePrompt?.trim() || sceneSpec?.negative_prompt || "";
    if (sceneSpec?.profile_prompt && sceneSpec.profile === promptProfile) {
      prompt = sceneSpec.profile_prompt;
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
    // 触发词自动前置：生效 LoRA 的触发词必须逐字精确（大脑会漏写/改写 → LoRA 不生效却看不出），
    // 故按文件名查触发词表机械前置到正向提示词。对齐 /a 编排的 lora_inject 思路。
    // 已在提示词里出现的词不重复注入（大小写不敏感）。查表失败/无触发词不阻断出图。
    if (loras.length) {
      try {
        const items = (await listLoras()).items || [];
        for (const lora of loras) {
          const metadata = resolveLoraPromptMetadata(items, lora.name);
          if (!metadata.found) {
            throw new Error(`实际加载的 LoRA 没有精确元数据记录：${lora.name}`);
          }
          prompt = applyProfileLoraTriggers(prompt, promptProfile, metadata.additions);
        }
      } catch (error) {
        failSlot(
          "lora_metadata",
          error instanceof Error ? error.message : "LoRA触发词与建议提示词读取失败",
        );
        return;
      }
    }
    // 底图需先上传到 ComfyUI input 目录，取回可供 LoadImage 引用的文件名
    let uploadedImage = "";
    const needsImage = tpl.exposed.some((f) => workflowFieldBinding(f) === "base_image");
    if (needsImage && baseImage) {
      try {
        const blob = await (await fetch(localViewUrl(baseImage))).blob();
        const file = new File([blob], baseImage.split(/[\\/]/).pop() || "base.png", { type: blob.type || "image/png" });
        uploadedImage = (await uploadImage(file, settings.comfyuiUrl)).name;
      } catch { /* 底图上传失败 → 退化为纯文生图，不阻断 */ }
    }
    // 按 exposed 的隐藏 binding 组 values；提交 key 始终是“节点id.原字段名”。
    const latentSize = latentSizeFor(
      sceneSpec?.aspect_ratio || "2:3",
      preset.latentLongEdge === 2048 || preset.latentLongEdge === 4096
        ? preset.latentLongEdge : 1024,
    );
    const values = illustrationTemplateValues(tpl.exposed, {
      prompt, negativePrompt, loraName, loraWeight, baseImage: uploadedImage,
      latentSize,
    });
    try {
      const st = await comfyStatus(settings.comfyuiUrl);
      if (!st.running) {
        failSlot("comfyui_status", "ComfyUI 尚未启动");
        return;
      }
      const loraStack = loras.map(({ name, weight }) => ({ name, weight }));
      const loraMode = preset.loraMode || "single";
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
        );
        setMessages((current) => bindMediaSlotPrompt(current, messageId, slotId, r.prompt_id!));
        pollResult(r.prompt_id, outputNodeIds, regeneration, target, prompt);
      } else {
        failSlot("submit", "ComfyUI 没有返回任务 ID");
      }
    } catch (error) {
      failSlot("submit", error instanceof Error ? error.message : "自动插画提交失败");
    }
  };
  // APPEND3_HERE

  // /s 启动：取最近一张已确认的工作流卡，用抓取到的画布工作流提交生成
  const runWorkflow = async (cardId?: string) => {
    if (wfRunning || !!streamingId) return;  // 防重复提交：已有任务在跑时忽略
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
      const r = await submitGraph(wf.capturedGraph, settings.comfyuiUrl);
      pushBot(`已提交到 ComfyUI 生成（prompt_id: ${r.prompt_id}，${r.node_count} 个节点），正在运转工作流…`);
      const outputNodeIds = tpl?.primary_output_node_id ? [tpl.primary_output_node_id] : [];
      const regeneration = workflowRegenerationSnapshot(
        wf.capturedGraph, settings.comfyuiUrl, outputNodeIds);
      if (r.prompt_id) pollResult(r.prompt_id, outputNodeIds, regeneration);
    } catch (e) {
      pushBot(`启动失败：${(e as Error).message}`);
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
        if (action === "expire") {
          if (p.target) discardFailedIllustration(
            p.target.messageId, p.target.slotId, "resume_expired",
            "后台出图任务已过期", p.prompt_id,
          );
          workflowRuntime.cancel(p.prompt_id);
          continue;
        }
        const comfyuiUrl = comfyRegenerationUrl(p.regeneration) || settings.comfyuiUrl;
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
    if (!text && content.images.length === 0 && !content.maskedImage) return;
    atBottomRef.current = true;  // 用户主动发送时强制跟随到底
    if (blocksDialogueSubmission(gen)) { enqueue(content); return; }
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
    if (!raw && content.images.length === 0 && !content.maskedImage) return;
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
      setMessages((current) => reduceChatStreamEvent(
        current, botId, { ...event, id: slotId },
      ));
      void submitIllustration(
        event.prompt, event.motion, event.actors, botId, slotId, event.sceneSpec, event.turnId,
      );
      return;
    }
    // RAG 记忆库创建状态：右下角轻提示（创建中/成功/失败），不入气泡流
    if (event.type === "rag_status") {
      emitRagStatus({ state: event.state, kind: event.kind, count: event.count });
      return;
    }
    setMessages((current) => reduceChatStreamEvent(current, botId, event));
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
    const userMsg: ChatMessage = {
      id: userMsgId || crypto.randomUUID(),
      role: "user",
      text: t,
      parts: content?.parts || (images.length > 0 ? [
        ...(t ? [{ type: "text" as const, text: t }] : []),
        ...images.map((url) => ({ type: "image" as const, url })),
      ] : undefined),
    };
    const botId = crypto.randomUUID();
    setMessages((m) => [
      ...m,
      ...(skipUserMsg ? [] : [userMsg]),
      { id: botId, role: "assistant", text: "" },
    ]);
    dispatch({ t: "agentStart", botId });  // 进入 agent 态（未出图）
    const onDone = (err?: string) => {
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
    setMessages((current) => [...current, { id: botId, role: "assistant", text: "" }]);
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
            inspiration: { query: card.query, prompt: card.prompt, tags: card.tags || [], sources: card.sources || [] } }
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
        setMessages((current) => upsertMessages(current, [
          agentImageMessage(rec.url, rec.id, rec.regeneration || snapshot),
        ]));
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
  const deleteMessage = (id: string) => { historyRuntime.deleteMessage(id); };

  // 会话导入后重载：从后端快照重新拉取消息流覆盖当前视图（导入端点已落盘为真源）。
  const reloadFromSnapshot = async () => {
    try {
      const snap = await fetchSnapshot(threadId);
      historyRuntime.replace((snap.items || []) as ChatMessage[], false);
    } catch { /* 拉取失败保持当前视图，用户可刷新 */ }
  };

  return {
    messages, streamingId, wfRunning, slowWatchPromptId, wfProgress, wfNode, queued, regeneratingIds,
    send, runCommand, pushBot, pushMsg,
    actOnPromptApproval, actOnRouteChoice, regenerateResult,
    pickTemplate, runWorkflow, updateCardDraft, markCardDone, markCardReopen,
    applyWorkflowOps, ignoreWorkflowOps, editWorkflowOp,
    stopGenerating, guideQueued, cancelQueued,
    confirmReq, compact, compacting,
    contextReminder, dismissContextReminder,
    clearHome, clearCache: clearCacheAction, reloadFromSnapshot,
    editMessage, deleteMessage, regenerateMessage,
    checkpoints, createCheckpoint, restoreCheckpoint, deleteCheckpoint, messagesUpTo,
  };
}
