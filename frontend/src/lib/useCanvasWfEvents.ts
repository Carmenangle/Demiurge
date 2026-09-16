// useCanvasWfEvents.ts — 画布「运转任务」四事件的订阅桥（单一属主）
//
// 原内联在 `views/CanvasStageFlow.tsx` 的 useEffect 里（改造前 64 行）。这里只收
// `laf-canvas-wf-run` / `-run-graph` / `-progress` / `-done` 四个窗口事件的**订阅与字段映射**；
// 「列表增删」仍在 `lib/wfRunList.ts`（P12 收口），本模块只负责「新项长什么样」与「订阅时机」。
//
// ⚠ 重订阅时机是本接缝的**行为面**：effect 依赖数组仍是 `[placePlaceholder]`（与改造前逐字一致）——
// `wfRunsRef` / `pendingAnchorRef` 是 ref、`setWfRuns` 是 setState setter，身份天然稳定，
// 所以把 effect 搬进 hook **不改变**订阅/解绑时机。**不要**把 refs 或 setter 加进依赖数组。
//
// ⚠ 两处**刻意差异**（由 `wfRunList.contract.test.ts` 钉住，不要顺手统一）：
//   · 新占位落点：`progress` 路径在**事务内**用 `prev` 算（同一拍内连续两次事件各自避让到不同空位），
//     `run` 路径沿用 `wfRunsRef.current`（既有差异）；
//   · `node` 字段映射：`d.node !== undefined ? d.node : r.node`（空串视为有效值）。
//
// ⚠ `done` 仍记「原位替换锚点」（写 `pendingAnchorRef`）：接缝 `wfRunList` 只管按 id 移除、
// 不认识锚点——这条边界由合约锁断言，别把锚点下沉到 wfRunList。

import { useEffect, type Dispatch, type RefObject, type SetStateAction } from "react";
import { CARD_W } from "../components/canvas/CanvasTypes";
import type { PlacedRect } from "./canvasRuntime";
import { addWfRun, dropWfRun, updateWfRun, upsertWfRun } from "./wfRunList";
import type { WfRun } from "./canvasPlaceholderNodes";
/** 占位落点：画布 placePlaceholder 只返回坐标（宽高由占位节点自己决定）*/export type PlaceholderPos = { x: number; y: number };


/** 订阅桥的依赖：全部由调用方（画布组件）持有，本模块不自持状态。 */
export interface CanvasWfEventDeps {
  /** 当前运转任务列表：`onRun` 用它的矩形避让，`onDone` 用它找原位替换锚点 */
  wfRunsRef: RefObject<WfRun[]>;
  /** 占位节点位置 → 内容节点原位替换锚点 */
  pendingAnchorRef: RefObject<PlacedRect | null>;
  /** 占位落点计算（画布组件内 useCallback）——hook 的 effect 依赖数组里就是它 */
  placePlaceholder: (w: number, h: number, extra?: PlacedRect[]) => PlaceholderPos;
  setWfRuns: Dispatch<SetStateAction<WfRun[]>>;
}

/**
 * 订阅 `laf-canvas-wf-*` 四个窗口事件，并归约到画布的运转任务列表（占位节点）。
 * ⚠ effect 依赖数组**保持 `[placePlaceholder]` 不变**（改它就会改变重订阅时机）。
 */
export function useCanvasWfEvents(deps: CanvasWfEventDeps): void {
  const { wfRunsRef, pendingAnchorRef, placePlaceholder, setWfRuns } = deps;
  useEffect(() => {
    const onRun = (e: Event) => {
      const d = (e as CustomEvent).detail as { runId?: string; toolNodeId?: string; templateName?: string } | undefined;
      if (!d?.runId) return;
      // 以最新内容节点为锚点展开（生成中占位也遵从此原则）
      const pos = placePlaceholder(CARD_W, 427, wfRunsRef.current.map((r) => ({ x: r.x, y: r.y, w: CARD_W, h: 427 })));
      // 列表增删收归单一属主 `lib/wfRunList`（P12）：本组件只管「新项长什么样」
      setWfRuns((prev) => addWfRun(prev, d.runId!, () => ({
        id: d.runId!, toolNodeId: d.toolNodeId || "", templateName: d.templateName || "工作流",
        promptId: "", progress: null, x: pos.x, y: pos.y,
      })));
    };
    const onProg = (e: Event) => {
      const d = (e as CustomEvent).detail as { taskId?: string; promptId?: string; progress?: number | null; node?: string; templateName?: string } | undefined;
      if (!d?.taskId) return;
      // upsert：编辑器弹窗路径只发 progress 事件（不发 run），占位节点同样要出现
      setWfRuns((prev) => upsertWfRun(
        prev, d.taskId!,
        (r) => ({
          ...r,
          promptId: d.promptId || r.promptId,
          progress: d.progress !== undefined ? d.progress : r.progress,
          node: d.node !== undefined ? d.node : r.node,
          templateName: d.templateName || r.templateName,
        }),
        () => {
          // 新占位的落点在事务内算：占用矩形必须取自 `prev`（而非 `wfRunsRef`），
          // 否则同一拍内连续两次事件会各自算到同一个「空位」。
          const pos = placePlaceholder(CARD_W, 427, prev.map((r) => ({ x: r.x, y: r.y, w: CARD_W, h: 427 })));
          return {
            id: d.taskId!, toolNodeId: "", templateName: d.templateName || "工作流",
            promptId: d.promptId || "", progress: d.progress !== undefined ? d.progress : null,
            node: d.node || undefined, x: pos.x, y: pos.y,
          };
        },
      ));
    };
    const onDone = (e: Event) => {
      const d = (e as CustomEvent).detail as { taskId?: string } | undefined;
      if (!d?.taskId) return;
      // ★ 原位替换：占位节点清除时记录其位置，内容节点到达后落在原位（垂直居中对齐）
      const run = wfRunsRef.current.find((r) => r.id === d.taskId);
      if (run) {
        pendingAnchorRef.current = { x: run.x, y: run.y, w: CARD_W, h: 427 };
      }
      setWfRuns((prev) => dropWfRun(prev, d.taskId!));
    };
    // 提交后携带 API prompt 图：占位节点据此显示真实提示词/节点名（对齐对话模式）
    const onRunGraph = (e: Event) => {
      const d = (e as CustomEvent).detail as { runId?: string; graph?: unknown } | undefined;
      if (!d?.runId) return;
      setWfRuns((prev) => updateWfRun(prev, d.runId!, (r) => ({ ...r, graph: d.graph })));
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
}
