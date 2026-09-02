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
  /** 停顿守卫：任务一直卡在排队（从未观察到节点开始运转）超过 stall 窗口 → 停止。 */
  stalled?: (pending: PendingGeneration) => void;
}

type RuntimeOptions = {
  storage?: Pick<Storage, "getItem" | "setItem">;
  now?: () => number;
  fetchResult?: typeof getResult;
  schedule?: (callback: () => void, delayMs: number) => unknown;
};

export function registerPending(
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

export const unregisterPending = (
  pending: readonly PendingGeneration[], promptId: string,
): PendingGeneration[] => pending.filter((item) => item.prompt_id !== promptId);

export function pendingResumeAction(
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

export function generationResultAction(status: string): "complete" | "fail" | "poll" {
  if (status === "completed") return "complete";
  if (status === "failed") return "fail";
  return "poll";
}

export type WorkflowPollOutcome =
  | { kind: "complete"; result: GenResult }
  | { kind: "failed"; error: string }
  | { kind: "stalled"; error: string }
  | { kind: "still_running" };

/**
 * 轮询 ComfyUI 出图结果直到完成/失败/硬超时。
 * 超时合同复用 pollSchedule：图片 20 分钟（150×2s + 60×15s）、视频 60 分钟（450×2s + 180×15s）。
 * 完成判定只看 /history status；WS 实时进度另有 subscribeProgress 驱动，不在此处。
 * still_running：到达硬超时仍 running/pending（ComfyUI 后台仍在跑，调用方不得擅自删除占位节点）。
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
  let tries = 0;
  let consecutiveNotFound = 0;
  // 停顿守卫（对齐 workflowRuntime stall 窗口）：任务一直卡在排队（从未观察到节点运转）
  // 超过窗口 → stalled 早停，调用方可据此清理坏死任务并自动重试（2026-08-29 用户需求）。
  let firstPendingAt = -1;
  let seenRunning = false;
  for (;;) {
    tries += 1;
    try {
      const result = await fetchResult(promptId, comfyuiUrl, []);
      if (result.status === "completed") return { kind: "complete", result };
      if (result.status === "failed") {
        return { kind: "failed", error: result.error || "ComfyUI 工作流执行失败" };
      }
      if (result.status === "running") {
        seenRunning = true;
        firstPendingAt = -1;
        consecutiveNotFound = 0;
      } else if (result.status === "pending") {
        consecutiveNotFound = 0;
        if (firstPendingAt < 0) firstPendingAt = now();
        if (!seenRunning && now() - firstPendingAt >= DEFAULT_STALL_TIMEOUT_MS) {
          return { kind: "stalled", error: STALLED_POLL_MESSAGE };
        }
      } else if (result.status === "not_found") {
        consecutiveNotFound += 1;
        if (consecutiveNotFound >= 5) {
          return { kind: "failed", error: "出图任务已丢失（ComfyUI 可能已重启）" };
        }
      } else {
        consecutiveNotFound = 0;
      }
    } catch {
      // ComfyUI 暂不可达（重启中/忙）：继续轮询，硬超时兑底
    }
    const sched = pollSchedule(tries, mediaType);
    if (sched.delayMs === null) return { kind: "still_running" };
    await new Promise<void>((resolve) => schedule(resolve, sched.delayMs!));
  }
}

export const notFoundPollAction = (count: number): "retry" | "fail" => count >= 5 ? "fail" : "retry";

export function shouldFinalize(
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
  private readonly storage: Pick<Storage, "getItem" | "setItem">;
  private readonly now: () => number;
  private readonly fetchResult: typeof getResult;
  private readonly schedule: (callback: () => void, delayMs: number) => unknown;
  private readonly finalized = new Set<string>();
  private readonly cancelled = new Set<string>();

  constructor(threadId: string, options: RuntimeOptions = {}) {
    this.key = `laf_pending_gen_${threadId}`;
    this.storage = options.storage || localStorage;
    this.now = options.now || Date.now;
    this.fetchResult = options.fetchResult || getResult;
    this.schedule = options.schedule || ((callback, delayMs) => setTimeout(callback, delayMs));
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

  cancel(promptId: string): void {
    this.cancelled.add(promptId);
    this.remove(promptId);
  }

  recoveryAction(item: PendingGeneration, resumedIds: ReadonlySet<string>): "skip" | "expire" | "inspect" {
    return pendingResumeAction(item, resumedIds, this.now());
  }

  start(input: WorkflowWatchInput, observer: WorkflowWatchObserver, initialDelayMs = 1500): void {
    const pending = this.track(input);
    const stallTimeoutMs = input.stallTimeoutMs ?? DEFAULT_STALL_TIMEOUT_MS;
    let tries = 0;
    let consecutiveNotFound = 0;
    let seenRunning = false;   // 是否观察到节点开始运转（/queue queue_running）
    const tick = async () => {
      tries += 1;
      try {
        const result = await this.fetchResult(
          pending.prompt_id, input.comfyuiUrl, pending.outputNodeIds || [],
        );
        if (result.status === "running") seenRunning = true;
        const action = generationResultAction(result.status);
        if (action === "complete") {
          const produced = await this.finalize(result, pending, observer);
          this.remove(pending.prompt_id);
          observer.completed(result, pending, produced);
          return;
        }
        if (action === "fail") {
          this.remove(pending.prompt_id);
          observer.failed(pending, "execution", result.error || "ComfyUI 工作流执行失败");
          return;
        }
        if (result.status === "not_found") {
          consecutiveNotFound += 1;
          if (notFoundPollAction(consecutiveNotFound) === "fail") {
            if (!this.list().some((item) => item.prompt_id === pending.prompt_id)) return;
            this.remove(pending.prompt_id);
            observer.failed(pending, "task_not_found", "出图任务已丢失");
            return;
          }
        } else {
          consecutiveNotFound = 0;
        }
      } catch {
        // ComfyUI may be temporarily unavailable; retain the task and keep watching.
      }
      // 停顿守卫：一直卡在排队（从未观察到节点加载/运转）超过 stall 窗口 → 早停，
      // 而不是死等到 20/60 分钟硬超时。
      if (!seenRunning && this.now() - pending.createdAt >= stallTimeoutMs) {
        if (!this.list().some((item) => item.prompt_id === pending.prompt_id)) return;
        this.remove(pending.prompt_id);
        (observer.stalled ?? observer.timedOut)(pending);
        return;
      }
      const next = pollSchedule(tries, pending.mediaType);
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

/** 画布工作流运转记入后台活动：提交成功即写入 laf_pending_gen_<threadId>，
 *  SupportWidget/comfyBackgroundActivity 面板扫描该 key 显示「出图中」。
 *  画布运转自持轮询（pollWorkflowResult），这里只做标记，不启动 runtime 的观察循环。 */
export function trackCanvasWorkflow(
  threadId: string, promptId: string, comfyuiUrl: string, prompt = "", runId = "",
): void {
  try {
    new WorkflowGenerationRuntime(threadId).track({ promptId, comfyuiUrl, outputNodeIds: [], prompt, runId });
  } catch { /* 后台活动标记失败不阻塞运转 */ }
}

/** 画布工作流运转结束（完成/失败/超时）后移除后台活动标记。 */
export function untrackCanvasWorkflow(threadId: string, promptId: string): void {
  try {
    new WorkflowGenerationRuntime(threadId).remove(promptId);
  } catch { /* ignore */ }
}
