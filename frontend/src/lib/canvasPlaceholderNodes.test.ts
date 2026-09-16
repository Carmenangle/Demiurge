// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import type { Node } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import {
  buildDraftInputNodes,
  buildStreamingPlaceholderNodes,
  buildWfRunNodes,
  streamingPlaceholderPrompt,
  type PendingInput,
  type WfRun,
} from "./canvasPlaceholderNodes";
import { CARD_W, type CardNodeData } from "../components/canvas/CanvasTypes";
import { workflowGenMetadata } from "./regeneration";
import type { ChatMessage } from "../types/chat";

/**
 * 画布「占位节点」投影 builder 的特征测试（2026-09-16 · T2.1 Stage A）。
 *
 * 背景：`views/CanvasStageFlow.tsx` 的投影 useEffect 改造前 **581 行**，其中 4 块是
 * 「占位/临时」节点的构造（生成中占位、待输入占位、工作流运转占位）。按架构审计 P2 的
 * 「先拆『占位节点渲染』」处置，它们外移到 `lib/canvasPlaceholderNodes.ts`。
 *
 * 三道网（沿用 T1.1–T1.3 已验证手法）：
 *   ① **等价矩阵**——把改造前的内联写法抄成 `legacy*`，在输入网格上断言
 *      「新接缝结果 ≡ 手写结果」⇒「零行为变化」可证伪。
 *   ② **纯搬移锁**——搬移语句必须**逐字**出现在新属主（抓「伪装成搬移的重写」）。
 *      这些 builder 返回的对象里全是回调（JSON 比不出），故行为面靠 ③ 的回调直调补。
 *   ③ **源码形态锁 + 回调直调**——钉「分层没被绕过」（可达性闭包）与回调语义
 *      （坐标换算 / setter 实参 / 空回调），这两件**行为网测不出**。
 */

// 源码锁统一按 LF 归一（仓库文件是 CRLF；不归一 `$` 锚点会失配）
const SEAM = readFileSync(new URL("./canvasPlaceholderNodes.ts", import.meta.url), "utf-8")
  .replace(/\r\n/g, "\n");
const CANVAS = readFileSync(new URL("../views/CanvasStageFlow.tsx", import.meta.url), "utf-8")
  .replace(/\r\n/g, "\n");

/** 比较用归一化：回调统一烙成 "[fn]"（函数本身不可比），其余字段逐字段比。 */
const strip = (v: unknown): unknown =>
  JSON.parse(JSON.stringify(v, (_k, val) => (typeof val === "function" ? "[fn]" : val)));

const user = (id: string, text: string): ChatMessage => ({ id, role: "user", text });
const asst = (id: string, text: string): ChatMessage => ({ id, role: "assistant", text });

const rectRef = () => ({
  current: { getBoundingClientRect: () => ({ left: 100, top: 50 }) } as unknown as HTMLDivElement,
});

// ── ①-a 等价矩阵：streamingPlaceholderPrompt ────────────────────────────────
// 改造前写法（`views/CanvasStageFlow.tsx` 投影 useEffect 内的 IIFE）逐字抄录。
function legacyStreamingPlaceholderPrompt(
  streamingId: string | null | undefined,
  messages: ChatMessage[],
): string {
  const fn = (() => {
    if (!streamingId) return "";
    const idx = messages.findIndex((m) => m.id === streamingId);
    for (let i = idx - 1; i >= 0; i--) if (messages[i].role === "user") return messages[i].text;
    return "";
  })();
  return fn;
}

describe("① 等价矩阵：新接缝结果 ≡ 改造前手写写法", () => {
  it("streamingPlaceholderPrompt：取 streamingId 之前最近一条 user 消息", () => {
    const cases: Array<[string | null | undefined, ChatMessage[]]> = [
      [null, []],
      [undefined, [user("m0", "U0")]],
      ["", [user("m0", "U0")]],
      ["m9", []],                                   // 找不到 → ""
      ["m2", [user("m0", "U0"), asst("m1", "A1"), asst("m2", "A2")]],
      ["m1", [user("m0", "U0"), asst("m1", "A1")]],
      ["m1", [asst("m0", "A0"), asst("m1", "A1")]], // 前面没有 user → ""
      ["m0", [user("m0", "U0"), asst("m1", "A1")]], // idx=0 → 循环不跑 → ""
      ["m3", [user("m1", "U1"), user("m2", "U2"), asst("m3", "A3")]], // 取最近（m2）
      ["m3", [user("m1", ""), asst("m2", "A2"), asst("m3", "A3")]],   // 空文本也照取
    ];
    for (const [streamingId, messages] of cases) {
      const got = streamingPlaceholderPrompt(streamingId, messages);
      const legacy = legacyStreamingPlaceholderPrompt(streamingId, messages);
      expect(got, `streamingId=${String(streamingId)}`).toBe(legacy);
    }
    // 至少有一格是非空，否则矩阵是空转
    expect(cases.some(([sid, msgs]) => legacyStreamingPlaceholderPrompt(sid, msgs) !== "")).toBe(true);
  });

  // ── ①-b/c/d 三个 builder：改造前写法逐字抄录（`streamingPosRef.current` → `streamingPos` 参数化）──
  function legacyBuildStreamingPlaceholderNodes(deps: {
    streamingId: string | null | undefined;
    messages: ChatMessage[];
    streamingPos: { id: string; x: number; y: number } | null;
    stageWrapRef: { current: HTMLDivElement | null };
    openCtxForNode: (x: number, y: number, nodeId: string) => void;
  }): Node<CardNodeData>[] {
    const { streamingId, messages, streamingPos, stageWrapRef, openCtxForNode } = deps;
    const streamingMsg = streamingId ? messages.find((m) => m.id === streamingId) : undefined;
    const streamingPrompt = legacyStreamingPlaceholderPrompt(streamingId, messages);
    const streamingPlaceholder: Node<CardNodeData>[] = (streamingId && streamingPos?.id === streamingId)
      ? [{
          id: `gen-${streamingId}`,
          type: "card" as const,
          position: { x: streamingPos!.x, y: streamingPos!.y },
          data: {
            node: {
              id: `gen-${streamingId}`, type: "input" as const,
              x: streamingPos!.x, y: streamingPos!.y, w: CARD_W, h: 120,
              generationIds: [], input: true, prompt: streamingPrompt, groupKey: `streaming-${streamingId}`,
              inputStatus: "generating" as const, traceText: streamingMsg?.text || "",
            },
            gens: [], imageUrls: [], prompt: streamingPrompt, isSel: false, naturalSize: undefined,
            onSelect: () => {},
            onOpen: () => {},
            onResize: () => {}, onImgLoaded: () => {},
            onNodeCtx: (e: React.MouseEvent, nn: { id: string }) => {
              const rect = stageWrapRef.current?.getBoundingClientRect();
              openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
            },
          },
        }]
      : [];
    return streamingPlaceholder;
  }

  function legacyBuildDraftInputNodes(deps: {
    pendingInputs: PendingInput[];
    setInspirationEditId: (v: string | null) => void;
    setInspirationEditKind: (v: string) => void;
    setInspirationEditTitle: (v: string) => void;
    setInspirationEditContent: (v: string) => void;
    stageWrapRef: { current: HTMLDivElement | null };
    openCtxForNode: (x: number, y: number, nodeId: string) => void;
  }): Node<CardNodeData>[] {
    const {
      pendingInputs, setInspirationEditId, setInspirationEditKind,
      setInspirationEditTitle, setInspirationEditContent, stageWrapRef, openCtxForNode,
    } = deps;
    const draftInputs: Node<CardNodeData>[] = pendingInputs.map((p, i) => ({
      id: p.id,
      type: "card",
      position: { x: p.x, y: p.y },
      data: {
        node: {
          id: p.id, type: "input", x: p.x, y: p.y, w: CARD_W, h: 120,
          generationIds: [], input: true, prompt: p.prompt, groupKey: `input-${i}`,
          inputStatus: p.status,
        },
        gens: [], imageUrls: [], prompt: p.prompt, isSel: false, naturalSize: undefined,
        onSelect: () => {},
        // draft 卡双击 → 新建灵感卡（不是生成内容）
        onOpen: (nn: { inputStatus?: string; prompt?: string }) => {
          if (nn.inputStatus === "draft") {
            setInspirationEditId("new");
            setInspirationEditKind("preset");
            setInspirationEditTitle(nn.prompt && nn.prompt !== "新输入节点" ? nn.prompt : "");
            setInspirationEditContent(nn.prompt && nn.prompt !== "新输入节点" ? nn.prompt : "");
          }
        },
        onResize: () => {}, onImgLoaded: () => {},
        onNodeCtx: (e: React.MouseEvent, nn: { id: string }) => {
          const rect = stageWrapRef.current?.getBoundingClientRect();
          openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
        },
      },
    }));
    return draftInputs;
  }

  function legacyBuildWfRunNodes(wfRuns: WfRun[]): Node<CardNodeData>[] {
    const wfRunNodes: Node<CardNodeData>[] = wfRuns.map((run) => {
      // 真实提示词/节点名（对齐对话模式）：graph 在提交后随 run-graph 事件带入
      const meta = run.graph ? workflowGenMetadata(run.templateName, run.graph) : null;
      const runPrompt = meta?.prompt || run.templateName;
      const runNodeLabel = run.node ? (() => {
        try {
          const g = (run.graph ?? null) as Record<string, { class_type?: string }> | null;
          const c = g?.[run.node]?.class_type;
          return c ? `${c} (#${run.node})` : `节点 #${run.node}`;
        } catch { return `节点 #${run.node}`; }
      })() : "";
      return {
        id: run.id,
        type: "card",
        position: { x: run.x, y: run.y },
        style: { width: CARD_W, height: 427 },
        data: {
          node: {
            id: run.id, type: "input", x: run.x, y: run.y, w: CARD_W, h: 427,
            generationIds: [], input: true, inputStatus: "generating",
            prompt: runPrompt, groupKey: run.id,
          },
          gens: [], imageUrls: [], prompt: runPrompt,
          isSel: false, naturalSize: undefined,
          wfProgress: run.progress, wfProgressNode: runNodeLabel,
          onSelect: () => {}, onOpen: () => {}, onResize: () => {}, onImgLoaded: () => {},
          // 占位节点无右键菜单（临时节点，任务结束自动清除）
          onNodeCtx: () => {},
        },
      };
    });
    return wfRunNodes;
  }

  const noopCtx = () => {};

  it("buildStreamingPlaceholderNodes：流式占位（含 id 不匹配 → 不投影）", () => {
    const msgs = [user("m0", "画一只猫"), asst("m1", "过程行"), asst("m2", "过程行2")];
    const cases: Array<[string | null | undefined, { id: string; x: number; y: number } | null]> = [
      [null, null],
      [undefined, { id: "m2", x: 1, y: 1 }],
      ["", null],
      ["m2", null],                                  // 无落点缓存 → 不投影
      ["m2", { id: "other", x: 5, y: 6 }],           // id 不一致 → 不投影
      ["m2", { id: "m2", x: 30, y: 40 }],            // 命中 → 投影
      ["m0", { id: "m0", x: -7, y: 0 }],
    ];
    let nonEmpty = 0;
    for (const [streamingId, streamingPos] of cases) {
      const deps = {
        streamingId, messages: msgs, streamingPos,
        stageWrapRef: rectRef(), openCtxForNode: noopCtx,
      };
      const got = buildStreamingPlaceholderNodes(deps);
      const legacy = legacyBuildStreamingPlaceholderNodes(deps);
      expect(strip(got), `streamingId=${String(streamingId)}`).toEqual(strip(legacy));
      if (got.length > 0) nonEmpty++;
    }
    expect(nonEmpty).toBe(2);

    // 钉具体值（不能只靠「两侧同为空」的等价性）：命中格单独断言
    const hit = buildStreamingPlaceholderNodes({
      streamingId: "m2", messages: msgs, streamingPos: { id: "m2", x: 30, y: 40 },
      stageWrapRef: rectRef(), openCtxForNode: noopCtx,
    });
    expect(hit).toHaveLength(1);
    expect(hit[0].id).toBe("gen-m2");
    expect(hit[0].position).toEqual({ x: 30, y: 40 });
    expect(hit[0].data.node.h).toBe(120);
    expect(hit[0].data.node.w).toBe(CARD_W);
    expect(hit[0].data.node.groupKey).toBe("streaming-m2");
    expect(hit[0].data.prompt).toBe("画一只猫");        // 前一条 user 消息
    expect(hit[0].data.node.traceText).toBe("过程行2"); // 该条 assistant 的过程行

    // 前一条 user 不存在（idx=0）→ 提示词空串，但过程行照取
    const noUserBefore = buildStreamingPlaceholderNodes({
      streamingId: "m0", messages: msgs, streamingPos: { id: "m0", x: 1, y: 2 },
      stageWrapRef: rectRef(), openCtxForNode: noopCtx,
    });
    expect(noUserBefore[0].data.prompt).toBe("");
    expect(noUserBefore[0].data.node.traceText).toBe("画一只猫");
  });

  it("buildDraftInputNodes：待输入占位（groupKey 用序号、status 透传）", () => {
    const setters = () => ({
      setInspirationEditId: vi.fn(), setInspirationEditKind: vi.fn(),
      setInspirationEditTitle: vi.fn(), setInspirationEditContent: vi.fn(),
    });
    const cases: PendingInput[][] = [
      [],
      [{ id: "p0", prompt: "草稿", x: 0, y: 0, status: "draft" }],
      [{ id: "p1", prompt: "在跑", x: 10, y: 20, status: "generating" }],
      [
        { id: "p0", prompt: "A", x: 1, y: 2, status: "draft" },
        { id: "p1", prompt: "B", x: 3, y: 4, status: "generating" },
      ],
    ];
    for (const pendingInputs of cases) {
      const deps = { pendingInputs, ...setters(), stageWrapRef: rectRef(), openCtxForNode: noopCtx };
      const got = buildDraftInputNodes(deps);
      const legacy = legacyBuildDraftInputNodes(deps);
      expect(strip(got)).toEqual(strip(legacy));
      got.forEach((n, i) => {
        expect(n.id).toBe(pendingInputs[i].id);
        expect(n.data.node.groupKey).toBe(`input-${i}`);
        expect(n.data.node.inputStatus).toBe(pendingInputs[i].status);
        expect(n.data.prompt).toBe(pendingInputs[i].prompt);
      });
    }
  });

  it("buildWfRunNodes：工作流运转占位（graph/node → 提示词与节点名）", () => {
    const graph = { "3": { class_type: "KSampler" }, "9": { class_type: "SaveImage" } };
    const cases: WfRun[][] = [
      [],
      [{ id: "r1", toolNodeId: "t1", promptId: "p1", templateName: "模板A", progress: null, x: 1, y: 2 }],
      [{ id: "r2", toolNodeId: "t1", promptId: "p2", templateName: "模板A", progress: 42, x: 3, y: 4, graph }],
      [{ id: "r3", toolNodeId: "t1", promptId: "p3", templateName: "模板A", progress: 7, x: 5, y: 6, node: "9", graph }],
      [{ id: "r4", toolNodeId: "t1", promptId: "p4", templateName: "模板A", progress: 7, x: 5, y: 6, node: "77", graph }],
      [{ id: "r5", toolNodeId: "t1", promptId: "p5", templateName: "模板A", progress: 1, x: 8, y: 9, node: "9" }],
      [
        { id: "r6", toolNodeId: "t", promptId: "p6", templateName: "模板B", progress: 0, x: 0, y: 0 },
        { id: "r7", toolNodeId: "t", promptId: "p7", templateName: "模板B", progress: 100, x: 1, y: 1, node: "3", graph },
      ],
    ];
    for (const wfRuns of cases) {
      const got = buildWfRunNodes(wfRuns);
      const legacy = legacyBuildWfRunNodes(wfRuns);
      expect(strip(got)).toEqual(strip(legacy));
      got.forEach((n, i) => {
        expect(n.style).toEqual({ width: CARD_W, height: 427 });
        expect(n.data.node.inputStatus).toBe("generating");
        expect(n.data.wfProgress).toBe(wfRuns[i].progress);
      });
    }
    // 三格可分辨的节点名（有 class_type / 有 node 无 class_type / 无 node）
    const labels = buildWfRunNodes(cases[5]).map((n) => n.data.wfProgressNode);
    expect(labels).toEqual(["节点 #9"]);
  });
});

// ── ② 纯搬移锁：**行为网看不见的槽位**逐字保留 ──────────────────────────────
//
// 为什么不把「所有搬移语句」都放进锁：等价矩阵已逐字段对拍全部**可观测数据**，
// 回调体由 ③ 的回调直调覆盖 —— 把那两类语句再锁一遍 ⇒ 一次变异两道红、丢掉诊断力
// （T1.2/T1.3 教训：两道锁必须**分工**，不许冗余）。
// 真正两道网都看不见的只有一类：**空回调 / 空值槽**——`strip` 把回调统一烙成 "[fn]"
// （无法区分「空回调」与「真回调」），行为测试又只对着有语义的那几个调 ⇒
// 「`onResize: () => {}` 被悄悄换成真处理器」这类改动会静默通过。
describe("② 纯搬移锁：空回调 / 空值槽逐字保留（行为网看不见的槽位）", () => {
  /** [整行去缩进后的原文, 在新属主里应出现的**次数**] —— 钉次数是因为同一个空槽
   *  在别处（如 draft 占位）也有，只判「存在」会掩盖某一处被换成真处理器。 */
  const EMPTY_SLOTS: ReadonlyArray<readonly [string, number]> = [
    // 生成中占位：双击/拉伸不接任何行为（生命周期跟随对话流，不是持久节点）
    ["gens: [], imageUrls: [], prompt: streamingPrompt, isSel: false, naturalSize: undefined,", 1],
    ["onSelect: () => {},", 2],                                  // 生成中占位 + 待输入占位
    ["onOpen: () => {},", 1],                                    // 仅生成中占位（draft 那处有真回调）
    ["onResize: () => {}, onImgLoaded: () => {},", 2],           // 生成中占位 + 待输入占位
    // 待输入占位：只接 onOpen（双击新建灵感卡），拉伸/右键空
    ["gens: [], imageUrls: [], prompt: p.prompt, isSel: false, naturalSize: undefined,", 1],
    // 工作流运转占位：**一个回调都不接**（临时节点，任务结束自动清除）
    ["gens: [], imageUrls: [], prompt: runPrompt,", 1],
    ["onSelect: () => {}, onOpen: () => {}, onResize: () => {}, onImgLoaded: () => {},", 1],
    ["onNodeCtx: () => {},", 1],
  ];

  it("空回调 / 空值槽在新属主逐字保留（按次数钉）", () => {
    const trimmed = SEAM.split("\n").map((l: string) => l.trim());
    for (const [line, count] of EMPTY_SLOTS) {
      const hits = trimmed.filter((l: string) => l === line).length;
      expect(hits, line).toBe(count);
    }
  });
});

// ── ③ 源码形态锁：分层没被绕过 + 回调语义 ───────────────────────────────────
describe("③ 源码形态锁：投影 effect 由接缝组装，回调语义不动", () => {
  /** 定位投影 useEffect 的行区间（锚点唯一：`const nextNodes = [...inputs, ...toolCards, ...`）。 */
  function projectionEffect(text: string): { start: number; end: number; body: string } {
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

  /**
   * **单一可达性闭包锁**（T1.2 教训：形态锁之间不许冗余，一条锁要同时覆盖
   * ①组装 ②内联回退 ③死接缝 —— 否则同一次变异多处红，丢掉「一变异一红」的诊断力）：
   *   · ① 组装：三个 builder 在投影 effect 内**各恰一次**（`name\(`，不是 `name\s*\(`）；
   *   · ② 内联回退：effect 内不得再出现**只属于这 4 块**的搬移语句（把调用换回内联会同时触发 ① 与 ②）；
   *   · ③ 死接缝：接缝若定义却无人调用 → ① 归零；
   *   · 行数：effect 必须真的短了（改造前 581 行；改后实测 498）。
   */
  it("投影 effect 由三个占位 builder 组装（可达性闭包 + 内联回退 + 行数）", () => {
    const { start, end, body } = projectionEffect(CANVAS);

    // ① 组装 / ③ 死接缝
    for (const call of ["buildStreamingPlaceholderNodes(", "buildDraftInputNodes(", "buildWfRunNodes("]) {
      const hits = body.match(new RegExp(call.replace("(", "\\("), "g")) || [];
      expect(hits.length, `${call} 在 effect 内调用次数`).toBe(1);
    }
    // 接线必须保住：落点缓存仍在调用点取（builder 拿不到 ref 内部值）
    expect(body).toContain("streamingPos: streamingPosRef.current,");
    expect(body).toContain("const wfRunNodes: Node<CardNodeData>[] = buildWfRunNodes(wfRuns);");

    // ② 内联回退：只挑**唯一属于这 4 块**的语句（坐标换算三行在 projected/工具卡/剧情/灵感/
    //    参考图里也存在、同为 indent 12，放进缺席名单会误红）
    const UNIQUE_TO_MOVED = [
      "inputStatus: \"generating\" as const, traceText: streamingMsg?.text || \"\",",
      "x: streamingPos!.x, y: streamingPos!.y, w: CARD_W, h: 120,",
      "setInspirationEditTitle(nn.prompt && nn.prompt !== \"新输入节点\"",
      "const g = (run.graph ?? null) as Record<string, { class_type?: string }> | null;",
      "wfProgress: run.progress, wfProgressNode: runNodeLabel,",
      "style: { width: CARD_W, height: 427 },",
      "id: p.id, type: \"input\", x: p.x, y: p.y, w: CARD_W, h: 120,",
      "for (let i = idx - 1; i >= 0; i--)",
      "const runNodeLabel = run.node ?",
      "const streamingMsg = streamingId ? messages.find(",
    ] as const;
    for (const line of UNIQUE_TO_MOVED) expect(body, line).not.toContain(line);

    // 行数
    expect(end - start + 1, "投影 effect 行数（改造前 581）").toBeLessThanOrEqual(520);
  });

  it("WfRun / PendingInput 单一属主在接缝（组件内不得再声明）", () => {
    expect(SEAM).toMatch(/export type WfRun = \{/);
    expect(SEAM).toMatch(/export type PendingInput = /);
    expect(CANVAS).not.toMatch(/^\s*type WfRun = \{/m);
    expect(CANVAS).not.toMatch(/^\s*type PendingInput = /m);
    expect(CANVAS).toContain("type PendingInput, type WfRun,");
  });

  it("占位节点回调语义：坐标换算扣掉 stageWrap 偏移；draft 双击才回填 setter", () => {
    const openCtx = vi.fn();
    const deps = {
      streamingId: "m1",
      messages: [user("m0", "U"), asst("m1", "A")],
      streamingPos: { id: "m1", x: 11, y: 22 },
      stageWrapRef: rectRef(),
      openCtxForNode: openCtx,
    };
    const ph = buildStreamingPlaceholderNodes(deps);
    (ph[0].data.onNodeCtx as unknown as (e: unknown, n: unknown) => void)(
      { clientX: 130, clientY: 90 }, { id: "gen-m1" },
    );
    expect(openCtx).toHaveBeenCalledWith(30, 40, "gen-m1"); // 130-100, 90-50

    // ★ stageWrap 未挂载（rect 为 undefined）→ 偏移回退 0，坐标原样透传。
    //   少了这一格，`rect?.left || 0` 改成 `|| 1` 这类变异会**静默通过**
    //   （第一格 left=100 是真值，`100 || 1` 恒等于 100）—— 红检实测漏网后补的。
    const detached = buildStreamingPlaceholderNodes({ ...deps, stageWrapRef: { current: null } });
    (detached[0].data.onNodeCtx as unknown as (e: unknown, n: unknown) => void)(
      { clientX: 130, clientY: 90 }, { id: "gen-m1" },
    );
    expect(openCtx).toHaveBeenLastCalledWith(130, 90, "gen-m1");
    // left/top 恰为 0：仍是「减 0」而不是「减 1」
    const zeroRect = buildStreamingPlaceholderNodes({
      ...deps,
      stageWrapRef: { current: { getBoundingClientRect: () => ({ left: 0, top: 0 }) } as unknown as HTMLDivElement },
    });
    (zeroRect[0].data.onNodeCtx as unknown as (e: unknown, n: unknown) => void)(
      { clientX: 7, clientY: 9 }, { id: "gen-m1" },
    );
    expect(openCtx).toHaveBeenLastCalledWith(7, 9, "gen-m1");

    const setInspirationEditId = vi.fn();
    const setInspirationEditKind = vi.fn();
    const setInspirationEditTitle = vi.fn();
    const setInspirationEditContent = vi.fn();
    const draft = buildDraftInputNodes({
      pendingInputs: [
        { id: "d1", prompt: "草稿提示", x: 0, y: 0, status: "draft" },
        { id: "g1", prompt: "在跑", x: 0, y: 0, status: "generating" },
      ],
      setInspirationEditId, setInspirationEditKind, setInspirationEditTitle, setInspirationEditContent,
      stageWrapRef: rectRef(), openCtxForNode: openCtx,
    });
    const open = (i: number) => (draft[i].data.onOpen as unknown as (n: unknown) => void)(draft[i].data.node);
    open(0); // draft → 回填 4 个 setter
    expect(setInspirationEditId).toHaveBeenCalledWith("new");
    expect(setInspirationEditKind).toHaveBeenCalledWith("preset");
    expect(setInspirationEditTitle).toHaveBeenCalledWith("草稿提示");
    expect(setInspirationEditContent).toHaveBeenCalledWith("草稿提示");
    open(1); // generating → 不动 setter
    expect(setInspirationEditId).toHaveBeenCalledTimes(1);

    // 「新输入节点」是空文案哨兵：不应被当作提示词回填
    const draft2 = buildDraftInputNodes({
      pendingInputs: [{ id: "d2", prompt: "新输入节点", x: 0, y: 0, status: "draft" }],
      setInspirationEditId, setInspirationEditKind, setInspirationEditTitle, setInspirationEditContent,
      stageWrapRef: rectRef(), openCtxForNode: openCtx,
    });
    (draft2[0].data.onOpen as unknown as (n: unknown) => void)(draft2[0].data.node);
    expect(setInspirationEditTitle).toHaveBeenLastCalledWith("");

    // 占位节点的空回调：不炸、不产生副作用
    const ph2 = buildStreamingPlaceholderNodes(deps);
    (ph2[0].data.onOpen as unknown as (n: unknown) => void)(ph2[0].data.node);
    const wf = buildWfRunNodes([{ id: "r", toolNodeId: "t", promptId: "p", templateName: "T", progress: null, x: 0, y: 0 }]);
    (wf[0].data.onNodeCtx as unknown as (e: unknown, n: unknown) => void)({ clientX: 0, clientY: 0 }, { id: "r" });
    // openCtx 只被三次占位 onNodeCtx 调过（挂载 rect / 未挂载 / left=top=0）；
    // 空回调、onOpen、wfRun 的 onNodeCtx 都不碰它
    expect(openCtx).toHaveBeenCalledTimes(3);
  });
});
