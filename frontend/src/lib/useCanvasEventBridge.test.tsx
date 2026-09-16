// @vitest-environment jsdom
//
// useCanvasEventBridge 的特征网（T2.1-B2 · 2026-09-16）
//
// 背景：`views/CanvasStageFlow.tsx` 里其余 5 处订阅（① 详情 <details> 元素级 toggle / ②
// `laf-generation-saved` + 首次拉对话历史 / ③ `laf-canvas-workflow-tool` / ④ `mousedown` 捕获关闭
// 右键菜单 / ⑤ `CANVAS_INSPIRATION_EVENT` + 挂载消费积压）改造前是 **5 个内联 effect（~100 行）**，
// 已**逐字节搬移**到 `lib/useCanvasEventBridge.ts`。该组件没有组件测试（审计 R5）⇒ 三道网分工：
//
//   ① 行为网（describe「真事件派发」）—— jsdom + React 19 `act` 真挂载、真派发 `window` 事件、
//      真往 DOM 元素派发 `toggle` / `mousedown`，断言**经过 hook 之后**可观测的那一面
//      （队列 / toast 文案 / 卡片 / 展开态 / 订阅是否在场）。这是唯一能同时覆盖
//      「订阅时机 / 解绑 / 元素级委托 / 挂载消费积压」的一层。
//   ② 等价矩阵（describe「等价矩阵」）—— 把改造前的映射与去重写法**逐字抄**成 `legacy*` 参照，
//      在输入网格上断言 hook 实际产出 ≡ 参照产出 ⇒ 可证伪的「零行为变化」。
//   ③ 源码形态锁（describe「源码形态锁」）—— 行为网**抓不到分层回退**（hook 被删后内联回组件照样
//      可能全绿），故另钉：5 个依赖数组行、订阅/解绑配对（含「①刻意不解绑」）、画布侧「每个 hook
//      恰好调用一次」与行数上界。
//
// ⚠ 刻意**不与** `useCanvasWfEvents.test.tsx`（T2.1-B1）重叠：那份管 `laf-canvas-wf-*`，本份管其余五处
//   —— 形态锁之间冗余 ⇒ 一变异多红、丢掉「一变异一红」的诊断力。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { act, useEffect, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { INSPIRATION_CARD_W, INSPIRATION_CARD_H } from "../components/canvas/CanvasTypes";
import type { InspirationCardStored } from "./canvasLayout";
import { CANVAS_INSPIRATION_EVENT, consumePendingInspirations, pushInspirationsToCanvas } from "./inspirationInsert";
import {
  useCanvasGenerationSaved, useCanvasInspirationDrop, useCanvasWorkflowToolCreate,
  useCtxMenuOutsideDismiss, useDetailToggleTracking,
  type CtxMenuState, type PendingToolCreate,
} from "./useCanvasEventBridge";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const TOOL_EVENT = "laf-canvas-workflow-tool";
const SAVED_EVENT = "laf-generation-saved";

type Payload = { id?: string; title?: string; content?: string; imageUrl?: string };
type Spot = { x: number; y: number };
// ⚠ `estimatedNodeCount` 刻意是 `unknown`：等价矩阵要投**非数**（"7" / "abc"）进事件 detail
//   来复现「`Number(...) || 0` 归一」的分支，写死 `number` 会让网格投不进去。
type ToolDetail = { templateId?: string; templateName?: string; estimatedNodeCount?: unknown };

// ─────────────────────────────────────────────────────────────────────────────
// 改造前写法的**逐字抄录**（唯一等价性参照，不得顺手优化）
// ─────────────────────────────────────────────────────────────────────────────

/** 改造前 `onTool` 整段（去重判据 + id 派生 + 队列追加）逐字抄录。 */
const legacyToolOnEvent = (
  detail: ToolDetail | undefined,
  queue: PendingToolCreate[],
  nodeTemplateIds: string[],
): { queue: PendingToolCreate[]; toast?: string } => {
  const templateId = (detail?.templateId || "").trim();
  const templateName = (detail?.templateName || "").trim() || "工作流模板";
  if (!templateId) return { queue };
  const exists = nodeTemplateIds.includes(templateId)
    || queue.some((p) => p.templateId === templateId);
  if (exists) {
    return { queue, toast: `工作流「${templateName}」已在画布上（同模板只保留一张卡片）` };
  }
  const cnt = Number(detail?.estimatedNodeCount) || 0;
  return { queue: [...queue, { id: `wftool-${templateId}`, templateId, templateName, estimatedNodeCount: cnt }] };
};

/** 改造前 `createFromPayloads` 整段（去重 + 落位 + 卡片构造）逐字抄录。
 *  ⚠ 去重只看**批前快照**（`cards`）⇒ 同一批里同 id 的两个 payload 会**各建一张**（既有行为，不修）。 */
const legacyInspirationDrop = (
  items: Payload[],
  cards: InspirationCardStored[],
  spots: Spot[],
  seq: { n: number },
): { fresh: InspirationCardStored[]; next: InspirationCardStored[] | null } => {
  if (!items || items.length === 0) return { fresh: [], next: null };
  const fresh: InspirationCardStored[] = [];
  for (const d of items) {
    const title = (d.title || "").trim() || "灵感卡";
    const content = d.content || "";
    const sid = d.id && d.id.startsWith("insp-") ? d.id : `insp-${d.id || "<rand>"}`;
    const exists = cards.some((c) => c.id === sid);
    if (exists) continue;
    const pos = spots[seq.n++];
    fresh.push({
      id: sid, kind: "preset", title, content,
      imageUrl: d.imageUrl || "",
      x: pos.x, y: pos.y, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H,
    });
  }
  if (fresh.length === 0) return { fresh: [], next: null };
  return { fresh, next: [...cards, ...fresh] };
};

// ─────────────────────────────────────────────────────────────────────────────
// 挂载脚手架：真实组件 + 真实 window/元素事件；setState 用同步写账顶替
// ─────────────────────────────────────────────────────────────────────────────

// ⚠ **每个用例结束必须卸载**：未卸载的 root 会把 window 监听留给下一个用例，
//   于是「派发 → 谁消费」全部串味（首版就是这么踩的：① 断言失败后没走到 unmount，
//   后面 4 个 ⑤ 用例 push 的积压全被上一轮的监听吃掉了 ⇒ 一律 `[]`）。
const teardowns: Array<() => Promise<void>> = [];
async function teardownAll() {
  while (teardowns.length > 0) {
    const t = teardowns.pop()!;
    await t();
  }
}

function makeRig(over: {
  queue?: PendingToolCreate[];
  cards?: InspirationCardStored[];
  nodeTemplateIds?: string[];
  storyText?: string;
} = {}) {
  const state = {
    /** ③ 队列（hook 通过 setPendingToolCreates 追加） */
    queue: (over.queue ?? []).slice(),
    /** ③ 重复派发提示 */
    toasts: [] as Array<{ msg: string; kind?: string }>,
    /** ⑤ 卡片（hook 通过 setInspirationCards 追加） */
    cards: (over.cards ?? []).slice(),
    /** ⑤ 落盘调用 */
    persists: [] as Array<{ cards?: InspirationCardStored[] } | undefined>,
    /** ⑤ 落位调用序（与 legacy 参照共用同一串 spot） */
    spotCalls: 0,
    /** ① 依赖数组里那一项（原 `detailNode?.storyText`） */
    storyTextPeek: over.storyText,
  };
  const spots: Spot[] = [];
  for (let i = 0; i < 40; i++) spots.push({ x: 10 * (i + 1), y: 20 * (i + 1) });

  // ② 记调用序，用来钉「先 load 再 refresh」
  const calls: string[] = [];
  let loadImpl: () => Promise<Set<string>> = async () => new Set(["u1"]);
  const refreshArgs: Array<Set<string> | undefined> = [];

  // ⚠ ref 刻意**不在 setState 当拍同步**（真实 setState 要到下一次渲染才落到 ref）。
  //   同步由 `syncRefs()` 在「一次派发整体结束后」补，见其注释。
  const rfNodesRef = {
    current: (over.nodeTemplateIds ?? []).map((templateId, i) => ({
      id: `n${i}`, data: { node: { type: "workflow-tool", templateId } },
    })),
  };
  const pendingToolCreatesRef: { current: PendingToolCreate[] } = { current: state.queue.slice() };
  const detailHtmlRef: { current: HTMLDivElement | null } = { current: null };
  const detailOpenKeys: { current: Set<string> } = { current: new Set<string>() };
  const ctxMenuRef: { current: HTMLDivElement | null } = { current: null };
  const inspirationCardsRef: { current: InspirationCardStored[] } = { current: state.cards.slice() };

  const ctx = {
    /** setCtxMenu 的**调用记录**（比「渲染日志」更硬：未挂监听时必然是空数组） */
    calls: [] as Array<CtxMenuState | null>,
    /** 菜单当前值（由 Wrapper 每次渲染刷新） */
    value: null as CtxMenuState | null,
    open: (_s: CtxMenuState) => {},
  };

  const setPendingToolCreates: Dispatch<SetStateAction<PendingToolCreate[]>> = (arg) => {
    state.queue = typeof arg === "function"
      ? (arg as (p: PendingToolCreate[]) => PendingToolCreate[])(state.queue)
      : arg;
  };
  const setInspirationCards: Dispatch<SetStateAction<InspirationCardStored[]>> = (arg) => {
    state.cards = typeof arg === "function"
      ? (arg as (p: InspirationCardStored[]) => InspirationCardStored[])(state.cards)
      : arg;
  };
  const showToast = (msg: string, kind?: "info" | "error" | "success") => {
    state.toasts.push({ msg, kind });
  };
  const findFreeSpot = (w: number, h: number) => {
    void w; void h;
    return spots[state.spotCalls++];
  };
  const persistNow = (o?: { cards?: InspirationCardStored[] }) => {
    state.persists.push(o);
  };

  /**
   * 复现组件里「**渲染期**同步 ref」的写法（`CanvasStageFlow.tsx` L281 `inspirationCardsRef.current
   * = inspirationCards`、L394 `pendingToolCreatesRef.current = pendingToolCreates`）。
   *
   * ⚠ 时机很关键：必须等**一次派发整体结束**再调，不能塞进 setState 里——改造前 ⑤ 的
   *   `const next = [...inspirationCardsRef.current, ...fresh]` 读的是**批前快照**，
   *   若 setter 内同步 ref 会把本批新增算两遍（这就是 `syncRefs` 单独暴露的原因）。
   */
  const syncRefs = () => {
    pendingToolCreatesRef.current = state.queue;
    inspirationCardsRef.current = state.cards;
  };

  return {
    state, calls, refreshArgs, spots, syncRefs,
    // ②
    setLoadImpl: (fn: () => Promise<Set<string>>) => { loadImpl = fn; },
    refresh: async (urlsOverride?: Set<string>) => { calls.push("refresh"); refreshArgs.push(urlsOverride); },
    loadHistoryUrls: async () => { calls.push("load"); return loadImpl(); },
    // ③
    rfNodesRef, pendingToolCreatesRef, setPendingToolCreates, showToast,
    // ④
    ctxMenuRef, ctx,
    // ⑤
    detailHtmlRef, detailOpenKeys, inspirationCardsRef, setInspirationCards, findFreeSpot, persistNow,
    // 由 Wrapper 在挂载后接线
    version: (() => {}) as () => void,
  };
}

type Rig = ReturnType<typeof makeRig>;

function Host({ rig, ctxMenu, setCtxMenu }: {
  rig: Rig;
  ctxMenu: CtxMenuState | null;
  setCtxMenu: Dispatch<SetStateAction<CtxMenuState | null>>;
}) {
  useDetailToggleTracking({
    detailHtmlRef: rig.detailHtmlRef as RefObject<HTMLDivElement | null>,
    detailOpenKeys: rig.detailOpenKeys as RefObject<Set<string>>,
    detailStoryText: rig.state.storyTextPeek,
  });
  useCanvasGenerationSaved({ refresh: rig.refresh, loadHistoryUrls: rig.loadHistoryUrls });
  useCanvasWorkflowToolCreate({
    rfNodesRef: rig.rfNodesRef as unknown as RefObject<never>,
    pendingToolCreatesRef: rig.pendingToolCreatesRef,
    setPendingToolCreates: rig.setPendingToolCreates,
    showToast: rig.showToast,
  });
  useCtxMenuOutsideDismiss({
    ctxMenu,
    ctxMenuRef: rig.ctxMenuRef,
    setCtxMenu,
  });
  useCanvasInspirationDrop({
    inspirationCardsRef: rig.inspirationCardsRef,
    setInspirationCards: rig.setInspirationCards,
    findFreeSpot: rig.findFreeSpot,
    persistNow: rig.persistNow,
    showToast: rig.showToast,
  });
  return (
    <>
      <div ref={rig.detailHtmlRef} data-detail="1">
        <details><summary>思考过程</summary><p>正文</p></details>
      </div>
      <div ref={rig.ctxMenuRef} data-ctxmenu="1" />
    </>
  );
}

function Wrapper({ rig }: { rig: Rig }) {
  const [version, setVersion] = useState(0);
  const [ctxMenu, setCtxMenu] = useState<CtxMenuState | null>(null);
  void version;
  useEffect(() => {
    rig.ctx.value = ctxMenu;
    rig.ctx.open = (s: CtxMenuState) => setCtxMenu(s);
    rig.version = () => setVersion((n) => n + 1);
  });
  // setCtxMenu 包一层记账：未挂监听时「一次都没被调」才是硬证据
  const trackedSetCtxMenu: Dispatch<SetStateAction<CtxMenuState | null>> = (arg) => {
    rig.ctx.calls.push(typeof arg === "function" ? null : arg);
    setCtxMenu(arg);
  };
  return <Host rig={rig} ctxMenu={ctxMenu} setCtxMenu={trackedSetCtxMenu} />;
}

async function mount(rig: Rig) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let down = false;
  const shutdown = async () => {
    if (down) return;
    down = true;
    await act(async () => { root.unmount(); });
    host.remove();
  };
  teardowns.push(shutdown);

  await act(async () => { root.render(<Wrapper rig={rig} />); });
  rig.syncRefs();                                   // 挂载后的第一次「渲染期同步」

  return {
    async fire(name: string, detail?: unknown) {
      await act(async () => { window.dispatchEvent(new CustomEvent(name, { detail })); });
      rig.syncRefs();
    },
    async push(items: Payload[]) {
      await act(async () => { pushInspirationsToCanvas(items); });
      rig.syncRefs();
    },
    async fireOn(el: EventTarget, e: Event) {
      await act(async () => { el.dispatchEvent(e); });
      rig.syncRefs();
    },
    async rerender() {
      await act(async () => { rig.version(); });
      rig.syncRefs();
    },
    async openMenu(s: CtxMenuState) {
      await act(async () => { rig.ctx.open(s); });
      rig.syncRefs();
    },
    unmount: shutdown,
  };
}

const card = (over: Partial<InspirationCardStored> = {}): InspirationCardStored => ({
  id: "insp-seed", kind: "preset", title: "种子", content: "c",
  imageUrl: "", x: 0, y: 0, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H, ...over,
});

beforeEach(() => {
  consumePendingInspirations();          // 清掉模块级积压，避免跨用例串味
});
afterEach(async () => {
  await teardownAll();                   // 卸载所有 root ⇒ window 监听一并撤掉
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  consumePendingInspirations();
});

// ─────────────────────────────────────────────────────────────────────────────
describe("真事件派发（jsdom：挂载 + 真派发 window / 元素事件）", () => {
  it("① 详情 <details>：绑定一次 + 元素级 toggle 委托（open 记录 / close 清除）", async () => {
    const spy = vi.spyOn(HTMLElement.prototype, "addEventListener");
    const rig = makeRig();
    const h = await mount(rig);
    const el = rig.detailHtmlRef.current!;
    const details = el.querySelector("details")!;

    expect(el.dataset.detailsBound).toBe("1");
    // ⚠ 只数「落在**本容器**上的 toggle」：React 19 自己也会给 root 容器挂 toggle 监听，
    //   全量计数会数到 3（首版实测）⇒ 必须按 this 收窄，否则红检连 React 的内部实现一起数。
    const toggleBinds = () =>
      spy.mock.calls.filter((c, i) => c[0] === "toggle" && spy.mock.contexts[i] === el).length;
    expect(toggleBinds()).toBe(1);

    // 子元素派发 toggle → 捕获阶段在容器上被委托到（这就是改造前那条 addEventListener 的真实作用）
    expect(rig.detailOpenKeys.current.size).toBe(0);
    details.open = true;
    await h.fireOn(details, new Event("toggle"));
    expect([...rig.detailOpenKeys.current]).toEqual(["思考过程"]);

    details.open = false;
    await h.fireOn(details, new Event("toggle"));
    expect([...rig.detailOpenKeys.current]).toEqual([]);

    // 依赖数组真的变了 ⇒ layout effect 重跑；dataset 守卫 ⇒ 不再绑第二条
    rig.state.storyTextPeek = "正文变了";
    await h.rerender();
    expect(toggleBinds()).toBe(1);
    await h.unmount();
  });

  it("① 重渲染：deps 不变不重跑；deps 变才按 store 恢复展开态（innerHTML 重写后不塌陷）", async () => {
    const rig = makeRig();
    const h = await mount(rig);
    const details = rig.detailHtmlRef.current!.querySelector("details")!;
    rig.detailOpenKeys.current.add("思考过程");     // 模拟「上一轮展开过」

    details.open = false;
    await h.rerender();                             // storyText 未变 ⇒ layout effect 不重跑
    expect(details.open).toBe(false);               // ← 钉住依赖数组就是重订阅/重恢复时机

    rig.state.storyTextPeek = "正文变了";
    await h.rerender();                             // deps 变 ⇒ 重跑 → restoreOpenDetails
    expect(details.open).toBe(true);
    await h.unmount();
  });

  it("② 挂载先拉对话历史再投影；事件触发 refresh() + 重拉历史", async () => {
    const rig = makeRig();
    const h = await mount(rig);
    expect(rig.calls).toEqual(["load", "refresh"]);
    expect([...rig.refreshArgs[0]!]).toEqual(["u1"]);

    await h.fire(SAVED_EVENT);
    expect(rig.calls).toEqual(["load", "refresh", "refresh", "load"]);
    expect(rig.refreshArgs[1]).toBeUndefined();     // 事件路径不传 urls（走 conversationUrls）
    await h.unmount();
  });

  it("② 卸载在首次拉取 resolve 之前 ⇒ refresh 不再被调用（alive 护住挂载路径）", async () => {
    let release: (v: Set<string>) => void = () => {};
    const rig = makeRig();
    rig.setLoadImpl(() => new Promise<Set<string>>((res) => { release = res; }));
    const h = await mount(rig);
    expect(rig.calls).toEqual(["load"]);
    await h.unmount();
    await act(async () => { release(new Set(["late"])); });
    expect(rig.calls).toEqual(["load"]);            // 没有第二个 "refresh"
  });

  it("④ 菜单没开时不挂监听（点哪都不关）；菜单开着时点外关闭、点内不关", async () => {
    const rig = makeRig();
    const h = await mount(rig);

    await h.fireOn(document.body, new MouseEvent("mousedown", { bubbles: true }));
    expect(rig.ctx.calls).toEqual([]);              // 未开菜单 ⇒ setCtxMenu 一次都没被调

    await h.openMenu({ x: 3, y: 4, nodeIds: ["a"] });
    expect(rig.ctx.value).toEqual({ x: 3, y: 4, nodeIds: ["a"] });

    await h.fireOn(rig.ctxMenuRef.current!, new MouseEvent("mousedown", { bubbles: true }));
    expect(rig.ctx.calls).toEqual([]);              // 点菜单内部 → 不关（仍未调用过 setter）

    await h.fireOn(document.body, new MouseEvent("mousedown", { bubbles: true }));
    expect(rig.ctx.calls).toEqual([null]);          // 点外部 → 关一次
    expect(rig.ctx.value).toBeNull();
    await h.unmount();
  });

  it("⑤ 挂载时先消费积压（卡片 + 落盘 + 提示各一次）", async () => {
    pushInspirationsToCanvas([{ id: "insp-a", title: "女仆装", content: "x" }]);
    const rig = makeRig();
    const h = await mount(rig);

    expect(rig.state.cards.map((c) => c.id)).toEqual(["insp-a"]);
    expect(rig.state.cards[0]).toEqual({
      id: "insp-a", kind: "preset", title: "女仆装", content: "x", imageUrl: "",
      x: 10, y: 20, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H,
    });
    expect(rig.state.persists).toEqual([{ cards: rig.state.cards }]);
    expect(rig.state.toasts).toEqual([{ msg: "已发送 1 张灵感卡到画布", kind: "success" }]);
    await h.unmount();
  });

  it("⑤ 事件增量消费；缓存空则早退（不落盘、不提示）", async () => {
    const rig = makeRig();
    const h = await mount(rig);
    await h.fire(CANVAS_INSPIRATION_EVENT);         // 缓存空 ⇒ 无事发生
    expect(rig.state.persists).toHaveLength(0);
    expect(rig.state.toasts).toHaveLength(0);

    await h.push([{ id: "insp-b", title: "T", content: "" }]);
    expect(rig.state.cards.map((c) => c.id)).toEqual(["insp-b"]);
    expect(rig.state.persists).toHaveLength(1);
    await h.unmount();
  });

  it("⑤ 派生 id 已存在 ⇒ 整批跳过（不落盘）；无 id 用随机 8 位兜底", async () => {
    pushInspirationsToCanvas([{ id: "insp-dup", title: "重复", content: "" }]);
    const rig = makeRig({ cards: [card({ id: "insp-dup" })] });
    const h = await mount(rig);
    expect(rig.state.cards).toHaveLength(1);
    expect(rig.state.persists).toHaveLength(0);
    expect(rig.state.toasts).toHaveLength(0);

    await h.push([{ title: "无 id" }]);
    const added = rig.state.cards[1];
    expect(added.id).toMatch(/^insp-[0-9a-f]{8}$/);
    expect(added.title).toBe("无 id");
    await h.unmount();
  });

  it("③ 有效派发建卡（id 稳定派生）；templateId 空/纯空白早退；重复派发只提示不建卡", async () => {
    const rig = makeRig();
    const h = await mount(rig);

    await h.fire(TOOL_EVENT);
    await h.fire(TOOL_EVENT, { templateId: "" });
    await h.fire(TOOL_EVENT, { templateId: "   " });
    expect(rig.state.queue).toHaveLength(0);
    expect(rig.state.toasts).toHaveLength(0);

    await h.fire(TOOL_EVENT, { templateId: " t1 ", templateName: " 双人对话 ", estimatedNodeCount: 7 });
    expect(rig.state.queue).toEqual([
      { id: "wftool-t1", templateId: "t1", templateName: "双人对话", estimatedNodeCount: 7 },
    ]);
    expect(rig.state.toasts).toHaveLength(0);       // 与 legacy 一致：提示只在「已存在」分支

    await h.fire(TOOL_EVENT, { templateId: "t1", templateName: "双人对话" });
    expect(rig.state.queue).toHaveLength(1);
    expect(rig.state.toasts).toEqual([
      { msg: "工作流「双人对话」已在画布上（同模板只保留一张卡片）" },
    ]);
    await h.unmount();
  });

  it("③ 画布上已有同模板卡 ⇒ 只提示；estimatedNodeCount 非数 ⇒ 0；templateName 空白 ⇒ 默认名", async () => {
    const rig = makeRig({ nodeTemplateIds: ["t9"] });
    const h = await mount(rig);
    await h.fire(TOOL_EVENT, { templateId: "t9", templateName: "已在画布" });
    expect(rig.state.queue).toHaveLength(0);
    expect(rig.state.toasts).toEqual([
      { msg: "工作流「已在画布」已在画布上（同模板只保留一张卡片）" },
    ]);

    await h.fire(TOOL_EVENT, { templateId: "t2", templateName: "  ", estimatedNodeCount: "abc" });
    expect(rig.state.queue).toEqual([
      { id: "wftool-t2", templateId: "t2", templateName: "工作流模板", estimatedNodeCount: 0 },
    ]);
    await h.unmount();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("等价矩阵：hook 产出 ≡ 改造前手写写法（逐点相等）", () => {
  it("③ onTool 全分支 × 72 组输入网格", async () => {
    const TEMPLATE_IDS = ["", "  ", "t1", " t1 "];
    const TEMPLATE_NAMES: Array<string | undefined> = ["", "T"];
    const COUNTS: Array<number | string | undefined> = [undefined, "7", "abc"];
    const DUPS = ["none", "node", "queue"] as const;

    let checked = 0;
    for (const templateId of TEMPLATE_IDS) {
      for (const templateName of TEMPLATE_NAMES) {
        for (const estimatedNodeCount of COUNTS) {
          for (const dup of DUPS) {
            const seed: PendingToolCreate[] = dup === "queue"
              ? [{ id: "wftool-t1", templateId: "t1", templateName: "已有", estimatedNodeCount: 1 }]
              : [];
            const nodeIds = dup === "node" ? ["t1"] : [];
            const rig = makeRig({ queue: seed, nodeTemplateIds: nodeIds });
            const h = await mount(rig);
            const detail: ToolDetail = { templateId, templateName, estimatedNodeCount };
            await h.fire(TOOL_EVENT, detail);

            const exp = legacyToolOnEvent(detail, seed, nodeIds);
            expect(rig.state.queue, JSON.stringify({ templateId, templateName, estimatedNodeCount, dup }))
              .toEqual(exp.queue);
            expect(rig.state.toasts.map((t) => t.msg)).toEqual(exp.toast ? [exp.toast] : []);
            await h.unmount();
            checked += 1;
          }
        }
      }
    }
    expect(checked).toBe(72);
  });

  it("⑤ createFromPayloads 全分支 × 24 组输入网格", async () => {
    const TITLES: Array<string | undefined> = ["", "  ", "T"];
    const IDS = ["a", "insp-b"];
    const EXISTING = [[], [card({ id: "insp-b" })]];
    const LENS = [1, 2];

    let checked = 0;
    for (const title of TITLES) {
      for (const id of IDS) {
        for (const existing of EXISTING) {
          for (const len of LENS) {
            const items: Payload[] = Array.from({ length: len }, () => ({ id, title, content: "c" }));
            const rig = makeRig({ cards: existing });
            const h = await mount(rig);
            await h.push(items);

            const exp = legacyInspirationDrop(items, existing, rig.spots, { n: 0 });
            expect(rig.state.cards, JSON.stringify({ title, id, len, existing: existing.length }))
              .toEqual(exp.next ?? existing);
            expect(rig.state.persists).toEqual(exp.next ? [{ cards: exp.next }] : []);
            expect(rig.state.toasts.map((t) => t.msg))
              .toEqual(exp.fresh.length > 0 ? [`已发送 ${exp.fresh.length} 张灵感卡到画布`] : []);
            await h.unmount();
            checked += 1;
          }
        }
      }
    }
    expect(checked).toBe(24);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
describe("源码形态锁（行为网抓不到的分层回退）", () => {
  const read = (rel: string) =>
    readFileSync(new URL(rel, import.meta.url), "utf-8").replace(/\r\n/g, "\n");
  /**
   * 去注释 ⇒ 形态锁**只看代码**。否则：① 注释里提一句 `window.addEventListener(...)` 就被当成
   * 「还在订阅」；② 把 hook 调用**注释掉**仍满足「调用恰一次」——两向都是假绿/假红。
   * ⚠ 只删**整行注释**与块注释，不删行尾 `//`：源码里有 `http://…` 这类字符串，
   *   粗暴截断会把同一行后面的代码吞掉（假绿）。
   */
  const stripComments = (s: string) =>
    s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "");
  const canvasRaw = read("../views/CanvasStageFlow.tsx");
  const moduleSrc = stripComments(read("./useCanvasEventBridge.ts"));
  const canvasSrc = stripComments(canvasRaw);

  it("五个 effect（1 layout + 4 effect），依赖数组逐字保持改造前的值（重订阅时机＝行为面）", () => {
    expect(moduleSrc.match(/useLayoutEffect\(\(\) => \{/g)).toHaveLength(1);
    expect(moduleSrc.match(/useEffect\(\(\) => \{/g)).toHaveLength(4);
    const deps = [
      "}, [detailStoryText]);",
      "}, [refresh, loadHistoryUrls]);",
      "}, []);",
      "}, [ctxMenu]);",
      "}, [findFreeSpot, persistNow]);",
    ];
    for (const line of deps) {
      const hits = moduleSrc.split("\n").filter((l: string) => l.trim() === line.trim()).length;
      expect(hits, line).toBe(1);
    }
  });

  it("订阅/解绑配对不串；① 的元素级 toggle 刻意不解绑", () => {
    expect(moduleSrc.match(/window\.addEventListener\(/g)).toHaveLength(4);
    expect(moduleSrc.match(/window\.removeEventListener\(/g)).toHaveLength(4);
    const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // ⚠ ⑤ 用的是**常量名**（`CANVAS_INSPIRATION_EVENT`）⇒ 锁必须按标识符匹配，
    //   常量的字面值另行在 `inspirationInsert.ts` 与运行时两处钉住，否则就是一把空锁。
    const spec: Array<[string, string, string]> = [
      ['"laf-generation-saved"', "", "②"],
      ['"laf-canvas-workflow-tool"', "", "③"],
      ['"mousedown"', ", true", "④"],                     // ④ 是捕获阶段
      ["CANVAS_INSPIRATION_EVENT", "", "⑤"],
    ];
    for (const [lit, flag, label] of spec) {
      const added = moduleSrc.match(
        new RegExp(`window\\.addEventListener\\(${esc(lit)}, (\\w+)${esc(flag)}\\)`),
      );
      expect(added, `${label} add`).not.toBeNull();
      expect(moduleSrc, `${label} remove`)
        .toContain(`window.removeEventListener(${lit}, ${added![1]}${flag})`);
    }
    expect(CANVAS_INSPIRATION_EVENT).toBe("laf-inspiration-to-canvas");
    expect(read("./inspirationInsert.ts"))
      .toContain('CANVAS_INSPIRATION_EVENT = "laf-inspiration-to-canvas"');
    expect(moduleSrc.match(/el\.addEventListener\("toggle"/g)).toHaveLength(1);
    expect(moduleSrc).not.toMatch(/removeEventListener\("toggle"/);
  });

  it("画布侧五个 hook 各恰调用一次，且不再自己订阅这五个事件", () => {
    for (const hook of [
      "useDetailToggleTracking(", "useCanvasGenerationSaved(", "useCanvasWorkflowToolCreate(",
      "useCtxMenuOutsideDismiss(", "useCanvasInspirationDrop(",
    ]) {
      expect(canvasSrc.match(new RegExp(hook.replace("(", "\\("), "g")), hook).toHaveLength(1);
    }
    for (const gone of [
      'addEventListener("laf-generation-saved"',
      'addEventListener("laf-canvas-workflow-tool"',
      'addEventListener("mousedown"',
      "addEventListener(CANVAS_INSPIRATION_EVENT",
      'el.addEventListener("toggle"',
    ]) {
      expect(canvasSrc, gone).not.toContain(gone);
    }
    // 订阅与消费整体外移 ⇒ 这两个模块不该再被画布引用
    expect(canvasSrc).not.toContain('from "../lib/stableDetailsOpen"');
    expect(canvasSrc).not.toContain('from "../lib/inspirationInsert"');
  });

  it("画布侧不得再出现「只属于已搬走逻辑」的语句（闭包锁）", () => {
    for (const token of [
      "const onSaved = () => {",
      "const onTool = (e: Event)",
      "const onDown = (e: MouseEvent)",
      "const createFromPayloads = (",
      "const onInsp = () =>",
      "restoreOpenDetails(",
      "trackDetailsToggle(",
      "consumePendingInspirations()",
      "`wftool-${templateId}`",
      'd.id.startsWith("insp-")',
    ]) {
      expect(canvasSrc, token).not.toContain(token);
    }
  });

  it("画布文件没有把这五处订阅长回去（行数上界；内联回退会让它涨 90+ 行）", () => {
    const lines = canvasRaw.split("\n").length - 1;   // 行数用原文（去注释会少算块注释行）
    expect(lines).toBeLessThanOrEqual(3120);
  });
});
