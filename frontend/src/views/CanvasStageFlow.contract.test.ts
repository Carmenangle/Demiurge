// CanvasStageFlow 视图接线形态锁（2026-10-24 行为网首刀）。
//
// 现状背景：视图本体 2119 行、零组件测试；其行为面已大范围外移到 lib/ 接缝
// （T2.1 的投影/事件桥/WF 事件 + 后续 canvasRuntime/canvasLayout/canvasConversation/
// canvasKeyboard/runCanvasWorkflow），各接缝自带行为网——**视图侧缺的是「接线不被
// 破坏」的锁**。本文件锁当前真实接线（读源断言，不渲染：ReactFlow/DnD 在 jsdom 下
// 只会产出抖动的假网；真实渲染由 e2e-manual 的 wallpaper/canvas 系列承担）。
//
// 红线出处：ARCHITECTURE 条目 28（画布/弹窗不得再出现 pollWorkflowResult；帧路径
// 不得改 start）与 T2.1 红线（hook 恰一次、effect 依赖数组=行为面）。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./CanvasStageFlow.tsx", import.meta.url), "utf8");
const noComments = src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .split(/\r?\n/).map((l: string) => l.replace(/(?<!:)\/\/.*$/, "")).join("\n");
function count(hay: string, needle: string): number {
  let n = 0, i = 0;
  for (;;) {
    const at = hay.indexOf(needle, i);
    if (at === -1) return n;
    n += 1; i = at + needle.length;
  }
}

describe("外移接缝的 import 面（缺一即接线断裂）", () => {
  const SEAM_IMPORTS = [
    '"../lib/canvasPlaceholderNodes"', '"../lib/canvasProjection"', '"../lib/useCanvasWfEvents"',
    '"../lib/useCanvasEventBridge"', '"../lib/canvasConversation"', '"../lib/runCanvasWorkflow"',
    '"../lib/canvasKeyboard"', '"../lib/canvasRuntime"', '"../lib/canvasLayout"',
  ];
  it.each(SEAM_IMPORTS)("视图必须从 %s 导入", (mod) => {
    expect(src).toContain(mod);
  });
});

describe("六个行为 hook 恰调用一次（调用序=effect 执行序，T2.1 红线）", () => {
  const HOOKS = [
    "useDetailToggleTracking({", "useCanvasGenerationSaved({", "useCanvasWorkflowToolCreate({",
    "useCanvasWfEvents({", "useCtxMenuOutsideDismiss({", "useCanvasInspirationDrop({",
  ];
  it.each(HOOKS)("%s 恰一次", (h) => {
    expect(count(noComments, h)).toBe(1);
  });
});

describe("已外移判定不得回涨为内联定义（分层回退＝红）", () => {
  it("投影/占位/WF 判定族无 function 定义（只允许 import 消费）", () => {
    for (const fn of [
      "function buildContentNodes", "function buildToolCards", "function mergeProjectedNodes",
      "function buildCanvasEdges", "function buildStoryNodes", "function buildInspirationNodes",
      "function buildStreamingPlaceholderNodes", "function buildDraftInputNodes", "function buildWfRunNodes",
      "function canvasRunEffects", "function advanceWorkflowTick",
    ]) {
      expect(count(noComments, fn), fn).toBe(0);
    }
    // 消费面仍在（经 import）：投影合并与占位构造至少各出现一次调用
    expect(count(noComments, "mergeProjectedNodes(")).toBeGreaterThanOrEqual(1);
    expect(noComments).toContain("buildStreamingPlaceholderNodes");
  });
});

describe("条目 28 红线：轮询归属", () => {
  it("视图不得出现 pollWorkflowResult（画布守望统一走 runtime.start）", () => {
    expect(count(noComments, "pollWorkflowResult")).toBe(0);
  });
  it("WorkflowToolModal（弹窗路径）同样不得自带轮询", () => {
    // 历史叙述注释不算（该文件 L120 自述旧实现曾自持轮询）
    const modalRaw = readFileSync(new URL("../components/WorkflowToolModal.tsx", import.meta.url), "utf8");
    const modal = modalRaw
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split(/\r?\n/).map((l: string) => l.replace(/(?<!:)\/\/.*$/, "")).join("\n");
    expect(count(modal, "pollWorkflowResult")).toBe(0);
  });
});

describe("体积护栏（拆分进度不回涨）", () => {
  it("视图行数 ≤ 2150（当前 2119；续刀只降不升）", () => {
    expect(src.split("\n").length).toBeLessThanOrEqual(2150);
  });
});
