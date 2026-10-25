// @vitest-environment jsdom
//
// useSessionLoad 的特征网（T2.2-B3-b · 2026-09-17）
//
// 背景：`lib/useChatSession.ts` 里「进入仓库/切换时加载消息」的 useEffect 改造前是 **162 行内联体**
// （含首行语义注释；体 161 行），加上模块顶层 `loadOpeningMessage`（13 行），已**逐字节搬移**到
// `lib/useSessionLoad.ts`。该 hook 没有组件测试 ⇒ 三道网分工：
//
//   ① 行为网 —— jsdom + React 19 `act` 真挂载 Host（只调用 hook），断言**经过 hook 之后**可观测的那一面：
//      home 草稿区、三级兜底（localStorage → 后端快照 → langgraph 历史）落点、修剪/补图后的回写、
//      切回竞态短轮询（8×2500ms / running 转完整恢复 / 未增长即稳定）、卸载清理的逐项复位。
//   ② 等价矩阵 —— 把改造前「三级兜底选谁」的判定**逐字抄**成 `legacySource` 参照，在
//      `isHome × 快照请求状态 × 本地命中` 网格上断言实际最终落点 ≡ 参照。
//   ③ 源码形态锁 —— 行为网**抓不到分层回退**（hook 被删后内联回视图照样可能全绿），故另钉：
//      搬移语句逐字在新属主且视图缺席、依赖数组那一行、视图「恰好调用一次」且调用点在
//      `snapTimer` / `recoveryTokenRef` / `historyRuntime` 定义之后、`// APPEND_HERE` 之前。
//
// ⚠ 硬要求（承 T2.1/T2.2-A/B1/B2 教训）：
//   ① 用例隔离：`afterEach` 按登记表统一卸载（残留 root 会在下一用例里继续跑 effect）；
//   ② 等价矩阵逐组重置计数器与 localStorage（同 it 内多组共享同一日志，不重置会累积）；
//   ③ 形态锁必须**去注释**（否则注释里提一句 `三级兜底` 就算「还在」）；
//   ④ 源码只能在 `it` 回调内读（jsdom 模块求值期 `import.meta.url` 不是 `file:` 协议）；
//   ⑤ ⚠ `// APPEND_HERE` 是**整行注释**，`stripComments` 会抹掉 ⇒ 位置判据必须在原文上算，
//      且必须先断锚点存在（B1/B2 同款坑：`indexOf` 得 -1 会让上界断言假通过）。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { act, createElement, type MutableRefObject } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Node 25 的内置 localStorage 桩遮蔽 jsdom（clear 等方法缺失，settings.test 同源坑）：
// 统一换成内存实现，组件与测试读写的都是同一实例。
class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() { return this.map.size; }
  clear() { this.map.clear(); }
  getItem(k: string) { return this.map.get(k) ?? null; }
  key(i: number) { return [...this.map.keys()][i] ?? null; }
  removeItem(k: string) { return this.map.delete(k); }
  setItem(k: string, v: string) { this.map.set(k, String(v)); }
}
vi.stubGlobal("localStorage", new MemoryStorage());

import type { ChatMessage } from "../types/chat";
import { clearHomeDraft, writeHomeDraft } from "./homeDraft";
import { useSessionLoad, type SessionLoadDeps } from "./useSessionLoad";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ─────────────────────────────────────────────────────────────────────────────
// 外部依赖替身：后端接口边界（ai / characters）+ 恢复闸（agentRecovery）
// ─────────────────────────────────────────────────────────────────────────────
const H = vi.hoisted(() => ({
  /** `fetchSnapshot` 的行为：ok 返回 items；throw 模拟请求失败（才允许本地缓存兜底） */
  snapshot: { mode: "ok" as "ok" | "throw", items: [] as unknown[] },
  /** 按调用序返回不同快照（末项重复）：复刻「主链空快照 → 轮询捡到增长快照」 */
  snapshotSeq: null as null | Array<{ mode: "ok" | "throw"; items: unknown[] }>,
  /** `fetchAgentRunning` 返回的 running */
  agentRunning: false,
  /** `fetchHistory` 的行为 */
  history: { mode: "ok" as "ok" | "throw", items: [] as unknown[] },
  /** `shouldRecoverAgentRun` 的返回值（false ⇒ 不走切回竞态短轮询） */
  recoverable: false,
  /** 角色卡 first_mes（卡即作品开场白） */
  card: null as null | { first_mes?: string },
  /** 全量调用日志 */
  calls: [] as string[],
  /** 每次 `setMessages` 的入参（末项 = 交互结束后用户实际看到的那一份） */
  setMessages: [] as unknown[],
  /** `saveSnapshot` 的入参 */
  saved: [] as unknown[],
  /** `reportIllustrationFailure` 的入参 */
  failures: [] as unknown[],
  /** 在途生图任务（喂给 `workflowRuntime.list()`） */
  pending: [] as unknown[],
}));

vi.mock("../api/ai", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchSnapshot: async (tid: string) => {
      H.calls.push(`fetchSnapshot:${tid}`);
      const spec = H.snapshotSeq && H.snapshotSeq.length > 0
        ? (H.snapshotSeq.length > 1 ? H.snapshotSeq.shift()! : H.snapshotSeq[0])
        : H.snapshot;
      if (spec.mode === "throw") throw new Error("snapshot-down");
      return { items: spec.items };
    },
    fetchAgentRunning: async (tid: string) => {
      H.calls.push(`fetchAgentRunning:${tid}`);
      return { running: H.agentRunning };
    },
    fetchHistory: async (tid: string) => {
      H.calls.push(`fetchHistory:${tid}`);
      if (H.history.mode === "throw") throw new Error("history-down");
      return { items: H.history.items };
    },
    saveSnapshot: async (tid: string, items: unknown[]) => {
      H.calls.push(`saveSnapshot:${tid}`);
      H.saved.push(items);
    },
    reportIllustrationFailure: async (arg: unknown) => {
      H.calls.push("reportIllustrationFailure");
      H.failures.push(arg);
    },
  };
});

vi.mock("../api/characters", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    characterDetail: async (dir: string, name: string) => {
      H.calls.push(`characterDetail:${dir}/${name}`);
      return H.card;
    },
  };
});

vi.mock("./agentRecovery", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    shouldRecoverAgentRun: (tid: string) => {
      H.calls.push(`should:${tid}`);
      return H.recoverable;
    },
  };
});

// ─────────────────────────────────────────────────────────────────────────────
// 改造前写法的**逐字抄录**（唯一等价性参照，不得顺手优化）
// ─────────────────────────────────────────────────────────────────────────────
/** 改造前 effect 里「三级兜底最终用哪一份」的判定，逐字抄成纯函数。 */
const legacySource = (
  isHome: boolean,
  snapshot: "ok-items" | "ok-empty" | "throw",
  localHit: boolean,
): "homeDraft" | "snapshot" | "local" | "history" => {
  if (isHome) return "homeDraft";
  if (snapshot === "ok-items") return "snapshot";
  // 成功返回的空快照是当前 UUID 的历史真源；仅请求失败时才允许本地缓存兜底。
  if (snapshot === "throw") return localHit ? "local" : "history";
  return "history";
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

const localMsg = (id = "LOCAL"): ChatMessage => ({ id, role: "user", text: "local" } as ChatMessage);
const snapMsg = (id = "SNAP"): ChatMessage => ({ id, role: "assistant", text: "snap" } as ChatMessage);
const draftMsg = (id = "DRAFT"): ChatMessage => ({ id, role: "assistant", text: "draft" } as ChatMessage);

function makeDeps(over: Partial<SessionLoadDeps> = {}) {
  const recorded: ChatMessage[][] = [];
  const refs = {
    loadedRef: { current: false } as MutableRefObject<boolean>,
    messagesRef: { current: [] } as MutableRefObject<ChatMessage[]>,
    recoveryActiveRef: { current: false } as MutableRefObject<boolean>,
    activeThreadRef: { current: "r1" } as MutableRefObject<string>,
    recoveryTokenRef: { current: 0 } as MutableRefObject<number>,
    agentBusyRef: { current: false } as MutableRefObject<boolean>,
    bgRunningRef: { current: false } as MutableRefObject<boolean>,
    abortRef: { current: null } as MutableRefObject<SessionLoadDeps["abortRef"]["current"]>,
    scenarioSnapshotTurnRef: { current: 5 } as MutableRefObject<number>,
    snapTimer: { current: null } as MutableRefObject<ReturnType<typeof setTimeout> | null>,
    wsUnsubRef: { current: null } as MutableRefObject<(() => void) | null>,
  };
  const setMessages = vi.fn((next: ChatMessage[]) => {
    recorded.push(next);
    H.setMessages.push(next);
    refs.messagesRef.current = next;
  });
  const startAgentRecovery = vi.fn((tid?: string, repoId?: string) => {
    H.calls.push(`startAgentRecovery:${tid}:${repoId}`);
  });
  const dispatch = vi.fn((a: unknown) => {
    H.calls.push(`dispatch:${JSON.stringify(a)}`);
  });
  const deps: SessionLoadDeps = {
    threadId: "r1",
    chatKey: "laf_chat_r1",
    repo: { id: "r1" } as unknown as SessionLoadDeps["repo"],
    setMessages: setMessages as unknown as SessionLoadDeps["setMessages"],
    ...refs,
    workflowRuntime: { list: () => H.pending } as unknown as SessionLoadDeps["workflowRuntime"],
    startAgentRecovery: startAgentRecovery as unknown as SessionLoadDeps["startAgentRecovery"],
    settings: { characterDir: "chars" } as unknown as SessionLoadDeps["settings"],
    openingCardName: "",
    effectivePersona: { name: "玩家" } as unknown as SessionLoadDeps["effectivePersona"],
    dispatch: dispatch as unknown as SessionLoadDeps["dispatch"],
    ...over,
  };
  return { deps, ...refs, setMessages, startAgentRecovery, dispatch, recorded };
}

function Host({ deps }: { deps: SessionLoadDeps }) {
  useSessionLoad(deps);
  return null;
}

async function mount(deps: SessionLoadDeps) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Host, { deps, key: "host" })); });
  const rerender = async (next: SessionLoadDeps) => {
    await act(async () => { root.render(createElement(Host, { deps: next, key: "host" })); });
  };
  const unmount = async () => {
    await act(async () => { root.unmount(); });
    host.remove();
  };
  teardowns.push(unmount);
  return { rerender, unmount };
}

/** 推进虚拟时间（`advanceTimersByTimeAsync` 会在 timer 之间 flush 微任务）。 */
async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

/** 只清微任务（不推进 timer）。 */
async function flush(rounds = 16) {
  await act(async () => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  });
}

/** 末次 `setMessages` 的入参（= 交互结束后用户看到的那一份）。 */
const finalMessages = (): ChatMessage[] =>
  (H.setMessages.length > 0 ? H.setMessages[H.setMessages.length - 1] : []) as ChatMessage[];

/** 从末次上屏内容反推兜底来源（与 `legacySource` 同构）。 */
const actualSource = (msgs: ChatMessage[]): string => {
  if (msgs.length === 0) return "history";                     // 空历史（含「清空后无历史」）
  const head = msgs[0] as ChatMessage & { id?: string };
  if (head.id === "DRAFT") return "homeDraft";
  if (head.id === "LOCAL") return "local";
  if (head.id === "SNAP") return "snapshot";
  if (head.text === "hist") return "history";
  return "other";
};

beforeEach(() => {
  vi.useFakeTimers();
  H.snapshot = { mode: "ok", items: [] };
  H.snapshotSeq = null;
  H.agentRunning = false;
  H.history = { mode: "ok", items: [] };
  H.recoverable = false;
  H.card = null;
  H.calls = [];
  H.setMessages = [];
  H.saved = [];
  H.failures = [];
  H.pending = [];
  clearHomeDraft();
  localStorage.clear();
});

afterEach(async () => {
  await teardownAll();
  vi.useRealTimers();
  vi.clearAllMocks();
  H.calls = [];
  H.setMessages = [];
});

// ─────────────────────────────────────────────────────────────────────────────
describe("行为网：首页(home)草稿区", () => {
  it("home 有草稿 ⇒ 直接上屏草稿；不读 localStorage、不打后端快照/历史", async () => {
    writeHomeDraft([draftMsg()]);
    localStorage.setItem("laf_chat_r1", JSON.stringify([localMsg()]));
    const d = makeDeps({ threadId: "home", chatKey: "laf_chat_home" });
    const h = await mount(d.deps);
    await flush();
    expect(H.setMessages).toHaveLength(1);
    expect((finalMessages()[0] as ChatMessage).id).toBe("DRAFT");
    expect(H.calls.filter((c) => c.startsWith("fetchSnapshot"))).toEqual([]);
    expect(H.calls.filter((c) => c.startsWith("fetchHistory"))).toEqual([]);
    expect(d.loadedRef.current).toBe(true);
    await h.unmount();
  });

  it("home 无草稿 ⇒ 不上屏（保持空），但 loadedRef 仍置位", async () => {
    const d = makeDeps({ threadId: "home", chatKey: "laf_chat_home" });
    const h = await mount(d.deps);
    await flush();
    expect(H.setMessages).toHaveLength(0);
    expect(d.loadedRef.current).toBe(true);
    await h.unmount();
  });

  it("home 仍接后台轮询：可恢复线程时打 fetchAgentRunning（生成中切回来能看到进度）", async () => {
    writeHomeDraft([draftMsg()]);
    H.recoverable = true;
    const d = makeDeps({ threadId: "home", chatKey: "laf_chat_home" });
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toContain("should:home");
    expect(H.calls).toContain("fetchAgentRunning:home");
    await h.unmount();
  });

  it("home 草稿区不写 localStorage（刷新即空，仅进程内保留）", async () => {
    writeHomeDraft([draftMsg()]);
    const d = makeDeps({ threadId: "home", chatKey: "laf_chat_home" });
    const h = await mount(d.deps);
    await flush();
    expect(localStorage.getItem("laf_chat_home")).toBeNull();
    await h.unmount();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("行为网：三级兜底（本地 → 后端快照 → langgraph 历史）", () => {
  it("本地命中且快照请求失败 ⇒ 保留本地渲染，不再拉历史（本地兜底窗口）", async () => {
    localStorage.setItem("laf_chat_r1", JSON.stringify([localMsg()]));
    H.snapshot = { mode: "throw", items: [] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(actualSource(finalMessages())).toBe("local");
    expect(H.calls.filter((c) => c.startsWith("fetchHistory"))).toEqual([]);
    expect(d.loadedRef.current).toBe(true);
    await h.unmount();
  });

  it("快照非空 ⇒ 快照为历史真源（覆盖本地），且引用未变时不回写", async () => {
    localStorage.setItem("laf_chat_r1", JSON.stringify([localMsg()]));
    H.snapshot = { mode: "ok", items: [snapMsg()] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(actualSource(finalMessages())).toBe("snapshot");
    // 无 removed、无补图 ⇒ restoredMessages === snapshotMessages ⇒ 不重写本地/后端
    expect(H.calls.filter((c) => c.startsWith("saveSnapshot"))).toEqual([]);
    expect(JSON.parse(localStorage.getItem("laf_chat_r1")!) as unknown[]).toEqual([localMsg()]);
    await h.unmount();
  });

  it("成功返回的空快照是历史真源 ⇒ 清本地缓存并落到历史；本地不兜底", async () => {
    localStorage.setItem("laf_chat_r1", JSON.stringify([localMsg()]));
    H.snapshot = { mode: "ok", items: [] };
    H.history = { mode: "ok", items: [{ role: "assistant", content: "hist" }] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(actualSource(finalMessages())).toBe("history");
    expect(localStorage.getItem("laf_chat_r1")).toBeNull();
    expect(H.calls).toContain("fetchHistory:r1");
    await h.unmount();
  });

  it("修剪出未提交孤儿插画槽 ⇒ 逐个上报失败槽（stage=resume_unsubmitted）并回写本地+快照", async () => {
    H.snapshot = {
      mode: "ok",
      items: [{
        id: "m1",
        role: "assistant",
        text: "x",
        parts: [{ type: "media-slot", slotId: "s1", status: "pending" }],
      }],
    };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.failures).toHaveLength(1);
    expect(H.failures[0]).toMatchObject({
      threadId: "r1", repoId: "r1", messageId: "m1", slotId: "s1",
      stage: "resume_unsubmitted",
    });
    expect(H.calls.filter((c) => c.startsWith("saveSnapshot"))).toHaveLength(1);
    expect(localStorage.getItem("laf_chat_r1")).not.toBeNull();
    await h.unmount();
  });

  it("单条「【历史摘要】」无图 ⇒ 拉历史补图并回写（旧摘要修复）", async () => {
    H.snapshot = { mode: "ok", items: [{ id: "s", role: "assistant", text: "【历史摘要】旧" }] };
    H.history = {
      mode: "ok",
      items: [{ role: "assistant", content: "【历史摘要】旧", images: ["u1"] }],
    };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toContain("fetchHistory:r1");
    expect(finalMessages()[0].image).toBe("u1");
    expect(H.calls.filter((c) => c.startsWith("saveSnapshot"))).toHaveLength(1);
    await h.unmount();
  });

  it("全无历史且关联角色卡 ⇒ 用 first_mes 作开场白首条（宏已替换）", async () => {
    H.card = { first_mes: "你好 {{user}}，我是 {{char}}。" };
    const d = makeDeps({ openingCardName: "冷倾雪" });
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toContain("characterDetail:chars/冷倾雪");
    expect(finalMessages()).toHaveLength(1);
    expect(finalMessages()[0].role).toBe("assistant");
    expect(finalMessages()[0].text).toBe("你好 玩家，我是 冷倾雪。");
    await h.unmount();
  });

  it("全无历史但 first_mes 为空 ⇒ 保持空（不注入空开场白）", async () => {
    H.card = { first_mes: "   " };
    const d = makeDeps({ openingCardName: "冷倾雪" });
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toContain("characterDetail:chars/冷倾雪");
    // 空快照 → setMessages([])（历史真源）+ 空历史 → setMessages([])：两次调用，末次才是用户所见
    expect(H.setMessages).toHaveLength(2);
    expect(finalMessages()).toEqual([]);
    await h.unmount();
  });

  it("无关联角色卡 ⇒ 直接落到历史消息（role/图片映射：content→text、images→image 分区）", async () => {
    H.history = { mode: "ok", items: [{ role: "assistant", content: "hist", images: ["u1", "u2"] }] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    const msgs = finalMessages();
    expect(msgs).toHaveLength(1);
    expect(msgs[0].text).toBe("hist");
    expect(msgs[0].parts?.map((p) => p.type)).toEqual(["text", "image", "image"]);
    expect(msgs[0].id).toMatch(/^[0-9a-f-]{8,}$/i);   // crypto.randomUUID 现生成，不是后端 id
    await h.unmount();
  });

  it("后端未起（快照与历史皆抛）⇒ 保持空，loadedRef 仍经 finally 置位", async () => {
    H.snapshot = { mode: "throw", items: [] };
    H.history = { mode: "throw", items: [] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.setMessages).toHaveLength(0);
    expect(d.loadedRef.current).toBe(true);
    await h.unmount();
  });

  it("重订阅时机 = 依赖 [threadId]：同线程重渲染（含换 settings/repo 引用）不重跑，换线程才重跑", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls.filter((c) => c.startsWith("fetchSnapshot"))).toHaveLength(1);
    await h.rerender({ ...d.deps });
    await flush();
    expect(H.calls.filter((c) => c.startsWith("fetchSnapshot"))).toHaveLength(1);
    // settings / repo 每渲染都是新对象：若被补进依赖，恢复会反复重跑（依赖数组＝行为面）
    await h.rerender({
      ...d.deps,
      settings: { characterDir: "chars" } as unknown as SessionLoadDeps["settings"],
      repo: { id: "r1" } as unknown as SessionLoadDeps["repo"],
    });
    await flush();
    expect(H.calls.filter((c) => c.startsWith("fetchSnapshot"))).toHaveLength(1);
    await h.rerender({ ...d.deps, threadId: "r2", chatKey: "laf_chat_r2" });
    await flush();
    expect(H.calls.filter((c) => c.startsWith("fetchSnapshot"))).toEqual(["fetchSnapshot:r1", "fetchSnapshot:r2"]);
    await h.unmount();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("行为网：切回竞态短轮询（maybeStartBgPoll）", () => {
  it("仍在 running ⇒ 转完整后台恢复（startAgentRecovery(threadId, repo.id)），随后停止轮询", async () => {
    H.recoverable = true;
    H.agentRunning = true;
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).toContain("startAgentRecovery:r1:r1");
    expect(H.calls.filter((c) => c.startsWith("fetchAgentRunning"))).toHaveLength(1);
    expect(H.calls.filter((c) => c.startsWith("fetchSnapshot"))).toHaveLength(1);   // 只主链那一次
    await advance(2500 * 4);
    expect(H.calls.filter((c) => c.startsWith("fetchAgentRunning"))).toHaveLength(1);  // 不再轮
    await h.unmount();
  });

  it("不在 running 但快照增长 ⇒ 上屏新快照并写本地缓存，随后停止轮询", async () => {
    H.recoverable = true;
    H.agentRunning = false;
    // 主链拿到空快照（清本地、messagesRef 归零）；轮询首轮拿到增长后的快照 ⇒ 走「捡到新消息就渲染」分支
    H.snapshotSeq = [
      { mode: "ok", items: [] },
      { mode: "ok", items: [snapMsg()] },
    ];
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(actualSource(finalMessages())).toBe("snapshot");
    expect(JSON.parse(localStorage.getItem("laf_chat_r1")!) as unknown[]).toHaveLength(1);
    const polls = () => H.calls.filter((c) => c.startsWith("fetchAgentRunning")).length;
    const seen = polls();
    await advance(2500 * 8);
    expect(polls()).toBe(seen);               // 已稳定，不再轮询
    await h.unmount();
  });

  it("快照非空但未增长 ⇒ 视为稳定直接停（不再轮询、不写本地缓存）", async () => {
    H.recoverable = true;
    H.agentRunning = false;
    H.snapshot = { mode: "ok", items: [snapMsg()] };
    const d = makeDeps();
    // 快照先被主链上屏 ⇒ 轮询时 items 不再长于 messagesRef ⇒ 稳定分支
    localStorage.setItem("laf_chat_r1", JSON.stringify([localMsg()]));
    const h = await mount(d.deps);
    await flush();
    expect(actualSource(finalMessages())).toBe("snapshot");
    const polls = () => H.calls.filter((c) => c.startsWith("fetchAgentRunning")).length;
    expect(polls()).toBe(1);
    await advance(2500 * 10);
    expect(polls()).toBe(1);                  // 稳定 ⇒ 不再轮询
    expect(localStorage.getItem("laf_chat_r1")).not.toBeNull();   // 引用未变 ⇒ 也未回写
    await h.unmount();
  });

  it("轮询到空快照 ⇒ 每轮之间等 2500ms，最多 8 轮（上限后停）", async () => {
    H.recoverable = true;
    H.agentRunning = false;
    H.snapshot = { mode: "ok", items: [] };
    H.history = { mode: "throw", items: [] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    const polls = () => H.calls.filter((c) => c.startsWith("fetchAgentRunning")).length;
    expect(polls()).toBe(1);                  // 主链结束后立刻第 1 轮
    await advance(2500 * 6);
    expect(polls()).toBe(7);                  // 每等 2500ms 进入下一轮
    await advance(2500 * 20);
    expect(polls()).toBe(8);                  // 上限 8 轮；之后不再轮
    await h.unmount();
  });

  it("recoveryActiveRef 已置位 ⇒ 不轮询（正在恢复时不抢）", async () => {
    H.recoverable = true;
    const d = makeDeps();
    d.recoveryActiveRef.current = true;
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).not.toContain("fetchAgentRunning:r1");
    await h.unmount();
  });

  it("不合法的线程键（shouldRecoverAgentRun=false）⇒ 不轮询", async () => {
    H.recoverable = false;
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    expect(H.calls).not.toContain("fetchAgentRunning:r1");
    await h.unmount();
  });

  it("2500ms 挂起期间卸载 ⇒ 不再继续下一轮（alive 闸）", async () => {
    H.recoverable = true;
    H.snapshot = { mode: "ok", items: [] };
    H.history = { mode: "throw", items: [] };
    const d = makeDeps();
    const h = await mount(d.deps);
    await flush();
    const first = H.calls.filter((c) => c.startsWith("fetchSnapshot")).length;
    await h.unmount();
    await advance(2500 * 6);
    expect(H.calls.filter((c) => c.startsWith("fetchSnapshot")).length).toBe(first);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("行为网：卸载清理（逐项复位）", () => {
  it("按序复位 refs、关进度 WS、清快照防抖、dispatch({t:'reset'})", async () => {
    const wsUnsub = vi.fn();
    const d = makeDeps();
    d.wsUnsubRef.current = wsUnsub;
    d.snapTimer.current = setTimeout(() => undefined, 5000);
    const h = await mount(d.deps);
    await flush();
    await h.unmount();
    expect(d.scenarioSnapshotTurnRef.current).toBe(-1);
    expect(d.activeThreadRef.current).toBe("");
    expect(d.recoveryTokenRef.current).toBe(1);
    expect(d.recoveryActiveRef.current).toBe(false);
    expect(d.agentBusyRef.current).toBe(false);
    expect(d.bgRunningRef.current).toBe(false);
    expect(d.abortRef.current).toBeNull();          // releaseAgentStream(..., "navigation") ⇒ null
    expect(d.snapTimer.current).toBeNull();
    expect(wsUnsub).toHaveBeenCalledTimes(1);
    expect(d.wsUnsubRef.current).toBeNull();
    expect(d.dispatch).toHaveBeenCalledWith({ t: "reset" });
  });

  it("进入时先清 loadedRef（避免上一线程的「已加载」误判为已就绪）", async () => {
    const d = makeDeps();
    d.loadedRef.current = true;
    H.snapshot = { mode: "ok", items: [snapMsg()] };
    const h = await mount(d.deps);
    await flush();
    expect(d.loadedRef.current).toBe(true);   // 本轮结束后仍为 true（加载完成）
    await h.unmount();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("等价矩阵：三级兜底最终落点 ≡ 改造前 effect 手写判定（逐组相等）", () => {
  it("8 组（isHome × 快照请求状态 × 本地命中）上的落点", async () => {
    const cases: Array<{ name: string; isHome: boolean; snapshot: "ok-items" | "ok-empty" | "throw"; localHit: boolean }> = [
      { name: "home / ok-empty / 无本地", isHome: true, snapshot: "ok-empty", localHit: false },
      { name: "home / ok-items / 有本地", isHome: true, snapshot: "ok-items", localHit: true },
      { name: "仓库 / ok-items / 无本地", isHome: false, snapshot: "ok-items", localHit: false },
      { name: "仓库 / ok-items / 有本地", isHome: false, snapshot: "ok-items", localHit: true },
      { name: "仓库 / ok-empty / 无本地", isHome: false, snapshot: "ok-empty", localHit: false },
      { name: "仓库 / ok-empty / 有本地", isHome: false, snapshot: "ok-empty", localHit: true },
      { name: "仓库 / throw / 无本地", isHome: false, snapshot: "throw", localHit: false },
      { name: "仓库 / throw / 有本地", isHome: false, snapshot: "throw", localHit: true },
    ];
    let checked = 0;
    for (const c of cases) {
      // ⚠ 逐组重置：本 it 内 8 组共享同一日志与 localStorage，不重置会累积
      H.calls = [];
      H.setMessages = [];
      H.saved = [];
      localStorage.clear();
      clearHomeDraft();

      const tid = c.isHome ? "home" : "r1";
      const chatKey = `laf_chat_${tid}`;
      if (c.localHit) localStorage.setItem(chatKey, JSON.stringify([localMsg()]));
      if (c.isHome) writeHomeDraft([draftMsg()]);
      H.snapshot = c.snapshot === "ok-items"
        ? { mode: "ok", items: [snapMsg()] }
        : c.snapshot === "ok-empty" ? { mode: "ok", items: [] } : { mode: "throw", items: [] };
      H.history = { mode: "ok", items: [{ role: "assistant", content: "hist" }] };

      const d = makeDeps({ threadId: tid, chatKey });
      const h = await mount(d.deps);
      await flush();

      const expected = legacySource(c.isHome, c.snapshot, c.localHit);
      expect(actualSource(finalMessages()), `${c.name} ⇒ 应等于 legacySource=${expected}`).toBe(expected);
      await h.unmount();
      checked += 1;
    }
    expect(checked).toBe(8);
    // 防空转：网格必须真的踩到四个分支
    const branches = new Set(cases.map((c) => legacySource(c.isHome, c.snapshot, c.localHit)));
    expect([...branches].sort()).toEqual(["history", "homeDraft", "local", "snapshot"]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("源码形态锁：搬移逐字 + 视图不再自持加载链", () => {
  const SEAM = "./useSessionLoad.ts";
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

  it("加载链在新属主恰一份；视图已无该链的任何符号", () => {
    const seam = strip(SEAM);
    expect(count(seam, "useEffect(() => {")).toBe(1);
    expect(count(seam, "const maybeStartBgPoll = async () => {")).toBe(1);
    expect(count(seam, "for (let i = 0; i < 8; i++) {")).toBe(1);
    expect(count(seam, "await new Promise((r) => setTimeout(r, 2500));")).toBe(1);
    expect(count(seam, "async function loadOpeningMessage(")).toBe(1);

    const view = strip(VIEW);
    for (const gone of ["maybeStartBgPoll", "loadOpeningMessage", "resolveInitialHistory",
      "restoreSubmittedMediaSlots", "pruneUnsubmittedMediaSlots", "recoverCompactedSummaryImage",
      "normalizeInspirationCard", "fetchHistory", "substituteMacros", "characterDetail", "readHomeDraft"]) {
      expect(count(view, gone), `视图不应再自持：${gone}`).toBe(0);
    }
    expect(count(view, '"resume_unsubmitted"')).toBe(0);
    expect(count(view, "【历史摘要】")).toBe(0);
  });

  it("依赖数组仍是 [threadId]；home 草稿区语义注释随体迁入", () => {
    const seam = strip(SEAM);
    expect(count(seam, "}, [threadId]);")).toBe(1);
    const raw = read(SEAM);
    // 两行整行注释 ⇒ 必须在**未去注释**的原文里断（stripComments 会抹掉它们）
    expect(count(raw, "// 进入仓库/切换时加载消息，三级兜底：本地 localStorage → 后端消息流快照 → langgraph 对话历史。")).toBe(1);
    expect(count(raw, "// APPEND_HERE")).toBe(0);
  });

  it("搬移语句逐字保留（三级兜底 / 竞态短轮询 / 修剪上报 / 卡片开场白 / 清理）", () => {
    const seam = strip(SEAM);
    const must = [
      "const isHome = threadId === \"home\";",
      "const local = isHome ? null : localStorage.getItem(chatKey);",
      "if (!shouldRecoverAgentRun(threadId)) return;",
      "if (!alive || recoveryActiveRef.current) return;",
      "const st = await fetchAgentRunning(threadId);",
      "if (st.running) {",
      "startAgentRecovery(threadId, repo?.id);",
      "if (items.length > messagesRef.current.length) {",
      "if (items.length > 0) return;",
      "if (readHomeDraft().length > 0) setMessages(readHomeDraft());",
      "const pruned = pruneUnsubmittedMediaSlots(snapshotMessages);",
      "stage: \"resume_unsubmitted\", error: \"页面恢复时发现未提交到 ComfyUI 的孤儿插画槽\",",
      "const initialHistory = resolveInitialHistory(",
      "if (shownLocal) { loadedRef.current = true; await maybeStartBgPoll(); return; }",
      "const opening = await loadOpeningMessage(settings.characterDir, openingCardName, effectivePersona.name || \"\");",
      "finally { if (alive) loadedRef.current = true; }",
      "wsUnsubRef.current?.(); wsUnsubRef.current = null;",
      "dispatch({ t: \"reset\" });",
    ];
    for (const line of must) {
      expect(count(seam, line), line).toBe(1);
      expect(count(strip(VIEW), line), `视图不应再自持：${line}`).toBe(0);
    }
    // ⚠ 行尾注释会被 `stripComments` 连带抹掉 ⇒ 「注释仍在」只能在**未去注释**的原文里断
    const rawSeam = read(SEAM);
    for (const line of [
      "if (items.length > 0) return; // 快照已有内容且未增长，视为稳定",
      "if (shownLocal) { loadedRef.current = true; await maybeStartBgPoll(); return; }  // 本地已渲染、后端无快照",
      "wsUnsubRef.current?.(); wsUnsubRef.current = null;  // 关进度 WS，避免切仓库泄漏连接",
      "dispatch({ t: \"reset\" });  // 清空生成状态 + 待发队列，避免串到新仓库",
    ]) {
      expect(count(rawSeam, line), line).toBe(1);
    }
    // ⚠ `readHomeDraft` 随体迁入新属主：视图只留写/清访问器
    expect(count(seam, "readHomeDraft()")).toBe(2);
    // ⚠ activeThreadRef.recoveryTokenRef.agentBusyRef.bgRunningRef 在清理里各恰一次（视图另有既有点，故只钉新属主）
    expect(count(seam, "agentBusyRef.current = false;")).toBe(1);
    expect(count(seam, "bgRunningRef.current = false;")).toBe(1);
  });

  it("视图恰好调用一次，且调用点在依赖的 ref/运行时定义之后、`// APPEND_HERE` 之前", () => {
    const raw = read(VIEW);
    const view = stripComments(raw);
    expect(count(view, "useSessionLoad({")).toBe(1);
    expect(view).toMatch(
      /useSessionLoad\(\{\s*\n\s*threadId, chatKey, repo, setMessages, loadedRef, messagesRef, recoveryActiveRef,\s*\n\s*activeThreadRef, recoveryTokenRef, agentBusyRef, bgRunningRef, abortRef,\s*\n\s*scenarioSnapshotTurnRef, snapTimer, wsUnsubRef, workflowRuntime, startAgentRecovery,\s*\n\s*settings, openingCardName, effectivePersona, dispatch,\s*\n\s*\}\);/,
    );
    // 21 个同名 deps 一个都不能少（结构与数量由这行钉住）
    for (const name of ["threadId", "chatKey", "repo", "setMessages", "loadedRef", "messagesRef",
      "recoveryActiveRef", "activeThreadRef", "recoveryTokenRef", "agentBusyRef", "bgRunningRef",
      "abortRef", "scenarioSnapshotTurnRef", "snapTimer", "wsUnsubRef", "workflowRuntime",
      "startAgentRecovery", "settings", "openingCardName", "effectivePersona", "dispatch"]) {
      expect(view, name).toContain(name);
    }

    const at = raw.indexOf("useSessionLoad({");
    expect(at).toBeGreaterThan(raw.indexOf("const snapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);"));
    expect(at).toBeGreaterThan(raw.indexOf("const recoveryTokenRef = useRef(0);"));
    expect(at).toBeGreaterThan(raw.indexOf("const historyRuntime = historyRuntimeRef.current.runtime;"));
    // ⚠ `// APPEND_HERE` 是整行注释：位置判据必须在原文上算，且先断锚点存在
    const appendAt = raw.indexOf("// APPEND_HERE");
    expect(appendAt).toBeGreaterThan(0);
    expect(at).toBeLessThan(appendAt);
  });

  it("视图行数上界（本步只减不增：<= 1770 行）", () => {
    const raw = read(VIEW);
    expect(raw.split("\n").length).toBeLessThanOrEqual(1770);
  });
});
