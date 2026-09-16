// useCanvasEventBridge.ts — 画布「事件桥」的其余五处订阅（单一属主，T2.1-B2）
//
// 改造前这些订阅内联在 `views/CanvasStageFlow.tsx` 里，散在 5 个 effect（合计 ~100 行）：
//   ① 详情面板正文 <details> 的展开态跟随（元素级 `toggle`，零解绑）
//   ② `laf-generation-saved` + 「首次进入先拉对话历史媒体 URL 再投影」
//   ③ `laf-canvas-workflow-tool`（`/w` 选模板 → 建工作流工具卡）
//   ④ 右键菜单「点外关闭」（`mousedown` **捕获**）
//   ⑤ `CANVAS_INSPIRATION_EVENT` + 「挂载时先消费积压」（灵感卡落地）
//
// ⚠ **一订阅一 hook**，且在组件里**原地替换**（hook 调用点 = 原 effect 的位置）⇒ ①②④⑤ 的相对顺序
//   与改造前那 4 个 effect 一致，「effect 执行顺序」这一隐性行为面按构造保持不变。
//   ⚠ **例外 = ③**：调用点**必须**落在 `rfNodesRef` 声明之后 —— hook 把 ref 当**实参立即求值**，
//   而改造前它只在事件回调闭包里被引用（TDZ 安全）。于是 ③ 比原来晚了 3 个 effect（那两个
//   `[pendingToolCreates]` 的纯 setState 反应式 effect）。**惰性可证**：③ 的 effect 体同步只做一次
//   `addEventListener`，不写 state、不读 pendingToolCreates ⇒ 与任何 effect 的相对顺序都不可观测。
//
// ⚠ 每个 hook 的 **effect 依赖数组逐字保持改造前的值** —— 那就是各接缝的**行为面**（重订阅时机）：
//   ① `[detailStoryText]`（原 `[detailNode?.storyText]`）② `[refresh, loadHistoryUrls]`
//   ③ `[]` ④ `[ctxMenu]` ⑤ `[findFreeSpot, persistNow]`。
//   依赖里的 ref / setState setter 身份天然稳定 ⇒ 把 effect 搬进 hook 不改变订阅/解绑时机。
//   **不要**把 refs / setter / showToast 顺手加进依赖数组。
//
// ⚠ 与 `lib/useCanvasWfEvents.ts`（T2.1-B1）的分工：那边持有 `laf-canvas-wf-*` 四个事件
//   （与 `lib/wfRunList.ts`（P12）同属「运转任务列表归约」一条链）；本模块持有其余五处。
//   两边事件名不重叠、互不调用 ⇒ 各自仍是单一属主；**不要**把任何一个搬来搬去。
//
// ⚠ 四处**保留的既有形态**（`useCanvasEventBridge.test.tsx` 钉住，别顺手统一）：
//   · ② 的 `alive` 只护住「挂载时的首次拉取」，事件回调**不检查**它（改造前就是这样）；
//   · ③ 的去重判据是「画布节点 ∨ 本地队列」**两处都查**（与派发侧 5a 判据同源）；
//   · ④ 的监听带 `capture: true`，且用 `if (!ctxMenu) return;` 让「菜单没开就不挂监听」；
//   · ⑤ 的 `exists` 去重按**派生 id**（补 `insp-` 前缀之后）比对，而不是原始 `d.id`。

import { useEffect, useLayoutEffect, type Dispatch, type RefObject, type SetStateAction } from "react";
// ⚠ 别名 `FlowNode`：hook ④ 里要用 **DOM 的全局 `Node`**（`e.target instanceof Node`），
//   若这里直接 `import type { Node }`，值位置会撞上 type-only 导入（TS1361）。
import type { Node as FlowNode } from "@xyflow/react";
import { INSPIRATION_CARD_W, INSPIRATION_CARD_H, type CardNodeData } from "../components/canvas/CanvasTypes";
import { CANVAS_INSPIRATION_EVENT, consumePendingInspirations } from "./inspirationInsert";
import type { InspirationCardStored, NodeLayout, ReferenceImageStored } from "./canvasLayout";
import { restoreOpenDetails, trackDetailsToggle } from "./stableDetailsOpen";

/** 工作流工具卡待建队列项（`/w` 事件派发路径；对话消息投影路径见 `lib/canvasConversation.ts`）。 */
export interface PendingToolCreate {
  id: string; templateId: string; templateName: string; estimatedNodeCount: number;
}

/** 画布右键菜单状态（节点右键 `openCtxForNode` / 空白右键 `onPaneContextMenu` 共用）。 */
export interface CtxMenuState { x: number; y: number; nodeIds: string[]; }

/** `persistNow` 的覆盖项（本模块只用到 `cards`，其余字段照抄组件侧签名以保持类型单一属主）。 */
export interface CanvasPersistOverrides {
  nodes?: Record<string, NodeLayout>;
  edges?: { source: string; target: string }[];
  cards?: InspirationCardStored[];
  refs?: ReferenceImageStored[];
  deletedIds?: string[];
}

type ToastKind = "info" | "error" | "success";

// ─────────────────────────────────────────────────────────────────────────────
// ① 详情面板正文 <details> 展开态跟随
// ─────────────────────────────────────────────────────────────────────────────
export interface DetailToggleDeps {
  /** 详情面板正文容器（组件里直接挂到 JSX 的 `ref`） */
  detailHtmlRef: RefObject<HTMLDivElement | null>;
  /** 已展开的 key 集合（跨重渲染记住） */
  detailOpenKeys: RefObject<Set<string>>;
  /** **依赖数组里的那一项**（原 `[detailNode?.storyText]`） */
  detailStoryText: string | undefined;
}

/**
 * 详情面板正文里被正则折叠的 `<details>`（思考过程）在重渲染重写后保持展开状态（同对话模式）。
 * ⚠ **`useLayoutEffect`**：必须在浏览器重排/重绘前恢复展开态，别改成 `useEffect`；
 * ⚠ 元素级监听用 `dataset.detailsBound` 防重复绑定，且**从不解绑**（随该 DOM 元素生命周期）。
 */
export function useDetailToggleTracking(deps: DetailToggleDeps): void {
  const { detailHtmlRef, detailOpenKeys, detailStoryText } = deps;
  useLayoutEffect(() => {
    const el = detailHtmlRef.current;
    if (!el) return;
    restoreOpenDetails(el, detailOpenKeys.current);
    if (!el.dataset.detailsBound) {
      el.dataset.detailsBound = "1";
      el.addEventListener("toggle", (e) => trackDetailsToggle(e.target, detailOpenKeys.current), true);
    }
  }, [detailStoryText]);
}

// ─────────────────────────────────────────────────────────────────────────────
// ② `laf-generation-saved` + 首次拉取对话历史
// ─────────────────────────────────────────────────────────────────────────────
export interface GenerationSavedDeps {
  /** 重拉生成记录（组件内 `refresh`，依赖 `[repoId, embed, conversationUrlSignature]`） */
  refresh: (urlsOverride?: Set<string>) => Promise<void>;
  /** 拉当前对话的媒体 URL 集合（持久化真源） */
  loadHistoryUrls: () => Promise<Set<string>>;
}

/**
 * 挂载时先拉对话历史媒体 URL 再投影 + 订阅 `laf-generation-saved`（实时更新兜底）。
 * ⚠ 依赖仍是 `[refresh, loadHistoryUrls]`。
 */
export function useCanvasGenerationSaved(deps: GenerationSavedDeps): void {
  const { refresh, loadHistoryUrls } = deps;
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
}

// ─────────────────────────────────────────────────────────────────────────────
// ③ `laf-canvas-workflow-tool`（/w 选模板 → 工作流工具卡）
// ─────────────────────────────────────────────────────────────────────────────
export interface WorkflowToolCreateDeps {
  /** 画布当前节点（去重用：同模板卡已在画布上则只弹提示） */
  rfNodesRef: RefObject<FlowNode<CardNodeData>[]>;
  /** 本地待建队列（去重用：同一模板的卡还在队列里也算已存在） */
  pendingToolCreatesRef: RefObject<PendingToolCreate[]>;
  setPendingToolCreates: Dispatch<SetStateAction<PendingToolCreate[]>>;
  /** 重复派发时提示「同模板只保留一张卡片」（用户可见文案由组件侧单一属主） */
  showToast: (msg: string, kind?: ToastKind) => void;
}

/**
 * 订阅 `laf-canvas-workflow-tool`：`/w` 选模板后由 ChatView 派发，画布创建工具型节点。
 * ⚠ 依赖仍是 `[]`（回调只读 refs + setter，身份稳定）。
 */
export function useCanvasWorkflowToolCreate(deps: WorkflowToolCreateDeps): void {
  const { rfNodesRef, pendingToolCreatesRef, setPendingToolCreates, showToast } = deps;
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
}

// ─────────────────────────────────────────────────────────────────────────────
// ④ 右键菜单「点外关闭」
// ─────────────────────────────────────────────────────────────────────────────
export interface CtxMenuDismissDeps {
  /** 菜单当前是否打开 —— **依赖数组里就是它**（菜单没开不挂监听） */
  ctxMenu: CtxMenuState | null;
  ctxMenuRef: RefObject<HTMLDivElement | null>;
  setCtxMenu: Dispatch<SetStateAction<CtxMenuState | null>>;
}

/** 点击菜单外部任意处 → 关闭菜单（标准 context menu 行为，无需点「关闭」）。依赖仍是 `[ctxMenu]`。 */
export function useCtxMenuOutsideDismiss(deps: CtxMenuDismissDeps): void {
  const { ctxMenu, ctxMenuRef, setCtxMenu } = deps;
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
}

// ─────────────────────────────────────────────────────────────────────────────
// ⑤ 灵感卡落地（`CANVAS_INSPIRATION_EVENT` + 挂载时消费积压）
// ─────────────────────────────────────────────────────────────────────────────
export interface InspirationDropDeps {
  inspirationCardsRef: RefObject<InspirationCardStored[]>;
  setInspirationCards: Dispatch<SetStateAction<InspirationCardStored[]>>;
  findFreeSpot: (
    w: number, h: number,
    extra?: Array<{ x: number; y: number; w: number; h: number }>,
  ) => { x: number; y: number };
  persistNow: (overrides?: CanvasPersistOverrides) => void;
  showToast: (msg: string, kind?: ToastKind) => void;
}

/**
 * 灵感卡发送画布：对话灵感卡/素材库统一走「缓存 + 通知」通道
 * （画布未挂载时事件不丢；挂载时先消费积压，之后通知增量消费）。
 * ⚠ 依赖仍是 `[findFreeSpot, persistNow]`。
 */
export function useCanvasInspirationDrop(deps: InspirationDropDeps): void {
  const { inspirationCardsRef, setInspirationCards, findFreeSpot, persistNow, showToast } = deps;
  useEffect(() => {
    const createFromPayloads = (items: Array<{
      id?: string; title?: string; content?: string; imageUrl?: string;
    }>) => {
      if (!items || items.length === 0) return;
      const fresh: InspirationCardStored[] = [];
      for (const d of items) {
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
    // 挂载时先消费积压（发送时画布未挂载的场景）
    createFromPayloads(consumePendingInspirations());
    const onInsp = () => createFromPayloads(consumePendingInspirations());
    window.addEventListener(CANVAS_INSPIRATION_EVENT, onInsp);
    return () => window.removeEventListener(CANVAS_INSPIRATION_EVENT, onInsp);
  }, [findFreeSpot, persistNow]);
}
