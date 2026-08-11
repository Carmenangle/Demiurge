import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  Boxes,
  Bot,
  Clapperboard,
  CornerDownRight,
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
} from "lucide-react";
import { type Repo, type RepoBinding } from "../stores/repos";
import { type WorkMode } from "../lib/viewRouting";
import { modelDisplayName, resolvedEmbedModel, useSettings, activeUserPersona } from "../stores/settings";
import { resolveModelProxy } from "../lib/modelProxy";
import { KnowledgeModal } from "../components/KnowledgeModal";
import { TableModal } from "../components/TableModal";
import { RichInput, type RichContent, type RichInputHandle } from "../components/RichInput";
import { WorkflowCard } from "../components/WorkflowCard";
import { useChatSession } from "../lib/useChatSession";
import { ConfirmModal } from "../components/Modal";
import { MaskEditorModal, type MaskEditorResult } from "../components/MaskEditorModal";
import { StylePresetModal } from "../components/StylePresetModal";
import { MediaInsertModal } from "../components/MediaInsertModal";
import { UserMessage, AssistantMessage, InspirationCard, PortsPlanCard } from "../components/chat/ChatMessages";
import { ModelSwitcher, SizeSwitcher } from "../components/chat/ChatControls";
import { comfyStatus, startComfy, localViewUrl } from "../api/comfyui";
import { listAgents, type Agent } from "../api/agents";
import { listTemplates, type Template } from "../api/workflows";
import { indexDocument } from "../api/ai";
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
  const { displayRegex, characterPortraits } = useChatPresentationAssets(
    cardNames, characterDir, settings.outputDir, repo?.id || "",
    settings.presetDir, settings.activePresetName,
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
    messages, streamingId, wfRunning, slowWatchPromptId, wfProgress, wfNode, queued, regeneratingIds,
    send, runCommand, pushBot,
    actOnPromptApproval, actOnRouteChoice, regenerateResult,
    pickTemplate, runWorkflow, updateCardDraft, markCardDone, markCardReopen,
    applyWorkflowOps, ignoreWorkflowOps, editWorkflowOp,
    stopGenerating, guideQueued, cancelQueued,
    confirmReq, compact, compacting, contextReminder, dismissContextReminder,
    clearHome, clearCache, reloadFromSnapshot,
    editMessage, deleteMessage, regenerateMessage, createCheckpoint, messagesUpTo,
  } = useChatSession({
    repo, settings, setGeneratedCover, chat, genModel, videoModel, workMode,
    size: resolvedImageSize.size,
    imageQuality: generationPreferences.quality, templates, setShowPicker, atBottomRef,
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
    pickTemplate(t);
  };

  // 稳定回调：让 memo 的消息组件在流式/进度刷新时跳过重渲染。
  // 依赖 hook 返回的函数(runCommand)会每渲染变引用，用 latest-ref 兜住，回调本身保持稳定。
  const runCommandRef = useRef(runCommand);
  runCommandRef.current = runCommand;
  const regenerateResultRef = useRef(regenerateResult);
  regenerateResultRef.current = regenerateResult;
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
                title={`剧情插画${settings.illustrate ? "（开）" : "（关）"}：开启后在剧情高潮点（好感度跨档/失控/每段兜底）自动配图，复用生图模型。只存图片地址不回灌对话，按需付费`}
              >
                <ImageIcon size={15} />
              </button>
            )}
            {cardName && (
              <button
                className={`btn icon-only ${settings.mediaInsert?.[repo?.id || ""]?.templateId ? "primary" : ""}`}
                onClick={() => setShowMediaInsert(true)}
                title="多元数据插入：预设本作品剧情高潮点自动出图用的 ComfyUI 工作流模板 + 可选角色 LoRA。保存预设会自动开启剧情插画，出图异步后台进行，可在右侧徽记查看进度"
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
                  isBusy={!!streamingId || wfRunning}
                  onDraft={(draft) => updateCardDraft(m.id, draft)}
                  onDone={(draft, captured) => markCardDone(m.id, draft, captured)}
                  onReopen={() => markCardReopen(m.id)}
                  onRun={() => runWorkflow(m.id)}
                  onNotify={pushBot}
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
                  onInsert={(text) => richRef.current?.insertText(text)}
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
                  onPromptApproval={actOnPromptApproval}
                  onRouteChoice={actOnRouteChoice}
                  onRegenerate={handleRegenerate}
                  regenerating={regeneratingIds.has(m.id)}
                  onEdit={handleEditAssistant}
                  onDelete={handleDeleteMessage}
                  onCreateCheckpoint={hasRepo ? handleCreateCheckpoint : undefined}
                  onBranch={hasRepo && repo?.parentId && onBranch ? handleBranch : undefined}
                />
              )}
              <span className="chat-message-end" data-message-end={m.id} aria-hidden="true" />
              </div>
            ))}
          </div>

          {maskEditorUrl && (
            <MaskEditorModal
              imageUrl={maskEditorUrl}
              onCancel={() => setMaskEditorUrl(null)}
              onComplete={handleMaskComplete}
            />
          )}

          <div className="chat-input-wrap">
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
          placeholder={inputPlaceholder}
        />
        <div className="chat-actions">
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
              {/* 生成中仍可发送：Enter 或点此 = 打断并合并（生图/工作流流程会先确认） */}
              <button
                className="btn primary chat-send-btn"
                title="打断当前生成并发送，AI 会带上下文续写；若在生图/工作流会先确认"
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
          </div>
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
