import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * `runCanvasWorkflow` 的行为网（2026-09-15 架构审计 P10 · F1）。
 *
 * 这是**两条既有路径**（画布 `runWorkflowTask` / 弹窗 `handleRun`）收口后的共同行为网：
 * 调用顺序、事件 payload、提示文案逐字、终态五格、以及四处**刻意差异**逐格。
 *
 * 为什么锁在**事件与 sink 调用**上而不是渲染上：这条链的产物全是跨组件契约
 * （`laf-canvas-wf-run-graph` / `-progress` / `-done`），漏一条画布占位与 `ChatView`
 * 进度列表就会永久残留（审计 R1）；而两个组件零渲染级测试（审计 R5）。
 * 真机跨组件验收归 P20 的画布 e2e（清单里明确它是 P10/P12 的验收网）。
 *
 * ⚠ fixture 按**真实调用点**建两套 sink（画布 3 项 / 弹窗 7 项），**不用**一个「全功能 sink」：
 * 画布本就没有本地忙碌态/进度条，用一个什么都有 的 sink 会把「画布不该出现 teardown」
 * 这类分层差异测没。
 */

const mocks = vi.hoisted(() => ({
  submitGraph: vi.fn(),
  comfyStatus: vi.fn(),
  interruptComfy: vi.fn(),
  subscribeProgress: vi.fn(),
  getRuntime: vi.fn(),
  runtimeStart: vi.fn(),
  workflowGenMetadata: vi.fn(),
  workflowRegenerationSnapshot: vi.fn(),
  dispatchEvent: vi.fn(),
}));

vi.mock("../api/comfyui", () => ({
  submitGraph: mocks.submitGraph,
  comfyStatus: mocks.comfyStatus,
  interruptComfy: mocks.interruptComfy,
}));
vi.mock("./comfyProgress", () => ({ subscribeProgress: mocks.subscribeProgress }));
vi.mock("./workflowGenerationRuntime", () => ({
  getRuntime: mocks.getRuntime,
}));
vi.mock("./regeneration", () => ({
  workflowGenMetadata: mocks.workflowGenMetadata,
  workflowRegenerationSnapshot: mocks.workflowRegenerationSnapshot,
}));

import { runCanvasWorkflow, type CanvasWorkflowInput, type CanvasWorkflowSink } from "./runCanvasWorkflow";

const COMFY = "http://127.0.0.1:8188";
/** API 格式抓取图：无视频/音频输出节点 ⇒ mediaType 应为 image。 */
const GRAPH = { "3": { class_type: "KSampler", inputs: {} }, "9": { class_type: "SaveImage", inputs: {} } };
const VIDEO_GRAPH = { "3": { class_type: "KSampler" }, "12": { class_type: "VHS_VideoCombine" } };
const DRAFT = { nodes: [{ id: 3, type: "KSampler" }] };
const REGEN = { kind: "workflow", templateName: "T" };

const finalizeWorkflow = vi.fn(async () => true);

// ── 两个 fixture：除「刻意差异开关」外完全同形，差异取值与真实调用点逐字一致 ──────────
const CANVAS_MISSING_TEXT = "还没抓到画布内容，请先双击打开编辑器点「选择完毕」";
const MODAL_MISSING_TEXT = "还没抓到画布内容，请先在左栏点「选择完毕」。";
/** 两处逐字相同的提交提示（收口后只在原语里有一份）。 */
const SUBMIT_NOTIFY = "notify[info]:已提交到 ComfyUI（prompt_id: p-1，7 个节点），正在运转工作流…";

function canvasInput(patch: Partial<CanvasWorkflowInput> = {}, captured: unknown = GRAPH): CanvasWorkflowInput {
  return {
    captured, comfyuiUrl: COMFY, templateName: "T", wfDraft: DRAFT,
    threadId: "repo-1", outputDir: "/out", runId: "wfrun-abc",
    finalizeWorkflow,
    precheckComfyStatus: false, requireComfyUrl: true, emitInitialProgress: false,
    missingCapturedText: CANVAS_MISSING_TEXT,
    ...patch,
  };
}

function modalInput(patch: Partial<CanvasWorkflowInput> = {}, captured: unknown = GRAPH): CanvasWorkflowInput {
  return {
    captured, comfyuiUrl: COMFY, templateName: "T", wfDraft: DRAFT,
    threadId: "repo-1", outputDir: "/out", runId: "wfrun-abc",
    finalizeWorkflow,
    precheckComfyStatus: true, requireComfyUrl: false, emitInitialProgress: true,
    missingCapturedText: MODAL_MISSING_TEXT,
    ...patch,
  };
}

interface Harness { calls: string[]; sink: CanvasWorkflowSink }

/** 画布调用点提供的 sink 面：3 项（**没有**本地忙碌态/进度条/teardown）。 */
function canvasHarness(): Harness {
  const calls: string[] = [];
  return {
    calls,
    sink: {
      notify: (text, kind) => calls.push(`notify[${kind}]:${text}`),
      dispatchDone: () => calls.push("done"),
      reloadFromSnapshot: () => calls.push("reload"),
    },
  };
}

/** 弹窗调用点提供的 sink 面：7 项（多出 beginBusy / teardown / onProgressTick / onNodeTick）。 */
function modalHarness(): Harness {
  const calls: string[] = [];
  return {
    calls,
    sink: {
      notify: (text, kind) => calls.push(`notify[${kind}]:${text}`),
      dispatchDone: () => calls.push("done"),
      reloadFromSnapshot: () => calls.push("reload"),
      beginBusy: () => calls.push("busy"),
      teardown: () => calls.push("teardown"),
      onProgressTick: (progress, node) => calls.push(`tick:${progress}:${node ?? ""}`),
      onNodeTick: (node) => calls.push(`nodetick:${node}`),
    },
  };
}

interface Dispatched { type: string; detail: Record<string, unknown> }
const dispatched = (): Dispatched[] => mocks.dispatchEvent.mock.calls
  .map(([e]) => e as CustomEvent)
  .map((e) => ({ type: e.type, detail: e.detail as Record<string, unknown> }));
const dispatchedTypes = (): string[] => dispatched().map((e) => e.type);
const only = (type: string): Dispatched[] => dispatched().filter((e) => e.type === type);
/** 初始「进度未知」事件（弹窗专有）之外的**真实进度**事件。 */
const realProgress = (): Dispatched[] => only("laf-canvas-wf-progress").filter((e) => e.detail.progress !== null);

/** 订阅回调捕获（测试手动触发 onProgress / onNode）。 */
let progressCbs: { onProgress?: (pct: number, p: { node?: string }) => void; onNode?: (id: string) => void } = {};

const flush = async (): Promise<void> => { await new Promise((r) => setTimeout(r, 0)); };

/** 回填「一次成功提交」所需的 mock 实现（`clearAllMocks` 之后复用）。 */
function armHappyPath(): void {
  mocks.submitGraph.mockResolvedValue({ ok: true, prompt_id: "p-1", node_count: 7 });
  mocks.comfyStatus.mockResolvedValue({ running: true, managed: true });
  mocks.interruptComfy.mockResolvedValue({ ok: true });
  mocks.subscribeProgress.mockImplementation((_url: string, _pid: string, cbs: typeof progressCbs) => {
    progressCbs = cbs;
    return vi.fn();
  });
  mocks.getRuntime.mockReturnValue({ start: mocks.runtimeStart });
  mocks.workflowGenMetadata.mockReturnValue({
    prompt: "真实提示词", templateName: "T", modelName: "M", loraNames: ["L"],
  });
  mocks.workflowRegenerationSnapshot.mockReturnValue(REGEN);
}

beforeEach(() => {
  vi.clearAllMocks();
  progressCbs = {};
  vi.stubGlobal("window", { dispatchEvent: mocks.dispatchEvent });
  armHappyPath();
});

afterEach(() => { vi.unstubAllGlobals(); });

// ── 1. 共有链：两条路径逐条同形 ───────────────────────────────────────────────
describe("共有链 · 提交 → 订阅 → 交给 runtime", () => {
  it("画布路径：submitGraph → 提交提示 → run-graph → subscribeProgress → start", async () => {
    const { calls, sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();

    expect(mocks.submitGraph).toHaveBeenCalledWith(GRAPH, COMFY);
    expect(calls).toEqual([SUBMIT_NOTIFY]);
    expect(dispatchedTypes()).toEqual(["laf-canvas-wf-run-graph"]);
    expect(only("laf-canvas-wf-run-graph")[0].detail).toEqual({ runId: "wfrun-abc", graph: GRAPH });
    expect(mocks.subscribeProgress).toHaveBeenCalledWith(COMFY, "p-1", expect.anything());
    expect(mocks.getRuntime).toHaveBeenCalledWith("repo-1");
    expect(mocks.runtimeStart).toHaveBeenCalledTimes(1);
  });

  it("弹窗路径：同链 + 预检在提交前（提交提示与 run-graph payload 与画布逐字相同）", async () => {
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput(), sink);
    await flush();

    expect(mocks.submitGraph).toHaveBeenCalledWith(GRAPH, COMFY);
    // busy 先置（`wfRunningRef` 同步置忙，同一 tick 内第二次点击会被守卫挡下）
    expect(calls).toEqual(["busy", SUBMIT_NOTIFY]);
    expect(mocks.comfyStatus.mock.invocationCallOrder[0])
      .toBeLessThan(mocks.submitGraph.mock.invocationCallOrder[0]);
    expect(only("laf-canvas-wf-run-graph")[0].detail).toEqual({ runId: "wfrun-abc", graph: GRAPH });
  });

  it("start 入参：线程键/owner 归一到 threadId、mediaType 由抓取图推导", async () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();

    expect(mocks.workflowGenMetadata).toHaveBeenCalledWith("T", GRAPH, DRAFT);
    expect(mocks.workflowRegenerationSnapshot).toHaveBeenCalledWith(
      GRAPH, COMFY, [], "真实提示词", "T", "M", ["L"],
    );
    expect(mocks.runtimeStart).toHaveBeenCalledWith({
      promptId: "p-1",
      comfyuiUrl: COMFY,
      regeneration: REGEN,
      prompt: "真实提示词",
      owner: { threadId: "repo-1", repoId: "repo-1", outputDir: "/out" },
      mediaType: "image",
      runId: "wfrun-abc",
    }, expect.anything());
  });

  it("视频模板不被当成图片（否则会吃图片 20 分钟硬上限）", async () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput({}, VIDEO_GRAPH), sink);
    await flush();
    expect(mocks.runtimeStart.mock.calls[0][0].mediaType).toBe("video");
  });

  it("owner.outputDir 缺省时空串（不写 undefined 进 pending）", async () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput({ outputDir: undefined }), sink);
    await flush();
    expect(mocks.runtimeStart.mock.calls[0][0].owner.outputDir).toBe("");
  });

  it("promptText 回退链：meta.prompt → templateName → 「工作流生成」", async () => {
    for (const [metaPrompt, templateName, expected] of [
      ["P", "T", "P"], ["", "T", "T"], ["", "", "工作流生成"],
    ] as const) {
      vi.clearAllMocks();
      armHappyPath();
      mocks.workflowGenMetadata.mockReturnValue({
        prompt: metaPrompt, templateName: "X", modelName: "", loraNames: [],
      });
      const { sink } = canvasHarness();
      runCanvasWorkflow(canvasInput({ templateName }), sink);
      await flush();
      expect(mocks.runtimeStart.mock.calls[0][0].prompt).toBe(expected);
    }
  });

  it("进度与节点事件：payload 逐字（taskId / promptId / progress / node / nodeLabel / templateName）", async () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();

    progressCbs.onProgress?.(42, { node: "3" });
    progressCbs.onNode?.("9");
    expect(realProgress().map((e) => e.detail)).toEqual([
      { taskId: "wfrun-abc", promptId: "p-1", progress: 42, node: "3", nodeLabel: "KSampler (#3)", templateName: "T" },
      { taskId: "wfrun-abc", promptId: "p-1", node: "9", nodeLabel: "SaveImage (#9)", templateName: "T" },
    ]);
  });

  it("onProgress 无节点时 node 为空且**不查标签**", async () => {
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput(), sink);
    await flush();
    progressCbs.onProgress?.(5, {});
    expect(calls).toContain("tick:5:");
    expect(realProgress()[0].detail.nodeLabel).toBe("");
  });

  it("节点名对齐对话模式：`class_type (#id)`；未知 id 退化 `节点 #<id>`", async () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();
    progressCbs.onNode?.("3");
    progressCbs.onNode?.("77");
    expect(realProgress().map((e) => e.detail.nodeLabel)).toEqual(["KSampler (#3)", "节点 #77"]);
  });

  it("弹窗传入自己的 nodeLabel（额外回退 wfDraft）", async () => {
    const { sink } = modalHarness();
    runCanvasWorkflow(modalInput({ nodeLabel: (id) => `draft:${id}` }), sink);
    await flush();
    progressCbs.onNode?.("3");
    expect(realProgress()[0].detail.nodeLabel).toBe("draft:3");
  });

  it("runId 缺省（弹窗某路径）：派发零个进度事件但本地 tick 照常", async () => {
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput({ runId: undefined }), sink);
    await flush();
    expect(only("laf-canvas-wf-run-graph")).toHaveLength(0);
    expect(only("laf-canvas-wf-progress")).toHaveLength(0);
    progressCbs.onProgress?.(9, { node: "3" });
    expect(calls).toContain("tick:9:3");
    expect(only("laf-canvas-wf-progress")).toHaveLength(0);
    expect(mocks.runtimeStart.mock.calls[0][0].runId).toBeUndefined();
  });

  it("归档回调转发：observer.finalize 落到调用方给的链", async () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();
    const observer = mocks.runtimeStart.mock.calls[0][1] as { finalize: (r: unknown, p: unknown) => Promise<boolean> };
    await observer.finalize({ status: "completed" }, { prompt_id: "p-1" });
    expect(finalizeWorkflow).toHaveBeenCalledWith({ status: "completed" }, { prompt_id: "p-1" });
  });
});

// ── 2. 刻意差异：逐格锁死（「对齐」任一格都是行为回归）──────────────────────────
describe("刻意差异 · 不得统一成其中一侧的行为", () => {
  it("画布**不**预检 ComfyUI（让 submitGraph 自己抛错）", async () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();
    expect(mocks.comfyStatus).not.toHaveBeenCalled();
  });

  it("弹窗预检失败：专有文案 + 清本地态 + 收工，且**不提交**", async () => {
    mocks.comfyStatus.mockResolvedValue({ running: false, managed: false });
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput(), sink);
    await flush();
    expect(calls).toEqual([
      "busy",
      "notify[error]:ComfyUI 未启动。请先启动 ComfyUI，或在「设置」填写路径由工具自动拉起。",
      "teardown",
      "done",
    ]);
    expect(mocks.submitGraph).not.toHaveBeenCalled();
  });

  it("画布：未抓取与「未配置 ComfyUI 地址」合成同一早退（requireComfyUrl=true）", async () => {
    // 三种触发条件（无抓取图 / 地址为空 / 两者皆空）都是同一句画布文案
    const cases: Array<[string, Partial<CanvasWorkflowInput>, unknown]> = [
      ["无抓取图", {}, null],
      ["地址为空", { comfyuiUrl: "" }, GRAPH],
      ["两者皆空", { comfyuiUrl: "" }, undefined],
    ];
    for (const [name, patch, captured] of cases) {
      const { calls, sink } = canvasHarness();
      runCanvasWorkflow(canvasInput(patch, captured), sink);
      expect(calls, name).toEqual([`notify[error]:${CANVAS_MISSING_TEXT}`, "done"]);
    }
    expect(mocks.submitGraph).not.toHaveBeenCalled();
    expect(mocks.subscribeProgress).not.toHaveBeenCalled();
  });

  it("弹窗：地址为空**不**走「未抓取」早退（requireComfyUrl=false，由 comfyStatus 失败来报）", async () => {
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput({ comfyuiUrl: "" }), sink);
    await flush();
    expect(calls.some((c) => c.includes(MODAL_MISSING_TEXT))).toBe(false);
    // 空地址下仍走到 comfyStatus 预检（只是它会失败）
    expect(mocks.comfyStatus).toHaveBeenCalledWith("");
  });

  it("弹窗：captured 缺失时用**弹窗专有**文案（与画布那句不同）", async () => {
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput({}, null), sink);
    expect(calls).toEqual([`notify[error]:${MODAL_MISSING_TEXT}`, "teardown", "done"]);
  });

  it("弹窗补发一次「进度未知」（在 run-graph 之后），画布没有", async () => {
    {
      const { sink } = modalHarness();
      runCanvasWorkflow(modalInput(), sink);
      await flush();
      const initial = only("laf-canvas-wf-progress").filter((e) => e.detail.progress === null);
      expect(initial).toHaveLength(1);
      expect(initial[0].detail).toEqual({
        taskId: "wfrun-abc", promptId: "p-1", progress: null, node: "", templateName: "T",
      });
      expect(dispatchedTypes().indexOf("laf-canvas-wf-progress"))
        .toBeGreaterThan(dispatchedTypes().indexOf("laf-canvas-wf-run-graph"));
    }
    vi.clearAllMocks();
    armHappyPath();
    {
      const { sink } = canvasHarness();
      runCanvasWorkflow(canvasInput(), sink);
      await flush();
      expect(only("laf-canvas-wf-progress")).toHaveLength(0);
    }
  });
});

// ── 3. 终态五格 + 幂等 ──────────────────────────────────────────────────────
describe("终态 · 经终态表的执行口翻成事件", () => {
  /** 跑到「已交付 runtime」并取出 observer。 */
  async function armed(input: CanvasWorkflowInput, kind: "canvas" | "modal") {
    const harness = kind === "canvas" ? canvasHarness() : modalHarness();
    runCanvasWorkflow(input, harness.sink);
    await flush();
    const observer = mocks.runtimeStart.mock.calls[0][1] as {
      completed: (r: unknown, p: unknown, produced: boolean) => void;
      failed: (p: unknown, stage: "execution" | "task_not_found", error: string) => void;
      stalled?: (p: { prompt_id: string }, error: string) => void;
      timedOut: (p: unknown) => void;
      released: (p: unknown) => void;
    };
    return { ...harness, observer };
  }

  it("completed：提示产出数 + 回读快照 + 派发 done（画布路径）", async () => {
    const { calls, observer } = await armed(canvasInput(), "canvas");
    calls.length = 0;
    observer.completed({ images: [1, 2, 3], videos: [1], audios: [1, 2] }, {}, true);
    expect(calls).toEqual([
      "notify[info]:运转完成，3 张图、1 个视频、2 个音频已入库。",
      "reload",
      "done",
    ]);
  });

  it("completed：弹窗路径先清本地态再走终态表（teardown → notify → reload → done）", async () => {
    const { calls, observer } = await armed(modalInput(), "modal");
    expect(calls).toEqual(["busy", SUBMIT_NOTIFY]);
    calls.length = 0;
    observer.completed({ images: [1], videos: [], audios: [] }, {}, true);
    // 顺序对齐改前 `settle`：teardown() 在 runCanvasEffects() 之前
    expect(calls).toEqual([
      "teardown",
      "notify[info]:运转完成，1 张图已入库。",
      "reload",
      "done",
    ]);
  });

  it("empty：三路媒体皆空 → 报错文案 + 派发 done，但**不回读快照**", async () => {
    const { calls, observer } = await armed(canvasInput(), "canvas");
    calls.length = 0;
    observer.completed({ images: [], videos: [], audios: [] }, {}, false);
    expect(calls).toEqual([
      "notify[error]:运转完成但未拿到图片/视频/音频（可能输出节点未正确配置），请到 ComfyUI 控制台查看。",
      "done",
    ]);
  });

  it("failed：task_not_found 用「任务已丢失」文案，execution 透传错误", async () => {
    const a = await armed(canvasInput(), "canvas");
    a.calls.length = 0;
    a.observer.failed({}, "task_not_found", "ignored");
    expect(a.calls).toEqual(["notify[error]:运转失败：出图任务已丢失（ComfyUI 可能已重启）", "done"]);

    vi.clearAllMocks();
    armHappyPath();
    const b = await armed(canvasInput(), "canvas");
    b.calls.length = 0;
    b.observer.failed({}, "execution", "采样失败");
    expect(b.calls).toEqual(["notify[error]:运转失败：采样失败", "done"]);
  });

  it("stalled：清队列并**中断**该 prompt（唯一会打断 ComfyUI 的终态）", async () => {
    const { calls, observer } = await armed(canvasInput(), "canvas");
    calls.length = 0;
    observer.stalled?.({ prompt_id: "p-1" }, "长时间未开始加载节点（ComfyUI 队列未运转），已停止");
    expect(calls).toEqual([
      "notify[error]:运转失败：长时间未开始加载节点（ComfyUI 队列未运转），已停止",
      "done",
    ]);
    expect(mocks.interruptComfy).toHaveBeenCalledWith(COMFY, "p-1");
  });

  it("timed_out：保留占位（**不派发 done**）并告知后台仍在运转", async () => {
    const { calls, observer } = await armed(canvasInput(), "canvas");
    calls.length = 0;
    observer.timedOut({});
    expect(calls).toEqual([
      "notify[info]:运转仍在进行中（已超过预期时长）。画布会保留「生成中」节点，请稍后在 ComfyUI 控制台查看或重新运转。",
    ]);
    expect(mocks.interruptComfy).not.toHaveBeenCalled();
  });

  it("released（5 分钟释放忙碌）：零副作用", async () => {
    const { calls, observer } = await armed(canvasInput(), "canvas");
    calls.length = 0;
    observer.released({});
    expect(calls).toEqual([]);
  });

  it("终态幂等：第二个终态一律被丢弃（不会二次派发 done）", async () => {
    const { calls, observer } = await armed(canvasInput(), "canvas");
    calls.length = 0;
    observer.completed({ images: [1], videos: [], audios: [] }, {}, true);
    observer.failed({}, "execution", "late");
    expect(calls.filter((c) => c === "done")).toHaveLength(1);
    expect(calls.some((c) => c.includes("late"))).toBe(false);
  });
});

// ── 3b. 空判定（P11）：判据是权威值 `produced`，不是 counts 重算 ─────────────────
/**
 * `WorkflowWatchObserver.completed(result, pending, produced)` 的**第三参是权威值**，
 * 由 `workflowGenerationRuntime.finalize()` 得自归档链 `useChatSession.finalizeGeneration`
 * 的返回值（对话侧 `useChatSession.ts:910` 就在消费它）。
 *
 * 改前画布/弹窗**忽略**它、各自按 `counts.images + counts.videos + counts.audios > 0` 重算
 * ⇒ 两套空判定。四格矩阵里有两格分叉，本块把它们逐格钉住。
 *
 * ⚠ 权威值语义（实测，别按直觉理解）：`produced=false` **不是**「入库失败」——
 * 归档失败（`durableFinalizeSucceeded` 为假）是**抛错**，被 runtime 吞掉后**保留 pending 重试**，
 * 根本走不到 `completed`。`false` 的真实来源只有两个：
 *   ① 结果三路媒体全空**且**无文本（`finalizeGeneration` 首行早退）；
 *   ② 回灌消息为空（`workflowMessages(result.messages).length === 0`）。
 */
describe("空判定 · 权威 `produced`（P11）", () => {
  const DONE_1_IMAGE = "notify[info]:运转完成，1 张图已入库。";
  const DONE_0_IMAGE = "notify[info]:运转完成，0 张图已入库。";
  const EMPTY_TEXT =
    "notify[error]:运转完成但未拿到图片/视频/音频（可能输出节点未正确配置），请到 ComfyUI 控制台查看。";

  /** 跑到「已交付 runtime」后，用给定 result / produced 触发一次 completed，返回 sink 调用序列。 */
  async function completedCalls(
    result: Record<string, unknown>, produced: boolean,
  ): Promise<string[]> {
    const { calls, sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();
    const observer = mocks.runtimeStart.mock.calls[0][1] as {
      completed: (r: unknown, p: unknown, produced: boolean) => void;
    };
    calls.length = 0;
    observer.completed(result, {}, produced);
    return calls;
  }

  const WITH_IMAGE = { images: [1], videos: [], audios: [] };
  const NO_MEDIA = { images: [], videos: [], audios: [] };

  it("有媒体 + produced=true ⇒ completed（两口径一致）", async () => {
    expect(await completedCalls(WITH_IMAGE, true)).toEqual([DONE_1_IMAGE, "reload", "done"]);
  });

  it("无媒体 + produced=false ⇒ empty（两口径一致）", async () => {
    expect(await completedCalls(NO_MEDIA, false)).toEqual([EMPTY_TEXT, "done"]);
  });

  it("★ 分叉格 A：有媒体但 produced=false ⇒ 必须听权威值（empty）", async () => {
    // 现状（counts 重算）= completed ⇒ **误报**「1 张图已入库」，
    // 而归档链其实没产出可见内容（`workflowMessages` 为空）。
    expect(await completedCalls(WITH_IMAGE, false)).toEqual([EMPTY_TEXT, "done"]);
  });

  it("★ 分叉格 B：无媒体但 produced=true（纯文本产出）⇒ 必须听权威值（completed）", async () => {
    // 现状（counts 重算）= empty ⇒ 把**已入库的文本产出**误报成「未拿到图片/视频/音频」。
    expect(await completedCalls(NO_MEDIA, true)).toEqual([DONE_0_IMAGE, "reload", "done"]);
  });
});

// ── 4. 早退与异常 ──────────────────────────────────────────────────────────
describe("早退与异常 · 不套终态表但仍须收工", () => {
  it("归档链未接线：明说 + 收工，绝不静默产出一张不落库的图", () => {
    const { calls, sink } = canvasHarness();
    runCanvasWorkflow(canvasInput({ finalizeWorkflow: null }), sink);
    expect(calls).toEqual([
      "notify[error]:画布归档链路未接线，无法运转工作流（请刷新页面重试）",
      "done",
    ]);
    expect(mocks.submitGraph).not.toHaveBeenCalled();
  });

  it("提交阶段抛错：`运转失败：<msg>` + 收工（画布）", async () => {
    mocks.submitGraph.mockRejectedValue(new Error("boom"));
    const { calls, sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();
    expect(calls).toEqual(["notify[error]:运转失败：boom", "done"]);
  });

  it("提交阶段抛错：弹窗路径额外清本地忙碌态", async () => {
    mocks.submitGraph.mockRejectedValue(new Error("boom"));
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput(), sink);
    await flush();
    expect(calls).toEqual([
      "busy", "notify[error]:运转失败：boom", "teardown", "done",
    ]);
  });

  it("预检抛错（ComfyUI 探测本身失败）：与提交抛错同一收工路径", async () => {
    mocks.comfyStatus.mockRejectedValue(new Error("ECONNREFUSED"));
    const { calls, sink } = modalHarness();
    runCanvasWorkflow(modalInput(), sink);
    await flush();
    expect(calls).toEqual([
      "busy", "notify[error]:运转失败：ECONNREFUSED", "teardown", "done",
    ]);
  });

  it("早退后既不定阅也不登记 pending", () => {
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput({}, null), sink);
    expect(mocks.subscribeProgress).not.toHaveBeenCalled();
    expect(mocks.runtimeStart).not.toHaveBeenCalled();
  });

  it("终态收工后订阅被退订（不泄漏 /ws 连接）", async () => {
    const stop = vi.fn();
    mocks.subscribeProgress.mockImplementation(() => stop);
    const { sink } = canvasHarness();
    runCanvasWorkflow(canvasInput(), sink);
    await flush();
    const observer = mocks.runtimeStart.mock.calls[0][1] as { timedOut: (p: unknown) => void };
    observer.timedOut({});
    expect(stop).toHaveBeenCalledTimes(1);
  });
});
