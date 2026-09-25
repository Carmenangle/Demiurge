import { afterEach, describe, expect, it, vi } from "vitest";
import type { GenResult } from "../api/comfyui";
import { WorkflowGenerationRuntime, advanceWorkflowTick, getRuntime, initialTickState, pollSchedule, pollWorkflowResult, DEFAULT_STALL_TIMEOUT_MS, durableFinalizeSucceeded, threadKey, migrateLegacyEmptyThreadKey, STALLED_POLL_MESSAGE } from "./workflowGenerationRuntime";
import type { WorkflowTickContext } from "./workflowGenerationRuntime";

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

  // ── P13（2026-09-15）：`stalled` 转发已产出的 error ──────────────────────────
  //
  // 改造前：`stalled?: (pending) => void` **不转发** `decision.error`，消费方只能自己
  // `import STALLED_POLL_MESSAGE` 再塞回（画布侧 `runCanvasWorkflow` 就是搬同一个常量，
  // 对话侧则硬编码同字面值）⇒「文案归 runtime」名存实亡，改文案要改 3 处。
  // 现在：runtime 在派发点把 `STALLED_POLL_MESSAGE` 当第二参传出去，消费方直接用参数。
  it("P13：停顿守卫把已产出的 error 经第二参转发（消费方无需再 import 常量）", async () => {
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

    current = 1000;
    await scheduled.shift()!();
    expect(watch.stalled).toHaveBeenCalledWith(
      expect.objectContaining({ prompt_id: "p1" }), STALLED_POLL_MESSAGE,
    );
  });

  it("P13：未提供 `stalled` 时仍落到 `timedOut`（`??` 合并调用改 if/else 后行为不变）", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    let current = 0;
    // 刻意**不含** stalled：验证缺省回退分支
    const watch = {
      finalize: vi.fn(async () => true), completed: vi.fn(), failed: vi.fn(),
      released: vi.fn(), timedOut: vi.fn(),
    };
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => current,
      fetchResult: vi.fn(async (): Promise<GenResult> => ({
        status: "pending", images: [], videos: [], audios: [], texts: [],
      })),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy", stallTimeoutMs: 1000 }, watch);

    current = 1000;
    await scheduled.shift()!();
    expect(watch.timedOut).toHaveBeenCalledWith(expect.objectContaining({ prompt_id: "p1" }));
  });
});

// ===== V1.3 视频超时独立于图片 =====
describe("pollSchedule · 媒体类型超时合同", () => {
  it("图片：5 分钟（150×2s）释放忙碌，30 分钟（250 tries）硬上限", () => {
    expect(pollSchedule(149)).toEqual({ releaseBusy: false, delayMs: 2000 });
    expect(pollSchedule(150)).toEqual({ releaseBusy: true, delayMs: 15000 });
    expect(pollSchedule(151)).toEqual({ releaseBusy: false, delayMs: 15000 });
    expect(pollSchedule(209)).toEqual({ releaseBusy: false, delayMs: 15000 });
    expect(pollSchedule(250)).toEqual({ releaseBusy: false, delayMs: null });
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

  it("running 撑到 pollSchedule 硬上限（250 tries）→ still_running，不谎报失败", async () => {
    vi.useFakeTimers();
    const fetchResult = vi.fn(async (): Promise<GenResult> => running);
    const outcomePromise = pollWorkflowResult("p1", "http://comfy", "image", { fetchResult });
    // 图片合同：149×2s + 100×15s ≈ 30 分钟硬上限；第 250 次 fetch 后 pollSchedule(250)=null
    await vi.advanceTimersByTimeAsync(2_000 * 150 + 15_000 * 101);
    const outcome = await outcomePromise;
    expect(outcome).toEqual({ kind: "still_running" });
    expect(fetchResult).toHaveBeenCalledTimes(250);
  });
});

// ===== 2026-09-15 C4-A Step 2：每 threadId 唯一 runtime + inspect 恢复不丢字段 =====
describe("getRuntime · 每 threadId 唯一实例", () => {
  it("同一 threadId 返回同一实例，不同 threadId 各自一个", () => {
    const first = getRuntime("t-same");
    expect(getRuntime("t-same")).toBe(first);
    expect(getRuntime("t-other")).not.toBe(first);
  });

  it("单例跟随重新 stub 的全局 localStorage（惰性解析，不缓存首个用例的 stub）", () => {
    const firstStub = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => firstStub.get(key) ?? null,
      setItem: (key: string, value: string) => { firstStub.set(key, value); },
    });
    try {
      getRuntime("t-swap").track({ promptId: "p1", comfyuiUrl: "http://comfy" });
      expect(JSON.parse(firstStub.get("laf_pending_gen_t-swap") ?? "[]")).toHaveLength(1);
    } finally {
      vi.unstubAllGlobals();
    }

    const secondStub = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => secondStub.get(key) ?? null,
      setItem: (key: string, value: string) => { secondStub.set(key, value); },
    });
    try {
      // 复用同一单例实例，但必须写进**新**的 stub——这是单例在测试里可用的前提
      getRuntime("t-swap").track({ promptId: "p2", comfyuiUrl: "http://comfy" });
      expect(firstStub.has("laf_pending_gen_t-swap")).toBe(true);   // 旧 stub 未被再写
      expect(JSON.parse(secondStub.get("laf_pending_gen_t-swap") ?? "[]")
        .map((x: { prompt_id: string }) => x.prompt_id)).toEqual(["p2"]);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("inspect 恢复不丢字段（C4-A Step 2 / R9）", () => {
  it("恢复后 pending 仍保留 mediaType/runId/baseSlotRef", async () => {
    const storage = memoryStorage();
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("recover", {
      storage, now: () => 10, schedule: () => 0,
      fetchResult: vi.fn(async (): Promise<GenResult> => ({
        status: "running", images: [], videos: [], audios: [], texts: [],
      })),
    });
    const tracked = runtime.track({
      promptId: "p1", comfyuiUrl: "http://comfy", mediaType: "video", runId: "wfrun-1",
      baseSlotRef: { messageId: "m1", slotId: "s1" },
    });

    // inspect 发现仍在 running ⇒ 交回 start 继续守望，途中会 track 重写这条 pending
    expect(await runtime.inspect(tracked, "http://comfy", watch)).toBe("watching");
    expect(runtime.list()).toEqual([
      expect.objectContaining({
        prompt_id: "p1", mediaType: "video", runId: "wfrun-1",
        baseSlotRef: { messageId: "m1", slotId: "s1" },
      }),
    ]);
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

// ===== 2026-09-15 架构审计 C4-A 第 1 步：两套循环共用的 tick 核心 =====
//
// `WorkflowGenerationRuntime.start`（后台守望）与 `pollWorkflowResult`（同步轮询）此前各自
// 重写同一套判定（tries / not_found 连击 / 停顿守卫 / pollSchedule），是「时机与判定有两个属主」
// 的前科来源。判定收进 `advanceWorkflowTick` 后，用它当回归网钉住语义：
// 下面这些用例是 Step 2–5 把三个调用方搬到 runtime 时必须持续为真的不变量。
describe("advanceWorkflowTick · 后台守望/同步轮询共用判定核心", () => {
  const ctx = (over: Partial<WorkflowTickContext> = {}): WorkflowTickContext => ({
    now: 0, mediaType: "image", stallWindowMs: 1000, stallBasis: "created_at", createdAt: 0, ...over,
  });

  it("completed → complete，且 tries 前进一步", () => {
    const step = advanceWorkflowTick(initialTickState(), { ok: true, status: "completed" }, ctx());
    expect(step.decision).toEqual({ kind: "complete" });
    expect(step.state.tries).toBe(1);
  });

  it("failed → failed 且带回上游错误文案；无文案时退回默认", () => {
    expect(advanceWorkflowTick(initialTickState(), { ok: true, status: "failed", error: "采样失败" }, ctx()).decision)
      .toEqual({ kind: "failed", notFound: false, error: "采样失败" });
    expect(advanceWorkflowTick(initialTickState(), { ok: true, status: "failed" }, ctx()).decision)
      .toEqual({ kind: "failed", notFound: false, error: "ComfyUI 工作流执行失败" });
  });

  it("not_found 连击到第 5 次才判任务丢失", () => {
    let state = initialTickState();
    for (let index = 1; index <= 4; index += 1) {
      const step = advanceWorkflowTick(state, { ok: true, status: "not_found" }, ctx());
      state = step.state;
      expect(step.decision.kind).toBe("poll");
    }
    expect(advanceWorkflowTick(state, { ok: true, status: "not_found" }, ctx()).decision)
      .toEqual({ kind: "failed", notFound: true, error: "出图任务已丢失" });
  });

  it("中间夹一次 pending 会清零 not_found 连击（不跨状态累计）", () => {
    let state = initialTickState();
    for (let index = 0; index < 4; index += 1) {
      state = advanceWorkflowTick(state, { ok: true, status: "not_found" }, ctx()).state;
    }
    state = advanceWorkflowTick(state, { ok: true, status: "pending" }, ctx()).state;
    expect(state.consecutiveNotFound).toBe(0);
    for (let index = 0; index < 4; index += 1) {
      const step = advanceWorkflowTick(state, { ok: true, status: "not_found" }, ctx());
      state = step.state;
      expect(step.decision.kind).toBe("poll");
    }
  });

  it("观察到 running 后永久免除停顿守卫（seenRunning 不回落）", () => {
    let state = initialTickState();
    state = advanceWorkflowTick(state, { ok: true, status: "running" }, ctx()).state;
    expect(state.seenRunning).toBe(true);
    expect(advanceWorkflowTick(state, { ok: true, status: "pending" }, ctx({ now: 999_999 })).decision.kind)
      .toBe("poll");
  });

  it("停顿口径差异：created_at 对任意观测计时，first_pending 只在 pending 上计时", () => {
    // 后台守望（created_at）：即便 ComfyUI 暂不可达（ok:false）也照常计时 → 早停
    expect(advanceWorkflowTick(initialTickState(), { ok: false }, ctx({ now: 1000 })).decision.kind)
      .toBe("stalled");

    // 同步轮询（first_pending）：ok:false 不推进停顿计时，只是继续等（硬超时兜底）
    const state = initialTickState();
    expect(advanceWorkflowTick(state, { ok: false }, ctx({ now: 1000, stallBasis: "first_pending" })).decision.kind)
      .toBe("poll");

    // 基准取「首个 pending 观测」而非任务创建时刻：第二跳才可能越窗
    const first = advanceWorkflowTick(state, { ok: true, status: "pending" }, ctx({ now: 100, stallBasis: "first_pending" }));
    expect(first.decision.kind).toBe("poll");
    expect(first.state.stallBasisAt).toBe(100);
    expect(advanceWorkflowTick(first.state, { ok: true, status: "pending" }, ctx({ now: 1100, stallBasis: "first_pending" })).decision.kind)
      .toBe("stalled");
  });

  it("到达 pollSchedule 硬上限 → still_running（不谎报失败）", () => {
    let state = initialTickState();
    for (let index = 0; index < 249; index += 1) {
      state = advanceWorkflowTick(state, { ok: true, status: "running" }, ctx()).state;
    }
    expect(advanceWorkflowTick(state, { ok: true, status: "running" }, ctx()).decision)
      .toEqual({ kind: "still_running" });
  });

  it("released 只在释放忙碌那一次 tick 置位（图片 150 tries）", () => {
    let state = initialTickState();
    for (let index = 0; index < 148; index += 1) {
      state = advanceWorkflowTick(state, { ok: true, status: "running" }, ctx()).state;
    }
    const at149 = advanceWorkflowTick(state, { ok: true, status: "running" }, ctx());
    expect(at149.decision).toEqual({ kind: "poll", released: false, delayMs: 2000 });
    expect(advanceWorkflowTick(at149.state, { ok: true, status: "running" }, ctx()).decision)
      .toEqual({ kind: "poll", released: true, delayMs: 15000 });
  });

  // ── 第 59 刀（队列感知停顿）：ComfyUI 有任务在执行 ⇒ 队列在推进 ⇒ 深队列排队
  //    是合法等待，停顿基准被存活观测顶起；队列空转后才起算停顿窗口。──────────

  it("第59刀 created_at 基准：队列在推进（queueBusy）就不算停顿，空转后才起算", () => {
    // 任务创建于 0，窗口 1000ms；ComfyUI 一直在跑别的任务（queueBusy=true）
    let state = initialTickState();
    let now = 0;
    for (let step = 0; step < 5; step += 1) {
      now = step * 700;                     // 0 / 700 / 1400 / 2100 / 2800 —— 早已越过窗口
      state = advanceWorkflowTick(
        state, { ok: true, status: "pending", queueBusy: true },
        ctx({ now, stallBasis: "created_at", createdAt: 0 }),
      ).state;
    }
    expect(advanceWorkflowTick(
      state, { ok: true, status: "pending", queueBusy: true },
      ctx({ now: now + 700, stallBasis: "created_at", createdAt: 0 }),
    ).decision.kind).toBe("poll");          // 队列一直在推进 ⇒ 永不停顿

    // 队列空转（queueBusy 消失）⇒ 从最后一次存活时刻（2800）起算停顿窗口
    const idle = advanceWorkflowTick(
      state, { ok: true, status: "pending" },
      ctx({ now: now + 700, stallBasis: "created_at", createdAt: 0 }),
    );
    expect(idle.decision.kind).toBe("poll");                      // 空转首跳：窗口刚起算
    expect(advanceWorkflowTick(
      idle.state, { ok: true, status: "pending" },
      ctx({ now: now + 700 + 1000, stallBasis: "created_at", createdAt: 0 }),
    ).decision.kind).toBe("stalled");                             // 空转超窗 ⇒ 停顿
  });

  it("第59刀 同步口径（first_pending）：busy 重置停顿计时，重新起算", () => {
    const first = advanceWorkflowTick(
      initialTickState(), { ok: true, status: "pending" },
      ctx({ now: 100, stallBasis: "first_pending" }));
    expect(first.state.stallBasisAt).toBe(100);
    // 队列在推进 ⇒ 计时重置（-1），下次非 busy 观测重新起算
    const busy = advanceWorkflowTick(
      first.state, { ok: true, status: "pending", queueBusy: true },
      ctx({ now: 1100, stallBasis: "first_pending" }));
    expect(busy.state.stallBasisAt).toBe(-1);
    expect(busy.decision.kind).toBe("poll");
    // 重新起算后一个窗口内不判停顿
    expect(advanceWorkflowTick(
      busy.state, { ok: true, status: "pending" }, ctx({ now: 2000, stallBasis: "first_pending" }),
    ).decision.kind).toBe("poll");
  });
});

// ===== 两套循环的推进不变量 =====
//
// 「tries 必须与 fetch 是否成功无关地前进」——原实现把 `tries += 1` 写在 fetch 之前；
// 抽公共核心时若把自增放进「有观测才推进」的分支，fetch 持续抛错就会永远停在 2s 档、
// 硬超时永不触发（≡ 无限轮询）。这两条用例把该不变量钉在两套循环上。
describe("tick 推进不变量：fetch 抛错不影响计时", () => {
  it("同步轮询：fetch 全程抛错仍走到图片硬超时（250 tries）→ still_running", async () => {
    vi.useFakeTimers();
    try {
      const fetchResult = vi.fn(async (): Promise<GenResult> => { throw new Error("ComfyUI down"); });
      const promise = pollWorkflowResult("p1", "http://comfy", "image", { fetchResult });
      await vi.advanceTimersByTimeAsync(2_000 * 150 + 15_000 * 101);
      expect(await promise).toEqual({ kind: "still_running" });
      expect(fetchResult).toHaveBeenCalledTimes(250);
    } finally {
      vi.useRealTimers();
    }
  });

  it("后台守望：fetch 全程抛错仍走到硬超时 → timedOut（不停顿、不死循环）", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("work", {
      storage: memoryStorage(), now: () => 10,
      fetchResult: vi.fn(async (): Promise<GenResult> => { throw new Error("ComfyUI down"); }),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);

    // 初排 + 每次 tick 各入队一次；第 250 次 tick 判定硬超时且不再入队
    for (let index = 0; index < 250; index += 1) {
      const callback = scheduled.shift();
      expect(callback, `第 ${index + 1} 次 tick 应当已被调度`).toBeTruthy();
      await callback!();
    }
    expect(watch.timedOut).toHaveBeenCalledOnce();
    expect(watch.failed).not.toHaveBeenCalled();
    expect(watch.completed).not.toHaveBeenCalled();
  });
});

// ── P17（2026-09-15）：线程键单点 + 旧空键在途任务迁移 ───────────────────────
describe("threadKey · 线程键单点推导", () => {
  it("仓库 id 原样返回；空串 / null / undefined → home", () => {
    expect(threadKey("repo-a")).toBe("repo-a");
    expect(threadKey("")).toBe("home");
    expect(threadKey(null)).toBe("home");
    expect(threadKey(undefined)).toBe("home");
  });

  it("空键与 home 归一到同一个 runtime 实例（首页任务不再分裂成两个判重域）", () => {
    // 收口前：画布 `getRuntime("")` 与对话 `getRuntime("home")` 是两个实例、两套 finalized/cancelled，
    // 同一 pending 可被归档两次，且首页任务不被恢复。
    expect(getRuntime(threadKey(""))).toBe(getRuntime(threadKey(null)));
    expect(getRuntime(threadKey(null))).toBe(getRuntime("home"));
  });
});

describe("migrateLegacyEmptyThreadKey · 旧空键 pending 迁移", () => {
  function storageWith(initial: Record<string, string>) {
    const values = new Map(Object.entries(initial));
    return {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
      dump: () => Object.fromEntries(values),
    };
  }

  it("把 `laf_pending_gen_` 下的在途任务并入 `_home`，并删掉旧键", () => {
    const storage = storageWith({
      "laf_pending_gen_": JSON.stringify([{ prompt_id: "p1", createdAt: 1 }]),
      "laf_pending_gen_home": JSON.stringify([{ prompt_id: "p0", createdAt: 0 }]),
    });
    expect(migrateLegacyEmptyThreadKey(storage)).toBe(1);
    const dump = storage.dump();
    expect(dump["laf_pending_gen_"]).toBeUndefined();
    expect(
      (JSON.parse(dump["laf_pending_gen_home"]) as Array<{ prompt_id: string }>).map((i) => i.prompt_id),
    ).toEqual(["p0", "p1"]);   // 已存在的 home 分片在前，旧键条目按 prompt_id 去重后追加
  });

  it("幂等：重复调用是空操作（旧键已删）", () => {
    const storage = storageWith({
      "laf_pending_gen_": JSON.stringify([{ prompt_id: "p1", createdAt: 1 }]),
    });
    expect(migrateLegacyEmptyThreadKey(storage)).toBe(1);
    expect(migrateLegacyEmptyThreadKey(storage)).toBe(0);
  });

  it("无旧键 / 旧键为空数组 → 空操作，且不新建 home 键", () => {
    expect(migrateLegacyEmptyThreadKey(storageWith({}))).toBe(0);
    const empty = storageWith({ "laf_pending_gen_": "[]" });
    expect(migrateLegacyEmptyThreadKey(empty)).toBe(0);
    expect(empty.dump()["laf_pending_gen_"]).toBe("[]");
    expect(empty.dump()["laf_pending_gen_home"]).toBeUndefined();
  });

  it("旧键内容损坏（非 JSON）→ 不动旧键、返回 0（绝不因此抛穿启动路径）", () => {
    const broken = storageWith({ "laf_pending_gen_": "{oops" });
    expect(migrateLegacyEmptyThreadKey(broken)).toBe(0);
    expect(broken.dump()["laf_pending_gen_"]).toBe("{oops");
  });

  it("无条目带 prompt_id → 不并入、但旧键仍被清掉（避免每次调用重复扫描）", () => {
    const storage = storageWith({ "laf_pending_gen_": JSON.stringify([{ createdAt: 1 }]) });
    expect(migrateLegacyEmptyThreadKey(storage)).toBe(0);
    expect(storage.dump()["laf_pending_gen_"]).toBeUndefined();
    expect(storage.dump()["laf_pending_gen_home"]).toBe("[]");
  });
});

// ── P11（2026-09-15）：`produced` 必须原样透传给 observer.completed 的第三参 ────────
//
// 画布/弹窗的 completed/empty 判定改用它之后（见 `runCanvasWorkflow.ts`），
// 这条**唯一来源**就成了画布空判定的命门：runtime 若改成传常量或漏传，
// 画布会静默退回「永远 reported 完成」或「永远 reported 为空」，而行为网测不出来。
// 两条路径（后台守望 tick / 恢复 inspect）都要透传。
describe("P11 · completed 第三参透传 finalize 的返回值", () => {
  async function completedProduced(path: "tick" | "inspect", value: boolean): Promise<unknown> {
    const scheduled: Array<() => Promise<void> | void> = [];
    const watch = { ...observer(), finalize: vi.fn(async () => value) };
    const runtime = new WorkflowGenerationRuntime(`p11-${path}-${value}`, {
      storage: memoryStorage(), now: () => 10,
      fetchResult: vi.fn(async () => completed),
      schedule: (callback) => scheduled.push(callback),
    });
    if (path === "tick") {
      runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
      await scheduled.shift()!();
    } else {
      const item = runtime.track({ promptId: "p1", comfyuiUrl: "http://comfy" });
      await runtime.inspect(item, "http://comfy", watch);
    }
    expect(watch.completed).toHaveBeenCalledOnce();
    return watch.completed.mock.calls[0][2];
  }

  it("后台守望（tick）路径：false 原样透传（画布据此判 empty）", async () => {
    expect(await completedProduced("tick", false)).toBe(false);
  });

  it("后台守望（tick）路径：true 原样透传", async () => {
    expect(await completedProduced("tick", true)).toBe(true);
  });

  it("恢复（inspect）路径：同样透传 false", async () => {
    expect(await completedProduced("inspect", false)).toBe(false);
  });
});

// ── P12 佐证（2026-09-15）：终态对 `runtime.pending` 的**去留** ──────────────────────
//
// 架构审查 P12 曾建议把 `useChatSession` 的「前台任务 FIFO」（`activePromptQueue`）
// 换成「从 `runtime.list()` 投影」。**实测否决**：`pending` 是**持久化记录**（刷新后据此恢复），
// 不是「在跑的任务」—— 下面两组用例把这条写死：
//   · `completed` / `failed` / `stalled` 会 `remove` ⇒ 事后 list 里没有它；
//   · `timed_out` / `released`（= 转后台继续/放弃守望）**不移除** ⇒ 事后 list 里**还在**。
// 若按 list() 投影，「超时」的任务会被算成仍在跑 ⇒ 进度条永不结束、队列永不空闲。
// 所以前台队列必须独立存在；P12 只把它收成单一属主（`lib/foregroundRunQueue.ts`）。
describe("P12 佐证 · 终态对 runtime.pending 的去留", () => {
  const pendingResult: GenResult = { status: "pending", images: [], videos: [], audios: [], texts: [] };

  /** 驱动后台守望直到 `scheduled` 耗尽或达到上限，返回实际跑过的 tick 数。 */
  async function driveTicks(scheduled: Array<() => Promise<void> | void>, limit: number): Promise<number> {
    let ran = 0;
    for (let index = 0; index < limit; index += 1) {
      const callback = scheduled.shift();
      if (!callback) break;
      await callback();
      ran += 1;
    }
    return ran;
  }

  it("completed / failed / stalled 会 remove（事后 list 里没有它）", async () => {
    // completed
    {
      const scheduled: Array<() => Promise<void> | void> = [];
      const watch = observer();
      const runtime = new WorkflowGenerationRuntime("p12-rm-completed", {
        storage: memoryStorage(), now: () => 10, fetchResult: vi.fn(async () => completed),
        schedule: (callback) => scheduled.push(callback),
      });
      runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
      await scheduled.shift()!();
      expect(watch.completed).toHaveBeenCalledOnce();
      expect(runtime.list()).toEqual([]);
    }
    // failed
    {
      const scheduled: Array<() => Promise<void> | void> = [];
      const watch = observer();
      const runtime = new WorkflowGenerationRuntime("p12-rm-failed", {
        storage: memoryStorage(), now: () => 10,
        fetchResult: vi.fn(async (): Promise<GenResult> => ({
          status: "failed", error: "采样失败", images: [], videos: [], audios: [], texts: [],
        })),
        schedule: (callback) => scheduled.push(callback),
      });
      runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
      await scheduled.shift()!();
      expect(watch.failed).toHaveBeenCalledOnce();
      expect(runtime.list()).toEqual([]);
    }
    // stalled（停顿守卫越窗）
    {
      const scheduled: Array<() => Promise<void> | void> = [];
      const watch = observer();
      let current = 0;
      const runtime = new WorkflowGenerationRuntime("p12-rm-stalled", {
        storage: memoryStorage(), now: () => current,
        fetchResult: vi.fn(async () => pendingResult),
        schedule: (callback) => scheduled.push(callback),
      });
      runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
      await scheduled.shift()!();
      expect(runtime.list()).toHaveLength(1);
      current = DEFAULT_STALL_TIMEOUT_MS + 1;
      await scheduled.shift()!();
      expect(watch.stalled).toHaveBeenCalledOnce();
      expect(runtime.list()).toEqual([]);
    }
  });

  it("timed_out **不移除** pending（= 转后台继续，刷新后据此恢复）", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("p12-keep-timeout", {
      storage: memoryStorage(), now: () => 10, fetchResult: vi.fn(async () => pendingResult),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
    const ran = await driveTicks(scheduled, 260);
    expect(ran).toBeGreaterThan(0);
    expect(watch.timedOut).toHaveBeenCalledOnce();
    expect(runtime.list()).toHaveLength(1);   // ★ 仍在
  });

  it("released（释放忙碌 / 转后台）同样**不移除** pending", async () => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const watch = observer();
    const runtime = new WorkflowGenerationRuntime("p12-keep-released", {
      storage: memoryStorage(), now: () => 10, fetchResult: vi.fn(async () => pendingResult),
      schedule: (callback) => scheduled.push(callback),
    });
    runtime.start({ promptId: "p1", comfyuiUrl: "http://comfy" }, watch);
    const ran = await driveTicks(scheduled, 160);
    expect(ran).toBeGreaterThan(0);
    expect(watch.released).toHaveBeenCalled();
    expect(runtime.list()).toHaveLength(1);   // ★ 仍在
  });
});

// ===== P14（2026-09-15）：导出面收窄 —— 公开门面等价网 =====
//
// 背景：`registerPending` / `unregisterPending` / `pendingResumeAction` / `generationResultAction` /
// `notFoundPollAction` / `shouldFinalize` 六个纯判定函数在 runtime 内部**都有生产调用**
// （`track` / `remove` / `recoveryAction` / `inspect` / `advanceWorkflowTick` / `finalize`），
// 但**对外零生产消费方**：唯一外部引用是 `lib/chatGeneration.test.ts`，而那是 `chatGeneration.ts`
// 的测试文件（这些函数早已搬来本模块，测试没跟着搬 ⇒ 测试归属错位）。
// ⇒ 六个函数降为**模块内可见**，导出面不再暴露纯判定实现细节。
//
// 顺序纪律：**先用公开门面把行为钉死、再收窄导出**。本块就是那张网 ——
// 每条用例都只经公开门面（`track` / `remove` / `recoveryAction` / `inspect`）断言
// 「同一输入 → 同一结果」，逐条承接 `chatGeneration.test.ts` 里原先直连六个函数的断言，
// 证明收窄导出**零覆盖损失**。`notFoundPollAction` 的等价网是 `advanceWorkflowTick`
// 的「not_found 连击到第 5 次才判丢失」（本文件上方已覆盖），故此处不重复。
// 形态面（六个名字不得再次 `export`）由 `workflowGenerationRuntime.contract.test.ts` 源码锁兜住。
describe("P14 · 公开门面等价网（导出面收窄前先钉行为）", () => {
  const make = (threadId: string, now: () => number = () => 10, fetchResult?: () => Promise<GenResult>) => {
    const scheduled: Array<() => Promise<void> | void> = [];
    const runtime = new WorkflowGenerationRuntime(threadId, {
      storage: memoryStorage(), now,
      fetchResult: vi.fn(fetchResult ?? (async () => completed)),
      schedule: (callback) => scheduled.push(callback),
    });
    return { runtime, scheduled };
  };

  it("track：同 promptId 去重后追加（顺序稳定），且不触碰既有条目", () => {
    let clock = 1;
    const { runtime } = make("p14-track-dedupe", () => clock);
    runtime.track({ promptId: "p1", comfyuiUrl: "http://comfy" });
    clock = 2;
    runtime.track({ promptId: "p2", comfyuiUrl: "http://comfy" });
    clock = 3;
    runtime.track({ promptId: "p1", comfyuiUrl: "http://comfy" });
    expect(runtime.list()).toEqual([
      { prompt_id: "p2", createdAt: 2 },
      { prompt_id: "p1", createdAt: 3 },
    ]);
  });

  it("track：outputNodeIds 非空才落键（空数组省略该键，不是 undefined 占位）", () => {
    const { runtime } = make("p14-track-nodes", () => 1);
    const withNodes = runtime.track({ promptId: "p1", comfyuiUrl: "http://comfy", outputNodeIds: ["47"] });
    expect(withNodes).toEqual({ prompt_id: "p1", createdAt: 1, outputNodeIds: ["47"] });
    expect(Object.keys(withNodes)).toContain("outputNodeIds");

    const bare = runtime.track({ promptId: "p2", comfyuiUrl: "http://comfy", outputNodeIds: [] });
    expect(bare).toEqual({ prompt_id: "p2", createdAt: 1 });
    expect(Object.keys(bare)).not.toContain("outputNodeIds");   // ★ 键省略
  });

  it("track：regeneration / target / prompt 完整固化（自动插画任务恢复后仍可重放）", () => {
    const regeneration = {
      kind: "template" as const, templateId: "tpl",
      values: { "39.text": "masterpiece\n1girl" },
      comfyuiUrl: "http://127.0.0.1:8188", outputNodeIds: ["45"], prompt: "masterpiece\n1girl",
    };
    const { runtime } = make("p14-track-fields", () => 1);
    const item = runtime.track({
      promptId: "p1", comfyuiUrl: "http://comfy", outputNodeIds: ["45"], regeneration,
      target: { messageId: "bot-1", slotId: "slot-1", background: true },
      prompt: "masterpiece\n1girl, climax action",
    });
    expect(item).toEqual({
      prompt_id: "p1", createdAt: 1, outputNodeIds: ["45"], regeneration,
      target: { messageId: "bot-1", slotId: "slot-1", background: true },
      prompt: "masterpiece\n1girl, climax action",
    });
  });

  it("track：owner / mediaType / baseSlotRef / runId 有条件固化（缺省时不落 undefined 键）", () => {
    const { runtime } = make("p14-track-optional", () => 1);
    const bare = runtime.track({ promptId: "p1", comfyuiUrl: "http://comfy" });
    expect(Object.keys(bare)).toEqual(["prompt_id", "createdAt"]);   // ★ 键序 = 拼装顺序

    const full = runtime.track({
      promptId: "p2", comfyuiUrl: "http://comfy", mediaType: "video", runId: "run-9",
      owner: { threadId: "repo-1", repoId: "repo-1", outputDir: "out" },
      baseSlotRef: { messageId: "m1", slotId: "s1" },
    });
    expect(full).toEqual({
      prompt_id: "p2", createdAt: 1, mediaType: "video", runId: "run-9",
      owner: { threadId: "repo-1", repoId: "repo-1", outputDir: "out" },
      baseSlotRef: { messageId: "m1", slotId: "s1" },
    });
  });

  it("remove：只移除指定任务并保持顺序；移除不存在的 id 原样不动", () => {
    let clock = 1;
    const { runtime } = make("p14-remove", () => clock);
    runtime.track({ promptId: "p1", comfyuiUrl: "http://comfy" });
    clock = 2;
    runtime.track({ promptId: "p2", comfyuiUrl: "http://comfy" });
    runtime.remove("p1");
    expect(runtime.list()).toEqual([{ prompt_id: "p2", createdAt: 2 }]);
    runtime.remove("missing");
    expect(runtime.list()).toEqual([{ prompt_id: "p2", createdAt: 2 }]);
  });

  it("recoveryAction：已处理 → skip；6 小时边界（含端点）→ inspect / expire", () => {
    // 第 58 刀：批量生图（999 串行提交＋模型首载）会越过 30 分钟，过期窗口放宽到 6 小时
    const item = { prompt_id: "p1", createdAt: 1000 };
    const at = (now: number) => make("p14-recovery", () => now).runtime;
    expect(at(1000).recoveryAction(item, new Set(["p1"]))).toBe("skip");
    expect(at(1000 + 6 * 60 * 60 * 1000).recoveryAction(item, new Set())).toBe("inspect");   // 恰 6 小时仍可恢复
    expect(at(1001 + 6 * 60 * 60 * 1000).recoveryAction(item, new Set())).toBe("expire");   // 越界即过期
  });

  it("inspect：completed → 归档并返回 complete；failed → 立即失败；其它 → 转后台守望", async () => {
    const { runtime: completedRun } = make("p14-inspect-complete", () => 1);
    let watch = observer();
    expect(await completedRun.inspect({ prompt_id: "p1", createdAt: 1 }, "http://comfy", watch))
      .toBe("complete");
    expect(watch.finalize).toHaveBeenCalledOnce();
    expect(watch.completed).toHaveBeenCalledOnce();

    const { runtime: failedRun } = make("p14-inspect-failed", () => 1, async () => ({
      status: "failed", error: "采样失败", images: [], videos: [], audios: [], texts: [],
    }));
    watch = observer();
    expect(await failedRun.inspect({ prompt_id: "p1", createdAt: 1 }, "http://comfy", watch))
      .toBe("failed");
    expect(watch.failed).toHaveBeenCalledWith(expect.anything(), "execution", "采样失败");

    const { runtime: runningRun } = make("p14-inspect-running", () => 1, async () => ({
      status: "running", images: [], videos: [], audios: [], texts: [],
    }));
    watch = observer();
    expect(await runningRun.inspect({ prompt_id: "p1", createdAt: 1 }, "http://comfy", watch))
      .toBe("watching");
    expect(runningRun.list().map((item) => item.prompt_id)).toEqual(["p1"]);   // 转守望后仍在途
  });

  it("shouldFinalize 等价：同一 prompt 二次归档被拦（finalize 恰一次）", async () => {
    const { runtime } = make("p14-finalize-once", () => 1);
    const watch = observer();
    const item = { prompt_id: "p1", createdAt: 1 };
    expect(await runtime.inspect(item, "http://comfy", watch)).toBe("complete");
    expect(await runtime.inspect(item, "http://comfy", watch)).toBe("complete");
    expect(watch.finalize).toHaveBeenCalledOnce();   // ★ 第二次被 finalized 集合拦下
  });

  it("shouldFinalize 等价：falsy promptId（老路径兼容）仍放行归档", async () => {
    // 原 `chatGeneration.test.ts` 测的是 `undefined`（该分支在生产不可达：`PendingGeneration.prompt_id`
    // 类型为 string）；公开门面能构造的 falsy 形态是空串 —— 与 `!promptId` 是同一分支，等价承接。
    const { runtime } = make("p14-finalize-falsy", () => 1);
    const watch = observer();
    expect(await runtime.inspect({ prompt_id: "", createdAt: 1 }, "http://comfy", watch))
      .toBe("complete");
    expect(watch.finalize).toHaveBeenCalledOnce();
  });
});
