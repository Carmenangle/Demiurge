// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * 「运转任务列表」的**源码形态接线锁**（2026-09-15 架构审计 P12 · F2）。
 *
 * 背景：`views/ChatView.tsx` 的 `canvasWfRuns`（对话框进度条）与 `views/CanvasStageFlow.tsx`
 * 的 `wfRuns`（画布「生成中」占位节点）原先**各持一套逐字相同的 `laf-canvas-wf-run` /
 * `-progress` / `-done` → 列表归约**（各约 25 行）。两个属主 ⇒ 任一处漏同步都不报错，
 * 且已经出现事实性漂移（同一条事件序列下两处行为不同：恢复时占位节点不回填，而进度条
 * 走 `getRuntime().list()` 回填）。
 *
 * 为什么只能上源码锁：这两个宿主**没有组件测试**（审计 R5），而事件处理就是纯列表归约
 * ⇒ 行为面由 `wfRunList.test.ts` 的**等价矩阵**（新接缝结果 ≡ 改造前手写写法结果）覆盖，
 * 本文件只钉「分层没被绕过」与「刻意差异没被抹掉」这两件**行为网测不出**的事。
 * 前科：`GET /narrative/` 曾漏调 `with_sequence`，界面显示错位而 2650 例单测全绿 ——
 * 接线错必须在**出口层**断言。
 *
 * 真机跨组件验收归 P20 的画布 e2e。
 */
const viewSrc = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");

const chatView = viewSrc("../views/ChatView.tsx");
const canvas = viewSrc("../views/CanvasStageFlow.tsx");
const seam = viewSrc("./wfRunList.ts");

/** 改造前两处手写的「按 id 增删」三种形态 —— 再出现即为单一属主被绕过。 */
const HAND_WRITTEN = [
  /\(prev\) => \(prev\.some\(\(r\) => r\.id ===/,   // 幂等追加（onRun）
  /const exists = prev\.some\(\(r\) => r\.id ===/,   // 存在性判定（onProg 的 upsert）
  /\.filter\(\(r\) => r\.id !== /,                   // 按 id 移除（onDone）
] as const;

describe("P12 · 列表增删的单一属主：两处调用点必须走接缝", () => {
  it("四条原语都在属主里（泛型签名，改名/去泛型在此挡下）", () => {
    for (const fn of ["addWfRun", "updateWfRun", "upsertWfRun", "dropWfRun"]) {
      expect(seam, fn).toMatch(new RegExp(`export function ${fn}<`));
    }
  });

  it("ChatView：run / progress / done 三点分别走 add / upsert / drop", () => {
    expect(chatView).toMatch(/setCanvasWfRuns\(\(prev\) => addWfRun\(prev, d\.runId!, \(\) => \(\{/);
    expect(chatView).toMatch(/setCanvasWfRuns\(\(prev\) => upsertWfRun\(\s*prev, d\.taskId!,/);
    expect(chatView).toMatch(/setCanvasWfRuns\(\(prev\) => dropWfRun\(prev, d\.taskId!\)\)/);
  });

  it("画布：run / run-graph / progress / done 四点分别走 add / update / upsert / drop", () => {
    expect(canvas).toMatch(/setWfRuns\(\(prev\) => addWfRun\(prev, d\.runId!, \(\) => \(\{/);
    expect(canvas).toMatch(
      /setWfRuns\(\(prev\) => updateWfRun\(prev, d\.runId!, \(r\) => \(\{ \.\.\.r, graph: d\.graph \}\)\)\)/,
    );
    expect(canvas).toMatch(/setWfRuns\(\(prev\) => upsertWfRun\(\s*prev, d\.taskId!,/);
    expect(canvas).toMatch(/setWfRuns\(\(prev\) => dropWfRun\(prev, d\.taskId!\)\)/);
  });

  it("两处都不得再手写按 id 的增删（分层回退）", () => {
    for (const [name, src] of [["ChatView", chatView], ["画布", canvas]] as const) {
      for (const pattern of HAND_WRITTEN) {
        expect(src, `${name} 仍在手写 ${pattern}`).not.toMatch(pattern);
      }
    }
  });
});

// ── 刻意差异：这一组一旦被「统一」，某一侧行为就会回归 ────────────────────────
//
// 收口只收「列表增删」这一层。字段映射、原位替换锚点、画布专有字段**刻意留在调用点**
// ——它们本来就不一样，不属于「重复」。
describe("P12 · 刻意差异必须留在调用点", () => {
  it("标签来源不同：ChatView 优先 nodeLabel（class_type 标签），画布只用 node", () => {
    expect(chatView).toMatch(/const nodeLabel = d\.nodeLabel \|\| d\.node;/);
    expect(canvas).toMatch(/node: d\.node !== undefined \? d\.node : r\.node,/);
    expect(canvas).not.toMatch(/d\.nodeLabel/);
  });

  it("画布新占位的落点：progress 路径在**事务内**用 `prev` 算（run 路径沿用 `wfRunsRef`，属既有差异）", () => {
    // 用 `wfRunsRef.current` 替代 `prev` 会让同一拍内连续两次事件各自算到同一个「空位」。
    expect(canvas).toMatch(/const pos = placePlaceholder\(CARD_W, 427, prev\.map\(/);
    expect(canvas).toMatch(/const pos = placePlaceholder\(CARD_W, 427, wfRunsRef\.current\.map\(/);
  });

  it("画布 onDone 仍记原位替换锚点（接缝只管移除，不认识锚点）", () => {
    expect(canvas).toMatch(
      /pendingAnchorRef\.current = \{ x: run\.x, y: run\.y, w: CARD_W, h: 427 \}/,
    );
    expect(seam).not.toMatch(/pendingAnchorRef/);
  });

  it("画布专有字段（x/y/toolNodeId/promptId/graph）与「新占位怎么放」不属接缝", () => {
    for (const token of ["toolNodeId", "promptId", "graph", "placePlaceholder"]) {
      expect(seam, token).not.toContain(token);
    }
  });
});
