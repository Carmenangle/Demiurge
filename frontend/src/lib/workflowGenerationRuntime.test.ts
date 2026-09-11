import { afterEach, describe, expect, it, vi } from "vitest";
import type { GenResult } from "../api/comfyui";
import { WorkflowGenerationRuntime, pollSchedule, pollWorkflowResult, DEFAULT_STALL_TIMEOUT_MS, durableFinalizeSucceeded, trackCanvasWorkflow, untrackCanvasWorkflow } from "./workflowGenerationRuntime";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
}

const completed: GenResult = {
  status: "completed", images: [{ filename: "done.png", subfolder: "", type: "output" }],
  videos: [], audios: [], texts: [],
};

function observer() {
  return {
    finalize: vi.fn(async () => true), completed: vi.fn(), failed: vi.fn(),
    released: vi.fn(), timedOut: vi.fn(), stalled: vi.fn(),
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

  it("tracks multiple ComfyUI queue entries without one replacing another", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => 10, fetchResult: vi.fn(async () => completed),
      schedule: (callback) => scheduled.push(callback),
    });

    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
    runtime.start({ promptId: "p2", comfyuiUrl: "http://comfy" }, watch);
    expect(runtime.list().map((item) => item.prompt_id)).toEqual(["p1", "p2"]);

    await scheduled.shift()!();
    expect(runtime.list().map((item) => item.prompt_id)).toEqual(["p2"]);
    await scheduled.shift()!();
    expect(runtime.list()).toEqual([]);
    expect(watch.completed).toHaveBeenCalledTimes(2);
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
        status: "not_found", images: [], videos: [], audios: [], texts: [],
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

  it("停顿守卫：一直排队（无节点运转）超过 stall 窗口 → stalled 早停", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    let current = 0;
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => current,
      fetchResult: vi.fn(async (): Promise<GenResult> => ({
        status: "pending", images: [], videos: [], audios: [], texts: [],
      })),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy", stallTimeoutMs: 1000 }, watch);

    await scheduled.shift()!();          // 第一次 tick：尚未越过窗口，继续轮询
    expect(watch.stalled).not.toHaveBeenCalled();
    expect(runtime.list()).toHaveLength(1);

    current = 1000;                       // 越过 stall 窗口后仍未观察到节点运转
    await scheduled.shift()!();
    expect(watch.stalled).toHaveBeenCalledOnce();
    expect(runtime.list()).toEqual([]);
  });

  it("节点已开始运转（/queue running）则不触发停顿守卫", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    let current = 0;
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => current,
      fetchResult: vi.fn(async (): Promise<GenResult> => ({
        status: "running", images: [], videos: [], audios: [], texts: [],
      })),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy", stallTimeoutMs: 1000 }, watch);

    current = 1000;
    await scheduled.shift()!();
    expect(watch.stalled).not.toHaveBeenCalled();
    expect(runtime.list()).toEqual([expect.objectContaining({ prompt_id: "p1" })]);
  });
});

// ===== V1.3 视频超时独立于图片 =====
describe("pollSchedule · 媒体类型超时合同", () => {
  it("图片：5 分钟（150×2s）释放忙碌，20 分钟（210 tries）硬上限", () => {
    expect(pollSchedule(149)).toEqual({ releaseBusy: false, delayMs: 2000 });
    expect(pollSchedule(150)).toEqual({ releaseBusy: true, delayMs: 15000 });
    expect(pollSchedule(209)).toEqual({ releaseBusy: false, delayMs: 15000 });
    expect(pollSchedule(210)).toEqual({ releaseBusy: false, delayMs: null });
  });
  it("视频：15 分钟（450×2s）释放忙碌，60 分钟（630 tries）硬上限——禁止 5 分钟掐掉 10 分钟任务", () => {
    expect(pollSchedule(150, "video")).toEqual({ releaseBusy: false, delayMs: 2000 });
    expect(pollSchedule(449, "video")).toEqual({ releaseBusy: false, delayMs: 2000 });
    expect(pollSchedule(450, "video")).toEqual({ releaseBusy: true, delayMs: 15000 });
    expect(pollSchedule(629, "video")).toEqual({ releaseBusy: false, delayMs: 15000 });
    expect(pollSchedule(630, "video")).toEqual({ releaseBusy: false, delayMs: null });
  });
  it("默认 image（未标媒体类型的旧 pending 保持原行为）", () => {
    expect(pollSchedule(150)).toEqual({ releaseBusy: true, delayMs: 15000 });
  });
});

// ===== M3 空媒体语义 =====
describe("durableFinalizeSucceeded · 空媒体防御", () => {
  it("durable 且所有媒体持久化并快照 → 成功", () => {
    expect(durableFinalizeSucceeded({
      durable: true,
      images: [{ persisted: true, snapshotted: true }],
    })).toBe(true);
  });

  it("durable 且部分媒体未持久化 → 失败", () => {
    expect(durableFinalizeSucceeded({
      durable: true,
      images: [{ persisted: true, snapshotted: false }],
    })).toBe(false);
  });

  it("durable 且 media 为空数组 → 失败（空真防御）", () => {
    // 旧代码 [].every() 空真→成功；修复后空 media 应返回 false
    expect(durableFinalizeSucceeded({
      durable: true,
      images: [],
    })).toBe(false);
  });

  it("durable 且 images 和 videos 均为空 → 失败", () => {
    expect(durableFinalizeSucceeded({
      durable: true,
      images: [],
      videos: [],
      audios: [],
    })).toBe(false);
  });

  it("非 durable 直接成功（无需持久化）", () => {
    expect(durableFinalizeSucceeded({
      durable: false,
      images: [],
    })).toBe(true);
  });

  it("视频 + 图片混合：全部持久化 → 成功", () => {
    expect(durableFinalizeSucceeded({
      durable: true,
      images: [{ persisted: true, snapshotted: true }],
      videos: [{ persisted: true, snapshotted: true }],
    })).toBe(true);
  });

  it("音频 + 图片混合：全部持久化 → 成功", () => {
    expect(durableFinalizeSucceeded({
      durable: true,
      images: [{ persisted: true, snapshotted: true }],
      audios: [{ persisted: true, snapshotted: true }],
    })).toBe(true);
  });
});

// ===== 2026-08-23 画布运转超时合同：复用 pollSchedule，禁止 4 分钟掐掉长任务 =====
describe("pollWorkflowResult · 画布/弹窗运转轮询", () => {
  const running: GenResult = { status: "running", images: [], videos: [], audios: [], texts: [] };
  afterEach(() => { vi.useRealTimers(); });

  it("completed 带图 → complete", async () => {
    const fetchResult = vi.fn(async () => completed);
    const outcome = await pollWorkflowResult("p1", "http://comfy", "image", { fetchResult });
    expect(outcome.kind).toBe("complete");
    if (outcome.kind === "complete") expect(outcome.result.images[0].filename).toBe("done.png");
    expect(fetchResult).toHaveBeenCalledWith("p1", "http://comfy", []);
  });

  it("failed → 立即失败", async () => {
    const fetchResult = vi.fn(async (): Promise<GenResult> => ({
      status: "failed", error: "采样失败", images: [], videos: [], audios: [], texts: [],
    }));
    const outcome = await pollWorkflowResult("p1", "http://comfy", "image", { fetchResult });
    expect(outcome).toEqual({ kind: "failed", error: "采样失败" });
  });

  it("连续 5 次 not_found → 失败（任务已丢失）", async () => {
    vi.useFakeTimers();
    const fetchResult = vi.fn(async (): Promise<GenResult> => ({
      status: "not_found", images: [], videos: [], audios: [], texts: [],
    }));
    const outcomePromise = pollWorkflowResult("p1", "http://comfy", "image", { fetchResult });
    await vi.advanceTimersByTimeAsync(2_000 * 5);   // 每次轮询 2s；第 5 次 fetch 判定失败
    const outcome = await outcomePromise;
    expect(outcome).toEqual({ kind: "failed", error: "出图任务已丢失（ComfyUI 可能已重启）" });
    expect(fetchResult).toHaveBeenCalledTimes(5);
  });

  it("running 撑到 pollSchedule 硬上限（210 tries）→ still_running，不谎报失败", async () => {
    vi.useFakeTimers();
    const fetchResult = vi.fn(async (): Promise<GenResult> => running);
    const outcomePromise = pollWorkflowResult("p1", "http://comfy", "image", { fetchResult });
    // 图片合同：149×2s + 60×15s ≈ 20 分钟硬上限；第 210 次 fetch 后 pollSchedule(210)=null
    await vi.advanceTimersByTimeAsync(2_000 * 150 + 15_000 * 61);
    const outcome = await outcomePromise;
    expect(outcome).toEqual({ kind: "still_running" });
    expect(fetchResult).toHaveBeenCalledTimes(210);
  });
});

describe("canvas workflow background activity (trackCanvasWorkflow/untrackCanvasWorkflow)", () => {
  function stubLocalStorage() {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) || null,
      setItem: (key: string, value: string) => { values.set(key, value); },
    });
    return values;
  }

  it("track writes laf_pending_gen_<threadId> so SupportWidget shows 出图中", () => {
    const values = stubLocalStorage();
    try {
      trackCanvasWorkflow("repo-a", "p1", "http://comfy", "模板A");
      const parsed = JSON.parse(values.get("laf_pending_gen_repo-a") || "[]");
      expect(parsed).toHaveLength(1);
      expect(parsed[0]).toMatchObject({ prompt_id: "p1", prompt: "模板A" });
      expect(typeof parsed[0].createdAt).toBe("number");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("track with runId stores runId so the chat progress bar can restore after remount", () => {
    const values = stubLocalStorage();
    try {
      trackCanvasWorkflow("repo-a", "p1", "http://comfy", "模板A", "wfrun-abc123");
      const parsed = JSON.parse(values.get("laf_pending_gen_repo-a") || "[]");
      expect(parsed[0]).toMatchObject({ prompt_id: "p1", prompt: "模板A", runId: "wfrun-abc123" });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("untrack removes only the matching prompt_id", () => {
    const values = stubLocalStorage();
    try {
      trackCanvasWorkflow("repo-a", "p1", "http://comfy", "A");
      trackCanvasWorkflow("repo-a", "p2", "http://comfy", "B");
      untrackCanvasWorkflow("repo-a", "p1");
      const parsed = JSON.parse(values.get("laf_pending_gen_repo-a") || "[]");
      expect(parsed.map((x: { prompt_id: string }) => x.prompt_id)).toEqual(["p2"]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("untrack on empty id is a no-op (no throw)", () => {
    const values = stubLocalStorage();
    try {
      expect(() => untrackCanvasWorkflow("repo-a", "")).not.toThrow();
      expect(JSON.parse(values.get("laf_pending_gen_repo-a") || "[]")).toEqual([]);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("pollWorkflowResult 停顿守卫（同步路径，2026-08-29 卡死自愈）", () => {
  const waitUntil = async (cond: () => boolean) => {
    for (let i = 0; i < 60 && !cond(); i++) await Promise.resolve();
  };

  it("一直排队（无节点运转）超过 stall 窗口 → stalled", async () => {
    const scheduled: Array<() => void> = [];
    let current = 0;
    const fetchResult = vi.fn(async (): Promise<GenResult> => ({
      status: "pending", images: [], videos: [], audios: [], texts: [],
    }));
    const promise = pollWorkflowResult("p1", "http://comfy", "image", {
      fetchResult,
      schedule: (cb: () => void) => { scheduled.push(cb); },
      now: () => current,
    });
    await waitUntil(() => scheduled.length >= 1);
    expect(fetchResult).toHaveBeenCalledTimes(1);
    current = DEFAULT_STALL_TIMEOUT_MS + 1;
    scheduled.shift()!();
    const outcome = await promise;
    expect(outcome.kind).toBe("stalled");
  });

  it("节点开始运转（completed 观察到）则不误杀", async () => {
    const scheduled: Array<() => void> = [];
    let current = 0;
    const fetchResult = vi.fn(async (): Promise<GenResult> => ({
      status: current > 0 ? "completed" : "pending",
      images: [{ filename: "a.png", subfolder: "", type: "output" }],
      videos: [], audios: [], texts: [],
    }));
    const promise = pollWorkflowResult("p1", "http://comfy", "image", {
      fetchResult,
      schedule: (cb: () => void) => { scheduled.push(cb); },
      now: () => current,
    });
    await waitUntil(() => scheduled.length >= 1);
    scheduled.shift()!();   // 第 2 次 fetch：仍 pending，停顿计时中但未越窗
    await waitUntil(() => scheduled.length >= 2);
    current = DEFAULT_STALL_TIMEOUT_MS + 1;
    scheduled.shift()!();   // 第 3 次 fetch：completed → 不触发守卫
    const outcome = await promise;
    expect(outcome.kind).toBe("complete");
  });
});
