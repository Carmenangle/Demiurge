// foregroundRunQueue.ts — 「前台运转任务 FIFO」的归约核心（**单一属主**）
//
// 2026-09-15 架构审计 P12（F2）：`lib/useChatSession.ts` 里同一个「出队并判断是否已清空」
// 的判定被**手写了 5 遍**（5 个终态回调各一份 `activePromptQueue.current =
// activePromptQueue.current.filter(id => id !== pending.prompt_id)` + `if (length === 0)`），
// 入队判定又写了一遍（`push` + `if (length === 1)`）。任一处写错都不报错：
// 队列非空时进度的 WS 订阅会漏退订/漏切换，队列该空时不空则「运转中」永远不结束。
//
// 本模块只负责**队列本身**（入队 / 出队 / 是否首个 / 是否已空闲 / 新队首）。
// **每个终态各自的尾巴留在调用方** —— 它们本来就不同，是行为而非重复：
//   · `completed`：空闲 → 停进度 + 清慢守望 + 派发 workflowDone；**非空闲 → 把 WS 订阅切到新队首**；
//   · `failed` / `stalled`：空闲 → 停进度 + 清慢守望 + 派发 workflowDone（**不切换订阅**）；
//   · `released`：空闲 → 停进度 + 派发 workflowDone（**不清慢守望**，随后反而要置它）；
//   · `timedOut`：前台任务只在空闲时清慢守望（**不停进度、不派发**）。
// ⚠ 上述差异由 `foregroundRunQueue.test.ts` 逐格钉住，**不要顺手统一**。
//
// ⚠ 为什么**不**改成「从 `runtime.list()` 投影」（清单原建议的方向，实测否决）：
//   `runtime.pending` 是**持久化记录**，不是「在跑的前台任务」——
//   `timedOut` 终态**不移除** pending（`keepPlaceholder` 语义：任务转后台继续，刷新后据此恢复），
//   `released` 也不移除。若按 `list()` 投影，「超时/转后台」的任务会被算成仍在跑
//   ⇒ 进度条永不结束。故队列必须**独立存在**，本模块只保证它只有一个属主。

/** 入队结果：新队列 + 该任务是否为**首个**前台任务（只有首个接管进度条 WS）。 */
export interface ForegroundJoin {
  queue: string[];
  first: boolean;
}

/** 出队结果：新队列 + 是否已**空闲** + 新队首（仅 `completed` 用它切换订阅）。 */
export interface ForegroundLeave {
  queue: string[];
  idle: boolean;
  head: string | undefined;
}

/** 入队（原 `activePromptQueue.current.push(...)` + `length === 1` 判定）。 */
export function joinForeground(queue: string[], promptId: string): ForegroundJoin {
  const next = [...queue, promptId];
  return { queue: next, first: next.length === 1 };
}

/** 出队（原 5 处 `filter(id => id !== promptId)` + `length === 0` 判定）。 */
export function leaveForeground(queue: string[], promptId: string): ForegroundLeave {
  const next = queue.filter((id) => id !== promptId);
  return { queue: next, idle: next.length === 0, head: next[0] };
}
