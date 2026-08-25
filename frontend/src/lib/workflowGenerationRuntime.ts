import { getResult, type GenResult } from "../api/comfyui";
import type { RegenerationSnapshot } from "../types/chat";

export interface PendingGeneration {
  prompt_id: string;
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
  comfyuiUrl: string;
  outputNodeIds?: string[];
  regeneration?: RegenerationSnapshot;
  target?: PendingGeneration["target"];
  prompt?: string;
  owner?: PendingGeneration["owner"];
  mediaType?: PendingGeneration["mediaType"];
  baseSlotRef?: PendingGeneration["baseSlotRef"];
}

export interface WorkflowWatchObserver {
  finalize: (result: GenResult, pending: PendingGeneration) => Promise<boolean>;
  completed: (result: GenResult, pending: PendingGeneration, produced: boolean) => void;
  failed: (pending: PendingGeneration, stage: "execution" | "task_not_found", error: string) => void;
  released: (pending: PendingGeneration) => void;
  timedOut: (pending: PendingGeneration) => void;
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
  } = {},
): Promise<WorkflowPollOutcome> {
  const fetchResult = opts.fetchResult || getResult;
  const schedule = opts.schedule || ((callback, delayMs) => setTimeout(callback, delayMs));
  let tries = 0;
  let consecutiveNotFound = 0;
  for (;;) {
    tries += 1;
    try {
      const result = await fetchResult(promptId, comfyuiUrl, []);
      if (result.status === "completed") return { kind: "complete", result };
      if (result.status === "failed") {
        return { kind: "failed", error: result.error || "ComfyUI 工作流执行失败" };
      }
      if (result.status === "not_found") {
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
      input.baseSlotRef,
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
    let tries = 0;
    let consecutiveNotFound = 0;
    const tick = async () => {
      tries += 1;
      try {
        const result = await this.fetchResult(
          pending.prompt_id, input.comfyuiUrl, pending.outputNodeIds || [],
        );
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
