// 聊天会话引擎：从 ChatView 抽出的「持久化 + 生成编排」深簇。
// 拥有 messages / 生成生命周期 reducer / 各类 ref 与加载·落盘 effect，
// 对外只暴露渲染所需的状态与动作句柄；UI 局部态（模型选择/面板开关/尺寸）留在 ChatView。
// 接口即测试面：整台生成引擎集中一处，不必渲染千行组件即可推演其行为。
import { type MutableRefObject, useEffect, useReducer, useRef, useState } from "react";
import type { AgentRoute, ChatMessage, MsgPart, PromptApproval, RegenerationSnapshot, RouteChoice } from "../types/chat";
import type { Repo } from "../stores/repos";
import type { useSettings } from "../stores/settings";
import { activeStyleTemplate } from "../stores/settings";
import type { RichContent } from "../components/RichInput";
import type { Template } from "../api/workflows";
import {
  comfyStatus, startComfy, submitGraph, getResult, interruptComfy,
  saveLocalSrc, localViewUrl, finalizeGeneration as persistWorkflowGeneration,
  type GenResult,
} from "../api/comfyui";
import {
  fetchHistory, multiAgent,
  saveSnapshot, fetchSnapshot, fetchAgentRunning, cancelAgent,
  fetchInspiration, regenerateImage as replayImageGeneration,
  enqueueChatQueueTask, listChatQueueTasks, cancelChatQueueTask,
} from "../api/ai";
import { refreshChatBackgroundActivities } from "./chatBackgroundActivity";
import type { ChatStreamEvent } from "../api/chatStreamProtocol";
import {
  reduce as reduceGen, initialGenState,
  streamingBotId, needsConfirm, runningPromptId,
  type QueueItem,
} from "./generationLifecycle";
import { useWorkflowOrchestration } from "./workflowOrchestration";
import { subscribeProgress } from "./comfyProgress";
import {
  needsImageInput, hasImageProvided, pickBestText, shouldFinalize,
  registerPending, unregisterPending, pendingResumeAction, pollSchedule,
  slimSnapshot as slimSnapshotPure,
} from "./chatGeneration";
import {
  agentImageMessage, applyRouteChoice, reduceChatStreamEvent, upsertMessages, workflowMessages,
} from "./chatSessionEvents";
import { recoverCompactedSummaryImage } from "./contextManagement";
import { recoverAgentRun, shouldRecoverAgentRun } from "./agentRecovery";
import type { ImageQuality } from "./viewRouting";
import { useChatMaintenance } from "./useChatMaintenance";
import { resolveImageRegenerationModel, workflowRegenerationSnapshot } from "./regeneration";

type Model = { baseUrl: string; apiKey: string; modelName: string };

// 首页(home)=临时草稿区：草稿存模块级内存变量，随浏览器进程存活——
// 页面刷新(进程重开)即重置为空，但应用运行期间切走首页再回来仍保留。不落 localStorage / 后端快照。
let homeDraft: ChatMessage[] = [];

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
  size: string;                                  // 生图尺寸 "宽x高"
  imageQuality: ImageQuality;                    // GPT Image 质量档；不支持的模型由后端省略
  templates: Template[];
  setShowPicker: (v: boolean) => void;           // 与 /w 选择浮层共享
  atBottomRef: MutableRefObject<boolean>;        // 与滚动跟随 UI 共享
}

// PLACEHOLDER_BODY

export function useChatSession(deps: ChatSessionDeps) {
  const {
    repo, settings, setGeneratedCover, chat, genModel, videoModel, size, imageQuality,
    templates, setShowPicker, atBottomRef,
  } = deps;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
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
  const wfRunning = gen.status.kind === "workflow";    // /s 工作流进行中
  // 排队列表：后端持久化队列（离开页面/刷新后仍在），按本仓库过滤。
  // 内容(RichContent)后端只存 multiAgent payload；UI 的编辑回填/引导另存本地映射，缺失则用文本兜底。
  const [queued, setQueued] = useState<QueueItem[]>([]);
  const [wfProgress, setWfProgress] = useState<number | null>(null);  // 工作流实时进度%（WS，null=无）
  const [wfNode, setWfNode] = useState<string>("");  // 当前执行的节点显示名（WS executing 消息）
  const [regeneratingIds, setRegeneratingIds] = useState<Set<string>>(new Set());
  const wsUnsubRef = useRef<(() => void) | null>(null);  // 当前进度 WS 退订
  const abortRef = useRef<{ botId: string; abort: () => void } | null>(null);  // 中断当前流式生成及其所有者
  const bgRunningRef = useRef(false);  // 后台任务进行中：此时后端拥有快照写权，前端不抢写以免覆盖
  // 慢守望阶段（releaseBusy 后仍在轮询）：wfRunning 已 false，但仍需显示停止键
  const [slowWatchPromptId, setSlowWatchPromptId] = useState<string | null>(null);
  // 对话线 id = 仓库 id（首页用 "home"）：后端按此落盘多轮记忆与 RAG 知识库
  const threadId = repo?.id || "home";
  const activeThreadRef = useRef(threadId);
  activeThreadRef.current = threadId;
  const recoveryTokenRef = useRef(0);
  const recoveryActiveRef = useRef(false);
  const chatKey = `laf_chat_${threadId}`;
  const loadedRef = useRef(false);  // 标记本仓库消息已加载，避免初始空数组覆盖已存记录
  const snapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);  // 后端快照防抖
  // 队列项内容本地映射：后端队列只存 multiAgent payload，UI 的编辑回填/引导需要原始 RichContent。
  const queueContentKey = `laf_chat_queue_content_${threadId}`;
  const readQueueContent = (): Record<string, RichContent> => {
    try { return JSON.parse(localStorage.getItem(queueContentKey) || "{}"); } catch { return {}; }
  };
  const saveQueueContent = (taskId: string, content: RichContent) => {
    try {
      const map = readQueueContent();
      map[taskId] = content;
      localStorage.setItem(queueContentKey, JSON.stringify(map));
    } catch { /* 超额忽略，UI 用文本兜底 */ }
  };
  const dropQueueContent = (taskId: string) => {
    try {
      const map = readQueueContent();
      delete map[taskId];
      localStorage.setItem(queueContentKey, JSON.stringify(map));
    } catch { /* ignore */ }
  };
  // 从后端拉取本仓库排队消息，投影为队列条（内容优先取本地映射，缺失用文本兜底）。
  const refreshQueue = async () => {
    if (threadId === "home") { setQueued([]); return; }
    try {
      const { tasks } = await listChatQueueTasks(threadId);
      const contentMap = readQueueContent();
      const items: QueueItem[] = tasks
        .filter((task) => task.status === "queued" || task.status === "running")
        .map((task) => ({
          id: task.id,
          text: task.status === "running" ? `发送中…${task.need ? "：" + task.need : ""}` : task.need,
          content: contentMap[task.id] || { parts: [], text: task.need, images: [] },
        }));
      setQueued(items);
    } catch { /* 后端未起：保持已有 */ }
  };

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
    embed: settings.embedModel,
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
          const snapshotMessages = snap.items as ChatMessage[];
          let restoredMessages = snapshotMessages;
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
      } catch { /* 快照接口失败，继续兜底 */ }
      if (shownLocal) { loadedRef.current = true; await maybeStartBgPoll(); return; }  // 本地已渲染、后端无快照
      try {
        const r = await fetchHistory(threadId);
        if (!alive) return;
        setMessages(
          (r.items || []).map((m) => {
            const imgs = m.images || [];
            const parts: MsgPart[] = [];
            if (m.content) parts.push({ type: "text", text: m.content });
            for (const u of imgs) parts.push({ type: "image", url: u });
            return {
              id: crypto.randomUUID(),
              role: m.role === "assistant" ? "assistant" : "user",
              text: m.content,
              parts: parts.length > 0 ? parts : undefined,
            };
          }),
        );
      } catch { /* 后端未起/无历史，保持空 */ }
      finally { if (alive) loadedRef.current = true; }
      await maybeStartBgPoll();
    })();
    return () => {
      alive = false;
      activeThreadRef.current = "";
      recoveryTokenRef.current += 1;
      recoveryActiveRef.current = false;
      bgRunningRef.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
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
      if (bgRunningRef.current) return;  // 后台任务在写快照，前端不抢写以免覆盖后端落盘
      const full = await slimSnapshot(original);
      // localStorage 只存轻量快取：去掉 capturedGraph，parts / portsPlan 里的 dataURI 已被 slimSnapshot 转成本地 URL。
      const slim = full.map((m) =>
        m.workflow ? { ...m, workflow: { ...m.workflow, capturedGraph: null } } : m,
      );
      try {
        localStorage.setItem(chatKey, JSON.stringify(slim));
      } catch { /* 超额等忽略 */ }
      saveSnapshot(tid, full).catch(() => {});  // 后端未起则忽略，本地仍在
      if (tid === threadId && JSON.stringify(full) !== JSON.stringify(original)) {
        setMessages(full);
      }
    }, 600);
  }, [messages, chatKey, threadId]);

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

  // ===== 进行中生图任务持久化（切仓库/刷新后可恢复）=====
  const pendingKey = `laf_pending_gen_${threadId}`;
  const getPending = (): { prompt_id: string; createdAt: number; outputNodeIds?: string[]; regeneration?: RegenerationSnapshot }[] => {
    try { return JSON.parse(localStorage.getItem(pendingKey) || "[]"); } catch { return []; }
  };
  const addPending = (
    promptId: string,
    outputNodeIds: string[] = [],
    regeneration?: RegenerationSnapshot,
  ) => {
    const list = registerPending(
      getPending(), promptId, Date.now(), outputNodeIds, regeneration);
    try { localStorage.setItem(pendingKey, JSON.stringify(list)); } catch { /* ignore */ }
  };
  const removePending = (promptId: string) => {
    try {
      localStorage.setItem(pendingKey, JSON.stringify(unregisterPending(getPending(), promptId)));
    } catch { /* ignore */ }
  };

  // 已 finalize 的 promptId，防同一次生成被 pollResult 与切回 resume 重复落盘=重复出图
  const finalizedRef = useRef<Set<string>>(new Set());

  // 把一次已完成的生成结果交给后端统一留存，再投影为消息。返回是否产出了内容。
  const finalizeGeneration = async (r: GenResult, promptId?: string): Promise<boolean> => {
    const pending = getPending();
    if (!shouldFinalize(promptId, pending, finalizedRef.current)) return false;
    const best = pickBestText(r.texts);
    if ((r.images?.length || 0) === 0 && (r.videos?.length || 0) === 0 && !best) return false;
    if (!promptId) return false;
    finalizedRef.current.add(promptId);
    try {
      const savedRegeneration = pending.find((item) => item.prompt_id === promptId)?.regeneration;
      const regeneration = savedRegeneration?.kind === "workflow"
        ? { ...savedRegeneration, prompt: best }
        : savedRegeneration;
      const comfyuiUrl = regeneration?.kind === "workflow"
        ? regeneration.comfyuiUrl
        : settings.comfyuiUrl;
      const result = await persistWorkflowGeneration({
        threadId,
        repoId: repo?.id || "home",
        promptId,
        prompt: best,
        images: r.images || [],
        videos: r.videos || [],
        outputDir: settings.outputDir,
        comfyuiUrl,
        embed: settings.embedModel,
        chat,
        regeneration,
      });
      const blocks = workflowMessages(result.messages);
      setMessages((current) => upsertMessages(current, blocks));
      const firstImage = blocks.find((message) => message.image)?.image;
      if (firstImage && repo?.id) setGeneratedCover(repo.id, firstImage);
      if (result.durable && result.images.some((image) => image.indexed)) {
        window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: threadId }));
      }
      return blocks.length > 0;
    } catch (error) {
      finalizedRef.current.delete(promptId);
      throw error;
    }
  };

  // 轮询某次生成的结果，拿到图片/视频后插入对话流
  // 收尾进度 WS：退订 + 清进度条
  const stopProgress = () => {
    wsUnsubRef.current?.();
    wsUnsubRef.current = null;
    setWfProgress(null);
    setWfNode("");
  };

  const pollResult = (
    promptId: string,
    outputNodeIds: string[] = [],
    regeneration?: RegenerationSnapshot,
  ) => {
    addPending(promptId, outputNodeIds, regeneration);  // 记进行中（含完整重放参数），切仓库/刷新后可恢复
    const comfyuiUrl = regeneration?.kind === "workflow"
      ? regeneration.comfyuiUrl
      : settings.comfyuiUrl;
    dispatch({ t: "workflowStart", promptId });  // 状态 C：工作流出图中
    // 节点 id → 类型名映射（用 capturedGraph，API 格式 {id:{class_type,inputs}}）
    const graph = regeneration?.kind === "workflow" ? regeneration.graph : null;
    const nodeLabel = (id: string): string => {
      try {
        const node = (graph as Record<string, { class_type?: string }>)?.[id];
        return node?.class_type ? `${node.class_type} (#${id})` : `节点 #${id}`;
      } catch { return `节点 #${id}`; }
    };
    // 实时进度：直连 ComfyUI /ws（完成判定仍以下方轮询为准，WS 只驱动进度条）
    stopProgress();
    setWfProgress(0);
    setWfNode("");
    wsUnsubRef.current = subscribeProgress(comfyuiUrl, promptId, {
      onProgress: (pct) => setWfProgress(pct),
      onNode: (id) => setWfNode(nodeLabel(id)),
    });
    let tries = 0;
    const tick = async () => {
      tries += 1;
      try {
        const r = await getResult(promptId, comfyuiUrl, outputNodeIds);
        if (r.status === "completed") {
          const got = await finalizeGeneration(r, promptId);
          if (!got && (r.images?.length || 0) === 0 && (r.videos?.length || 0) === 0 && !pickBestText(r.texts)) {
            pushBot("生成完成，但没有输出（工作流未含 SaveImage / 视频合成 / 文字输出节点）。");
          }
          removePending(promptId);
          stopProgress();
          setSlowWatchPromptId(null);
          dispatch({ t: "workflowDone", promptId });
          return;
        }
        if (r.status === "not_found") {
          // 任务丢失：只有 promptId 还在 pending 里才报错，
          // 若已被 removePending 清掉说明任务已正常完成，静默结束轮询
          if (!getPending().some((p) => p.prompt_id === promptId)) { setSlowWatchPromptId(null); return; }
          removePending(promptId);
          stopProgress();
          setSlowWatchPromptId(null);
          refreshChatBackgroundActivities();
          dispatch({ t: "workflowDone", promptId });
          pushBot("⚠️ 出图任务已丢失（ComfyUI 可能已重启或队列被清空）。如需重新生图，请点工作流卡片的「运转工作流」。");
          return;
        }
      } catch {
        // 历史还没出，继续等
      }
      // 前 150 次每 2 秒（快轮询 5 分钟），之后转慢守望每 15 秒，直到 ~20 分钟硬上限。
      // 全程不 removePending：即使用户不切仓库干等，超长任务(实测 71 节点 4.4 分钟，
      // 甚至更久)出图后也能被这条守望自动 finalize，不再丢图。
      const schedule = pollSchedule(tries);
      if (schedule.releaseBusy) {
        // 快轮询阶段结束仍没完成：解除"运转中"占用不阻塞操作，进入慢守望。
        // 保留 slowWatchPromptId 使停止键继续可见，用户仍可取消。
        stopProgress();
        dispatch({ t: "workflowDone", promptId });
        setSlowWatchPromptId(promptId);
        pushBot("生成较复杂、仍在后台进行，出图后会自动载入（也可在 ComfyUI 面板看进度）。");
      }
      if (schedule.delayMs !== null) {
        setTimeout(tick, schedule.delayMs);
      } else {
        // 达上限仍未出：慢守望结束，清除停止键
        setSlowWatchPromptId(null);
      }
      // 达 210 次(约 20 分钟)仍未出：停止本轮守望，但保留 pending，
      // 下次进仓库/刷新由 resume 兜底重查。
    };
    setTimeout(tick, 1500);
  };

  // 模板是否定义了图像输入口 / 图值是否已填 → 见 lib/chatGeneration（纯逻辑，已抽出可测）
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
      const list = getPending();
      for (const p of list) {
        const action = pendingResumeAction(p, resumedRef.current, Date.now());
        if (action === "skip") continue;  // 本会话已处理过，不重复
        resumedRef.current.add(p.prompt_id);
        if (action === "expire") { removePending(p.prompt_id); continue; }
        const outputNodeIds = p.outputNodeIds || [];  // 主输出过滤（提交时随 pending 落盘）
        try {
            const comfyuiUrl = p.regeneration?.kind === "workflow"
              ? p.regeneration.comfyuiUrl
              : settings.comfyuiUrl;
            const r = await getResult(p.prompt_id, comfyuiUrl, outputNodeIds);
          if (!alive) return;
          if (r.status === "completed") {
            await finalizeGeneration(r, p.prompt_id);
            removePending(p.prompt_id);
            dispatch({ t: "workflowDone", promptId: p.prompt_id });  // 补清工作流态，否则切回后卡在"运转中"
          } else {
            pollResult(p.prompt_id, outputNodeIds, p.regeneration);  // 仍在跑，重新挂轮询
          }
        } catch {
          pollResult(p.prompt_id, outputNodeIds, p.regeneration);    // 查询失败按仍在跑处理，继续轮询
        }
      }
    };
    resume();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);
  // APPEND4_HERE

  // 发送入口：生成进行中默认进队列，否则直接执行
  const send = (content: RichContent) => {
    const text = content.text.trim();
    if (!text && content.images.length === 0 && !content.maskedImage) return;
    atBottomRef.current = true;  // 用户主动发送时强制跟随到底
    if (streamingId || wfRunning) { enqueue(content); return; }
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
    void enqueueChatQueueTask({
      threadId,
      message: text,
      images,
      imageMask: content.maskedImage
        ? { image: content.maskedImage.image, mask: content.maskedImage.mask } : null,
      chat,
      gen: genModel,
      video: videoModel,
      embed: settings.embedModel,
      size, imageQuality,
      outputDir: settings.outputDir, repoId: repo?.id || threadId,
      proxyUrl: settings.proxyEnabled ? settings.proxyUrl : "",
      styleTemplate: activeStyleTemplate(settings), agentId: settings.activeAgentId || "",
      contextMaxTokens: settings.contextMaxTokens,
    }).then((res) => {
      if (res.task?.id) saveQueueContent(res.task.id, content);
      void refreshQueue();
      refreshChatBackgroundActivities();
    }).catch(() => { /* 后端未起：忽略，无持久化 */ });
  };

  // 取消队列里的某条（后端删除 + 清本地内容映射）
  const cancelQueued = (id: string) => {
    dropQueueContent(id);
    setQueued((current) => current.filter((item) => item.id !== id));  // 本地即时移除
    void cancelChatQueueTask(id).catch(() => {});
    void refreshQueue();
    refreshChatBackgroundActivities();
  };

  // 进入/切回仓库时拉取后端排队消息；页面在场时定时刷新，反映 worker 推进后的出队。
  useEffect(() => {
    void refreshQueue();
    if (threadId === "home") return;
    const timer = setInterval(() => { void refreshQueue(); }, 2000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

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
    setMessages((current) => reduceChatStreamEvent(current, botId, event));
  };

  // 自由文本 → 多 Agent（Supervisor/LangGraph 编排，多轮上下文）：主管分派→生图/反推/灵感/工具专家。
  // 复用同一套生命周期（消息/图片/状态/落盘），是"前端生命周期与后端 agent 解耦"的体现。
  // skipUserMsg：用户气泡已由调用方（如编排判定前）提前 push，这里只补 bot 气泡，避免重复。
  // userMsgId：复用调用方预 push 的用户消息 id，保证后端 userMessageId 关联一致。
  const runFreeText = (t: string, content?: RichContent, skipUserMsg = false, userMsgId?: string) => {
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
      threadId, t, images, chat, genModel, size,
      {
        onEvent: (event) => handleAgentStreamEvent(botId, event),
        onDone,
      },
      { outputDir: settings.outputDir, repoId: repo?.id || threadId, embed: settings.embedModel,
        proxyUrl: settings.proxyEnabled ? settings.proxyUrl : "", messageId: botId,
        userMessageId: userMsg.id,
        styleTemplate: activeStyleTemplate(settings), agentId: settings.activeAgentId || "",
        contextMaxTokens: settings.contextMaxTokens,
        imageQuality,
        video: videoModel },
      undefined,
      undefined,
      imageMask,
    );
    abortRef.current = { botId, abort };
  };

  const actOnPromptApproval = (
    approval: PromptApproval,
    action: "submit" | "change" | "cancel",
    editedPrompt?: string,
  ): Promise<void> => new Promise((resolve) => {
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
      threadId, actionText, [], chat, genModel, size,
      {
        onEvent: (event) => handleAgentStreamEvent(botId, event),
        onDone,
      },
      {
        outputDir: settings.outputDir,
        repoId: repo?.id || threadId,
        embed: settings.embedModel,
        proxyUrl: settings.proxyEnabled ? settings.proxyUrl : "",
        messageId: botId,
        styleTemplate: activeStyleTemplate(settings),
        agentId: settings.activeAgentId || "",
        contextMaxTokens: settings.contextMaxTokens,
        imageQuality,
        video: videoModel,
      },
      { approvalId: approval.id, action, editedPrompt },
    );
    abortRef.current = { botId, abort };
  });

  const actOnRouteChoice = (
    choice: RouteChoice,
    route: AgentRoute,
  ): Promise<void> => new Promise((resolve) => {
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
      threadId, source.text, sourceImages, chat, genModel, size,
      {
        onEvent: (event) => handleAgentStreamEvent(botId, event),
        onDone,
      },
      {
        outputDir: settings.outputDir,
        repoId: repo?.id || threadId,
        embed: settings.embedModel,
        proxyUrl: settings.proxyEnabled ? settings.proxyUrl : "",
        messageId: botId,
        userMessageId: source.id,
        styleTemplate: activeStyleTemplate(settings),
        agentId: settings.activeAgentId || "",
        contextMaxTokens: settings.contextMaxTokens,
        imageQuality,
        video: videoModel,
      },
      undefined,
      { route, userMessageId: source.id },
      sourceImageMask,
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
    abortRef.current?.abort();
    abortRef.current = null;
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
    if (pid) removePending(pid);
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
    dropQueueContent(id);
    setQueued((current) => current.filter((q) => q.id !== id));  // 本地即时移除
    void cancelChatQueueTask(id).catch(() => {});                // 从后端队列删除，避免 worker 再跑
    dispatch({ t: "stop" });              // 停当前生成（保留半成品）
    await hardCancel(pid);
    if (sid) {
      setMessages((ms) =>
        ms.map((m) => (m.id === sid ? { ...m, text: (m.text || "") + "（已打断）" } : m)),
      );
    }
    void refreshQueue();
    dispatchSend(item.content);  // 同 thread 新一轮：AI 带上下文续写 = 合并
  };

  const regenerateResult = async (messageId: string) => {
    const message = messages.find((item) => item.id === messageId);
    const snapshot = message?.regeneration;
    if (!snapshot) {
      pushBot("这张历史图片生成时尚未保存完整参数，无法保证准确重生成。");
      return;
    }
    if (streamingId || wfRunning || regeneratingIds.size > 0) {
      pushBot("当前已有生成任务，请等待完成后再重新生图。");
      return;
    }
    setRegeneratingIds((current) => new Set(current).add(messageId));
    try {
      if (snapshot.kind === "ai-image") {
        const model = resolveImageRegenerationModel(snapshot, settings.imageModels);
        if (!model) {
          throw new Error(
            `原生图模型已不存在：${snapshot.model.modelName}（${snapshot.model.baseUrl}）`,
          );
        }
        const rec = await replayImageGeneration(snapshot, { apiKey: model.apiKey }, {
          threadId,
          repoId: repo?.id || "home",
          outputDir: settings.outputDir,
          embed: settings.embedModel,
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
      const submitted = await submitGraph(snapshot.graph, snapshot.comfyuiUrl);
      if (!submitted.prompt_id) throw new Error("ComfyUI 未返回 prompt_id");
      pollResult(submitted.prompt_id, snapshot.outputNodeIds, snapshot);
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

  return {
    messages, streamingId, wfRunning, slowWatchPromptId, wfProgress, wfNode, queued, regeneratingIds,
    send, runCommand, pushBot, pushMsg,
    actOnPromptApproval, actOnRouteChoice, regenerateResult,
    pickTemplate, runWorkflow, updateCardDraft, markCardDone, markCardReopen,
    applyWorkflowOps, ignoreWorkflowOps, editWorkflowOp,
    stopGenerating, guideQueued, cancelQueued,
    confirmReq, compact, compacting,
    contextReminder, dismissContextReminder,
    clearHome, clearCache: clearCacheAction,
  };
}
