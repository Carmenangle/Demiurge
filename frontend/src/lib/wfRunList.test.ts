import { describe, expect, it } from "vitest";

import type { WfRunLike } from "./wfRunList";
import { addWfRun, dropWfRun, updateWfRun, upsertWfRun } from "./wfRunList";

/**
 * `wfRunList` 的特征测试（2026-09-15 架构审计 P12 · F2）。
 *
 * **为什么用「等价矩阵」而不是渲染测试**：这两个列表的宿主
 * （`views/ChatView.tsx` 的 `canvasWfRuns` / `views/CanvasStageFlow.tsx` 的 `wfRuns`）
 * 都没有组件测试（审计 R5），而它们的事件处理就是纯列表归约。
 * 于是特征测试的形态 = **把改造前两处手写的写法原样抄成 `legacy*` 参考实现**，
 * 对同一批输入断言「新接缝的结果 ≡ 手写写法的结果」——
 * 这样「零行为变化」是被**矩阵**证明的，不是靠读代码相信。
 *
 * 真机跨组件验收归 P20 的画布 e2e。
 */

interface Run extends WfRunLike {
  templateName: string;
  progress: number | null;
  node?: string;
  /** 画布专有（路径 ③） */
  x?: number;
  graph?: unknown;
}

// ── 参考实现：改造前 `ChatView.tsx`（路径 ②）与 `CanvasStageFlow.tsx`（路径 ③）的原写法 ──

/** ② `onRun` / ③ `onRun`：不存在才追加，已存在**返回原引用**。 */
const legacyAdd = (list: Run[], id: string, create: () => Run): Run[] =>
  list.some((r) => r.id === id) ? list : [...list, create()];

/** ③ `onRunGraph`：**无条件**逐项 map（不存在也不新建）。 */
const legacyGraph = (list: Run[], id: string, graph: unknown): Run[] =>
  list.map((r) => (r.id === id ? { ...r, graph } : r));

/** ② `onProg`：存在则合并，不存在则**追加并直接用它当新项**。 */
const legacyUpsert = (
  list: Run[], id: string, patch: { progress?: number | null; node?: string; templateName: string },
): Run[] =>
  list.some((r) => r.id === id)
    ? list.map((r) => (r.id === id ? {
      ...r,
      progress: patch.progress !== undefined ? patch.progress : r.progress,
      node: patch.node !== undefined ? patch.node : r.node,
      templateName: patch.templateName || r.templateName,
    } : r))
    : [...list, {
      id,
      templateName: patch.templateName || "工作流",
      progress: patch.progress !== undefined ? patch.progress : null,
      node: patch.node || undefined,
    }];

/** ② / ③ `onDone`：按 id 移除，不存在**返回原引用**。 */
const legacyDrop = (list: Run[], id: string): Run[] =>
  list.some((r) => r.id === id) ? list.filter((r) => r.id !== id) : list;

// ── 新接缝的对应写法（调用方各自传自己的 create/merge） ─────────────────────────

const newAdd = (list: Run[], id: string, item: Run): Run[] => addWfRun(list, id, () => item);
const newGraph = (list: Run[], id: string, graph: unknown): Run[] => updateWfRun(list, id, (r) => ({ ...r, graph }));
const newUpsert = (
  list: Run[], id: string, patch: { progress?: number | null; node?: string; templateName: string },
): Run[] => upsertWfRun(
  list, id,
  (r) => ({
    ...r,
    progress: patch.progress !== undefined ? patch.progress : r.progress,
    node: patch.node !== undefined ? patch.node : r.node,
    templateName: patch.templateName || r.templateName,
  }),
  () => ({
    id,
    templateName: patch.templateName || "工作流",
    progress: patch.progress !== undefined ? patch.progress : null,
    node: patch.node || undefined,
  }),
);
const newDrop = (list: Run[], id: string): Run[] => dropWfRun(list, id);

const RUN_A: Run = { id: "run-a", templateName: "T1", progress: 10, node: "KSampler (#3)", x: 0 };
const RUN_B: Run = { id: "run-b", templateName: "T2", progress: null, x: 400 };
const EMPTY: Run[] = [];

/** 覆盖「列表状态 × 事件目标（命中 / 未命中）」的输入矩阵。 */
const LISTS: Array<[string, Run[]]> = [
  ["空列表", EMPTY],
  ["单元素·命中", [RUN_A]],
  ["单元素·未命中", [RUN_B]],
  ["两元素·命中首个", [RUN_A, RUN_B]],
  ["两元素·命中末个", [RUN_A, RUN_B]],
];
const targetOf = (name: string): string => (name.includes("未命中") ? "run-z" : (name.includes("末个") ? "run-b" : "run-a"));

describe("P12 · 等价矩阵：新接缝 ≡ 改造前两处手写写法", () => {
  for (const [name, list] of LISTS) {
    const id = targetOf(name);

    it(`addWfRun ≡ ② / ③ 的 onRun（${name}）`, () => {
      const item = { id, templateName: "新", progress: null };
      expect(newAdd(list, id, item)).toEqual(legacyAdd(list, id, () => item));
    });

    it(`updateWfRun ≡ ③ 的 onRunGraph（${name}）`, () => {
      expect(newGraph(list, id, { n: 1 })).toEqual(legacyGraph(list, id, { n: 1 }));
    });

    it(`upsertWfRun ≡ ② 的 onProg（${name}）`, () => {
      const patch = { progress: 42, node: "SaveImage (#9)", templateName: "T9" };
      expect(newUpsert(list, id, patch)).toEqual(legacyUpsert(list, id, patch));
    });

    it(`dropWfRun ≡ ② / ③ 的 onDone（${name}）`, () => {
      expect(newDrop(list, id)).toEqual(legacyDrop(list, id));
    });
  }

  it("两条渲染路径（② 进度条 / ③ 占位）在**同一条事件序列**下得到同样顺序的 id 列表", () => {
    // 事件序列 = 原语 `runCanvasWorkflow` 实际派发的顺序：run-graph → progress(多次) → done
    const events: Array<(l: Run[]) => Run[]> = [
      (l) => newAdd(l, "run-a", { id: "run-a", templateName: "T", progress: null, x: 0 }),
      (l) => newGraph(l, "run-a", { "3": { class_type: "KSampler" } }),
      (l) => newUpsert(l, "run-a", { progress: 0, templateName: "T" }),
      (l) => newUpsert(l, "run-a", { progress: 55, node: "KSampler (#3)", templateName: "T" }),
      (l) => newAdd(l, "run-b", { id: "run-b", templateName: "T2", progress: null, x: 400 }),
      (l) => newDrop(l, "run-a"),
      (l) => newDrop(l, "run-b"),
    ];
    let list: Run[] = [];
    const ids: string[][] = [];
    for (const step of events) {
      list = step(list);
      ids.push(list.map((r) => r.id));
    }
    expect(ids).toEqual([
      ["run-a"], ["run-a"], ["run-a"], ["run-a"], ["run-a", "run-b"], ["run-b"], [],
    ]);
    // 每个 id 都只出现一次（upsert 不会重复插入）
    expect(ids.every((row) => new Set(row).size === row.length)).toBe(true);
  });
});

describe("P12 · 引用语义（无事发生必须返回入参原引用，否则白白 re-render）", () => {
  it("addWfRun：id 已存在 → 返回原引用", () => {
    const list = [RUN_A];
    expect(newAdd(list, "run-a", { id: "run-a", templateName: "X", progress: null })).toBe(list);
  });

  it("updateWfRun：id 不存在 → 内容不变（与旧 map 写法逐项相等）", () => {
    const list = [RUN_A, RUN_B];
    const out = newGraph(list, "run-z", { n: 1 });
    expect(out).toEqual(list);
    expect(out).not.toBe(list);   // ← 与改造前 onRunGraph 的「无条件 map」一致
  });

  it("upsertWfRun：id 已存在 → 返回新数组（合并必然改字段）", () => {
    const list = [RUN_A];
    const out = newUpsert(list, "run-a", { progress: 99, templateName: "T1" });
    expect(out).not.toBe(list);
    expect(out).toHaveLength(1);
    expect(out[0].progress).toBe(99);
  });

  it("dropWfRun：id 不存在 → 返回原引用", () => {
    const list = [RUN_A];
    expect(newDrop(list, "run-z")).toBe(list);
    expect(newDrop([], "run-z")).toEqual([]);
  });

  it("入参列表**不被就地修改**（四个操作都返回新列表或原引用，绝不 push/splice 原数组）", () => {
    const list = [RUN_A];
    const snapshot = JSON.parse(JSON.stringify(list));
    newAdd(list, "run-b", RUN_B);
    newGraph(list, "run-a", { n: 1 });
    newUpsert(list, "run-b", { progress: 1, templateName: "T2" });
    newDrop(list, "run-b");
    expect(list).toEqual(snapshot);
  });
});

describe("P12 · 刻意差异留在调用方（本模块不得统一）", () => {
  it("标签来源不是本模块的事：nodeLabel 的取舍由调用方在 patch 里给", () => {
    // ② 用 `d.nodeLabel || d.node`（优先 class_type 标签）；③ 直接用 `d.node`。
    // 两者都只是「把算好的 node 塞进 patch」，本模块不认识 nodeLabel。
    const withLabel = newUpsert([RUN_B], "run-b", { node: "KSampler (#3)", templateName: "T2" });
    const bareId = newUpsert([RUN_B], "run-b", { node: "3", templateName: "T2" });
    expect(withLabel[0].node).toBe("KSampler (#3)");
    expect(bareId[0].node).toBe("3");
  });

  it("patch 里**缺省**的字段保持原值（`undefined` 不当「清空」用）", () => {
    const list: Run[] = [{ id: "run-a", templateName: "T1", progress: 30, node: "N" }];
    const out = newUpsert(list, "run-a", { templateName: "T1" });
    expect(out[0].progress).toBe(30);
    expect(out[0].node).toBe("N");
  });

  it("patch 里的 `null` **会**覆盖（进度未知 = progress:null，与 undefined 语义不同）", () => {
    const list: Run[] = [{ id: "run-a", templateName: "T1", progress: 30 }];
    const out = newUpsert(list, "run-a", { progress: null, templateName: "T1" });
    expect(out[0].progress).toBeNull();
  });

  it("templateName 为空串时保持原值（`patch.templateName || r.templateName`）", () => {
    const list: Run[] = [{ id: "run-a", templateName: "保留我", progress: null }];
    const out = newUpsert(list, "run-a", { templateName: "" });
    expect(out[0].templateName).toBe("保留我");
  });
});
