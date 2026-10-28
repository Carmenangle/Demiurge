// @vitest-environment jsdom
//
// useWorkflowResume 的特征网（T2.2-B2 · 2026-09-17）
//
// 背景：`lib/useChatSession.ts` 里「进入仓库/切回时恢复进行中的生图任务」的 useEffect 改造前是
// **36 行内联体**（注释 1 + `resumedRef` 1 + effect 34），已**逐字节搬移**到 `lib/useWorkflowResume.ts`。
// 该 hook 没有组件测试 ⇒ 三道网分工：
//
//   ① 行为网（describe「真恢复派发」）—— jsdom + React 19 `act` 真挂载 Host（只调用 hook），
//      真跑 fake timer 驱动的 40×100ms 等加载，断言**经过 hook 之后**可观测的那一面
//      （读队列 / inspect / 归档失败槽 / P22 出队 / 卸载闸 / 重订阅时机）。
//   ② 等价矩阵（describe「等价矩阵」）—— 把改造前 effect 里「action × outcome × 落点 → 副作用」
//      的分派**逐字抄**成 `legacySignature` 参照，在 9 组网格上断言实际副作用签名 ≡ 参照。
//   ③ 源码形态锁（describe「源码形态锁」）—— 行为网**抓不到分层回退**（hook 被删后内联回视图照样
//      可能全绿），故另钉：搬移语句逐字在新属主且视图缺席、依赖数组那一行与禁用注释、
//      视图「恰好调用一次」且调用位置在 `cancelForegroundRun` / `discardFailedIllustration` 定义之后。
//
// ⚠ 硬要求（承 T2.1/T2.2-A 教训）：
//   ① 用例隔离：`afterEach` 按登记表统一卸载（残留 root 会在下一用例里继续跑 effect）；
//   ② 等价矩阵逐组重置计数器（同 it 内多组共享 `H.calls`，不重置会累积）；
//   ③ 形态锁必须**去注释**（否则注释里提一句 `resumedRef` 就算「还在」）；
//   ④ 源码只能在 `it` 回调内读（jsdom 模块求值期 `import.meta.url` 不是 `file:` 协议）。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { act, createElement, type MutableRefObject } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkflowGenerationRuntime, WorkflowWatchObserver } from "./workflowGenerationRuntime";
import { useWorkflowResume, type WorkflowResumeDeps } from "./useWorkflowResume";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ─────────────────────────────────────────────────────────────────────────────
// 外部依赖替身：恢复运行时（可编排 action/outcome）+ 出图地址推导
// ─────────────────────────────────────────────────────────────────────────────
type Pend = {
  prompt_id: string;
  regeneration?: unknown;
  target?: { messageId: string; slotId: string };
};

const H = vi.hoisted(() => ({
  /** 下一次 `workflowRuntime.list()` 返回的在途任务 */
  list: [] as Array<Record<string, unknown>>,
  /** 逐 prompt_id 编排 `recoveryAction` 的返回值（缺省 "inspect"） */
  actions: {} as Record<string, "skip" | "expire" | "inspect">,
  /** 逐 prompt_id 编排 `inspect` 的 outcome（缺省 "watching"） */
  outcomes: {} as Record<string, string>,
  /** `comfyRegenerationUrl` 的映射表（缺省 null ⇒ 回落 settings.comfyuiUrl） */
  urls: {} as Record<string, string | null>,
  /** 全量调用日志：list / recoveryAction:<pid> / inspect:<pid>@<url> / discard:<pid> / cancel:<pid> */
  calls: [] as string[],
  /** 每次 `recoveryAction` 看到的 resumedRef 快照（钉「登记时机」与「跨重订阅保留」） */
  seen: [] as Array<{ pid: string; ids: string[] }>,
  /** true = `inspect` 挂起，等 `releases` 放行（复刻「慢 history 查询」） */
  inspectHold: false,
  releases: [] as Array<() => void>,
}));

vi.mock("./regeneration", () => ({
  comfyRegenerationUrl: (r: unknown) => (r == null ? null : (H.urls[String(r)] ?? null)),
}));

// ─────────────────────────────────────────────────────────────────────────────
// 改造前写法的**逐字抄录**（唯一等价性参照，不得顺手优化）
// ─────────────────────────────────────────────────────────────────────────────
/**
 * 改造前 effect 循环体里那段分派，逐字抄录成纯函数：
 * `skip` 直接跳过；`expire` 先查 history（`watching` 才算真在跑），
 * 有落点才进失败槽，**出队无条件**；其余（`inspect`）只查一次。
 */
const legacySignature = (
  action: "skip" | "expire" | "inspect",
  outcome: string,
  hasTarget: boolean,
): string => {
  if (action === "skip") return "none";
  if (action === "expire") {
    if (outcome !== "watching") return "none";
    return hasTarget ? "discard+cancel" : "cancel";
  }
  return "none";
};

/** 从调用日志反推实际副作用签名（与参照同构）。 */
const actualSignature = (calls: string[], pid: string): string => {
  const discarded = calls.includes(`discard:${pid}`);
  const cancelled = calls.includes(`cancel:${pid}`);
  if (discarded && cancelled) return "discard+cancel";
  if (cancelled) return "cancel";
  return "none";
};

// ─────────────────────────────────────────────────────────────────────────────
// 挂载脚手架：真实 Host 组件（只调用 hook）+ fake timer
// ─────────────────────────────────────────────────────────────────────────────
const teardowns: Array<() => Promise<void>> = [];
async function teardownAll() {
  while (teardowns.length > 0) {
    const t = teardowns.pop()!;
    await t();
  }
}

function makeDeps(over: Partial<WorkflowResumeDeps> = {}) {
  const loadedRef = { current: true };
  const discard = vi.fn((...a: unknown[]) => { H.calls.push(`discard:${String(a[4])}`); });
  const cancel = vi.fn((pid: string) => { H.calls.push(`cancel:${pid}`); });
  const runtime = {
    list: () => {
      H.calls.push("list");
      return H.list;
    },
    recoveryAction: (p: Pend, resumed: ReadonlySet<string>) => {
      H.calls.push(`recoveryAction:${p.prompt_id}`);
      H.seen.push({ pid: p.prompt_id, ids: [...resumed] });
      return H.actions[p.prompt_id] ?? "inspect";
    },
    inspect: async (p: Pend, url: string) => {
      H.calls.push(`inspect:${p.prompt_id}@${url}`);
      if (H.inspectHold) {
        // 挂起点：由用例显式放行 —— 覆盖「history 查询期间卸载」这条真实失败路径
        await new Promise<void>((resolve) => { H.releases.push(resolve); });
      }
      return H.outcomes[p.prompt_id] ?? "watching";
    },
  } as unknown as WorkflowGenerationRuntime;
  const deps: WorkflowResumeDeps = {
    threadId: "r1",
    loadedRef: loadedRef as MutableRefObject<boolean>,
    workflowRuntime: runtime,
    workflowObserver: {} as unknown as WorkflowWatchObserver,
    settings: { comfyuiUrl: "http://s" } as unknown as WorkflowResumeDeps["settings"],
    discardFailedIllustration: discard as unknown as WorkflowResumeDeps["discardFailedIllustration"],
    cancelForegroundRun: cancel as unknown as WorkflowResumeDeps["cancelForegroundRun"],
    ...over,
  };
  return { deps, loadedRef, discard, cancel };
}

function Host({ deps }: { deps: WorkflowResumeDeps }) {
  useWorkflowResume(deps);
  return null;
}

async function mount(deps: WorkflowResumeDeps) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Host, { deps, key: "host" })); });
  const rerender = async (next: WorkflowResumeDeps) => {
    await act(async () => { root.render(createElement(Host, { deps: next, key: "host" })); });
  };
  const unmount = async () => {
    await act(async () => { root.unmount(); });
    host.remove();
  };
  teardowns.push(unmount);
  return { rerender, unmount };
}

/** 推进虚拟时间（`advanceTimersByTimeAsync` 会在 timer 之间 flush 微任务 ⇒ await 链能跑完）。 */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** 只清微任务（不推进 timer）：用于让已 `setTimeout(0)` / 已 resolve 的 await 链走完。 */
async function flush(rounds = 12) {
  await act(async () => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  });
}

const WAIT_ALL = 100 * 40;   // 40 轮 × 100ms：等加载的上限

beforeEach(() => {
  vi.useFakeTimers();
  H.list = [];
  H.actions = {};
  H.outcomes = {};
  H.urls = {};
  H.calls = [];
  H.seen = [];
  H.inspectHold = false;
  H.releases = [];
});

afterEach(async () => {
  await teardownAll();
  vi.useRealTimers();
  vi.clearAllMocks();
  H.calls = [];
  H.seen = [];
});

// ─────────────────────────────────────────────────────────────────────────────
describe("真恢复派发（jsdom：挂载 Host + fake timer 推进等加载）", () => {
  it("加载未就绪时先等：就绪前的等待期不读在途队列", async () => {
    const d = makeDeps();
    d.loadedRef.current = false;
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toEqual([]);              // 仍在等加载 ⇒ 一次队列都没读

    await advance(1000);                      // 10 轮过去，依旧没就绪
    expect(H.calls).toEqual([]);

    d.loadedRef.current = true;               // 下一轮条件失败 ⇒ 跳出等待
    await advance(100);
    expect(H.calls).toEqual(["list"]);
    await h.unmount();
  });

  it("等加载有 40 轮上限：长期不就绪也不会无限等（越过上限即继续）", async () => {
    const d = makeDeps();
    d.loadedRef.current = false;
    const h = await mount(d.deps);
    await advance(WAIT_ALL - 100);            // 差最后一轮：仍未跳出
    expect(H.calls).toEqual([]);
    await advance(100);                       // 第 40 轮耗尽 ⇒ 上限生效
    expect(H.calls).toEqual(["list"]);
    await advance(WAIT_ALL * 2);              // 再等多久都不会重读（一次性恢复）
    expect(H.calls.filter((c) => c === "list")).toHaveLength(1);
    await h.unmount();
  });

  it("空队列 ⇒ 只读一次队列，不 inspect / 不归档 / 不出队", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toEqual(["list"]);
    expect(d.discard).not.toHaveBeenCalled();
    expect(d.cancel).not.toHaveBeenCalled();
    await h.unmount();
  });

  it("本会话已处理过（skip）⇒ 不查 history、不出队，且登记发生在查 history 之前", async () => {
    H.list = [{ prompt_id: "p1" }];
    H.actions = { p1: "skip" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toEqual(["list", "recoveryAction:p1"]);
    expect(H.seen).toEqual([{ pid: "p1", ids: [] }]);   // skip 不登记
    await h.unmount();
  });

  it("在途未过期（inspect）⇒ 只查一次 ComfyUI history，不进失败槽、不出队", async () => {
    H.list = [{ prompt_id: "p1" }];
    H.actions = { p1: "inspect" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toEqual(["list", "recoveryAction:p1", "inspect:p1@http://s"]);
    expect(d.discard).not.toHaveBeenCalled();
    expect(d.cancel).not.toHaveBeenCalled();
    await h.unmount();
  });

  it("过期但 history 里仍在跑（watching）且有落点 ⇒ 先查后判：归档进失败槽 + P22 出队", async () => {
    H.list = [{ prompt_id: "p1", target: { messageId: "m1", slotId: "s1" } }];
    H.actions = { p1: "expire" };
    H.outcomes = { p1: "watching" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(d.discard).toHaveBeenCalledTimes(1);
    // 文案与阶段码逐字：改一个字就是改了用户可见提示
    expect(d.discard.mock.calls[0]).toEqual([
      "m1", "s1", "resume_expired", "后台出图任务已过期", "p1",
    ]);
    expect(d.cancel).toHaveBeenCalledWith("p1");
    // 顺序面：必须「先 inspect 拿到终态」→「再归档」→「再出队」
    const i = H.calls.indexOf("inspect:p1@http://s");
    const j = H.calls.indexOf("discard:p1");
    const k = H.calls.indexOf("cancel:p1");
    expect(i).toBeGreaterThanOrEqual(0);
    expect(i).toBeLessThan(j);
    expect(j).toBeLessThan(k);
    await h.unmount();
  });

  it("过期且 history 查不到（not_found）⇒ 不归档、不出队（不凭年龄静默丢弃已完成图的反面）", async () => {
    H.list = [{ prompt_id: "p1", target: { messageId: "m1", slotId: "s1" } }];
    H.actions = { p1: "expire" };
    H.outcomes = { p1: "not_found" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toEqual(["list", "recoveryAction:p1", "inspect:p1@http://s"]);
    expect(d.discard).not.toHaveBeenCalled();
    expect(d.cancel).not.toHaveBeenCalled();
    await h.unmount();
  });

  it("过期 watching 但**没有落点** ⇒ 不进失败槽，但仍**无条件** P22 出队", async () => {
    // 这条钉的是 `cancelForegroundRun` 位于 `if (p.target)` **之外**：
    // 恢复期队列本就为空、出队是幂等空操作，但漏掉它会让「恢复过的 pid」永远留在前台队列判定里。
    H.list = [{ prompt_id: "p1" }];
    H.actions = { p1: "expire" };
    H.outcomes = { p1: "watching" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(d.discard).not.toHaveBeenCalled();
    expect(d.cancel).toHaveBeenCalledWith("p1");
    await h.unmount();
  });

  it("多条任务逐条处理：登记在查 history 之前 ⇒ 下一条已能看到前一条的 id", async () => {
    H.list = [
      { prompt_id: "p1" },
      { prompt_id: "p2" },
      { prompt_id: "p3", target: { messageId: "m3", slotId: "s3" } },
    ];
    H.actions = { p1: "skip", p2: "inspect", p3: "expire" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.seen).toEqual([
      { pid: "p1", ids: [] },            // p1 是 skip ⇒ 不登记
      { pid: "p2", ids: [] },            // p2 登记发生在自己的 recoveryAction 之后
      { pid: "p3", ids: ["p2"] },        // p3 已能看到 p2 的登记
    ]);
    expect(d.cancel).toHaveBeenCalledWith("p3");
    await h.unmount();
  });

  it("出图地址推导：有 regeneration 快照用推导值，缺失才回落 settings.comfyuiUrl", async () => {
    H.list = [{ prompt_id: "p1", regeneration: "regen1" }, { prompt_id: "p2", regeneration: "regen2" }];
    H.actions = { p1: "inspect", p2: "inspect" };
    H.urls = { regen1: "http://regen" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toContain("inspect:p1@http://regen");
    expect(H.calls).toContain("inspect:p2@http://s");
    await h.unmount();
  });

  it("在途任务认自己那台机：pending 固化的 comfyuiUrl 优先于快照与 settings（C 段）", async () => {
    H.list = [
      { prompt_id: "p1", regeneration: "regen1", comfyuiUrl: "http://submitted" },
      { prompt_id: "p2", regeneration: "regen2" },
    ];
    H.actions = { p1: "inspect", p2: "inspect" };
    H.urls = { regen1: "http://regen" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    // 提交那台机优先；旧数据（无该字段）仍按旧口径推导
    expect(H.calls).toContain("inspect:p1@http://submitted");
    expect(H.calls).toContain("inspect:p2@http://s");
    await h.unmount();
  });

  it("卸载闸①：等加载期间卸载 ⇒ 放行后不再读队列", async () => {
    const d = makeDeps();
    d.loadedRef.current = false;
    const h = await mount(d.deps);
    await h.unmount();
    await advance(WAIT_ALL * 2);
    expect(H.calls).toEqual([]);
  });

  it("卸载闸②：history 查询挂起期间卸载 ⇒ 晚到的 outcome 不得再归档/出队", async () => {
    H.list = [{ prompt_id: "p1", target: { messageId: "m1", slotId: "s1" } }];
    H.actions = { p1: "expire" };
    H.inspectHold = true;
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toContain("inspect:p1@http://s");   // 已进入查询、尚未回来

    await h.unmount();                                  // 挂起期间卸载
    H.releases.shift()!();                              // 放行晚到的查询结果
    await flush();
    expect(d.discard).not.toHaveBeenCalled();
    expect(d.cancel).not.toHaveBeenCalled();
  });

  it("红线①：依赖数组只有 threadId ⇒ 换 settings / observer 对象身份不重跑，换线程才重跑", async () => {
    // 这条是「重订阅时机＝行为面」的行为侧证据：observer / settings 每渲染都是新对象，
    // 一旦补进依赖数组，恢复链会在每次渲染重跑（重复 inspect、重复登记）。
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls.filter((c) => c === "list")).toHaveLength(1);

    await h.rerender({
      ...d.deps,
      settings: { comfyuiUrl: "http://s" } as unknown as WorkflowResumeDeps["settings"],
      workflowObserver: {} as unknown as WorkflowWatchObserver,
    });
    await flush();
    expect(H.calls.filter((c) => c === "list")).toHaveLength(1);   // 新对象 ⇒ 不重订阅

    await h.rerender({ ...d.deps, threadId: "r2" });
    await flush();
    expect(H.calls.filter((c) => c === "list")).toHaveLength(2);   // 切线程 ⇒ 重订阅
    await h.unmount();
  });

  it("红线②：resumedRef 跨 threadId 重订阅保留 ⇒ 同一 prompt_id 不会被二次处理", async () => {
    H.list = [{ prompt_id: "p1" }];
    H.actions = { p1: "inspect" };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.seen).toEqual([{ pid: "p1", ids: [] }]);

    await h.rerender({ ...d.deps, threadId: "r2" });
    await flush();
    expect(H.seen).toHaveLength(2);
    // ⚠ 关键：第二次仍看到第一次登记的 p1 ⇒ 去重集合是**同一 ref 实例**（未随重订阅重建）
    expect(H.seen[1]).toEqual({ pid: "p1", ids: ["p1"] });
    await h.unmount();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("等价矩阵：分派 ≡ 改造前 effect 手写判定（逐组相等）", () => {
  it("9 组（action × outcome × 落点）上的副作用签名", async () => {
    const cases: Array<{
      name: string;
      action: "skip" | "expire" | "inspect";
      outcome: string;
      hasTarget: boolean;
    }> = [
      { name: "skip / watching / 有落点", action: "skip", outcome: "watching", hasTarget: true },
      { name: "skip / not_found / 无落点", action: "skip", outcome: "not_found", hasTarget: false },
      { name: "expire / watching / 有落点", action: "expire", outcome: "watching", hasTarget: true },
      { name: "expire / watching / 无落点", action: "expire", outcome: "watching", hasTarget: false },
      { name: "expire / not_found / 有落点", action: "expire", outcome: "not_found", hasTarget: true },
      { name: "expire / not_found / 无落点", action: "expire", outcome: "not_found", hasTarget: false },
      { name: "inspect / watching / 有落点", action: "inspect", outcome: "watching", hasTarget: true },
      { name: "inspect / watching / 无落点", action: "inspect", outcome: "watching", hasTarget: false },
      { name: "inspect / not_found / 有落点", action: "inspect", outcome: "not_found", hasTarget: true },
    ];
    let checked = 0;
    for (const c of cases) {
      H.calls = [];                        // ⚠ 逐组重置：本 it 内 9 组共享同一日志，不重置会累积
      H.seen = [];
      H.list = [{
        prompt_id: "p1",
        ...(c.hasTarget ? { target: { messageId: "m1", slotId: "s1" } } : {}),
      }];
      H.actions = { p1: c.action };
      H.outcomes = { p1: c.outcome };
      const d = makeDeps();
      const h = await mount(d.deps);
      await flush();

      const expected = legacySignature(c.action, c.outcome, c.hasTarget);
      expect(actualSignature(H.calls, "p1"), `${c.name} ⇒ 应等于 legacySignature=${expected}`)
        .toBe(expected);
      await h.unmount();
      checked += 1;
    }
    expect(checked).toBe(9);
    // 防空转：网格必须真的踩到三个分支（none / cancel / discard+cancel）
    const branches = new Set(cases.map((c) => legacySignature(c.action, c.outcome, c.hasTarget)));
    expect([...branches].sort()).toEqual(["cancel", "discard+cancel", "none"]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("源码形态锁：搬移逐字 + 视图不再自持恢复链", () => {
  const SEAM = "./useWorkflowResume.ts";
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

  it("恢复链在新属主恰一份；视图已无该 effect 的任何符号", () => {
    const seam = strip(SEAM);
    expect(count(seam, "workflowRuntime.recoveryAction(")).toBe(1);
    expect(count(seam, "useEffect(() => {")).toBe(1);
    expect(count(seam, "resumedRef.current.add(p.prompt_id);")).toBe(1);

    const view = strip(VIEW);
    expect(count(view, "recoveryAction")).toBe(0);
    expect(count(view, "resumedRef")).toBe(0);
    expect(count(view, "resume_expired")).toBe(0);
    expect(count(view, '"后台出图任务已过期"')).toBe(0);
    expect(count(view, 'outcome !== "watching"')).toBe(0);
  });

  it("依赖数组仍是 [threadId]，且 react-hooks/exhaustive-deps 禁用注释仍在", () => {
    const seam = strip(SEAM);
    expect(count(seam, "}, [threadId]);")).toBe(1);
    expect(count(seam, "workflowObserver")).toBeGreaterThan(0);
    // 该行是注释 ⇒ 必须在**未去注释**的原文里断
    const raw = read(SEAM);
    expect(count(raw, "// eslint-disable-next-line react-hooks/exhaustive-deps")).toBe(1);
    const at = raw.indexOf("// eslint-disable-next-line react-hooks/exhaustive-deps");
    expect(raw.slice(at)).toMatch(/^\/\/ eslint-disable-next-line react-hooks\/exhaustive-deps\n\s+\}, \[threadId\]\);/);
  });

  it("搬移语句逐字保留（等加载节奏 / 登记时机 / 先查后判 / P22 出队）", () => {
    const seam = strip(SEAM);
    const must = [
      "for (let i = 0; i < 40 && !loadedRef.current; i++) await new Promise((r) => setTimeout(r, 100));",
      "const action = workflowRuntime.recoveryAction(p, resumedRef.current);",
      "if (action === \"skip\") continue;",
      "resumedRef.current.add(p.prompt_id);",
      "const comfyuiUrl = p.comfyuiUrl || comfyRegenerationUrl(p.regeneration) || settings.comfyuiUrl;",
      "const outcome = await workflowRuntime.inspect(p, comfyuiUrl, workflowObserver);",
      "if (outcome !== \"watching\") continue;",
      "cancelForegroundRun(p.prompt_id);",
      "return () => { alive = false; };",
      "  const resumedRef = useRef<Set<string>>(new Set());",
    ];
    for (const line of must) {
      expect(count(seam, line), line).toBe(1);
      expect(count(strip(VIEW), line), `视图不应再自持：${line}`).toBe(0);
    }
    // ⚠ `if (!alive) return;` **不能**进上面那份「恰一次」列表：它是三个早退闸
    //   （等加载后 / expire 分支 inspect 后 / 其余 inspect 后），且视图里另有大量既有点
    //   ⇒ 既不能要求恰一次，也不能要求视图缺席。
    expect(count(seam, "if (!alive) return;")).toBe(3);
    // 归档调用的实参逐字（messageId/slotId 取自落点，阶段码与文案是用户可见面）
    expect(seam).toMatch(/discardFailedIllustration\(\s*\n\s*p\.target\.messageId, p\.target\.slotId, "resume_expired",\s*\n\s*"后台出图任务已过期", p\.prompt_id,\s*\n\s*\);/);
  });

  it("视图恰好调用一次，且调用点在两个被注入的闭包符号定义之后（TDZ / 调用序）", () => {
    // ⚠ `// APPEND4_HERE` 是**整行注释**，`stripComments` 会把它抹掉（`indexOf` 得 -1）⇒
    //   位置判据必须在**未去注释**的原文上算；且必须先断 `appendAt > 0`，否则「找不到」
    //   会让上界断言 `at < -1` 假通过（B1 同款判据就踩了这个坑）。
    const raw = read(VIEW);
    const view = stripComments(raw);
    expect(count(view, "useWorkflowResume({")).toBe(1);
    expect(view).toMatch(/useWorkflowResume\(\{\s*\n\s*threadId, loadedRef, workflowRuntime, workflowObserver, settings,\s*\n\s*discardFailedIllustration, cancelForegroundRun,\s*\n\s*\}\);/);

    const at = raw.indexOf("useWorkflowResume({");
    expect(at).toBeGreaterThan(raw.indexOf("const cancelForegroundRun = (promptId: string): void => {"));
    expect(at).toBeGreaterThan(raw.indexOf("const discardFailedIllustration = ("));
    const appendAt = raw.indexOf("// APPEND4_HERE");
    expect(appendAt).toBeGreaterThan(0);
    expect(at).toBeLessThan(appendAt);
  });

  it("视图行数上界（本步只减不增：<= 1950 行）", () => {
    const raw = read(VIEW);
    expect(raw.split("\n").length).toBeLessThanOrEqual(1950);
  });
});
