// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import {
  buildCanvasEdges,
  buildContentNodes,
  buildInspirationNodes,
  buildReferenceImageNodes,
  buildStoryNodes,
  buildToolCards,
  mergeProjectedNodes,
  type CanvasEdgesDeps,
  type ContentNodesDeps,
  type InspirationNodesDeps,
  type ReferenceImageNodesDeps,
  type StoryNodesDeps,
  type ToolCardsDeps,
} from "./canvasProjection";
import { CARD_W, INSPIRATION_CARD_W, INSPIRATION_CARD_H, type CardNodeData } from "../components/canvas/CanvasTypes";
import type { CanvasNode, GenLike } from "./canvasRuntime";
import type { InspirationCardStored, NodeLayout, ReferenceImageStored } from "./canvasLayout";
import type { ChatMessage } from "../types/chat";

/**
 * 画布「节点投影 / 连线派生」builder 的特征测试（2026-09-16 · T2.1 Stage C）。
 *
 * 背景：`views/CanvasStageFlow.tsx` 的投影 useEffect 在 T2.1-A/B2 之后仍有 **498 行**，
 * 其中 7 块是「把 state 映射成 ReactFlow nodes/edges」的构造与判定。按架构审计的
 * 「投影 builder 外移」处置，它们收口到 `lib/canvasProjection.ts`。
 *
 * 三道网（沿用 T1.1–T1.3 / A / B2 已验证手法）：
 *   ① **逐字搬移锁**——7 段代码**由生成脚本从改动前视图逐行提取**（机器判定：原段落每一条
 *      代码行在写盘后的新属主里次数一致）。搬移是「机器逐字搬运」而非「人肉重打」，
 *      故等价矩阵在此退化为同义反复（拿副本对副本），改以 **机器搬移锁 + 直断言行为网**
 *      两件互补证据承担「零行为变化可证伪」。
 *   ② **行为网（直断言）**——每个 builder 覆盖决策分支（去重优先级 / 三源合并 / 接替锚点 /
 *      五类双击分派 / 合并去重语义 / 连线过滤），断言**具体值**而非「两侧同为空」。
 *   ③ **源码形态锁**——钉「effect 由 7 个 builder 组装」（可达性闭包 + 内联回退 + 行数 +
 *      依赖数组逐字不变 + 属主唯一），这三件行为网测不出。
 */

/**
 * ⚠ 源码必须在 **it 回调内**读：jsdom 环境下模块顶层求值时 `import.meta.url` 不是 `file:`
 *   协议（`readFileSync` 直接抛 `The URL must be of scheme file`），测试执行期才是 file URL。
 *   故这里只暴露 `read(rel)`，调用点放在 it 内。
 */
const read = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8").replace(/\r\n/g, "\n");
/** 去注释（锁只钉代码；注释里提事件名/语句名会算「还在」——B2 教训）。 */
const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "");
/** 去注释 + 去缩进 + 丢空行 ⇒ 逐行比对的形态（锁的统一输入）。 */
const codeLines = (s: string) => stripComments(s).split("\n")
  // 行尾注释也要去掉（否则 `continue; // 说明` 与锁里的纯代码串对不上）；
  // `(?<!:)` 保住 `http://` 这类字符串里的双斜杠
  .map((l) => l.replace(/(?<!:)\/\/.*$/, "").trim())
  .filter((l) => l.length > 0);
/** 比较用归一化：回调统一烙成 "[fn]"（函数本身不可比），其余字段逐字段比。 */
const strip = (v: unknown): unknown =>
  JSON.parse(JSON.stringify(v, (_k, val) => (typeof val === "function" ? "[fn]" : val)));

const ref = <T,>(v: T) => ({ current: v });
const rectRef = () => ({
  current: { getBoundingClientRect: () => ({ left: 100, top: 50 }) } as unknown as HTMLDivElement,
});
const msg = (id: string, role: "user" | "assistant", text: string, extra: Partial<ChatMessage> = {}): ChatMessage =>
  ({ id, role, text, ...extra }) as ChatMessage;
const cn = (over: Partial<CanvasNode> & { id: string }): CanvasNode =>
  ({ type: "image", x: 0, y: 0, w: 200, h: 150, generationIds: [], ...over }) as CanvasNode;
const gen = (id: string, image_url: string, prompt = "P"): GenLike => ({ id, prompt, image_url });
const node = (over: Partial<Node<CardNodeData>> & { id: string }): Node<CardNodeData> =>
  ({ position: { x: 0, y: 0 }, data: { node: cn({ id: over.id }) }, ...over }) as unknown as Node<CardNodeData>;

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** 详情/弹窗类 setter 的公共夹具（各 builder 的 rig 复用）。 */
function modalSpies() {
  return {
    setDetailNode: vi.fn(), setInspirationEditId: vi.fn(), setInspirationEditKind: vi.fn(),
    setInspirationEditTitle: vi.fn(), setInspirationEditContent: vi.fn(),
    setWbSeedFrom: vi.fn(), setWbRepoLoc: vi.fn(), setWbPopupTitle: vi.fn(),
    setCharModal: vi.fn(), setPresetModalOpen: vi.fn(),
  };
}

const settings = { comfyuiUrl: "", outputDir: "/out", worldbookDir: "/wb", characterDir: "/char", embedModel: "", proxyUrl: "", systemProxyUrl: "", proxyEnabled: false, activePresetName: "" } as unknown as ToolCardsDeps["settings"];

// ── ① buildContentNodes：生成内容节点 ─────────────────────────────────────────
function contentRig(over: Partial<ContentNodesDeps> = {}) {
  return {
    rawNodes: [] as CanvasNode[],
    layoutNodes: {} as Record<string, NodeLayout>,
    contentPlacement: null,
    byGroup: new Map<string, GenLike[]>(),
    naturalSizesRef: ref<Record<string, { w: number; h: number }>>({}),
    layoutNodesRef: ref<Record<string, NodeLayout>>({}),
    stageWrapRef: rectRef(),
    openCtxForNode: vi.fn(),
    persistNow: vi.fn(),
    setLayoutNodes: vi.fn(),
    setRfNodes: vi.fn(),
    ...modalSpies(),
    ...over,
  } as ContentNodesDeps & ReturnType<typeof modalSpies> & {
    openCtxForNode: ReturnType<typeof vi.fn>; persistNow: ReturnType<typeof vi.fn>;
    setLayoutNodes: ReturnType<typeof vi.fn>; setRfNodes: ReturnType<typeof vi.fn>;
  };
}

describe("① buildContentNodes：坐标来源 / 组关系 / 尺寸锁 / 回调语义", () => {
  it("空输入 → 空数组；无布局无锚点 → 用节点自身坐标、无 parentId、无 style（高度自适应）", () => {
    expect(buildContentNodes(contentRig())).toEqual([]);

    const rig = contentRig({
      rawNodes: [cn({ id: "g1", x: 11, y: 22 })],
      byGroup: new Map([["g1", [gen("x1", "u1")]]]),
    });
    const [n] = buildContentNodes(rig);
    expect(n.id).toBe("g1");
    expect(n.type).toBe("card");
    expect(n.position).toEqual({ x: 11, y: 22 });
    expect(n.parentId).toBeUndefined();
    expect(n.style).toBeUndefined();              // 非 custom → 不写 style（高度自适应）
    expect(n.selected).toBe(false);
    expect(n.data.gens).toHaveLength(1);
    expect(n.data.imageUrls).toEqual(["u1"]);
    expect(n.data.prompt).toBe("P");              // 非 input → 取首个 generation 的 prompt
    expect(n.data.customLabel).toBe("");
    expect(n.data.customSize).toBe(false);
    expect(n.data.node.id).toBe("g1");            // data.node 即投影源节点
  });

  it("持久化布局优先于锚点网格；parentId/custom/label 三处派生", () => {
    const rig = contentRig({
      rawNodes: [cn({ id: "g1", x: 11, y: 22 }), cn({ id: "n1", x: 1, y: 2, w: 300, h: 90 })],
      layoutNodes: {
        g1: { x: 100, y: 200, w: 240, h: 300, custom: true, label: "介绍", parentId: "grp" },
        n1: { x: 7, y: 8, w: 1, h: 1 },             // 有布局（未 custom）→ 不再用 contentPlacement
      },
      contentPlacement: { map: { n1: { x: 555, y: 666 }, g1: { x: 999, y: 999 } } },
    });
    const got = buildContentNodes(rig);
    const byId = Object.fromEntries(got.map((n) => [n.id, n]));

    expect(byId.g1.position).toEqual({ x: 100, y: 200 });
    expect(byId.g1.parentId).toBe("grp");           // 组内子节点：相对坐标 + parentId
    expect(byId.g1.extent).toBe("parent");
    expect(byId.g1.style).toEqual({ width: 240, height: 300 });
    expect(byId.g1.data.customLabel).toBe("介绍");
    expect(byId.g1.data.customSize).toBe(true);

    expect(byId.n1.position).toEqual({ x: 7, y: 8 }); // 布局优先，锚点网格不参与
    expect(byId.n1.style).toBeUndefined();

    // 无布局 + 有锚点 → 用锚点坐标，尺寸仍取节点自身 w/h
    const rig2 = contentRig({
      rawNodes: [cn({ id: "n1", x: 1, y: 2, w: 300, h: 90 })],
      contentPlacement: { map: { n1: { x: 55, y: 66 } } },
    });
    expect(buildContentNodes(rig2)[0].position).toEqual({ x: 55, y: 66 });
  });

  it("input 节点 prompt 取自身 prompt；双击语义分流（input → 新建灵感卡，内容 → 详情）", () => {
    const inputNode = cn({ id: "i1", type: "input", input: true, prompt: "画猫" });
    const rig = contentRig({ rawNodes: [inputNode, cn({ id: "c1" })] });
    const got = buildContentNodes(rig);
    expect(got[0].data.prompt).toBe("画猫");

    // 双击 input → 走「新建灵感卡」预置（不是详情面板）
    (got[0].data.onOpen as (n: CanvasNode) => void)(inputNode);
    expect(rig.setInspirationEditId).toHaveBeenCalledWith("new");
    expect(rig.setInspirationEditKind).toHaveBeenCalledWith("preset");
    expect(rig.setInspirationEditTitle).toHaveBeenCalledWith("画猫");
    expect(rig.setInspirationEditContent).toHaveBeenCalledWith("画猫");
    expect(rig.setDetailNode).not.toHaveBeenCalled();

    // 双击内容节点 → 详情面板
    const c1 = cn({ id: "c1" });
    (got[1].data.onOpen as (n: CanvasNode) => void)(c1);
    expect(rig.setDetailNode).toHaveBeenCalledWith(c1);
  });

  it("input 节点 prompt 为空串时也原样回填（不吞空值）", () => {
    const rig = contentRig({ rawNodes: [cn({ id: "i1", type: "input", input: true })] });
    const got = buildContentNodes(rig);
    expect(got[0].data.prompt).toBe("");
    (got[0].data.onOpen as (n: CanvasNode) => void)(cn({ id: "i1", type: "input", input: true }));
    expect(rig.setInspirationEditTitle).toHaveBeenCalledWith("");
  });

  it("右键坐标扣掉 stageWrap 偏移；拉伸中只改 style，拉伸结束才落布局 + 落盘", () => {
    const rig = contentRig({ rawNodes: [cn({ id: "g1" })] });
    const [n] = buildContentNodes(rig);

    const ev = { clientX: 130, clientY: 80 } as React.MouseEvent;
    n.data.onNodeCtx(ev, n.data.node);
    expect(rig.openCtxForNode).toHaveBeenCalledWith(30, 30, "g1"); // 130-100 / 80-50

    // 拉伸中：函数式更新 rfNodes（只动这一个节点的 style/customSize）
    const prev = [node({ id: "g1" }), node({ id: "other" })];
    (n.data.onResize as (id: string, w: number, h: number) => void)("g1", 321, 123);
    const updater = rig.setRfNodes.mock.calls[0][0] as (p: Node<CardNodeData>[]) => Node<CardNodeData>[];
    const after = updater(prev);
    expect(after[0].style).toEqual({ width: 321, height: 123 });
    expect(after[0].data.customSize).toBe(true);
    expect(after[1]).toBe(prev[1]);               // 其它节点引用不变

    // 拉伸结束：写 layoutNodes（custom:true）+ persistNow
    (n.data.onResizeEnd as (id: string, w: number, h: number) => void)("g1", 321, 123);
    const next = rig.setLayoutNodes.mock.calls[0][0] as Record<string, NodeLayout>;
    expect(next.g1.custom).toBe(true);
    expect(next.g1.w).toBe(321);
    expect(next.g1.h).toBe(123);
    expect(rig.persistNow).toHaveBeenCalledWith({ nodes: next });
  });

  it("图片原尺寸去重：同尺寸不触发重渲染，新尺寸写 ref 并只更新目标节点", () => {
    const naturalSizesRef = ref<Record<string, { w: number; h: number }>>({ u1: { w: 10, h: 20 } });
    const rig = contentRig({
      rawNodes: [cn({ id: "g1" })],
      byGroup: new Map([["g1", [gen("x1", "u1")]]]),
      naturalSizesRef,
    });
    const [n] = buildContentNodes(rig);
    expect(n.data.naturalSize).toEqual({ w: 10, h: 20 });

    // 同尺寸 → 直接 return，不进 setRfNodes
    n.data.onImgLoaded("u1", 10, 20);
    expect(rig.setRfNodes).not.toHaveBeenCalled();

    // 新尺寸 → 写 ref + 只更新 image_url 命中的节点
    n.data.onImgLoaded("u1", 33, 44);
    expect(naturalSizesRef.current.u1).toEqual({ w: 33, h: 44 });
    const updater = rig.setRfNodes.mock.calls[0][0] as (p: Node<CardNodeData>[]) => Node<CardNodeData>[];
    const hit = { ...node({ id: "g1" }), data: { ...node({ id: "g1" }).data, gens: [gen("x1", "u1")] } };
    const miss = { ...node({ id: "z" }), data: { ...node({ id: "z" }).data, gens: [gen("x2", "u2")] } };
    const after = updater([hit, miss]);
    expect(after[0].data.naturalSize).toEqual({ w: 33, h: 44 });
    expect(after[1]).toBe(miss);
  });
});

// ── ② buildToolCards：三源合并 + 同模板去重 ───────────────────────────────────
function toolRig(over: Partial<ToolCardsDeps> = {}) {
  return {
    messages: [] as ChatMessage[],
    deletedIdSet: new Set<string>(),
    layoutNodes: {} as Record<string, NodeLayout>,
    pendingToolCreates: [],
    findFreeSpot: vi.fn(() => ({ x: 1, y: 2 })),
    settings,
    layoutNodesRef: ref<Record<string, NodeLayout>>({}),
    stageWrapRef: rectRef(),
    openCtxForNode: vi.fn(),
    persistNow: vi.fn(),
    runWorkflowTask: vi.fn(),
    setLayoutNodes: vi.fn(),
    setRfNodes: vi.fn(),
    setToolModalNode: vi.fn(),
    ...over,
  } as ToolCardsDeps & {
    findFreeSpot: ReturnType<typeof vi.fn>; persistNow: ReturnType<typeof vi.fn>;
    runWorkflowTask: ReturnType<typeof vi.fn>; setLayoutNodes: ReturnType<typeof vi.fn>;
    setRfNodes: ReturnType<typeof vi.fn>; setToolModalNode: ReturnType<typeof vi.fn>;
  };
}
const wfMsg = (id: string, templateId: string, name = "模板A", done = false) =>
  msg(id, "assistant", "工作流", {
    workflow: { templateId, templateName: name, done, draftGraph: { d: templateId }, capturedGraph: { c: templateId } },
  } as unknown as Partial<ChatMessage>);

describe("② buildToolCards：三源合并优先级 / 去重 / 空位避让 / 工具卡回调", () => {
  it("空输入 → 空数组", () => {
    expect(buildToolCards(toolRig())).toEqual([]);
  });

  it("① 对话投影：同模板多张只保一张；id 稳定 wftool-<templateId>；draft/captured/confirmed 透传", () => {
    const rig = toolRig({ messages: [wfMsg("m1", "T1", "模板甲", true), wfMsg("m2", "T1", "模板甲", true)] });
    const got = buildToolCards(rig);
    expect(got).toHaveLength(1);
    expect(got[0].id).toBe("wftool-T1");
    expect(got[0].data.node.templateId).toBe("T1");
    expect(got[0].data.node.templateName).toBe("模板甲");
    expect(got[0].data.node.wfConfirmed).toBe(true);
    expect(got[0].data.node.wfDraft).toEqual({ d: "T1" });
    expect(got[0].data.node.wfCaptured).toEqual({ c: "T1" });
    expect(got[0].data.prompt).toBe("模板甲");
  });

  it("① 已删除（deletedIdSet 命中）→ 不复活", () => {
    const rig = toolRig({ messages: [wfMsg("m1", "T1")], deletedIdSet: new Set(["wftool-T1"]) });
    expect(buildToolCards(rig)).toEqual([]);
  });

  it("三源合并：① 投影优先于 ② 事件待创建 与 ③ 已持久化（同 templateId 只留第一源）", () => {
    const rig = toolRig({
      messages: [wfMsg("m1", "T1", "投影名", true)],
      pendingToolCreates: [
        { id: "wftool-T1", templateId: "T1", templateName: "事件名", estimatedNodeCount: 5 },
        { id: "wftool-T2", templateId: "T2", templateName: "事件乙", estimatedNodeCount: 2 },
      ],
      layoutNodes: {
        "persisted-3": { x: 0, y: 0, w: CARD_W, h: 180, templateId: "T1", templateName: "持久甲" },
        "persisted-4": { x: 0, y: 0, w: CARD_W, h: 180, templateId: "T4", templateName: "持久丁", wfExposedIds: ["a", "b"] },
      },
    });
    const got = buildToolCards(rig);
    expect(got.map((n) => n.id)).toEqual(["wftool-T1", "wftool-T2", "persisted-4"]);
    // ① 胜出：拿投影的 templateName/wfConfirmed，而不是事件源或持久源
    expect(got[0].data.node.templateName).toBe("投影名");
    expect(got[0].data.node.wfConfirmed).toBe(true);
    expect(got[1].data.node.templateName).toBe("事件乙");
    // ③ 持久源：id 用持久化 id（保住用户布局）
    expect(got[2].data.node.templateName).toBe("持久丁");
  });

  it("节点数 = max(已暴露, 预估)；尺寸：未 custom → width 固定 + height auto；custom → 跟随布局", () => {
    const rig = toolRig({
      messages: [wfMsg("m1", "T1", "甲")],
      layoutNodes: {
        "wftool-T1": { x: 5, y: 6, w: CARD_W, h: 180, wfExposedIds: ["a", "b", "c"] },
      },
      pendingToolCreates: [{ id: "wftool-T9", templateId: "T9", templateName: "九", estimatedNodeCount: 1 }],
    });
    const got = buildToolCards(rig);
    const t1 = got.find((n) => n.id === "wftool-T1")!;
    // exposedCount 3 > estCount（= layoutNodes[p.id].wfExposedIds.length = 3）→ 3
    expect(t1.data.node.wfEstimatedNodeCount).toBe(3);
    expect(t1.data.node.h).toBe(Math.max(180, 120 + 3 * 70));
    expect(t1.style).toEqual({ width: CARD_W, height: "auto" });

    const t9 = got.find((n) => n.id === "wftool-T9")!;
    expect(t9.data.node.wfEstimatedNodeCount).toBe(1);   // 事件源自带预估
    expect(t9.data.comfyUrl).toBe("");                   // settings.comfyuiUrl || ""

    const rig2 = toolRig({
      messages: [wfMsg("m1", "T1")],
      layoutNodes: { "wftool-T1": { x: 5, y: 6, w: 300, h: 410, custom: true } },
    });
    expect(buildToolCards(rig2)[0].style).toEqual({ width: 300, height: 410 });
    expect(buildToolCards(rig2)[0].position).toEqual({ x: 5, y: 6 });
  });

  it("② 事件源 + 已存在布局：暴露节点数与事件预估互有高低 → 取 max（否则卡片显示 0 节点）", () => {
    // 暴露（3）> 事件预估（1）→ 3
    const rig = toolRig({
      pendingToolCreates: [{ id: "wftool-TX", templateId: "TX", templateName: "X", estimatedNodeCount: 1 }],
      layoutNodes: { "wftool-TX": { x: 0, y: 0, w: CARD_W, h: 180, wfExposedIds: ["a", "b", "c"] } },
    });
    const [n] = buildToolCards(rig);
    expect(n.data.node.wfEstimatedNodeCount).toBe(3);
    expect(n.data.node.h).toBe(Math.max(180, 120 + 3 * 70));

    // 暴露（0）< 事件预估（5）→ 5
    const rig2 = toolRig({
      pendingToolCreates: [{ id: "wftool-TY", templateId: "TY", templateName: "Y", estimatedNodeCount: 5 }],
    });
    expect(buildToolCards(rig2)[0].data.node.wfEstimatedNodeCount).toBe(5);
  });

  it("无持久化布局 → findFreeSpot 找空位，且同批次多张卡互相避让（extras 累积）", () => {
    const seen: number[] = [];
    const rig = toolRig({
      pendingToolCreates: [
        { id: "wftool-A", templateId: "A", templateName: "甲", estimatedNodeCount: 0 },
        { id: "wftool-B", templateId: "B", templateName: "乙", estimatedNodeCount: 0 },
      ],
      findFreeSpot: vi.fn((_w: number, _h: number, extra?: Array<{ x: number; y: number; w: number; h: number }>) => {
        seen.push(extra?.length ?? -1);
        return { x: 10 * (extra?.length ?? 0), y: 20 };
      }),
    });
    const got = buildToolCards(rig);
    expect(seen).toEqual([0, 1]);                    // 第二张卡带上了第一张的占位矩形
    expect(got[0].position).toEqual({ x: 0, y: 20 });
    expect(got[1].position).toEqual({ x: 10, y: 20 });
  });

  it("按钮回调：选择完毕/更改/双击打开编辑器；运转派发事件 + 调 runWorkflowTask", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "abcd1234-0000-0000-0000-000000000000" });
    const rig = toolRig({ messages: [wfMsg("m1", "T1", "甲")] });
    const [n] = buildToolCards(rig);
    const nn = n.data.node;

    (n.data.onConfirmWorkflow as (n: CanvasNode) => void)(nn);
    expect(rig.setToolModalNode).toHaveBeenCalledWith({ node: nn, autoConfirm: true });
    (n.data.onOpen as (n: CanvasNode) => void)(nn);
    expect(rig.setToolModalNode).toHaveBeenLastCalledWith({ node: nn, autoConfirm: false });

    const dispatched: CustomEvent[] = [];
    const spy = vi.spyOn(window, "dispatchEvent").mockImplementation((e) => { dispatched.push(e as CustomEvent); return true; });
    (n.data.onRunWorkflow as (n: CanvasNode) => void)(nn);
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].type).toBe("laf-canvas-wf-run");
    expect(dispatched[0].detail.toolNodeId).toBe("wftool-T1");
    expect(dispatched[0].detail.templateName).toBe("甲");
    expect(dispatched[0].detail.runId).toMatch(/^wfrun-/);
    expect(rig.runWorkflowTask).toHaveBeenCalledWith(nn, dispatched[0].detail.runId);
    spy.mockRestore();

    // 更改 → rfNodes 置 wfConfirmed:false；layoutNodes 置 wfConfirmed:false + 重算高度；开编辑器
    const changed = { ...nn, wfExposedIds: ["a", "b"] };
    (n.data.onChangeWorkflow as (n: CanvasNode) => void)(changed);
    const rfUpdater = rig.setRfNodes.mock.calls[0][0] as (p: Node<CardNodeData>[]) => Node<CardNodeData>[];
    const after = rfUpdater([node({ id: "wftool-T1", data: { ...node({ id: "wftool-T1" }).data, node: nn } })]);
    expect(after[0].data.node.wfConfirmed).toBe(false);
    const layUpdater = rig.setLayoutNodes.mock.calls[0][0] as (p: Record<string, NodeLayout>) => Record<string, NodeLayout>;
    const lay = layUpdater({ "wftool-T1": { x: 1, y: 2, w: CARD_W, h: 999, wfConfirmed: true } });
    expect(lay["wftool-T1"].wfConfirmed).toBe(false);
    expect(lay["wftool-T1"].h).toBe(Math.max(180, 120 + 2 * 70));
    expect(rig.setToolModalNode).toHaveBeenLastCalledWith({ node: { ...changed, wfConfirmed: false }, autoConfirm: false });
  });

  it("拉伸结束写 layoutNodes + 落盘；右键坐标扣 stageWrap 偏移", () => {
    const rig = toolRig({ messages: [wfMsg("m1", "T1")] });
    const [n] = buildToolCards(rig);
    (n.data.onResizeEnd as (id: string, w: number, h: number) => void)("wftool-T1", 500, 600);
    const next = rig.setLayoutNodes.mock.calls[0][0] as Record<string, NodeLayout>;
    expect(next["wftool-T1"]).toMatchObject({ w: 500, h: 600, custom: true, x: 0, y: 0 });
    expect(rig.persistNow).toHaveBeenCalledWith({ nodes: next });

    n.data.onNodeCtx({ clientX: 101, clientY: 51 } as React.MouseEvent, n.data.node);
    expect(rig.openCtxForNode).toHaveBeenCalledWith(1, 1, "wftool-T1");
  });
});

// ── ③ buildStoryNodes：剧情楼层 ───────────────────────────────────────────────
function storyRig(over: Partial<StoryNodesDeps> = {}) {
  return {
    streamingId: null as string | null,
    messages: [] as ChatMessage[],
    layoutNodes: {} as Record<string, NodeLayout>,
    displayRegex: undefined,
    layoutNodesRef: ref<Record<string, NodeLayout>>({}),
    stageWrapRef: rectRef(),
    openCtxForNode: vi.fn(),
    persistNow: vi.fn(),
    setDetailNode: vi.fn(),
    setLayoutNodes: vi.fn(),
    setRfNodes: vi.fn(),
    setShowStoryThinking: vi.fn(),
    findFreeSpot: vi.fn(() => ({ x: 3, y: 4 })),
    streamingPosRef: ref<{ id: string; x: number; y: number } | null>(null),
    placeholderConsumedByStoryRef: ref(false),
    pendingStoryLayoutsRef: ref<Record<string, { x: number; y: number; w: number; h: number }>>({}),
    ...over,
  } as StoryNodesDeps & {
    findFreeSpot: ReturnType<typeof vi.fn>; persistNow: ReturnType<typeof vi.fn>;
    setDetailNode: ReturnType<typeof vi.fn>; setLayoutNodes: ReturnType<typeof vi.fn>;
    setRfNodes: ReturnType<typeof vi.fn>; setShowStoryThinking: ReturnType<typeof vi.fn>;
  };
}
/**
 * 剧情楼层消息（route=roleplay，命中 isStoryNode 白名单）。
 * ⚠ 封面必须挂在 **parts 图片槽**：顶层 `m.image` 是「生成内容气泡」标记，
 *   带上它会被 `isStoryNode` 判为非剧情楼层（自动插画走 parts，不影响封面）。
 */
const storyMsg = (id: string, text: string, cover = ""): ChatMessage => {
  const m = msg(id, "assistant", text, { route: "roleplay" } as Partial<ChatMessage>);
  return cover ? { ...m, parts: [{ type: "image", url: cover }] } : m;
};

describe("③ buildStoryNodes：楼层投影 / 生成中跳过 / 接替锚点 / 首次放置落盘", () => {
  it("空输入 → 空数组；非剧情路由不进楼层", () => {
    expect(buildStoryNodes(storyRig())).toEqual([]);
    expect(buildStoryNodes(storyRig({ messages: [msg("m1", "user", "提问")] }))).toEqual([]);
    expect(buildStoryNodes(storyRig({ messages: [msg("m2", "assistant", "普通回复")] }))).toEqual([]);
  });

  it("有封面 → 卡宽 + 高 240、左图右文；无封面 → 9:16 竖版；序号/总数/groupKey 正确", () => {
    const rig = storyRig({
      messages: [
        storyMsg("s1", "第一楼", "cover.png"),
        storyMsg("s2", "第二楼"),
      ],
    });
    const got = buildStoryNodes(rig);
    expect(got.map((n) => n.id)).toEqual(["story-s1", "story-s2"]);

    expect(got[0].style).toEqual({ width: CARD_W, height: 240 });
    expect(got[0].data.imageUrls).toEqual(["cover.png"]);
    expect(got[0].data.node.storyIndex).toBe(1);
    expect(got[0].data.node.storyTotal).toBe(2);
    expect(got[0].data.node.storyImage).toBe("cover.png");
    expect(got[0].data.node.groupKey).toBe("story-s1");

    expect(got[1].style).toEqual({ width: INSPIRATION_CARD_W, height: INSPIRATION_CARD_H });
    expect(got[1].data.imageUrls).toEqual([]);
    expect(got[1].data.node.storyIndex).toBe(2);
    expect(got[1].data.node.storyTotal).toBe(2);
  });

  it("生成中的消息（streamingId）跳过楼层投影；清空后同一消息才落成楼层", () => {
    const m = storyMsg("s2", "生成中的正文", "c.png");
    const still = buildStoryNodes(storyRig({ messages: [storyMsg("s1", "一楼"), m], streamingId: "s2" }));
    expect(still.map((n) => n.id)).toEqual(["story-s1"]);
    const done = buildStoryNodes(storyRig({ messages: [storyMsg("s1", "一楼"), m], streamingId: null }));
    expect(done.map((n) => n.id)).toEqual(["story-s1", "story-s2"]);
  });

  it("接替生成中占位：本条 messageId 命中落点缓存 → 原地投影 + 标记锚点已消费 + 记待落盘位置", () => {
    const rig = storyRig({
      messages: [storyMsg("s1", "一楼", "c.png")],
      streamingPosRef: ref({ id: "s1", x: 88, y: 99 }),
      findFreeSpot: vi.fn(() => { throw new Error("命中接替时不该再找空位"); }),
    });
    const got = buildStoryNodes(rig);
    expect(got[0].position).toEqual({ x: 88, y: 99 });
    expect(rig.placeholderConsumedByStoryRef.current).toBe(true);
    expect(rig.pendingStoryLayoutsRef.current["story-s1"]).toEqual({ x: 88, y: 99, w: CARD_W, h: 240 });
  });

  it("未命中落点（别条消息）→ 走 findFreeSpot，同样记待落盘位置；持久化布局优先", () => {
    const rig = storyRig({
      messages: [storyMsg("s1", "一楼")],
      streamingPosRef: ref({ id: "other", x: 88, y: 99 }),
    });
    const got = buildStoryNodes(rig);
    expect(got[0].position).toEqual({ x: 3, y: 4 });
    expect(rig.placeholderConsumedByStoryRef.current).toBe(false);
    expect(rig.pendingStoryLayoutsRef.current["story-s1"]).toEqual({ x: 3, y: 4, w: INSPIRATION_CARD_W, h: INSPIRATION_CARD_H });

    const rig2 = storyRig({
      messages: [storyMsg("s1", "一楼")],
      layoutNodes: { "story-s1": { x: 200, y: 300, w: 280, h: 460, custom: true } },
    });
    const [n2] = buildStoryNodes(rig2);
    expect(n2.position).toEqual({ x: 200, y: 300 });
    expect(n2.style).toEqual({ width: 280, height: 460 });
    expect(rig2.pendingStoryLayoutsRef.current["story-s1"]).toBeUndefined(); // 已有布局 → 不记
  });

  it("displayRegex 进 data；双击 → 关思考块并开详情；右键扣偏移", () => {
    const displayRegex = [{ name: "r" }] as unknown as StoryNodesDeps["displayRegex"];
    const rig = storyRig({ messages: [storyMsg("s1", "一楼")], displayRegex });
    const [n] = buildStoryNodes(rig);
    expect(n.data.displayRegex).toBe(displayRegex);

    n.data.onOpen(n.data.node);
    expect(rig.setShowStoryThinking).toHaveBeenCalledWith(false);
    expect(rig.setDetailNode).toHaveBeenCalledWith(n.data.node);

    n.data.onNodeCtx({ clientX: 105, clientY: 55 } as React.MouseEvent, n.data.node);
    expect(rig.openCtxForNode).toHaveBeenCalledWith(5, 5, "story-s1");
  });

  it("拉伸结束写 layoutNodes（基准缺失时用剧情默认高 240）", () => {
    const rig = storyRig({ messages: [storyMsg("s1", "一楼")] });
    const [n] = buildStoryNodes(rig);
    (n.data.onResizeEnd as (id: string, w: number, h: number) => void)("story-s1", 300, 400);
    const next = rig.setLayoutNodes.mock.calls[0][0] as Record<string, NodeLayout>;
    expect(next["story-s1"]).toMatchObject({ w: 300, h: 400, custom: true, x: 0, y: 0 });
    expect(rig.persistNow).toHaveBeenCalledWith({ nodes: next });
  });
});

// ── ④ buildInspirationNodes：灵感卡 ───────────────────────────────────────────
function inspRig(over: Partial<InspirationNodesDeps> = {}) {
  return {
    inspirationCards: [] as InspirationCardStored[],
    inspirationCardsRef: ref<InspirationCardStored[]>([]),
    settings,
    repoId: "repo1",
    stageWrapRef: rectRef(),
    openCtxForNode: vi.fn(),
    persistNow: vi.fn(),
    setRfNodes: vi.fn(),
    setInspirationCards: vi.fn(),
    insertInspirationToChat: vi.fn(),
    showToast: vi.fn(),
    ...modalSpies(),
    ...over,
  } as InspirationNodesDeps & ReturnType<typeof modalSpies> & {
    persistNow: ReturnType<typeof vi.fn>; setRfNodes: ReturnType<typeof vi.fn>;
    setInspirationCards: ReturnType<typeof vi.fn>; insertInspirationToChat: ReturnType<typeof vi.fn>;
    showToast: ReturnType<typeof vi.fn>;
  };
}
const card = (over: Partial<InspirationCardStored> & { id: string }): InspirationCardStored =>
  ({ title: "标题", content: "正文", kind: "preset", x: 7, y: 8, w: 190, h: 150, ...over }) as InspirationCardStored;

describe("④ buildInspirationNodes：坐标透传 / 双击五类分派 / 卡动作", () => {
  it("空输入 → 空数组；坐标尺寸来自卡片本身（不走 layoutNodes）", () => {
    expect(buildInspirationNodes(inspRig())).toEqual([]);
    const rig = inspRig({ inspirationCards: [card({ id: "k1", x: 7, y: 8, w: 190, h: 150, imageUrl: "i.png" })] });
    const [n] = buildInspirationNodes(rig);
    expect(n.id).toBe("k1");
    expect(n.position).toEqual({ x: 7, y: 8 });
    expect(n.style).toEqual({ width: 190, height: 150 });
    expect(n.data.imageUrls).toEqual(["i.png"]);
    expect(n.data.customLabel).toBe("标题");
    expect(n.data.node.type).toBe("inspiration-card");
    expect(n.data.node.groupKey).toBe("k1");
  });

  it("双击分派 ①：带图 / 对话投影（sourceRef=conv）→ 左图右文编辑弹窗", () => {
    const rig = inspRig({
      inspirationCards: [card({ id: "k1", imageUrl: "i.png" }), card({ id: "k2", sourceRef: "conv" })],
    });
    const got = buildInspirationNodes(rig);
    rig.inspirationCardsRef.current = rig.inspirationCards;

    got[0].data.onOpen(got[0].data.node);
    expect(rig.setInspirationEditId).toHaveBeenCalledWith("k1");
    expect(rig.setInspirationEditKind).toHaveBeenCalledWith("preset");
    expect(rig.setPresetModalOpen).not.toHaveBeenCalled();

    got[1].data.onOpen(got[1].data.node);
    expect(rig.setInspirationEditId).toHaveBeenLastCalledWith("k2");
    expect(rig.setPresetModalOpen).not.toHaveBeenCalled();
  });

  it("双击分派 ②：世界书条目 + 快照定位可用 → 打开仓库快照世界书（编辑改快照，与源库隔离）", () => {
    const rig = inspRig({
      inspirationCards: [card({ id: "k1", kind: "worldbook-entry", sourceRef: "wb:人物设定", title: "人物" })],
    });
    const [n] = buildInspirationNodes(rig);
    rig.inspirationCardsRef.current = rig.inspirationCards;
    n.data.onOpen(n.data.node);
    expect(rig.setWbSeedFrom).toHaveBeenCalledWith({ base: "/wb", name: "人物设定" });
    expect(rig.setWbRepoLoc).toHaveBeenCalledWith({ output_dir: "/out", repo_id: "repo1" });
    expect(rig.setWbPopupTitle).toHaveBeenCalledWith("人物");
    expect(rig.setInspirationEditId).not.toHaveBeenCalled();

    // outputDir 缺失 → 快照分支不成立 → 落到最后的本地编辑
    const rig2 = inspRig({
      inspirationCards: [card({ id: "k1", kind: "worldbook-entry", sourceRef: "wb:人物设定" })],
      settings: { ...settings, outputDir: "" } as InspirationNodesDeps["settings"],
    });
    const [n2] = buildInspirationNodes(rig2);
    rig2.inspirationCardsRef.current = rig2.inspirationCards;
    n2.data.onOpen(n2.data.node);
    expect(rig2.setWbPopupTitle).not.toHaveBeenCalled();
    expect(rig2.setInspirationEditId).toHaveBeenCalledWith("k1");
  });

  it("双击分派 ③/④/⑤：角色卡 / 预设 / 其余 → 各弹窗与本地编辑", () => {
    const rig = inspRig({
      inspirationCards: [
        card({ id: "k1", kind: "character", sourceRef: "char:沈糯", content: "设定" }),
        card({ id: "k2", kind: "preset" }),
        card({ id: "k3", kind: "table-row" }),
      ],
    });
    const got = buildInspirationNodes(rig);
    rig.inspirationCardsRef.current = rig.inspirationCards;

    got[0].data.onOpen(got[0].data.node);
    expect(rig.setCharModal).toHaveBeenCalledWith({ name: "沈糯", cardId: "k1", content: "设定" });
    got[1].data.onOpen(got[1].data.node);
    expect(rig.setPresetModalOpen).toHaveBeenCalledWith(true);
    got[2].data.onOpen(got[2].data.node);
    expect(rig.setInspirationEditId).toHaveBeenLastCalledWith("k3");
  });

  it("角色卡无 char: 前缀 → 用标题兜底；卡片已不在 ref 里 → 直接返回不动 setter", () => {
    const rig = inspRig({ inspirationCards: [card({ id: "k1", kind: "character", title: "默认名" })] });
    const [n] = buildInspirationNodes(rig);
    n.data.onOpen(n.data.node);                       // ref 仍为空数组 → 找不到 → 早退
    expect(rig.setCharModal).not.toHaveBeenCalled();
    expect(rig.setInspirationEditId).not.toHaveBeenCalled();

    rig.inspirationCardsRef.current = rig.inspirationCards;
    n.data.onOpen(n.data.node);
    expect(rig.setCharModal).toHaveBeenCalledWith({ name: "默认名", cardId: "k1", content: "正文" });
  });

  it("卡动作：插入对话 / 复制（成功与失败两路）/ 编辑", () => {
    const writeText = vi.fn(() => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const rig = inspRig({ inspirationCards: [card({ id: "k1", content: "复制我" })] });
    const [n] = buildInspirationNodes(rig);
    rig.inspirationCardsRef.current = rig.inspirationCards;
    const act = n.data.onInspirationAction!;

    act("insert", n.data.node);
    expect(rig.insertInspirationToChat).toHaveBeenCalledWith(rig.inspirationCards[0]);

    act("copy", n.data.node);
    expect(writeText).toHaveBeenCalledWith("复制我");
    expect(rig.showToast).toHaveBeenCalledWith("内容已复制到剪贴板", "success");

    writeText.mockImplementation(() => { throw new Error("denied"); });
    act("copy", n.data.node);
    expect(rig.showToast).toHaveBeenLastCalledWith("复制失败，请手动选择", "error");

    act("edit", n.data.node);
    expect(rig.setInspirationEditId).toHaveBeenLastCalledWith("k1");

    // 卡片不在 ref 里 → 三个动作全部早退
    rig.inspirationCardsRef.current = [];
    rig.insertInspirationToChat.mockClear();
    act("insert", n.data.node);
    expect(rig.insertInspirationToChat).not.toHaveBeenCalled();
  });

  it("拉伸结束写回 inspirationCards + ref + 落盘；拉伸中只改 style", () => {
    const rig = inspRig({ inspirationCards: [card({ id: "k1" }), card({ id: "k2" })] });
    rig.inspirationCardsRef.current = rig.inspirationCards;
    const [n] = buildInspirationNodes(rig);

    (n.data.onResize as (id: string, w: number, h: number) => void)("k1", 400, 500);
    const updater = rig.setRfNodes.mock.calls[0][0] as (p: Node<CardNodeData>[]) => Node<CardNodeData>[];
    expect(updater([node({ id: "k1" })])[0].style).toEqual({ width: 400, height: 500 });

    (n.data.onResizeEnd as (id: string, w: number, h: number) => void)("k1", 400, 500);
    const next = rig.setInspirationCards.mock.calls[0][0] as InspirationCardStored[];
    expect(next[0]).toMatchObject({ id: "k1", w: 400, h: 500 });
    expect(next[1]).toBe(rig.inspirationCards[1]);    // 其它卡引用不变
    expect(rig.inspirationCardsRef.current).toBe(next);
    expect(rig.persistNow).toHaveBeenCalledWith({ cards: next });
  });
});

// ── ⑤ buildReferenceImageNodes：参考图 ───────────────────────────────────────
function refRig(over: Partial<ReferenceImageNodesDeps> = {}) {
  return {
    referenceImages: [] as ReferenceImageStored[],
    referenceImagesRef: ref<ReferenceImageStored[]>([]),
    setReferenceImages: vi.fn(),
    setRfNodes: vi.fn(),
    setDetailNode: vi.fn(),
    persistNow: vi.fn(),
    stageWrapRef: rectRef(),
    openCtxForNode: vi.fn(),
    ...over,
  } as ReferenceImageNodesDeps & {
    setReferenceImages: ReturnType<typeof vi.fn>; setRfNodes: ReturnType<typeof vi.fn>;
    setDetailNode: ReturnType<typeof vi.fn>; persistNow: ReturnType<typeof vi.fn>;
  };
}
const refImg = (over: Partial<ReferenceImageStored> & { id: string }): ReferenceImageStored =>
  ({ title: "图A", imageUrl: "a.png", x: 1, y: 2, w: 120, h: 160, ...over }) as ReferenceImageStored;

describe("⑤ buildReferenceImageNodes：坐标透传 / 双击详情 / 拉伸写回", () => {
  it("空输入 → 空数组；字段与坐标来自参考图记录", () => {
    expect(buildReferenceImageNodes(refRig())).toEqual([]);
    const [n] = buildReferenceImageNodes(refRig({ referenceImages: [refImg({ id: "r1" })] }));
    expect(n.id).toBe("r1");
    expect(n.position).toEqual({ x: 1, y: 2 });
    expect(n.style).toEqual({ width: 120, height: 160 });
    expect(n.data.imageUrls).toEqual(["a.png"]);
    expect(n.data.customLabel).toBe("图A");
    expect(n.data.node.referenceImageUrl).toBe("a.png");
    expect(n.data.node.referenceImageTitle).toBe("图A");
    expect(n.data.node.groupKey).toBe("r1");
  });

  it("双击 → 详情面板；右键扣 stageWrap 偏移", () => {
    const rig = refRig({ referenceImages: [refImg({ id: "r1" })] });
    const [n] = buildReferenceImageNodes(rig);
    n.data.onOpen(n.data.node);
    expect(rig.setDetailNode).toHaveBeenCalledWith(n.data.node);
    n.data.onNodeCtx({ clientX: 110, clientY: 60 } as React.MouseEvent, n.data.node);
    expect(rig.openCtxForNode).toHaveBeenCalledWith(10, 10, "r1");
  });

  it("拉伸结束写回 referenceImages + ref + 落盘", () => {
    const rig = refRig({ referenceImages: [refImg({ id: "r1" }), refImg({ id: "r2" })] });
    rig.referenceImagesRef.current = rig.referenceImages;
    const [n] = buildReferenceImageNodes(rig);
    (n.data.onResizeEnd as (id: string, w: number, h: number) => void)("r1", 220, 330);
    const next = rig.setReferenceImages.mock.calls[0][0] as ReferenceImageStored[];
    expect(next[0]).toMatchObject({ id: "r1", w: 220, h: 330 });
    expect(next[1]).toBe(rig.referenceImages[1]);
    expect(rig.referenceImagesRef.current).toBe(next);
    expect(rig.persistNow).toHaveBeenCalledWith({ refs: next });
  });
});

// ── ⑥ mergeProjectedNodes：投影合并判定（纯函数，最重要的一条语义）──────────
describe("⑥ mergeProjectedNodes：内容未变返回原引用 / 保留选中与组坐标 / data 必须比较", () => {
  it("内容（含 data 引用）完全一致 → 返回 prevNodes 原引用（React 不重渲染）", () => {
    const prev = [node({ id: "a" })];
    const next = [node({ id: "a" })];
    // 让两侧 data 同引用（投影重建会造新 data → 那种情况必须返回新数组，见下一用例）
    next[0] = { ...next[0], data: prev[0].data };
    expect(mergeProjectedNodes(prev, next)).toBe(prev);
  });

  it("data 引用变了（编辑器写回 wfCaptured/wfConfirmed）→ 必须返回新数组，否则首次点击静默失败", () => {
    const prev = [node({ id: "a" })];
    const next = [node({ id: "a" })];                  // data 是新对象（值相同、引用不同）
    const out = mergeProjectedNodes(prev, next);
    expect(out).not.toBe(prev);
    expect(out[0].data).toBe(next[0].data);          // 新 data 必须进 state（否则首次点击拿不到 wfCaptured）
    expect(out[0].position).toBe(prev[0].position);  // 坐标仍取 ReactFlow 实际值
  });

  it("数量不同 → 返回新数组", () => {
    expect(mergeProjectedNodes([], [node({ id: "a" })])).toHaveLength(1);
    expect(mergeProjectedNodes([node({ id: "a" })], [])).toHaveLength(0);
  });

  it("保留用户选中：prev.selected=true 而投影 selected=false → 合并结果仍选中", () => {
    const prev = [{ ...node({ id: "a" }), selected: true }];
    const next = [node({ id: "a" })];
    const out = mergeProjectedNodes(prev as Node<CardNodeData>[], next);
    expect(out[0].selected).toBe(true);
    expect(out).not.toBe(prev);                        // 选中态变了 → 必须重渲染
  });

  it("保留 ReactFlow 实际坐标与父子关系（组内子节点不能被投影回绝对坐标）", () => {
    const prev = [{ ...node({ id: "child", position: { x: 3, y: 4 } }), parentId: "grp", extent: "parent" as const }];
    const next = [node({ id: "child", position: { x: 999, y: 999 } })];
    const out = mergeProjectedNodes(prev as Node<CardNodeData>[], next);
    expect(out[0].position).toEqual({ x: 3, y: 4 });
    expect(out[0].parentId).toBe("grp");
    expect(out[0].extent).toBe("parent");
  });

  it("尺寸类变更必须进状态（resize 后 style / customSize），否则被静默丢弃", () => {
    const base = node({ id: "a" });
    const prev = [{ ...base, style: { width: 100, height: 200 } }];
    const next = [{ ...base, style: { width: 111, height: 222 } }];
    expect(mergeProjectedNodes(prev as Node<CardNodeData>[], next)).not.toBe(prev);

    const prev2 = [{ ...base, data: { ...base.data, customSize: false } }];
    const next2 = [{ ...base, data: { ...base.data, customSize: true } }];
    expect(mergeProjectedNodes(prev2 as Node<CardNodeData>[], next2)).not.toBe(prev2);
  });

  it("id 顺序不一致 → 返回新数组（不因长度相同就误判相等）", () => {
    const prev = [node({ id: "a" }), node({ id: "b" })];
    const next = [{ ...node({ id: "b" }), data: prev[1].data }, { ...node({ id: "a" }), data: prev[0].data }];
    expect(mergeProjectedNodes(prev, next)).not.toBe(prev);
  });
});

// ── ⑦ buildCanvasEdges：连线派生 ─────────────────────────────────────────────
function edgeRig(over: Partial<CanvasEdgesDeps> = {}) {
  return {
    inputs: [] as Node<CardNodeData>[],
    toolCards: [] as Node<CardNodeData>[],
    storyNodes: [] as Node<CardNodeData>[],
    projected: [] as Node<CardNodeData>[],
    referenceImageNodes: [] as Node<CardNodeData>[],
    layoutEdges: [] as { source: string; target: string }[],
    showStoryFlowRef: ref(false),
    ...over,
  } as CanvasEdgesDeps;
}
const en = (id: string): Node<CardNodeData> => node({ id });

describe("⑦ buildCanvasEdges：手动线过滤 + 自动剧情顺序线（派生、不落盘）", () => {
  it("手动线：两端都还在 → 保留；任一端已不存在 → 过滤掉；id 形如 source-target", () => {
    const rig = edgeRig({
      projected: [en("a"), en("b")],
      referenceImageNodes: [en("c")],
      layoutEdges: [
        { source: "a", target: "b" },
        { source: "b", target: "ghost" },
        { source: "ghost", target: "a" },
        { source: "a", target: "c" },
      ],
    });
    const { manualEdges } = buildCanvasEdges(rig);
    expect(manualEdges).toEqual([
      { id: "a-b", source: "a", target: "b", animated: false },
      { id: "a-c", source: "a", target: "c", animated: false },
    ]);
  });

  it("剧情顺序线关闭 → 不派生，且返回空 id 集合（旧自动线要被清掉）", () => {
    const rig = edgeRig({ storyNodes: [en("story-s1"), en("story-s2")] });
    const { autoEdges, autoEdgeIds } = buildCanvasEdges(rig);
    expect(autoEdges).toEqual([]);
    expect([...autoEdgeIds]).toEqual([]);
  });

  it("剧情顺序线开启 → 相邻楼层逐一连线（紫色虚线 + 箭头），id 与序号写入 data", () => {
    const rig = edgeRig({
      storyNodes: [en("story-s1"), en("story-s2"), en("story-s3")],
      showStoryFlowRef: ref(true),
    });
    const { autoEdges, autoEdgeIds } = buildCanvasEdges(rig);
    expect(autoEdges.map((e) => e.id)).toEqual(["storyflow-0", "storyflow-1"]);
    expect(autoEdges.map((e) => e.source)).toEqual(["story-s1", "story-s2"]);
    expect(autoEdges.map((e) => e.target)).toEqual(["story-s2", "story-s3"]);
    expect(autoEdges[0].type).toBe("smoothstep");
    expect(autoEdges[0].style).toEqual({ stroke: "#8b5cf6", strokeDasharray: "5 5", strokeWidth: 1.5, opacity: 0.75 });
    expect(autoEdges[0].markerEnd).toMatchObject({ color: "#8b5cf6", width: 14, height: 14 });
    expect(autoEdges[0].data).toEqual({ storyFrom: 1, storyTo: 2 });
    expect(autoEdges[1].data).toEqual({ storyFrom: 2, storyTo: 3 });
    expect([...autoEdgeIds]).toEqual(["storyflow-0", "storyflow-1"]);
  });

  it("单层剧情 → 自动线为空（slice(0,-1) 语义）；id 集合为空", () => {
    const rig = edgeRig({ storyNodes: [en("story-s1")], showStoryFlowRef: ref(true) });
    const { autoEdges, autoEdgeIds } = buildCanvasEdges(rig);
    expect(autoEdges).toEqual([]);
    expect(autoEdgeIds.size).toBe(0);
  });

  it("手动引用线来自持久化布局（不过滤历史 id 前缀，只按两端存在性）", () => {
    const rig = edgeRig({ toolCards: [en("wftool-T1")], inputs: [en("p0")] });
    const { manualEdges } = buildCanvasEdges({ ...rig, layoutEdges: [{ source: "wftool-T1", target: "p0" }] });
    expect(manualEdges.map((e) => e.id)).toEqual(["wftool-T1-p0"]);
  });
});

// ── ⑧ 逐字搬移锁：改动前视图里的判定关键语句必须在接缝里逐字存在 ──────────────
describe("⑧ 逐字搬移锁：决策关键语句在接缝里逐字保留（且已从视图搬走）", () => {
  const seamRaw = () => read("./canvasProjection.ts");
  const canvasRaw = () => read("../views/CanvasStageFlow.tsx");
  const countIn = (needles: string[], hay: string[]) => needles.map((n) => hay.filter((l) => l === n).length);

  const NEEDLES = [
    `const gensFor = byGroup.get(n.id) || [];`,
    `...(lyt?.parentId ? { parentId: lyt.parentId, extent: "parent" as const } : {}),`,
    `const toolDefaultH = (cnt: number) => Math.max(180, 120 + (cnt || 0) * 70);`,
    `if (deletedIdSet.has(p.id)) continue;`,
    `const nodeCount = Math.max(exposedCount, estCount);`,
    `const storySource = streamingId ? messages.filter((m) => m.id !== streamingId) : messages;`,
    `placeholderConsumedByStoryRef.current = true;`,
    `if (ph && ph.id === sp.messageId) {`,
    `onOpen: (nn: CanvasNode) => { setShowStoryThinking(false); setDetailNode(nn); },`,
    `const c = inspirationCardsRef.current.find((x) => x.id === n.id);`,
    `const next = inspirationCardsRef.current.map((c) => c.id === id ? { ...c, w, h } : c);`,
    `const next = referenceImagesRef.current.map((c) => c.id === id ? { ...c, w, h } : c);`,
    `let out = prev && prev.selected ? { ...n, selected: true } : n;`,
    `&& o.data === n.data;`,
    `const validIds = new Set([...inputs, ...toolCards, ...storyNodes, ...projected, ...referenceImageNodes].map((n) => n.id));`,
    `markerEnd: { type: MarkerType.ArrowClosed, color: "#8b5cf6", width: 14, height: 14 },`,
    `const autoEdgeIds = new Set(autoEdges.map((e) => e.id));`,
  ] as const;

  it("接缝里逐字保留（按次数钉：同一条语句出现次数也一致）", () => {
    const SEAM_CODE = codeLines(seamRaw());
    const hits = countIn([...NEEDLES], SEAM_CODE);
    NEEDLES.forEach((n, i) => expect(hits[i], n).toBe(1));

    // 同一语句在视图里**不得再出现**（搬走而非复制）——两处都锁 ⇒ 内联回退会同时触发
    const CANVAS_CODE = codeLines(canvasRaw());
    const stale = countIn([...NEEDLES], CANVAS_CODE);
    stale.forEach((n, i) => expect(n, NEEDLES[i]).toBe(0));
  });

  it("属主唯一：视图里不再声明这 7 个接缝（原局部量也随之消失）", () => {
    const seam = seamRaw();
    for (const fn of ["buildContentNodes", "buildToolCards", "buildStoryNodes", "buildInspirationNodes", "buildReferenceImageNodes", "mergeProjectedNodes", "buildCanvasEdges"]) {
      expect(seam, fn).toMatch(new RegExp(`export function ${fn}\\(`));
    }
    const CANVAS_CODE = codeLines(canvasRaw());
    const SEAM_CODE = codeLines(seam);
    for (const gone of [
      `const projected = rawNodes.map((n) => {`,
      `const toolCards: Node<CardNodeData>[] = toolSources.map((t) => {`,
      `const storyNodes: Node<CardNodeData>[] = projectStoryNodes(storySource)`,
      `const inspirationNodes: Node<CardNodeData>[] = inspirationCards.map((card) => ({`,
      `const referenceImageNodes: Node<CardNodeData>[] = referenceImages.map((ref) => ({`,
      `const prevMap = new Map(prevNodes.map((n) => [n.id, n]));`,
      `const manualEdges: Edge[] = layoutEdges`,
      `const newToolExtrasRef: Array<{ x: number; y: number; w: number; h: number }> = [];`,
      `const storyExtrasRef: Array<{ x: number; y: number; w: number; h: number }> = [];`,
    ]) {
      expect(CANVAS_CODE.some((l) => l.includes(gone)), gone).toBe(false);
      expect(SEAM_CODE.some((l) => l.includes(gone)), `seam ${gone}`).toBe(true);
    }
    expect(canvasRaw()).toContain("} from \"../lib/canvasProjection\";");
  });
});

// ── ⑨ 源码形态锁：投影 effect 由 7 个 builder 组装 ────────────────────────────
describe("⑨ 源码形态锁：投影 effect 组装 + 依赖数组逐字不变 + 行数", () => {
  /** 定位投影 useEffect 的行区间（锚点唯一：`const nextNodes = [...inputs, ...toolCards, ...`）。 */
  function projectionEffect(text: string) {
    const lines = text.split("\n");
    const anchor = lines.findIndex((l) => l.includes("const nextNodes: Node<CardNodeData>[] = [...inputs, ...toolCards, ...storyNodes, ...wfRunNodes,"));
    expect(anchor, "投影 effect 的 nextNodes 锚点丢失").toBeGreaterThan(-1);
    let end = -1;
    for (let i = anchor; i < lines.length; i++) {
      if (/^\s*\}, \[rawNodes, contentPlacement,/.test(lines[i])) { end = i; break; }
    }
    expect(end, "投影 effect 的依赖数组行丢失").toBeGreaterThan(-1);
    let start = -1;
    for (let i = anchor; i >= 0; i--) {
      if (/^  useEffect\(\(\) => \{$/.test(lines[i])) { start = i; break; }
    }
    expect(start, "投影 effect 起始行丢失").toBeGreaterThan(-1);
    return { start, end, body: lines.slice(start, end + 1).join("\n") };
  }

  it("7 个 builder 在 effect 内各恰一次（可达性闭包 + 死接缝）", () => {
    const { body } = projectionEffect(read("../views/CanvasStageFlow.tsx"));
    const calls = [
      "buildContentNodes(", "buildToolCards(", "buildStoryNodes(", "buildInspirationNodes(",
      "buildReferenceImageNodes(", "mergeProjectedNodes(", "buildCanvasEdges(",
    ];
    for (const call of calls) {
      const hits = body.match(new RegExp(call.replace("(", "\\("), "g")) || [];
      expect(hits.length, `${call} 在 effect 内调用次数`).toBe(1);
    }
    // 三块「临时节点」builder（T2.1-A）仍在
    for (const call of ["buildStreamingPlaceholderNodes(", "buildDraftInputNodes(", "buildWfRunNodes("]) {
      const hits = body.match(new RegExp(call.replace("(", "\\("), "g")) || [];
      expect(hits.length, `${call} 在 effect 内调用次数`).toBe(1);
    }
  });

  it("接缝调用与结果接线保持原语义（ref 同步 / 函数式合并 / 自动线 id 回写 / 边落 state）", () => {
    const { body } = projectionEffect(read("../views/CanvasStageFlow.tsx"));
    expect(body).toContain("storyNodesRef.current = storyNodes;");
    expect(body).toContain("setRfNodes((prevNodes) => mergeProjectedNodes(prevNodes, nextNodes));");
    expect(body).toContain("autoEdgeIdsRef.current = autoEdgeIds;");
    expect(body).toContain("setRfEdges([...manualEdges, ...autoEdges]);");
    expect(body).toContain("const wfRunNodes: Node<CardNodeData>[] = buildWfRunNodes(wfRuns);");
    expect(body).toContain("streamingPos: streamingPosRef.current,");
    // 组装完整性：7 个来源一个都不能漏拼进 nextNodes（顺序也钉住）
    expect(body).toContain("const nextNodes: Node<CardNodeData>[] = [...inputs, ...toolCards, ...storyNodes, ...wfRunNodes, ...inspirationNodes, ...referenceImageNodes, ...projected];");
    // 内联回退：搬走块里的语句不得再出现在 effect 内
    for (const gone of [
      `const gensFor = byGroup.get(n.id) || [];`,
      `const nodeCount = Math.max(exposedCount, estCount);`,
      `const storySource = streamingId`,
      `let out = prev && prev.selected`,
      `const validIds = new Set(`,
      `const manualEdges: Edge[] = layoutEdges`,
    ]) {
      expect(body, gone).not.toContain(gone);
    }
  });

  it("依赖数组逐字不变（15 项，顺序与内容一概不动）", () => {
    const { body } = projectionEffect(read("../views/CanvasStageFlow.tsx"));
    // layoutLoaded 是 2026-09-17 布局竞态修复加入的守卫（挂载早期 layoutNodes={} 时禁止投影，
    // 防 findFreeSpot 网格覆盖写盘毁掉用户布局）——必须钉住，防止未来被「精简 deps」删掉。
    expect(body).toContain("}, [rawNodes, contentPlacement, layoutNodes, pendingInputs, pendingToolCreates, byGroup, layoutEdges, inspirationCards, referenceImages, wfRuns, runWorkflowTask, messages, displayRegex, showStoryFlow, layoutLoaded]);");
  });

  it("effect 真的短了（改造前 581 行 → A/B2 后 498 → 本次 ≤ 130）", () => {
    const { start, end } = projectionEffect(read("../views/CanvasStageFlow.tsx"));
    expect(end - start + 1).toBeLessThanOrEqual(130);
  });

  it("挂载期写盘守卫齐全（layoutLoaded 挡主投影 + 两个落盘 effect）", () => {
    const src = stripComments(read("../views/CanvasStageFlow.tsx"));
    // 主投影 effect：守卫在 buildContentNodes 之前出现（挂载早期 layoutNodes={} 禁投影）
    const { body } = projectionEffect(src);
    const guardAt = body.indexOf("if (!layoutLoaded) return;");
    const builderAt = body.indexOf("buildContentNodes({");
    expect(guardAt, "主投影 effect 缺 layoutLoaded 守卫").toBeGreaterThan(-1);
    expect(builderAt, "buildContentNodes 调用丢失").toBeGreaterThan(-1);
    expect(guardAt, "守卫必须在 buildContentNodes 之前").toBeLessThan(builderAt);
    // 两个「位置落盘」effect 必须各有守卫（pendingStory / contentPlacement），
    // 它们无条件 saveLayout——守卫缺失 = 挂载期网格覆盖写盘（2026-09-17 用户布局被毁的根因）。
    expect(src.match(/if \(!layoutLoaded\) return;/g)?.length, "layoutLoaded 守卫应至少 4 处（灵感卡投影 + 主投影 + 两个落盘）").toBeGreaterThanOrEqual(4);
  });
});

