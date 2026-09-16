// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * 画布/弹窗「运转工作流」编排的**源码形态契约锁**（2026-09-15 架构审计 P10 · F1）。
 *
 * 背景：`views/CanvasStageFlow.tsx` 的 `runWorkflowTask` 与 `components/WorkflowToolModal.tsx`
 * 的 `handleRun` 原先**各自持有一条逐句重复的编排链**（submitGraph → 提示 → run-graph →
 * subscribeProgress → workflowGenMetadata → workflowRegenerationSnapshot → observer → start），
 * 是「两个属主」——一处改了另一处不跟（P19 之前 `interruptQueue` 就只在弹窗侧补过）。
 *
 * 两组件**零渲染级测试**（`WorkflowCard.test.tsx` 之外没人为它们挂 jsdom 根），
 * 而这条链又恰好全是不进渲染面的跨组件契约（审计 R5）⇒ 只能用**源码形态锁**钉住分层与刻意差异：
 *   · 编排链只允许在 `lib/runCanvasWorkflow.ts` 出现（组件只能是「给 input + 提供 sink」）；
 *   · 两条路径的四处**刻意差异**必须逐字保留在各自调用点，**不得统一成其中一侧的行为**。
 *
 * 行为面（调用顺序 / 事件 payload / 文案）由 `runCanvasWorkflow.test.ts` 的 mock 行为网覆盖；
 * 真机跨组件验收归 P20 的画布 e2e（本清单 P20 明确把它作为 P10/P12 的验收网）。
 */
const CANVAS_SRC = readFileSync(new URL("../views/CanvasStageFlow.tsx", import.meta.url), "utf-8");
const MODAL_SRC = readFileSync(new URL("../components/WorkflowToolModal.tsx", import.meta.url), "utf-8");
const PRIMITIVE_SRC = readFileSync(new URL("./runCanvasWorkflow.ts", import.meta.url), "utf-8");

/** 注释里会引用旧写法（如 `submitGraph`、`interruptComfy`）作背景，判定只看代码。 */
const strip = (src: string): string =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");

const canvasCode = strip(CANVAS_SRC);
const modalCode = strip(MODAL_SRC);

/** 链上只有原语能有的调用：组件里再出现 = 分层回退。 */
const PRIMITIVE_ONLY_CALLS = [
  "submitGraph(",
  "comfyStatus(",
  "subscribeProgress(",
  "canvasRunEffects(",
  "runCanvasEffects(",
  "getRuntime(",
  "interruptComfy(",
] as const;

/** 这两个**画布还有另一处用途**（占位节点显示名 `CanvasStageFlow.tsx:1450`），故只对弹窗要求不得再出现。 */
const MODAL_ABSENT_CALLS = [
  "workflowGenMetadata(",
  "workflowRegenerationSnapshot(",
] as const;

/** 「已提交到 ComfyUI（…），正在运转工作流…」——两处本来逐字相同，收口后只允许有一份。 */
const SUBMIT_TEXT = "已提交到 ComfyUI（prompt_id: ${promptId}，${r.node_count} 个节点），正在运转工作流…";

/**
 * **派发**权归属（不是「事件名」归属）：`run-graph` / `progress` 由原语派发；
 * `done` 由调用方的 sink 派发（P19 的执行口 `dispatchDone`）。
 * 注意不能用裸事件名判定 —— 画布还**监听**这三个事件（`addEventListener`），名字必然出现。
 */
const PRIMITIVE_DISPATCHES = [
  'new CustomEvent("laf-canvas-wf-run-graph"',
  'new CustomEvent("laf-canvas-wf-progress"',
] as const;
const SINK_DISPATCH = 'new CustomEvent("laf-canvas-wf-done"';

describe("P10 · 编排链的单一属主", () => {
  it("链的每一步都只在原语里出现", () => {
    for (const call of PRIMITIVE_ONLY_CALLS) {
      expect(PRIMITIVE_SRC, `原语缺少 ${call}`).toContain(call);
      expect(canvasCode, `画布仍在直接调用 ${call}（分层回退）`).not.toContain(call);
      expect(modalCode, `弹窗仍在直接调用 ${call}（分层回退）`).not.toContain(call);
    }
  });

  it("元数据/快照装配不再由弹窗持有（画布那处是**另一用途**，不在此列）", () => {
    for (const call of MODAL_ABSENT_CALLS) {
      expect(modalCode, `弹窗仍在直接调用 ${call}`).not.toContain(call);
    }
    expect(PRIMITIVE_SRC).toContain("workflowGenMetadata(");
    expect(PRIMITIVE_SRC).toContain("workflowRegenerationSnapshot(");
  });

  it("两组件都改为调用 runCanvasWorkflow（各恰好一次）", () => {
    for (const [name, code] of [["画布", canvasCode], ["弹窗", modalCode]] as const) {
      const hits = code.match(/runCanvasWorkflow\(/g) || [];
      expect(hits.length, `${name} 调用次数`).toBe(1);
    }
  });

  it("提交提示只在原语里有一份", () => {
    expect(PRIMITIVE_SRC).toContain(SUBMIT_TEXT);
    expect(canvasCode).not.toContain("已提交到 ComfyUI（");
    expect(modalCode).not.toContain("已提交到 ComfyUI（");
  });

  it("派发权归属：run-graph / progress 归原语，done 归调用方的 sink", () => {
    for (const dispatch of PRIMITIVE_DISPATCHES) {
      expect(PRIMITIVE_SRC, `原语缺少 ${dispatch}`).toContain(dispatch);
      expect(canvasCode, `画布仍在派发 ${dispatch}`).not.toContain(dispatch);
      expect(modalCode, `弹窗仍在派发 ${dispatch}`).not.toContain(dispatch);
    }
    // done 走 sink.dispatchDone（P19 把「该不该派发」交给终态表，原语自己不决定）
    expect(canvasCode).toContain(SINK_DISPATCH);
    expect(modalCode).toContain(SINK_DISPATCH);
    expect(PRIMITIVE_SRC).not.toContain(SINK_DISPATCH);
    expect(PRIMITIVE_SRC).toContain("sink.dispatchDone()");
  });
});

// ── 刻意差异：这四条一旦被「对齐」，某一侧行为就会回归 ────────────────────────────
//
// 审计原文：「把『是否预检』显式做成入参开关，**不要**统一成其中一侧的行为」。
// 收口后差异从「两份代码各自不同」变成「调用点两个字面值不同」，锁的形态随之改变。
describe("P10 · 两条路径的刻意差异（不得统一）", () => {
  it("comfyStatus 预检：弹窗开、画布关", () => {
    expect(modalCode).toMatch(/precheckComfyStatus:\s*true/);
    expect(canvasCode).toMatch(/precheckComfyStatus:\s*false/);
  });

  it("「未抓到画布内容」是否也覆盖「未配置 ComfyUI 地址」：画布开、弹窗关", () => {
    expect(canvasCode).toMatch(/requireComfyUrl:\s*true/);
    expect(modalCode).toMatch(/requireComfyUrl:\s*false/);
  });

  it("订阅后是否补发一次「进度未知」：弹窗开、画布关", () => {
    expect(modalCode).toMatch(/emitInitialProgress:\s*true/);
    expect(canvasCode).toMatch(/emitInitialProgress:\s*false/);
  });

  it("「未抓到画布内容」文案两处**本来就不同**，逐字保留在各自调用点", () => {
    expect(canvasCode).toContain("还没抓到画布内容，请先双击打开编辑器点「选择完毕」");
    expect(modalCode).toContain("还没抓到画布内容，请先在左栏点「选择完毕」。");
  });

  it("弹窗的 re-entrancy 守卫留在组件（画布没有这一步，属既有差异）", () => {
    expect(modalCode).toMatch(/if \(wfRunningRef\.current\)/);
    expect(canvasCode).not.toContain("wfRunningRef");
  });

  it("预检失败文案（弹窗专有）逐字保留", () => {
    expect(PRIMITIVE_SRC).toContain(
      "ComfyUI 未启动。请先启动 ComfyUI，或在「设置」填写路径由工具自动拉起。",
    );
  });

  it("归档链未接线文案（画布专有）逐字保留", () => {
    expect(PRIMITIVE_SRC).toContain("画布归档链路未接线，无法运转工作流（请刷新页面重试）");
  });
});

// ── P11：空判定的判据必须是 observer 的**第三参**，不是 counts 重算 ───────────────
//
// 为什么必须上源码锁：权威值 `produced` 与 `counts > 0` 只在两种输入下分叉
// （有媒体但归档链没产出可见内容 / 无媒体但有文本产出），而这两格**行为网能测**——
// 但「谁来决定空」这件事一旦被改回 counts 重算，两格会静默变回旧行为；
// 真正防的是**第三种写法**：既接住 `produced` 又顺手 `|| counts > 0`（裁判分裂，行为网测不出）。
describe("P11 · 空判定用权威 `produced`（不得回退到 counts 重算）", () => {
  const primitiveCode = strip(PRIMITIVE_SRC);

  it("completed 回调接住第三参，并以它作为 completed/empty 判据", () => {
    expect(primitiveCode).toMatch(/completed:\s*\([^)]*,\s*produced\s*\)/);
    expect(primitiveCode).toContain('canvasRunEffects(produced ? "completed" : "empty"');
  });

  it("原语里不得自行推导 produced（它只能来自 observer 形参）", () => {
    expect(primitiveCode).not.toMatch(/const\s+produced\s*=/);
  });

  it("不得再用 counts 相加重算空判定（counts 只供文案数字）", () => {
    expect(primitiveCode).not.toMatch(/counts\.images\s*\+\s*counts\.videos/);
    expect(primitiveCode).not.toMatch(/counts\.videos\s*\+\s*counts\.audios/);
  });
});

// ── P13：`stalled` 必须转发已产出的 error（文案属主不得被消费方搬回去）────────────
//
// 改造前：`stalled?: (pending) => void` **不转发** `decision.error` ⇒ 三处消费方各自去取文案：
// 画布/弹窗侧 `import STALLED_POLL_MESSAGE` 再塞回（把同一个常量搬了一次），对话侧**硬编码同字面值**。
// ⇒「文案归 runtime」名存实亡：改一句话要动 3 个文件，漏一处不报错（字面值还长得一样）。
//
// 行为面（文案渲染）由 `runCanvasWorkflow.test.ts` + `workflowGenerationRuntime.test.ts` 覆盖；
// 本组锁**分层**：常量只允许出现在 runtime，消费方只能吃第二参。
// （`runCanvasWorkflow.test.ts` 里那行 mock 常量也一并删掉了 —— 它是「消费方还在自己取文案」的假线索。）
describe("P13 · stalled 转发 error：文案常量不得被消费方搬回", () => {
  const runtimeCode = strip(readFileSync(new URL("./workflowGenerationRuntime.ts", import.meta.url), "utf-8"));
  const canvasPrimitive = strip(PRIMITIVE_SRC);

  it("runtime 的 stalled 签名为 `(pending, error)`，且在派发点转发常量", () => {
    expect(runtimeCode).toMatch(/stalled\?:\s*\(pending: PendingGeneration, error: string\) => void;/);
    expect(runtimeCode).toMatch(
      /if \(observer\.stalled\) observer\.stalled\(pending, STALLED_POLL_MESSAGE\);\s*else observer\.timedOut\(pending\);/,
    );
  });

  it("画布消费方直接用第二参（不得再塞常量）", () => {
    expect(canvasPrimitive).toMatch(
      /stalled: \(pending, error\) => settle\(canvasRunEffects\("stalled", \{ error \}\), pending\.prompt_id\)/,
    );
    expect(canvasPrimitive).not.toMatch(/STALLED_POLL_MESSAGE/);
  });

  it("对话消费方用第二参（原为同值硬编码）", () => {
    const hook = strip(readFileSync(new URL("./useChatSession.ts", import.meta.url), "utf-8"));
    expect(hook).toMatch(/stalled: \(pending, error\) => \{/);
    expect(hook).toMatch(/pending\.target\.slotId, "stalled",\s*error, pending\.prompt_id,/);
    expect(hook).not.toMatch(/STALLED_POLL_MESSAGE/);
  });

  it("全仓生产文件除 runtime 外零处引用 STALLED_POLL_MESSAGE", () => {
    for (const rel of [
      "../views/CanvasStageFlow.tsx",
      "../components/WorkflowToolModal.tsx",
      "./canvasWorkflowRun.ts",
      "./runCanvasWorkflow.ts",
      "./useChatSession.ts",
    ]) {
      expect(strip(readFileSync(new URL(rel, import.meta.url), "utf-8")), rel)
        .not.toMatch(/STALLED_POLL_MESSAGE/);
    }
  });
});
