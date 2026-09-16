// wfRunList.ts — 「按 id 增量维护的运转任务列表」的归约核心（**单一属主**）
//
// 2026-09-15 架构审计 P12（F2）：`views/ChatView.tsx` 的 `canvasWfRuns`（对话框下方进度条）
// 与 `views/CanvasStageFlow.tsx` 的 `wfRuns`（画布「生成中」占位节点）**各自手写了一套
// 一模一样的事件 → 列表归约**：同样是 `laf-canvas-wf-run` / `-progress` / `-done` 三个事件，
// 同样是「不存在就追加、存在就合并、done 就按 id 移除」，两套各约 25 行。
// 两个属主意味着**任一处漏同步都不报错**，而且已经出现事实性漂移（见下）。
//
// 这里只收**列表增删这一层**（真正重复且易漂移的部分）；各调用方的**字段映射**与
// **额外副作用**留在原处 —— 它们本来就不一样，不属于「重复」：
//   · 标签来源：ChatView 优先 `nodeLabel`（`d.nodeLabel || d.node`），画布占位直接用 `d.node`；
//   · 画布多 `x/y`（空位放置）、`graph`（占位节点显示真实提示词）、`toolNodeId`、`promptId`；
//   · 画布 `onDone` 还要记「原位替换锚点」。
// ⚠ 上面两条**是刻意差异**（行为面由 `wfRunList.test.ts` 的等价矩阵钉住），**不要顺手统一**。
//
// 引用语义（**不得放宽**，否则会引入无意义 re-render）：
// · `addWfRun` / `upsertWfRun` / `dropWfRun` 在「无事发生」时返回**入参原引用**——
//   这正是改造前四处手写 `prev.some(...) ? prev : …` 的写法；
// · `updateWfRun` 与原 `onRunGraph` 一致，**总是**返回新数组（逐项 map）。

/** 列表项的最小约束：能被 id 定位。 */
export interface WfRunLike {
  id: string;
}

/** 不存在则追加；**已存在 → 原引用返回**（幂等 create，如 `laf-canvas-wf-run`）。 */
export function addWfRun<T extends WfRunLike>(list: T[], id: string, create: () => T): T[] {
  return list.some((run) => run.id === id) ? list : [...list, create()];
}

/** 就地合并已存在项；**不存在 → 原引用返回，不新建**（如 `laf-canvas-wf-run-graph`）。 */
export function updateWfRun<T extends WfRunLike>(list: T[], id: string, merge: (run: T) => T): T[] {
  return list.map((run) => (run.id === id ? merge(run) : run));
}

/** 合并或新增：存在 → 合并；不存在 → 用 `create()` 追加（如 `laf-canvas-wf-progress` 的 upsert）。 */
export function upsertWfRun<T extends WfRunLike>(
  list: T[], id: string, merge: (run: T) => T, create: () => T,
): T[] {
  return list.some((run) => run.id === id)
    ? list.map((run) => (run.id === id ? merge(run) : run))
    : [...list, create()];
}

/** 按 id 移除；**不存在 → 原引用返回**（如 `laf-canvas-wf-done`）。 */
export function dropWfRun<T extends WfRunLike>(list: T[], id: string): T[] {
  return list.some((run) => run.id === id) ? list.filter((run) => run.id !== id) : list;
}
