// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * 「前台运转任务 FIFO」的**源码形态接线锁**（2026-09-15 架构审计 P12 · F2）。
 *
 * 背景：`lib/useChatSession.ts` 里「出队并判断是否已清空」被**手写了 5 遍**
 * （`completed` / `failed` / `released` / `timedOut` / `stalled` 各一份 `filter(id => id !== …)`
 * + `if (length === 0)`），入队判定又写了一遍（`push` + `if (length === 1)`）。
 * 任一处写错都不报错：队列非空时 WS 订阅会漏退订/漏切换；该空时不空则「运转中」永不结束。
 *
 * 行为面（入队/出队/首个/空闲/队首）由 `foregroundRunQueue.test.ts` 的**等价矩阵**
 * （新接缝结果 ≡ 改造前手写写法结果）覆盖；本文件只钉两件行为网测不出的事：
 *   ① **五处终态一个都不能漏**（漏一处 → 那个终态永不结束，行为网只在「恰好走到那个终态」时才红）；
 *   ② **各终态的尾巴是刻意差异**（收口只收队列本身，尾巴样式不得被「统一」掉）；
 *   ③ **取消路径也必须出队**（2026-09-15 P22 新增的第 6 个出队点）。
 *
 * ⚠ **P22 为什么需要第 6 个出队点**：`workflowRuntime.cancel(pid)` 只做 `cancelled.add` +
 *   `pending.remove`，**不派发任何终态回调**（被取消的任务随后走 `notFound` / 停顿守卫的静默
 *   `return`）。出队原本只挂在终态回调上 ⇒ 被取消的 pid 永远留在 `activePromptQueue`，
 *   下一次前台任务 `joinForeground` 得 `first=false`、进度条不再订阅。故取消路径必须补出队，
 *   单一属主是 `useChatSession.cancelForegroundRun`（本文件下方钉其形态，
 *   `useChatSession.contract.test.ts` 的「P22」组钉其调用点覆盖）。
 *
 * ⚠ 为什么不改成「从 `runtime.list()` 投影」（清单原建议，实测否决）：
 *   `timedOut` / `released` 终态**不 remove** pending（转后台继续 / 刷新后恢复的语义），
 *   投影会把它们算成「仍在跑」⇒ 进度条永不结束。该实测由
 *   `workflowGenerationRuntime.test.ts` 的「P12 佐证 · 终态对 runtime.pending 的去留」钉住。
 */
const viewSrc = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");

const hook = viewSrc("./useChatSession.ts");
const seam = viewSrc("./foregroundRunQueue.ts");

/** 观察者回调以 4 空格缩进的 `},` 收尾；据此把单个终态回调切出来单独断言。 */
const callbackBody = (marker: string): string => {
  const start = hook.indexOf(marker);
  expect(start, `找不到终态回调 ${marker}`).toBeGreaterThan(-1);
  const end = hook.indexOf("\n    },", start);
  expect(end, `${marker} 未闭合`).toBeGreaterThan(-1);
  return hook.slice(start, end);
};

const TERMINALS = ["completed: (", "failed: (", "released: (", "timedOut: (", "stalled: ("] as const;

/** 「空闲 → 停进度 + 清慢守望 + 派发 workflowDone」——completed / failed / stalled 三者同形。 */
const IDLE_TAIL = /if \(left\.idle\) \{\s*stopProgress\(\);\s*setSlowWatchPromptId\(null\);\s*dispatch\(\{ t: "workflowDone", promptId: pending\.prompt_id \}\);\s*\}/;

describe("P12 · 出队判定收归单一属主：五处终态必须全走接缝", () => {
  it("接缝导出 join / leave 两个原语（含返回结构）", () => {
    expect(seam).toMatch(/export function joinForeground\(queue: string\[\], promptId: string\): ForegroundJoin/);
    expect(seam).toMatch(/export function leaveForeground\(queue: string\[\], promptId: string\): ForegroundLeave/);
  });

  it("入队恰 1 处、出队恰 6 处＝5 个终态 + 1 条取消路径（增删出队点必须同步改本锁）", () => {
    expect([...hook.matchAll(/joinForeground\(/g)]).toHaveLength(1);
    // 5 处来自五个终态回调（下方逐个钉住）+ 1 处来自 P22 的取消路径（再下方单独钉形态）
    expect([...hook.matchAll(/leaveForeground\(/g)]).toHaveLength(6);
  });

  it("第 6 个出队点是「取消」而非「终态」：cancel 与出队必须同体（P22）", () => {
    // 这一条不是给 P12 收口背书，而是给 P22 的分层锁上保险：runtime 的 cancel 不派发终态，
    // 出队只能由调用方补 ⇒ 若把它删掉（或拆散成两个函数），队列会残留被取消的 pid，
    // 下一次前台任务拿到 `first=false`、进度条 WS 不再订阅。
    expect(hook).toMatch(
      /const cancelForegroundRun = \(promptId: string\): void => \{\s*workflowRuntime\.cancel\(promptId\);\s*const left = leaveForeground\(activePromptQueue\.current, promptId\);\s*activePromptQueue\.current = left\.queue;\s*\};/,
    );
  });

  it("入队点接住 `first`（首个才接管进度条 WS）", () => {
    expect(hook).toMatch(
      /const joined = joinForeground\(activePromptQueue\.current, promptId\);\s*activePromptQueue\.current = joined\.queue;\s*if \(joined\.first\) \{/,
    );
  });

  it("五个终态回调逐个都经 leaveForeground 出队（漏一处不会被别处盖住）", () => {
    for (const marker of TERMINALS) {
      expect(callbackBody(marker), marker).toContain(
        "leaveForeground(activePromptQueue.current, pending.prompt_id)",
      );
    }
  });

  it("不得再手写队列同步（filter 出队 / push 入队 / 裸 length 判定）", () => {
    expect(hook).not.toMatch(/activePromptQueue\.current = activePromptQueue\.current\.filter\(/);
    expect(hook).not.toMatch(/activePromptQueue\.current\.push\(/);
    expect(hook).not.toMatch(/activePromptQueue\.current\.length === (0|1)/);
  });
});

describe("P12 · 各终态的尾巴是刻意差异（收口只收队列，不得统一）", () => {
  it("completed：非空闲要把 WS 订阅切到新队首；其余四个终态都不切换", () => {
    expect(callbackBody("completed: (")).toContain("wsUnsubRef.current = subscribeProgress(nextUrl, nextId,");
    for (const marker of ["failed: (", "released: (", "timedOut: (", "stalled: ("] as const) {
      expect(callbackBody(marker), marker).not.toContain("subscribeProgress(");
    }
  });

  it("completed / failed / stalled：空闲尾巴是同一段三步（停进度 + 清慢守望 + 派发）", () => {
    for (const marker of ["completed: (", "failed: (", "stalled: ("] as const) {
      expect(callbackBody(marker), marker).toMatch(IDLE_TAIL);
    }
  });

  it("released：空闲只停进度 + 派发（**不清**慢守望——随后反而要把它置为本任务）", () => {
    const released = callbackBody("released: (");
    expect(released).toMatch(
      /if \(left\.idle\) \{\s*stopProgress\(\);\s*dispatch\(\{ t: "workflowDone", promptId: pending\.prompt_id \}\);\s*\}/,
    );
    expect(released).not.toContain("setSlowWatchPromptId(null)");
    expect(released).toContain("setSlowWatchPromptId(pending.prompt_id);");
  });

  it("timedOut：前台任务只在空闲时清慢守望（不停进度、不派发）", () => {
    const timedOut = callbackBody("timedOut: (");
    expect(timedOut).toMatch(/if \(left\.idle\) setSlowWatchPromptId\(null\);/);
    expect(timedOut).not.toContain("stopProgress()");
    expect(timedOut).not.toContain("workflowDone");
  });

  it("failed / stalled：派发后仍各带自己的文案（逐字保留）", () => {
    expect(callbackBody("failed: (")).toContain("⚠️ 出图任务已丢失（ComfyUI 可能已重启或队列被清空）");
    expect(callbackBody("stalled: (")).toContain("⚠️ 出图任务长时间未开始运转（ComfyUI 队列未加载节点）");
  });

  it("队列[不]投影自 runtime：接缝不认识 runtime，hook 也不拿 list() 回填队列", () => {
    expect(seam).not.toMatch(/runtime/i);
    expect(seam).not.toMatch(/\blist\(\)/);
    expect(hook).not.toMatch(/activePromptQueue\.current = [^\n]*\.list\(\)/);
  });
});
