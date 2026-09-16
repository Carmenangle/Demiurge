// @vitest-environment jsdom
//
// useCanvasWfEvents 的特征网（T2.1-B1 · 2026-09-16）
//
// 背景：`views/CanvasStageFlow.tsx` 里订阅 `laf-canvas-wf-run` / `-run-graph` / `-progress` /
// `-done` 的 useEffect 改造前是 **64 行内联体**，已**逐字节搬移**到 `lib/useCanvasWfEvents.ts`。
// 这个组件**没有组件测试**（审计 R5），事件处理就是「纯列表归约 + 两处列表外副作用」⇒
// 三道网分工如下：
//
//   ① 行为网（本文件 describe「真事件派发」）——jsdom + React 19 `act` 真挂载组件、真派发
//      `window` 的 CustomEvent，断言**经过 hook 之后**列表与锚点变成什么。这是唯一能同时覆盖
//      「订阅时机 / 字段映射 / 事务内落点 / 解绑」的一层。
//   ② 等价矩阵（describe「等价矩阵」）——把改造前的合并/新建写法**逐字抄**成 `legacy*` 参照，
//      在 72 组输入网格上断言 hook 实际产出 ≡ 参照产出 ⇒ 可证伪的「零行为变化」。
//   ③ 源码形态锁（describe「源码形态锁」）——行为网**抓不到分层回退**（hook 被删后内联回组件
//      照样可能全绿），故另钉：依赖数组行、订阅/解绑配对、画布侧「恰好调用一次」与文件行数上界。
//
// ⚠ 刻意不与 `wfRunList.contract.test.ts` 重叠（T1.2 教训：形态锁之间冗余 ⇒ 一变异多红、
//   丢掉「一变异一红」的诊断力）：那份合约锁管「调用点是否走单一属主 + 刻意差异没被统一」，
//   本文件管「订阅本身（时机/配对/是否还在组件里）」。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { act, type Dispatch, type RefObject, type SetStateAction } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import type { PlacedRect } from "./canvasRuntime";
import { CARD_W } from "../components/canvas/CanvasTypes";
import type { WfRun } from "./canvasPlaceholderNodes";
import { useCanvasWfEvents } from "./useCanvasWfEvents";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

// ─────────────────────────────────────────────────────────────────────────────
// 改造前写法的**逐字抄录**（唯一等价性参照，不得顺手优化）
// ─────────────────────────────────────────────────────────────────────────────
const WF_RUN = "laf-canvas-wf-run";
const WF_RUN_GRAPH = "laf-canvas-wf-run-graph";
const WF_PROGRESS = "laf-canvas-wf-progress";
const WF_DONE = "laf-canvas-wf-done";

/** 改造前 `onProg` 里「已存在 → 合并」的那段字段映射，逐字抄录。 */
const legacyProgMerge = (r: WfRun, d: ProgDetail): WfRun => ({
  ...r,
  promptId: d.promptId || r.promptId,
  progress: d.progress !== undefined ? d.progress : r.progress,
  node: d.node !== undefined ? d.node : r.node,
  templateName: d.templateName || r.templateName,
});

/** 改造前 `onProg` 里「不存在 → 新建」的那段，逐字抄录（pos 由调用方给定的落点代入）。 */
const legacyProgCreate = (
  d: ProgDetail, pos: { x: number; y: number },
): WfRun => ({
  id: d.taskId!, toolNodeId: "", templateName: d.templateName || "工作流",
  promptId: d.promptId || "", progress: d.progress !== undefined ? d.progress : null,
  node: d.node || undefined, x: pos.x, y: pos.y,
});

type ProgDetail = {
  taskId?: string; promptId?: string; progress?: number | null; node?: string; templateName?: string;
};

// ─────────────────────────────────────────────────────────────────────────────
// 挂载脚手架：真实组件 + 真实 window 事件，setState 用一个同步 reducer 顶替
// ─────────────────────────────────────────────────────────────────────────────
type PlaceCall = { w: number; h: number; extra?: PlacedRect[] };

function makeRig(initial: WfRun[] = [], refRuns?: WfRun[]) {
  const state = {
    list: initial.slice(),
    placeCalls: [] as PlaceCall[],
    placeSeq: 0,
  };
  // ⚠ ref 刻意**不随后续事件同步**：真实场景里同一拍内连续两次事件时 `wfRunsRef.current`
  //   仍停留在上一次渲染的值 —— 「progress 路径必须用 prev 而非 ref」正是靠这一点证伪。
  const wfRunsRef = { current: (refRuns ?? initial).slice() };
  const pendingAnchorRef: { current: PlacedRect | null } = { current: null };
  const placePlaceholder = (w: number, h: number, extra?: PlacedRect[]) => {
    state.placeCalls.push({ w, h, extra });
    state.placeSeq += 1;
    return { x: 100 * state.placeSeq, y: 200 * state.placeSeq };
  };
  const setWfRuns: Dispatch<SetStateAction<WfRun[]>> = (arg) => {
    state.list = typeof arg === "function" ? (arg as (p: WfRun[]) => WfRun[])(state.list) : arg;
  };
  return { state, wfRunsRef, pendingAnchorRef, placePlaceholder, setWfRuns };
}

type Rig = ReturnType<typeof makeRig>;

function Host({ rig }: { rig: Rig }) {
  useCanvasWfEvents({
    wfRunsRef: rig.wfRunsRef as RefObject<WfRun[]>,
    pendingAnchorRef: rig.pendingAnchorRef as RefObject<PlacedRect | null>,
    placePlaceholder: rig.placePlaceholder,
    setWfRuns: rig.setWfRuns,
  });
  return null;
}

async function mount(rig: Rig) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<Host rig={rig} />); });
  return {
    /** 真的往 `window` 派发一个 CustomEvent（走 addEventListener 那条路） */
    async fire(name: string, detail?: unknown) {
      await act(async () => {
        window.dispatchEvent(new CustomEvent(name, { detail }));
      });
    },
    async unmount() {
      await act(async () => { root.unmount(); });
      host.remove();
    },
  };
}

const baseRun = (over: Partial<WfRun> = {}): WfRun => ({
  id: "wf-1", toolNodeId: "n0", promptId: "p0", templateName: "T0",
  progress: 0.25, node: "9", x: 5, y: 6, ...over,
});

// ─────────────────────────────────────────────────────────────────────────────
describe("真事件派发（jsdom：挂载组件 + 派发 window 事件）", () => {
  it("run：新增项字段来自事件、落点取自 wfRunsRef 的矩形（既有差异）", async () => {
    const existing = baseRun({ id: "wf-old", x: 1, y: 2 });
    const rig = makeRig([existing]);
    const h = await mount(rig);
    await h.fire(WF_RUN, { runId: "wf-2", toolNodeId: "n1", templateName: "" });

    expect(rig.state.placeCalls).toEqual([
      { w: CARD_W, h: 427, extra: [{ x: 1, y: 2, w: CARD_W, h: 427 }] },
    ]);
    expect(rig.state.list).toHaveLength(2);
    expect(rig.state.list[1]).toEqual({
      id: "wf-2", toolNodeId: "n1", templateName: "工作流",
      promptId: "", progress: null, x: 100, y: 200,
    });
    await h.unmount();
  });

  it("run：同 id 重复派发不重复建项，但落点计算仍会执行（既有行为）", async () => {
    const rig = makeRig([baseRun()]);
    const h = await mount(rig);
    const before = rig.state.list;
    await h.fire(WF_RUN, { runId: "wf-1" });
    expect(rig.state.list).toBe(before);
    expect(rig.state.placeCalls).toHaveLength(1);
    await h.unmount();
  });

  it("progress：新占位落点在事务内取自 prev —— 同一拍两次事件各自避让到不同空位", async () => {
    const rig = makeRig([]);
    const h = await mount(rig);
    await h.fire(WF_PROGRESS, { taskId: "t1", progress: 0.1 });
    await h.fire(WF_PROGRESS, { taskId: "t2", progress: 0.2 });

    // 第一次 prev=[]；第二次 prev 已含 t1 ⇒ 第二个落点必须把 t1 的矩形算进避让范围。
    expect(rig.state.placeCalls[0].extra).toEqual([]);
    expect(rig.state.placeCalls[1].extra).toEqual([{ x: 100, y: 200, w: CARD_W, h: 427 }]);
    expect(rig.state.list.map((r) => [r.id, r.x, r.y]))
      .toEqual([["t1", 100, 200], ["t2", 200, 400]]);
    await h.unmount();
  });

  it("run-graph：按 id 合并 graph，命中不到 id 时列表内容不变", async () => {
    const rig = makeRig([baseRun()]);
    const h = await mount(rig);
    const graph = { "3": { class_type: "KSampler" } };
    await h.fire(WF_RUN_GRAPH, { runId: "wf-1", graph });
    expect(rig.state.list[0].graph).toEqual(graph);
    expect(rig.state.list[0].toolNodeId).toBe("n0");

    const snapshot = rig.state.list.map((r) => ({ ...r }));
    await h.fire(WF_RUN_GRAPH, { runId: "wf-unknown", graph: { "9": {} } });
    expect(rig.state.list).toEqual(snapshot);
    await h.unmount();
  });

  it("done：先按 ref 找原位替换锚点再移除；id 未知时锚点不动、列表引用不变", async () => {
    const a = baseRun({ id: "wf-a", x: 11, y: 22 });
    const b = baseRun({ id: "wf-b" });
    const rig = makeRig([a, b]);
    const h = await mount(rig);

    await h.fire(WF_DONE, { taskId: "wf-b" });
    expect(rig.pendingAnchorRef.current).toEqual({ x: 5, y: 6, w: CARD_W, h: 427 });
    expect(rig.state.list.map((r) => r.id)).toEqual(["wf-a"]);

    const before = rig.state.list;
    await h.fire(WF_DONE, { taskId: "wf-gone" });
    expect(rig.state.list).toBe(before);          // dropWfRun 无事发生 → 原引用
    expect(rig.pendingAnchorRef.current).toEqual({ x: 5, y: 6, w: CARD_W, h: 427 });
    await h.unmount();
  });

  it("四个事件在 detail / id 缺失时一律早退（不动列表、不落点、不写锚点）", async () => {
    const rig = makeRig([baseRun()]);
    const h = await mount(rig);
    const before = rig.state.list;
    for (const name of [WF_RUN, WF_RUN_GRAPH, WF_PROGRESS, WF_DONE]) {
      await h.fire(name);              // 无 detail
      await h.fire(name, {});          // detail 里没有 id
    }
    expect(rig.state.list).toBe(before);
    expect(rig.state.placeCalls).toHaveLength(0);
    expect(rig.pendingAnchorRef.current).toBeNull();
    await h.unmount();
  });

  it("卸载后四个监听全部解绑（再派发不再有任何反应）", async () => {
    const rig = makeRig([baseRun()]);
    const h = await mount(rig);
    await h.unmount();
    const after: WfRun[] = [];
    rig.setWfRuns(after);
    await act(async () => {
      for (const name of [WF_RUN, WF_RUN_GRAPH, WF_PROGRESS, WF_DONE]) {
        window.dispatchEvent(new CustomEvent(name, { detail: { runId: "x", taskId: "x" } }));
      }
    });
    expect(rig.state.list).toEqual(after);
    expect(rig.state.placeCalls).toHaveLength(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("等价矩阵：产物 ≡ 改造前手写写法（逐点相等）", () => {
  it("progress 的「合并 / 新建」两路 × 72 组输入网格", async () => {
    const PROMPTS: Array<string | undefined> = ["", "p1"];
    const PROGRESS: Array<number | null | undefined> = [undefined, null, 0.5];
    const NODES: Array<string | undefined> = [undefined, "", "3"];
    const TEMPLATES: Array<string | undefined> = ["", "T"];

    let checked = 0;
    for (const exists of [false, true]) {
      for (const promptId of PROMPTS) {
        for (const progress of PROGRESS) {
          for (const node of NODES) {
            for (const templateName of TEMPLATES) {
              const base = baseRun({ id: "wf-1", x: 5, y: 6 });
              const rig = makeRig([base]);
              const h = await mount(rig);
              const d: ProgDetail = {
                taskId: exists ? "wf-1" : "wf-new", promptId, progress, node, templateName,
              };
              await h.fire(WF_PROGRESS, d);
              const expected = exists
                ? [legacyProgMerge(base, d)]
                : [base, legacyProgCreate(d, { x: 100, y: 200 })];
              expect(rig.state.list, JSON.stringify({ exists, ...d })).toEqual(expected);
              await h.unmount();
              checked += 1;
            }
          }
        }
      }
    }
    expect(checked).toBe(72);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("源码形态锁（行为网抓不到的分层回退）", () => {
  const read = (rel: string) =>
    readFileSync(new URL(rel, import.meta.url), "utf-8").replace(/\r\n/g, "\n");
  const moduleSrc = read("./useCanvasWfEvents.ts");
  const canvasSrc = read("../views/CanvasStageFlow.tsx");

  it("依赖数组仍是 [placePlaceholder]（改它会改变重订阅时机）", () => {
    expect(moduleSrc.match(/useEffect\(\(\) => \{/g)).toHaveLength(1);
    expect(moduleSrc.match(/\}, \[placePlaceholder\]\);/g)).toHaveLength(1);
  });

  it("四个事件各注册一次、各解绑一次，且配对不串", () => {
    expect(moduleSrc.match(/window\.addEventListener\(/g)).toHaveLength(4);
    expect(moduleSrc.match(/window\.removeEventListener\(/g)).toHaveLength(4);
    for (const name of [WF_RUN, WF_RUN_GRAPH, WF_PROGRESS, WF_DONE]) {
      expect(moduleSrc.match(new RegExp(`addEventListener\\("${name}", (\\w+)\\)`)), name)
        .not.toBeNull();
      const added = moduleSrc.match(new RegExp(`addEventListener\\("${name}", (\\w+)\\)`))![1];
      expect(moduleSrc, name).toMatch(
        new RegExp(`removeEventListener\\("${name}", ${added}\\)`),
      );
    }
  });

  it("画布侧只剩一行调用：四个事件不再由组件自己订阅", () => {
    expect(canvasSrc.match(/useCanvasWfEvents\(/g)).toHaveLength(1);
    expect(canvasSrc).not.toMatch(/addEventListener\("laf-canvas-wf-/);
    expect(canvasSrc).not.toMatch(/removeEventListener\("laf-canvas-wf-/);
  });

  it("画布侧不得再出现「只属于已搬走逻辑」的语句（闭包锁）", () => {
    for (const token of [
      "const onRun = (e: Event)",
      "const onProg = (e: Event)",
      "const onDone = (e: Event)",
      "const onRunGraph = (e: Event)",
      "addWfRun(", "updateWfRun(", "upsertWfRun(", "dropWfRun(",
      "pendingAnchorRef.current = { x: run.x",
    ]) {
      expect(canvasSrc, token).not.toContain(token);
    }
  });

  it("画布文件没有把事件桥长回去（行数上界；内联回退会让它涨 60+ 行）", () => {
    const lines = canvasSrc.split("\n").length - 1;
    expect(lines).toBeLessThanOrEqual(3240);
  });
});
