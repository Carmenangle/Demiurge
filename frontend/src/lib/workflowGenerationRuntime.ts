import { getResult, type GenResult } from "../api/comfyui";
import type { RegenerationSnapshot } from "../types/chat";

export interface PendingGeneration {
  prompt_id: string;
  createdAt: number;
  outputNodeIds?: string[];
  regeneration?: RegenerationSnapshot;
  target?: { messageId: string; slotId: string; background: true };
  prompt?: string;
}

export interface WorkflowWatchInput {
  promptId: string;
  comfyuiUrl: string;
  outputNodeIds?: string[];
  regeneration?: RegenerationSnapshot;
  target?: PendingGeneration["target"];
  prompt?: string;
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
  target?: PendingGeneration["target"], prompt = "",
): PendingGeneration[] {
  return [
    ...pending.filter((item) => item.prompt_id !== promptId),
    {
      prompt_id: promptId, createdAt,
      ...(outputNodeIds.length ? { outputNodeIds } : {}),
      ...(regeneration ? { regeneration } : {}),
      ...(target ? { target } : {}),
      ...(prompt ? { prompt } : {}),
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

export function pollSchedule(tries: number): { releaseBusy: boolean; delayMs: number | null } {
  return { releaseBusy: tries === 150, delayMs: tries < 150 ? 2000 : tries < 210 ? 15000 : null };
}

export function generationResultAction(status: string): "complete" | "fail" | "poll" {
  if (status === "completed") return "complete";
  if (status === "failed") return "fail";
  return "poll";
}

export const notFoundPollAction = (count: number): "retry" | "fail" => count >= 5 ? "fail" : "retry";

export function shouldFinalize(
  promptId: string | undefined,
  persistedPending: readonly { prompt_id: string }[],
  finalizedIds: ReadonlySet<string>,
): boolean {
  if (!promptId) return true;
  return persistedPending.some((item) => item.prompt_id === promptId) && !finalizedIds.has(promptId);
}

export class WorkflowGenerationRuntime {
  private readonly key: string;
  private readonly storage: Pick<Storage, "getItem" | "setItem">;
  private readonly now: () => number;
  private readonly fetchResult: typeof getResult;
  private readonly schedule: (callback: () => void, delayMs: number) => unknown;
  private readonly finalized = new Set<string>();

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
      input.regeneration, input.target, input.prompt,
    );
    this.write(next);
    return next.find((item) => item.prompt_id === input.promptId)!;
  }

  remove(promptId: string): void {
    this.write(unregisterPending(this.list(), promptId));
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
      const next = pollSchedule(tries);
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
    }, observer);
    return "watching";
  }

  private async finalize(
    result: GenResult, pending: PendingGeneration, observer: WorkflowWatchObserver,
  ): Promise<boolean> {
    if (!shouldFinalize(pending.prompt_id, this.list(), this.finalized)) return false;
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
