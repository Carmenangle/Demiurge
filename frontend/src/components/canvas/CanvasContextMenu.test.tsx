// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { CanvasContextMenu } from "./CanvasContextMenu";
import type { CtxMenuState } from "../../lib/useCanvasEventBridge";
import type { Node } from "@xyflow/react";
import type { CardNodeData } from "./CanvasTypes";
import type { InspirationCardStored, NodeLayout, ReferenceImageStored } from "../../lib/canvasLayout";

// T2.1-D2：右键功能栏外移后的回归网（行为网 + 逐字搬移锁 + 源码形态锁）。
// 硬要求：每个用例必须卸载（残留 root 的 window 监听会吃掉后续用例事件）；源码只能在 it 回调内读。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SEAM = "./CanvasContextMenu.tsx";
const VIEW = "../../views/CanvasStageFlow.tsx";

function read(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
}
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").map((l) => l.replace(/(?<!:)\/\/.*$/, "")).join("\n");
}
function count(hay: string, needle: string): number {
  let n = 0;
  let i = 0;
  for (;;) {
    const at = hay.indexOf(needle, i);
    if (at === -1) return n;
    n += 1;
    i = at + needle.length;
  }
}

let container: HTMLDivElement;
let root: Root;

const blank: CtxMenuState = { x: 10, y: 20, nodeIds: [] };
const mkNode = (id: string, over: Record<string, unknown> = {}) =>
  ({ id, position: { x: 0, y: 0 }, data: { node: { type: "image" } }, ...over }) as unknown as Node<CardNodeData>;
const inspCard = { id: "c1", kind: "preset", title: "卡一", content: "内容一" } as InspirationCardStored;

interface Fix {
  ctxMenu?: CtxMenuState;
  rf?: Node<CardNodeData>[];
  cards?: InspirationCardStored[];
  refs?: ReferenceImageStored[];
  layouts?: Record<string, NodeLayout>;
  importBusy?: boolean;
}

async function mountMenu(fix: Fix = {}) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  const mocks = {
    setCtxMenu: vi.fn(),
    setRfNodes: vi.fn(),
    createGroup: vi.fn(),
    setNewInspiration: vi.fn(),
    importInspirationFromLibrary: vi.fn(),
    insertInspirationToChat: vi.fn(),
    showToast: vi.fn(),
    setInspirationEditId: vi.fn(),
    setInspirationEditKind: vi.fn(),
    setInspirationEditTitle: vi.fn(),
    setInspirationEditContent: vi.fn(),
    removeInspiration: vi.fn(),
    setReferenceImages: vi.fn(),
    persistNow: vi.fn(),
    setGroupRenameId: vi.fn(),
    setGroupRenameTitle: vi.fn(),
    setLayoutNodes: vi.fn(),
    requestDelete: vi.fn(),
    openEditLabel: vi.fn(),
  };
  const ctxMenuRef = { current: null as HTMLDivElement | null };
  const rfNodesRef = { current: fix.rf ?? [] };
  const inspirationCardsRef = { current: fix.cards ?? [] };
  const referenceImagesRef = { current: fix.refs ?? [] };
  const layoutNodesRef = { current: fix.layouts ?? {} };
  const el = (
    <CanvasContextMenu
      ctxMenu={fix.ctxMenu ?? blank}
      ctxMenuRef={ctxMenuRef}
      setCtxMenu={mocks.setCtxMenu}
      setRfNodes={mocks.setRfNodes}
      rfNodesRef={rfNodesRef}
      createGroup={mocks.createGroup}
      setNewInspiration={mocks.setNewInspiration}
      importInspirationFromLibrary={mocks.importInspirationFromLibrary}
      importBusy={fix.importBusy ?? false}
      inspirationCardsRef={inspirationCardsRef}
      insertInspirationToChat={mocks.insertInspirationToChat}
      showToast={mocks.showToast}
      setInspirationEditId={mocks.setInspirationEditId}
      setInspirationEditKind={mocks.setInspirationEditKind}
      setInspirationEditTitle={mocks.setInspirationEditTitle}
      setInspirationEditContent={mocks.setInspirationEditContent}
      removeInspiration={mocks.removeInspiration}
      referenceImagesRef={referenceImagesRef}
      setReferenceImages={mocks.setReferenceImages}
      persistNow={mocks.persistNow}
      layoutNodesRef={layoutNodesRef}
      setGroupRenameId={mocks.setGroupRenameId}
      setGroupRenameTitle={mocks.setGroupRenameTitle}
      setLayoutNodes={mocks.setLayoutNodes}
      requestDelete={mocks.requestDelete}
      openEditLabel={mocks.openEditLabel}
    />
  );
  await act(async () => {
    root.render(el as ReactElement);
  });
  return { mocks, ctxMenuRef, rfNodesRef, inspirationCardsRef, referenceImagesRef, layoutNodesRef };
}

async function click(el: Element | null | undefined) {
  await act(async () => {
    (el as HTMLElement | null)?.click();
  });
}
function item(label: string): HTMLElement | null {
  return (Array.from(container.querySelectorAll(".ctx-item")).find(
    (el) => (el.textContent || "").trim() === label,
  ) || null) as HTMLElement | null;
}
function itemText(sub: string): HTMLElement | null {
  return (Array.from(container.querySelectorAll(".ctx-item")).find(
    (el) => (el.textContent || "").includes(sub),
  ) || null) as HTMLElement | null;
}

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container?.remove();
});

// ───────────── 行为网 ─────────────
describe("CanvasContextMenu 空白右键（nodeIds=[]）", () => {
  it("四项菜单 + 位置样式逐字", async () => {
    const { ctxMenuRef } = await mountMenu();
    expect(item("全部取消选择")).toBeTruthy();
    expect(item("选中节点建立组")).toBeTruthy();
    expect(item("新建灵感卡")).toBeTruthy();
    expect(item("从素材库导入灵感卡")).toBeTruthy();
    const menu = container.querySelector("div[style]") as HTMLElement | null;
    expect(menu?.style.zIndex).toBe("40");
    expect(ctxMenuRef.current).toBeInstanceOf(HTMLElement);
  });

  it("「全部取消选择」以函数 updater 清选中态", async () => {
    const { mocks } = await mountMenu();
    await click(item("全部取消选择"));
    expect(mocks.setRfNodes).toHaveBeenCalledTimes(1);
    const updater = mocks.setRfNodes.mock.calls[0][0] as (ns: Node<CardNodeData>[]) => Node<CardNodeData>[];
    const rf = [
      { id: "a", selected: true },
      { id: "b", selected: false },
    ] as unknown as Node<CardNodeData>[];
    expect(updater(rf)).toEqual([{ id: "a", selected: false }, { id: "b", selected: false }]);
  });

  it("「选中节点建立组」传选中节点 id；「新建灵感卡」固定 preset 空表单", async () => {
    const { mocks } = await mountMenu({
      rf: [mkNode("a", { selected: true }), mkNode("b", { selected: true }), mkNode("c", { selected: false })],
    });
    await click(item("选中节点建立组"));
    expect(mocks.createGroup).toHaveBeenCalledWith(["a", "b"]);
    await click(item("新建灵感卡"));
    expect(mocks.setNewInspiration).toHaveBeenCalledWith({ kind: "preset", title: "", content: "" });
  });

  it("「从素材库导入」文案随 importBusy 切换；点击调 importInspirationFromLibrary(false)", async () => {
    const { mocks } = await mountMenu({ importBusy: true });
    expect(item("从素材库导入灵感卡")).toBeNull();
    expect(itemText("导入中…")).toBeTruthy();
    await click(itemText("导入中…"));
    expect(mocks.importInspirationFromLibrary).toHaveBeenCalledWith(false);
  });
});

describe("CanvasContextMenu 单选分支", () => {
  it("灵感卡节点：四项菜单；「插入到对话」→ insertInspirationToChat(卡) + 关菜单", async () => {
    const { mocks } = await mountMenu({
      ctxMenu: { x: 1, y: 2, nodeIds: ["c1"] },
      rf: [mkNode("c1", { data: { node: { type: "inspiration-card" } } })],
      cards: [inspCard],
    });
    expect(item("插入到对话")).toBeTruthy();
    expect(item("复制文本")).toBeTruthy();
    expect(item("编辑标题/内容")).toBeTruthy();
    expect(item("删除灵感卡")).toBeTruthy();
    await click(item("插入到对话"));
    expect(mocks.insertInspirationToChat).toHaveBeenCalledWith(inspCard);
    expect(mocks.setCtxMenu).toHaveBeenCalledWith(null);
  });

  it("灵感卡「复制文本」：clipboard 可用 → writeText + toast success", async () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    try {
      const { mocks } = await mountMenu({
        ctxMenu: { x: 1, y: 2, nodeIds: ["c1"] },
        rf: [mkNode("c1", { data: { node: { type: "inspiration-card" } } })],
        cards: [inspCard],
      });
      await click(item("复制文本"));
      expect(writeText).toHaveBeenCalledWith("内容一");
      expect(mocks.showToast).toHaveBeenCalledWith("内容已复制", "success");
      expect(mocks.setCtxMenu).toHaveBeenCalledWith(null);
    } finally {
      delete (navigator as unknown as { clipboard?: unknown }).clipboard;
    }
  });

  it("灵感卡「编辑标题/内容」四 setter 回填；「删除灵感卡」→ removeInspiration(id)", async () => {
    const { mocks } = await mountMenu({
      ctxMenu: { x: 1, y: 2, nodeIds: ["c1"] },
      rf: [mkNode("c1", { data: { node: { type: "inspiration-card" } } })],
      cards: [inspCard],
    });
    await click(item("编辑标题/内容"));
    expect(mocks.setInspirationEditId).toHaveBeenCalledWith("c1");
    expect(mocks.setInspirationEditKind).toHaveBeenCalledWith("preset");
    expect(mocks.setInspirationEditTitle).toHaveBeenCalledWith("卡一");
    expect(mocks.setInspirationEditContent).toHaveBeenCalledWith("内容一");
    expect(mocks.setCtxMenu).toHaveBeenCalledWith(null);
    await click(item("删除灵感卡"));
    expect(mocks.removeInspiration).toHaveBeenCalledWith("c1");
  });

  it("参考图节点：「编辑标题」kind 固定 preset；「删除参考图」同步 ref + persistNow({refs})", async () => {
    const refs = [{ id: "r1", title: "参考一" }] as ReferenceImageStored[];
    const { mocks, referenceImagesRef } = await mountMenu({
      ctxMenu: { x: 1, y: 2, nodeIds: ["r1"] },
      rf: [mkNode("r1", { data: { node: { type: "reference-image" } } })],
      refs,
    });
    expect(item("编辑标题")).toBeTruthy();
    expect(item("删除参考图")).toBeTruthy();
    await click(item("编辑标题"));
    expect(mocks.setInspirationEditId).toHaveBeenCalledWith("r1");
    expect(mocks.setInspirationEditKind).toHaveBeenCalledWith("preset");
    expect(mocks.setInspirationEditTitle).toHaveBeenCalledWith("参考一");
    await click(item("删除参考图"));
    expect(mocks.setReferenceImages).toHaveBeenCalledWith([]);
    expect(referenceImagesRef.current).toEqual([]);
    expect(mocks.persistNow).toHaveBeenCalledWith({ refs: [] });
    expect(mocks.setCtxMenu).toHaveBeenCalledWith(null);
  });

  it("组节点：「编辑组名」回填 label；「删除组（连带子节点）」→ requestDelete", async () => {
    const { mocks } = await mountMenu({
      ctxMenu: { x: 1, y: 2, nodeIds: ["g1"] },
      rf: [mkNode("g1", { data: { node: { type: "group" } } })],
      layouts: { g1: { label: "组一", x: 0, y: 0 } } as unknown as Record<string, NodeLayout>,
    });
    await click(item("编辑组名"));
    expect(mocks.setGroupRenameId).toHaveBeenCalledWith("g1");
    expect(mocks.setGroupRenameTitle).toHaveBeenCalledWith("组一");
    await click(item("删除组（连带子节点）"));
    expect(mocks.requestDelete).toHaveBeenCalledWith(["g1"]);
    expect(mocks.setCtxMenu).toHaveBeenCalledWith(null);
  });

  it("「删除组（保留内容）」：updater 内断父链转绝对坐标 + persistNow({nodes})", async () => {
    const { mocks } = await mountMenu({
      ctxMenu: { x: 1, y: 2, nodeIds: ["g1"] },
      rf: [
        mkNode("g1", { data: { node: { type: "group" } } }),
        mkNode("c1", { parentId: "g1", position: { x: 5, y: 5 } }),
      ],
      layouts: {
        g1: { label: "组", x: 10, y: 20 },
      } as unknown as Record<string, NodeLayout>,
    });
    await click(item("删除组（保留内容）"));
    expect(mocks.setCtxMenu).toHaveBeenCalledWith(null);
    expect(mocks.setLayoutNodes).toHaveBeenCalledTimes(1);
    const updater = mocks.setLayoutNodes.mock.calls[0][0] as (prev: Record<string, NodeLayout>) => Record<string, NodeLayout>;
    const prev = {
      g1: { label: "组", x: 10, y: 20 },
      c1: { label: "", x: 30, y: 40, parentId: "g1" },
    } as unknown as Record<string, NodeLayout>;
    const next = updater(prev);
    expect("g1" in next).toBe(false);
    expect(next.c1).toEqual({ label: "", x: 40, y: 60, parentId: undefined });
    // ★ persist 在 updater 内拿 next（外层同步读 ref 是旧值）
    expect(mocks.persistNow).toHaveBeenCalledWith({ nodes: next });
  });

  it("其它节点兜底：openEditLabel(id, customLabel||prompt)；「删除节点」→ requestDelete", async () => {
    const { mocks } = await mountMenu({
      ctxMenu: { x: 1, y: 2, nodeIds: ["n1"] },
      rf: [mkNode("n1", { data: { node: { type: "image" }, customLabel: "我的卡" } })],
    });
    await click(item("编辑介绍文字"));
    expect(mocks.openEditLabel).toHaveBeenCalledWith("n1", "我的卡");
    await click(item("删除节点"));
    expect(mocks.requestDelete).toHaveBeenCalledWith(["n1"]);
  });

  it("组节点不进兜底的「编辑介绍文字」", async () => {
    await mountMenu({
      ctxMenu: { x: 1, y: 2, nodeIds: ["g1"] },
      rf: [mkNode("g1", { data: { node: { type: "group" } } })],
    });
    expect(item("编辑介绍文字")).toBeNull();
  });
});

describe("CanvasContextMenu 多选分支", () => {
  it("两项：建立组（N 个节点）+ 一起删除（N）", async () => {
    const { mocks } = await mountMenu({ ctxMenu: { x: 1, y: 2, nodeIds: ["a", "b", "c"] } });
    expect(itemText("建立组（3 个节点）")).toBeTruthy();
    expect(itemText("一起删除（3）")).toBeTruthy();
    await click(itemText("建立组（3 个节点）"));
    expect(mocks.createGroup).toHaveBeenCalledWith(["a", "b", "c"]);
    await click(itemText("一起删除（3）"));
    expect(mocks.requestDelete).toHaveBeenCalledWith(["a", "b", "c"]);
  });
});

describe("CanvasContextMenu 关闭行为", () => {
  it("点击菜单本体 → setCtxMenu(null)（stopPropagation 阻断遮罩链）", async () => {
    const { mocks } = await mountMenu();
    const menu = container.querySelector("div[style]") as HTMLElement;
    await act(async () => {
      menu.click();
    });
    expect(mocks.setCtxMenu).toHaveBeenCalledWith(null);
  });
});

// ───────────── 逐字搬移锁 + 源码形态锁 ─────────────
describe("逐字搬移锁（T2.1-D2 ctxMenu）", () => {
  const MOVED: [string, number][] = [
    ["if (n?.data.node.type === \"inspiration-card\") {", 1],
    ["if (n?.data.node.type === \"reference-image\") {", 1],
    ["if (n?.data.node.type === \"group\") {", 1],
    ["if (n && n.data.node.type !== \"group\") {", 1],
    ["void importInspirationFromLibrary(false)", 1],
    ["if (c) void insertInspirationToChat(c);", 1],
    ["void navigator.clipboard.writeText(c.content);", 1],
    ["persistNow({ refs: next });", 1],
    ["persistNow({ nodes: next });", 1],
    ["const lyt = layoutNodesRef.current[n.id];", 1],
    ["const groupId = ctxMenu.nodeIds[0];", 1],
  ];

  it("11 条判定/副作用语句在新属主按次数出现", () => {
    const seam = stripComments(read(SEAM));
    for (const [needle, n] of MOVED) {
      expect([needle, count(seam, needle)]).toEqual([needle, n]);
    }
  });

  it("同样的语句在视图缺席（确属搬移，而非复制）", () => {
    const view = stripComments(read(VIEW));
    const gone = [
      "if (n?.data.node.type === \"inspiration-card\") {",
      "void importInspirationFromLibrary(false)",
      "全部取消选择", "<X size={13}",
    ];
    for (const needle of gone) {
      expect([needle, count(view, needle)]).toEqual([needle, 0]);
    }
  });
});

describe("源码形态锁（T2.1-D2 ctxMenu）", () => {
  it("视图恰一次使用 + 调用块逐字（26 props 同名接线）", () => {
    const view = read(VIEW);
    expect([count(view, "<CanvasContextMenu"), 1]).toEqual([count(view, "<CanvasContextMenu"), 1]);
    const block = [
      "      {ctxMenu && (",
      "        <CanvasContextMenu",
      "          ctxMenu={ctxMenu}",
      "          ctxMenuRef={ctxMenuRef}",
      "          setCtxMenu={setCtxMenu}",
      "          setRfNodes={setRfNodes}",
      "          rfNodesRef={rfNodesRef}",
      "          createGroup={createGroup}",
      "          setNewInspiration={setNewInspiration}",
      "          importInspirationFromLibrary={importInspirationFromLibrary}",
      "          importBusy={importBusy}",
      "          inspirationCardsRef={inspirationCardsRef}",
      "          insertInspirationToChat={insertInspirationToChat}",
      "          showToast={showToast}",
      "          setInspirationEditId={setInspirationEditId}",
      "          setInspirationEditKind={setInspirationEditKind}",
      "          setInspirationEditTitle={setInspirationEditTitle}",
      "          setInspirationEditContent={setInspirationEditContent}",
      "          removeInspiration={removeInspiration}",
      "          referenceImagesRef={referenceImagesRef}",
      "          setReferenceImages={setReferenceImages}",
      "          persistNow={persistNow}",
      "          layoutNodesRef={layoutNodesRef}",
      "          setGroupRenameId={setGroupRenameId}",
      "          setGroupRenameTitle={setGroupRenameTitle}",
      "          setLayoutNodes={setLayoutNodes}",
      "          requestDelete={requestDelete}",
      "          openEditLabel={openEditLabel}",
      "        />",
      "      )}",
    ].join("\n");
    expect(view.includes(block)).toBe(true);
  });

  it("死 import 已清（lucide 只剩视图自用三个；AudioPlayer/renderMarkdown/runScripts 已随体迁走）", () => {
    const view = read(VIEW);
    expect(view.includes('import { GitBranch, ListOrdered, Sparkles } from "lucide-react";')).toBe(true);
    expect(count(view, 'from "../components/AudioPlayer"')).toBe(0);
    expect(count(view, 'from "../lib/renderMarkdown"')).toBe(0);
    expect(view.includes('import { Placement, type RegexScript } from "../lib/regexEngine";')).toBe(true);
  });
});
