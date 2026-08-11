import { describe, expect, it, vi } from "vitest";
import type { GenResult } from "../api/comfyui";
import { WorkflowGenerationRuntime } from "./workflowGenerationRuntime";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
}

const completed: GenResult = {
  status: "completed", images: [{ filename: "done.png", subfolder: "", type: "output" }],
  videos: [], texts: [],
};

function observer() {
  return {
    finalize: vi.fn(async () => true), completed: vi.fn(), failed: vi.fn(),
    released: vi.fn(), timedOut: vi.fn(),
  };
}

describe("workflow generation runtime", () => {
  it("persists, polls and finalizes a task once", async () => {
    const scheduled: Array<() => void> = [];
    const storage = memoryStorage();
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage, now: () => 10, fetchResult: vi.fn(async () => completed),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
    expect(runtime.list()).toEqual([{ prompt_id: "p1", createdAt: 10 }]);
    await scheduled.shift()!();
    expect(watch.finalize).toHaveBeenCalledOnce();
    expect(watch.completed).toHaveBeenCalledOnce();
    expect(runtime.list()).toEqual([]);
  });

  it("does not finalize the same prompt through poll and recovery twice", async () => {
    const storage = memoryStorage();
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage, now: () => 10, fetchResult: vi.fn(async () => completed), schedule: () => 0,
    });
    const item = runtime.track({ promptId: "p1", comfyuiUrl: "http://comfy" });
    await Promise.all([
      runtime.inspect(item, "http://comfy", watch),
      runtime.inspect(item, "http://comfy", watch),
    ]);
    expect(watch.finalize).toHaveBeenCalledOnce();
    expect(runtime.list()).toEqual([]);
  });

  it("fails only after five consecutive not-found results", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => 10,
      fetchResult: vi.fn(async (): Promise<GenResult> => ({
        status: "not_found", images: [], videos: [], texts: [],
      })),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
    for (let index = 0; index < 5; index += 1) await scheduled.shift()!();
    expect(watch.failed).toHaveBeenCalledOnce();
    expect(watch.failed.mock.calls[0][1]).toBe("task_not_found");
  });

  it("实时守望不因展示层误清存储而跳过完成归档", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const storage = memoryStorage();
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage, now: () => 10, fetchResult: vi.fn(async () => completed),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
    storage.setItem("laf_pending_gen_work", "[]");

    await scheduled.shift()!();

    expect(watch.finalize).toHaveBeenCalledOnce();
    expect(watch.completed).toHaveBeenCalledWith(completed, expect.any(Object), true);
  });

  it("持久化任务固化提交时的仓库归属", () => {
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => 10, schedule: () => 0,
    });

    const item = runtime.track({
      promptId: "p1", comfyuiUrl: "http://comfy",
      owner: { threadId: "work", repoId: "repo-old", outputDir: "D:/pictures" },
    });

    expect(item.owner).toEqual({
      threadId: "work", repoId: "repo-old", outputDir: "D:/pictures",
    });
  });

  it("用户显式取消后即使 ComfyUI 迟到完成也不归档", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => 10, fetchResult: vi.fn(async () => completed),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
    runtime.cancel("p1");

    await scheduled.shift()!();

    expect(watch.finalize).not.toHaveBeenCalled();
  });
});
