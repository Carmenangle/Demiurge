// @vitest-environment jsdom
//
// usePlanTaskFeed 的特征网（T2.2-B1 · 2026-09-17）
//
// 背景：`lib/useChatSession.ts` 里「计划采集产物逐张实时上屏」的订阅 useEffect 改造前是
// **58 行内联体**（48 行 effect + 8 行语义注释），已**逐字节搬移**到 `lib/usePlanTaskFeed.ts`。
// 该 hook 没有组件测试（审计 R5）⇒ 三道网分工：
//
//   ① 行为网（describe「真订阅派发」）—— jsdom + React 19 `act` 真挂载 Host（只调用 hook），
//      真调 `subscribePlanTaskActivities` 注册的 listener、真跑 fake timer 驱动的轮询，
//      断言**经过 hook 之后**可观测的那一面（拉快照次数 / 消息上屏 / 事件派发 / 解绑）。
//   ② 等价矩阵（describe「等价矩阵」）—— 把改造前 listener 里「running/ended → settleLeft 60/6/0」
//      的判定**逐字抄**成 `legacySettleLeft` 参照，在 10 组 activities 网格上断言实际轮询轮数 ≡ 参照。
//   ③ 源码形态锁（describe「源码形态锁」）—— 行为网**抓不到分层回退**（hook 被删后内联回视图照样
//      可能全绿），故另钉：搬移语句逐字在新属主且视图缺席、依赖数组那一行、视图「恰好调用一次」
//      且调用位置仍在原 effect 位置（hook 调用序 = effect 执行序）。
//
// ⚠ 硬要求（承 T2.1-B2 四条教训）：
//   ① 用例隔离：`afterEach` 按登记表统一卸载（残留 root 会让下一用例的 setMessages 串味）；
//   ② 墓碑单例跨用例保留 ⇒ `beforeEach` 显式 clear；
//   ③ 形态锁必须**去注释**（否则注释里提一句事件名就算「还在订阅」）；
//   ④ 源码只能在 `it` 回调内读（jsdom 模块求值期 `import.meta.url` 不是 `file:` 协议）。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { act, createElement, type Dispatch, type SetStateAction } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types/chat";
import type { PlanTaskActivity } from "./planTaskActivity";
import { deletedMessageTombstones } from "./deletedMessageTombstones";
import { usePlanTaskFeed, type PlanTaskFeedDeps } from "./usePlanTaskFeed";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ─────────────────────────────────────────────────────────────────────────────
// 外部依赖替身：订阅源（手动触发 listener）+ 快照接口（计数 + 可控内容）
// ─────────────────────────────────────────────────────────────────────────────
const H = vi.hoisted(() => ({
  /** `subscribePlanTaskActivities` 注册进来的 listener（卸载时应被摘掉） */
  listeners: [] as Array<(items: unknown[]) => void>,
  /** `fetchSnapshot` 调用次数 = 轮询轮数 */
  fetches: 0,
  /** 下一轮快照返回的 items */
  snap: { items: [] as unknown[] },
  /** true = 每次 fetchSnapshot 挂起，等 `releases` 里的函数放行（复刻「慢快照」） */
  hold: false,
  releases: [] as Array<() => void>,
}));

vi.mock("./planTaskActivity", () => ({
  subscribePlanTaskActivities: (fn: (items: unknown[]) => void) => {
    H.listeners.push(fn);
    return () => {
      const at = H.listeners.indexOf(fn);
      if (at >= 0) H.listeners.splice(at, 1);
    };
  },
}));

vi.mock("../api/ai", () => ({
  fetchSnapshot: async () => {
    H.fetches += 1;
    if (H.hold) {
      // 挂起点：由用例显式放行 —— 覆盖「拉快照期间切走线程」这条真实失败路径
      await new Promise<void>((resolve) => { H.releases.push(resolve); });
    }
    return H.snap;
  },
}));

// ─────────────────────────────────────────────────────────────────────────────
// 改造前写法的**逐字抄录**（唯一等价性参照，不得顺手优化）
// ─────────────────────────────────────────────────────────────────────────────
const RUNNING = new Set(["queued", "running"]);
const ENDED = new Set(["done", "partial", "error", "cancelled", "blocked"]);

/** 改造前 listener 里「算 settleLeft」的那段，逐字抄录（repo_id 过滤 + running/ended 判定 + 60/6/0）。 */
const legacySettleLeft = (activities: PlanTaskActivity[], threadId: string): number => {
  const mine = activities.filter((a) => a.repo_id === threadId);
  const running = mine.some((a) => RUNNING.has(a.status));
  const ended = mine.some((a) => ENDED.has(a.status));
  if (!running && !ended) {
    return 0;
  }
  return running ? 60 : 6;
};

// ─────────────────────────────────────────────────────────────────────────────
// 挂载脚手架：真实 Host 组件（只调用 hook）+ 真 listener + fake timer
// ─────────────────────────────────────────────────────────────────────────────
const teardowns: Array<() => Promise<void>> = [];
async function teardownAll() {
  while (teardowns.length > 0) {
    const t = teardowns.pop()!;
    await t();
  }
}

type Act = PlanTaskActivity;
const act1 = (id: string, repoId: string, status: Act["status"]): Act => ({
  id, repo_id: repoId, intent: "意图", status, progress: "", needsApproval: false,
});
const msg = (over: Partial<ChatMessage> & { id: string }): ChatMessage =>
  ({ role: "assistant", text: "", ...over } as ChatMessage);

function makeDeps(threadId = "r1") {
  const state = { messages: [] as ChatMessage[] };
  const setMessages = ((arg: unknown) => {
    state.messages = typeof arg === "function"
      ? (arg as (m: ChatMessage[]) => ChatMessage[])(state.messages)
      : (arg as ChatMessage[]);
  }) as unknown as Dispatch<SetStateAction<ChatMessage[]>>;
  const deps = {
    threadId,
    recoveryActiveRef: { current: false },
    agentBusyRef: { current: false },
    activeThreadRef: { current: threadId },
    setMessages,
  } as PlanTaskFeedDeps;
  return { deps, state };
}

function Host({ deps }: { deps: PlanTaskFeedDeps }) {
  usePlanTaskFeed(deps);
  return null;
}

async function mount(deps: PlanTaskFeedDeps) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Host, { deps, key: "host" })); });
  const unmount = async () => {
    await act(async () => { root.unmount(); });
    host.remove();
  };
  teardowns.push(unmount);
  return { unmount };
}

/** 把一批 activities 投给订阅源（复刻真实 `publish()` 的整批投递）。 */
async function fire(activities: Act[]) {
  await act(async () => {
    for (const fn of [...H.listeners]) fn(activities as unknown[]);
  });
}

/** 推进虚拟时间（`advanceTimersByTimeAsync` 会在 timer 之间 flush 微任务 ⇒ tick 的 await 能跑完）。 */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const ADVANCE_ALL = 2000 * 200;   // 400s：足够跑完任一分支的全部轮询

beforeEach(() => {
  vi.useFakeTimers();
  H.listeners.length = 0;
  H.fetches = 0;
  H.snap = { items: [] };
  H.hold = false;
  H.releases.length = 0;
  deletedMessageTombstones.clear("r1");
  deletedMessageTombstones.clear("home");
});

afterEach(async () => {
  await teardownAll();
  vi.useRealTimers();
  vi.clearAllMocks();
  try { localStorage.clear(); } catch { /* jsdom 恒有，纯保险 */ }
  H.listeners.length = 0;
});

// ─────────────────────────────────────────────────────────────────────────────
describe("真订阅派发（jsdom：挂载 Host + 投递 activities + fake timer）", () => {
  it("home 线程不订阅（临时草稿区不跟计划任务）", async () => {
    const d = makeDeps("home");
    const h = await mount(d.deps);
    expect(H.listeners).toHaveLength(0);
    await h.unmount();
  });

  it("挂载即订阅一次，卸载即解绑", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    expect(H.listeners).toHaveLength(1);
    await h.unmount();
    expect(H.listeners).toHaveLength(0);
  });

  it("本会话无计划任务（空列表 / 只有别会话任务）→ 不轮询", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([]);
    await fire([act1("t-other", "r2", "running")]);
    await advance(ADVANCE_ALL);
    expect(H.fetches).toBe(0);
    await h.unmount();
  });

  it("running → 轮询 60 轮（运行中持续跟随）", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    await advance(ADVANCE_ALL);
    expect(H.fetches).toBe(60);
    await h.unmount();
  });

  it("终态（done）→ 再扫 6 轮收尾，之后停", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "done")]);
    await advance(ADVANCE_ALL);
    expect(H.fetches).toBe(6);
    await h.unmount();
  });

  it("Agent 恢复期（recoveryActiveRef）→ 顺延不丢扫尾：停下时不拉快照、解除后立刻续跑", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    d.deps.recoveryActiveRef.current = true;
    await advance(2000 * 5);
    expect(H.fetches).toBe(0);          // 忙 → tick 早退（settleLeft 不递减）
    d.deps.recoveryActiveRef.current = false;
    await advance(2000);
    expect(H.fetches).toBe(1);          // 解除后仍在调度 ⇒ 顺延的扫尾没丢
    await h.unmount();
  });

  it("切走线程（activeThreadRef 不匹配）→ tick 早退不拉快照，切回后续跑", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    d.deps.activeThreadRef.current = "r2";
    await advance(2000 * 5);
    expect(H.fetches).toBe(0);
    d.deps.activeThreadRef.current = "r1";
    await advance(2000);
    expect(H.fetches).toBe(1);
    await h.unmount();
  });

  it("拉回的快照按 appendUnknownMessages 上屏（新 id 追加，不改既有消息）", async () => {
    H.snap = { items: [msg({ id: "m1", role: "user", text: "需求" }), msg({ id: "m2", image: "a.png" })] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    await advance(2000);
    expect(d.state.messages.map((m) => m.id)).toEqual(["m1", "m2"]);

    // 第二轮：既有 id 不重复追加；新 id 追加
    H.snap = { items: [msg({ id: "m1", role: "user", text: "需求" }), msg({ id: "m3" })] };
    await advance(2000);
    expect(d.state.messages.map((m) => m.id)).toEqual(["m1", "m2", "m3"]);
    await h.unmount();
  });

  it("墓碑过滤：已删除消息不得被快照回灌复活", async () => {
    deletedMessageTombstones.record("r1", "m2");
    H.snap = { items: [msg({ id: "m1" }), msg({ id: "m2" })] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    await advance(2000);
    expect(d.state.messages.map((m) => m.id)).toEqual(["m1"]);
    await h.unmount();
  });

  it("拉快照**挂起期间**切走线程 → 回来的那批快照不得污染当前线程（tick 中段早退）", async () => {
    // 这条覆盖的是 tick 里 `if (!alive || activeThreadRef.current !== threadId) return;`：
    // fetch 是 await，期间用户切走时旧快照晚到会把**别的线程**的消息塞进当前视图。
    H.snap = { items: [msg({ id: "m-late" })] };
    H.hold = true;
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    await advance(2000);
    expect(H.fetches).toBe(1);                    // tick 已进入、快照挂起

    d.deps.activeThreadRef.current = "r2";        // 挂起期间切走
    H.releases.shift()!();                        // 放行快照
    await act(async () => { await Promise.resolve(); });
    expect(d.state.messages).toEqual([]);         // 晚到的快照被丢掉
    await h.unmount();
  });

  it("本轮新到 assistant 媒体 → 派发 laf-generation-saved；无新媒体则不派发", async () => {
    const spy = vi.fn();
    window.addEventListener("laf-generation-saved", spy);
    H.snap = { items: [msg({ id: "m1", image: "a.png" })] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    await advance(2000);
    expect(spy).toHaveBeenCalledTimes(1);
    expect((spy.mock.calls[0][0] as CustomEvent).detail).toBe("r1");

    // 同一条（seenIds 已记）+ 新的非媒体消息 ⇒ 本轮无 freshMedia ⇒ 不再派发
    H.snap = { items: [msg({ id: "m1", image: "a.png" }), msg({ id: "m2", text: "正文" })] };
    await advance(2000);
    expect(spy).toHaveBeenCalledTimes(1);

    window.removeEventListener("laf-generation-saved", spy);
    await h.unmount();
  });

  it("卸载后既解绑订阅也清掉未触发的轮询 timer", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await fire([act1("t1", "r1", "running")]);
    await h.unmount();
    await advance(ADVANCE_ALL);
    expect(H.fetches).toBe(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("等价矩阵：轮询策略 ≡ 改造前 listener 手写判定（逐组相等）", () => {
  it("10 组 activities（本会话/别会话 × 各状态）上的 settleLeft 取值与轮询轮数", async () => {
    const cases: Array<{ name: string; items: Act[] }> = [
      { name: "空列表", items: [] },
      { name: "只有别会话任务", items: [act1("t-x", "r2", "running")] },
      { name: "本会话 queued", items: [act1("t1", "r1", "queued")] },
      { name: "本会话 running", items: [act1("t1", "r1", "running")] },
      { name: "本会话 done", items: [act1("t1", "r1", "done")] },
      { name: "本会话 cancelled", items: [act1("t1", "r1", "cancelled")] },
      { name: "本会话 blocked", items: [act1("t1", "r1", "blocked")] },
      { name: "本会话 error + partial", items: [act1("t1", "r1", "error"), act1("t2", "r1", "partial")] },
      { name: "本会话 done + running（running 优先）", items: [act1("t1", "r1", "done"), act1("t2", "r1", "running")] },
      { name: "别会话 running + 本会话 done", items: [act1("t-x", "r2", "running"), act1("t1", "r1", "done")] },
    ];
    let checked = 0;
    for (const c of cases) {
      const expectedRounds = legacySettleLeft(c.items, "r1");
      H.fetches = 0;                       // ⚠ 逐组重置：本 it 内 10 组共享同一计数器，不重置会累积
      const d = makeDeps();
      const h = await mount(d.deps);
      await fire(c.items);
      await advance(ADVANCE_ALL);
      expect(H.fetches, `${c.name} ⇒ 轮数应等于 legacySettleLeft=${expectedRounds}`)
        .toBe(expectedRounds);
      await h.unmount();
      checked += 1;
    }
    expect(checked).toBe(10);
    // 防空转：网格必须真的踩到三个分支（0 / 6 / 60）
    const branches = new Set(cases.map((c) => legacySettleLeft(c.items, "r1")));
    expect([...branches].sort((a, b) => a - b)).toEqual([0, 6, 60]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("源码形态锁：搬移逐字 + 视图不再自持订阅", () => {
  const SEAM = "./usePlanTaskFeed.ts";
  const VIEW = "./useChatSession.ts";

  const read = (rel: string) =>
    readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const stripComments = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, "").split("\n")
      .map((l) => l.replace(/(?<!:)\/\/.*$/, "")).join("\n");
  const strip = (rel: string) => stripComments(read(rel));
  const count = (hay: string, needle: string): number => {
    let n = 0;
    for (let i = hay.indexOf(needle); i !== -1; i = hay.indexOf(needle, i + needle.length)) n += 1;
    return n;
  };

  it("订阅点在新属主恰一次；视图已无订阅与轮询状态名", () => {
    const seam = strip(SEAM);
    expect(count(seam, "subscribePlanTaskActivities(")).toBe(1);
    expect(seam).toMatch(/const unsub = subscribePlanTaskActivities\(\(activities\) => \{/);

    const view = strip(VIEW);
    expect(count(view, "subscribePlanTaskActivities")).toBe(0);
    expect(view).not.toMatch(/settleLeft/);
    expect(view).not.toMatch(/pickFreshAssistantMedia/);
    expect(view).not.toMatch(/appendUnknownMessages/);
    expect(view).not.toMatch(/scheduleNext/);
  });

  it("依赖数组仍是 [threadId]（重订阅时机＝本接缝的行为面）", () => {
    const seam = strip(SEAM);
    expect(count(seam, "useEffect(() => {")).toBe(1);
    expect(count(seam, "}, [threadId]);")).toBe(1);
    expect(seam).toMatch(/const RUNNING = new Set\(\["queued", "running"\]\);/);
    expect(seam).toMatch(/const ENDED = new Set\(\["done", "partial", "error", "cancelled", "blocked"\]\);/);
  });

  it("搬移语句逐字保留（轮询节奏 / 早退判据 / 事件派发）", () => {
    const seam = strip(SEAM);
    // ⚠ 不放 `if (threadId === "home") return;`：子串式 needle 会撞上视图里 **既有** 的
    //   `enqueue` 首行同句（那也是原样保留的既有代码，不是本接缝）。「是否还在自持轮询」
    //   由下一条用例的状态名缺席断言负责。
    const must = [
      "if (recoveryActiveRef.current || agentBusyRef.current || activeThreadRef.current !== threadId) {",
      "freshMedia = pickFreshAssistantMedia(items, seenIds);",
      "setMessages((current) => appendUnknownMessages(current, items));",
      "if (alive && settleLeft > 0) { settleLeft -= 1; scheduleNext(); }",
      "timer = setTimeout(() => { void tick(); }, 2000);",
      "settleLeft = running ? 60 : 6;",
      "return () => { alive = false; if (timer) clearTimeout(timer); unsub(); };",
    ];
    for (const line of must) {
      expect(count(seam, line), line).toBe(1);
      expect(count(strip(VIEW), line), `视图不应再自持：${line}`).toBe(0);
    }
    // ⚠ 事件派发**不能**要求视图缺席：`laf-generation-saved` 在视图里另有 3 处**既有合法**派发点
    //   （startAgentRecovery 的 `detail: targetThread`、finalizeGeneration 两处 `detail: owner.threadId`、
    //   流事件桥的 `detail: threadId`）⇒ 只钉「新属主恰一次」。
    expect(count(seam, 'window.dispatchEvent(new CustomEvent("laf-generation-saved", { detail: threadId }));')).toBe(1);
    // 墓碑过滤是跨行调用，单独按整块钉
    expect(seam).toMatch(/const items = deletedMessageTombstones\.filterDeleted\(\s*\n\s*threadId, \(snap\.items \|\| \[\]\) as ChatMessage\[\],\s*\n\s*\);/);
  });

  it("视图恰好调用一次，且调用点仍在原 effect 位置（hook 调用序 = effect 执行序）", () => {
    const view = strip(VIEW);
    expect(count(view, "usePlanTaskFeed({")).toBe(1);
    expect(view).toMatch(/usePlanTaskFeed\(\{ threadId, recoveryActiveRef, agentBusyRef, activeThreadRef, setMessages \}\);/);

    // ⚠ `// APPEND_HERE` 是**整行注释**，`stripComments` 会把它抹掉 ⇒ 在 `strip(VIEW)` 上
    //   `indexOf` 得 -1，下界断言 `at > -1` 恒真（**假通过 / 防空失效**，T2.2-B2 同款坑）。
    //   位置判据一律落在**未去注释**的原文上，并先断锚点确实存在。
    const raw = read(VIEW);
    const anchorAt = raw.indexOf("// APPEND_HERE");
    expect(anchorAt).toBeGreaterThan(0);
    const at = raw.indexOf("usePlanTaskFeed({");
    expect(at).toBeGreaterThan(anchorAt);
    // ⚠ 上界锚点同样必须先断存在：T2.2-B4-b 把持久化链搬走后 `const persistDataUri` 已不在视图里，
    //   直接拿它的 `indexOf` 当上界会得 -1，使这条断言**恒真**（防空失效 —— 本用例注释里早就警告过）。
    const iPersist = raw.indexOf("useSessionPersist({");
    expect(iPersist).toBeGreaterThan(0);
    expect(at).toBeLessThan(iPersist);
  });

  it("视图行数上界（本步只减不增：<= 2000 行）", () => {
    const raw = read(VIEW);
    expect(raw.split("\n").length).toBeLessThanOrEqual(2000);
  });
});
