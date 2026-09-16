import { describe, expect, it } from "vitest";

import { joinForeground, leaveForeground } from "./foregroundRunQueue";

/**
 * `foregroundRunQueue` 的特征测试（2026-09-15 架构审计 P12 · F2）。
 *
 * 改造前 `lib/useChatSession.ts` 手写了 5 份同样的「出队 + 判空」：
 *   `activePromptQueue.current = activePromptQueue.current.filter(id => id !== pending.prompt_id);
 *    if (activePromptQueue.current.length === 0) { …各自不同的尾巴… }`
 * 以及 1 份入队：`push(promptId)` + `if (length === 1) { 接管进度条 }`。
 * 本文件用 `legacy*` 参考实现把「接缝 ≡ 旧写法」钉成矩阵。
 *
 * ⚠ **5 个终态的尾巴留在调用方、且各不相同**（本模块只管队列）：
 *   | 终态 | idle 时 | 非 idle 时 |
 *   |---|---|---|
 *   | `completed` | 停进度 + 清慢守望 + 派发 workflowDone | **把进度 WS 订阅切到新队首** |
 *   | `failed` | 停进度 + 清慢守望 + 派发 workflowDone | 无动作 |
 *   | `stalled` | 停进度 + 清慢守望 + 派发 workflowDone | 无动作 |
 *   | `released` | 停进度 + 派发 workflowDone（**不清慢守望**，随后另置它） | 无动作 |
 *   | `timedOut` | 清慢守望（**不停进度、不派发**；且只有非后台任务才出队） | 无动作 |
 * 这张表是**行为合同**，由 `useChatSession` 的源码形态锁 + P20 e2e 共同看住；
 * 本文件只保证「谁判定 idle」只有一个属主。
 */

/** 改造前的入队写法。 */
function legacyJoin(queue: string[], promptId: string): { queue: string[]; first: boolean } {
  const next = [...queue];
  next.push(promptId);
  return { queue: next, first: next.length === 1 };
}

/** 改造前的出队写法。 */
function legacyLeave(queue: string[], promptId: string): { queue: string[]; idle: boolean; head: string | undefined } {
  const next = queue.filter((id) => id !== promptId);
  return { queue: next, idle: next.length === 0, head: next[0] };
}

const QUEUES: Array<[string, string[], string]> = [
  ["空队列", [], "p1"],
  ["单元素·命中", ["p1"], "p1"],
  ["单元素·未命中", ["p2"], "p1"],
  ["两元素·命中队首", ["p1", "p2"], "p1"],
  ["两元素·命中队尾", ["p1", "p2"], "p2"],
  ["三元素·命中中间", ["p1", "p2", "p3"], "p2"],
  ["重复 id 只留一份以外的全部删除", ["p1", "p2", "p1"], "p1"],
];

describe("P12 · 等价矩阵：入队/出队 ≡ 改造前手写写法", () => {
  for (const [name, queue, id] of QUEUES) {
    it(`joinForeground ≡ 旧 push + length===1（${name}）`, () => {
      expect(joinForeground(queue, id)).toEqual(legacyJoin(queue, id));
    });

    it(`leaveForeground ≡ 旧 filter + length===0（${name}）`, () => {
      expect(leaveForeground(queue, id)).toEqual(legacyLeave(queue, id));
    });
  }

  it("入参队列**不被就地修改**（旧写法是 `push` 原地改 ref；新写法必须返回新数组）", () => {
    const queue = ["p1"];
    const snapshot = [...queue];
    joinForeground(queue, "p2");
    leaveForeground(queue, "p1");
    expect(queue).toEqual(snapshot);
  });
});

describe("P12 · 队列语义（FIFO + 空闲判定）", () => {
  it("首个体现在 first=true，后续为 false（决定谁接管进度条 WS）", () => {
    const one = joinForeground([], "p1");
    expect(one.first).toBe(true);
    const two = joinForeground(one.queue, "p2");
    expect(two.first).toBe(false);
    expect(two.queue).toEqual(["p1", "p2"]);
  });

  it("FIFO：head 是**最早**入队的那个（`completed` 用它切下一个订阅）", () => {
    const { queue } = joinForeground(joinForeground([], "p1").queue, "p2");
    const after = leaveForeground(queue, "p1");
    expect(after.idle).toBe(false);
    expect(after.head).toBe("p2");
  });

  it("idle ⇔ 队列为空；head 在 idle 时是 undefined", () => {
    const empty = leaveForeground([], "p1");
    expect(empty).toEqual({ queue: [], idle: true, head: undefined });
    expect(leaveForeground(["p1"], "p1").idle).toBe(true);
    expect(leaveForeground(["p1"], "p1").head).toBeUndefined();
  });

  it("出队一个**不在队列里**的 id：队列不变，也不算空闲（后台任务不占前台队列）", () => {
    const after = leaveForeground(["p1"], "p-bg");
    expect(after.queue).toEqual(["p1"]);
    expect(after.idle).toBe(false);
    expect(after.head).toBe("p1");
  });

  it("入队同一个 id 两次会得到两份（原实现就是 push；后台/前台由调用方分流，不靠去重）", () => {
    const { queue } = joinForeground(joinForeground([], "p1").queue, "p1");
    expect(queue).toEqual(["p1", "p1"]);
    // 出队按值过滤 ⇒ 一次把两份都清掉（与旧写法一致）
    expect(leaveForeground(queue, "p1").idle).toBe(true);
  });

  it("连续出队直到空：每一步的 idle 与真实长度一致（进度条只在最后一次停下）", () => {
    let queue = joinForeground(joinForeground([], "p1").queue, "p2").queue;
    const states: boolean[] = [];
    for (const id of ["p1", "p2"]) {
      const step = leaveForeground(queue, id);
      queue = step.queue;
      states.push(step.idle);
    }
    expect(states).toEqual([false, true]);
  });
});

// ── P22（被取消的任务必须出队）────────────────────────────────────────────────
//
// 背景（2026-09-15 实锤）：`workflowRuntime.cancel(pid)` 只做 `cancelled.add` + `pending.remove`，
// **不派发任何终态回调** —— 被取消的任务随后在 runtime 里走 `notFound`（pending 已不在列表
// ⇒ 静默 `return`）或停顿守卫的同款静默 `return`，5 个终态回调一个都不跑。而出队原本只挂在
// 终态回调上 ⇒ 被取消的 pid 永远留在 `activePromptQueue`，下一次前台任务 `joinForeground`
// 得 `first=false`、**不再订阅进度 WS**（症状：停一次之后，后续出图全程无进度）。
//
// 修法：取消路径改成「cancel + 出队」的单一属主 `useChatSession.cancelForegroundRun`。
// 本组钉住该修法所依赖的两条队列语义；接线本身由 `useChatSession.contract.test.ts` 的
// P22 组按源码锁看住（纯函数侧怎么测都抓不到「终止路径漏了一个动作」）。
describe("P22 · 取消一个前台任务后，下一个前台任务必须重新接管进度条", () => {
  it("残留 id 会让新任务拿 first=false —— 先把症状本身钉死", () => {
    const stale = ["p1"];                  // p1 已被取消，但没出队（缺陷现场）
    const joined = joinForeground(stale, "p2");
    expect(joined.first).toBe(false);      // ← 旧行为：p2 不订阅进度 WS
  });

  it("取消时出队 ⇒ 新任务 reclaim first=true（修复后的行为）", () => {
    let queue = joinForeground([], "p1").queue;
    queue = leaveForeground(queue, "p1").queue;   // ← `cancelForegroundRun` 里的那一步
    const joined = joinForeground(queue, "p2");
    expect(joined.first).toBe(true);
    expect(joined.queue).toEqual(["p2"]);
  });

  it("取消一个**不在队列里**的 id 是幂等的（慢守望 / 后台 / 恢复期 pid 同一入口，不误伤）", () => {
    const queue = ["p1", "p2"];
    const snapshot = [...queue];
    const left = leaveForeground(queue, "p-bg");
    expect(left.queue).toEqual(snapshot);
    expect(left.idle).toBe(false);
    expect(left.head).toBe("p1");
    expect(queue).toEqual(snapshot);       // 入参仍不被就地修改
  });

  it("取消队首而队列还有别的任务时不算空闲（不误停进度条、不误判收尾）", () => {
    const left = leaveForeground(["p1", "p2"], "p1");
    expect(left.idle).toBe(false);
    expect(left.head).toBe("p2");
  });
});

