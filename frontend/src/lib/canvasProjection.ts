// canvasProjection.ts — 画布「节点投影 / 连线派生」builder（单一属主）
//
// 原内联在 `views/CanvasStageFlow.tsx` 的投影 useEffect 里。T2.1-A 先把「占位/临时节点」
// （生成中占位 / 待输入占位 / 工作流运转占位）收口到 `lib/canvasPlaceholderNodes.ts`，
// 本模块收口剩下**把 state 映射成 ReactFlow nodes/edges** 的全部构造与判定：
//
//   · `buildContentNodes`        —— 生成内容节点（持久化布局 / 锚点网格坐标 / 组关系）
//   · `buildToolCards`           —— 工作流工具卡（对话投影 ① → 事件待创建 ② → 已持久化 ③ 三源合并去重）
//   · `buildStoryNodes`          —— 剧情楼层（封面或 9:16、原位替换锚点、首次放置待落盘位置）
//   · `buildInspirationNodes`    —— 灵感卡（独立持久化坐标；双击按 kind 分派 5 类弹窗）
//   · `buildReferenceImageNodes` —— 参考图（独立持久化坐标）
//   · `mergeProjectedNodes`      —— 投影合并判定（保留选中 / ReactFlow 实际坐标与父子关系 / 内容未变返回原引用）
//   · `buildCanvasEdges`         —— 连线派生（手动引用线过滤 + 自动剧情顺序线）
//
// 结构性事实：
//   · 这些 builder **都不落盘**；持久化由调用方传入的 `persistNow` 回调承担。
//   · **只有** `buildStoryNodes` 会写 ref（`pendingStoryLayoutsRef` 首次投影待落盘位置、
//     `placeholderConsumedByStoryRef` 标记原位替换锚点已被消费）—— 均为改造前既有行为，逐字搬移；
//     `buildCanvasEdges` 把自动线 id 集合**返回**给调用方写 ref（builder 自身无副作用）。
//   · `mergeProjectedNodes` 是**纯函数**（入参出参，无 ref 无 setter）：它是全模块唯一的
//     「投影合并判定」真源，`mergeProjectedNodes(prev, next)` 返回 `prev` 原引用即代表
//     「内容未变、React 不重渲染」——这条去重语义不能改为 `next` 或新数组。
import type { RefObject } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import { CARD_W, INSPIRATION_CARD_W, INSPIRATION_CARD_H, type CardNodeData } from "../components/canvas/CanvasTypes";
import type { CanvasNode, GenLike, InspirationKind } from "./canvasRuntime";
import type { InspirationCardStored, NodeLayout, ReferenceImageStored } from "./canvasLayout";
import type { CanvasPersistOverrides, PendingToolCreate } from "./useCanvasEventBridge";
import type { OpenCtxForNode } from "./canvasPlaceholderNodes";
import type { RepoWorldbookLoc } from "../api/worldbook";
import type { RegexScript } from "./regexEngine";
import type { Settings } from "../stores/settings";
import { projectStoryNodes, projectWorkflowTools } from "./canvasConversation";
import type { ChatMessage } from "../types/chat";

/** ReactFlow 节点数组（`data` 为画布卡片数据）——本模块所有 builder 的统一返回形状。 */
type CardNodes = Node<CardNodeData>[];
/** `useNodesState` 的 setter：builder 内只以「函数式更新」调用（保留选中 / 组坐标）。 */
type SetCardNodes = (updater: (prev: CardNodes) => CardNodes) => void;
/** `useState` 布局表的 setter：**两种调用形态都有**（直接传新表 / 函数式更新，见工具卡「更改」分支）。 */
type SetLayoutNodes = (
  v: Record<string, NodeLayout> | ((prev: Record<string, NodeLayout>) => Record<string, NodeLayout>),
) => void;
/** 节点矩形（`findFreeSpot` 的空位避让入参 / 返回值）。 */
type Rect = { x: number; y: number; w: number; h: number };
/** 非生成内容节点（新内容节点锚点网格占位）的坐标表。 */
type ContentPlacement = { map: Record<string, { x: number; y: number }> } | null;
// ── deps 形状（只列 builder 真正引用的组件级符号；模块级 import 不重复注入）─────────
export interface ContentNodesDeps {
  rawNodes: CanvasNode[];
  layoutNodes: Record<string, NodeLayout>;
  contentPlacement: ContentPlacement;
  byGroup: Map<string, GenLike[]>;
  naturalSizesRef: RefObject<Record<string, { w: number; h: number }>>;
  layoutNodesRef: RefObject<Record<string, NodeLayout>>;
  stageWrapRef: RefObject<HTMLDivElement | null>;
  openCtxForNode: OpenCtxForNode;
  persistNow: (overrides?: CanvasPersistOverrides) => void;
  setDetailNode: (v: CanvasNode | null) => void;
  setInspirationEditId: (v: string | null) => void;
  setInspirationEditKind: (v: InspirationKind) => void;
  setInspirationEditTitle: (v: string) => void;
  setInspirationEditContent: (v: string) => void;
  setLayoutNodes: SetLayoutNodes;
  setRfNodes: SetCardNodes;
}

export interface ToolCardsDeps {
  messages: ChatMessage[];
  deletedIdSet: Set<string>;
  layoutNodes: Record<string, NodeLayout>;
  pendingToolCreates: PendingToolCreate[];
  findFreeSpot: (w: number, h: number, extra?: Rect[]) => { x: number; y: number };
  settings: Settings;
  layoutNodesRef: RefObject<Record<string, NodeLayout>>;
  stageWrapRef: RefObject<HTMLDivElement | null>;
  openCtxForNode: OpenCtxForNode;
  persistNow: (overrides?: CanvasPersistOverrides) => void;
  runWorkflowTask: (nn: CanvasNode, runId: string) => void;
  setLayoutNodes: SetLayoutNodes;
  setRfNodes: SetCardNodes;
  setToolModalNode: (v: { node: CanvasNode; autoConfirm: boolean } | null) => void;
}

export interface StoryNodesDeps {
  streamingId: string | null | undefined;
  messages: ChatMessage[];
  layoutNodes: Record<string, NodeLayout>;
  /** 显示层正则（剧情正文在 CardNodeComponent 里跑正则，与对话模式同款管线） */
  displayRegex: RegexScript[] | undefined;
  layoutNodesRef: RefObject<Record<string, NodeLayout>>;
  stageWrapRef: RefObject<HTMLDivElement | null>;
  openCtxForNode: OpenCtxForNode;
  persistNow: (overrides?: CanvasPersistOverrides) => void;
  setDetailNode: (v: CanvasNode | null) => void;
  setLayoutNodes: SetLayoutNodes;
  setRfNodes: SetCardNodes;
  setShowStoryThinking: (v: boolean) => void;
  findFreeSpot: (w: number, h: number, extra?: Rect[]) => { x: number; y: number };
  /** 生成中占位落点缓存：剧情楼层**接替**其位置后置 `placeholderConsumedByStoryRef`（防陈旧锚点污染） */
  streamingPosRef: RefObject<{ id: string; x: number; y: number } | null>;
  placeholderConsumedByStoryRef: RefObject<boolean>;
  /** 剧情楼层首次放置的待落盘位置（由调用方的持久化 effect 消费） */
  pendingStoryLayoutsRef: RefObject<Record<string, { x: number; y: number; w: number; h: number }>>;
}

export interface InspirationNodesDeps {
  inspirationCards: InspirationCardStored[];
  inspirationCardsRef: RefObject<InspirationCardStored[]>;
  settings: Settings;
  repoId: string;
  stageWrapRef: RefObject<HTMLDivElement | null>;
  openCtxForNode: OpenCtxForNode;
  persistNow: (overrides?: CanvasPersistOverrides) => void;
  setRfNodes: SetCardNodes;
  setInspirationCards: (v: InspirationCardStored[]) => void;
  setInspirationEditId: (v: string | null) => void;
  setInspirationEditKind: (v: InspirationKind) => void;
  setInspirationEditTitle: (v: string) => void;
  setInspirationEditContent: (v: string) => void;
  setWbSeedFrom: (v: { base: string; name: string } | null) => void;
  setWbRepoLoc: (v: RepoWorldbookLoc | null) => void;
  setWbPopupTitle: (v: string) => void;
  setCharModal: (v: { name: string; cardId: string; content: string } | null) => void;
  setPresetModalOpen: (v: boolean) => void;
  insertInspirationToChat: (card: InspirationCardStored) => void;
  showToast: (msg: string, kind: "info" | "error" | "success") => void;
}

export interface ReferenceImageNodesDeps {
  referenceImages: ReferenceImageStored[];
  referenceImagesRef: RefObject<ReferenceImageStored[]>;
  setReferenceImages: (v: ReferenceImageStored[]) => void;
  setRfNodes: SetCardNodes;
  setDetailNode: (v: CanvasNode | null) => void;
  persistNow: (overrides?: CanvasPersistOverrides) => void;
  stageWrapRef: RefObject<HTMLDivElement | null>;
  openCtxForNode: OpenCtxForNode;
}

export interface CanvasEdgesDeps {
  inputs: CardNodes;
  toolCards: CardNodes;
  storyNodes: CardNodes;
  projected: CardNodes;
  referenceImageNodes: CardNodes;
  layoutEdges: { source: string; target: string }[];
  /** 自动剧情顺序线开关（`showStoryFlowRef.current`：ref 取值 → 不重订阅） */
  showStoryFlowRef: RefObject<boolean>;
}
// ── buildContentNodes ─────────────────────────────────────────────────

/**
 * 生成内容节点投影（生成结果 → 卡片节点）。
 * 坐标优先持久化布局（`layoutNodes`），新节点用锚点网格（`contentPlacement`）算出的坐标；
 * 组内子节点写相对坐标 + `parentId/extent`（刷新后还原组关系）；手动 resize 过 → 锁定 w/h。
 */
export function buildContentNodes(deps: ContentNodesDeps): Node<CardNodeData>[] {
  const { rawNodes, layoutNodes, contentPlacement, byGroup, naturalSizesRef, layoutNodesRef,
    stageWrapRef, openCtxForNode, persistNow, setDetailNode, setInspirationEditId,
    setInspirationEditKind, setInspirationEditTitle, setInspirationEditContent, setLayoutNodes,
    setRfNodes } = deps;
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
        // 画布节点优先渲染缩略图（P0-2 派生层）；详情/全屏仍用 imageUrls 原图
        thumbUrls: gensFor.map((g) => g.thumbUrl || g.thumb_url || ""),
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
  return projected;
}
// ── buildToolCards ────────────────────────────────────────────────────

/**
 * ★ 工作流工具卡：三源合并 + 同模板去重后构造节点。
 * ① 对话消息投影（主源：重启可靠）→ ② 事件待创建（`/w` 刚发、消息还没进的兜底）
 * → ③ `layoutNodes` 已持久化工具卡（旧数据 / 切画布再回）。三源共用 `seenTemplate`
 * 去重（投影优先，保证 id 稳定可恢复布局）；已删除 id 不复活。
 * 同批次新卡用 `newToolExtrasRef` 互相避让空位。
 */
export function buildToolCards(deps: ToolCardsDeps): Node<CardNodeData>[] {
  const { messages, deletedIdSet, layoutNodes, pendingToolCreates, findFreeSpot, settings,
    layoutNodesRef, stageWrapRef, openCtxForNode, persistNow, runWorkflowTask, setLayoutNodes,
    setRfNodes, setToolModalNode } = deps;
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
  return toolCards;
}
// ── buildStoryNodes ───────────────────────────────────────────────────

/**
 * ★ 剧情楼层节点投影（每个楼层一条剧情文本消息 → 一个节点）。
 * 封面 = 剧情自动插画（有图 → 左图右文；无图 → 9:16 竖版）。正文渲染跑显示层正则（`displayRegex`）。
 * 生成中的消息（`streamingId`）跳过投影，改显示占位节点，避免被误判为已完成楼层。
 * 首帧位置：**接替生成中占位节点的原位置**（本次渲染直接读 `streamingPosRef`，不等 effect 转存）；
 * 否则 `findFreeSpot` 找空位。两种情况都写 `pendingStoryLayoutsRef`（首次放置即持久化，防漂移）。
 */
export function buildStoryNodes(deps: StoryNodesDeps): Node<CardNodeData>[] {
  const { streamingId, messages, layoutNodes, displayRegex, layoutNodesRef, stageWrapRef,
    openCtxForNode, persistNow, setDetailNode, setLayoutNodes, setRfNodes, setShowStoryThinking,
    findFreeSpot, streamingPosRef, placeholderConsumedByStoryRef, pendingStoryLayoutsRef } = deps;
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
  return storyNodes;
}
// ── buildInspirationNodes ─────────────────────────────────────────────

/**
 * 灵感卡节点（独立持久化：位置直接来自 `inspirationCards`，不走 `layoutNodes`）。
 * 双击按 `kind` 分派：带图 / `sourceRef="conv"` → 左图右文编辑弹窗；世界书 → 仓库快照弹窗；
 * 角色卡 → 角色卡弹窗；预设 → 偏置预设弹窗；其余 → 本地编辑。
 * 拉伸结束写回 `inspirationCards` + 落盘（拉伸中只改 `rfNodes` style，避免投影重建闪烁）。
 */
export function buildInspirationNodes(deps: InspirationNodesDeps): Node<CardNodeData>[] {
  const { inspirationCards, inspirationCardsRef, settings, repoId, stageWrapRef, openCtxForNode,
    persistNow, setRfNodes, setInspirationCards, setInspirationEditId, setInspirationEditKind,
    setInspirationEditTitle, setInspirationEditContent, setWbSeedFrom, setWbRepoLoc,
    setWbPopupTitle, setCharModal, setPresetModalOpen, insertInspirationToChat, showToast } = deps;
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
        // ★ 资产库发送来的灵感卡（带图）/ 对话自动投影的灵感卡（sourceRef="conv"）
        //   → 打开「左图右文」编辑弹窗（可删图/替换图），不误入偏置预设
        if (c.imageUrl || c.sourceRef === "conv") {
          setInspirationEditId(c.id);
          setInspirationEditKind(c.kind);
          setInspirationEditTitle(c.title);
          setInspirationEditContent(c.content);
          return;
        }
        if (c.kind === "worldbook-entry" && c.sourceRef) {
          // 画布模式：双击打开仓库快照世界书（编辑即改快照，与源库隔离）
          if (settings.outputDir && repoId) {
                            const wbName = c.sourceRef?.startsWith("wb:") ? c.sourceRef.slice(3) : "";
              const wbDir = settings.worldbookDir || settings.characterDir || "";
              setWbSeedFrom(wbName && wbDir ? { base: wbDir, name: wbName } : null);
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
  return inspirationNodes;
}
// ── buildReferenceImageNodes ──────────────────────────────────────────

/**
 * 参考图节点（独立持久化：位置直接来自 `referenceImages`，不走 `layoutNodes`）。
 * 拉伸结束写回 `referenceImages` + 落盘。
 */
export function buildReferenceImageNodes(deps: ReferenceImageNodesDeps): Node<CardNodeData>[] {
  const { referenceImages, referenceImagesRef, setReferenceImages, setRfNodes, setDetailNode,
    persistNow, stageWrapRef, openCtxForNode } = deps;
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
  return referenceImageNodes;
}
// ── mergeProjectedNodes ───────────────────────────────────────────────

/**
 * ★ 投影合并判定（**纯函数**）：把新投影的 `nextNodes` 合并进当前 `prevNodes`。
 * · 保留用户选中状态（框选/单击），避免 useEffect 重跑强制 `selected:false` 取消选中；
 * · 保留 ReactFlow 实际坐标（含组内相对坐标）与父子关系（`parentId/extent`），
 *   否则组内子节点会被投影回绝对坐标 → 组崩坏；
 * · 内容（id / position / selected / 组关系 / 尺寸 / `customSize` / **`data` 整体**）未变时
 *   返回 `prevNodes` **原引用** → React 不重渲染 → 不闪。
 *   `data` 必须比较：编辑器写回 `wfCaptured/wfConfirmed` 后若返回旧数组，卡片回调拿到的 `nn`
 *   是旧数据 → 「运转工作流」首次点击因 `wfCaptured` 空而静默失败（要点两下）。
 */
export function mergeProjectedNodes(
  prevNodes: Node<CardNodeData>[],
  nextNodes: Node<CardNodeData>[],
): Node<CardNodeData>[] {
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
}
// ── buildCanvasEdges ──────────────────────────────────────────────────

/**
 * ★ 连线派生：手动引用线（持久化布局恢复，node 已不存在则过滤）+ 自动剧情顺序线。
 * 自动线是**派生数据**（按剧情顺序连接相邻楼层）：不落盘、用户删不掉，用紫色虚线 + 箭头
 * 与手动线视觉区分；返回的 `autoEdgeIds` 由调用方写 `autoEdgeIdsRef`（供删除保护 / 落盘过滤用）。
 */
export function buildCanvasEdges(deps: CanvasEdgesDeps): { manualEdges: Edge[]; autoEdges: Edge[]; autoEdgeIds: Set<string> } {
  const { inputs, toolCards, storyNodes, projected, referenceImageNodes, layoutEdges,
    showStoryFlowRef } = deps;
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
  // 自动线 id 集合交回调用方写 ref（builder 自身无副作用）
  const autoEdgeIds = new Set(autoEdges.map((e) => e.id));
  return { manualEdges, autoEdges, autoEdgeIds };
}
