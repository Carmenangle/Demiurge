// canvasPlaceholderNodes.ts — 画布「占位节点」投影 builder（单一属主）
//
// 原内联在 `views/CanvasStageFlow.tsx` 的投影 useEffect 里（该 effect 改造前 584 行）。
// 这里只放**占位/临时**节点的构造：生成中占位（对话流 streamingId / 工作流运转 wfRuns）
// 与待输入占位（draft 输入卡）。它们生命周期短、任务结束即被真实产出节点替换，
// 故与持久化节点（剧情楼层/灵感卡/参考图/工具卡/投影内容）分开归属。
//
// 结构性事实：这三个 constructor **不落盘、不改 ref**，只把「当前 state」映射成 ReactFlow 节点；
// 唯一的读写发生在回调里（双击/右键），由调用方传入的 setter / ref / openCtxForNode 承担。
import type { RefObject } from "react";
import type { Node } from "@xyflow/react";
import { CARD_W, type CardNodeData } from "../components/canvas/CanvasTypes";
import type { CanvasNode, InspirationKind } from "./canvasRuntime";
import { workflowGenMetadata } from "./regeneration";
import type { ChatMessage } from "../types/chat";

/** 待输入占位卡（draft 输入节点 / 生成中占位）。原为 CanvasStageFlow 组件内局部 type。 */
export type PendingInput = { id: string; prompt: string; x: number; y: number; status: "draft" | "generating" };

/** 工作流运转任务（画布占位节点 + 对话框进度条共用一条）。原为 CanvasStageFlow 组件内局部 type。 */
export type WfRun = {
  id: string; toolNodeId: string; promptId: string; templateName: string;
  progress: number | null; node?: string; x: number; y: number;
  /** 提交时的 API prompt 图（{id:{class_type,inputs}}）：占位节点显示真实提示词/节点名用 */
  graph?: unknown;
};

/** 画布节点回调共用的「右键弹菜单」签名（节点 id + 屏幕坐标换算）。 */
export type OpenCtxForNode = (x: number, y: number, nodeId: string) => void;

/**
 * 生成中占位的提示词：取 streamingId 之前**最近一条 user 消息**的文本。
 * 找不到（streamingId 为空 / 历史上没有 user 消息）→ 空串。
 */
export function streamingPlaceholderPrompt(
  streamingId: string | null | undefined,
  messages: ChatMessage[],
): string {
  if (!streamingId) return "";
  const idx = messages.findIndex((m) => m.id === streamingId);
  for (let i = idx - 1; i >= 0; i--) if (messages[i].role === "user") return messages[i].text;
  return "";
}

/**
 * ★ 生成中占位节点（对话流 streamingId 投影）：画布输入栏与对话模式同源发送，
 *   占位节点表面显示调度主管委派过程行（traceText）+ 生成中；生命周期跟随对话流，
 *   切走再切回不丢失，生成结束即被真实产出节点（或剧情楼层）替换。
 *
 * `streamingPos` = `streamingPosRef.current`（落点缓存）：id 与 streamingId 不一致
 * （用户已切到别处 / 缓存属上一轮）→ 不投影，返回空数组。
 */
export function buildStreamingPlaceholderNodes(deps: {
  streamingId: string | null | undefined;
  messages: ChatMessage[];
  streamingPos: { id: string; x: number; y: number } | null;
  stageWrapRef: RefObject<HTMLDivElement | null>;
  openCtxForNode: OpenCtxForNode;
}): Node<CardNodeData>[] {
  const { streamingId, messages, streamingPos, stageWrapRef, openCtxForNode } = deps;
  const streamingMsg = streamingId ? messages.find((m) => m.id === streamingId) : undefined;
  const streamingPrompt = streamingPlaceholderPrompt(streamingId, messages);
  return (streamingId && streamingPos?.id === streamingId)
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
          onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
            const rect = stageWrapRef.current?.getBoundingClientRect();
            openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
          },
        },
      }]
    : [];
}

/**
 * 待输入占位节点（draft 输入卡）：position/status 直接来自 pendingInputs，不走 layoutNodes。
 * 双击 draft 卡 → 打开「新建灵感卡」（输入节点不是生成内容）。
 */
export function buildDraftInputNodes(deps: {
  pendingInputs: PendingInput[];
  setInspirationEditId: (v: string | null) => void;
  setInspirationEditKind: (v: InspirationKind) => void;
  setInspirationEditTitle: (v: string) => void;
  setInspirationEditContent: (v: string) => void;
  stageWrapRef: RefObject<HTMLDivElement | null>;
  openCtxForNode: OpenCtxForNode;
}): Node<CardNodeData>[] {
  const {
    pendingInputs, setInspirationEditId, setInspirationEditKind,
    setInspirationEditTitle, setInspirationEditContent, stageWrapRef, openCtxForNode,
  } = deps;
  return pendingInputs.map((p, i) => ({
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
      onOpen: (nn: CanvasNode) => {
        if (nn.inputStatus === "draft") {
          setInspirationEditId("new");
          setInspirationEditKind("preset");
          setInspirationEditTitle(nn.prompt && nn.prompt !== "新输入节点" ? nn.prompt : "");
          setInspirationEditContent(nn.prompt && nn.prompt !== "新输入节点" ? nn.prompt : "");
        }
      },
      onResize: () => {}, onImgLoaded: () => {},
      onNodeCtx: (e: React.MouseEvent, nn: CanvasNode) => {
        const rect = stageWrapRef.current?.getBoundingClientRect();
        openCtxForNode(e.clientX - (rect?.left || 0), e.clientY - (rect?.top || 0), nn.id);
      },
    },
  }));
}

/**
 * ★ 工作流「生成中」占位节点：运转任务存活期间显示，完毕由生成内容节点替换。
 *   通用 9:16 占位（与 input generating 占位同款外观 + 模板名 + 实时进度），
 *   不绑定工作流模板形态——剧情生成/apikey 生成/工作流生成等任何任务都长这样。
 *
 * 纯函数：入参只有 wfRuns，无 ref/无 setter。
 */
export function buildWfRunNodes(wfRuns: WfRun[]): Node<CardNodeData>[] {
  return wfRuns.map((run) => {
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
}
