import { getResult, type GenResult } from "../api/comfyui";
import type { RegenerationSnapshot } from "../types/chat";

/** 停顿守卫默认窗口：任务一直未观察到节点运转（/queue 仍是 queue_pending）超过此时长就早停。
 *  5 分钟早于图片 20 分钟 / 视频 60 分钟硬超时，避免 ComfyUI 队列拥堵时误导用户死等。 */
export const DEFAULT_STALL_TIMEOUT_MS = 300_000;
/** 同步轮询停顿守卫文案（对齐 workflowRuntime stalled 观察者）。 */
export const STALLED_POLL_MESSAGE = "长时间未开始加载节点（ComfyUI 队列未运转），已停止";

export interface PendingGeneration {
  prompt_id: string;
  /** 画布/弹窗运转任务的 runId（对话框进度条按它关联；对话模式没有） */
  runId?: string;
  createdAt: number;
  outputNodeIds?: string[];
  regeneration?: RegenerationSnapshot;
  target?: { messageId: string; slotId: string; background: true };
  prompt?: string;
  owner?: { threadId: string; repoId: string; outputDir: string };
  /** V1.3：媒体类型驱动超时合同（视频数分钟~十分钟级，独立于图片放宽） */
  mediaType?: "image" | "video" | "audio";
  /** M2.1：视频首帧底图来源槽引用（供 derived_from 记录） */
  baseSlotRef?: { messageId: string; slotId: string };
}

export interface WorkflowWatchInput {
  promptId: string;
  /** 画布/弹窗运转任务 id；对话框进度条按它恢复 */
  runId?: string;
  comfyuiUrl: string;
  outputNodeIds?: string[];
  regeneration?: RegenerationSnapshot;
  target?: PendingGeneration["target"];
  prompt?: string;
  owner?: PendingGeneration["owner"];
  mediaType?: PendingGeneration["mediaType"];
  baseSlotRef?: PendingGeneration["baseSlotRef"];
  /** 停顿窗口（毫秒）：任务一直未观察到节点运转时的早停阈值。默认 5 分钟。 */
  stallTimeoutMs?: number;
}

export interface WorkflowWatchObserver {
  finalize: (result: GenResult, pending: PendingGeneration) => Promise<boolean>;
  completed: (result: GenResult, pending: PendingGeneration, produced: boolean) => void;
  failed: (pending: PendingGeneration, stage: "execution" | "task_not_found", error: string) => void;
  released: (pending: PendingGeneration) => void;
  timedOut: (pending: PendingGeneration) => void;
  /**
   * 停顿守卫：任务一直卡在排队（从未观察到节点开始运转）超过 stall 窗口 → 停止。
   *
   * **P13（2026-09-15）**：第二参转发判定已经产出的 `error`（＝ `STALLED_POLL_MESSAGE`）——
   * 消费方**不必**再 `import STALLED_POLL_MESSAGE` 自己塞回（旧形态让「文案归 runtime」名存实亡：
   * 画布侧 `runCanvasWorkflow` 就是把同一个常量再搬一次，对话侧则硬编码同字面值）。
   */
  stalled?: (pending: PendingGeneration, error: string) => void;
}

type RuntimeOptions = {
  storage?: Pick<Storage, "getItem" | "setItem">;
  now?: () => number;
  fetchResult?: typeof getResult;
  schedule?: (callback: () => void, delayMs: number) => unknown;
};

/**
 * ⚠ **P14（2026-09-15）起本组纯判定函数是模块内实现细节**：只服务于本模块的
 * `WorkflowGenerationRuntime`（`track` / `remove` / `recoveryAction` / `inspect` / `finalize`）
 * 与 tick 核心（`advanceWorkflowTick`），**不再导出**。
 *
 * 审计结论（清单 P14）：导出面 = 测试面。唯一外部引用是 `lib/chatGeneration.test.ts`——
 * 那是 `chatGeneration.ts` 的测试文件，而这些函数早已搬来本模块、测试没跟着搬 ⇒ 归属错位，
 * 于是「纯判定实现细节」被当成公开 API 挂着。
 *
 * 收窄后的保险：行为由 `workflowGenerationRuntime.test.ts` 的「P14 · 公开门面等价网」
 * 经公开门面逐条钉住（零覆盖损失）；形态由 `workflowGenerationRuntime.contract.test.ts`
 * 的源码锁钉住（六个名字不得再次 `export`，且模块内必须有生产调用 ⇒ 不是死代码）。
 */
function registerPending(
  pending: readonly PendingGeneration[], promptId: string, createdAt: number,
  outputNodeIds: string[] = [], regeneration?: RegenerationSnapshot,
  target?: PendingGeneration["target"], prompt = "", owner?: PendingGeneration["owner"],
  mediaType?: PendingGeneration["mediaType"], baseSlotRef?: PendingGeneration["baseSlotRef"],
  runId?: string,
): PendingGeneration[] {
  return [
    ...pending.filter((item) => item.prompt_id !== promptId),
    {
      prompt_id: promptId, createdAt,
      ...(outputNodeIds.length ? { outputNodeIds } : {}),
      ...(regeneration ? { regeneration } : {}),
      ...(target ? { target } : {}),
      ...(prompt ? { prompt } : {}),
      ...(owner ? { owner } : {}),
      ...(mediaType ? { mediaType } : {}),
      ...(baseSlotRef ? { baseSlotRef } : {}),
      ...(runId ? { runId } : {}),
    },
  ];
}

const unregisterPending = (
  pending: readonly PendingGeneration[], promptId: string,
): PendingGeneration[] => pending.filter((item) => item.prompt_id !== promptId);

function pendingResumeAction(
  item: PendingGeneration, resumedIds: ReadonlySet<string>, now: number,
): "skip" | "expire" | "inspect" {
  if (resumedIds.has(item.prompt_id)) return "skip";
  return now - item.createdAt > 30 * 60 * 1000 ? "expire" : "inspect";
}

export function pollSchedule(
  tries: number, mediaType: "image" | "video" | "audio" = "image",
): { releaseBusy: boolean; delayMs: number | null } {
  // V1.3：视频/音频超时合同独立于图片——图片 5min 释放忙碌 / 20min 上限；
  // 视频/音频放宽为 15min 释放忙碌 / 60min 上限（450×2s + 180×15s = 15min + 45min = 60min）。
  const releaseTries = mediaType === "video" || mediaType === "audio" ? 450 : 150;   // 2s 间隔：15min / 5min
  const hardTries = mediaType === "video" ? 630 : 210;      // 之后 15s：上限 60min / 20min
  return {
    releaseBusy: tries === releaseTries,
    delayMs: tries < releaseTries ? 2000 : tries < hardTries ? 15000 : null,
  };
}

function generationResultAction(status: string): "complete" | "fail" | "poll" {
  if (status === "completed") return "complete";
  if (status === "failed") return "fail";
  return "poll";
}

const notFoundPollAction = (count: number): "retry" | "fail" => count >= 5 ? "fail" : "retry";

// ── tick 核心：两个调用方（后台守望 / 同步轮询）共用的一次「推进」─────────────
//
// 2026-09-15 架构审计 C4-A 第 1 步：`WorkflowGenerationRuntime.start` 与 `pollWorkflowResult`
// 此前各自重写同一套循环（tries / not_found 连击 / 停顿守卫 / pollSchedule），
// 导致「时机与判定」有两个属主（前科：画布曾硬编码 240 秒，与共享的 pollSchedule 合同脱钩）。
// 现在把「一次 fetch 之后该怎么判定」抽成纯函数，两个 loop 只负责各自的 I/O 与终态派发。
// **两个口径必须都保留**（否则就不是零行为变化）：
//   · 同步轮询：停顿基准 = 首次观测到 `pending` 的时刻，且只在 `pending` 观测上判定；
//     仅当处于 running 分支时重置基准与 not_found 连击。
//   · 后台守望：停顿基准 = 任务创建时刻，`!seenRunning` 时对**任意**观测（含 ComfyUI 不可达）判定。

/** 一次 fetch 的观测。`ok=false` = ComfyUI 暂不可达（重启中/忙）：不计入状态，仅继续计时与调度。 */
export type WorkflowFetchObservation =
  | { ok: true; status: string; error?: string }
  | { ok: false };

/** tick 核心的滚动状态（纯数据，由调用方各自持有）。 */
export interface WorkflowTickState {
  /** 已完成的轮询次数（本次推进之前） */
  tries: number;
  consecutiveNotFound: number;
  /** 是否观察到节点已开始运转：一旦 true 就永不回落（停顿守卫据此早停） */
  seenRunning: boolean;
  /** `first_pending` 基准下的停顿计时起点；-1 = 尚未开始计时 */
  stallBasisAt: number;
}

export interface WorkflowTickContext {
  now: number;
  mediaType?: "image" | "video" | "audio";
  /** 停顿窗口（毫秒）：一直未观察到节点运转即早停 */
  stallWindowMs: number;
  /** 停顿计时基准（两个调用方口径不同，见上方注释） */
  stallBasis: "first_pending" | "created_at";
  /** `stallBasis=created_at` 时必填：任务创建时刻 */
  createdAt?: number;
}

export type WorkflowTickDecision =
  | { kind: "complete" }
  /** `notFound=true` 表示任务已丢失；错误文案由调用方决定（两端口径不同） */
  | { kind: "failed"; error: string; notFound: boolean }
  | { kind: "stalled"; error: string }
  | { kind: "still_running" }
  | { kind: "poll"; released: boolean; delayMs: number };

export function initialTickState(): WorkflowTickState {
  return { tries: 0, consecutiveNotFound: 0, seenRunning: false, stallBasisAt: -1 };
}

/** 停顿守卫：返回 true 表示应当 early-stop。两个调用方的口径差异全在这里。 */
export function workflowStalled(
  state: WorkflowTickState, context: WorkflowTickContext, status?: string,
): boolean {
  if (state.seenRunning) return false;
  if (context.stallBasis === "created_at") {
    const createdAt = context.createdAt ?? context.now;
    return context.now - createdAt >= context.stallWindowMs;
  }
  // 同步轮询口径：只在「观测到 pending」时判定，且基准是首个 pending 观测时刻。
  // 注意 `>= 0` 而非 `> 0`：原实现以 -1 作「尚未开始计时」哨兵，`now()` 返回 0 时基准就是 0，
  // 写成 `> 0` 会让该轮永不计时（测试里的时钟从 0 起，会直接漏判停顿）。
  return status === "pending" && state.stallBasisAt >= 0
    && context.now - state.stallBasisAt >= context.stallWindowMs;
}

/** 推进一次 tick：不触碰任何 I/O 与存储，只产出「新状态 + 该做什么」。 */
export function advanceWorkflowTick(
  state: WorkflowTickState, observation: WorkflowFetchObservation, context: WorkflowTickContext,
): { state: WorkflowTickState; decision: WorkflowTickDecision } {
  const next: WorkflowTickState = { ...state, tries: state.tries + 1 };
  if (observation.ok) {
    const { status } = observation;
    if (status === "completed") return { state: next, decision: { kind: "complete" } };
    if (status === "failed") {
      return {
        state: next,
        decision: {
          kind: "failed", notFound: false,
          error: observation.error || "ComfyUI 工作流执行失败",
        },
      };
    }
    if (status === "running") {
      next.seenRunning = true;
      next.stallBasisAt = -1;
      next.consecutiveNotFound = 0;
    } else if (status === "pending") {
      next.consecutiveNotFound = 0;
      if (context.stallBasis === "first_pending" && next.stallBasisAt < 0) {
        next.stallBasisAt = context.now;
      }
    } else if (status === "not_found") {
      next.consecutiveNotFound = state.consecutiveNotFound + 1;
      if (notFoundPollAction(next.consecutiveNotFound) === "fail") {
        return { state: next, decision: { kind: "failed", notFound: true, error: "出图任务已丢失" } };
      }
    } else {
      next.consecutiveNotFound = 0;
    }
  }
  if (workflowStalled(next, context, observation.ok ? observation.status : undefined)) {
    return { state: next, decision: { kind: "stalled", error: STALLED_POLL_MESSAGE } };
  }
  const schedule = pollSchedule(next.tries, context.mediaType);
  if (schedule.delayMs === null) return { state: next, decision: { kind: "still_running" } };
  return {
    state: next,
    decision: { kind: "poll", released: schedule.releaseBusy, delayMs: schedule.delayMs },
  };
}

// ── 同步轮询（await 型）：首尾帧（画布/弹窗已于 C4-A Step 3/4 改走 `start`）──────

export type WorkflowPollOutcome =
  | { kind: "complete"; result: GenResult }
  | { kind: "failed"; error: string }
  | { kind: "stalled"; error: string }
  | { kind: "still_running" };

/**
 * 轮询 ComfyUI 出图结果直到完成/失败/硬超时（await 型：调用方要拿到结果才能继续）。
 * 超时合同复用 pollSchedule：图片 20 分钟（150×2s + 60×15s）、视频 60 分钟（450×2s + 180×15s）。
 * 完成判定只看 /history status；WS 实时进度另有 subscribeProgress 驱动，不在此处。
 * still_running：到达硬超时仍 running/pending（ComfyUI 后台仍在跑，调用方不得擅自删除占位节点）。
 *
 * 2026-09-15（C4-A 第 1 步）：判定逻辑已收进 `advanceWorkflowTick`，本函数只负责 I/O 与把决策翻成 outcome；
 * 停顿口径＝`first_pending`（与改前逐条对齐，行为不变）。
 *
 * 2026-09-15（C4-A 第 5 步）：**三调用方归位**。画布（`CanvasStageFlow`）与弹窗（`WorkflowToolModal`）
 * 改走 `WorkflowGenerationRuntime.start`（后台守望 + 登记 pending）；**首尾帧路径（`useChatSession`
 * 的 `planFirstlastFrameTasks` 段）保留本函数**——它必须 `await` 拿到 `result.images[0]` 再
 * `moveComfyOutputToInput` 当另一帧的底图，而且**不得登记 pending**（帧图不进对话占位/恢复列表）。
 * 本函数正是「同步 await 且不写 pending」的门面，故不另造 `watch()` 门面：两者共享
 * `advanceWorkflowTick` 判定核心，判定属主仍在本模块。
 */
export async function pollWorkflowResult(
  promptId: string,
  comfyuiUrl: string,
  mediaType: "image" | "video" = "image",
  opts: {
    fetchResult?: typeof getResult;
    schedule?: (callback: () => void, delayMs: number) => unknown;
    now?: () => number;
  } = {},
): Promise<WorkflowPollOutcome> {
  const fetchResult = opts.fetchResult || getResult;
  const schedule = opts.schedule || ((callback, delayMs) => setTimeout(callback, delayMs));
  const now = opts.now || (() => Date.now());
  let state = initialTickState();
  for (;;) {
    let result: GenResult | null = null;
    let observation: WorkflowFetchObservation;
    try {
      result = await fetchResult(promptId, comfyuiUrl, []);
      observation = { ok: true, status: result.status, error: result.error };
    } catch {
      // ComfyUI 暂不可达（重启中/忙）：继续轮询，硬超时兜底
      observation = { ok: false };
    }
    const stepped = advanceWorkflowTick(state, observation, {
      now: now(), mediaType,
      stallWindowMs: DEFAULT_STALL_TIMEOUT_MS, stallBasis: "first_pending",
    });
    state = stepped.state;
    const decision = stepped.decision;
    switch (decision.kind) {
      case "complete":
        return { kind: "complete", result: result! };
      case "failed":
        return {
          kind: "failed",
          error: decision.notFound ? "出图任务已丢失（ComfyUI 可能已重启）" : decision.error,
        };
      case "stalled":
        return { kind: "stalled", error: decision.error };
      case "still_running":
        return { kind: "still_running" };
      default:
        await new Promise<void>((resolve) => schedule(resolve, decision.delayMs));
    }
  }
}

/** 收尾判重（P14 起模块内）：`finalized` 拦并发收尾窗口，`cancelled` 拦用户显式取消。
 *  公开门面 = `WorkflowGenerationRuntime.finalize`（行为网在「P14 · 公开门面等价网」）。 */
function shouldFinalize(
  promptId: string | undefined,
  _persistedPending: readonly { prompt_id: string }[],
  finalizedIds: ReadonlySet<string>,
  cancelledIds: ReadonlySet<string> = new Set(),
): boolean {
  if (!promptId) return true;
  return !finalizedIds.has(promptId) && !cancelledIds.has(promptId);
}

export function durableFinalizeSucceeded(result: {
  durable: boolean;
  images: readonly { persisted: boolean; snapshotted: boolean }[];
  videos?: readonly { persisted: boolean; snapshotted: boolean }[];
  audios?: readonly { persisted: boolean; snapshotted: boolean }[];
}): boolean {
  // V1.3：视频结果的 media 在 videos（images 为空数组）——合并后统一校验，杜绝 undefined.every 抛错。
  // 注意：durable 且 media 为空时（如产物全在 temp 尚未持久化）应返回 false，
  // 而非旧代码 [].every() 的空真→成功。caller 可据此决定等待或重试。
  const media = [...(result.images || []), ...(result.videos || []), ...(result.audios || [])];
  return !result.durable || (media.length > 0 && media.every((item) => item.persisted && item.snapshotted));
}

export class WorkflowGenerationRuntime {
  private readonly key: string;
  private readonly storageOverride?: Pick<Storage, "getItem" | "setItem">;
  private readonly now: () => number;
  private readonly fetchResult: typeof getResult;
  private readonly schedule: (callback: () => void, delayMs: number) => unknown;
  private readonly finalized = new Set<string>();
  private readonly cancelled = new Set<string>();

  constructor(threadId: string, options: RuntimeOptions = {}) {
    this.key = `laf_pending_gen_${threadId}`;
    this.storageOverride = options.storage;
    this.now = options.now || Date.now;
    this.fetchResult = options.fetchResult || getResult;
    this.schedule = options.schedule || ((callback, delayMs) => setTimeout(callback, delayMs));
  }

  /**
   * 存储解析：注入的 `override` 优先，否则**每次读**全局 `localStorage`。
   *
   * 为什么不在构造函数里快照 `localStorage`：`getRuntime` 的单例会把实例缓存住，
   * 若在此快照，测试里 `vi.stubGlobal("localStorage", …)` 后换的 stub 就永远读不到
   * （单例会把**第一个用例**的 stub 缓存到后续用例上）；而构造函数里裸引用 `localStorage`
   * 在没有全局存储的环境还会直接抛。改成惰性解析后，`list()`/`write()` 自带的 try/catch
   * 也顺带兜住了「全局存储不可用」。
   */
  private get storage(): Pick<Storage, "getItem" | "setItem"> {
    return this.storageOverride ?? localStorage;
  }

  list(): PendingGeneration[] {
    try {
      const value = JSON.parse(this.storage.getItem(this.key) || "[]");
      return Array.isArray(value) ? value : [];
    } catch {
      return [];
    }
  }

  track(input: WorkflowWatchInput): PendingGeneration {
    const next = registerPending(
      this.list(), input.promptId, this.now(), input.outputNodeIds,
      input.regeneration, input.target, input.prompt, input.owner, input.mediaType,
      input.baseSlotRef, input.runId,
    );
    this.write(next);
    return next.find((item) => item.prompt_id === input.promptId)!;
  }

  remove(promptId: string): void {
    this.write(unregisterPending(this.list(), promptId));
  }

  /**
   * 取消一个在途任务：进 `cancelled` 判重集合 + 从持久化 pending 里移除（刷新后不再恢复）。
   *
   * ⚠ **本方法不派发任何终态回调**（不是遗漏，是刻意的）：取消后不会走 `observer.failed` /
   * `observer.stalled` —— `tick` 里 `notFound` 与停顿守卫两处都有「pending 已不在列表 ⇒ 静默
   * `return`」的早退，正是为「已取消 / 已归档的任务不再报错」而设（2026-09-15 P22 实测确认）。
   *
   * ⇒ **调用方义务**：若该任务曾被登记进前台队列（`activePromptQueue`），取消时**必须同时出队**，
   * 否则残留 id 会让下一次 `joinForeground` 得到 `first=false`、进度条 WS 不再订阅。
   * 现成属主：`useChatSession.cancelForegroundRun`（唯一入库路径，源码锁在
   * `useChatSession.contract.test.ts` 的「P22」组）。
   */
  cancel(promptId: string): void {
    this.cancelled.add(promptId);
    this.remove(promptId);
  }

  recoveryAction(item: PendingGeneration, resumedIds: ReadonlySet<string>): "skip" | "expire" | "inspect" {
    return pendingResumeAction(item, resumedIds, this.now());
  }

  /**
   * 后台守望：登记 pending → 持续轮询 → 终态交给 observer。
   *
   * 2026-09-15（C4-A 第 1 步）：判定逻辑已收进 `advanceWorkflowTick`，本方法只负责 I/O、
   * pending 存储与 observer 派发；停顿口径＝`created_at`（与改前逐条对齐，行为不变）。
   * **归档抛错必须留在 try 内**：那正是「原图/会话槽尚未持久化 → 保留 pending 自动重试」的实现方式
   * （`useChatSession.finalizeGeneration` 就靠抛错触发重试），故 complete 分支外包一层 catch，
   * 失败后退回与改前一致的「停顿守卫 + 下一轮调度」尾段。
   */
  start(input: WorkflowWatchInput, observer: WorkflowWatchObserver, initialDelayMs = 1500): void {
    const pending = this.track(input);
    const stallTimeoutMs = input.stallTimeoutMs ?? DEFAULT_STALL_TIMEOUT_MS;
    const tickContext = (): WorkflowTickContext => ({
      now: this.now(), mediaType: pending.mediaType, stallWindowMs: stallTimeoutMs,
      stallBasis: "created_at", createdAt: pending.createdAt,
    });
    let state = initialTickState();
    const tick = async (): Promise<void> => {
      let result: GenResult | null = null;
      let observation: WorkflowFetchObservation = { ok: false };
      try {
        try {
          result = await this.fetchResult(
            pending.prompt_id, input.comfyuiUrl, pending.outputNodeIds || [],
          );
          observation = { ok: true, status: result.status, error: result.error };
        } catch {
          // ComfyUI may be temporarily unavailable; retain the task and keep watching.
        }
        // 注意：必须在 fetch **之外**推进 tick —— 原实现的 `tries += 1` 在 fetch 之前，
        // 若 fetch 抛错就不计数，pollSchedule 会永远停在 2s 档、硬超时永不触发（≡ 无限轮询）。
        const stepped = advanceWorkflowTick(state, observation, tickContext());
        state = stepped.state;
        const decision = stepped.decision;
        if (decision.kind === "complete") {
          const produced = await this.finalize(result!, pending, observer);
          this.remove(pending.prompt_id);
          observer.completed(result!, pending, produced);
          return;
        }
        if (decision.kind === "failed") {
          if (decision.notFound
              && !this.list().some((item) => item.prompt_id === pending.prompt_id)) return;
          this.remove(pending.prompt_id);
          observer.failed(pending, decision.notFound ? "task_not_found" : "execution", decision.error);
          return;
        }
      } catch {
        // 归档/派发抛错（含 observer.finalize 抛错）→ 与原实现一致：不 return，落到尾段继续守望。
        // 「原图/会话槽尚未持久化 → 保留 pending 自动重试」正是这条路径：
        // useChatSession.finalizeGeneration 靠抛错触发重试。
      }
      // ── 尾段：停顿守卫 + 下一轮调度 ──
      // 改前这段位于 try 之外，故观察者回调（released/timedOut/stalled）抛错不在此捕获。
      if (workflowStalled(state, tickContext())) {
        if (!this.list().some((item) => item.prompt_id === pending.prompt_id)) return;
        this.remove(pending.prompt_id);
        // P13：`stalled` 转发判定产出的 error；缺省实现仍是 `timedOut`（签名不同 ⇒ 不再用 `??` 合并调用）
        if (observer.stalled) observer.stalled(pending, STALLED_POLL_MESSAGE);
        else observer.timedOut(pending);
        return;
      }
      const next = pollSchedule(state.tries, pending.mediaType);
      if (next.releaseBusy) observer.released(pending);
      if (next.delayMs === null) observer.timedOut(pending);
      else this.schedule(tick, next.delayMs);
    };
    this.schedule(tick, initialDelayMs);
  }

  async inspect(
    item: PendingGeneration,
    comfyuiUrl: string,
    observer: WorkflowWatchObserver,
  ): Promise<"complete" | "failed" | "watching"> {
    try {
      const result = await this.fetchResult(item.prompt_id, comfyuiUrl, item.outputNodeIds || []);
      const action = generationResultAction(result.status);
      if (action === "complete") {
        const produced = await this.finalize(result, item, observer);
        this.remove(item.prompt_id);
        observer.completed(result, item, produced);
        return "complete";
      }
      if (action === "fail") {
        this.remove(item.prompt_id);
        observer.failed(item, "execution", result.error || "ComfyUI 工作流执行失败");
        return "failed";
      }
    } catch {
      // A failed inspection resumes the durable watcher below.
    }
    this.start({
      promptId: item.prompt_id, comfyuiUrl, outputNodeIds: item.outputNodeIds,
      regeneration: item.regeneration, target: item.target, prompt: item.prompt,
      owner: item.owner,
      // 2026-09-15（C4-A Step 2）：`start` 会 `track` 重写这条 pending，
      // 不转发就等于把已落盘的字段**丢掉**（旧代码只传 7 个字段 ⇒ 恢复后 mediaType/runId/baseSlotRef 全没，
      // 最直接的后果是视频任务退回图片的 20 分钟硬超时）。
      mediaType: item.mediaType, runId: item.runId, baseSlotRef: item.baseSlotRef,
    }, observer);
    return "watching";
  }

  private async finalize(
    result: GenResult, pending: PendingGeneration, observer: WorkflowWatchObserver,
  ): Promise<boolean> {
    if (!shouldFinalize(pending.prompt_id, this.list(), this.finalized, this.cancelled)) return false;
    this.finalized.add(pending.prompt_id);
    try {
      return await observer.finalize(result, pending);
    } catch (error) {
      this.finalized.delete(pending.prompt_id);
      throw error;
    }
  }

  private write(items: PendingGeneration[]): void {
    try { this.storage.setItem(this.key, JSON.stringify(items)); } catch { /* ignore */ }
  }
}

/**
 * 线程键单点：仓库 id；首页（无仓库）统一为 `"home"`。
 *
 * 修复 2026-09-15 P17：此前三处各自推导 —— 画布 `repo?.id || ""`，而 `useChatSession.ts` 与
 * `ChatView.tsx` 用 `repo?.id || "home"` ⇒ 首页场景 `getRuntime("") ≠ getRuntime("home")`：
 * **两个实例、两套 `finalized`/`cancelled` 判重集合、两个 `laf_pending_gen_*` 键**
 * ⇒ 同一 pending 可被归档两次，且首页任务既**不被恢复**（恢复循环走 `"home"` 那个实例）
 * 也**不进后台活动面板**（`comfyBackgroundActivity.scanComfyActivities` 显式跳过空键与 `home`）。
 *
 * 「首页 = 对话线 id `home`」是既有约定（后端按 threadId 落盘多轮记忆与 RAG 知识库），`""` 只是画布
 * 那处的历史笔误 ⇒ 收口成唯一推导，**禁止再各自 `|| ""` / `|| "home"`**。契约锁见
 * `useChatSession.contract.test.ts`（「P17 线程键单点接线」）。
 */
export function threadKey(repoId?: string | null): string {
  return repoId || "home";
}

/** 历史遗留的空线程键（画布原用 `repo?.id || ""` 拼出来的）。 */
const LEGACY_EMPTY_THREAD_KEY = "laf_pending_gen_";

/**
 * 一次性迁移（2026-09-15 P17）：把遗留**空键** `laf_pending_gen_` 下的在途 pending 合并进
 * `laf_pending_gen_home`，避免线程键归一后旧键下的任务永远读不到（**静默丢任务**：不报错、
 * 表现为「任务不见了、成果没归档」）。
 *
 * 幂等：按 `prompt_id` 去重合并后删旧键；无旧键时是空操作。返回实际并入条数（便于断言）。
 */
export function migrateLegacyEmptyThreadKey(
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> = localStorage,
): number {
  try {
    const raw = storage.getItem(LEGACY_EMPTY_THREAD_KEY);
    if (!raw) return 0;
    const legacy = JSON.parse(raw);
    if (!Array.isArray(legacy) || legacy.length === 0) return 0;
    const homeKey = `laf_pending_gen_${threadKey(null)}`;
    const existing = JSON.parse(storage.getItem(homeKey) || "[]");
    const home: PendingGeneration[] = Array.isArray(existing) ? existing : [];
    const seen = new Set(home.map((item) => item?.prompt_id));
    const merged = [...home, ...legacy.filter((item) => item?.prompt_id && !seen.has(item.prompt_id))];
    storage.setItem(homeKey, JSON.stringify(merged));
    storage.removeItem(LEGACY_EMPTY_THREAD_KEY);
    return merged.length - home.length;
  } catch {
    return 0;
  }
}

/**
 * 每个 `threadId` 唯一一个 runtime 实例（首次调用的 options 生效，之后忽略）。
 *
 * **为什么必须单例**：`finalized`/`cancelled` 去重集合是**实例内**状态，而画布与对话会读写**同一个**
 * `laf_pending_gen_<threadId>`（画布/弹窗 `start` 与对话 `pollResult` 写、对话恢复 `inspect` 读并归档）。
 * 两实例各自判重 ⇒ 同一个任务可能被**归档两次**（2026-09-15 C4-A Step 2）。
 *
 * 顺带纠正一条此前的误判：`track`/`remove` 的「读全量 → 改 → 写回」是**同步**的，
 * JS 单线程下不会交错 ⇒ 存储本身**没有**覆盖窗口；真正的缺口只是上面这个实例内集合。
 */
const runtimes = new Map<string, WorkflowGenerationRuntime>();

let legacyKeyMigrated = false;

export function getRuntime(threadId: string, options?: RuntimeOptions): WorkflowGenerationRuntime {
  // 首次进入 runtime 层时补一次空键迁移（P17）；放在这里是因为**所有**对该存储键的读写都经此入口，
  // 无需依赖 App 启动顺序。裸引用 localStorage 在无全局存储的环境会抛，故整体兜 try/catch。
  if (!legacyKeyMigrated) {
    legacyKeyMigrated = true;
    try { migrateLegacyEmptyThreadKey(); } catch { /* 无全局存储环境忽略 */ }
  }
  const cached = runtimes.get(threadId);
  if (cached) return cached;
  const runtime = new WorkflowGenerationRuntime(threadId, options);
  runtimes.set(threadId, runtime);
  return runtime;
}
