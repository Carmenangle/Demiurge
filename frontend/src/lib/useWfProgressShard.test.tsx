// @vitest-environment jsdom
//
// useWfProgressShard 的特征网（T2.2-B4-a · 2026-09-17）
//
// 背景：`lib/useChatSession.ts` 里「工作流进度的跨导航分片」改造前是 **3 处内联**——
// 模块级两条 Map（含 7 行 P18 语义注释）、组件内两条 `useState` + 两条包装 setter（带注释）、
// 切线程重对齐 `useEffect`（3 行）。已**逐字节搬移**到 `lib/useWfProgressShard.ts`。
// 该 hook 没有组件测试 ⇒ 分工：
//
//   ① 行为网 —— jsdom + React 19 `act` 真挂载 Host（只调用 hook），断言**经过 hook 之后**可观测的那一面：
//      首挂载读分片、包装 setter 双写、切线程重对齐（P18 不跨仓库串）、跨卸载/重挂载保留、双向不串。
//   ② 源码形态锁 —— 行为网**抓不到分层回退**（hook 被删后内联回视图照样可能全绿），故另钉：
//      搬移语句逐字在新属主且视图缺席、`useEffect` 恰一次、视图「恰好调用一次」且调用点夹在
//      `threadId` 定义与 `regeneratingIds` 之间（hook 调用序＝状态初始化序）。
//      ⚠ 本刀**不做等价矩阵**：搬移体是「读分片 / 写分片」的纯映射，逐字抄一份 `legacy*` 会退化成
//      同义反复（T2.1-C 教训），故以「逐字搬移锁 + 直断言行为网」替代。
//
// ⚠ 硬要求（承 T2.1/T2.2-A/B1/B2/B3 教训）：
//   ① 用例隔离：`afterEach` 按登记表统一卸载（残留 root 会在下一用例里继续跑 effect）；
//   ② 分片是**模块级真源**、本刀**故意不导出 reset**（导出 reset 等于把「状态住在模块里」这条事实让渡出去）
//      ⇒ 每个用例用**唯一线程键**（`nextThread()`）保证互不污染，绝不靠「上一用例留下的分片」；
//   ③ 形态锁必须**去注释**再判「视图缺席」（否则注释里提一句 `persistedWfProgress` 就算「还在」）；
//   ④ 源码只能在 `it` 回调内读（jsdom 模块求值期 `import.meta.url` 不是 `file:` 协议）；
//   ⑤ 位置判据必须先断锚点存在（`indexOf` 得 -1 会让上界断言假通过）。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { useWfProgressShard, type WfProgressShard } from "./useWfProgressShard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ─────────────────────────────────────────────────────────────────────────────
// 挂载脚手架：真实 Host 组件（只调用 hook）
// ─────────────────────────────────────────────────────────────────────────────
/** 每次调用给一个**新线程键**：分片是模块级真源，用例之间只能靠键隔离（无 reset API 是刻意的）。 */
let seq = 0;
const nextThread = () => `tk-${++seq}`;

const teardowns: Array<() => Promise<void>> = [];
async function teardownAll() {
  while (teardowns.length > 0) {
    const t = teardowns.pop()!;
    await t();
  }
}

/** 最近一次渲染拿到的那份分片（hook 的返回值）。 */
let latest: WfProgressShard | null = null;

function Host({ threadId }: { threadId: string }) {
  latest = useWfProgressShard(threadId);
  return null;
}

async function mount(threadId: string) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(createElement(Host, { threadId, key: "h" })); });
  const rerender = async (next: string) => {
    await act(async () => { root.render(createElement(Host, { threadId: next, key: "h" })); });
  };
  const unmount = async () => {
    await act(async () => { root.unmount(); });
    host.remove();
  };
  teardowns.push(unmount);
  return { rerender, unmount };
}

/** 在一次 act 里连续写两项（复刻真实 WS 回调：进度与节点名常同批到达）。 */
async function write(progress: number | null, node: string) {
  await act(async () => {
    latest!.updateWfProgress(progress);
    latest!.updateWfNode(node);
  });
}

afterEach(async () => {
  await teardownAll();
  latest = null;
});

// ─────────────────────────────────────────────────────────────────────────────
// ① 行为网
// ─────────────────────────────────────────────────────────────────────────────
describe("useWfProgressShard 行为网", () => {
  it("首次挂载按当前线程键读分片：全新线程 ⇒ null / \"\"", async () => {
    await mount(nextThread());
    expect(latest!.wfProgress).toBeNull();
    expect(latest!.wfNode).toBe("");
  });

  it("包装 setter 同时写 state 与分片：同一渲染周期内立即可见", async () => {
    await mount(nextThread());
    await write(42, "KSampler");
    expect(latest!.wfProgress).toBe(42);
    expect(latest!.wfNode).toBe("KSampler");
    await write(97, "VAEDecode");
    expect(latest!.wfProgress).toBe(97);
    expect(latest!.wfNode).toBe("VAEDecode");
  });

  it("切线程重对齐：A 的进度不带到 B，切回 A 复原（P18 不跨仓库串）", async () => {
    const a = nextThread();
    const b = nextThread();
    const h = await mount(a);
    await write(61, "VAEDecode");
    await h.rerender(b);
    expect(latest!.wfProgress).toBeNull();   // 空分片 ⇒ 归 null，而不是沿用 A 的 61
    expect(latest!.wfNode).toBe("");
    await h.rerender(a);
    expect(latest!.wfProgress).toBe(61);
    expect(latest!.wfNode).toBe("VAEDecode");
  });

  it("双向不串：在 A 写、切到 B 写、再各切回，各自读自己的分片", async () => {
    const a = nextThread();
    const b = nextThread();
    const h = await mount(a);
    await write(9, "N1");
    await h.rerender(b);
    await write(88, "N2");
    await h.rerender(a);
    expect([latest!.wfProgress, latest!.wfNode]).toEqual([9, "N1"]);
    await h.rerender(b);
    expect([latest!.wfProgress, latest!.wfNode]).toEqual([88, "N2"]);
  });

  it("分片跨卸载/重挂载保留（切换页面回来后进度不丢——本刀的存在理由）", async () => {
    const a = nextThread();
    const h1 = await mount(a);
    await write(77, "SaveImage");
    await h1.unmount();
    await mount(a);
    expect(latest!.wfProgress).toBe(77);
    expect(latest!.wfNode).toBe("SaveImage");
  });

  it("重挂载读的是**当前**分片值，不是模块加载期的快照", async () => {
    const a = nextThread();
    const h1 = await mount(a);
    await write(12, "N1");
    await h1.unmount();
    // 卸载后再由「另一个实例」写同一个键（模拟同线程的另一处写点），重挂载必须看到新值
    const h2 = await mount(a);
    await write(34, "N2");
    await h2.unmount();
    await mount(a);
    expect([latest!.wfProgress, latest!.wfNode]).toEqual([34, "N2"]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// ② 源码形态锁（行为网抓不到分层回退）
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

const SEAM_TS = "./useWfProgressShard.ts";

const count = (hay: string, needle: string) => hay.split(needle).length - 1;

describe("useWfProgressShard 源码形态锁", () => {
  it("搬移语句逐字在新属主（分片 Map / useState / 包装 setter / 重对齐）", () => {
    const seam = readSrc(SEAM_TS);
    for (const line of [
      "const persistedWfProgress = new Map<string, number | null>();",
      "const persistedWfNode = new Map<string, string>();",
      "  const [wfProgress, setWfProgress] = useState<number | null>(persistedWfProgress.get(threadId) ?? null);  // 工作流实时进度%（WS，null=无），跨导航持久化",
      "  const [wfNode, setWfNode] = useState<string>(persistedWfNode.get(threadId) ?? \"\");  // 当前执行的节点显示名（WS executing 消息），跨导航持久化",
      "  // 包装 setter：同时写入**当前线程的分片**，确保切换页面回来后进度不丢失",
      "  const updateWfProgress = (v: number | null) => { persistedWfProgress.set(threadId, v); setWfProgress(v); };",
      "  const updateWfNode = (v: string) => { persistedWfNode.set(threadId, v); setWfNode(v); };",
      "  // P18：切线程时把 state 重新对齐到目标线程的分片（否则沿用上一个仓库的进度/节点名）",
      "    setWfProgress(persistedWfProgress.get(threadId) ?? null);",
      "    setWfNode(persistedWfNode.get(threadId) ?? \"\");",
      "  }, [threadId]);",
      "// 注：原 `persistedWfPromptParams` 已删 —— 全仓零引用的死代码（架构审查 P18 硬约束#3）。",
    ]) {
      expect(count(seam, line), line).toBe(1);
    }
    // 重对齐 effect 恰一个（增删 effect 必须同步改本锁）
    expect(count(seam, "useEffect(() => {")).toBe(1);
    // 依赖数组只吃 threadId
    expect(count(seam, "}, [threadId]);")).toBe(1);
    // 零引用死代码不得复活
    expect(stripComments(seam)).not.toMatch(/persistedWfPromptParams/);
  });

  it("视图缺席：视图代码里再也读不到分片符号（分层回退＝红）", () => {
    const view = readSrc(VIEW_TS);
    const code = stripComments(view);
    for (const sym of [
      "persistedWfProgress", "persistedWfNode", "setWfProgress", "setWfNode",
      "new Map<string, number | null>()",
    ]) {
      expect(count(code, sym), sym).toBe(0);
    }
  });

  it("调用点：视图恰一次，且夹在 threadId 定义与 regeneratingIds 之间（hook 调用序＝状态初始化序）", () => {
    const view = readSrc(VIEW_TS);
    const call = "const { wfProgress, wfNode, updateWfProgress, updateWfNode } = useWfProgressShard(threadId);";
    expect(count(view, call)).toBe(1);
    const iThread = view.indexOf("const threadId = threadKey(repo?.id);");
    const iCall = view.indexOf(call);
    const iRegen = view.indexOf("const [regeneratingIds, setRegeneratingIds]");
    expect(iThread).toBeGreaterThan(-1);   // 锚点必须先存在（否则 -1 让断言假通过）
    expect(iRegen).toBeGreaterThan(-1);
    expect(iThread).toBeLessThan(iCall);
    expect(iCall).toBeLessThan(iRegen);
  });

  it("分片读写都带线程键（P18 硬约束：退回单例即跨仓库串）", () => {
    const seam = stripComments(readSrc(SEAM_TS));
    expect(seam).toMatch(/persistedWfProgress\.set\(threadId, v\)/);
    expect(seam).toMatch(/persistedWfNode\.set\(threadId, v\)/);
    expect(seam).toMatch(/persistedWfProgress\.get\(threadId\) \?\? null/);
    expect(seam).toMatch(/persistedWfNode\.get\(threadId\) \?\? ""/);
  });
});
