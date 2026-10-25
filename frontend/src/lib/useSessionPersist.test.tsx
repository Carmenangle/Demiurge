// @vitest-environment jsdom
//
// useSessionPersist 的特征网（T2.2-B4-b · 2026-09-17）
//
// 背景：`lib/useChatSession.ts` 里「消息变化时持久化」的链路改造前是 **内联三件**——
// `persistDataUri`（data:URI 大图落盘瘦身，11 行含注释）、`slimSnapshot`（1 行）、
// 防抖持久化 `useEffect`（2 行语义注释 + 41 行体，含 600ms 防抖与两道 `canCommitSnapshot` 闸）。
// 已**逐字节搬移**到 `lib/useSessionPersist.ts`。该 hook 没有组件测试 ⇒ 三道网分工：
//
//   ① 行为网 —— jsdom + React 19 `act` 真挂载 Host（只调用 hook），断言**经过 hook 之后**可观测的那一面：
//      加载闸、home 草稿区、600ms 防抖与写点内容（localStorage 瘦身 vs 后端全量）、
//      两道 `canCommitSnapshot` 闸（含 `await` 期间被推进的真实竞态）、回写、超额吞错、回合快照去重与回滚、
//      重订阅时机（改 settings 无关字段不重跑 / 改 outputDir 才重跑）。
//   ② 等价矩阵 —— 把改造前「最终落哪个写点」的判定**逐字抄**成 `legacyCommitTarget`，在
//      `loaded × isHome × 第一道闸 × 第二道闸` 的 16 组网格上断言实际落点 ≡ 参照。
//   ③ 源码形态锁 —— 行为网**抓不到分层回退**（hook 被删后内联回视图照样可能全绿），故另钉：
//      搬移语句逐字在新属主且视图缺席、`useEffect` 恰一次、依赖数组那一行、视图「恰好调用一次」
//      且**实参清单逐字**、调用点夹在 `usePlanTaskFeed({` 与 `const pickTemplate` 之间。
//
// ⚠ 硬要求（承 T2.1/T2.2-A/B1/B2/B3 教训）：
//   ① 用例隔离：`afterEach` 按登记表统一卸载（残留 root 会在下一用例里继续跑 effect / 吃掉 timer）；
//   ② 等价矩阵在单 `it` 内循环 ⇒ **逐组重置** localStorage / homeDraft / 计数器（不重置会累积）；
//   ③ 形态锁必须**去注释**再判「视图缺席」（注释里提一句 `canCommitSnapshot` 就算「还在」）；
//   ④ 源码只能在 `it` 回调内读（jsdom 模块求值期 `import.meta.url` 不是 `file:` 协议）；
//   ⑤ 位置判据必须先断锚点存在（`indexOf` 得 -1 会让上界断言假通过）；
//   ⑥ `act` 里对 **真实 ref** 赋值不要包 `act`（ref 不是状态，包了只会多一次无意义刷新）。
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
import { clearHomeDraft, readHomeDraft } from "./homeDraft";
import { useSessionPersist, type SessionPersistDeps } from "./useSessionPersist";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const DATA_URI = "data:image/png;base64,AAAA";

// ─────────────────────────────────────────────────────────────────────────────
// 外部依赖替身：落盘瘦身（comfyui）/ 后端快照（ai）/ 回合快照（scenario）
// ─────────────────────────────────────────────────────────────────────────────
const H = vi.hoisted(() => ({
  /** 全量调用日志（断言顺序与次数） */
  calls: [] as string[],
  /** `saveSnapshot` 的入参（末项 = 最后一次落盘内容） */
  saved: [] as unknown[],
  /** `saveLocalSrc` 的入参 */
  srcArgs: [] as unknown[],
  /** `createScenarioSnapshot` 的入参 */
  scenario: [] as unknown[],
  scenarioMode: "ok" as "ok" | "throw",
  /** 挂起 `saveLocalSrc`（复刻「图片落盘耗时」窗口，用例内 release） */
  holdSrc: { on: false, release: null as null | (() => void) },
}));

vi.mock("../api/comfyui", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    saveLocalSrc: async (arg: unknown) => {
      H.calls.push("saveLocalSrc");
      H.srcArgs.push(arg);
      if (H.holdSrc.on) await new Promise<void>((resolve) => { H.holdSrc.release = resolve; });
      return { path: "/local-view/ref.png" };
    },
    localViewUrl: (p: string) => `local-view://${p}`,
  };
});

vi.mock("../api/ai", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    saveSnapshot: async (tid: string, items: unknown[]) => {
      H.calls.push(`saveSnapshot:${tid}`);
      H.saved.push(items);
    },
  };
});

vi.mock("../api/scenario", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    createScenarioSnapshot: async (arg: unknown) => {
      H.calls.push("createScenarioSnapshot");
      H.scenario.push(arg);
      if (H.scenarioMode === "throw") throw new Error("scenario-down");
    },
  };
});

// ─────────────────────────────────────────────────────────────────────────────
// 改造前写法的**逐字抄录**（唯一等价性参照，不得顺手优化）
// ─────────────────────────────────────────────────────────────────────────────
/** 改造前 effect 里「最终落到哪个写点」的判定，逐字抄成纯函数。 */
const legacyCommitTarget = (
  loaded: boolean, isHome: boolean, blockedFirst: boolean, blockedSecond: boolean,
): "skip-loading" | "homeDraft" | "none" | "commit" => {
  if (!loaded) return "skip-loading";
  if (isHome) return "homeDraft";
  if (blockedFirst) return "none";
  if (blockedSecond) return "none";
  return "commit";
};

// ─────────────────────────────────────────────────────────────────────────────
// 挂载脚手架
// ─────────────────────────────────────────────────────────────────────────────
const teardowns: Array<() => Promise<void>> = [];
async function teardownAll() {
  while (teardowns.length > 0) {
    const t = teardowns.pop()!;
    await t();
  }
}

const userMsg = (id = "u1", text = "hi"): ChatMessage =>
  ({ id, role: "user", text } as ChatMessage);
const botMsg = (id = "b1", text = "story"): ChatMessage =>
  ({ id, role: "assistant", text } as ChatMessage);
/** 用户上传的参考图：data:URI 是瘦身链唯一触发条件。 */
const uploadMsg = (id = "u2"): ChatMessage =>
  ({ id, role: "user", text: "", parts: [{ type: "image", url: DATA_URI }] } as unknown as ChatMessage);
/** 工作流节点卡：`capturedGraph` 只在 localStorage 那一份里被抹掉。 */
const workflowMsg = (id = "w1"): ChatMessage =>
  ({
    id, role: "assistant", text: "",
    workflow: { templateId: "t", templateName: "T", draftGraph: null, capturedGraph: { nodes: [1] }, done: true },
  } as unknown as ChatMessage);

function makeDeps(over: Partial<SessionPersistDeps> = {}) {
  const refs = {
    loadedRef: { current: true } as MutableRefObject<boolean>,
    messagesRef: { current: [] } as MutableRefObject<ChatMessage[]>,
    activeThreadRef: { current: "t1" } as MutableRefObject<string>,
    agentBusyRef: { current: false } as MutableRefObject<boolean>,
    bgRunningRef: { current: false } as MutableRefObject<boolean>,
    snapTimer: { current: null } as MutableRefObject<ReturnType<typeof setTimeout> | null>,
    scenarioSnapshotTurnRef: { current: 0 } as MutableRefObject<number>,
  };
  const messages = [userMsg()];
  refs.messagesRef.current = messages;
  const setMessages = vi.fn((_next: unknown) => undefined);
  const deps: SessionPersistDeps = {
    threadId: "t1",
    chatKey: "laf_chat_t1",
    repo: { id: "t1" } as unknown as SessionPersistDeps["repo"],
    settings: { outputDir: "out" } as unknown as SessionPersistDeps["settings"],
    messages,
    setMessages: setMessages as unknown as SessionPersistDeps["setMessages"],
    streamingId: null,
    regeneratingIds: new Set<string>(),
    ...refs,
    ...over,
  };
  return { deps, ...refs, messages, setMessages };
}

function Host({ deps }: { deps: SessionPersistDeps }) {
  useSessionPersist(deps);
  return null;
}

async function mount(deps: SessionPersistDeps) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Host, { deps, key: "host" })); });
  const rerender = async (next: SessionPersistDeps) => {
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

const localRaw = (key: string) => localStorage.getItem(key);
const localParsed = (key: string) => JSON.parse(localRaw(key)!) as ChatMessage[];

// ─────────────────────────────────────────────────────────────────────────────
// ① 行为网
// ─────────────────────────────────────────────────────────────────────────────
describe("useSessionPersist 行为网", () => {
  it("加载完成前不写：连防抖 timer 都不排（防空数组覆盖已存记录）", async () => {
    const d = makeDeps({ loadedRef: { current: false } as MutableRefObject<boolean> });
    await mount(d.deps);
    await advance(2000);
    expect(localRaw("laf_chat_t1")).toBeNull();
    expect(H.saved).toHaveLength(0);
    expect(readHomeDraft()).toHaveLength(0);
    expect(d.snapTimer.current).toBeNull();
  });

  it("首页(home) 只写内存草稿区：同步写、不排 timer、不落 localStorage / 后端快照", async () => {
    const messages = [botMsg("d1", "draft")];
    const d = makeDeps({
      threadId: "home", chatKey: "laf_chat_home", messages,
      activeThreadRef: { current: "home" } as MutableRefObject<string>,
    });
    d.messagesRef.current = messages;
    await mount(d.deps);
    expect(readHomeDraft().map((m) => m.id)).toEqual(["d1"]);   // 同步写（不经 600ms）
    expect(d.snapTimer.current).toBeNull();
    await advance(2000);
    expect(localRaw("laf_chat_home")).toBeNull();
    expect(H.saved).toHaveLength(0);
  });

  it("600ms 防抖：未到点不写，到点写「localStorage 瘦身版 + 后端全量快照」", async () => {
    const messages = [uploadMsg(), workflowMsg(), botMsg()];
    const d = makeDeps({ messages });
    d.messagesRef.current = messages;
    await mount(d.deps);

    await advance(599);
    expect(localRaw("laf_chat_t1")).toBeNull();
    expect(H.saved).toHaveLength(0);

    await advance(1);
    await flush();
    // 瘦身链被触发（data:URI 走落盘）
    expect(H.calls.filter((c) => c === "saveLocalSrc")).toHaveLength(1);
    expect(H.srcArgs[0]).toEqual({ src: DATA_URI, repoId: "t1", outputDir: "out", subdir: "reference" });
    // 本地那份：data:URI 已换成本地小地址
    expect((localParsed("laf_chat_t1")[0].parts![0] as { url: string }).url).toBe("local-view:///local-view/ref.png");
    // 本地那份：capturedGraph 被抹掉
    expect(localParsed("laf_chat_t1")[1].workflow!.capturedGraph).toBeNull();
    // 后端那份：capturedGraph 原样保留（本地轻量化不能污染真源）
    expect((H.saved[0] as ChatMessage[])[1].workflow!.capturedGraph).toEqual({ nodes: [1] });
  });

  it("防抖合并：600ms 内连续换 messages 只落最后一次", async () => {
    const a = [userMsg("a")];
    const b = [userMsg("b")];
    const d = makeDeps({ messages: a });
    d.messagesRef.current = a;
    const h = await mount(d.deps);

    await advance(300);
    const d2 = { ...d.deps, messages: b };
    d.messagesRef.current = b;
    await h.rerender(d2);       // 先 clearTimeout 再重排
    await advance(300);
    expect(H.saved).toHaveLength(0);   // 距第二次调度仅 300ms
    await advance(300);
    await flush();
    expect(H.saved).toHaveLength(1);
    expect((H.saved[0] as ChatMessage[])[0].id).toBe("b");
  });

  it("第一道闸：后台任务 / 流式 / 重生成进行中一律不提交", async () => {
    for (const over of [
      { bgRunningRef: { current: true } as MutableRefObject<boolean> },
      { streamingId: "bot-1" },
      { regeneratingIds: new Set(["bot-1"]) },
    ]) {
      H.saved = [];
      H.calls = [];
      const d = makeDeps(over as Partial<SessionPersistDeps>);
      const h = await mount(d.deps);
      await advance(2000);
      await flush();
      expect(localRaw("laf_chat_t1"), JSON.stringify(over)).toBeNull();
      expect(H.saved, JSON.stringify(over)).toHaveLength(0);
      await h.unmount();
    }
  });

  it("第一道闸：快照链已失效（messagesRef 被推进）⇒ 不提交", async () => {
    const d = makeDeps();
    await mount(d.deps);
    d.messagesRef.current = [userMsg("newer")];   // 防抖期内又追加了消息
    await advance(2000);
    await flush();
    expect(H.saved).toHaveLength(0);
  });

  it("第一道闸：线程已切走（activeThreadRef ≠ tid）⇒ 不提交", async () => {
    const d = makeDeps();
    await mount(d.deps);
    d.activeThreadRef.current = "t2";
    await advance(2000);
    await flush();
    expect(H.saved).toHaveLength(0);
  });

  it("第二道闸：`await` 期间 messagesRef 被推进 ⇒ 连 localStorage 都不写（真实竞态）", async () => {
    const messages = [uploadMsg()];
    const d = makeDeps({ messages });
    d.messagesRef.current = messages;
    H.holdSrc.on = true;               // 挂住落盘，制造 await 窗口
    await mount(d.deps);

    await advance(600);                // timer 触发，卡在 await slimSnapshot
    expect(H.calls).toContain("saveLocalSrc");
    d.messagesRef.current = [uploadMsg(), userMsg("newer")];   // 落盘期间又追加了用户消息
    await act(async () => { H.holdSrc.release!(); });           // 放行落盘
    await flush();

    expect(localRaw("laf_chat_t1")).toBeNull();   // 旧任务连 localStorage 都不得写
    expect(H.saved).toHaveLength(0);
  });

  it("第二道闸：`await` 期间后台任务/Agent 接管 ⇒ 不提交", async () => {
    for (const flip of ["agentBusyRef", "bgRunningRef"] as const) {
      localRaw("laf_chat_t1");
      localStorage.clear();
      H.saved = [];
      H.calls = [];
      H.holdSrc.on = true;
      H.holdSrc.release = null;
      const messages = [uploadMsg()];
      const d = makeDeps({ messages });
      d.messagesRef.current = messages;
      const h = await mount(d.deps);
      await advance(600);
      (d[flip] as MutableRefObject<boolean>).current = true;
      await act(async () => { H.holdSrc.release!(); });
      await flush();
      expect(localRaw("laf_chat_t1"), flip).toBeNull();
      expect(H.saved, flip).toHaveLength(0);
      H.holdSrc.on = false;
      await h.unmount();
    }
  });

  it("回写：瘦身后与原文不同 ⇒ setMessages(acceptSlimmedMessages(current, original, full))", async () => {
    const messages = [uploadMsg()];
    const d = makeDeps({ messages });
    d.messagesRef.current = messages;
    await mount(d.deps);
    await advance(700);
    await flush();

    expect(d.setMessages).toHaveBeenCalledTimes(1);
    const updater = d.setMessages.mock.calls[0][0] as (c: ChatMessage[]) => ChatMessage[];
    // 上游 current 仍 === original ⇒ 接受瘦身结果
    expect(updater(messages)).not.toBe(messages);
    expect((updater(messages)[0].parts![0] as { url: string }).url).toBe("local-view:///local-view/ref.png");
    // 上游 current 已被替换 ⇒ 保持上游（不回退用户刚看到的内容）
    const replaced = [userMsg("other")];
    expect(updater(replaced)).toBe(replaced);
  });

  it("内容未被瘦身改动时不做无谓回写（省一次渲染）", async () => {
    const d = makeDeps();
    await mount(d.deps);
    await advance(700);
    await flush();
    expect(H.saved).toHaveLength(1);
    expect(d.setMessages).not.toHaveBeenCalled();
  });

  it("localStorage 超额抛错被吞：后端快照照写", async () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    const d = makeDeps();
    await mount(d.deps);
    await advance(700);
    await flush();
    spy.mockRestore();
    expect(H.saved).toHaveLength(1);
  });

  it("自动回合快照：turn 大于水位才建，并在成功后抬水位", async () => {
    const messages = [userMsg(), botMsg("b1", "正文")];
    const d = makeDeps({ messages });
    d.messagesRef.current = messages;
    d.scenarioSnapshotTurnRef.current = 0;
    await mount(d.deps);
    await advance(700);
    await flush();
    expect(H.scenario).toHaveLength(1);
    expect(H.scenario[0]).toEqual({
      output_dir: "out", repo_id: "t1", turn: 1, label: "自动回合快照", dedupe_key: "turn:1",
    });
    expect(d.scenarioSnapshotTurnRef.current).toBe(1);
  });

  it("自动回合快照：水位已到位（turn ≤ ref）⇒ 不重复建", async () => {
    const messages = [userMsg(), botMsg("b1", "正文")];
    const d = makeDeps({ messages });
    d.messagesRef.current = messages;
    d.scenarioSnapshotTurnRef.current = 5;
    await mount(d.deps);
    await advance(700);
    await flush();
    expect(H.saved).toHaveLength(1);
    expect(H.scenario).toHaveLength(0);
    expect(d.scenarioSnapshotTurnRef.current).toBe(5);
  });

  it("自动回合快照：建失败回滚水位到 turn-1（下次仍会重试）", async () => {
    H.scenarioMode = "throw";
    const messages = [userMsg(), botMsg("b1", "正文")];
    const d = makeDeps({ messages });
    d.messagesRef.current = messages;
    d.scenarioSnapshotTurnRef.current = 0;
    await mount(d.deps);
    await advance(700);
    await flush();
    expect(H.scenario).toHaveLength(1);
    expect(d.scenarioSnapshotTurnRef.current).toBe(0);   // 1 - 1
  });

  it("未配 outputDir / 无 repo ⇒ 仍落 localStorage 与后端快照，只是不建回合快照", async () => {
    const messages = [userMsg(), botMsg("b1", "正文")];
    const d = makeDeps({
      messages,
      settings: { outputDir: "" } as unknown as SessionPersistDeps["settings"],
    });
    d.messagesRef.current = messages;
    await mount(d.deps);
    await advance(700);
    await flush();
    expect(localRaw("laf_chat_t1")).not.toBeNull();
    expect(H.saved).toHaveLength(1);
    expect(H.scenario).toHaveLength(0);
  });

  it("重订阅时机：改 settings 无关字段不重跑；改 settings.outputDir 才重跑（依赖数组＝行为面）", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await advance(700);
    await flush();
    expect(H.saved).toHaveLength(1);

    // 无关字段变化：messages/chatKey/threadId/outputDir/repo?.id/streamingId/regeneratingIds.size 全不变
    const unrelated = {
      ...d.deps,
      settings: { outputDir: "out", theme: "dark" } as unknown as SessionPersistDeps["settings"],
    };
    await h.rerender(unrelated);
    await advance(700);
    await flush();
    expect(H.saved).toHaveLength(1);   // 没重排 timer

    const outChanged = {
      ...d.deps,
      settings: { outputDir: "out2" } as unknown as SessionPersistDeps["settings"],
    };
    await h.rerender(outChanged);
    await advance(700);
    await flush();
    expect(H.saved).toHaveLength(2);   // 重排了
  });

  it("换线程 ⇒ 重跑（chatKey/threadId 都在依赖数组里）", async () => {
    const d = makeDeps();
    const h = await mount(d.deps);
    await advance(700);
    await flush();
    expect(H.saved).toHaveLength(1);
    const messages = [userMsg("t2-msg")];
    d.messagesRef.current = messages;
    d.activeThreadRef.current = "t2";
    await h.rerender({ ...d.deps, threadId: "t2", chatKey: "laf_chat_t2", messages });
    await advance(700);
    await flush();
    expect(H.saved).toHaveLength(2);
    expect(H.calls.filter((c) => c === "saveSnapshot:t2")).toHaveLength(1);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// ② 等价矩阵：loaded × isHome × 第一道闸 × 第二道闸 = 16 组
// ─────────────────────────────────────────────────────────────────────────────
describe("useSessionPersist 等价矩阵（落点判定）", () => {
  it("16 组网格上实际落点 ≡ 改造前判定", async () => {
    let seq = 0;
    for (const loaded of [false, true]) {
      for (const isHome of [false, true]) {
        for (const blockedFirst of [false, true]) {
          for (const blockedSecond of [false, true]) {
            // ⚠ 逐组重置（同 it 内共享 H / localStorage / 模块级草稿区）
            localStorage.clear();
            clearHomeDraft();
            H.saved = [];
            H.calls = [];
            H.scenario = [];
            H.holdSrc.on = false;
            H.holdSrc.release = null;

            const tid = `grid-${++seq}`;
            const key = `laf_chat_${tid}`;
            const messages = [userMsg()];
            const d = makeDeps({
              threadId: isHome ? "home" : tid,
              chatKey: isHome ? "laf_chat_home" : key,
              messages,
              loadedRef: { current: loaded } as MutableRefObject<boolean>,
              activeThreadRef: { current: isHome ? "home" : tid } as MutableRefObject<string>,
              bgRunningRef: { current: blockedFirst } as MutableRefObject<boolean>,
              agentBusyRef: { current: blockedSecond } as MutableRefObject<boolean>,
            });
            d.messagesRef.current = messages;
            const h = await mount(d.deps);
            await advance(700);
            await flush();

            const tag = `loaded=${loaded} isHome=${isHome} blockedFirst=${blockedFirst} blockedSecond=${blockedSecond}`;
            const homeWritten = readHomeDraft().length > 0;
            const localWritten = localRaw(isHome ? "laf_chat_home" : key) !== null;
            const snapWritten = H.saved.length > 0;
            const actual = homeWritten
              ? "homeDraft"
              : (localWritten || snapWritten)
                ? "commit"
                : (d.snapTimer.current === null ? "skip-loading" : "none");
            expect(actual, tag).toBe(legacyCommitTarget(loaded, isHome, blockedFirst, blockedSecond));
            // 写点互斥：草稿区（home）与「本地 + 后端」快照双写不会同时发生
            expect(homeWritten && (localWritten || snapWritten), tag).toBe(false);
            await h.unmount();
          }
        }
      }
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// ③ 源码形态锁（行为网抓不到分层回退）
// ─────────────────────────────────────────────────────────────────────────────
const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<!:)\/\/[^\n]*/g, "");

/**
 * 读同目录源码（**必须在 `it` 回调内调用**：jsdom 模块求值期 `import.meta.url` 不是 `file:` 协议）。
 * ⚠ 相对路径必须以**变量**传入：`new URL("./x.ts", import.meta.url)` 这种**字面量**写法会被 Vite 的
 *   asset 转换接管，运行期拿到服务端 URL（`TypeError: The URL must be of scheme file`）；
 *   走形参即绕开该转换（既有 useSessionLoad / useWorkflowResume / usePlanTaskFeed 同款）。
 */
const readSrc = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8").replace(/\r\n/g, "\n");

const VIEW_TS = "./useChatSession.ts";

const SEAM_TS = "./useSessionPersist.ts";

const count = (hay: string, needle: string) => hay.split(needle).length - 1;

describe("useSessionPersist 源码形态锁", () => {
  it("搬移语句逐字在新属主（瘦身链 / 两道闸 / 防抖 600ms / 落盘 / 回写）", () => {
    const seam = readSrc(SEAM_TS);
    for (const line of [
      "  // 存快照前给图片瘦身：把用户上传的 data:URI 大图落盘转 local-view 小地址。",
      "  // data:URI 只来自用户上传的参考图 → 落 reference/ 子夹，与生成图（仓库根目录）分开。",
      "  const persistDataUri = async (src: string): Promise<string> => {",
      "  const slimSnapshot = (msgs: ChatMessage[]) => slimSnapshotPure(msgs, persistDataUri);",
      "  // 消息变化时持久化：本地快取 + 后端快照统一防抖；本地也写瘦身后内容，避免 dataURI 大图撑爆 localStorage。",
      "  // 首页(home)=临时草稿区：只写模块级 homeDraft（进程内切走切回保留，刷新即空），不落 localStorage / 后端快照。",
      "    if (!loadedRef.current) return;  // 加载完成前不写，防止空数组覆盖",
      "    if (threadId === \"home\") { writeHomeDraft(messages); return; }  // 首页草稿区：仅存内存",
      "    if (snapTimer.current) clearTimeout(snapTimer.current);",
      "    const tid = threadId;",
      "    const original = messages;",
      "    snapTimer.current = setTimeout(async () => {",
      "      if (bgRunningRef.current || streamingId || regeneratingIds.size > 0) return;",
      "      if (!canCommitSnapshot(",
      "      // 流式正文或重生成尚未终态时不能创建世界状态快照，否则会把半截正文与未完成写回固化。",
      "      const full = await slimSnapshot(original);",
      "      if (agentBusyRef.current || bgRunningRef.current || !canCommitSnapshot(",
      "        localStorage.setItem(chatKey, JSON.stringify(slim));",
      "      saveSnapshot(tid, full).then(() => {",
      "        if (settings.outputDir && repo?.id && turn > scenarioSnapshotTurnRef.current) {",
      "          }).catch(() => { scenarioSnapshotTurnRef.current = turn - 1; });",
      "      }).catch(() => {});  // 后端未起则忽略本地仍在",
      "      if (tid === threadId && JSON.stringify(full) !== JSON.stringify(original)) {",
      "        setMessages((current) => acceptSlimmedMessages(current, original, full));",
      "    }, 600);",
      "  }, [messages, chatKey, threadId, settings.outputDir, repo?.id, streamingId, regeneratingIds.size]);",
    ]) {
      expect(count(seam, line), line).toBe(1);
    }
    // 防抖持久化 effect 恰一个（增删 effect 必须同步改本锁）
    expect(count(seam, "useEffect(() => {")).toBe(1);
  });

  it("视图缺席：视图代码里再也读不到持久化链的裸符号（分层回退＝红）", () => {
    const view = readSrc(VIEW_TS);
    const code = stripComments(view);
    for (const sym of [
      "persistDataUri", "slimSnapshotPure", "canCommitSnapshot", "acceptSlimmedMessages",
      "createScenarioSnapshot", "saveLocalSrc", "setTimeout(async () => {",
    ]) {
      expect(count(code, sym), sym).toBe(0);
    }
  });

  it("调用点：视图恰一次、实参清单逐字、夹在 usePlanTaskFeed 与 pickTemplate 之间", () => {
    const view = readSrc(VIEW_TS);
    expect(count(view, "useSessionPersist({")).toBe(1);
    expect(view).toMatch(
      /useSessionPersist\(\{\s*\n\s*threadId, chatKey, repo, settings, messages, setMessages, loadedRef, messagesRef,\s*\n\s*activeThreadRef, agentBusyRef, bgRunningRef, snapTimer, scenarioSnapshotTurnRef,\s*\n\s*streamingId, regeneratingIds,\s*\n\s*\}\);/,
    );
    const iPlan = view.indexOf("usePlanTaskFeed({");
    const iCall = view.indexOf("useSessionPersist({");
    const iPick = view.indexOf("  const pickTemplate = (t: Template) => {");
    expect(iPlan).toBeGreaterThan(-1);   // 锚点必须先存在（否则 -1 让断言假通过）
    expect(iPick).toBeGreaterThan(-1);
    expect(iPlan).toBeLessThan(iCall);
    expect(iCall).toBeLessThan(iPick);
  });
});

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  clearHomeDraft();
  H.calls = [];
  H.saved = [];
  H.srcArgs = [];
  H.scenario = [];
  H.scenarioMode = "ok";
  H.holdSrc.on = false;
  H.holdSrc.release = null;
});

afterEach(async () => {
  await teardownAll();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
