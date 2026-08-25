// CanvasStageFlow.tsx — 生成画布内容区（ReactFlow v12 版）
//
// 用 @xyflow/react 提供专业画布能力：拖拽、pan/zoom、连线（edges=metadata references）、
// NodeResizer 缩放、框选；自定义节点卡片复用 CanvasStage 的渲染语义（图片原比例/字段/video/audio/input）。
// 布局/连线/视口持久化到后端 canvas.json（lib/canvasLayout），不污染 generation_store/快照/角色卡。
//
// 交互：
//   - 左键节点拖动（ReactFlow 内置）
//   - 滚轮缩放 / 空白拖动 pan（内置）
//   - 节点右侧手柄拖到另一节点左侧 → 连线（metadata references）
//   - 双击节点 → 详情面板
//   - 拖动松开时中心点对齐吸附（±10px）

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow, Controls, MiniMap, Background, BackgroundVariant,
  useNodesState, useEdgesState, addEdge, MarkerType,
  type Node, type Edge, type EdgeChange, type Connection, type OnNodeDrag,
  type OnConnect, type NodeChange, type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Edit3, FolderPlus, GitBranch, ListOrdered, MessageSquarePlus, Send, Sparkles, Trash2, Upload, X } from "lucide-react";
import { chatAppend, fetchHistory, listGenerations, listReferenceImages, type Generation, type ReferenceImage } from "../api/ai";
import { resolvedEmbedModel, useSettings } from "../stores/settings";
import type { Repo } from "../stores/repos";
import {
  computeAxisSnap, generationNodeType, nodeDetail, nodeAbsolutePosition, placeNewNodes, projectNodes,
  type CanvasNode, type GenLike, type InspirationKind, type PlacedRect,
} from "../lib/canvasRuntime";
import {
  loadLayout, saveLayout, clampScale, type NodeLayout, type InspirationCardStored, type ReferenceImageStored, type Viewport,
} from "../lib/canvasLayout";
import { SendToChatModal, type SendPayload } from "../components/SendToChatModal";
import { ConfirmModal } from "../components/Modal";
import { WorkflowToolModal } from "../components/WorkflowToolModal";
import { listCharacters, characterDetail, characterRepoDetail } from "../api/characters";
import { listWorldbookEntries, repoWorldbookEntries, type RepoWorldbookLoc } from "../api/worldbook";
import { WorldBookPopup } from "../components/WorldBookPopup";
import { AudioPlayer } from "../components/AudioPlayer";
import { PresetModal } from "../components/PresetModal";
import { CanvasCharacterModal } from "../components/CanvasCharacterModal";
import { presetDetail } from "../api/preset";
import type { ChatMessage } from "../types/chat";
import { conversationMediaUrls, conversationTurnUrls, filterGensByConversation, pruneUnboundInspirationCards, projectWorkflowTools, projectStoryNodes } from "../lib/canvasConversation";
import { pollWorkflowResult } from "../lib/workflowGenerationRuntime";
import { workflowGenMetadata } from "../lib/regeneration";
import {
  CARD_W, SNAP_PX, SNAP_RELEASE_PX, HINT_PX,
  INSPIRATION_CARD_W, INSPIRATION_CARD_H, INSPIRATION_META,
  type CardNodeData, type Guide, type SnapAxisState, type ToastItem,
} from "../components/canvas/CanvasTypes";
import { canvasNodeTypes } from "../components/canvas/CardNodeComponent";
import { InitFitView, FlowBridge, GuidesOverlay, ViewportBridge } from "../components/canvas/CanvasHelpers";
import { ToastLayer } from "../components/canvas/ToastLayer";
import { CenterCanvasButton } from "../components/canvas/CenterCanvasButton";
import { IMAGE_EXTENSIONS } from "../components/canvas/CanvasTypes";
import { saveWebMaterial } from "../api/ai";
import { createUndoStack, pushSnapshot, undo, redo, handleCanvasKeyDown } from "../lib/canvasKeyboard";
import { submitGraph, finalizeGeneration } from "../api/comfyui";
import { subscribeProgress } from "../lib/comfyProgress";
import { globalPendingToolCreates, canvasBridge } from "../components/canvas/shared";
import { activeChatModel } from "../stores/settings";
import { runScripts, Placement, type RegexScript } from "../lib/regexEngine";
import { renderMarkdown } from "../lib/renderMarkdown";

const nodeTypes = canvasNodeTypes;

// ===== ReactFlow 画布 =====
export function CanvasStageFlow({
  repo, settings, messages, streamingId, onGenerated, onSelectActivePreset, onDraftSubmit, displayRegex, onDeleteMessage,
}: {
  repo?: Repo;
  settings: ReturnType<typeof useSettings>["settings"];
  /** 当前作品对话消息（画布只投影对话里的实际产出，历史生成不进画布） */
  messages: ChatMessage[];
  /** 当前正在流式生成的 assistant 消息 id（画布「生成中」占位节点据此投影，跟随对话流生命周期） */
  streamingId?: string | null;
  /** 显示层正则（markdownOnly）：剧情节点楼层正文渲染前隐藏/压缩 <think> 等区块（与对话模式同款） */
  displayRegex?: RegexScript[];
  /** 画布内生成落库后回调（用于让上层刷新对话消息，使新产出进入对话→画布） */
  onGenerated?: () => void;
  /** 预设弹窗里切换激活预设（写回 settings.activePresetName） */
  onSelectActivePreset?: (name: string) => void;
  /** 双击新建的 draft 输入卡提交生成（上层复用画布输入栏的生成链路） */
  onDraftSubmit?: (prompt: string) => Promise<void> | void;
  /** 删除剧情楼层节点时回调（等效删除对话中的对应消息，由上层 deleteMessage 执行） */
  onDeleteMessage?: (messageId: string) => void;
}) {
  const [gens, setGens] = useState<Generation[]>([]);
  // gens 的稳定引用：latestContentAnchor 等稳定回调（[] deps）读取最新生成记录用
  const gensRef = useRef(gens);
  gensRef.current = gens;
  const [loading, setLoading] = useState(true);
  const [detailNode, setDetailNode] = useState<CanvasNode | null>(null);
  const [showStoryThinking, setShowStoryThinking] = useState(false);
  const [sendTarget, setSendTarget] = useState<{ title: string; payload: SendPayload } | null>(null);
  // Toast 队列：非实际产出（提交/失败/状态提示）用 2s 自动消失弹窗替代模态框
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastIdRef = useRef(0);
  const showToast = useCallback((msg: string, kind: "info" | "error" | "success" = "info") => {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, msg, kind }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 2200);
  }, []);
  // 图片原始尺寸：用 ref 而非 state，避免每次图片加载触发 setState → 巨大 useEffect 重跑 → 全部节点重建
  const naturalSizesRef = useRef<Record<string, { w: number; h: number }>>({});
  // 稳定 embed 引用：resolvedEmbedModel 每次返回新对象 → refresh 回调重建 →
  // useEffect 重跑 → setLoading(true) → 「加载生成记录…」反复闪现（尤其缩放后点卡片时）
  const embed = useMemo(() => resolvedEmbedModel(settings),
    [settings.embedModel, settings.proxyUrl, settings.proxyEnabled]);
  const repoId = repo?.id || "";

  // 画布视口持久化：onMoveEnd 更新 ref，persistNow 落盘
  const viewportRef = useRef<Viewport>({ x: 0, y: 0, scale: 1 });
  const [savedViewport, setSavedViewport] = useState<Viewport>({ x: 0, y: 0, scale: 1 });

  // 追踪最新生成节点：refresh 后对比 gen.id 差集 → 回到画布中心时以它们为焦点
  const prevGenIdSet = useRef<Set<string>>(new Set());
  const [newNodeIds, setNewNodeIds] = useState<string[]>([]);

  // ===== 对话实际内容自动导入（用户拍板规则）=====
  // 画布只自动投影「当前对话里的实际产出」；资产库（generation_store 全量）需用户手动导入。
  // 媒体 URL 来源合并两处，避免重进画布时内存 messages 未加载完导致画布空：
  //   1. 内存 messages（实时，会话内变化即时反映）
  //   2. 后端 /ai/chat/history（持久化真源，重进/刷新后可靠，不依赖前端加载时序）
  const liveUrls = useMemo(() => conversationMediaUrls(messages), [messages]);
  const [historyUrls, setHistoryUrls] = useState<Set<string>>(new Set());
  // 重拉持久化对话历史中的媒体 URL（首次挂载 + laf-generation-saved 时调用）：
  // finalize_workflow_batch 会把新产出作为助手消息 upsert 进快照/checkpoint，
  // 重拉后 conversationUrls 实时包含新图 → 画布投影过滤放行 → 无需页面刷新。
  // 返回拉到的 URL 集合：首次挂载时先拉历史再投影，避免 conversationUrls 尚空时
  // refresh() 用 filterGensByConversation 把生成内容节点过滤光（首次进画布空白 bug）。
  const loadHistoryUrls = useCallback(async (): Promise<Set<string>> => {
    if (!repoId) return new Set();
    try {
      const r = await fetchHistory(repoId);
      const urls = conversationTurnUrls(r?.items || []);
      setHistoryUrls(urls);
      return urls;
    } catch { /* 历史拉取失败不阻塞画布（仍可用内存 messages 兜底） */ }
    return new Set();
  }, [repoId]);
  const conversationUrls = useMemo(() => {
    const s = new Set(liveUrls);
    for (const u of historyUrls) s.add(u);
    return s;
  }, [liveUrls, historyUrls]);
  // 稳定签名：仅当 URL 集合内容变化才触发 refresh（避免 messages 引用每次变化都重拉）
  const conversationUrlSignature = useMemo(
    () => [...conversationUrls].sort().join("\n"),
    [conversationUrls],
  );

  // 当前仓库绑定集合（画布初始状态只显示绑定的角色卡/世界书/预设；未绑定则没有）
  const boundCards = useMemo(
    () => new Set((repo?.cardNames || []).map((n) => n.trim()).filter(Boolean)),
    [repo?.cardNames],
  );
  const boundWorldbook = useMemo(() => (repo?.worldbookName || "").trim(), [repo?.worldbookName]);
  const boundPreset = useMemo(() => (repo?.presetName || settings.activePresetName || "").trim(), [repo?.presetName, settings.activePresetName]);

  const [layoutNodes, setLayoutNodes] = useState<Record<string, NodeLayout>>({});
  const [layoutEdges, setLayoutEdges] = useState<{ source: string; target: string }[]>([]);
  // 剧情顺序线开关：自动派生连线（紫色虚线箭头）按剧情先后连接相邻楼层，不落盘；
  // 偏好存 localStorage（默认开）。
  const [showStoryFlow, setShowStoryFlow] = useState<boolean>(() => localStorage.getItem("canvas_show_story_flow") !== "0");
  const showStoryFlowRef = useRef(showStoryFlow);
  showStoryFlowRef.current = showStoryFlow;
  const toggleShowStoryFlow = useCallback(() => {
    setShowStoryFlow((v) => {
      const next = !v;
      localStorage.setItem("canvas_show_story_flow", next ? "1" : "0");
      return next;
    });
  }, []);
  // 剧情节点投影列表（数组顺序 = 剧情顺序）：供自动时序线与「整理剧情顺序」使用
  const storyNodesRef = useRef<Node<CardNodeData>[]>([]);
  // 当前对话消息的稳定引用：undo/redo 恢复时过滤「消息已删」的剧情楼层（删除楼层=删消息，不可撤销）
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  // 新剧情节点待落盘位置：首次投影用 findFreeSpot 算出位置后立即写 layoutNodes + 持久化，
  // 否则位置不进 canvas.json，重进画布时由 findFreeSpot 重新计算 → 剧情楼层漂移。
  const pendingStoryLayoutsRef = useRef<Record<string, { x: number; y: number; w: number; h: number }>>({});
  // 自动剧情顺序线的 id 集合：onEdgesChange 拦截 remove/replace，用户删不掉派生线
  const autoEdgeIdsRef = useRef<Set<string>>(new Set());
  const layoutNodesRef = useRef(layoutNodes);
  layoutNodesRef.current = layoutNodes;
  // 已删除投影节点黑名单（持久化到 canvas.json）：投影过滤用，防 refresh/重投影复活。
  // 同「会话快照已删消息不得复活」契约：删除是显式操作，必须由黑名单而非本地过滤实现。
  const [deletedIds, setDeletedIds] = useState<string[]>([]);
  const deletedIdsRef = useRef(deletedIds);
  deletedIdsRef.current = deletedIds;

  // refresh 竞态保护：并发拉取（laf-generation-saved 高频触发）时旧响应后到会覆盖新数据
  // → 新生成的节点「消失」。用序列号丢弃过期响应。
  // ★ 画布只自动投影「对话实际内容」（用户拍板：资产库需手动导入，不自动全量进画布）：
  //   filterGensByConversation 按 conversationUrls 过滤；URL 集合变化（对话新增产出）时重拉。
  const refreshSeqRef = useRef(0);
  const refresh = useCallback(async (urlsOverride?: Set<string>) => {
    if (!repoId) return;
    const seq = ++refreshSeqRef.current;
    setLoading(true);
    try {
      const r = await listGenerations(repoId, embed);
      if (seq !== refreshSeqRef.current) return; // 过期响应，丢弃
      const filtered = filterGensByConversation(r.items || [], urlsOverride ?? conversationUrls);
      // 后端返回 snake_case 元数据字段 → 前端 camelCase（GenLike 接口）
      setGens(filtered.map((g: any) => ({
        ...g,
        templateName: g.template_name || g.templateName || "",
        modelName: g.model_name || g.modelName || "",
        loraName: g.lora_names ? (typeof g.lora_names === "string" ? g.lora_names.split(",").filter(Boolean)[0] || "" : "") : (g.loraName || ""),
        loraNames: g.lora_names ? (typeof g.lora_names === "string" ? g.lora_names.split(",").filter(Boolean) : []) : (g.loraNames || []),
      })));
    } catch {
      if (seq !== refreshSeqRef.current) return;
      setGens([]);
    }
    if (seq === refreshSeqRef.current) setLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, embed, conversationUrlSignature]);

  // 加载 gens + 布局
  useEffect(() => {
    let alive = true;
    // ★ 首次进入先拉对话历史媒体 URL（持久化真源）再投影：
    //   否则 conversationUrls 尚空，refresh() 的 filterGensByConversation 会把
    //   生成内容节点全部过滤掉 → 首次进画布空白，要切走再切回（重挂载）才出现。
    (async () => {
      const urls = await loadHistoryUrls();
      if (alive) await refresh(urls);
    })();
    const onSaved = () => {
      void refresh();
      // ★ 实时更新兜底（无需页面刷新）：laf-generation-saved 时重拉对话历史，
      //   让 conversationUrls 实时包含新产出（workflow finalize 已 upsert 快照），
      //   投影过滤放行后生成内容节点立即出现，不依赖上层回调与页面刷新。
      void loadHistoryUrls();
    };
    window.addEventListener("laf-generation-saved", onSaved);
    return () => { alive = false; window.removeEventListener("laf-generation-saved", onSaved); };
  }, [refresh, loadHistoryUrls]);

  // gens 变化时计算新增节点 ID（用于「回到画布中心」聚焦新生成节点）
  useEffect(() => {
    const curIds = new Set(gens.map((g) => g.id));
    const prev = prevGenIdSet.current;
    // 首次加载：全部视为新节点
    if (prev.size === 0) {
      prevGenIdSet.current = curIds;
      if (gens.length > 0) {
        const raw = projectNodes(gens).nodes;
        setNewNodeIds(raw.map((n) => n.id));
      }
      return;
    }
    const newGenIds = [...curIds].filter((id) => !prev.has(id));
    prevGenIdSet.current = curIds;
    if (newGenIds.length > 0) {
      const raw = projectNodes(gens.filter((g) => newGenIds.includes(g.id))).nodes;
      setNewNodeIds(raw.map((n) => n.id));
    }
  }, [gens]);

  // 灵感卡持久化列表（独立于 generation 节点；位置/大小/标题/内容/来源类型都存这里）
  const [inspirationCards, setInspirationCards] = useState<InspirationCardStored[]>([]);
  const inspirationCardsRef = useRef(inspirationCards);
  inspirationCardsRef.current = inspirationCards;

  // 参考图持久化列表（文件夹拖入画布的图片节点，独立于灵感卡）
  const [referenceImages, setReferenceImages] = useState<ReferenceImageStored[]>([]);
  const referenceImagesRef = useRef(referenceImages);
  referenceImagesRef.current = referenceImages;

  useEffect(() => {
    if (!repoId) return;
    let cancelled = false;
    loadLayout(repoId, settings.outputDir).then((layout) => {
      if (cancelled) return;
      setLayoutNodes(layout.nodes);
      setLayoutEdges(layout.edges);
      setSavedViewport(layout.viewport);
      viewportRef.current = layout.viewport;
      // 清理历史残留：story 楼层删除已改为「删除对话消息」（不进黑名单），
      // 旧 canvas.json 里可能有 story- 前缀的黑名单残留，一次性剔除，避免吞掉仍存在的楼层。
      const rawDeleted = Array.isArray(layout.deletedIds) ? layout.deletedIds : [];
      const cleanDeleted = rawDeleted.filter((id) => !/^story-/.test(id));
      setDeletedIds(cleanDeleted);
      if (cleanDeleted.length !== rawDeleted.length) {
        void saveLayout(repoId, settings.outputDir, {
          nodes: layout.nodes, edges: layout.edges,
          viewport: layout.viewport,
          inspirationCards: layout.inspirationCards || [],
          referenceImages: layout.referenceImages || [],
          deletedIds: cleanDeleted,
        });
      }
      // 参考图恢复
      setReferenceImages(layout.referenceImages || []);
      referenceImagesRef.current = layout.referenceImages || [];
      // 历史「全量导入」迁移清理：只保留当前仓库绑定来源的卡（用户自建/拖放无 sourceRef 的保留）
      const rawCards = Array.isArray(layout.inspirationCards) ? layout.inspirationCards : [];
      const pruned = pruneUnboundInspirationCards(rawCards, boundCards, boundWorldbook, boundPreset);
      if (pruned.length !== rawCards.length) {
        // 落盘清理后的结果，避免刷新后未绑定卡复活
        void saveLayout(repoId, settings.outputDir, {
          nodes: layout.nodes, edges: layout.edges,
          viewport: layout.viewport, inspirationCards: pruned,
          referenceImages: layout.referenceImages || [],
          deletedIds: layout.deletedIds || [],
        });
      }
      setInspirationCards(pruned);
      inspirationCardsRef.current = pruned;
      // 素材库 → 灵感卡：只导入当前仓库绑定的角色卡/世界书/预设（未绑定则不导入）
      if (!cancelled) void importInspirationFromLibrary(true, layout.nodes);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, settings.outputDir, boundCards, boundWorldbook, boundPreset]);

  // 自动导入 reference 文件夹中新图片（对话中插入的图片天然落到 reference/，画布自动读取）
  useEffect(() => {
    if (!repoId) return;
    let cancelled = false;
    // 等 layout 加载完成再导入（避免与 restore 竞态）
    const timer = setTimeout(async () => {
      if (cancelled) return;
      try {
        const { items } = await listReferenceImages(settings.outputDir, repoId);
        if (cancelled) return;
        const existing = referenceImagesRef.current;
        const existingUrls = new Set(existing.map((r) => r.imageUrl).filter(Boolean));
        const existingFilenames = new Set(existing.map((r) => r.title).filter(Boolean));
        const newItems = items.filter((item) =>
          !existingUrls.has(item.url) && !existingFilenames.has(item.filename),
        );
        if (newItems.length === 0) return;
        // ★ 统一空位放置：参考图不再固定 (24, maxY) 网格（会压到生成结果/灵感卡）
        const REF_W = 280;
        const REF_H = 280;
        const extras: Array<{ x: number; y: number; w: number; h: number }> = [];
        const newRefs: ReferenceImageStored[] = newItems.map((item) => {
          const p = findFreeSpot(REF_W, REF_H, extras);
          extras.push({ x: p.x, y: p.y, w: REF_W, h: REF_H });
          return {
            id: `ref-${crypto.randomUUID().slice(0, 8)}`,
            title: item.filename,
            imageUrl: item.url,
            x: p.x, y: p.y, w: REF_W, h: REF_H,
          };
        });
        if (cancelled) return;
        const merged = [...existing, ...newRefs];
        setReferenceImages(merged);
        referenceImagesRef.current = merged;
        persistNow({ refs: merged });
      } catch { /* 后端不可用静默跳过 */ }
    }, 500);
    return () => { cancelled = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, settings.outputDir]);

  // input 节点事件（占位卡）：draft=双击新建待输入；generating=已提交生成中（由对话流 streamingId 投影）
  type PendingInput = { id: string; prompt: string; x: number; y: number; status: "draft" | "generating" };
  const [pendingInputs, setPendingInputs] = useState<PendingInput[]>([]);

  // 工作流工具卡：/w 选模板后，ChatView 派发 laf-canvas-workflow-tool，画布创建工具型节点
  // globalPendingToolCreates 由 ChatView（始终挂载）维护，Canvas 挂载时消费并清空。
  // 注意：不能在 useState initializer 里 splice —— React.StrictMode 下 render 双跑，
  // 第一次 splice 清空数组、第二次拿到空数组并覆盖 state → 节点永远不出现。
  // 改为挂载 useEffect 消费 + 函数式 set：StrictMode 双跑时第二次 splice 结果为空数组，
  // [...prev, ...[]] === prev，幂等无害。
  const [pendingToolCreates, setPendingToolCreates] = useState<Array<{
    id: string; templateId: string; templateName: string; estimatedNodeCount: number;
  }>>([]);
  const pendingToolCreatesRef = useRef(pendingToolCreates);
  pendingToolCreatesRef.current = pendingToolCreates;
  useEffect(() => {
    canvasBridge.canvasMounted = true;
    if (globalPendingToolCreates.length > 0) {
      const drained = globalPendingToolCreates.splice(0, globalPendingToolCreates.length);
      // 5a 去重：同模板的卡已存在（rfNodes 或本地队列）→ 丢弃重复派发
      const existing = new Set(
        rfNodesRef.current
          .filter((n) => n.data?.node?.type === "workflow-tool" && n.data.node.templateId)
          .map((n) => n.data.node.templateId),
      );
      const fresh = drained.filter((p) => !existing.has(p.templateId)
        && !pendingToolCreatesRef.current.some((x) => x.templateId === p.templateId));
      if (fresh.length > 0) setPendingToolCreates((prev) => [...prev, ...fresh]);
    }
    return () => { canvasBridge.canvasMounted = false; };
  }, []);
  useEffect(() => {
    const onTool = (e: Event) => {
      const detail = (e as CustomEvent).detail as {
        templateId?: string; templateName?: string; estimatedNodeCount?: number;
      } | undefined;
      const templateId = (detail?.templateId || "").trim();
      const templateName = (detail?.templateName || "").trim() || "工作流模板";
      if (!templateId) return;
      // 5a 唯一性：同模板卡已存在（画布节点或本地队列）→ 复用，不新建
      const exists = rfNodesRef.current.some((n) =>
        n.data?.node?.type === "workflow-tool" && n.data.node.templateId === templateId,
      ) || pendingToolCreatesRef.current.some((p) => p.templateId === templateId);
      if (exists) {
        showToast(`工作流「${templateName}」已在画布上（同模板只保留一张卡片）`);
        return;
      }
      const cnt = Number(detail?.estimatedNodeCount) || 0;
      // 画布已挂载：ChatView 兜底监听已按 canvasMounted 跳过写入 global，这里直接进本地 state
      // ★ id 用稳定派生 wftool-<templateId>（与对话消息投影同 id）：事件与投影去重后不会重复建卡，
      //   且布局位置以稳定 id 持久化 → 重启后投影节点能对上旧位置。
      setPendingToolCreates((prev) => [...prev, {
        id: `wftool-${templateId}`,
        templateId, templateName, estimatedNodeCount: cnt,
      }]);
    };
    window.addEventListener("laf-canvas-workflow-tool", onTool);
    return () => window.removeEventListener("laf-canvas-workflow-tool", onTool);
  }, []);
  // 新增工具卡时标记为"新节点"（画布中心按钮高亮 + 跳转）
  const prevToolCountRef = useRef(0);
  useEffect(() => {
    const ids = pendingToolCreates.map((t) => t.id);
    if (ids.length > prevToolCountRef.current) {
      setNewNodeIds(ids);
    }
    prevToolCountRef.current = ids.length;
  }, [pendingToolCreates]);
  // 工具卡创建即持久化（templateId 进 layoutNodes）：未编辑过的卡切画布再回也能恢复，
  // 否则只有 pendingToolCreates 一次性事件驱动，消费完切走再回卡就消失。
  useEffect(() => {
    if (pendingToolCreates.length === 0) return;
    setLayoutNodes((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const t of pendingToolCreates) {
        if (!prev[t.id]?.templateId) {
          const base = prev[t.id] || { x: 24, y: 24, w: CARD_W, h: 180 };
          next[t.id] = { ...base, templateId: t.templateId, templateName: t.templateName };
          changed = true;
        }
      }
      if (!changed) return prev;
      persistNow({ nodes: next });
      return next;
    });
  }, [pendingToolCreates]);

  // 双击工具卡弹窗（workflow-tool 走双栏编辑器，其他节点走详情 modal）
  const [toolModalNode, setToolModalNode] = useState<{ node: CanvasNode; autoConfirm: boolean } | null>(null);
  const [inputSubmitting, setInputSubmitting] = useState(false);
  // 世界书弹窗：双击 worldbook-entry 灵感卡时打开（左侧内容+右侧导航）
  const [wbRepoLoc, setWbRepoLoc] = useState<RepoWorldbookLoc | null>(null);
  const [wbPopupTitle, setWbPopupTitle] = useState("");
  // 角色卡弹窗：双击 character 灵感卡（照搬资产库预览、去掉新建作品、只写画布本地）
  const [charModal, setCharModal] = useState<{ name: string; cardId: string; content: string } | null>(null);
  // 预设弹窗：双击 preset 灵感卡 → 打开偏置预设
  const [presetModalOpen, setPresetModalOpen] = useState(false);

  // 投影（过滤删除黑名单：已删除节点不得复活——同会话快照删除语义）
  const deletedIdSet = useMemo(() => new Set(deletedIds), [deletedIds]);
  const { nodes: rawNodes, byGroup } = useMemo(() => {
    const { nodes, byGroup: bg } = projectNodes(gens);
    const del = deletedIdSet;
    if (del.size === 0) return { nodes, byGroup: bg };
    const kept = nodes.filter((n) => !del.has(n.id));
    const byG = new Map<string, GenLike[]>();
    for (const n of kept) {
      const g = bg.get(n.id);
      if (g) byG.set(n.id, g);
    }
    return { nodes: kept, byGroup: byG };
  }, [gens, deletedIdSet]);
  // ReactFlow nodes（合并布局坐标 + input 节点 + naturalSize）
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node<CardNodeData>>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const rfNodesRef = useRef(rfNodes);
  rfNodesRef.current = rfNodes;

  // ===== 新节点统一锚点网格放置（用户拍板规则）=====
  // 1) 原位替换：生成内容节点落在「生成中」占位节点的位置（垂直居中对齐）。
  // 2) 以最新节点为锚点小网格展开：任何新节点（含生成中占位）都从最新已放置节点向右/向下排。
  // 3) 范围不重叠：落点与已有节点包围盒重叠时逐格向右、换行向下避让。

  /** 占位节点（生成中）位置 → 内容节点原位替换锚点 */
  const pendingAnchorRef = useRef<PlacedRect | null>(null);

  /** 收集画布上所有已占用包围盒（绝对坐标），供重叠避让 */
  const collectOccupiedRects = useCallback((extra?: PlacedRect[]): PlacedRect[] => {
    const dim = (v: unknown, fb: number) => {
      const n = typeof v === "number" ? v : typeof v === "string" ? parseFloat(v) : NaN;
      return Number.isFinite(n) ? n : fb;
    };
    const rects: PlacedRect[] = [];
    const byId = new Map(rfNodesRef.current.map((n) => [n.id, n]));
    for (const n of rfNodesRef.current) {
      if (n.parentId) continue; // 子节点已含在父组包围盒内
      const p = nodeAbsolutePosition(n, byId);
      const lyt = layoutNodesRef.current[n.id];
      rects.push({
        x: p.x, y: p.y,
        w: lyt?.w ?? dim(n.style?.width, n.measured?.width ?? CARD_W),
        h: lyt?.h ?? dim(n.style?.height, n.measured?.height ?? 200),
      });
    }
    for (const c of inspirationCardsRef.current) rects.push({ x: c.x, y: c.y, w: c.w, h: c.h });
    for (const r of referenceImagesRef.current) rects.push({ x: r.x, y: r.y, w: r.w, h: r.h });
    if (extra) for (const r of extra) rects.push(r);
    return rects;
  }, []);

  /** 最新「已放置」内容节点锚点（无则 null → 从原点开始） */
  const latestContentAnchor = useCallback((): PlacedRect | null => {
    const sorted = [...gensRef.current].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    for (const g of sorted) {
      const type = generationNodeType(g);
      const id = `${type === "image-group" ? "img" : type}-${g.id}`;
      const lyt = layoutNodesRef.current[id];
      if (lyt) return { x: lyt.x, y: lyt.y, w: lyt.w, h: lyt.h };
    }
    return null;
  }, []);

  /** 占位节点（生成中）放置：以最新内容节点为锚点、向右小网格展开（生成中节点也遵从此原则） */
  const placePlaceholder = useCallback((w: number, h: number, extra?: PlacedRect[]) => {
    const anchor = latestContentAnchor();
    const occupied = collectOccupiedRects(extra);
    return placeNewNodes([{ w, h }], anchor, occupied, { replace: false, cols: 3 })[0];
  }, [collectOccupiedRects, latestContentAnchor]);

  // ★ 生成中占位节点位置缓存：画布输入栏与对话模式同源发送（useChatSession.send），
  //   占位节点由「对话流中正在生成的 assistant 消息」投影而来（streamingId）。
  //   位置首次出现时用 placePlaceholder 计算并固定（ref 缓存），避免每次重投影漂移；
  //   表面内容（主管委派过程行 / 提示词）实时取自 streamingId 对应的消息。
  const streamingPosRef = useRef<{ id: string; x: number; y: number } | null>(null);
  // 渲染期标记：本轮生成结束瞬间，占位位置是否已被剧情楼层接走（storyNodes 投影接位时置 true）。
  // 若已被接走，effect 不再把位置转存给 pendingAnchorRef，避免陈旧锚点污染后续内容节点放置。
  const placeholderConsumedByStoryRef = useRef(false);
  useEffect(() => {
    if (streamingId) {
      if (!streamingPosRef.current || streamingPosRef.current.id !== streamingId) {
        const pos = placePlaceholder(CARD_W, 120);
        streamingPosRef.current = { id: streamingId, x: pos.x, y: pos.y };
      }
    } else {
      // ★ 生成结束：占位位置转存给内容节点原位替换（剧情楼层则在渲染期直接读 streamingPosRef，
      //   因为剧情楼层投影先于本 effect 执行，streamingPosRef 此刻仍持有位置）。
      if (streamingPosRef.current && !placeholderConsumedByStoryRef.current) {
        pendingAnchorRef.current = {
          x: streamingPosRef.current.x,
          y: streamingPosRef.current.y,
          w: CARD_W,
          h: 120,
        };
      }
      placeholderConsumedByStoryRef.current = false;
      streamingPosRef.current = null;
    }
  }, [streamingId, placePlaceholder]);

  /** 新内容节点放置（纯计算，不落盘）：锚点=待替换占位（原位）/ 最新内容节点 */
  const contentPlacement = useMemo(() => {
    const newNodes = rawNodes.filter((n) => !layoutNodes[n.id]);
    if (newNodes.length === 0) return null;
    const pending = pendingAnchorRef.current;
    const hasPending = !!pending;
    const anchor = hasPending ? pending : latestContentAnchor();
    const occupied = collectOccupiedRects();
    const sizes = newNodes.map((n) => ({ w: n.w, h: n.h }));
    const positions = placeNewNodes(sizes, anchor, occupied, { replace: hasPending, cols: 3 });
    const map: Record<string, { x: number; y: number }> = {};
    const dims: Record<string, { w: number; h: number }> = {};
    newNodes.forEach((n, i) => {
      map[n.id] = positions[i];
      dims[n.id] = { w: n.w, h: n.h };
    });
    return { map, dims, ids: newNodes.map((n) => n.id), hasPending };
  }, [rawNodes, layoutNodes, collectOccupiedRects, latestContentAnchor]);

  // ===== 新节点统一空位放置（灵感卡/参考图等非生成内容沿用）：不堆叠 =====
  // 旧逻辑各创建路径用固定坐标（(24,24) 对角级联 / 画布中心+60 / 最下方网格），
  // 预设卡、工作流模板卡、输入卡经常落在第一批生成结果网格上。
  // 现统一收集全部已有节点包围盒，从上到下、从左到右扫描第一个放得下新节点的空位。
  const findFreeSpot = useCallback((
    w: number, h: number,
    /** 同批次尚未进入 state/ref 的新节点包围盒（互相避让） */
    extra?: Array<{ x: number; y: number; w: number; h: number }>,
  ): { x: number; y: number } => {
    const rects = collectOccupiedRects(extra);
    const GAP = 32;
    const M = 24;
    if (rects.length === 0) return { x: M, y: M };
    const colW = Math.max(240, w + GAP);
    const intersects = (x: number, y: number) =>
      rects.some((r) => x < r.x + r.w + GAP && x + w + GAP > r.x && y < r.y + r.h + GAP && y + h + GAP > r.y);
    const maxY = Math.max(...rects.map((r) => r.y + r.h));
    // 候选行：顶边 + 每个已有节点的底边下方；逐行从左往右找空位
    const ys = [M, ...rects.map((r) => r.y + r.h + GAP)]
      .filter((y) => y <= maxY + GAP)
      .sort((a, b) => a - b);
    for (const y of ys) {
      for (let x = M; x <= M + 6 * colW; x += colW) {
        if (!intersects(x, y)) return { x, y };
      }
    }
    // 全部行都放不下 → 整体下方
    return { x: M, y: maxY + GAP + 24 };
  }, [collectOccupiedRects]);

  // ===== 工作流运转任务：画布「生成中」占位节点 + 对话框下方进度条 =====
  // 运转工作流 → 画布创建生成中节点（wfRuns 投影）；运转完毕 laf-canvas-wf-done 清除占位，
  // 生成内容节点由 laf-generation-saved 刷新替换。进度经 ComfyUI /ws 直连实时更新。
  type WfRun = {
    id: string; toolNodeId: string; promptId: string; templateName: string;
    progress: number | null; node?: string; x: number; y: number;
    /** 提交时的 API prompt 图（{id:{class_type,inputs}}）：占位节点显示真实提示词/节点名用 */
    graph?: unknown;
  };
  const [wfRuns, setWfRuns] = useState<WfRun[]>([]);
  const wfRunsRef = useRef(wfRuns);
  wfRunsRef.current = wfRuns;

  // onGenerated 由上层每次渲染新建（ChatView 传的是内联箭头）——用 ref 持有稳定引用，
  // 避免 runWorkflowTask 因回调身份变化被频繁重建（重建会连带触发投影 useEffect 重跑）。
  const onGeneratedRef = useRef(onGenerated);
  onGeneratedRef.current = onGenerated;

  // 运转执行（提交 → 订阅进度 → 轮询拿图 → 入库 → 派发完成）：
  // runId 关联画布占位节点与对话框进度条；capturedOverride 供编辑器弹窗同拍抓取结果直传。
  const runWorkflowTask = useCallback((nn: CanvasNode, runId: string, capturedOverride?: unknown) => {
    const captured = capturedOverride ?? nn.wfCaptured;
    if (!captured || !settings.comfyuiUrl) {
      showToast("还没抓到画布内容，请先双击打开编辑器点「选择完毕」", "error");
      // 清除「生成中」占位节点 + 对话框进度条（run 事件已派发，失败也要收工）
      try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runId } })); } catch { /* ignore */ }
      return;
    }
    (async () => {
      let stopProg: (() => void) | null = null;
      let keepPlaceholder = false;   // still_running 时保留「生成中」占位节点（ComfyUI 后台仍在跑）
      try {
        const r = await submitGraph(captured, settings.comfyuiUrl);
        showToast(`已提交到 ComfyUI（prompt_id: ${r.prompt_id}，${r.node_count} 个节点），正在运转工作流…`);
        // 占位节点进入「已提交」态 + 对话框进度条出现（graph 随 run 事件带给画布，占位节点显示真实提示词）
        window.dispatchEvent(new CustomEvent("laf-canvas-wf-run-graph", {
          detail: { runId, graph: captured },
        }));
        // 直连 ComfyUI /ws 订阅本 prompt 的步进进度（占位节点 + 对话框进度条共用）
        // 节点名对齐对话模式：class_type (#id)，事件同时携带原始 id（node）与标签（nodeLabel）
        const nodeLabel = (nid: string): string => {
          try {
            const g = captured as Record<string, { class_type?: string }>;
            const c = g?.[nid]?.class_type;
            return c ? `${c} (#${nid})` : `节点 #${nid}`;
          } catch { return `节点 #${nid}`; }
        };
        stopProg = subscribeProgress(settings.comfyuiUrl || "", r.prompt_id || "", {
          onProgress: (pct, pr) => window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
            detail: { taskId: runId, promptId: r.prompt_id || "", progress: pct, node: pr.node || "", nodeLabel: pr.node ? nodeLabel(pr.node) : "", templateName: nn.templateName || "" },
          })),
          onNode: (nid) => window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
            detail: { taskId: runId, promptId: r.prompt_id || "", node: nid, nodeLabel: nodeLabel(nid), templateName: nn.templateName || "" },
          })),
        });
        // 轮询拿图：复用 pollSchedule 合同（图片 20 分钟 / 视频 60 分钟），不再硬编码 240 秒。
        const outcome = await pollWorkflowResult(r.prompt_id || "", settings.comfyuiUrl || "", "image");
        if (outcome.kind === "failed") {
          showToast(`运转失败：${outcome.error}`, "error");
          return;
        }
        if (outcome.kind === "still_running") {
          // ComfyUI 仍在后台运转：不擅自删除「生成中」节点，保留占位并告知用户。
          keepPlaceholder = true;
          showToast("运转仍在进行中（已超过预期时长）。画布会保留「生成中」节点，请稍后在 ComfyUI 控制台查看或重新运转。");
          return; // 不派发 done，保留占位节点
        }
        const result = outcome.result;
        if ((!result.images || result.images.length === 0)
            && (!result.videos || result.videos.length === 0)
            && (!result.audios || result.audios.length === 0)) {
          showToast("运转完成但未拿到图片/视频/音频（可能输出节点未正确配置），请到 ComfyUI 控制台查看。", "error");
          return;
        }
        // 入库到 generation_store → 派发 laf-generation-saved 让画布用生成内容节点替换占位
        // 元数据与对话模式同源：真实提示词 + 主模型 + LoRA（详情面板展示用）
        const meta = workflowGenMetadata(nn.templateName || "", captured, nn.wfDraft);
        const embed = resolvedEmbedModel(settings);
        const chat = activeChatModel(settings);
        try {
          await finalizeGeneration({
            threadId: repoId,
            repoId,
            promptId: r.prompt_id || "",
            prompt: meta.prompt || nn.templateName || "工作流生成",
            images: result.images,
            videos: result.videos || [],
            audios: result.audios || [],
            outputDir: settings.outputDir,
            comfyuiUrl: settings.comfyuiUrl,
            embed: { baseUrl: embed.baseUrl || chat.baseUrl, apiKey: embed.apiKey || chat.apiKey, modelName: embed.modelName },
            chat: { baseUrl: chat.baseUrl, apiKey: chat.apiKey, modelName: chat.modelName },
            regeneration: undefined,
            target: undefined,
            templateName: meta.templateName,
            modelName: meta.modelName,
            loraNames: meta.loraNames,
          });
          showToast(`运转完成，${result.images.length} 张图${result.videos.length ? `、${result.videos.length} 个视频` : ""}${result.audios.length ? `、${result.audios.length} 个音频` : ""}已入库。`);
        } catch (e) {
          showToast(`运转完成（入库失败：${(e as Error).message}），稍后会自动入库。`);
        }
        try { window.dispatchEvent(new CustomEvent("laf-generation-saved")); } catch { /* ignore */ }
        // ★ 运转完成即回读对话快照（与 WorkflowToolModal.handleRun / ChatView.sendCanvas 对齐）：
        //   finalize 已把新产出作为助手消息落盘，刷新对话消息后 conversationUrls 实时
        //   包含新图 → 画布投影无需手动刷新即可出现生成内容节点。
        onGeneratedRef.current?.();
      } catch (e) {
        showToast(`运转失败：${(e as Error).message}`, "error");
      } finally {
        stopProg?.();
        // 清除画布占位节点 + 对话框进度条（still_running 时保留占位，不派发 done）
        if (!keepPlaceholder) {
          try { window.dispatchEvent(new CustomEvent("laf-canvas-wf-done", { detail: { taskId: runId } })); } catch { /* ignore */ }
        }
      }
    })();
  }, [settings, repoId, showToast]);

  // 运转任务事件：创建占位节点（空位放置）/ 进度更新 / 完成清除
  useEffect(() => {
    const onRun = (e: Event) => {
      const d = (e as CustomEvent).detail as { runId?: string; toolNodeId?: string; templateName?: string } | undefined;
      if (!d?.runId) return;
      // 以最新内容节点为锚点展开（生成中占位也遵从此原则）
      const pos = placePlaceholder(CARD_W, 427, wfRunsRef.current.map((r) => ({ x: r.x, y: r.y, w: CARD_W, h: 427 })));
      setWfRuns((prev) => (prev.some((r) => r.id === d.runId) ? prev : [...prev, {
        id: d.runId!, toolNodeId: d.toolNodeId || "", templateName: d.templateName || "工作流",
        promptId: "", progress: null, x: pos.x, y: pos.y,
      }]));
    };
    const onProg = (e: Event) => {
      const d = (e as CustomEvent).detail as { taskId?: string; promptId?: string; progress?: number | null; node?: string; templateName?: string } | undefined;
      if (!d?.taskId) return;
      // upsert：编辑器弹窗路径只发 progress 事件（不发 run），占位节点同样要出现
      setWfRuns((prev) => {
        const exists = prev.some((r) => r.id === d.taskId);
        if (exists) {
          return prev.map((r) => (r.id === d.taskId ? {
            ...r,
            promptId: d.promptId || r.promptId,
            progress: d.progress !== undefined ? d.progress : r.progress,
            node: d.node !== undefined ? d.node : r.node,
            templateName: d.templateName || r.templateName,
          } : r));
        }
        const pos = placePlaceholder(CARD_W, 427, prev.map((r) => ({ x: r.x, y: r.y, w: CARD_W, h: 427 })));
        return [...prev, {
          id: d.taskId!, toolNodeId: "", templateName: d.templateName || "工作流",
          promptId: d.promptId || "", progress: d.progress !== undefined ? d.progress : null,
          node: d.node || undefined, x: pos.x, y: pos.y,
        }];
      });
    };
    const onDone = (e: Event) => {
      const d = (e as CustomEvent).detail as { taskId?: string } | undefined;
      if (!d?.taskId) return;
      // ★ 原位替换：占位节点清除时记录其位置，内容节点到达后落在原位（垂直居中对齐）
      const run = wfRunsRef.current.find((r) => r.id === d.taskId);
      if (run) {
        pendingAnchorRef.current = { x: run.x, y: run.y, w: CARD_W, h: 427 };
      }
      setWfRuns((prev) => (prev.some((r) => r.id === d.taskId) ? prev.filter((r) => r.id !== d.taskId) : prev));
    };
    // 提交后携带 API prompt 图：占位节点据此显示真实提示词/节点名（对齐对话模式）
    const onRunGraph = (e: Event) => {
      const d = (e as CustomEvent).detail as { runId?: string; graph?: unknown } | undefined;
      if (!d?.runId) return;
      setWfRuns((prev) => prev.map((r) => (r.id === d.runId ? { ...r, graph: d.graph } : r)));
    };
    window.addEventListener("laf-canvas-wf-run", onRun);
    window.addEventListener("laf-canvas-wf-run-graph", onRunGraph);
    window.addEventListener("laf-canvas-wf-progress", onProg);
    window.addEventListener("laf-canvas-wf-done", onDone);
    return () => {
      window.removeEventListener("laf-canvas-wf-run", onRun);
      window.removeEventListener("laf-canvas-wf-run-graph", onRunGraph);
      window.removeEventListener("laf-canvas-wf-progress", onProg);
      window.removeEventListener("laf-canvas-wf-done", onDone);
    };
  }, [placePlaceholder]);

  // 撤销/重做栈：记录完整画布状态快照（nodes / edges / layout / 灵感卡 / 删除黑名单）
  // ★ deletedIds 必须进快照：否则删除节点 → Ctrl+Z 撤销 → 节点从 rfNodes 恢复，
  //   但投影 useEffect 仍按 deletedIds 过滤 → 节点被「吞掉」且因已持久化永远回不来。
  type UndoSnapshot = {
    nodes: typeof rfNodes;
    edges: typeof rfEdges;
    layout: typeof layoutNodes;
    cards: typeof inspirationCards;
    refs: typeof referenceImages;
    deletedIds: string[];
  };
  const undoStackRef = useRef(createUndoStack<UndoSnapshot>({
    nodes: rfNodes, edges: rfEdges, layout: layoutNodes, cards: inspirationCards, refs: referenceImages, deletedIds: deletedIdsRef.current,
  }));
  const pushUndo = useCallback(() => {
    undoStackRef.current = pushSnapshot(undoStackRef.current, {
      nodes: rfNodesRef.current,
      // 快照不含自动时序线（派生数据，投影 effect 会重建）
      edges: rfEdges.filter((e) => !e.id.startsWith("storyflow-")),
      layout: layoutNodesRef.current,
      cards: inspirationCardsRef.current,
      refs: referenceImagesRef.current,
      deletedIds: deletedIdsRef.current,
    });
  }, [rfEdges]);
  // 实时吸附辅助线：拖动中中心点对齐（滞回，防闪烁）；overlay 由 ReactFlow 内部子组件渲染
  const [guides, setGuides] = useState<Guide>({});
  const snapAxisRef = useRef<{ x: SnapAxisState; y: SnapAxisState }>(
    { x: { active: false, pos: 0 }, y: { active: false, pos: 0 } },
  );
  // ★ DOM 直画辅助线（用户实测可见链路：曾看到屏幕中央红蓝十字）。
  //   React 渲染的 GuidesOverlay 历轮修遍仍不可见（"边缘有线中心消失"= 线被节点 DOM 遮挡），
  //   恢复 DOM 直画：appendChild 到 stageWrap 顶层 + z-index 2147483000，100% 不被节点遮挡。
  //   snap=false：半透明细虚线（接近提示）；snap=true：蓝色虚线 + 蓝光晕（吸附贴住，用户要求虚线而非实线）。
  const guideLinesRef = useRef<{ v: HTMLDivElement; h: HTMLDivElement } | null>(null);
  const ensureGuideLines = useCallback(() => {
    const stage = stageWrapRef.current;
    if (!stage) return null;
    if (!guideLinesRef.current) {
      const mk = (vertical: boolean): HTMLDivElement => {
        const el = document.createElement("div");
        el.style.cssText = [
          "position:absolute", "pointer-events:none", "z-index:2147483000",
          "display:none",
          ...(vertical
            ? ["top:0", "width:0", "height:100%", "border-left:1px dashed #60a5fa"]
            : ["left:0", "height:0", "width:100%", "border-top:1px dashed #60a5fa"]),
        ].join(";");
        stage.appendChild(el);
        return el;
      };
      guideLinesRef.current = { v: mk(true), h: mk(false) };
    }
    return guideLinesRef.current;
  }, []);
  const updateGuideLines = useCallback((
    x: number | null, y: number | null,
    snapX: boolean, snapY: boolean,
    scale: number, vx: number, vy: number,
  ) => {
    const lines = ensureGuideLines();
    if (!lines) return;
    if (x !== null) {
      lines.v.style.display = "block";
      lines.v.style.left = `${x * scale + vx}px`;
      // 吸附 = 蓝色虚线（dashed）+ 蓝光晕；接近提示 = 更细的浅蓝虚线（1px）
      lines.v.style.borderLeft = snapX ? "2px dashed #2563eb" : "1px dashed #60a5fa";
      lines.v.style.opacity = snapX ? "1" : "0.65";
      lines.v.style.boxShadow = snapX ? "0 0 6px rgba(37,99,235,0.85), 0 0 2px rgba(37,99,235,1)" : "none";
    } else lines.v.style.display = "none";
    if (y !== null) {
      lines.h.style.display = "block";
      lines.h.style.top = `${y * scale + vy}px`;
      lines.h.style.borderTop = snapY ? "2px dashed #2563eb" : "1px dashed #60a5fa";
      lines.h.style.opacity = snapY ? "1" : "0.65";
      lines.h.style.boxShadow = snapY ? "0 0 6px rgba(37,99,235,0.85), 0 0 2px rgba(37,99,235,1)" : "none";
    } else lines.h.style.display = "none";
  }, [ensureGuideLines]);
  // 卸载时移除 DOM 线
  useEffect(() => () => {
    const lines = guideLinesRef.current;
    if (lines) {
      lines.v.remove();
      lines.h.remove();
      guideLinesRef.current = null;
    }
  }, []);

  useEffect(() => {
    const projected = rawNodes.map((n) => {
      // 已持久化布局 → 用持久化坐标；新节点 → 用锚点网格计算出的坐标（contentPlacement）
      const lyt = layoutNodes[n.id] || (contentPlacement && contentPlacement.map[n.id]
        ? { x: contentPlacement.map[n.id].x, y: contentPlacement.map[n.id].y, w: n.w, h: n.h }
        : null);
      const gensFor = byGroup.get(n.id) || [];
      return {
        id: n.id,
        type: "card" as const,
        // 组内子节点：position 存相对组坐标 + parentId（刷新后还原组关系）
        position: lyt ? { x: lyt.x, y: lyt.y } : { x: n.x, y: n.y },
        ...(lyt?.parentId ? { parentId: lyt.parentId, extent: "parent" as const } : {}),
        // 用户手动 resize 过（layout.custom）→ 显式尺寸跟随 w/h（锁定），否则高度自适应
        ...(lyt?.custom && typeof lyt.w === "number" && typeof lyt.h === "number"
          ? { style: { width: lyt.w, height: lyt.h } }
          : {}),
        selected: false,
        data: {
          node: n,
          gens: gensFor,
          imageUrls: gensFor.map((g) => g.image_url),
          prompt: n.type === "input" ? (n.prompt || "") : (gensFor[0]?.prompt || ""),
          customLabel: lyt?.label || "",
          customSize: !!lyt?.custom,
          isSel: false,
          naturalSize: naturalSizesRef.current[gensFor[0]?.image_url || ""],
          onSelect: () => {},
          onOpen: (nn: CanvasNode) => {
            // 输入节点 → 新建灵感卡（不是生成内容）
            if (nn.type === "input") {
              setInspirationEditId("new");
              setInspirationEditKind("preset");
              setInspirationEditTitle(nn.prompt || "");
              setInspirationEditContent(nn.prompt || "");
              return;
            }
            setDetailNode(nn);
          },
          onResize: (id: string, w: number, h: number) => {
            // 拉伸中：实时更新该节点 style（ReactFlow 官方模式，让 wrapper 跟随尺寸）。
            // 直接 setRfNodes 只改单个节点，不触发投影 useEffect 全量重建 → 无闪烁。
            // onResizeEnd 才落 layoutNodes（customSize 持久化）。
            setRfNodes((prev) => prev.map((n) => (n.id === id
              ? { ...n, style: { ...n.style, width: w, height: h }, data: { ...n.data, customSize: true } }
              : n)));
          },
          onResizeEnd: (id: string, w: number, h: number) => {
            const base = layoutNodesRef.current[id] || { x: 0, y: 0, w: CARD_W, h: 100 };
            const next = { ...layoutNodesRef.current, [id]: { ...base, w, h, custom: true } };
            setLayoutNodes(next);
            persistNow({ nodes: next });
          },
          onImgLoaded: (url: string, w: number, h: number) => {
            const cur = naturalSizesRef.current[url];
            if (cur && cur.w === w && cur.h === h) return;
            naturalSizesRef.current = { ...naturalSizesRef.current, [url]: { w, h } };
            // 直接更新目标节点的 naturalSize，不触发全量 useEffect 重建
            setRfNodes((prev) => prev.map((n) => {
              if (n.data.gens?.[0]?.image_url === url) {
                return { ...n, data: { ...n.data, naturalSize: { w, h } } };
              }
              return n;
            }));
          },
          onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
            const rect = stageWrapRef.current?.getBoundingClientRect();
            openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
          },
        },
      };
    });
    // ★ 生成中占位节点（对话流 streamingId 投影）：画布输入栏与对话模式同源发送，
    //   占位节点表面显示调度主管委派过程行（traceText）+ 生成中；生命周期跟随对话流，
    //   切走再切回不丢失，生成结束即被真实产出节点（或剧情楼层）替换。
    const streamingMsg = streamingId ? messages.find((m) => m.id === streamingId) : undefined;
    const streamingPrompt = (() => {
      if (!streamingId) return "";
      const idx = messages.findIndex((m) => m.id === streamingId);
      for (let i = idx - 1; i >= 0; i--) if (messages[i].role === "user") return messages[i].text;
      return "";
    })();
    const streamingPlaceholder: Node<CardNodeData>[] = (streamingId && streamingPosRef.current?.id === streamingId)
      ? [{
          id: `gen-${streamingId}`,
          type: "card" as const,
          position: { x: streamingPosRef.current!.x, y: streamingPosRef.current!.y },
          data: {
            node: {
              id: `gen-${streamingId}`, type: "input" as const,
              x: streamingPosRef.current!.x, y: streamingPosRef.current!.y, w: CARD_W, h: 120,
              generationIds: [], input: true, prompt: streamingPrompt, groupKey: `streaming-${streamingId}`,
              inputStatus: "generating" as const, traceText: streamingMsg?.text || "",
            },
            gens: [], imageUrls: [], prompt: streamingPrompt, isSel: false, naturalSize: undefined,
            onSelect: () => {},
            onOpen: () => {},
            onResize: () => {}, onImgLoaded: () => {},
            onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
              const rect = stageWrapRef.current?.getBoundingClientRect();
              openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
            },
          },
        }]
      : [];
    const draftInputs: Node<CardNodeData>[] = pendingInputs.map((p, i) => ({
      id: p.id,
      type: "card",
      position: { x: p.x, y: p.y },
      data: {
        node: {
          id: p.id, type: "input", x: p.x, y: p.y, w: CARD_W, h: 120,
          generationIds: [], input: true, prompt: p.prompt, groupKey: `input-${i}`,
          inputStatus: p.status,
        },
        gens: [], imageUrls: [], prompt: p.prompt, isSel: false, naturalSize: undefined,
        onSelect: () => {},
        // draft 卡双击 → 新建灵感卡（不是生成内容）
        onOpen: (nn: CanvasNode) => {
          if (nn.inputStatus === "draft") {
            setInspirationEditId("new");
            setInspirationEditKind("preset");
            setInspirationEditTitle(nn.prompt && nn.prompt !== "新输入节点" ? nn.prompt : "");
            setInspirationEditContent(nn.prompt && nn.prompt !== "新输入节点" ? nn.prompt : "");
          }
        },
        onResize: () => {}, onImgLoaded: () => {},
        onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
          const rect = stageWrapRef.current?.getBoundingClientRect();
          openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
        },
      },
    }));
    const inputs: Node<CardNodeData>[] = [...streamingPlaceholder, ...draftInputs];
    // 工作流工具卡（/w 选模板后由对话消息实时投影，与生成内容节点同机制）
    // ★ 主源 = 对话里的 workflow 消息投影（重启后对话历史恢复 → 节点自动出现，
    //   不依赖一次性事件或 canvas.json 持久化；同模板只保留一张，用户拍板规则）。
    //   辅助源 = 事件待创建 + layoutNodes 已持久化工具卡（去重后补充）。
    const toolDefaultH = (cnt: number) => Math.max(180, 120 + (cnt || 0) * 70);
    // 同批次新工具卡空位避让（在 toolCards map 内逐项累积）
    const newToolExtrasRef: Array<{ x: number; y: number; w: number; h: number }> = [];
    // 合并三来源，按 templateId 去重（投影优先，保证 id 稳定可恢复布局）
    const toolSources: Array<{
      id: string; templateId: string; templateName: string; estimatedNodeCount: number;
      wfDraft?: unknown; wfCaptured?: unknown; wfConfirmed?: boolean;
    }> = [];
    const seenTemplate = new Set<string>();
    // ① 对话消息投影（主源：重启可靠 + 同模板去重）
    for (const p of projectWorkflowTools(messages)) {
      if (seenTemplate.has(p.templateId)) continue;
      if (deletedIdSet.has(p.id)) continue; // 已删除不复活
      seenTemplate.add(p.templateId);
      toolSources.push({
        id: p.id,
        templateId: p.templateId,
        templateName: p.templateName || "工作流模板",
        estimatedNodeCount: (layoutNodes[p.id]?.wfExposedIds?.length || 0),
        wfDraft: p.wfDraft,
        wfCaptured: p.wfCaptured,
        wfConfirmed: p.wfConfirmed,
      });
    }
    // ② 事件待创建（刚 /w 消息还没进 messages 时兜底）
    for (const t of pendingToolCreates) {
      if (seenTemplate.has(t.templateId)) continue;
      seenTemplate.add(t.templateId);
      toolSources.push(t);
    }
    // ③ layoutNodes 已持久化工具卡（旧数据/切画布再回补充）
    for (const [pid, plyt] of Object.entries(layoutNodes)) {
      if (!plyt.templateId) continue;
      if (seenTemplate.has(plyt.templateId)) continue;
      seenTemplate.add(plyt.templateId);
      toolSources.push({
        id: pid,
        templateId: plyt.templateId,
        templateName: plyt.templateName || "工作流模板",
        estimatedNodeCount: (plyt.wfExposedIds?.length || 0),
      });
    }
    const toolCards: Node<CardNodeData>[] = toolSources.map((t) => {
      const tlyt = layoutNodes[t.id];
      // 初始节点数：优先真实 exposed_ids（编辑器选择完毕回流），否则用 /w 派发的预估数
      // （模板 node_order 长度）——避免卡片创建时显示「0 节点」（节点总是不存在表象）。
      const exposedCount = tlyt?.wfExposedIds?.length ?? 0;
      const estCount = t.estimatedNodeCount || 0;
      const nodeCount = Math.max(exposedCount, estCount);
      // 工具卡高度：默认 auto（随内容撑开——未选择时节点预览区/引导文字、已选择时收起态，
      // 框与内容始终一致，不再按模板节点数估算出大空白）；手动 resize 过 → 跟随 tlyt。
      // 新卡位置：无持久化布局时找空位（同批次多张卡用 extras 互相避让）
      const position = tlyt
        ? { x: tlyt.x, y: tlyt.y }
        : (() => {
          const pos = findFreeSpot(CARD_W, toolDefaultH(nodeCount), newToolExtrasRef);
          newToolExtrasRef.push({ x: pos.x, y: pos.y, w: CARD_W, h: toolDefaultH(nodeCount) });
          return pos;
        })();
      return {
      id: t.id,
      type: "card",
      position,
      // ★ ReactFlow 节点不读 data.node.h，必须显式 style.width。手动 resize 过 → 跟随 tlyt；否则 auto。
      style: { width: tlyt?.custom && typeof tlyt.w === "number" ? tlyt.w : CARD_W,
               height: tlyt?.custom && typeof tlyt.h === "number" ? tlyt.h : "auto" },
      data: {
        node: {
          id: t.id, type: "workflow-tool", x: 0, y: 0,
          w: CARD_W,
          h: Math.max(180, 120 + nodeCount * 70),
          generationIds: [],
          templateId: t.templateId,
          templateName: t.templateName,
          wfConfirmed: tlyt?.wfConfirmed ?? t.wfConfirmed ?? false,
          wfDraft: tlyt?.wfDraft ?? t.wfDraft,
          wfCaptured: tlyt?.wfCaptured ?? t.wfCaptured,
          wfExposedIds: tlyt?.wfExposedIds || [],
          wfEstimatedNodeCount: nodeCount,
        },
        gens: [], imageUrls: [], prompt: t.templateName, isSel: false, naturalSize: undefined,
        customSize: !!tlyt?.custom,
        comfyUrl: settings.comfyuiUrl || "",
        onSelect: () => {},
        onOpen: (nn: CanvasNode) => { setToolModalNode({ node: nn, autoConfirm: false }); },
        onResize: (id: string, w: number, h: number) => {
          // 拉伸中：实时更新 style 跟随（同 projected 节点，不触发投影重建）
          setRfNodes((prev) => prev.map((n) => (n.id === id
          ? { ...n, style: { ...n.style, width: w, height: h }, data: { ...n.data, customSize: true } }
          : n)));
        },
        // 拉伸结束写 layoutNodes + 持久化一次
        onResizeEnd: (id: string, w: number, h: number) => {
          const base = layoutNodesRef.current[id] || { x: 0, y: 0, w: CARD_W, h: 180 };
          const next = { ...layoutNodesRef.current, [id]: { ...base, w, h, custom: true } };
          setLayoutNodes(next);
          persistNow({ nodes: next });
        },
        onImgLoaded: () => {},
        onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
          const rect = stageWrapRef.current?.getBoundingClientRect();
          openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
        },
        // 工作流工具卡按钮回调
        onConfirmWorkflow: (nn: CanvasNode) => {
          // 卡片「选择完毕」→ 打开双栏编辑器并自动执行抓取（与对话模式卡片直接抓取对齐）
          setToolModalNode({ node: nn, autoConfirm: true });
        },
        onRunWorkflow: (nn: CanvasNode) => {
          // 运转工作流 → 画布创建「生成中」节点 + 对话框下方进度条；
          // 运转完毕占位节点被生成内容节点替换（laf-generation-saved 刷新 + laf-canvas-wf-done 清除）
          const runId = `wfrun-${crypto.randomUUID().slice(0, 8)}`;
          window.dispatchEvent(new CustomEvent("laf-canvas-wf-run", {
            detail: { runId, toolNodeId: nn.id, templateName: nn.templateName || "工作流" },
          }));
          runWorkflowTask(nn, runId);
        },
        onChangeWorkflow: (nn: CanvasNode) => {
          // 更改 → 回到未选择状态（保留节点参数和高度），并直接进入节点页面（编辑器）
          const nodeCount = nn.wfExposedIds?.length || 0;
          setRfNodes((ns) => ns.map((n) => (
            n.id === nn.id ? { ...n, data: { ...n.data, node: { ...n.data.node, wfConfirmed: false } } } : n
          )));
          setLayoutNodes((prev) => {
            const base = prev[nn.id] || { x: 0, y: 0, w: CARD_W, h: 180 };
            return { ...prev, [nn.id]: { ...base, wfConfirmed: false, h: Math.max(180, 120 + nodeCount * 70) } };
          });
          setToolModalNode({ node: { ...nn, wfConfirmed: false }, autoConfirm: false });
        },
      },
      };
    });
    // 剧情节点（对话楼层实时投影，与生成内容节点同机制）
    // ★ 每个楼层一条剧情文本消息 → 一个节点；重启后对话历史恢复 → 节点自动出现。
    //   封面 = 剧情自动插画；有图 → 左图右文（对齐图组节点）；无图 → 9:16 竖版卡。
    //   正文渲染跑显示层正则（markdownOnly）——与对话模式同款管线（见 CardNodeComponent）。
    const storyDefaultH = 240;
    const storyExtrasRef: Array<{ x: number; y: number; w: number; h: number }> = [];
    // ★ 正在生成的消息（streamingId）跳过剧情楼层投影：生成中只显示占位节点，
    //   避免它因已带 route 标签 + 非空 trace 正文被误判为「已完成」的剧情楼层。
    //   生成结束 streamingId 清空后，该消息才按标签正常落成剧情楼层。
    const storySource = streamingId ? messages.filter((m) => m.id !== streamingId) : messages;
    const storyNodes: Node<CardNodeData>[] = projectStoryNodes(storySource)
      // 剧情楼层删除 = 删除对话消息（走 onDeleteMessage，不进 deletedIds 黑名单），
      // 故这里不再按黑名单过滤：消息在则楼层在，消息删则投影自然消失。
      .map((sp) => {
      const hasCover = !!sp.image;
      const baseW = hasCover ? CARD_W : INSPIRATION_CARD_W;
      const baseH = hasCover ? storyDefaultH : INSPIRATION_CARD_H;
      const tlyt = layoutNodes[sp.id];
      const position = tlyt
        ? { x: tlyt.x, y: tlyt.y }
        : (() => {
          // ★ 剧情楼层接替生成中占位节点的原位置（生成结束瞬间 streamingPosRef 仍持有位置，
          //   无需等待 effect 转存，本次渲染直接读；若用户已生成过其它内容则仍走 findFreeSpot）。
          const ph = streamingPosRef.current;
          if (ph && ph.id === sp.messageId) {
            const pos = { x: ph.x, y: ph.y };
            // 标记占位已被剧情楼层接走：effect 不再转存 pendingAnchorRef（防陈旧锚点污染内容节点放置）
            placeholderConsumedByStoryRef.current = true;
            storyExtrasRef.push({ x: pos.x, y: pos.y, w: baseW, h: baseH });
            // 记录待落盘位置：独立 effect 写进 layoutNodes + canvas.json（首次放置即持久化）
            pendingStoryLayoutsRef.current[sp.id] = { x: pos.x, y: pos.y, w: baseW, h: baseH };
            return pos;
          }
          const pos = findFreeSpot(baseW, baseH, storyExtrasRef);
          storyExtrasRef.push({ x: pos.x, y: pos.y, w: baseW, h: baseH });
          // 记录待落盘位置：独立 effect 写进 layoutNodes + canvas.json（首次放置即持久化）
          pendingStoryLayoutsRef.current[sp.id] = { x: pos.x, y: pos.y, w: baseW, h: baseH };
          return pos;
        })();
      return {
        id: sp.id,
        type: "card",
        position,
        style: { width: tlyt?.custom && typeof tlyt.w === "number" ? tlyt.w : baseW,
                 height: tlyt?.custom && typeof tlyt.h === "number" ? tlyt.h : baseH },
        data: {
          node: {
            id: sp.id, type: "story", x: 0, y: 0, w: baseW, h: baseH,
            generationIds: [],
            storyText: sp.text,
            storyMessageId: sp.messageId,
            storyIndex: sp.index + 1,
            storyTotal: sp.total,
            storyImage: sp.image,
            storyVideo: sp.video,
            storyAudio: sp.audio,
            storyAudioLines: sp.audioLines,
            storyThinking: sp.thinking,
            groupKey: sp.id,
          },
          gens: [], imageUrls: sp.image ? [sp.image] : [], prompt: sp.text, isSel: false, naturalSize: undefined,
          customSize: !!tlyt?.custom,
          displayRegex,
          onSelect: () => {},
          onOpen: (nn: CanvasNode) => { setShowStoryThinking(false); setDetailNode(nn); },
          onResize: (id: string, w: number, h: number) => {
            setRfNodes((prev) => prev.map((n) => (n.id === id
              ? { ...n, style: { ...n.style, width: w, height: h }, data: { ...n.data, customSize: true } }
              : n)));
          },
          onResizeEnd: (id: string, w: number, h: number) => {
            const base = layoutNodesRef.current[id] || { x: 0, y: 0, w: CARD_W, h: storyDefaultH };
            const next = { ...layoutNodesRef.current, [id]: { ...base, w, h, custom: true } };
            setLayoutNodes(next);
            persistNow({ nodes: next });
          },
          onImgLoaded: () => {},
          onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
            const rect = stageWrapRef.current?.getBoundingClientRect();
            openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
          },
        },
      };
    });
    // 剧情节点投影列表同步给 ref（「整理剧情顺序」按钮 / 自动时序线用，顺序 = 剧情顺序）
    storyNodesRef.current = storyNodes;
    // 灵感卡节点（独立持久化，位置直接来自 inspirationCards 列表，不走 layoutNodes）
    const inspirationNodes: Node<CardNodeData>[] = inspirationCards.map((card) => ({
      id: card.id,
      type: "card",
      position: { x: card.x, y: card.y },
      style: { width: card.w, height: card.h },
      data: {
        node: {
          id: card.id, type: "inspiration-card",
          x: card.x, y: card.y, w: card.w, h: card.h,
          generationIds: [],
          inspirationKind: card.kind,
          inspirationTitle: card.title,
          inspirationContent: card.content,
          inspirationSourceRef: card.sourceRef,
          groupKey: card.id,
        },
        gens: [], imageUrls: card.imageUrl ? [card.imageUrl] : [], prompt: card.title, customLabel: card.title,
        isSel: false, naturalSize: undefined,
        onSelect: () => {},
        onOpen: (n: CanvasNode) => {
          // 双击灵感卡：世界书→弹窗；角色卡→角色卡弹窗；预设→偏置预设弹窗；其余→本地编辑
          const c = inspirationCardsRef.current.find((x) => x.id === n.id);
          if (!c) return;
          // ★ 资产库发送来的灵感卡（带图）→ 打开「左图右文」编辑弹窗（可删图/替换图），不误入偏置预设
          if (c.imageUrl) {
            setInspirationEditId(c.id);
            setInspirationEditKind(c.kind);
            setInspirationEditTitle(c.title);
            setInspirationEditContent(c.content);
            return;
          }
          if (c.kind === "worldbook-entry" && c.sourceRef) {
            // 画布模式：双击打开仓库快照世界书（编辑即改快照，与源库隔离）
            if (settings.outputDir && repoId) {
              setWbRepoLoc({ output_dir: settings.outputDir, repo_id: repoId });
              setWbPopupTitle(c.title);
              return;
            }
          }
          if (c.kind === "character") {
            const name = c.sourceRef?.startsWith("char:") ? c.sourceRef.slice("char:".length) : c.title;
            setCharModal({ name, cardId: c.id, content: c.content });
            return;
          }
          if (c.kind === "preset") {
            setPresetModalOpen(true);
            return;
          }
          setInspirationEditId(c.id);
          setInspirationEditKind(c.kind);
          setInspirationEditTitle(c.title);
          setInspirationEditContent(c.content);
        },
        onResize: (id: string, w: number, h: number) => {
          // 灵感卡拉伸中：实时更新 style 跟随；结束才写 inspirationCards（避免投影重建闪烁）
          setRfNodes((prev) => prev.map((n) => (n.id === id
          ? { ...n, style: { ...n.style, width: w, height: h }, data: { ...n.data, customSize: true } }
          : n)));
        },
        onResizeEnd: (id: string, w: number, h: number) => {
          const next = inspirationCardsRef.current.map((c) => c.id === id ? { ...c, w, h } : c);
          setInspirationCards(next);
          inspirationCardsRef.current = next;
          persistNow({ cards: next });
        },
        onImgLoaded: () => {},
        onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
          const rect = stageWrapRef.current?.getBoundingClientRect();
          openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
        },
        onInspirationAction: (action: "insert" | "copy" | "edit", nn: CanvasNode) => {
          const c = inspirationCardsRef.current.find((x) => x.id === nn.id);
          if (!c) return;
          if (action === "insert") insertInspirationToChat(c);
          else if (action === "copy") {
            try { void navigator.clipboard.writeText(c.content); showToast("内容已复制到剪贴板", "success"); } catch { showToast("复制失败，请手动选择", "error"); }
          } else if (action === "edit") {
            setInspirationEditId(c.id);
            setInspirationEditKind(c.kind);
            setInspirationEditTitle(c.title);
            setInspirationEditContent(c.content);
          }
        },
      },
    }));
    // 参考图节点（独立持久化，位置直接来自 referenceImages 列表，不走 layoutNodes）
    const referenceImageNodes: Node<CardNodeData>[] = referenceImages.map((ref) => ({
      id: ref.id,
      type: "card",
      position: { x: ref.x, y: ref.y },
      style: { width: ref.w, height: ref.h },
      data: {
        node: {
          id: ref.id, type: "reference-image",
          x: ref.x, y: ref.y, w: ref.w, h: ref.h,
          generationIds: [],
          referenceImageUrl: ref.imageUrl,
          referenceImageTitle: ref.title,
          groupKey: ref.id,
        },
        gens: [], imageUrls: ref.imageUrl ? [ref.imageUrl] : [],
        prompt: ref.title, customLabel: ref.title,
        isSel: false, naturalSize: undefined,
        onSelect: () => {},
        onOpen: (n: CanvasNode) => { setDetailNode(n); },
        onResize: (id: string, w: number, h: number) => {
          setRfNodes((prev) => prev.map((n) => (n.id === id
            ? { ...n, style: { ...n.style, width: w, height: h }, data: { ...n.data, customSize: true } }
            : n)));
        },
        onResizeEnd: (id: string, w: number, h: number) => {
          const next = referenceImagesRef.current.map((c) => c.id === id ? { ...c, w, h } : c);
          setReferenceImages(next);
          referenceImagesRef.current = next;
          persistNow({ refs: next });
        },
        onImgLoaded: () => {},
        onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
          const rect = stageWrapRef.current?.getBoundingClientRect();
          openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
        },
      },
    }));
    // 工作流「生成中」占位节点：运转任务存活期间显示，完毕由生成内容节点替换。
    // ★ 通用 9:16 占位（与 input generating 占位同款外观 + 模板名 + 实时进度），
    //   不绑定工作流模板形态——剧情生成/apikey 生成/工作流生成等任何任务都长这样。
    const wfRunNodes: Node<CardNodeData>[] = wfRuns.map((run) => {
      // 真实提示词/节点名（对齐对话模式）：graph 在提交后随 run-graph 事件带入
      const meta = run.graph ? workflowGenMetadata(run.templateName, run.graph) : null;
      const runPrompt = meta?.prompt || run.templateName;
      const runNodeLabel = run.node ? (() => {
        try {
          const g = (run.graph ?? null) as Record<string, { class_type?: string }> | null;
          const c = g?.[run.node]?.class_type;
          return c ? `${c} (#${run.node})` : `节点 #${run.node}`;
        } catch { return `节点 #${run.node}`; }
      })() : "";
      return {
        id: run.id,
        type: "card",
        position: { x: run.x, y: run.y },
        style: { width: CARD_W, height: 427 },
        data: {
          node: {
            id: run.id, type: "input", x: run.x, y: run.y, w: CARD_W, h: 427,
            generationIds: [], input: true, inputStatus: "generating",
            prompt: runPrompt, groupKey: run.id,
          },
          gens: [], imageUrls: [], prompt: runPrompt,
          isSel: false, naturalSize: undefined,
          wfProgress: run.progress, wfProgressNode: runNodeLabel,
          onSelect: () => {}, onOpen: () => {}, onResize: () => {}, onImgLoaded: () => {},
          // 占位节点无右键菜单（临时节点，任务结束自动清除）
          onNodeCtx: () => {},
        },
      };
    });
    const nextNodes: Node<CardNodeData>[] = [...inputs, ...toolCards, ...storyNodes, ...wfRunNodes, ...inspirationNodes, ...referenceImageNodes, ...projected];
    // 投影时保留用户选中状态（框选/单击），避免 useEffect 重跑强制 selected:false
    // 取消选中；且内容（id/position/selected）未变时返回原数组引用，React 不重渲染 → 不闪
    setRfNodes((prevNodes) => {
      const prevMap = new Map(prevNodes.map((n) => [n.id, n]));
      const merged = nextNodes.map((n) => {
        const prev = prevMap.get(n.id);
        // 保留用户选中状态（框选/单击），避免 useEffect 重跑强制 selected:false 取消选中
        let out = prev && prev.selected ? { ...n, selected: true } : n;
        if (prev) {
          // 保留 ReactFlow 实际坐标（含组内相对坐标）与父子关系（parentId/extent），
          // 否则组内子节点会被投影回绝对坐标 → 组崩坏
          out = {
            ...out,
            position: prev.position,
            ...(prev.parentId ? { parentId: prev.parentId, extent: prev.extent } : {}),
          };
        }
        return out;
      });
      if (
        prevNodes.length === merged.length
        && merged.every((n, i) => {
          const o = prevNodes[i];
          return o.id === n.id
            && o.position.x === n.position.x && o.position.y === n.position.y
            && !!o.selected === !!n.selected
            && (o.parentId || undefined) === (n.parentId || undefined)
            // 尺寸类变更必须进状态（resize 后 style/customSize 变更），否则被静默丢弃
            && (o.style?.width ?? undefined) === (n.style?.width ?? undefined)
            && (o.style?.height ?? undefined) === (n.style?.height ?? undefined)
            && !!o.data?.customSize === !!n.data?.customSize
            // ★ data 必须比较：编辑器写回 wfCaptured/wfConfirmed 后若仍返回旧数组，
            //   卡片回调拿到的 nn 是旧数据 → 「运转工作流」首次点击因 wfCaptured 空而静默失败（要点两下）
            && o.data === n.data;
        })
      ) {
        return prevNodes;
      }
      return merged;
    });
    // edges：手动引用线从持久化布局恢复（node 可能已不存在则过滤）；
    // 自动剧情顺序线为派生数据（按剧情顺序连接相邻楼层），不落盘、用户删不掉，
    // 与手动引用线用不同视觉区分（紫色虚线 + 箭头 vs 默认实线）。
    const validIds = new Set([...inputs, ...toolCards, ...storyNodes, ...projected, ...referenceImageNodes].map((n) => n.id));
    const manualEdges: Edge[] = layoutEdges
      .filter((e) => validIds.has(e.source) && validIds.has(e.target))
      .map((e) => ({ id: `${e.source}-${e.target}`, source: e.source, target: e.target, animated: false }));
    const autoEdges: Edge[] = showStoryFlowRef.current
      ? storyNodes.slice(0, -1).map((n, i) => ({
          id: `storyflow-${i}`,
          source: storyNodes[i].id,
          target: storyNodes[i + 1].id,
          type: "smoothstep",
          style: { stroke: "#8b5cf6", strokeDasharray: "5 5", strokeWidth: 1.5, opacity: 0.75 },
          markerEnd: { type: MarkerType.ArrowClosed, color: "#8b5cf6", width: 14, height: 14 },
          data: { storyFrom: i + 1, storyTo: i + 2 },
        }))
      : [];
    autoEdgeIdsRef.current = new Set(autoEdges.map((e) => e.id));
    setRfEdges([...manualEdges, ...autoEdges]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawNodes, contentPlacement, layoutNodes, pendingInputs, pendingToolCreates, byGroup, layoutEdges, inspirationCards, referenceImages, wfRuns, runWorkflowTask, messages, displayRegex, showStoryFlow]);

  // ★ 新内容节点位置立即持久化：锚点网格算出的坐标写进 layoutNodes 并落盘，
  //   否则刷新/切走再回时新节点又会被投影打回原点；同时消费原位替换锚点。
  useEffect(() => {
    if (!contentPlacement || contentPlacement.ids.length === 0) return;
    const next = { ...layoutNodesRef.current };
    let changed = false;
    for (const id of contentPlacement.ids) {
      if (!next[id]) {
        const p = contentPlacement.map[id];
        const d = contentPlacement.dims[id];
        next[id] = { x: p.x, y: p.y, w: d.w, h: d.h };
        changed = true;
      }
    }
    if (!changed) return;
    setLayoutNodes(next);
    pendingAnchorRef.current = null; // 原位替换锚点已消费
    if (repoId) {
      void saveLayout(repoId, settings.outputDir, {
        nodes: next,
        // 只持久化手动引用线（storyflow-* 是派生数据，不落盘）
        edges: rfEdges.filter((e) => !e.id.startsWith("storyflow-")).map((e) => ({ source: e.source, target: e.target })),
        viewport: viewportRef.current,
        inspirationCards: inspirationCardsRef.current,
        referenceImages: referenceImagesRef.current,
        deletedIds: deletedIdsRef.current,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentPlacement]);

  // 新剧情节点位置立即落盘：首次投影用 findFreeSpot 算出位置后写进 layoutNodes + canvas.json，
  // 否则位置不进持久化，重进画布时由 findFreeSpot 重新计算（受其它节点影响）→ 剧情楼层漂移。
  useEffect(() => {
    const pending = pendingStoryLayoutsRef.current;
    const ids = Object.keys(pending);
    if (ids.length === 0) return;
    const next = { ...layoutNodesRef.current };
    for (const id of ids) {
      if (!next[id]) next[id] = pending[id];
    }
    pendingStoryLayoutsRef.current = {};
    layoutNodesRef.current = next;
    setLayoutNodes(next);
    if (repoId) {
      void saveLayout(repoId, settings.outputDir, {
        nodes: next,
        edges: rfEdges.filter((e) => !e.id.startsWith("storyflow-")).map((e) => ({ source: e.source, target: e.target })),
        viewport: viewportRef.current,
        inspirationCards: inspirationCardsRef.current,
        referenceImages: referenceImagesRef.current,
        deletedIds: deletedIdsRef.current,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, layoutNodes]);

  // 持久化（布局 + edges + 灵感卡 + 删除黑名单）
  // 接受可选覆盖：调用方在 setState 后立刻 persist 时，ref 还未更新，需显式传入新值，
  // 否则旧值被写回 canvas.json → 刷新后「删掉的节点又回来了」。
  const persistNow = useCallback((overrides?: {
    nodes?: Record<string, NodeLayout>;
    edges?: { source: string; target: string }[];
    cards?: InspirationCardStored[];
    refs?: ReferenceImageStored[];
    deletedIds?: string[];
  }) => {
    if (!repoId) return;
    void saveLayout(repoId, settings.outputDir, {
      nodes: overrides?.nodes ?? layoutNodesRef.current,
      // 只持久化手动引用线（storyflow-* 是派生数据，不落盘）
      edges: overrides?.edges ?? rfEdges.filter((e) => !e.id.startsWith("storyflow-")).map((e) => ({ source: e.source, target: e.target })),
      viewport: viewportRef.current,
      inspirationCards: overrides?.cards ?? inspirationCardsRef.current,
      referenceImages: overrides?.refs ?? referenceImagesRef.current,
      deletedIds: overrides?.deletedIds ?? deletedIdsRef.current,
    });
  }, [repoId, settings.outputDir, rfEdges]);

  // 整理剧情顺序：把剧情楼层按消息顺序排成蛇形网格（复用 placeNewNodes 空位避让），
  // 写 layoutNodes + 落盘；非剧情节点保持原位不动。
  const arrangeStoryOrder = useCallback(() => {
    const storyList = storyNodesRef.current;
    if (storyList.length === 0) { showToast("画布上还没有剧情楼层", "info"); return; }
    const occupied = collectOccupiedRects();
    const sizes = storyList.map((n) => ({
      w: typeof n.style?.width === "number" ? n.style.width : CARD_W,
      h: typeof n.style?.height === "number" ? n.style.height : 240,
    }));
    const positions = placeNewNodes(sizes, null, occupied, { cols: 4, originX: 24, originY: 24 });
    const nextLayout = { ...layoutNodesRef.current };
    storyList.forEach((n, i) => {
      nextLayout[n.id] = {
        ...(nextLayout[n.id] || {}),
        x: positions[i].x, y: positions[i].y,
        w: sizes[i].w, h: sizes[i].h,
      };
    });
    setLayoutNodes(nextLayout);
    setRfNodes((ns) => ns.map((n) => {
      const idx = storyList.findIndex((s) => s.id === n.id);
      return idx >= 0 ? { ...n, position: { x: positions[idx].x, y: positions[idx].y } } : n;
    }));
    persistNow({ nodes: nextLayout });
    showToast(`已按剧情顺序整理 ${storyList.length} 个楼层`, "success");
  }, [collectOccupiedRects, persistNow, showToast]);

  // 拖动中：实时中心点对齐吸附（绝对坐标 + 滞回释放 + 蓝色发光辅助线）
  // 进入 SNAP_PX 贴住，偏离 SNAP_RELEASE_PX 才释放；x/y 各自独立，避免闪烁/乱飞。
  // 使用 onNodeDrag 的第三个参数 nodes（ReactFlow 内部维护的节点数组，measured 已就绪）。
  // ★ 吸附比较必须用绝对坐标（父组链累加）：组内子节点 position 是相对父组的局部坐标，
  //   直接跨节点比较局部坐标会让吸附判定错乱、辅助线画到错误位置。
  //   guides 存绝对坐标（GuidesOverlay 按画布全局坐标转屏幕坐标）；修正时把全局吸附
  //   差量加回拖动节点的局部 position（组内节点同样正确）。
  // ★ 共享吸附计算：onNodeDrag（拖动中贴住）与 onNodeDragStop（松手落点）复用同一结果，
  //   保证两者一致。否则松手瞬间 ReactFlow 用鼠标最终位置覆盖 onNodeDrag 的吸附修正 →
  //   节点偏离中心线（用户报的"上下左右半格偏差"）。返回吸附结果与落点反推所需尺寸/父链偏移。
  const computeSnap = useCallback((node: Node<CardNodeData>) => {
    const id = node.id;
    // ★ 真根因：ReactFlow v12 的 onNodeDrag 第三参并不保证是「所有节点」——实测只有被拖的
    //   1 个 → for 循环跳过自己 → targetCentersX 永远空 → 无线。用自己维护的 rfNodesRef
    //   （含所有生成节点 + 灵感卡 + 组等投影出来的节点）作为目标集合。
    const allNodes = rfNodesRef.current;
    const byId = new Map(allNodes.map((n) => [n.id, n]));
    const vp = viewportRef.current;
    const k = vp.scale > 0 ? vp.scale : 1;
    // ★ 尺寸真源 = 「卡片可见盒」（DOM 实测），不是 wrapper 盒：
    //   video/audio 卡 wrapper 320 宽但卡片固定渲染 240（fillWrapper=false）、图组/输入/工作流卡
    //   高度随内容自适应 → wrapper 中心 ≠ 可见中心 → 按 wrapper 尺寸吸附后可见中心偏移中心线。
    //   用卡片根元素 data-card-id 的 getBoundingClientRect 取真实可见盒；拖动中宽高不变，无帧滞后。
    const stageEl = stageWrapRef.current;
    const stageRect = stageEl?.getBoundingClientRect();
    const cardRectById = new Map<string, { w: number; h: number }>();
    if (stageEl && stageRect) {
      for (const el of stageEl.querySelectorAll<HTMLElement>("[data-card-id]")) {
        const rid = el.dataset.cardId;
        if (!rid) continue;
        const r = el.getBoundingClientRect();
        cardRectById.set(rid, { w: r.width, h: r.height });
      }
    }
    const getNodeSize = (n: typeof allNodes[number]): { w: number; h: number } => {
      const vis = cardRectById.get(n.id);
      if (vis && vis.w > 0 && vis.h > 0) return { w: vis.w / k, h: vis.h / k };
      const mw = n.measured?.width, mh = n.measured?.height;
      if (mw && mh) return { w: mw, h: mh };
      const w = parseDim(n.style?.width) ?? CARD_W;
      const h = parseDim(n.style?.height) ?? 320;
      if (w > 0 && h > 0) return { w, h };
      const lyt = layoutNodesRef.current[n.id];
      if (lyt && lyt.w > 0 && lyt.h > 0) return { w: lyt.w, h: lyt.h };
      return { w: CARD_W, h: 320 };
    };
    const { w: nodeW, h: nodeH } = getNodeSize(node);
    const abs = nodeAbsolutePosition(node, byId);
    const cx = abs.x + nodeW / 2;
    const cy = abs.y + nodeH / 2;
    // 视野过滤：只吸附视口内目标（+margin 容错）
    const vw = stageEl?.clientWidth || 0;
    const vh = stageEl?.clientHeight || 0;
    const hasStage = vw > 0 && vh > 0;
    const margin = 120;
    const targetCentersX: number[] = [];
    const targetCentersY: number[] = [];
    for (const n of allNodes) {
      if (n.id === id) continue;
      const { w: nw, h: nh } = getNodeSize(n);
      const nAbs = nodeAbsolutePosition(n, byId);
      const nx = nAbs.x + nw / 2;
      const ny = nAbs.y + nh / 2;
      if (hasStage) {
        const screenX = nx * vp.scale + vp.x;
        const screenY = ny * vp.scale + vp.y;
        if (screenX < -margin || screenX > vw + margin || screenY < -margin || screenY > vh + margin) continue;
      }
      targetCentersX.push(nx);
      targetCentersY.push(ny);
    }
    // 真实吸附计算：中心对中心；阈值按屏幕像素 / zoom 换算（fitView 缩放下仍能贴住）
    const prevX = snapAxisRef.current.x;
    const prevY = snapAxisRef.current.y;
    const rx = computeAxisSnap(cx, targetCentersX, prevX.active ? prevX.pos : null, SNAP_PX / k, SNAP_RELEASE_PX / k, HINT_PX / k);
    const ry = computeAxisSnap(cy, targetCentersY, prevY.active ? prevY.pos : null, SNAP_PX / k, SNAP_RELEASE_PX / k, HINT_PX / k);
    // 父链全局偏移：新局部 = (吸附点 - 半宽) - 父链偏移（组内子节点同样正确）
    const parentOffX = abs.x - node.position.x;
    const parentOffY = abs.y - node.position.y;
    return { rx, ry, nodeW, nodeH, parentOffX, parentOffY, vp };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onNodeDrag: OnNodeDrag<Node<CardNodeData>> = useCallback((_, node) => {
    const id = node.id;
    const { rx, ry, nodeW, nodeH, parentOffX, parentOffY, vp } = computeSnap(node);
    snapAxisRef.current = {
      x: rx.pos !== null ? { active: true, pos: rx.pos } : { active: false, pos: 0 },
      y: ry.pos !== null ? { active: true, pos: ry.pos } : { active: false, pos: 0 },
    };
    const snapX = rx.pos;
    const snapY = ry.pos;
    setGuides((prev) => {
      const nx = snapX ?? undefined;
      const ny = snapY ?? undefined;
      const nsx = rx.snap || undefined;
      const nsy = ry.snap || undefined;
      if (prev.x === nx && prev.y === ny && prev.snapX === nsx && prev.snapY === nsy) return prev;
      return { x: nx, y: ny, snapX: nsx, snapY: nsy };
    });
    updateGuideLines(snapX, snapY, !!rx.snap, !!ry.snap, vp.scale, vp.x, vp.y);
    if (rx.snap || ry.snap) {
      const nx = rx.snap ? (snapX! - nodeW / 2) - parentOffX : node.position.x;
      const ny = ry.snap ? (snapY! - nodeH / 2) - parentOffY : node.position.y;
      setRfNodes((ns) => {
        const cur = ns.find((n) => n.id === id);
        if (cur && Math.abs(cur.position.x - nx) < 0.5 && Math.abs(cur.position.y - ny) < 0.5) {
          return ns;
        }
        return ns.map((n) => (n.id === id ? { ...n, position: { x: nx, y: ny } } : n));
      });
    }
  }, [computeSnap, setRfNodes]);

  // 拖拽停止：保存布局 + 清除吸附态
  const parseDim = (v: unknown): number | undefined => {
    if (typeof v === "number") return v;
    if (typeof v === "string") { const n = parseFloat(v); return isNaN(n) ? undefined : n; }
    return undefined;
  };
  const onNodeDragStop: OnNodeDrag<Node<CardNodeData>> = useCallback((_, node) => {
    // ★ 松手最终对齐：ReactFlow 拖拽期间节点位置由鼠标驱动，onNodeDrag 的吸附修正在松手
    //   瞬间会被「鼠标最终位置」覆盖 → 节点往鼠标松手处偏移半格。这里在清空吸附态之前，
    //   复用滞回状态（snapAxisRef 尚存最后一帧）再做一次最终吸附，把落点钉在中心线上。
    const { rx, ry, nodeW, nodeH, parentOffX, parentOffY } = computeSnap(node);
    const finalX = rx.snap ? (rx.pos! - nodeW / 2) - parentOffX : node.position.x;
    const finalY = ry.snap ? (ry.pos! - nodeH / 2) - parentOffY : node.position.y;
    // 若最终对齐与鼠标位置有差，回写节点，让 ReactFlow 渲染停在吸附点（而非鼠标点）
    if (Math.abs(finalX - node.position.x) >= 0.5 || Math.abs(finalY - node.position.y) >= 0.5) {
      setRfNodes((ns) => ns.map((n) => (n.id === node.id ? { ...n, position: { x: finalX, y: finalY } } : n)));
    }
    snapAxisRef.current = { x: { active: false, pos: 0 }, y: { active: false, pos: 0 } };
    setGuides({});
    // 隐藏 DOM 直画辅助线
    const lines = guideLinesRef.current;
    if (lines) { lines.v.style.display = "none"; lines.h.style.display = "none"; }
    const lyt = layoutNodesRef.current[node.id];
    const base = lyt || { x: finalX, y: finalY,
      w: parseDim(node.style?.width) ?? node.measured?.width ?? CARD_W,
      h: parseDim(node.style?.height) ?? node.measured?.height ?? 100,
    };
    const newLayout = { ...layoutNodesRef.current, [node.id]: { ...base, x: finalX, y: finalY } };
    setLayoutNodes(newLayout);
    // ★ 灵感卡（角色卡/世界书/预设/表单行）的位置存在 inspirationCards 数组而非 layoutNodes；
    //   拖拽结束后必须同步更新 inspirationCards，否则重进画布时位置回弹到旧坐标。
    const isInspiration = node.data?.node?.type === "inspiration-card";
    const updatedCards = isInspiration
      ? inspirationCardsRef.current.map((c) =>
          c.id === node.id ? { ...c, x: finalX, y: finalY } : c)
      : inspirationCardsRef.current;
    if (isInspiration) {
      setInspirationCards(updatedCards);
      inspirationCardsRef.current = updatedCards;
    }
    persistNow({ nodes: newLayout, cards: updatedCards });
  }, [computeSnap, persistNow]);

  const onConnect: OnConnect = useCallback((conn: Connection) => {
    setRfEdges((eds) => {
      const next = addEdge({ ...conn, animated: false }, eds);
      persistNow({ edges: next.map((e) => ({ source: e.source, target: e.target })) });
      return next;
    });
  }, [persistNow, setRfEdges]);

  // 自动剧情顺序线是派生数据：拦截 remove/replace，用户删不掉；select 等其它变更放行
  const onEdgesChangeRaw = useCallback((changes: EdgeChange[]) => {
    const filtered = changes.filter((c) =>
      (c.type === "remove" || c.type === "replace") && autoEdgeIdsRef.current.has(c.id) ? false : true,
    );
    if (filtered.length > 0) onEdgesChange(filtered);
  }, [onEdgesChange]);

  // 连线 = metadata references：双击连线查看引用关系（source 被 target 引用）；
  // 自动剧情顺序线双击 → 显示「第 N 段 → 第 N+1 段」顺序说明
  const [edgeInfo, setEdgeInfo] = useState<{ source: string; target: string; storyFrom?: number; storyTo?: number } | null>(null);
  const onEdgeDoubleClick = useCallback((_: React.MouseEvent, edge: Edge) => {
    if (edge.id.startsWith("storyflow-")) {
      const d = (edge.data || {}) as { storyFrom?: number; storyTo?: number };
      setEdgeInfo({ source: edge.source, target: edge.target, storyFrom: d.storyFrom, storyTo: d.storyTo });
      return;
    }
    setEdgeInfo({ source: edge.source, target: edge.target });
  }, []);

  const onNodesChangeRaw = useCallback((changes: NodeChange<Node<CardNodeData>>[]) => {
    // 仅接受 position/select 类变更（拖拽由 ReactFlow 管理）
    onNodesChange(changes);
  }, [onNodesChange]);

  const detail = detailNode ? nodeDetail(detailNode, byGroup) : null;
  const openSend = (title: string, payload: SendPayload) => setSendTarget({ title, payload });

  // ===== 右键功能栏（右键卡片=编辑介绍/删除；框选多个=建组/一起删除） =====
  const stageWrapRef = useRef<HTMLDivElement | null>(null);
  const flowScreenRef = useRef<((p: { x: number; y: number }) => { x: number; y: number }) | null>(null);
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; nodeIds: string[] } | null>(null);
  const [editLabelId, setEditLabelId] = useState<string | null>(null);
  const [editLabelVal, setEditLabelVal] = useState("");

  const onNodeContextMenu: NodeMouseHandler<Node<CardNodeData>> = useCallback((e, node) => {
    e.preventDefault();
    const rect = stageWrapRef.current?.getBoundingClientRect();
    openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), node.id);
  }, []);

  // 供卡片自身右键调用（onNodeCtx）：阻止冒泡后弹出功能栏
  const openCtxForNode = useCallback((x: number, y: number, nodeId: string) => {
    const selectedIds = rfNodesRef.current.filter((n) => n.selected).map((n) => n.id);
    const ids = selectedIds.includes(nodeId) && selectedIds.length > 1 ? selectedIds : [nodeId];
    setCtxMenu({ x, y, nodeIds: ids });
  }, []);

  // 空白右键：弹「画布」菜单（全选/取消选择），也保证右键必有反应
  const onPaneContextMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    e.preventDefault();
    const rect = stageWrapRef.current?.getBoundingClientRect();
    setCtxMenu({
      x: (e as React.MouseEvent).clientX - (rect?.left || 0),
      y: (e as React.MouseEvent).clientY - (rect?.top || 0),
      nodeIds: [],
    });
  }, []);

  // 画布空白双击：新建输入节点（用户说「画布双击是新建节点不是放大」）
  // ReactFlow v12 无 onPaneDoubleClick，用 onPaneClick + 时间间隔检测模拟双击
  // 新建的是 draft 输入卡：显示「双击输入提示词」，双击打开编辑弹窗提交生成；
  // 生成中占位节点则改由对话流 streamingId 投影（画布输入栏与对话模式同源发送）。
  const lastPaneClickRef = useRef(0);
  const onPaneClick = useCallback((e: React.MouseEvent) => {
    const now = Date.now();
    if (now - lastPaneClickRef.current < 350) {
      // 双击：新建 draft 输入节点
      const conv = flowScreenRef.current;
      const pos = conv ? conv({ x: e.clientX, y: e.clientY }) : { x: e.clientX, y: e.clientY };
      const id = `input-${crypto.randomUUID().slice(0, 8)}`;
      setPendingInputs((prev) => [...prev, {
        id,
        prompt: "新输入节点",
        x: pos.x - CARD_W / 2,
        y: pos.y - 60,
        status: "draft",
      }]);
      // 新建即打开灵感卡编辑器
      setInspirationEditId("new");
      setInspirationEditKind("preset");
      setInspirationEditTitle("");
      setInspirationEditContent("");
      lastPaneClickRef.current = 0; // 重置，避免三连击误触发
    } else {
      lastPaneClickRef.current = now;
    }
  }, []);

  // 点击菜单外部任意处 → 关闭菜单（标准 context menu 行为，无需点「关闭」）
  const ctxMenuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!ctxMenu) return;
    const onDown = (e: MouseEvent) => {
      const el = ctxMenuRef.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) {
        setCtxMenu(null);
      }
    };
    window.addEventListener("mousedown", onDown, true);
    return () => window.removeEventListener("mousedown", onDown, true);
  }, [ctxMenu]);

  // ===== 灵感卡 / 组标题 编辑态 =====
  const [inspirationEditId, setInspirationEditId] = useState<string | null>(null);
  const [inspirationEditKind, setInspirationEditKind] = useState<InspirationKind>("preset");
  const inspImageFileRef = useRef<HTMLInputElement>(null);
  const [inspirationEditTitle, setInspirationEditTitle] = useState("");
  const [inspirationEditContent, setInspirationEditContent] = useState("");
  const [groupRenameId, setGroupRenameId] = useState<string | null>(null);
  const [groupRenameTitle, setGroupRenameTitle] = useState("");
  const [newInspiration, setNewInspiration] = useState<{ kind: InspirationKind; title: string; content: string } | null>(null);

  // 灵感卡 → 插入对话：调 chatAppend 把 content 作为 user 消息追加到当前作品对话流
  // 不动剧情模式代码——剧情模式会基于 RAG/编排自动处理这条消息
  const insertInspirationToChat = useCallback(async (card: InspirationCardStored) => {
    if (!repoId) { showToast("无当前作品，无法插入", "error"); return; }
    try {
      await chatAppend(repoId, "user", card.content || "(空内容)");
      showToast(`已插入「${card.title}」到对话流`, "success");
    } catch (e) {
      showToast(`插入失败：${(e as Error).message}`, "error");
    }
  }, [repoId]);

  // ===== 从素材库导入灵感卡（世界书/预设各为一张卡片，不再拆成组） =====
  const [importBusy, setImportBusy] = useState(false);
  const importInspirationFromLibrary = useCallback(async (silent = false, existingLayout?: Record<string, NodeLayout>) => {
    if (!repoId || importBusy) return;
    setImportBusy(true);
    const base = settings.characterDir || "";
    const wbDir = settings.worldbookDir || base;
    const presetDir = settings.presetDir || base;
    const existing = inspirationCardsRef.current;
    const has = (kind: string, ref: string) =>
      existing.some((c) => c.kind === kind && c.sourceRef === ref);
    // 计算已有图组最大 Y（保留：兜底信息）
    let maxY = 24;
    const layout = existingLayout || layoutNodesRef.current;
    for (const l of Object.values(layout)) {
      const ny = l.y + (l.h || 200) + 24;
      if (ny > maxY) maxY = ny;
    }
    void maxY;
    const newCards: InspirationCardStored[] = [];
    let total = 0;
    // ★ 统一空位放置：灵感卡自动落在生成结果网格右侧/空位，不再压住任何已有节点；
    //   同批次新卡用 extras 互相避让。
    const extras: Array<{ x: number; y: number; w: number; h: number }> = [];
    const gridPos = (_i: number) => {
      const p = findFreeSpot(INSPIRATION_CARD_W, INSPIRATION_CARD_H, extras);
      extras.push({ x: p.x, y: p.y, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H });
      return p;
    };
    // 角色卡：从仓库快照读取描述（优先快照，不存在回退源库）
    if (boundCards.size > 0 && base) {
      try {
        const chars = await listCharacters(base);
        for (const c of chars.items || []) {
          if (!boundCards.has(c.name.trim())) continue;
          const ref = `char:${c.name}`;
          if (has("character", ref)) continue;
          let desc = "";
          try {
            // 画布模式：优先读仓库快照角色卡
            if (settings.outputDir && repoId) {
              const d = await characterRepoDetail(settings.outputDir, repoId, c.name);
              desc = String((d as { description?: unknown })?.description || "");
            } else {
              const d = await characterDetail(base, c.name);
              desc = String((d as { description?: unknown })?.description || "");
            }
          } catch { /* 忽略详情失败 */ }
          const p = gridPos(total);
          newCards.push({
            id: `insp-${crypto.randomUUID().slice(0, 8)}`, kind: "character",
            title: c.name, content: desc || `角色卡：${c.name}`,
            sourceRef: ref, ...p, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H,
          });
          total++;
        }
      } catch { /* 角色卡不可用则跳过 */ }
    }
    // 世界书：整本一张卡片（不再逐条拆分）
    if (boundWorldbook && settings.outputDir && repoId) {
      const ref = `wb:${boundWorldbook}`;
      if (!has("worldbook-entry", ref)) {
        try {
          let entryCount = 0;
          try {
            const res = await repoWorldbookEntries({ output_dir: settings.outputDir, repo_id: repoId });
            if (!res.not_found) entryCount = (res.entries || []).length;
            else if (wbDir) {
              const src = await listWorldbookEntries({ base: wbDir, name: boundWorldbook });
              entryCount = (src.entries || []).length;
            }
          } catch { /* 详情不可用 */ }
          const p = gridPos(total);
          newCards.push({
            id: `insp-${crypto.randomUUID().slice(0, 8)}`, kind: "worldbook-entry",
            title: `📖 ${boundWorldbook}`,
            content: `${entryCount} 条条目`,
            sourceRef: ref, ...p, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H,
          });
          total++;
        } catch { /* 世界书不可用则跳过 */ }
      }
    }
    // 预设：整本一张卡片（不再逐条拆分）
    if (boundPreset && presetDir) {
      const ref = `preset:${boundPreset}`;
      if (!has("preset", ref)) {
        try {
          const d = await presetDetail(presetDir, boundPreset);
          const prompts = (d as { preset?: { prompts?: unknown[] } })?.preset?.prompts || [];
          const p = gridPos(total);
          newCards.push({
            id: `insp-${crypto.randomUUID().slice(0, 8)}`, kind: "preset",
            title: boundPreset,
            content: `${prompts.length} 个片段`,
            sourceRef: ref, ...p, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H,
          });
          total++;
        } catch { /* 预设不可用则跳过 */ }
      }
    }
    // 表格不设灵感卡（内容太丰富，由剧情模式按 RAG/表注入机制处理，不卡片化）
    if (total === 0) {
      if (!silent) showToast("未绑定角色卡/世界书/预设，或已在画布上，无需导入");
      setImportBusy(false);
      return;
    }
    // 一次性写回（去重保护：仅添加，不动已有）
    setInspirationCards((prev) => [...prev, ...newCards]);
    setImportBusy(false);
    const mergedCards = [...inspirationCardsRef.current, ...newCards];
    persistNow({ cards: mergedCards });
    if (!silent) showToast(`已导入 ${total} 张灵感卡`, "success");
  }, [repoId, boundCards, boundWorldbook, boundPreset, settings.characterDir, settings.worldbookDir, settings.presetDir, settings.outputDir, importBusy, persistNow]);

  const saveInspirationEdit = useCallback(() => {
    if (!inspirationEditId) return;
    // 参考图标题编辑
    const refMatch = referenceImagesRef.current.find((c) => c.id === inspirationEditId);
    if (refMatch) {
      const next = referenceImagesRef.current.map((c) => c.id === inspirationEditId
        ? { ...c, title: inspirationEditTitle.trim() || c.title }
        : c);
      setReferenceImages(next);
      referenceImagesRef.current = next;
      setInspirationEditId(null);
      persistNow({ refs: next });
      return;
    }
    if (inspirationEditId === "new") {
      // 从输入节点新建灵感卡：统一空位放置（不再固定左上网格堆叠）
      const pos = findFreeSpot(INSPIRATION_CARD_W, INSPIRATION_CARD_H);
      const newCard: InspirationCardStored = {
        id: `insp-${crypto.randomUUID().slice(0, 8)}`,
        kind: inspirationEditKind,
        title: inspirationEditTitle.trim() || "灵感卡",
        content: inspirationEditContent,
        x: pos.x,
        y: pos.y,
        w: INSPIRATION_CARD_W,
        h: INSPIRATION_CARD_H,
      };
      const next = [...inspirationCardsRef.current, newCard];
      setInspirationCards(next);
      setInspirationEditId(null);
      persistNow({ cards: next });
      return;
    }
    const next = inspirationCardsRef.current.map((c) => c.id === inspirationEditId
      ? { ...c, kind: inspirationEditKind, title: inspirationEditTitle.trim() || c.title, content: inspirationEditContent }
      : c);
    setInspirationCards(next);
    setInspirationEditId(null);
    persistNow({ cards: next });
  }, [inspirationEditId, inspirationEditKind, inspirationEditTitle, inspirationEditContent, persistNow]);

  // M1.4：画布灵感卡编辑弹窗「左图右文」——删除图片（只留文本）/ 替换图片（本地文件上传）
  const removeInspirationImage = useCallback(() => {
    if (!inspirationEditId) return;
    const next = inspirationCardsRef.current.map((c) => c.id === inspirationEditId
      ? { ...c, imageUrl: "" }
      : c);
    setInspirationCards(next);
    inspirationCardsRef.current = next;
    persistNow({ cards: next });
    showToast("已删除图片，保留文本", "success");
  }, [inspirationEditId, persistNow]);

  const replaceInspirationImage = useCallback(async (file: File) => {
    if (!inspirationEditId) return;
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!IMAGE_EXTENSIONS.has(ext)) {
      showToast("仅支持图片文件（png/jpg/webp/gif/bmp/avif）", "error");
      return;
    }
    try {
      // 本地文件 → data URI 上传（后端 data: 分支豁免候选校验，blob: 后端无法访问）
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("读取文件失败"));
        reader.readAsDataURL(file);
      });
      const res = await saveWebMaterial(settings.outputDir, dataUrl, "", file.name);
      const next = inspirationCardsRef.current.map((c) => c.id === inspirationEditId
        ? { ...c, imageUrl: res.url }
        : c);
      setInspirationCards(next);
      inspirationCardsRef.current = next;
      persistNow({ cards: next });
      showToast(`已替换图片「${res.title || file.name}」`, "success");
    } catch (err) {
      showToast(`替换图片失败：${(err as Error).message}`, "error");
    }
  }, [inspirationEditId, settings.outputDir, persistNow]);

  const removeInspiration = useCallback((id: string) => {
    const next = inspirationCardsRef.current.filter((c) => c.id !== id);
    setInspirationCards(next);
    setCtxMenu(null);
    persistNow({ cards: next });
  }, [persistNow]);

  // 删除剧情楼层节点 = 删除对话消息（onDeleteMessage 回调由上层提供；用 ref 保持最新引用，
  // 否则 deleteNodes 稳定 useCallback 闭包里的 onDeleteMessage 会过期）
  const onDeleteMessageRef = useRef(onDeleteMessage);
  onDeleteMessageRef.current = onDeleteMessage;

  // 删除节点（连带其组内子节点 + 关联连线 + 布局记录）
  // ★ 同步清理各来源 state，否则投影重建时「复活」：
  //   - pendingInputs/pendingToolCreates（input/工具卡来源）
  //   - inspirationCards（灵感卡来源）
  //   - deletedIds 黑名单（gen 投影节点来源：generation_store，id 前缀 img-/video-/audio-）
  //   黑名单随 canvas.json 持久化——删除是显式操作，refresh 不得复活已删节点。
  //   - story 剧情楼层：等效删除对话中的对应消息（走 onDeleteMessage，不进黑名单）
  const deleteNodes = useCallback((ids: string[]) => {
    const idSet = new Set(ids);
    const childIds = rfNodesRef.current
      .filter((n) => n.parentId && idSet.has(n.parentId))
      .map((n) => n.id);
    const all = new Set([...ids, ...childIds]);
    // 清理待落盘的新剧情节点位置，避免删除后又被落盘 effect 写回 layoutNodes
    for (const id of all) delete pendingStoryLayoutsRef.current[id];
    // ★ 剧情楼层节点 = 会话消息的直接投影：删除节点 = 删除对应消息（对话里同步消失，不可撤销）。
    for (const n of rfNodesRef.current) {
      if (!all.has(n.id)) continue;
      const mid = n.data?.node?.storyMessageId;
      if (n.data?.node?.type === "story" && mid) {
        onDeleteMessageRef.current?.(mid);
      }
    }
    setRfNodes((ns) => ns.filter((n) => !all.has(n.id)));
    setRfEdges((eds) => eds.filter((ed) => !all.has(ed.source) && !all.has(ed.target)));
    setPendingInputs((prev) => prev.filter((it) => !all.has(it.id)));
    setPendingToolCreates((prev) => prev.filter((t) => !all.has(t.id)));
    // 投影节点进删除黑名单（gen 派生 id，稳定锚定 generationId；工具卡投影节点同语义——
    // 删除是显式操作，对话消息投影不得复活已删节点）。story 除外：消息已删，投影自然消失，
    // 进黑名单反而会让撤销/未来同 id 语义混乱。
    const projectedDeleted = [...all].filter((i) => /^(img|video|audio|wftool)-/.test(i));
    const nextDeleted = projectedDeleted.length > 0
      ? [...deletedIdsRef.current, ...projectedDeleted.filter((i) => !deletedIdsRef.current.includes(i))]
      : deletedIdsRef.current;
    if (projectedDeleted.length > 0) {
      setDeletedIds(nextDeleted);
      deletedIdsRef.current = nextDeleted;
    }
    setInspirationCards((prev) => {
      const next = prev.filter((c) => !all.has(c.id));
      if (next.length !== prev.length) inspirationCardsRef.current = next;
      return next;
    });
    // 参考图清理
    setReferenceImages((prev) => {
      const next = prev.filter((c) => !all.has(c.id));
      if (next.length !== prev.length) referenceImagesRef.current = next;
      return next;
    });
    setLayoutNodes((prev) => {
      const next = { ...prev };
      for (const id of all) delete next[id];
      persistNow({ nodes: next, deletedIds: nextDeleted, cards: inspirationCardsRef.current, refs: referenceImagesRef.current });
      return next;
    });
    setCtxMenu(null);
  }, [persistNow]);
  const deleteNodesRef = useRef(deleteNodes);
  deleteNodesRef.current = deleteNodes;
  // 删除确认：涉及投影节点（img-/video-/audio-/wftool- 进黑名单；story- 删除对应消息）→ 先弹确认。
  // ★ 防"吞节点"：误删投影节点会被黑名单永久滤除（刷新也不回），必须让用户明确确认。
  const [deletePending, setDeletePending] = useState<{ ids: string[]; total: number; projected: number; story: number } | null>(null);
  const requestDelete = useCallback((ids: string[]) => {
    const story = ids.filter((i) => /^story-/.test(i)).length;
    const projected = ids.filter((i) => /^(img|video|audio|wftool)-/.test(i)).length;
    if (projected > 0 || story > 0) {
      setDeletePending({ ids, total: ids.length, projected, story });
      return;
    }
    pushUndo();
    deleteNodes(ids);
  }, [pushUndo, deleteNodes]);

  // 键盘快捷键：Delete 删除选中 / Ctrl+Z 撤销 / Ctrl+Y 重做 / Ctrl+A 全选
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    handleCanvasKeyDown(e, {
      onDelete: () => {
        const ids = rfNodesRef.current.filter((n) => n.selected).map((n) => n.id);
        if (ids.length > 0) { requestDelete(ids); }
      },
      onUndo: () => {
        const st = undo(undoStackRef.current);
        undoStackRef.current = st;
        // 过滤「消息已删」的剧情楼层：删除楼层 = 删除对话消息（不可撤销），undo 不得让其回魂。
        const liveIds = new Set(messagesRef.current.map((m) => m.id));
        setRfNodes(st.present.nodes.filter((n) => {
          const mid = n.data?.node?.storyMessageId;
          return !(n.data?.node?.type === "story" && mid && !liveIds.has(mid));
        }));
        setRfEdges(st.present.edges);
        setLayoutNodes(st.present.layout);
        setInspirationCards(st.present.cards);
        setReferenceImages(st.present.refs);
        // 恢复删除黑名单：删除的节点随撤销回到投影（防「吞节点」永久消失）
        setDeletedIds(st.present.deletedIds);
        deletedIdsRef.current = st.present.deletedIds;
        persistNow({ deletedIds: st.present.deletedIds });
      },
      onRedo: () => {
        const st = redo(undoStackRef.current);
        undoStackRef.current = st;
        const liveIds = new Set(messagesRef.current.map((m) => m.id));
        setRfNodes(st.present.nodes.filter((n) => {
          const mid = n.data?.node?.storyMessageId;
          return !(n.data?.node?.type === "story" && mid && !liveIds.has(mid));
        }));
        setRfEdges(st.present.edges);
        setLayoutNodes(st.present.layout);
        setInspirationCards(st.present.cards);
        setReferenceImages(st.present.refs);
        setDeletedIds(st.present.deletedIds);
        deletedIdsRef.current = st.present.deletedIds;
        persistNow({ deletedIds: st.present.deletedIds });
      },
      onSelectAll: () => {
        setRfNodes((ns) => ns.map((n) => ({ ...n, selected: true })));
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushUndo, requestDelete]);

  // 拖放外部图片/文件到画布 → 创建参考图节点
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  }, []);
  const onDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!IMAGE_EXTENSIONS.has(ext)) {
      showToast("仅支持拖放图片文件（png/jpg/webp/gif/bmp/avif）", "error");
      return;
    }
    // 上传到后端素材库，获得 local-view URL
    const conv = flowScreenRef.current;
    const pos = conv ? conv({ x: e.clientX, y: e.clientY }) : { x: e.clientX, y: e.clientY };
    const id = `ref-${crypto.randomUUID().slice(0, 8)}`;
    const REF_W = 280;
    const REF_H = 280;
    // 先创建占位卡（上传中），再异步填充
    const card: ReferenceImageStored = {
      id, title: file.name, imageUrl: "",
      x: pos.x - REF_W / 2, y: pos.y - REF_H / 2,
      w: REF_W, h: REF_H,
    };
    pushUndo();
    setReferenceImages((prev) => [...prev, card]);
    try {
      // 本地文件拖放：转 data URI 上传（后端 data: 分支豁免搜索结果候选校验，
      // 且 blob: 对象 URL 后端 urlopen 无法访问）
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("读取文件失败"));
        reader.readAsDataURL(file);
      });
      const res = await saveWebMaterial(settings.outputDir, dataUrl, "", file.name);
      setReferenceImages((prev) => prev.map((c) => c.id === id ? { ...c, imageUrl: res.url, title: res.title || file.name } : c));
      showToast(`已添加参考图「${res.title || file.name}」到画布`, "success");
      const next = referenceImagesRef.current.map((c) => c.id === id ? { ...c, imageUrl: res.url, title: res.title || file.name } : c);
      referenceImagesRef.current = next;
      persistNow({ refs: next });
    } catch (err) {
      showToast(`上传失败：${(err as Error).message}`, "error");
      setReferenceImages((prev) => prev.filter((c) => c.id !== id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.outputDir, pushUndo, persistNow]);


  // 右键画布空白 → 新建灵感卡（提交时生成 id 并加入列表）
  const createInspiration = useCallback(() => {
    if (!newInspiration) return;
    const id = `insp-${crypto.randomUUID().slice(0, 8)}`;
    // ★ 统一空位放置：不再用 maxY+24 行首（会压到生成结果网格）
    const pos = findFreeSpot(INSPIRATION_CARD_W, INSPIRATION_CARD_H);
    const card: InspirationCardStored = {
      id, kind: newInspiration.kind,
      title: newInspiration.title.trim() || "未命名",
      content: newInspiration.content,
      x: pos.x, y: pos.y,
      w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H,
    };
    setInspirationCards((prev) => [...prev, card]);
    setNewInspiration(null);
    setCtxMenu(null);
    const next = [...inspirationCardsRef.current, card];
    persistNow({ cards: next });
  }, [newInspiration, findFreeSpot, persistNow]);

  // M1.4：灵感卡发送画布——资产库派发 laf-inspiration-to-canvas，画布创建灵感卡节点
  useEffect(() => {
    const onInsp = (e: Event) => {
      const detail = (e as CustomEvent).detail as Array<{
        id?: string; title?: string; content?: string; imageUrl?: string;
      }> | undefined;
      if (!Array.isArray(detail) || detail.length === 0) return;
      const fresh: InspirationCardStored[] = [];
      for (const d of detail) {
        const title = (d.title || "").trim() || "灵感卡";
        const content = d.content || "";
        // 去重：同 id（资产库卡 id 稳定）已存在则跳过
        const sid = d.id && d.id.startsWith("insp-")
          ? d.id
          : `insp-${d.id || crypto.randomUUID().slice(0, 8)}`;
        const exists = inspirationCardsRef.current.some((c) => c.id === sid);
        if (exists) continue;
        const pos = findFreeSpot(INSPIRATION_CARD_W, INSPIRATION_CARD_H);
        fresh.push({
          id: sid, kind: "preset", title, content,
          imageUrl: d.imageUrl || "",
          x: pos.x, y: pos.y, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H,
        });
      }
      if (fresh.length === 0) return;
      setInspirationCards((prev) => [...prev, ...fresh]);
      const next = [...inspirationCardsRef.current, ...fresh];
      persistNow({ cards: next });
      showToast(`已发送 ${fresh.length} 张灵感卡到画布`, "success");
    };
    window.addEventListener("laf-inspiration-to-canvas", onInsp);
    return () => window.removeEventListener("laf-inspiration-to-canvas", onInsp);
  }, [findFreeSpot, persistNow]);

  // 组节点：编辑标题（双击组节点触发）→ 写回 layoutNodes[id].label（卡片渲染走 customLabel 优先）
  const saveGroupRename = useCallback(() => {
    if (!groupRenameId) return;
    setLayoutNodes((prev) => {
      const base = prev[groupRenameId] || { x: 0, y: 0, w: 200, h: 100 };
      const next = { ...prev, [groupRenameId]: { ...base, label: groupRenameTitle.trim() || "组" } };
      persistNow({ nodes: next });
      return next;
    });
    setGroupRenameId(null);
  }, [groupRenameId, groupRenameTitle, persistNow]);

  // 删除节点（连带其组内子节点 + 关联连线 + 布局记录）
  // ★ 同步清理各来源 state，否则投影重建时「复活」：
  //   - pendingInputs/pendingToolCreates（input/工具卡来源）
  //   - inspirationCards（灵感卡来源）
  //   - deletedIds 黑名单（gen 投影节点来源：generation_store，id 前缀 img-/video-/audio-）
  //   黑名单随 canvas.json 持久化——删除是显式操作，refresh 不得复活已删节点。

  // 编辑介绍文字（写入 layout.label，持久化到 canvas.json）
  const openEditLabel = useCallback((id: string, current: string) => {
    setEditLabelId(id);
    setEditLabelVal(current);
    setCtxMenu(null);
  }, []);
  const saveEditLabel = useCallback(() => {
    if (editLabelId) {
      setLayoutNodes((prev) => {
        const base = prev[editLabelId] || { x: 0, y: 0, w: CARD_W, h: 100 };
        const next = { ...prev, [editLabelId]: { ...base, label: editLabelVal.trim() } };
        persistNow({ nodes: next });
        return next;
      });
    }
    setEditLabelId(null);
    setEditLabelVal("");
  }, [editLabelId, editLabelVal, persistNow]);

  // 建立组：把选中的无父节点归入一个 group（虚线容器），拖动组整体移动子节点
  const createGroup = useCallback((ids: string[]) => {
    pushUndo();
    const members = rfNodesRef.current.filter((n) => ids.includes(n.id) && !n.parentId);
    if (members.length < 2) {
      setCtxMenu(null);
      return;
    }
    const groupId = `group-${crypto.randomUUID().slice(0, 8)}`;
    const minX = Math.min(...members.map((n) => n.position.x));
    const minY = Math.min(...members.map((n) => n.position.y));
    const maxX = Math.max(...members.map((n) => n.position.x + (
      (typeof n.style?.width === 'number' ? n.style.width : undefined)
        ?? n.measured?.width ?? CARD_W)));
    const maxY = Math.max(...members.map((n) => n.position.y + (
      (typeof n.style?.height === 'number' ? n.style.height : undefined)
        ?? n.measured?.height ?? 100)));
    const PAD = 24;
    const gx = minX - PAD;
    const gy = minY - PAD;
    const gw = Math.max(160, maxX - minX + PAD * 2);
    const gh = Math.max(100, maxY - minY + PAD * 2);
    const groupNode: Node<CardNodeData> = {
      id: groupId,
      type: "card",
      position: { x: gx, y: gy },
      style: { width: gw, height: gh },
      data: {
        node: { id: groupId, type: "group", x: gx, y: gy, w: gw, h: gh, generationIds: [] },
        gens: [], imageUrls: [], prompt: "组", customLabel: "组", isSel: false,
        naturalSize: undefined,
        onSelect: () => {},
        onOpen: (n: CanvasNode) => {
          // 双击组 → 弹编辑组名 modal
          const lyt = layoutNodesRef.current[n.id];
          setGroupRenameId(n.id);
          setGroupRenameTitle(lyt?.label || "组");
        },
        onResize: (id: string, w: number, h: number) => {
          // 组容器拉伸中：实时更新 style 跟随（子节点 extent=parent 随组缩放）
          setRfNodes((prev) => prev.map((n) => (n.id === id
          ? { ...n, style: { ...n.style, width: w, height: h }, data: { ...n.data, customSize: true } }
          : n)));
        },
        // 组容器拉伸结束才落 layoutNodes（避免投影重建 → 组与子节点关系抖动）
        onResizeEnd: (id: string, w: number, h: number) => {
          const base = layoutNodesRef.current[id] || { x: 0, y: 0, w: gw, h: gh };
          const next = { ...layoutNodesRef.current, [id]: { ...base, w, h, custom: true, label: base.label || "组" } };
          setLayoutNodes(next);
          persistNow({ nodes: next });
        },
        onImgLoaded: () => {},
        onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
          const rect = stageWrapRef.current?.getBoundingClientRect();
          openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
        },
        onGroupRename: (n: CanvasNode) => {
          const lyt = layoutNodesRef.current[n.id];
          setGroupRenameId(n.id);
          setGroupRenameTitle(lyt?.label || "组");
        },
      },
    };
    setRfNodes((ns) => [
      groupNode,
      ...ns.map((n) => (members.some((m) => m.id === n.id)
        ? { ...n, parentId: groupId, extent: "parent" as const, position: { x: n.position.x - gx, y: n.position.y - gy } }
        : n)),
    ]);
    setLayoutNodes((prev) => {
      const next = { ...prev, [groupId]: { x: gx, y: gy, w: gw, h: gh, custom: true, label: "组" } };
      // 子节点布局：相对组坐标 + parentId（刷新后还原组关系与位置）
      for (const m of members) {
        next[m.id] = {
          x: m.position.x - gx,
          y: m.position.y - gy,
          w: (typeof m.style?.width === 'number' ? m.style.width : undefined)
            ?? m.measured?.width ?? CARD_W,
          h: (typeof m.style?.height === 'number' ? m.style.height : undefined)
            ?? m.measured?.height ?? 100,
          parentId: groupId,
        };
      }
      persistNow({ nodes: next });
      return next;
    });
    setCtxMenu(null);
  }, [persistNow]);

  return (
    <div
      ref={stageWrapRef}
      onContextMenu={(e) => {
        // 兜底：事件冒泡到最外层仍未被处理时（ReactFlow 内部拦截/未命中节点），
        // 强制弹空白菜单并阻止浏览器菜单，保证右键必有反应
        e.preventDefault();
        const rect = stageWrapRef.current?.getBoundingClientRect();
        setCtxMenu((cur) => cur ?? {
          x: e.clientX - (rect?.left || 0),
          y: e.clientY - (rect?.top || 0),
          nodeIds: [],
        });
      }}
      style={{
        flex: 1, minHeight: 0, position: "relative",
        // 画布底色跟随主题（bright=浅色；夜间=深色），不再写死深色 fallback
        backgroundColor: "var(--bg, #f8f7fa)",
        // 点阵由 ReactFlow <Background> 渲染（画布单位 gap，随 zoom 缩放：
        // 放大点变疏、缩小点变密，给用户直观放缩反馈），颜色用主题 --dot
      }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChangeRaw}
        onEdgesChange={onEdgesChangeRaw}
        onConnect={onConnect}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onEdgeDoubleClick={onEdgeDoubleClick}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        onKeyDown={onKeyDown}
        onPaneClick={onPaneClick}
        onDragOver={onDragOver}
        onDrop={onDrop}
        onMoveEnd={(_e, vp) => { viewportRef.current = { x: vp.x, y: vp.y, scale: vp.zoom }; }}
        defaultViewport={{ x: savedViewport.x, y: savedViewport.y, zoom: savedViewport.scale }}
        // 左键空白拖=框选多选；中键=pan（右键留给功能栏菜单）
        selectionOnDrag
        panOnDrag={[1]}
        // 禁止选中时抬高 z-index，避免布局重算导致 pan 卡顿
        elevateNodesOnSelect={false}
        // 注意：不传 fitView——@xyflow/react v12 在 nodes 引用变化时会反复 fitView，
        // 重置 viewport 导致 minimap 的可视范围抖动 + 节点瞬间归位（看起来全黑）。
        // 首次进入时由 useReactFlow().fitView() 一次性调用即可（见下方 initFitViewRef）。
        minZoom={0.2}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
        style={{
          // 透明：点阵由外层 div 固定像素渲染（不随画布缩放改变密度）
          background: "transparent",
        }}
      >
        {/* 回到画布中心并入 Controls 面板（同款式同图层）；默认 Fit View 与其功能重复，由它替代 */}
        <Controls position="bottom-left" showFitView={false}>
          <CenterCanvasButton newNodeIds={newNodeIds} />
        </Controls>
        <MiniMap style={{ width: 120, height: 90 }} />
        {/* 点阵随画布 zoom 缩放（gap 为画布单位），颜色跟随主题 --dot */}
        <Background variant={BackgroundVariant.Lines} gap={22} size={1} color="var(--dot)" />
        <InitFitView delay={80} />
        <FlowBridge onReady={(fn) => { flowScreenRef.current = fn; }} />
        {/* 实时订阅 viewport → viewportRef（避免 GuidesOverlay 屏幕坐标偏位） */}
        <ViewportBridge onViewport={(vp) => { viewportRef.current = { x: vp.x, y: vp.y, scale: vp.zoom }; }} />
      </ReactFlow>

      {/* 辅助线：渲染在 ReactFlow 外部，按 viewportRef 实时坐标换算（ViewportBridge 持续刷新） */}
      <GuidesOverlay guides={guides} viewport={viewportRef.current} />

      {/* 剧情顺序工具条（顶部居中）：时序线开关 + 按顺序整理剧情楼层 */}
      <div className="canvas-story-tools">
        <button
          type="button"
          className={`canvas-story-tool${showStoryFlow ? " active" : ""}`}
          onClick={toggleShowStoryFlow}
          title="剧情顺序线：按楼层先后自动画紫色虚线箭头（区别于蓝色手动引用线，可随时隐藏）"
        >
          <GitBranch size={13} /> 剧情顺序线
        </button>
        <button
          type="button"
          className="canvas-story-tool"
          onClick={arrangeStoryOrder}
          title="把剧情楼层按先后顺序排成蛇形网格（其它节点保持原位）"
        >
          <ListOrdered size={13} /> 整理剧情
        </button>
      </div>

      {/* 右键功能栏（卡片右键：编辑介绍/删除；多选右键：建组/一起删除） */}
      {ctxMenu && (
        <div
          ref={ctxMenuRef}
          style={{
            position: "absolute", left: ctxMenu.x, top: ctxMenu.y, zIndex: 40,
            background: "var(--surface, #1e2126)", border: "1px solid var(--border, #333)",
            borderRadius: 8, boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
            padding: 4, minWidth: 172,
          }}
          onClick={(e) => { e.stopPropagation(); setCtxMenu(null); }}
          onContextMenu={(e) => e.preventDefault()}
        >
          {ctxMenu.nodeIds.length === 0 ? (
            <>
              <div
                className="ctx-item"
                onClick={() => {
                  setRfNodes((ns) => ns.map((n) => (n.selected ? { ...n, selected: false } : n)));
                }}
              >
                <X size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                全部取消选择
              </div>
              <div className="ctx-item" onClick={() => createGroup(rfNodesRef.current.filter((n) => n.selected).map((n) => n.id))}>
                <FolderPlus size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                选中节点建立组
              </div>
              <div className="ctx-sep" />
              <div
                className="ctx-item"
                onClick={() => setNewInspiration({ kind: "preset", title: "", content: "" })}
              >
                <MessageSquarePlus size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                新建灵感卡
              </div>
              <div className="ctx-item" onClick={() => void importInspirationFromLibrary(false)}>
                <Sparkles size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                {importBusy ? "导入中…" : "从素材库导入灵感卡"}
              </div>
            </>
          ) : ctxMenu.nodeIds.length === 1 ? (
            (() => {
              const n = rfNodesRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
              if (n?.data.node.type === "inspiration-card") {
                return (
                  <>
                    <div className="ctx-item" onClick={() => {
                      const c = inspirationCardsRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                      if (c) void insertInspirationToChat(c);
                      setCtxMenu(null);
                    }}>
                      <Send size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                      插入到对话
                    </div>
                    <div className="ctx-item" onClick={() => {
                      const c = inspirationCardsRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                      if (c) { try { void navigator.clipboard.writeText(c.content); showToast("内容已复制", "success"); } catch { showToast("复制失败", "error"); } }
                      setCtxMenu(null);
                    }}>
                      <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                      复制文本
                    </div>
                    <div className="ctx-item" onClick={() => {
                      const c = inspirationCardsRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                      if (c) {
                        setInspirationEditId(c.id);
                        setInspirationEditKind(c.kind);
                        setInspirationEditTitle(c.title);
                        setInspirationEditContent(c.content);
                      }
                      setCtxMenu(null);
                    }}>
                      <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                      编辑标题/内容
                    </div>
                    <div className="ctx-item danger" onClick={() => removeInspiration(ctxMenu.nodeIds[0])}>
                      <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                      删除灵感卡
                    </div>
                  </>
                );
              }
              if (n?.data.node.type === "reference-image") {
                return (
                  <>
                    <div className="ctx-item" onClick={() => {
                      const ref = referenceImagesRef.current.find((x) => x.id === ctxMenu.nodeIds[0]);
                      if (ref) {
                        setInspirationEditId(ref.id);
                        setInspirationEditKind("preset");
                        setInspirationEditTitle(ref.title);
                      }
                      setCtxMenu(null);
                    }}>
                      <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                      编辑标题
                    </div>
                    <div className="ctx-item danger" onClick={() => {
                      const next = referenceImagesRef.current.filter((c) => c.id !== ctxMenu.nodeIds[0]);
                      setReferenceImages(next);
                      referenceImagesRef.current = next;
                      persistNow({ refs: next });
                      setCtxMenu(null);
                    }}>
                      <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                      删除参考图
                    </div>
                  </>
                );
              }
              if (n?.data.node.type === "group") {
                return (
                  <>
                    <div className="ctx-item" onClick={() => {
                      const lyt = layoutNodesRef.current[n.id];
                      setGroupRenameId(n.id);
                      setGroupRenameTitle(lyt?.label || "组");
                      setCtxMenu(null);
                    }}>
                      <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                      编辑组名
                    </div>
                    <div className="ctx-item" onClick={() => {
                        setCtxMenu(null);
                        // 删除组但保留子节点：父组链断开，子节点坐标转绝对
                        const groupId = ctxMenu.nodeIds[0];
                        const childIds = rfNodesRef.current
                          .filter((n) => n.parentId === groupId)
                          .map((n) => n.id);
                        setRfNodes((ns) => ns.map((n) => {
                          if (n.id === groupId) return n;
                          if (childIds.includes(n.id)) {
                            const parent = ns.find((pn) => pn.id === groupId);
                            const px = parent?.position?.x ?? 0;
                            const py = parent?.position?.y ?? 0;
                            return { ...n, parentId: undefined, position: { x: n.position.x + px, y: n.position.y + py } };
                          }
                          return n;
                        }));
                        // 也从 layoutNodes 移除组容器，且子节点同步 parentId 置空 + 坐标转绝对
                        // （否则刷新后投影按 layoutNodes 恢复会把子节点挂回已删的组 → 崩坏）
                        setLayoutNodes((prev) => {
                          const next = { ...prev };
                          const group = prev[groupId];
                          delete next[groupId];
                          if (group) {
                            for (const [id, lyt] of Object.entries(prev)) {
                              if (lyt.parentId === groupId) {
                                next[id] = {
                                  ...lyt,
                                  x: lyt.x + group.x,
                                  y: lyt.y + group.y,
                                  parentId: undefined,
                                };
                              }
                            }
                          }
                          // ★ persist 必须在 updater 内拿 next：外层同步读 layoutNodesRef 是旧值
                          //   （ref 仅在渲染时更新）→ 会把删除前的布局存回去，刷新后组复活。
                          //   StrictMode 下 updater 双跑 → persist 幂等重复无害。
                          persistNow({ nodes: next });
                          return next;
                        });
                      }}>
                        <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                        删除组（保留内容）
                      </div>
                      <div className="ctx-item danger" onClick={() => { requestDelete(ctxMenu.nodeIds); }}>
                        <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                        删除组（连带子节点）
                      </div>
                  </>
                );
              }
              return (
                <>
                  <div
                    className="ctx-item"
                    onClick={() => {
                      if (n && n.data.node.type !== "group") {
                        openEditLabel(n.id, n.data.customLabel || n.data.prompt || "");
                      }
                    }}
                  >
                    <Edit3 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                    编辑介绍文字
                  </div>
                  <div className="ctx-item danger" onClick={() => { requestDelete(ctxMenu.nodeIds); }}>
                    <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                    删除节点
                  </div>
                </>
              );
            })()
          ) : (
            <>
              <div className="ctx-item" onClick={() => createGroup(ctxMenu.nodeIds)}>
                <FolderPlus size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                建立组（{ctxMenu.nodeIds.length} 个节点）
              </div>
              <div className="ctx-item danger" onClick={() => { requestDelete(ctxMenu.nodeIds); }}>
                <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                一起删除（{ctxMenu.nodeIds.length}）
              </div>
            </>
          )}
        </div>
      )}

      {/* 编辑卡片介绍文字 */}
      {editLabelId && (
        <div className="modal-mask" onClick={() => setEditLabelId(null)}>
          <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3>编辑卡片介绍文字</h3>
            <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 8px" }}>
              显示在卡片底部的一行文字（留空则恢复默认：提示词 / 图组张数）
            </p>
            <input
              value={editLabelVal}
              onChange={(e) => setEditLabelVal(e.target.value)}
              autoFocus
              placeholder="例如：主角初登场（金发红裙）"
              style={{
                width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
                border: "1px solid var(--border, #333)",
                background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
                fontSize: 13, fontFamily: "inherit",
              }}
            />
            <div className="modal-actions">
              <button className="btn" onClick={() => setEditLabelId(null)}>取消</button>
              <button className="btn primary" onClick={saveEditLabel}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* 编辑灵感卡（左图右文：左侧图片 + 左下角删除/替换；右侧文本编辑） */}
      {inspirationEditId && (() => {
        const editCard = inspirationCardsRef.current.find((c) => c.id === inspirationEditId) || null;
        const editImage = editCard?.imageUrl || "";
        return (
          <div className="modal-mask" onClick={() => setInspirationEditId(null)}>
            <div className="modal" style={{ maxWidth: 760 }} onClick={(e) => e.stopPropagation()}>
              <h3>编辑灵感卡</h3>
              <div style={{ display: "flex", gap: 16 }}>
                {/* 左：图片 + 左下角按钮 */}
                <div style={{ flex: "0 0 220px", display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{
                    width: 220, height: 220, borderRadius: 10, overflow: "hidden",
                    background: "rgba(255,255,255,0.03)", border: "1px solid var(--border, #333)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                  }}>
                    {editImage ? (
                      <img src={editImage} alt={inspirationEditTitle || "灵感卡"} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
                    ) : (
                      <span style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "center", padding: 8 }}>
                        无图片（纯文本卡）
                      </span>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "nowrap" }}>
                    <button
                      className="btn"
                      style={{ flex: 1, minWidth: 0, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 4, whiteSpace: "nowrap", padding: "6px 8px" }}
                      onClick={() => inspImageFileRef.current?.click()}
                    >
                      <Upload size={13} />替换图片
                    </button>
                    <button
                      className="btn danger"
                      style={{ flex: 1, minWidth: 0, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 4, whiteSpace: "nowrap", padding: "6px 8px" }}
                      onClick={removeInspirationImage}
                      disabled={!editImage}
                    >
                      <Trash2 size={13} />删除图片
                    </button>
                  </div>
                  <input
                    ref={inspImageFileRef} type="file" accept="image/*" style={{ display: "none" }}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void replaceInspirationImage(f);
                      e.target.value = "";
                    }}
                  />
                </div>
                {/* 右：类型/标题/内容 */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                    {(["character", "worldbook-entry", "preset", "table-row"] as InspirationKind[]).map((k) => {
                      const meta = INSPIRATION_META[k];
                      return (
                        <button
                          key={k}
                          className={`btn ${inspirationEditKind === k ? "primary" : ""}`}
                          onClick={() => setInspirationEditKind(k)}
                          style={{ flex: 1, borderLeft: `3px solid ${meta.color}` }}
                        >
                          {meta.icon} {meta.label}
                        </button>
                      );
                    })}
                  </div>
                  <input
                    value={inspirationEditTitle}
                    onChange={(e) => setInspirationEditTitle(e.target.value)}
                    autoFocus
                    placeholder="标题（显示在卡片头部）"
                    style={{
                      width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
                      border: "1px solid var(--border, #333)", marginBottom: 8,
                      background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
                      fontSize: 13, fontFamily: "inherit",
                    }}
                  />
                  <textarea
                    value={inspirationEditContent}
                    onChange={(e) => setInspirationEditContent(e.target.value)}
                    placeholder="内容（双击卡片 → 插入对话即把这段内容推送到对话流）"
                    rows={10}
                    style={{
                      width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
                      border: "1px solid var(--border, #333)", resize: "vertical",
                      background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
                      fontSize: 13, fontFamily: "inherit", lineHeight: 1.5,
                    }}
                  />
                  <div className="modal-actions">
                    <button className="btn" onClick={() => setInspirationEditId(null)}>取消</button>
                    <button className="btn primary" onClick={saveInspirationEdit}>保存</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {/* 新建灵感卡（右键空白 → 新建灵感卡） */}
      {newInspiration && (
        <div className="modal-mask" onClick={() => setNewInspiration(null)}>
          <div className="modal" style={{ maxWidth: 560 }} onClick={(e) => e.stopPropagation()}>
            <h3>新建灵感卡</h3>
            <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
              {(["character", "worldbook-entry", "preset", "table-row"] as InspirationKind[]).map((k) => {
                const meta = INSPIRATION_META[k];
                return (
                  <button
                    key={k}
                    className={`btn ${newInspiration.kind === k ? "primary" : ""}`}
                    onClick={() => setNewInspiration({ ...newInspiration, kind: k })}
                    style={{ flex: 1, borderLeft: `3px solid ${meta.color}` }}
                  >
                    {meta.icon} {meta.label}
                  </button>
                );
              })}
            </div>
            <input
              value={newInspiration.title}
              onChange={(e) => setNewInspiration({ ...newInspiration, title: e.target.value })}
              autoFocus
              placeholder="标题（显示在卡片头部）"
              style={{
                width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
                border: "1px solid var(--border, #333)", marginBottom: 8,
                background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
                fontSize: 13, fontFamily: "inherit",
              }}
            />
            <textarea
              value={newInspiration.content}
              onChange={(e) => setNewInspiration({ ...newInspiration, content: e.target.value })}
              placeholder="内容（双击卡片 → 插入对话即把这段内容推送到对话流）"
              rows={10}
              style={{
                width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
                border: "1px solid var(--border, #333)", resize: "vertical",
                background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
                fontSize: 13, fontFamily: "inherit", lineHeight: 1.5,
              }}
            />
            <div className="modal-actions">
              <button className="btn" onClick={() => setNewInspiration(null)}>取消</button>
              <button className="btn primary" onClick={createInspiration}>创建</button>
            </div>
          </div>
        </div>
      )}

      {/* 组重命名（双击/右键组节点） */}
      {groupRenameId && (
        <div className="modal-mask" onClick={() => setGroupRenameId(null)}>
          <div className="modal" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3>编辑组名</h3>
            <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 8px" }}>
              类似 ComfyUI 组的标题（左键拖标题栏移动整组，右下角可缩放）
            </p>
            <input
              value={groupRenameTitle}
              onChange={(e) => setGroupRenameTitle(e.target.value)}
              autoFocus
              placeholder="组名"
              style={{
                width: "100%", padding: 8, borderRadius: 6, boxSizing: "border-box",
                border: "1px solid var(--border, #333)",
                background: "var(--input-bg, rgba(0,0,0,0.2))", color: "var(--text)",
                fontSize: 13, fontFamily: "inherit",
              }}
            />
            <div className="modal-actions">
              <button className="btn" onClick={() => setGroupRenameId(null)}>取消</button>
              <button className="btn primary" onClick={saveGroupRename}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* 实时吸附辅助线已由 ReactFlow 内部 <GuidesOverlay> 渲染（见组件定义） */}

      {loading && gens.length === 0 && (
        <div className="empty-state" style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", zIndex: 5 }}>
          <Sparkles size={28} strokeWidth={1.4} style={{ opacity: 0.5 }} />
          <p>加载生成记录…</p>
        </div>
      )}

      {/* 删除确认：涉及投影节点（story- 删除对应消息 / 其余进 deletedIds 黑名单）→ 必须确认 */}
      {deletePending && (
        <ConfirmModal
          title="从画布删除节点"
          message={`将 ${deletePending.total} 个节点从画布移除${
            deletePending.story > 0
              ? `；其中 ${deletePending.story} 个为剧情楼层，删除后对话中对应消息也会一并删除（不可撤销）`
              : ""
          }${deletePending.projected > 0
              ? `；其中 ${deletePending.projected} 个为生成内容/工作流节点（资产库中的图片不会删除，Ctrl+Z 可恢复）`
              : ""
          }。`}
          confirmText="删除"
          danger
          onConfirm={() => {
            pushUndo();
            deleteNodes(deletePending.ids);
            setDeletePending(null);
            showToast(
              deletePending.story > 0
                ? `已删除 ${deletePending.total} 个节点（含 ${deletePending.story} 个剧情楼层，对应消息已删除）`
                : `已从画布移除 ${deletePending.total} 个节点（Ctrl+Z 可撤销）`,
              "success",
            );
          }}
          onCancel={() => setDeletePending(null)}
        />
      )}

      {/* 详情面板（双击节点） */}
      {detail && detailNode && (
        <div className="modal-mask" onClick={() => setDetailNode(null)}>
          <div className="modal" style={{
            width: detailNode.type === "story" ? "92vw" : 760,
            maxWidth: detailNode.type === "story" ? 1200 : undefined,
            height: detailNode.type === "story" ? "86vh" : undefined,
            maxHeight: "88vh",
            display: "flex", flexDirection: "column",
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <h3 style={{ margin: 0 }}>{detail.title}</h3>
              <button className="icon-btn" style={{ background: "transparent", color: "var(--text)" }} onClick={() => setDetailNode(null)}>
                <X size={18} />
              </button>
            </div>
            {detailNode.type === "story" ? (
              <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 16, overflow: "hidden" }}>
                {/* 左侧：媒体（视频 > 图片；无则空置提示，不占位） */}
                <div style={{ flex: "0 0 40%", minWidth: 260, maxWidth: 540, display: "flex", flexDirection: "column", gap: 8, overflowY: "auto" }}>
                  {detailNode.storyVideo && (
                    <video src={detailNode.storyVideo} controls style={{ width: "100%", borderRadius: 10, background: "#000" }} />
                  )}
                  {detailNode.storyImage && (
                    <img src={detailNode.storyImage} alt="剧情封面" style={{ width: "100%", borderRadius: 10, objectFit: "contain", background: "rgba(255,255,255,0.03)" }} />
                  )}
                  {(detailNode.storyAudioLines?.length ? detailNode.storyAudioLines : (detailNode.storyAudio ? [{ speaker: "", url: detailNode.storyAudio }] : [])).map((line, i) => (
                    <div className="audio-bubble" key={`${line.url}-${i}`}>
                      {line.speaker && <div className="audio-speaker-label">{line.speaker}</div>}
                      <AudioPlayer src={line.url} />
                    </div>
                  ))}
                  {!detailNode.storyVideo && !detailNode.storyImage && !detailNode.storyAudioLines?.length && !detailNode.storyAudio && (
                    <div style={{ width: "100%", aspectRatio: "9/16", borderRadius: 10, background: "rgba(255,255,255,0.03)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 13, textAlign: "center", padding: 12 }}>
                      本楼层无配图 / 视频 / 音频
                    </div>
                  )}
                </div>
                {/* 右侧：对话正文（think/status/roll 与对话模式同管线） */}
                <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 8, overflowY: "auto", paddingRight: 4 }}>
                  {detailNode.storyThinking ? (
                    <div className="thinking">
                      <button className="thinking-head" onClick={() => setShowStoryThinking((s) => !s)}>
                        {showStoryThinking ? "收起思考" : "查看思考"}
                      </button>
                      {showStoryThinking && <div className="thinking-body">{detailNode.storyThinking}</div>}
                    </div>
                  ) : null}
                  <div className="bot-text bot-html" style={{ fontSize: 14, lineHeight: 1.7, color: "var(--text)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(
                      (displayRegex && displayRegex.length > 0 && detailNode.storyText)
                        ? runScripts(detailNode.storyText, Placement.AI_OUTPUT, displayRegex, { isMarkdown: true, depth: 0 })
                        : (detailNode.storyText || "（空楼层）"),
                    ) }} />
                </div>
              </div>
            ) : (
            <div style={{ overflowY: "auto", display: "flex", gap: 16, flexWrap: "wrap" }}>
              {/* 左侧：主预览（图/视频/音频/输入按类型） */}
              <div style={{ flex: "1 1 300px", minWidth: 240 }}>
                {detailNode.type === "video" ? (
                  <video src={(detail.gens[0] as { video_url?: string } | undefined)?.video_url || ""} controls
                    style={{ width: "100%", borderRadius: 10, background: "#000" }} />
                ) : detailNode.type === "audio" ? (
                  <div style={{ width: "100%", aspectRatio: "16/9", borderRadius: 10, background: "rgba(255,255,255,0.03)",
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                    <span style={{ fontSize: 24 }}>🎵</span>
                    <span style={{ fontSize: 13, color: "var(--text-muted)" }}>音频节点（波形预览未接入）</span>
                  </div>
                ) : detailNode.type === "input" ? (
                  <div style={{ width: "100%", aspectRatio: "9/16", borderRadius: 10, background: "rgba(59,130,246,0.08)",
                    border: "1px dashed var(--primary, #3b82f6)", display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 13, color: "var(--primary, #3b82f6)" }}>
                    {detailNode.inputStatus === "draft" ? "输入节点（双击卡片输入提示词）" : "输入节点（生成中…）"}
                  </div>
                ) : detailNode.type === "reference-image" ? (
                  detail.imageUrls.length > 0 ? (
                    <img src={detail.imageUrls[0]} alt={detailNode.referenceImageTitle || "参考图"}
                      style={{ width: "100%", borderRadius: 10, background: "rgba(255,255,255,0.03)", objectFit: "contain" }} />
                  ) : (
                    <div style={{ width: "100%", aspectRatio: "1/1", borderRadius: 10, background: "rgba(255,255,255,0.03)",
                      display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13, color: "var(--text-muted)" }}>
                      🖼️ 参考图加载中…
                    </div>
                  )
                ) : detail.imageUrls[0] ? (
                  <>
                    <img src={detail.imageUrls[0]} alt="大图" style={{ width: "100%", borderRadius: 10 }} />
                    {(() => {
                      const s = naturalSizesRef.current[detail.imageUrls[0]];
                      return s ? (
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6, textAlign: "center" }}>
                          原图尺寸 {s.w} × {s.h}
                        </div>
                      ) : null;
                    })()}
                  </>
                ) : null}
              </div>
              {/* 右侧：元数据 */}
              <div style={{ flex: "1 1 260px", minWidth: 220 }}>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>提示词</div>
                <div style={{ fontSize: 13, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 240, overflowY: "auto" }}>
                  {detail.prompt || "（无提示词记录）"}
                </div>
                {(() => {
                  const first = detail.gens[0] as (GenLike & { description?: string }) | undefined;
                  if (!first) return null;
                  const tags = (Array.isArray(first.tags) ? first.tags : []).filter(Boolean);
                  const time = first.created_at ? new Date(first.created_at).toLocaleString() : "";
                  const templateName = first.templateName || "";
                  const modelName = first.modelName || "";
                  // loraNames 后端存为逗号分隔字符串，拆成数组展示
                  const loraNames: string[] = Array.isArray(first.loraNames) ? first.loraNames : [];
                  return (
                    <>
                      {tags.length > 0 && (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>标签</div>
                          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                            {tags.slice(0, 24).map((t) => (
                              <span key={t} style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: "rgba(59,130,246,0.12)", color: "var(--text)" }}>{t}</span>
                            ))}
                          </div>
                        </>
                      )}
                      {templateName ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>模板</div>
                          <div style={{ fontSize: 13, color: "var(--text)" }}>{templateName}</div>
                        </>
                      ) : null}
                      {modelName ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>模型</div>
                          <div style={{ fontSize: 12, color: "var(--text)", fontFamily: "monospace" }}>{modelName}</div>
                        </>
                      ) : null}
                      {(detailNode.type === "video" || detailNode.type === "audio") && (
                        <>
                          {first.resolution ? (
                            <>
                              <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>清晰度</div>
                              <div style={{ fontSize: 13, color: "var(--text)" }}>{first.resolution}</div>
                            </>
                          ) : null}
                          {first.duration ? (
                            <>
                              <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>时长</div>
                              <div style={{ fontSize: 13, color: "var(--text)" }}>{first.duration}</div>
                            </>
                          ) : null}
                          {first.emotionVectors ? (
                            <>
                              <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>情感向量</div>
                              <div style={{ fontSize: 13, color: "var(--text)" }}>{first.emotionVectors}</div>
                            </>
                          ) : null}
                          {first.referenceContent ? (
                            <>
                              <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>参考内容</div>
                              <div style={{ fontSize: 12, lineHeight: 1.5, color: "var(--text)" }}>{first.referenceContent}</div>
                            </>
                          ) : null}
                        </>
                      )}
                      {loraNames.length > 0 ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>LoRA</div>
                          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                            {loraNames.map((name: string) => (
                              <span key={name} style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: "rgba(168,85,247,0.14)", color: "var(--text)" }}>{name}</span>
                            ))}
                          </div>
                        </>
                      ) : null}
                      {first.description ? (
                        <>
                          <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "12px 0 4px" }}>描述</div>
                          <div style={{ fontSize: 12, lineHeight: 1.5, color: "var(--text-muted)" }}>{first.description}</div>
                        </>
                      ) : null}
                      {time && (
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 12 }}>生成时间：{time}</div>
                      )}
                    </>
                  );
                })()}
                {/* 每条生成独立节点（8-21 拍板）→ 无变体聚合，详情面板不再展示「全部变体」 */}
                <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button className="btn" onClick={() => openSend("发送至对话框", { text: "", images: detail.imageUrls.slice(0, 1) })}>
                    <Send size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话框
                  </button>
                  <button className="btn" onClick={() => openSend("发送至对话", { text: `提示词：${detail.prompt || ""}`, images: detail.imageUrls.slice(0, 1), prompt: detail.prompt || "" })}>
                    <Send size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />发送至对话
                  </button>
                  <button className="btn danger" onClick={() => { requestDelete([detailNode.id]); setDetailNode(null); }}>
                    <Trash2 size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />删除
                  </button>
                </div>
              </div>
            </div>
            )}
          </div>
        </div>
      )}

      {sendTarget && <SendToChatModal title={sendTarget.title} payload={sendTarget.payload} onDone={() => setSendTarget(null)} onCancel={() => setSendTarget(null)} />}
      {/* 连线引用关系（双击连线） */}
      {edgeInfo && (
        <div className="modal-mask" onClick={() => setEdgeInfo(null)}>
          <div className="modal" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
            {edgeInfo.storyFrom !== undefined ? (
              <>
                <h3>剧情顺序线</h3>
                <p style={{ fontSize: 13, lineHeight: 1.6, margin: 0 }}>
                  第 <b>{edgeInfo.storyFrom}</b> 段 → 第 <b>{edgeInfo.storyTo}</b> 段
                </p>
                <p style={{ margin: "10px 0 0", fontSize: 11, color: "var(--text-muted)" }}>
                  按对话剧情顺序自动派生（楼层增删后自动更新），非手动连线，不可删除。
                </p>
              </>
            ) : (
              (() => {
                const src = rfNodes.find((n) => n.id === edgeInfo.source);
                const tgt = rfNodes.find((n) => n.id === edgeInfo.target);
                const promptOf = (n?: Node<CardNodeData>) => n?.data?.prompt || n?.data?.node?.prompt || "（无提示词）";
                return (
                  <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                    <p style={{ margin: 0, color: "var(--text-muted)", fontSize: 12 }}>
                      {edgeInfo.target} → 引用 → {edgeInfo.source}
                    </p>
                    <p style={{ margin: "10px 0 2px" }}>◀ source（被引用）：</p>
                    <p style={{ margin: 0, color: "var(--text-muted)", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 120, overflowY: "auto" }}>
                      {src ? promptOf(src) : "（节点已不存在）"}
                    </p>
                    <p style={{ margin: "10px 0 2px" }}>▶ target（引用者）：</p>
                    <p style={{ margin: 0, color: "var(--text-muted)", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 120, overflowY: "auto" }}>
                      {tgt ? promptOf(tgt) : "（节点已不存在）"}
                    </p>
                    <p style={{ margin: "10px 0 0", fontSize: 11, color: "var(--text-muted)" }}>
                      语义：如视频引用底图、图生图引用参考图。当前为视觉连线，references 数据模型未与生成链路打通。
                    </p>
                  </div>
                );
              })()
            )}
            <div className="modal-actions">
              <button className="btn primary" onClick={() => setEdgeInfo(null)}>知道了</button>
            </div>
          </div>
        </div>
      )}
      {/* Toast 弹窗：非实际产出的状态提示（提交/失败/复制等），2s 自动渐隐消失 */}
      <ToastLayer toasts={toasts} />

      {/* 世界书弹窗（双击 worldbook-entry 灵感卡：编辑仓库快照，与源库隔离） */}
      {wbRepoLoc && (
        <WorldBookPopup
          location={{ base: "", name: "" }}
          repoLoc={wbRepoLoc}
          title={wbPopupTitle || "世界书条目"}
          onClose={() => { setWbRepoLoc(null); setWbPopupTitle(""); }}
        />
      )}

      {/* 角色卡弹窗（双击 character 灵感卡：预览/编辑描述，只写画布本地，不同步源库） */}
      {charModal && (
        <CanvasCharacterModal
          base={settings.characterDir || ""}
          name={charModal.name}
          initialContent={charModal.content}
          onSave={(content) => {
            const next = inspirationCardsRef.current.map((c) =>
              c.id === charModal.cardId ? { ...c, content } : c,
            );
            setInspirationCards(next);
            persistNow({ cards: next });
          }}
          onClose={() => setCharModal(null)}
        />
      )}

      {/* 预设弹窗（双击 preset 灵感卡 → 打开偏置预设） */}
      {presetModalOpen && (
        <PresetModal
          base={settings.presetDir || ""}
          activeName={boundPreset}
          onSelectActive={(name) => onSelectActivePreset?.(name)}
          onClose={() => setPresetModalOpen(false)}
        />
      )}

      {/* 工作流工具编辑器（双击 workflow-tool 节点） */}
      {toolModalNode && (
        <WorkflowToolModal
          node={toolModalNode.node}
          autoConfirm={toolModalNode.autoConfirm}
          repoId={repoId}
          settings={settings}
          onClose={() => setToolModalNode(null)}
          onGenerated={onGenerated}
          onUpdate={(updates) => {
            const tn = toolModalNode.node;
            // ★ 同步更新 toolModalNode：弹窗 node prop 必须即时反映最新状态（wfConfirmed/wfDraft/wfCaptured），
            // 否则 WorkflowCard key 不变不重渲——「更改」点击无反应，要退出重进才生效。
            setToolModalNode((prev) => (prev ? { ...prev, node: { ...prev.node, ...updates } } : prev));
            // 把工具卡状态写回画布节点（用于卡片徽标实时更新）
            setRfNodes((ns) => ns.map((n) => (
              n.id === tn.id ? { ...n, data: { ...n.data, node: { ...n.data.node, ...updates } } } : n
            )));
            // 同步写进 layoutNodes（确保持久化不丢，含 wfConfirmed/wfDraft/wfCaptured；
            // 并补 templateId/templateName——切画布再回按此恢复工具卡，否则节点消失）
            setLayoutNodes((prev) => {
              const base = prev[tn.id] || {
                x: tn.x, y: tn.y, w: 240, h: 180,
              };
              const next = { ...prev, [tn.id]: {
                ...base, ...updates,
                templateId: tn.templateId,
                templateName: tn.templateName,
              } };
              persistNow({ nodes: next });
              return next;
            });
          }}
          onNotify={(msg) => showToast(msg)}
        />
      )}
    </div>
  );
}
