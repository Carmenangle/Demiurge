import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import {
  ArrowDown,
  Boxes,
  Bot,
  Clapperboard,
  CornerDownRight,
  GitCompareArrows,
  Image as ImageIcon,
  Table,
  MessagesSquare,
  Archive,
  Palette,
  PanelRight,
  Pencil,
  RefreshCw,
  Send,
  Sparkles,
  Film,
  GripHorizontal,
  Trash2,
  Download,
  Upload,
  X,
  Minimize2,
  MessageSquarePlus,
} from "lucide-react";
import { type Repo, type RepoBinding } from "../stores/repos";
import { type WorkMode } from "../lib/viewRouting";
import { modelDisplayName, resolvedEmbedModel, useSettings, activeUserPersona } from "../stores/settings";
import { resolveModelProxy } from "../lib/modelProxy";
import { KnowledgeModal } from "../components/KnowledgeModal";
import { NarrativeCiPanel } from "../components/chat/NarrativeCiPanel";
import { TableModal } from "../components/TableModal";
import { RichInput, type RichContent, type RichInputHandle } from "../components/RichInput";
import { WorkflowCard } from "../components/WorkflowCard";
const CanvasStageFlow = lazy(() => import("./CanvasStageFlow").then((m) => ({ default: m.CanvasStageFlow })));
import { globalPendingToolCreates, canvasBridge } from "../components/canvas/shared";
import { useChatSession } from "../lib/useChatSession";
import { inspirationToAttachment, consumePendingInspirationAttachments, CHAT_INSPIRATION_EVENT } from "../lib/inspirationInsert";
import { ConfirmModal } from "../components/Modal";
import { MaskEditorModal, type MaskEditorResult } from "../components/MaskEditorModal";
import { StylePresetModal } from "../components/StylePresetModal";
import { MediaInsertModal } from "../components/MediaInsertModal";
import { UserMessage, AssistantMessage, InspirationCard, PortsPlanCard } from "../components/chat/ChatMessages";
import { ModelSwitcher, SizeSwitcher } from "../components/chat/ChatControls";
import { comfyStatus, startComfy, localViewUrl } from "../api/comfyui";
import { listAgents, type Agent } from "../api/agents";
import { listTemplates, type Template } from "../api/workflows";
import { indexDocument, proxyImageUrl } from "../api/ai";
import { resolveImageSize, supportsImageQuality } from "../lib/viewRouting";
import { useGenerationPreferences } from "../lib/generationPreferences";
import { useWorkflowTemplatePicker } from "../lib/workflowTemplatePicker";
import { useResizableChatInput } from "../lib/useResizableChatInput";
import { assistantAvatarState } from "../lib/assistantAvatar";
import { resolveCharacterPortrait } from "../lib/characterPortrait";
import { useChatPresentationAssets } from "../lib/useChatPresentationAssets";
import { useChatUnreadTracker } from "../lib/useChatUnreadTracker";
import { useChatTransfer } from "../lib/useChatTransfer";

export function ChatView({
  repo,
  settings,
  update,
  presets,
  setCover,
  setGeneratedCover,
  onBack,
  initialImage,
  onImageConsumed,
  onBranch,
  workMode = "story",
}: {
  repo?: Repo;
  settings: ReturnType<typeof useSettings>["settings"];
  update: ReturnType<typeof useSettings>["update"];
  presets: Pick<ReturnType<typeof useSettings>, "addStylePreset" | "updateStylePreset" | "removeStylePreset">;
  setCover: (id: string, cover: string) => void;
  setGeneratedCover: (id: string, cover: string) => void;
  onBack?: () => void;
  initialImage?: string | null;              // 从资产库「发送至对话」带来的图，挂载后插入输入框
  onImageConsumed?: () => void;
  onBranch?: (binding: Partial<RepoBinding>, msgs: unknown[], isLatest: boolean) => void;
  workMode?: WorkMode;   // 决定输入框提示文案（三模式各异）
}) {
  const streamRef = useRef<HTMLDivElement | null>(null);   // 对话滚动容器
  const atBottomRef = useRef(true);                        // 用户当前是否贴在底部（决定是否自动跟随）
  const richRef = useRef<RichInputHandle | null>(null);
  const chatInput = useResizableChatInput();
  // 显示层正则（markdownOnly）：全局脚本 + 当前卡内嵌脚本里的显示档，渲染 AI 正文前隐藏/压缩 <think> 等区块
  const cardNames = repo?.cardNames?.length ? repo.cardNames : (repo?.cardName ? [repo.cardName] : []);
  const cardName = repo?.openingCardName || repo?.cardName || cardNames[0] || "";
  const characterDir = settings.characterDir || "";
  // 仓库绑定预设优先于全局设置
  const resolvedPresetName = repo?.presetName || settings.activePresetName;
  const { displayRegex, characterPortraits } = useChatPresentationAssets(
    cardNames, characterDir, settings.outputDir, repo?.id || "",
    settings.presetDir, resolvedPresetName,
  );
  // 三模式输入框提示各异
  const inputPlaceholder = {
    story: "推进剧情、描写行动或对话；剧情高潮点会自动生成插画内嵌。Enter 发送，图片用上方 + 添加或直接粘贴",
    generate: "说出你想要的：描述画面直接生图、贴图让它反推或改图、提问绘画；/w 可选专业工作流。Enter 发送，图片用上方 + 添加或直接粘贴",
    code: "创建或检查角色卡、编写作品脚本、读取当前作品文件排错。Enter 发送",
  }[workMode];
  // 显示层宏：{{char}}→角色卡名、{{user}}→选中人设名(缺省「我」)。传给消息组件在渲染处统一替换。
  // useMemo 稳定引用，避免每次渲染新建对象打破消息组件 memo。
  const personaName = activeUserPersona(settings).name || "";
  const embed = resolvedEmbedModel(settings);
  const chatMacros = useMemo(() => ({ char: cardName, user: personaName }), [cardName, personaName]);
  // 资产库带图进来：挂载后插入输入框一次
  useEffect(() => {
    if (initialImage) {
      richRef.current?.insertImage(initialImage);
      richRef.current?.focus();
      onImageConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialImage]);
  const [hasText, setHasText] = useState(false);  // 输入框是否有可发送内容（文本或图片，驱动发送按钮）
  const [maskEditorUrl, setMaskEditorUrl] = useState<string | null>(null);
  // 对话线 id = 仓库 id（首页用 "home"）：后端按此落盘多轮记忆与 RAG 知识库
  const threadId = repo?.id || "home";

  const [modelId, setModelId] = useState(
    settings.activeImageModelId || settings.imageModels[0]?.id || "",
  );
  // 当前选中的对话模型（智能体大脑 + 反推）
  const [chatModelId, setChatModelId] = useState(
    settings.activeChatModelId || settings.chatModels[0]?.id || "",
  );
  const generationPreferences = useGenerationPreferences(threadId);
  const [showStylePresets, setShowStylePresets] = useState(false);  // 风格存档管理弹窗
  const [agents, setAgents] = useState<Agent[]>([]);  // 多 Agent 列表（对话切换用）
  useEffect(() => { listAgents().then((a) => setAgents(a.filter((x) => x.enabled))).catch(() => {}); }, []);
  const activeChat = settings.chatModels.find((m) => m.id === chatModelId) || settings.chatModels[0];
  const chat = {
    baseUrl: activeChat?.baseUrl || "",
    apiKey: activeChat?.apiKey || "",
    modelName: activeChat?.modelName || "",
    providerProfile: activeChat?.providerProfile || "openai_compatible",
    proxyMode: activeChat?.proxyMode,
    proxyUrl: resolveModelProxy(activeChat?.proxyMode, settings.proxyUrl, settings.proxyEnabled),
  };
  // 当前选中的生图模型（底部下拉），传给 agent 的生图工具
  const gm = settings.imageModels.find((m) => m.id === modelId) || settings.imageModels[0];
  const genModel = { baseUrl: gm?.baseUrl || "", apiKey: gm?.apiKey || "", modelName: gm?.modelName || "", proxyMode: gm?.proxyMode };
  const resolvedImageSize = resolveImageSize(
    generationPreferences.aspect,
    generationPreferences.resTier,
    generationPreferences.customEnabled,
    generationPreferences.customWidth,
    generationPreferences.customHeight,
    gm?.supportsCustomSize === true,
  );
  // 当前视频模型（videoModels）：选中的或第一个，传给 agent 的视频工具
  const vm = (settings.videoModels || []).find((m) => m.id === settings.activeVideoModelId) || (settings.videoModels || [])[0];
  const videoModel = { baseUrl: vm?.baseUrl || "", apiKey: vm?.apiKey || "", modelName: vm?.modelName || "", proxyMode: vm?.proxyMode };
  // ComfyUI 节点面板
  const [showComfy, setShowComfy] = useState(false);
  const [comfyRunning, setComfyRunning] = useState(false);
  const [comfyMsg, setComfyMsg] = useState("");
  // Toast 队列：系统消息（LoRA 应用、ComfyUI 提交、报错等）用 2s 自动消失悬浮窗
  const [toasts, setToasts] = useState<Array<{ id: number; msg: string; kind: "info" | "error" | "success" }>>([]);
  const toastIdRef = useRef(0);
  const showToast = useCallback((msg: string, kind: "info" | "error" | "success" = "info") => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, msg, kind }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 2200);
  }, []);
  // 工作流工具卡：ChatView 始终挂载，兜底监听 laf-canvas-workflow-tool，
  // 避免 Canvas 未挂载时事件丢失（Canvas 是 lazy 加载的）。
  // ★ 仅在画布未挂载时写入 globalPendingToolCreates：画布挂载时事件由画布自身监听消费，
  //   若这里也写入会在「切走再切回画布」时 splice 消费残留 → 出现重复工具卡。
  useEffect(() => {
    const onTool = (e: Event) => {
      const detail = (e as CustomEvent).detail as {
        templateId?: string; templateName?: string; estimatedNodeCount?: number;
      } | undefined;
      const templateId = (detail?.templateId || "").trim();
      const templateName = (detail?.templateName || "").trim() || "工作流模板";
      if (!templateId) return;
      if (canvasBridge.canvasMounted) return;
      const cnt = Number(detail?.estimatedNodeCount) || 0;
      // 5a 唯一性：兜底队列里同模板不重复 push（画布挂载后由画布侧再做完整去重）
      if (globalPendingToolCreates.some((p) => p.templateId === templateId)) return;
      globalPendingToolCreates.push({
        id: `wftool-${templateId}`, // 与画布投影同稳定 id：消费后不重复建卡
        templateId, templateName, estimatedNodeCount: cnt,
      });
    };
    window.addEventListener("laf-canvas-workflow-tool", onTool);
    return () => window.removeEventListener("laf-canvas-workflow-tool", onTool);
  }, []);
  // 内容视图切换（对话/画布）：画布=生成内容节点化浏览；功能栏/输入栏恒定（所有模式可用）。
  // 默认对话视图，手动切画布后按作品记忆（laf_view_<workId>）。
  const [contentView, setContentView] = useState<"chat" | "canvas">(() => {
    try {
      const saved = localStorage.getItem(`laf_view_${repo?.id || ""}`);
      if (saved === "canvas" || saved === "chat") return saved;
    } catch { /* ignore */ }
    return "chat";
  });
  const switchContentView = (v: "chat" | "canvas") => {
    setContentView(v);
    try { if (repo?.id) localStorage.setItem(`laf_view_${repo.id}`, v); } catch { /* ignore */ }
  };
  // 画布模式下输入栏折叠为悬浮小球（默认折叠最大化画布空间；点小球展开，左下角按钮收起）
  const [canvasInputFolded, setCanvasInputFolded] = useState(true);
  // 素材库「插入输入框」跨大区通道消费：ChatView 挂载时先取走缓存，之后每次收到通知增量消费。
  // 画布/对话模式共用同一个 RichInput，因此切回后无论停在画布还是对话，灵感卡都进同一输入框。
  useEffect(() => {
    const drain = () => {
      const cards = consumePendingInspirationAttachments();
      if (cards.length === 0) return;
      for (const card of cards) richRef.current?.insertInspirationCard(card);
      setCanvasInputFolded(false);  // 画布模式展开输入栏，让用户看到插入的灵感卡
    };
    drain();  // 挂载时消费（可能素材库推送时本组件未挂载）
    window.addEventListener(CHAT_INSPIRATION_EVENT, drain);
    return () => window.removeEventListener(CHAT_INSPIRATION_EVENT, drain);
  }, []);
  // 小球可上下拖动（对标快捷工具/后台活动浮标）：top 持久化，pointer capture 区分点击/拖动
  const FAB_TOP_KEY = "laf_canvas_input_fab_top";
  const [canvasFabTop, setCanvasFabTop] = useState(() => {
    try {
      const value = Number(localStorage.getItem(FAB_TOP_KEY));
      return Number.isFinite(value) && value > 0 ? value : window.innerHeight - 80;
    } catch { return window.innerHeight - 80; }
  });
  useEffect(() => {
    try { localStorage.setItem(FAB_TOP_KEY, String(canvasFabTop)); } catch { /* ignore */ }
  }, [canvasFabTop]);
  const canvasFabDragRef = useRef<{ moved: boolean; startY: number; startTop: number } | null>(null);
  const onCanvasFabPointerDown = (event: React.PointerEvent) => {
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
    canvasFabDragRef.current = { moved: false, startY: event.clientY, startTop: canvasFabTop };
  };
  const onCanvasFabPointerMove = (event: React.PointerEvent) => {
    const drag = canvasFabDragRef.current;
    if (!drag) return;
    const delta = event.clientY - drag.startY;
    if (Math.abs(delta) > 4) drag.moved = true;
    setCanvasFabTop(Math.min(window.innerHeight - 64, Math.max(8, drag.startTop + delta)));
  };
  const onCanvasFabPointerUp = () => {
    const drag = canvasFabDragRef.current;
    canvasFabDragRef.current = null;
    if (drag && !drag.moved) setCanvasInputFolded(false);  // 未拖动 → 点击展开输入栏
  };
  // 画布工作流运转任务：对话框下方进度条（与画布「生成中」占位节点同源事件驱动）
  const [canvasWfRuns, setCanvasWfRuns] = useState<Array<{
    id: string; templateName: string; progress: number | null; node?: string;
  }>>([]);
  useEffect(() => {
    const onRun = (e: Event) => {
      const d = (e as CustomEvent).detail as { runId?: string; templateName?: string } | undefined;
      if (!d?.runId) return;
      setCanvasWfRuns((prev) => (prev.some((r) => r.id === d.runId)
        ? prev
        : [...prev, { id: d.runId!, templateName: d.templateName || "工作流", progress: null }]));
    };
    const onProg = (e: Event) => {
      const d = (e as CustomEvent).detail as { taskId?: string; progress?: number | null; node?: string; nodeLabel?: string; templateName?: string } | undefined;
      if (!d?.taskId) return;
      const nodeLabel = d.nodeLabel || d.node;   // 优先 class_type 标签（对齐对话模式），裸 id 兜底
      // upsert：编辑器弹窗路径只发 progress 事件（不发 run），进度条同样要出现
      setCanvasWfRuns((prev) => (prev.some((r) => r.id === d.taskId)
        ? prev.map((r) => (r.id === d.taskId ? {
          ...r,
          progress: d.progress !== undefined ? d.progress : r.progress,
          node: nodeLabel !== undefined ? nodeLabel : r.node,
          templateName: d.templateName || r.templateName,
        } : r))
        : [...prev, {
          id: d.taskId!,
          templateName: d.templateName || "工作流",
          progress: d.progress !== undefined ? d.progress : null,
          node: nodeLabel || undefined,
        }]));
    };
    const onDone = (e: Event) => {
      const d = (e as CustomEvent).detail as { taskId?: string } | undefined;
      if (!d?.taskId) return;
      setCanvasWfRuns((prev) => (prev.some((r) => r.id === d.taskId) ? prev.filter((r) => r.id !== d.taskId) : prev));
    };
    window.addEventListener("laf-canvas-wf-run", onRun);
    window.addEventListener("laf-canvas-wf-progress", onProg);
    window.addEventListener("laf-canvas-wf-done", onDone);
    return () => {
      window.removeEventListener("laf-canvas-wf-run", onRun);
      window.removeEventListener("laf-canvas-wf-progress", onProg);
      window.removeEventListener("laf-canvas-wf-done", onDone);
    };
  }, []);
  // 画布视图的输入栏提交：直接复用对话模式的完整发送链路（useChatSession.send）。
  // 画布视图输入栏与对话模式对话框完全同源（均走 useChatSession.send）：
  // 用户消息、调度主管委派、专家执行、产出全部进入同一会话（对话模式可见）。
  // 画布上的「生成中」占位节点由 CanvasStageFlow 依据 streamingId + messages 投影，
  // 表面同步显示委派过程行；生命周期跟随对话流，切走再切回不丢失。
  // 工作流模板与 /w 选择浮层
  const [templates, setTemplates] = useState<Template[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  const templatePicker = useWorkflowTemplatePicker(templates);
  // 知识库：手动参考资料入库弹窗
  const [showKnowledge, setShowKnowledge] = useState(false);
  const [indexingDoc, setIndexingDoc] = useState(false);
  // 多 Agent（Supervisor/LangGraph）已成为唯一模式：自由文本走 /multi-agent，复用同一生命周期。
  // 单 agent 对外入口已下线（其大脑降级为多 Agent 的 tool_agent 专家节点，承接 MCP/工具串联）。

  // 聊天会话引擎：messages/生成生命周期/持久化/编排全部集中在 useChatSession（见 lib/useChatSession）。
  const {
    messages, streamingId, wfRunning, uploadingWf, slowWatchPromptId, wfProgress, wfNode, queued, regeneratingIds,
    send, runCommand, pushBot,
    retryIllustration,
    actOnPromptApproval, actOnRouteChoice, regenerateResult,
    pickTemplate, runWorkflow, updateCardDraft, markCardDone, markCardReopen,
    applyWorkflowOps, ignoreWorkflowOps, editWorkflowOp,
    stopGenerating, stopSlotGeneration, guideQueued, cancelQueued,
    confirmReq, compact, compacting, contextReminder, dismissContextReminder,
    clearHome, clearCache, reloadFromSnapshot,
    editMessage, deleteMessage, regenerateMessage, createCheckpoint, messagesUpTo,
    mergeAudioTracks,
  } = useChatSession({
    repo, settings, setGeneratedCover, chat, genModel, videoModel, workMode,
    size: resolvedImageSize.size,
    imageQuality: generationPreferences.quality, templates, setShowPicker, atBottomRef,
    onNotify: showToast,
  });
  const {
    unreadAgentIds, onStreamScroll, syncUnreadAgentMessages, jumpToFirstUnreadAgentMessage,
  } = useChatUnreadTracker(threadId, messages, streamRef, atBottomRef);
  const {
    snapshotFileRef, handleExportChat, handleImportChatFile,
  } = useChatTransfer(threadId, repo?.name || "会话", reloadFromSnapshot, pushBot);

  const pickTemplateAndRemember = (t: Template) => {
    templatePicker.remember(t.id);
    templatePicker.setQuery("");
    // 画布工具卡事件统一由 useChatSession.pickTemplate 派发（/w 命令与选择器共用），
    // 这里不再重复派发——否则两路径同时触发时画布侧 5a 去重会弹「已在画布上」toast 干扰。
    pickTemplate(t);
  };

  // 稳定回调：让 memo 的消息组件在流式/进度刷新时跳过重渲染。
  // 依赖 hook 返回的函数(runCommand)会每渲染变引用，用 latest-ref 兜住，回调本身保持稳定。
  const runCommandRef = useRef(runCommand);
  runCommandRef.current = runCommand;
  const retryIllustrationRef = useRef(retryIllustration);
  retryIllustrationRef.current = retryIllustration;
  const regenerateResultRef = useRef(regenerateResult);
  regenerateResultRef.current = regenerateResult;
  const promptApprovalRef = useRef(actOnPromptApproval);
  promptApprovalRef.current = actOnPromptApproval;
  const routeChoiceRef = useRef(actOnRouteChoice);
  routeChoiceRef.current = actOnRouteChoice;
  const setCoverRef = useRef(setCover);
  setCoverRef.current = setCover;
  const repoIdRef = useRef(repo?.id);
  repoIdRef.current = repo?.id;

  const handleAddToChat = useCallback((url: string) => richRef.current?.insertImage(url), []);
  const handleEditMessage = useCallback((content: RichContent) => {
    richRef.current?.replaceContent(content);
  }, []);
  const handleSendImage = useCallback((url: string) => {
    richRef.current?.insertImage(url);
    richRef.current?.focus();
  }, []);
  const handleMaskImage = useCallback((url: string) => setMaskEditorUrl(url), []);
  const handleMaskComplete = useCallback((result: MaskEditorResult) => {
    richRef.current?.insertMaskedImage(result);
    richRef.current?.focus();
    setMaskEditorUrl(null);
  }, []);
  const handleRunCommand = useCallback((cmd: string) => runCommandRef.current(cmd), []);
  const handleRegenerate = useCallback(
    (messageId: string, slotId?: string) => regenerateResultRef.current(messageId, slotId),
    [],
  );
  const stopSlotGenerationRef = useRef(stopSlotGeneration);
  stopSlotGenerationRef.current = stopSlotGeneration;
  const handleCancelGeneration = useCallback(
    (messageId: string, slotId: string) => void stopSlotGenerationRef.current(messageId, slotId),
    [],
  );
  const handlePromptApproval = useCallback(
    (...args: Parameters<typeof actOnPromptApproval>) => promptApprovalRef.current(...args),
    [],
  );
  const handleRouteChoice = useCallback(
    (...args: Parameters<typeof actOnRouteChoice>) => routeChoiceRef.current(...args),
    [],
  );
  // ④ AI 消息：编辑 / 检查点 / 分支（回调用 latest-ref 兜住，保持 memo 稳定）
  const editMessageRef = useRef(editMessage);
  editMessageRef.current = editMessage;
  const createCheckpointRef = useRef(createCheckpoint);
  createCheckpointRef.current = createCheckpoint;
  const messagesUpToRef = useRef(messagesUpTo);
  messagesUpToRef.current = messagesUpTo;
  const messageCountRef = useRef(messages.length);
  messageCountRef.current = messages.length;
  const onBranchRef = useRef(onBranch);
  onBranchRef.current = onBranch;
  const repoBindingRef = useRef<Partial<RepoBinding>>({});
  repoBindingRef.current = {
    cardName, cardNames, openingCardName: cardName,
    worldbookName: repo?.worldbookName || "", personaId: repo?.personaId || "",
  };
  const deleteMessageRef = useRef(deleteMessage);
  deleteMessageRef.current = deleteMessage;
  const regenerateMessageRef = useRef(regenerateMessage);
  regenerateMessageRef.current = regenerateMessage;
  const handleEditAssistant = useCallback((id: string, text: string) => editMessageRef.current(id, text), []);
  const handleDeleteMessage = useCallback((id: string) => {
    if (window.confirm("删除这条消息？")) deleteMessageRef.current(id);
  }, []);
  const handleRegenerateMessage = useCallback(
    (id: string) => void regenerateMessageRef.current(id),
    [],
  );
  const handleCreateCheckpoint = useCallback((id: string) => createCheckpointRef.current(id), []);
  const handleBranch = useCallback((id: string) => {
    if (!onBranchRef.current) return;
    const selected = messagesUpToRef.current(id);
    onBranchRef.current(repoBindingRef.current, selected, selected.length === messageCountRef.current);
  }, []);
  const [showTables, setShowTables] = useState(false);
  const [showMediaInsert, setShowMediaInsert] = useState(false);
  const handleSetCover = useCallback((url: string) => {
    const id = repoIdRef.current;
    if (id) setCoverRef.current(id, url);
  }, []);
  const hasRepo = !!repo;

  const submitDocument = (title: string, text: string) => {
    setIndexingDoc(true);
    indexDocument(threadId, text, title, resolvedEmbedModel(settings))
      .then((r) => {
        setShowKnowledge(false);
        pushBot(`已入库 ${r.chunks} 条参考资料，后续对话会自动检索引用。`);
      })
      .catch((e) => pushBot(`参考资料入库失败：${(e as Error).message}`))
      .finally(() => setIndexingDoc(false));
  };

  useEffect(() => {
    listTemplates().then((r) => setTemplates(r.items)).catch(() => {});
  }, []);

  // 面板打开时轮询 ComfyUI 状态
  useEffect(() => {
    if (!showComfy) return;
    let alive = true;
    const check = async () => {
      try {
        const s = await comfyStatus(settings.comfyuiUrl);
        if (alive) setComfyRunning(s.running);
      } catch {
        if (alive) setComfyRunning(false);
      }
    };
    check();
    const t = setInterval(check, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [showComfy, settings.comfyuiUrl]);

  const onStartComfy = async () => {
    if (!settings.comfyuiPath) {
      setComfyMsg("请先在「设置 → 路径」填写 ComfyUI 目录。");
      return;
    }
    setComfyMsg("正在启动 ComfyUI（首次较慢）…");
    try {
      const r = await startComfy(
        settings.comfyuiPath, settings.comfyuiUrl, settings.comfyuiPython,
      );
      setComfyMsg(r.message);
    } catch (e) {
      setComfyMsg(`启动失败：${(e as Error).message}`);
    }
  };

  return (
    <div className="chat-view">
      <div className="chat-view-head">
        {onBack && <button className="back-btn" onClick={onBack}>← 返回</button>}
        <h1>{repo?.name ?? "想生成什么？"}</h1>
        {!repo ? (
          // 首页(home)=临时草稿区：刷新按钮清空当前草稿（内容本就随页面刷新自动清空，这里手动清一次）。
          <button
            className="btn"
            style={{ marginLeft: "auto" }}
            onClick={clearHome}
            disabled={!!streamingId || wfRunning || messages.length === 0}
            title="清空首页草稿（首页内容不留存，刷新页面也会自动清空）"
          >
            <RefreshCw size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} />
            刷新
          </button>
        ) : (
          <>
            <button
              className="btn icon-only"
              style={{ marginLeft: "auto" }}
              onClick={compact}
              disabled={compacting || !!streamingId || wfRunning}
              title="压缩上下文：总结从第一条到最后一条的完整会话，并保留最后成果图；不受普通 Agent 最近 6+6 条读取范围限制"
            >
              <Archive size={15} />
            </button>
            {/* Narrative CI：剧情一致性诊断（非阻断，图标 + 待处置徽标，点击展开浮层） */}
            {repo && <NarrativeCiPanel outputDir={settings.outputDir} repoId={repo.id} />}
            {/* 对话/画布视图切换（功能栏同款 icon-only，-><- 图标） */}
            <button
              className={`btn icon-only ${contentView === "canvas" ? "primary" : ""}`}
              onClick={() => switchContentView(contentView === "canvas" ? "chat" : "canvas")}
              title={contentView === "canvas"
                ? "当前为画布视图，点击切回对话"
                : "当前为对话视图，点击切换画布（节点化浏览生成记录）"}
            >
              <GitCompareArrows size={15} />
            </button>
            {cardName && (
              <button
                className="btn icon-only"
                onClick={() => setShowTables(true)}
                title="数据表：好感度/态度接状态引擎、往事纪要接 RAG（可编辑），及背包/技能/任务/角色等通用表（AI 每轮自动填，可手动改）"
              >
                <Table size={15} />
              </button>
            )}
            {cardName && (
              <button
                className={`btn icon-only ${settings.illustrate ? "primary" : ""}`}
                aria-pressed={settings.illustrate}
                onClick={() => update({ illustrate: !settings.illustrate })}
                title={`剧情自动生成${settings.illustrate ? "（开）" : "（关）"}：开启后在剧情高潮点（好感度跨档/失控/每段兜底）按多元数据插入勾选的类型自动生成图片/视频/音频。异步后台进行，可在右侧徽记查看进度`}
              >
                <ImageIcon size={15} />
              </button>
            )}
            {cardName && (
              <button
                className={`btn icon-only ${settings.mediaInsert?.[repo?.id || ""]?.templateId ? "primary" : ""}`}
                onClick={() => setShowMediaInsert(true)}
                title="多元数据插入：勾选随剧情自动生成的内容类型（图片/视频/音频），并分别预设 ComfyUI 工作流模板 + 按角色参数（LoRA/底图/参考音轨）。保存预设会自动开启剧情自动生成"
              >
                <Clapperboard size={15} />
              </button>
            )}
            <button
              className="btn icon-only"
              onClick={handleExportChat}
              title="导出会话：把本作品的完整会话记录导出为 JSON（备份或搬到别处）"
            >
              <Download size={15} />
            </button>
            <button
              className="btn icon-only"
              onClick={() => snapshotFileRef.current?.click()}
              disabled={compacting || !!streamingId}
              title="导入会话：导入 JSON 会话记录，整体覆盖本作品当前会话"
            >
              <Upload size={15} />
            </button>
            <input
              ref={snapshotFileRef} type="file" accept="application/json,.json"
              hidden onChange={handleImportChatFile}
            />
            <button
              className="btn icon-only"
              onClick={clearCache}
              disabled={compacting || !!streamingId}
              title="清除缓存：清空当前对话内容并删除本仓库上传的参考图（reference 文件夹）；资产库与知识库保留"
            >
              <Trash2 size={15} />
            </button>
          </>
        )}
        <button
          className="btn icon-only"
          onClick={() => setShowKnowledge(true)}
          title="知识库：录入角色设定、画风说明等，AI 对话自动检索"
        >
          <Boxes size={15} />
        </button>
        <button
          className={`btn icon-only ${showComfy ? "primary is-selected" : ""}`}
          aria-pressed={showComfy}
          onClick={() => setShowComfy((s) => !s)}
          title="ComfyUI 节点面板"
        >
          <PanelRight size={15} />
        </button>
      </div>

      <div className="chat-layout">
        <div className="chat-col">
          {contentView === "canvas" ? (
            <Suspense fallback={<div className="chat-stream" style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)" }}>加载画布…</div>}>
              <CanvasStageFlow
                repo={repo}
                settings={settings}
                messages={messages}
                displayRegex={displayRegex}
                onGenerated={() => void reloadFromSnapshot()}
                onDeleteMessage={(id) => deleteMessage(id)}
                onSelectActivePreset={(name) => update({ activePresetName: name })}
                streamingId={streamingId}
                onDraftSubmit={(prompt) => send(
                  { parts: [{ type: "text", text: prompt }], text: prompt, images: [] },
                )}
                onInsertInspiration={(card) => {
                  // 右键灵感卡「插入对话」→ 输入框图片栏 9:16 卡片；发送时封面图走原始 URL、文本带灵感卡语义
                  richRef.current?.insertInspirationCard({
                    id: card.id, title: card.title, content: card.content,
                    imageUrl: card.imageUrl, sourceUrl: card.sourceUrl || card.imageUrl,
                  });
                  setCanvasInputFolded(false); // 展开输入栏，让用户看到插入的灵感卡
                }}
              />
            </Suspense>
          ) : (
          <div
            className="chat-stream"
            ref={streamRef}
            onScroll={onStreamScroll}
            onLoadCapture={() => requestAnimationFrame(syncUnreadAgentMessages)}
          >
            {settings.chatBgPath && (
              <div
                className="chat-bg"
                style={{
                  backgroundImage: `url(${localViewUrl(settings.chatBgPath)})`,
                  backgroundSize: (settings.chatBgFit ?? "cover") === "cover"
                    ? `${(settings.chatBgScale ?? 1) * 100}%`
                    : "contain",
                  backgroundPosition: `${settings.chatBgPosX ?? 50}% ${settings.chatBgPosY ?? 50}%`,
                  backgroundRepeat: "no-repeat",
                  opacity: settings.chatBgOpacity ?? 0.15,
                }}
              />
            )}
            {messages.map((m, mIdx) => (
              <div className="chat-message-anchor" data-message-id={m.id} key={m.id}>
              {m.role === "user" ? (
                <UserMessage
                  msg={m} macros={chatMacros} onAddToChat={handleAddToChat}
                  onEdit={handleEditMessage} onDelete={handleDeleteMessage}
                  onRegenerate={handleRegenerateMessage}
                  regenerationDisabled={!!streamingId || wfRunning}
                />
              ) : m.workflow ? (
                <WorkflowCard
                  msg={m}
                  comfyUrl={settings.comfyuiUrl}
                  chatModel={chat}
                  isBusy={!!streamingId || wfRunning || uploadingWf}
                  uploading={!!uploadingWf}
                  onDraft={(draft) => updateCardDraft(m.id, draft)}
                  onDone={(draft, captured) => markCardDone(m.id, draft, captured)}
                  onReopen={() => markCardReopen(m.id)}
                  onRun={() => runWorkflow(m.id)}
                  onNotify={showToast}
                  onOrchestrate={() => {
                    // 「AI 编排」：往输入框填入 /a 模板名 ，用户补充需求后发送即走编排
                    richRef.current?.insertText(`/a ${m.workflow!.templateName} `);
                  }}
                />
              ) : m.portsPlan ? (
                <PortsPlanCard
                  plan={m.portsPlan}
                  onApply={() => applyWorkflowOps(m.id)}
                  onIgnore={() => ignoreWorkflowOps(m.id)}
                  onEditOp={(opIndex, value) => editWorkflowOp(m.id, opIndex, value)}
                />
              ) : m.inspiration ? (
                <InspirationCard
                  data={m.inspiration}
                  threadId={threadId}
                  messageId={m.id}
                  proxyUrl={settings.proxyEnabled ? settings.proxyUrl : ""}
                  outputDir={settings.outputDir}
                  onNotify={showToast}
                  onInsert={(text, card) => {
                    if (card) {
                      // 灵感卡 → 插入输入框：封面图显示走 proxy（防盗链）、发送走原始 URL（后端 VLM 可访问）
                      const att = inspirationToAttachment(card);
                      if (att.sourceUrl && settings.proxyEnabled && settings.proxyUrl) {
                        att.imageUrl = proxyImageUrl(att.sourceUrl, settings.proxyUrl);
                      }
                      richRef.current?.insertInspirationCard(att);
                    } else {
                      richRef.current?.insertText(text);
                    }
                  }}
                  onSentToCanvas={() => switchContentView("canvas")}
                />
              ) : (
                <AssistantMessage
                  msg={m}
                  streaming={m.id === streamingId}
                  avatarState={assistantAvatarState(m, m.id === streamingId)}
                  portrait={resolveCharacterPortrait(
                    m.text || "", cardNames, cardName, characterPortraits,
                    messages.slice(Math.max(0, mIdx - 2), mIdx)
                      .map((item) => item.text || "").filter(Boolean).join("\n"),
                  )}
                  displayRegex={displayRegex}
                  depth={messages.length - 1 - mIdx}
                  macros={chatMacros}
                  onRunCommand={handleRunCommand}
                  onSendImage={handleSendImage}
                  onMaskImage={handleMaskImage}
                  onSetCover={hasRepo ? handleSetCover : undefined}
                  onPromptApproval={handlePromptApproval}
                  onRouteChoice={handleRouteChoice}
                  onRegenerate={handleRegenerate}
                  onCancelGeneration={handleCancelGeneration}
                  onRetryIllustration={retryIllustration}
                  regenerating={regeneratingIds.has(m.id)}
                  onEdit={handleEditAssistant}
                  onDelete={handleDeleteMessage}
                  onCreateCheckpoint={hasRepo ? handleCreateCheckpoint : undefined}
                  onBranch={hasRepo && repo?.parentId && onBranch ? handleBranch : undefined}
                  onMergeAudio={mergeAudioTracks}
                  visualCiRepoId={repo?.id}
                  visualCiOutputDir={settings.outputDir}
                />
              )}
              <span className="chat-message-end" data-message-end={m.id} aria-hidden="true" />
              </div>
            ))}
          {/* Toast 悬浮层 */}
          {toasts.length > 0 && (
            <div className="canvas-toast-container">
              {toasts.map((t) => (
                <div key={t.id} className={`canvas-toast ${t.kind === "info" ? "" : t.kind}`}>{t.msg}</div>
              ))}
            </div>
          )}
          </div>
          )}

          {maskEditorUrl && (
            <MaskEditorModal
              imageUrl={maskEditorUrl}
              onCancel={() => setMaskEditorUrl(null)}
              onComplete={handleMaskComplete}
            />
          )}

          <div className={`chat-input-wrap${contentView === "canvas" && canvasInputFolded ? " is-canvas-folded" : ""}`}>
            {/* 画布模式：输入栏左下角「收起为悬浮小球」 */}
            {contentView === "canvas" && !canvasInputFolded && (
              <button
                className="chat-input-collapse"
                type="button"
                title="收起为悬浮小球（点击右下角小球恢复）"
                onClick={() => setCanvasInputFolded(true)}
              >
                <Minimize2 size={13} />
                收为小球
              </button>
            )}
            {contextReminder && (
              <div className="context-reminder" role="status">
                <Archive size={16} />
                <span>
                  当前上下文约 {contextReminder.tokens.toLocaleString()} tokens，建议压缩以降低调用成本并保持前后约束清晰。
                </span>
                <button
                  className="btn"
                  disabled={compacting || !!streamingId || wfRunning}
                  onClick={compact}
                >
                  {compacting ? "压缩中…" : "压缩上下文"}
                </button>
                <button
                  className="icon-btn"
                  title="本轮稍后提醒"
                  onClick={dismissContextReminder}
                >
                  <X size={15} />
                </button>
              </div>
            )}
            {showPicker && (
              <div className="tpl-picker">
                <div className="tpl-picker-head">
                  <span>选择工作流模板</span>
                  <button
                    className="icon-btn"
                    style={{ background: "transparent", color: "var(--text)" }}
                    onClick={() => setShowPicker(false)}
                  >
                    <X size={15} />
                  </button>
                </div>
                {templates.length === 0 ? (
                  <p style={{ color: "var(--text-muted)", fontSize: 13, padding: "8px 12px" }}>
                    还没有模板，去「工作流模板」里创建并保存。
                  </p>
                ) : (
                  <>
                    <input
                      className="tpl-search"
                      autoFocus
                      value={templatePicker.query}
                      onChange={(e) => templatePicker.setQuery(e.target.value)}
                      placeholder="搜索模板名称…"
                    />
                    {templatePicker.count === 0 ? (
                      <p style={{ color: "var(--text-muted)", fontSize: 13, padding: "8px 12px" }}>
                        没有匹配「{templatePicker.query}」的模板。
                      </p>
                    ) : (
                      <>
                        {templatePicker.recent.length > 0 && (
                          <>
                            <div className="tpl-group-label">最近使用</div>
                            {templatePicker.recent.map((t) => (
                              <button
                                key={t.id}
                                className="tpl-item"
                                onClick={() => pickTemplateAndRemember(t)}
                              >
                                <strong>{t.name}</strong>
                                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                                  {t.exposed.length} 个字段
                                </span>
                              </button>
                            ))}
                          </>
                        )}
                        {templatePicker.recent.length > 0 && templatePicker.normal.length > 0 && (
                          <div className="tpl-group-label">全部模板</div>
                        )}
                        {templatePicker.normal.map((t) => (
                          <button
                            key={t.id}
                            className="tpl-item"
                            onClick={() => pickTemplateAndRemember(t)}
                          >
                            <strong>{t.name}</strong>
                            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                              {t.exposed.length} 个字段
                            </span>
                          </button>
                        ))}
                      </>
                    )}
                  </>
                )}
              </div>
            )}
          <div className="chat-input-bar">
            {unreadAgentIds.length > 0 && (
              <button
                className="chat-new-message-btn"
                type="button"
                title="跳到第一条新消息"
                aria-label="跳到第一条新消息"
                onClick={jumpToFirstUnreadAgentMessage}
              >
                <ArrowDown size={20} />
              </button>
            )}
            <div
              className="chat-input-resize-handle"
              role="separator"
              aria-label="调整输入框高度"
              aria-orientation="horizontal"
              aria-valuemin={72}
              aria-valuemax={360}
              aria-valuenow={chatInput.height}
              tabIndex={0}
              title="上下拖动调整输入框高度"
              onMouseDown={(e) => {
                e.preventDefault();
                chatInput.beginResize(e.clientY);
              }}
              onKeyDown={(e) => {
                if (chatInput.resizeByKey(e.key)) e.preventDefault();
              }}
            >
              <GripHorizontal size={18} />
            </div>
        {queued.length > 0 && (
          <div className="queue-strip">
            {queued.map((q) => (
              <div className="queue-row" key={q.id}>
                <CornerDownRight size={14} className="queue-row-icon" />
                <span className="queue-row-text" title={q.text || "（图片）"}>
                  {q.text || "（图片）"}
                </span>
                <button
                  className="queue-row-btn"
                  title="打断当前生成，让 AI 结合已生成内容继续处理这条（生图/工作流会先确认后果）"
                  onClick={() => guideQueued(q.id)}
                >
                  <CornerDownRight size={13} /> 引导
                </button>
                <button
                  className="queue-row-edit"
                  title="移回输入框编辑"
                  aria-label="编辑排队消息"
                  onClick={() => {
                    richRef.current?.replaceContent(q.content);
                    cancelQueued(q.id);
                  }}
                >
                  <Pencil size={13} />
                </button>
                <button className="queue-row-del" title="删除这条排队消息" onClick={() => cancelQueued(q.id)}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
        <RichInput
          ref={richRef}
          height={chatInput.height}
          onSubmit={send}
          onCanSubmitChange={setHasText}
          templateNames={templates.map((t) => t.name)}
          placeholder={contentView === "canvas"
            ? (streamingId ? "生成中…" : "输入提示词，为图生图 / 文生图 / 加参考图…")
            : inputPlaceholder}
        />
        <div className="chat-actions">
          {/* 画布工作流运转进度条（展开态）：与对话模式一致，嵌入工具栏行、
              位于模型切换器等功能按钮左侧（margin-right:auto 占满左侧空余）。
              折叠为小球时由 wrap 外的 .canvas-wf-runs 浮条兜底显示。 */}
          {contentView === "canvas" && !canvasInputFolded && canvasWfRuns.length > 0 && (
            <div className="canvas-wf-runs canvas-wf-runs-inline">
              {canvasWfRuns.map((r) => (
                <div key={r.id} className="wf-progress-wrap" style={{ maxWidth: "none", width: "100%" }}>
                  <div className="wf-progress" title={r.progress != null ? `工作流进度 ${r.progress}%` : "工作流运转中（排队/初始化）"}>
                    <div className="wf-progress-bar" style={{ width: `${r.progress ?? 0}%` }} />
                    <span className="wf-progress-txt">{r.progress != null ? `${r.progress}%` : "运转中…"}</span>
                  </div>
                  <span className="wf-progress-node" title="当前执行节点，若长时间不变可能卡住">
                    {r.node ? `节点 ${r.node} · ` : ""}{r.templateName}
                  </span>
                </div>
              ))}
            </div>
          )}
          <ModelSwitcher
            icon={<MessagesSquare size={18} />}
            label="对话模型"
            items={settings.chatModels.map((m) => ({ id: m.id!, name: modelDisplayName(m) }))}
            activeId={chatModelId}
            emptyHint="未配置对话模型（去设置添加）"
            onPick={(id) => { setChatModelId(id); update({ activeChatModelId: id }); }}
          />
          <ModelSwitcher
            icon={<Sparkles size={18} />}
            label="生图模型"
            items={settings.imageModels.map((m) => ({ id: m.id, name: modelDisplayName(m) }))}
            activeId={modelId}
            emptyHint="未配置生图模型（去设置添加）"
            onPick={(id) => { setModelId(id); update({ activeImageModelId: id }); }}
          />
          <ModelSwitcher
            icon={<Film size={18} />}
            label="视频模型"
            items={(settings.videoModels || []).map((m) => ({ id: m.id, name: modelDisplayName(m) }))}
            activeId={vm?.id || ""}
            emptyHint="未配置视频模型（去设置添加）"
            onPick={(id) => update({ activeVideoModelId: id })}
          />
          <SizeSwitcher
            aspect={generationPreferences.aspect}
            resTier={generationPreferences.resTier}
            quality={generationPreferences.quality}
            qualitySupported={supportsImageQuality(genModel.modelName)}
            customEnabled={generationPreferences.customEnabled}
            customWidth={generationPreferences.customWidth}
            customHeight={generationPreferences.customHeight}
            customSizeSupported={gm?.supportsCustomSize === true}
            onPick={generationPreferences.update}
            onCustomChange={generationPreferences.updateCustom}
          />
          <ModelSwitcher
            icon={<Palette size={18} />}
            label="提示词风格"
            items={[
              { id: "none", name: "不启用（原样直出）" },
              ...(settings.stylePresets || []).map((p) => ({ id: `preset:${p.id}`, name: `★ ${p.name || "未命名存档"}` })),
              { id: "__manage__", name: "＋ 管理风格存档…" },
            ]}
            activeId={settings.imageStyle && settings.imageStyle.startsWith("preset:") ? settings.imageStyle : "none"}
            emptyHint="提示词风格"
            onPick={(id) => {
              if (id === "__manage__") { setShowStylePresets(true); return; }
              update({ imageStyle: id === "none" ? "" : id });
            }}
          />
          {agents.length > 0 && (
            <ModelSwitcher
              icon={<Bot size={18} />}
              label="智能体"
              items={[
                { id: "none", name: "默认（内置）" },
                ...agents.map((a) => ({ id: a.id, name: a.name || "未命名" })),
              ]}
              activeId={settings.activeAgentId || "none"}
              emptyHint="智能体"
              onPick={(id) => update({ activeAgentId: id === "none" ? "" : id })}
            />
          )}
          {(streamingId || wfRunning || !!slowWatchPromptId) ? (
            <>
              {(wfRunning || !!slowWatchPromptId) && wfProgress !== null && (
                <div className="wf-progress-wrap">
                  <div className="wf-progress" title={`工作流进度 ${wfProgress}%`}>
                    <div className="wf-progress-bar" style={{ width: `${wfProgress}%` }} />
                    <span className="wf-progress-txt">{wfProgress}%</span>
                  </div>
                  {wfNode && <span className="wf-progress-node" title="当前执行节点，若长时间不变可能卡住">{wfNode}</span>}
                </div>
              )}
              {/* Agent 生成中会进入消息队列；只有 ComfyUI 运行时则直接开始下一轮对话。 */}
              <button
                className="btn primary chat-send-btn"
                title={streamingId ? "加入消息队列，当前 Agent 完成后按序处理" : "发送下一轮对话；ComfyUI 继续在后台运行"}
                onClick={() => richRef.current?.submit()}
                disabled={!hasText}
              >
                <Send size={16} style={{ marginRight: 6, verticalAlign: "-2px" }} />
                发送
              </button>
              <button className="btn danger" title="仅停止当前生成，不发送" onClick={stopGenerating}>
                <X size={16} style={{ marginRight: 6, verticalAlign: "-2px" }} />
                停止
              </button>
            </>
          ) : (
            <button className="btn primary chat-send-btn" onClick={() => richRef.current?.submit()} disabled={!hasText}>
              <Send size={16} style={{ marginRight: 6, verticalAlign: "-2px" }} />
              发送
            </button>
          )}
        </div>
      </div>
      {/* 画布工作流运转进度条（折叠为小球兜底）：独立于 chat-input-wrap（wrap display:none 会吞掉内部元素）。
          展开态进度条已嵌入 chat-actions 工具栏行（功能按钮左侧），与对话模式一致；
          仅当输入栏折叠为小球时才在底部显示独立浮条，任务结束自动消失。 */}
      {contentView === "canvas" && canvasInputFolded && canvasWfRuns.length > 0 && (
        <div className="canvas-wf-runs">
          {canvasWfRuns.map((r) => (
            <div key={r.id} className="wf-progress-wrap" style={{ maxWidth: "none", width: "100%" }}>
              <div className="wf-progress" title={r.progress != null ? `工作流进度 ${r.progress}%` : "工作流运转中（排队/初始化）"}>
                <div className="wf-progress-bar" style={{ width: `${r.progress ?? 0}%` }} />
                <span className="wf-progress-txt">{r.progress != null ? `${r.progress}%` : "运转中…"}</span>
              </div>
              <span className="wf-progress-node" title="当前执行节点，若长时间不变可能卡住">
                {r.node ? `节点 ${r.node} · ` : ""}{r.templateName}
              </span>
            </div>
          ))}
        </div>
      )}
      </div>
      {/* 画布模式悬浮小球：输入栏折叠为圆球的入口（点击恢复输入栏原位）。
          注意必须放在 chat-input-wrap 之外——wrap 折叠时 display:none 会连小球一起隐藏。
          对标快捷工具/后台活动：可上下拖动（pointer capture），拖动位置持久化。 */}
      {contentView === "canvas" && canvasInputFolded && (
        <button
          className="canvas-input-fab"
          type="button"
          title="展开对话框（画布输入，拖动可移动）"
          style={{ top: canvasFabTop, bottom: "auto" }}
          onPointerDown={onCanvasFabPointerDown}
          onPointerMove={onCanvasFabPointerMove}
          onPointerUp={onCanvasFabPointerUp}
        >
          <MessageSquarePlus size={22} />
        </button>
      )}
        </div>

        {showComfy && (
          <div className="comfy-panel">
            <div className="comfy-panel-head">
              <strong>ComfyUI 节点面板</strong>
              <button
                className="icon-btn"
                style={{ background: "transparent", color: "var(--text)" }}
                onClick={() => setShowComfy(false)}
              >
                <X size={16} />
              </button>
            </div>
            {comfyRunning ? (
              <iframe className="comfy-frame" src={settings.comfyuiUrl} title="ComfyUI" />
            ) : (
              <div className="comfy-empty">
                <p style={{ color: "var(--text-muted)" }}>
                  ComfyUI 未运行。复杂节点（如 D 站画廊）需在原生界面里选图操作。
                </p>
                <button className="btn primary" onClick={onStartComfy}>
                  启动 ComfyUI
                </button>
                {comfyMsg && <p style={{ color: "var(--text-muted)", fontSize: 13 }}>{comfyMsg}</p>}
              </div>
            )}
          </div>
        )}
      </div>

      {showTables && (
        <TableModal
          outputDir={settings.outputDir}
          repoId={threadId}
          cardName={cardName}
          chat={chat}
          onClose={() => setShowTables(false)}
        />
      )}
      {showKnowledge && (
        <KnowledgeModal
          repoName={repo?.name ?? "首页"}
          repoId={threadId}
          busy={indexingDoc}
          embed={resolvedEmbedModel(settings)}
          onSubmit={submitDocument}
          onClose={() => setShowKnowledge(false)}
        />
      )}
      {confirmReq && (
        <ConfirmModal
          title="确认操作"
          message={confirmReq.message}
          confirmText="确认"
          danger
          onConfirm={() => confirmReq.resolve(true)}
          onCancel={() => confirmReq.resolve(false)}
        />
      )}
      {showStylePresets && (
        <StylePresetModal
          presets={settings.stylePresets || []}
          onAdd={presets.addStylePreset}
          onUpdate={presets.updateStylePreset}
          onRemove={presets.removeStylePreset}
          onClose={() => setShowStylePresets(false)}
        />
      )}
      {showMediaInsert && (
        <MediaInsertModal
          templates={templates}
          cardName={repo?.cardName || ""}
          cardNames={cardNames}
          modelsDir={settings.modelsDir || (settings.comfyuiPath ? `${settings.comfyuiPath}/models` : "")}
          repoId={repo?.id || ""}
          outputDir={settings.outputDir || ""}
          preset={settings.mediaInsert?.[repo?.id || ""]}
          onSave={(preset) => {
            const key = repo?.id || "";
            update({
              illustrate: true,
              mediaInsert: { ...(settings.mediaInsert || {}), [key]: preset },
            });
            setShowMediaInsert(false);
          }}
          onClose={() => setShowMediaInsert(false)}
        />
      )}
    </div>
  );
}
