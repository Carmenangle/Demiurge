// runCanvasWorkflow.ts — 画布 / 弹窗「运转工作流」编排的**单一属主**
//
// 2026-09-15 架构审计 P10（F1，Modular/Testable Broken 主因）：
// `views/CanvasStageFlow.tsx` 的 `runWorkflowTask` 与 `components/WorkflowToolModal.tsx` 的 `handleRun`
// 此前**逐句重复**同一条编排 ——
//   `submitGraph` → 提交提示 → 派发 `laf-canvas-wf-run-graph` → `subscribeProgress`（派发 `laf-canvas-wf-progress`）
//   → `workflowGenMetadata` → `workflowRegenerationSnapshot` → 组装 `WorkflowWatchObserver`（5 个终态回调）
//   → `getRuntime(threadId).start(...)`
// 两份副本各自演化（P19 之前 `interruptQueue` 就只在弹窗侧补过）。本文件是这条链的**唯一属主**，
// 两个组件只剩「给 `input` + 提供 `sink`」。
//
// ⚠ **两条路径有刻意差异，不得统一**（审计原文：「把『是否预检』显式做成入参开关，**不要**统一成其中一侧的行为」）：
//   · `precheckComfyStatus` —— 弹窗在提交前预检 ComfyUI 是否已启动（未启动 → 专有文案早退）；
//     画布**不预检**，让 `submitGraph` 自己抛错走「运转失败：…」分支。
//   · `requireComfyUrl` —— 画布把「未抓到画布内容」与「未配置 ComfyUI 地址」合成同一早退；
//     弹窗只判 `captured`（地址为空时由 `comfyStatus` 失败报「ComfyUI 未启动」）。
//   · `emitInitialProgress` —— 弹窗订阅后补发一次 `progress: null`（进度条先落「未知」）；画布没有这一步。
//   · `missingCapturedText` —— 这句文案两处**本来就不同**，逐字保留。
//   · `nodeLabel` —— 弹窗额外回退 `wfDraft`（UI 格式）；画布只认抓取图（API 格式）。
// 差异逐条被 `runCanvasWorkflow.contract.test.ts` 锁死在两个调用点的字面值上；
// 行为面（调用顺序 / 事件 payload / 文案逐字）由 `runCanvasWorkflow.test.ts` 的 mock 行为网覆盖。
//
// 分工：**终态该做什么**归 `canvasWorkflowRun.ts` 的终态表 + `runCanvasEffects` 执行口（P19）；
// 本文件只负责「链本身」与「把终态交给执行口」。
//
// P11（同日）：`completed` / `empty` 的**空判定**用 observer 的**第三参 `produced`**（权威值，
// runtime 透传自归档链 `finalize` 的返回值，与对话侧同源），不按 `counts` 重算 —— 详见该回调处的注释。

import { comfyStatus, interruptComfy, submitGraph, type GenResult } from "../api/comfyui";
import { subscribeProgress } from "./comfyProgress";
import {
  getRuntime, type PendingGeneration, type WorkflowWatchObserver,
} from "./workflowGenerationRuntime";
import { canvasRunEffects, deriveWorkflowMediaType, runCanvasEffects, type CanvasRunEffects } from "./canvasWorkflowRun";
import { workflowGenMetadata, workflowRegenerationSnapshot } from "./regeneration";

/** 归档回调：与对话共用同一条 finalize 链（`useChatSession.finalizeGeneration`）。 */
export type CanvasWorkflowFinalize = (
  result: GenResult, pending: PendingGeneration,
) => Promise<boolean>;

export interface CanvasWorkflowInput {
  /** 抓取图（ComfyUI API 格式 `{id:{class_type,inputs}}`）；falsy = 未抓到。 */
  captured: unknown;
  comfyuiUrl: string;
  /** 模板名（元数据 + 进度事件带上，供占位节点/进度条展示）。 */
  templateName?: string;
  /** UI 格式草稿图（元数据回退用：`captured` 提取为空时由它兜底）。 */
  wfDraft?: unknown;
  /** 线程键（P17 单一属主 `threadKey`）：pending 判重域与 `owner` 都用它。 */
  threadId: string;
  outputDir?: string;
  /** 运转任务 id：画布占位节点 / 对话框进度条按它关联；弹窗可缺省。 */
  runId?: string;
  /** 归档链；缺省 → 早退（画布「归档链路未接线」兜底）。 */
  finalizeWorkflow?: CanvasWorkflowFinalize | null;
  /** 节点 id → 显示名；缺省用 `captured` 推导（弹窗传自己的 `wfNodeLabel`）。 */
  nodeLabel?: (id: string) => string;
  // ── 以下四个是**刻意差异开关**，两处取值相反，改动前请读文件头 ──────────────
  /** 【不得统一】提交前是否预检 ComfyUI 已启动：弹窗 true / 画布 false */
  precheckComfyStatus: boolean;
  /** 【不得统一】「未抓到画布内容」是否也覆盖 comfyuiUrl 为空：画布 true / 弹窗 false */
  requireComfyUrl: boolean;
  /** 【不得统一】订阅后是否补发一次「进度未知」事件：弹窗 true / 画布 false */
  emitInitialProgress: boolean;
  /** 【不得统一】「未抓到画布内容」的逐字文案（两处本来就不同） */
  missingCapturedText: string;
}

/**
 * 调用方提供的副作用出口。**必填三项是所有路径共有的跨组件契约**（漏派发 `done`
 * 会让画布占位节点与 `ChatView` 进度列表永久残留 —— 审计 R1）；带 `?` 的三项是弹窗专有
 * 的本地 React 态（画布没有这些状态，不传即可，不是「可选项忘了接线」）。
 */
export interface CanvasWorkflowSink {
  /** 提示：画布 = `showToast(text, kind)`；弹窗 = `onNotify(text)`（忽略 kind，与改前一致） */
  notify(text: string, kind: CanvasRunEffects["toast"]["kind"]): void;
  /** 派发 `laf-canvas-wf-done`：清画布占位 + 通知 `ChatView` 清进度列表 */
  dispatchDone(): void;
  /** 回读对话快照：产出已入库，画布据此投影出生成内容节点 */
  reloadFromSnapshot(): void;
  /** 置忙（仅弹窗有本地忙碌态）：在**预检之前**调用；早退/终态由 `teardown` 收回 */
  beginBusy?(): void;
  /** 终态/早退收尾：清本地 React 态（仅弹窗）；在**退订之前**调用 */
  teardown?(): void;
  /** 本地进度（仅弹窗进度条）：`subscribeProgress.onProgress` 里同步 */
  onProgressTick?(progress: number, node?: string): void;
  /** 本地当前节点（仅弹窗进度条）：切换节点时同步 */
  onNodeTick?(node: string): void;
}

/** 预检失败（弹窗专有路径）：ComfyUI 未启动。 */
const COMFY_DOWN_TEXT = "ComfyUI 未启动。请先启动 ComfyUI，或在「设置」填写路径由工具自动拉起。";
/** 归档链未接线（画布专有兜底）：明说并收工，绝不静默产出一张不落库的图。 */
const UNWIRED_TEXT = "画布归档链路未接线，无法运转工作流（请刷新页面重试）";
/** 任务在 ComfyUI 侧已丢失（重启/队列被清）时的失败文案。 */
const TASK_LOST_TEXT = "出图任务已丢失（ComfyUI 可能已重启）";

/** 抓取图 → 节点显示名（`class_type (#id)`）；画布路径的默认实现。 */
function defaultNodeLabel(captured: unknown): (id: string) => string {
  return (nid: string): string => {
    try {
      const g = captured as Record<string, { class_type?: string }>;
      const c = g?.[nid]?.class_type;
      return c ? `${c} (#${nid})` : `节点 #${nid}`;
    } catch { return `节点 #${nid}`; }
  };
}

/**
 * 提交 → 订阅进度 → 交给 runtime 守望 → 终态翻成事件。
 *
 * **同步返回 void**（内部起 async IIFE）：与改造前画布 `runWorkflowTask` 一致，
 * 两个调用点都（且只能）拿到「已受理」，终态一律经 observer 回调。
 */
export function runCanvasWorkflow(input: CanvasWorkflowInput, sink: CanvasWorkflowSink): void {
  const captured = input.captured;
  let stopProg: (() => void) | null = null;
  let settled = false;   // 终态/早退只收工一次（runtime 每个任务只给一个终态；此处防御性幂等）

  // 早退：文案不是「运转失败：…」形态（未抓取 / ComfyUI 未启动 / 归档链未接线），
  // 不套终态表，但仍须清本地态 + 派发 done —— 调用方**已经**派发过 `laf-canvas-wf-run`，
  // 不回 done 的话画布占位与进度条条目会永久残留（画布自己看不出来）。
  const settleEarly = (text: string): void => {
    if (settled) return;
    settled = true;
    sink.notify(text, "error");
    sink.teardown?.();
    stopProg?.();
    stopProg = null;
    sink.dispatchDone();
  };

  // 终态收工（幂等）：先清本地态 + 退订，其余副作用一律交给终态表的执行口
  // `runCanvasEffects` —— 表说做什么就做什么（P19：表 → sink 单一链路）。
  const settle = (effects: CanvasRunEffects, promptId = ""): void => {
    if (settled) return;
    settled = true;
    sink.teardown?.();
    stopProg?.();
    stopProg = null;
    runCanvasEffects(effects, {
      // 占位靠「不派发 done」保留（两侧本就如此）；显式留口，便于将来加「后台继续」标记。
      keepPlaceholder: () => { /* 保留占位：不做任何清理 */ },
      dispatchDone: () => sink.dispatchDone(),
      interruptQueue: () => {
        // 停顿早停：清 ComfyUI 队列 + 中断该 prompt（原在 stalled 回调直接调，现由表驱动）
        void interruptComfy(input.comfyuiUrl || "", promptId).catch(() => undefined);
      },
      reloadFromSnapshot: () => {
        try { sink.reloadFromSnapshot(); } catch { /* 回读快照失败不阻断收工 */ }
      },
      toast: (text, kind) => sink.notify(text, kind),
    });
  };

  if (!captured || (input.requireComfyUrl && !input.comfyuiUrl)) {
    settleEarly(input.missingCapturedText);
    return;
  }
  const finalizeFlow = input.finalizeWorkflow;
  if (!finalizeFlow) {
    settleEarly(UNWIRED_TEXT);
    return;
  }
  sink.beginBusy?.();

  void (async () => {
    try {
      if (input.precheckComfyStatus) {
        const st = await comfyStatus(input.comfyuiUrl);
        if (!st.running) {
          settleEarly(COMFY_DOWN_TEXT);
          return;
        }
      }
      const r = await submitGraph(captured, input.comfyuiUrl);
      const promptId = r.prompt_id || "";
      // 提交提示（两处逐字相同：**改这里等于同时改两条路径**）
      sink.notify(`已提交到 ComfyUI（prompt_id: ${promptId}，${r.node_count} 个节点），正在运转工作流…`, "info");
      // 占位节点进入「已提交」态 + 对话框进度条出现（graph 随 run 事件带给画布，占位节点显示真实提示词）
      if (input.runId) {
        try {
          window.dispatchEvent(new CustomEvent("laf-canvas-wf-run-graph", {
            detail: { runId: input.runId, graph: captured },
          }));
        } catch { /* ignore */ }
      }
      // 直连 ComfyUI /ws 订阅本 prompt 的步进进度（占位节点 + 对话框进度条共用）
      // 节点名对齐对话模式：`class_type (#id)`，事件同时携带原始 id（node）与标签（nodeLabel）
      const nodeLabel = input.nodeLabel ?? defaultNodeLabel(captured);
      stopProg = subscribeProgress(input.comfyuiUrl || "", promptId, {
        onProgress: (pct, pr) => {
          sink.onProgressTick?.(pct, pr.node);
          if (!input.runId) return;
          window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
            detail: { taskId: input.runId, promptId, progress: pct, node: pr.node || "", nodeLabel: pr.node ? nodeLabel(pr.node) : "", templateName: input.templateName || "" },
          }));
        },
        onNode: (nid) => {
          sink.onNodeTick?.(nid);
          if (!input.runId) return;
          window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
            detail: { taskId: input.runId, promptId, node: nid, nodeLabel: nodeLabel(nid), templateName: input.templateName || "" },
          }));
        },
      });
      // 【弹窗专有】进度条先落到「未知」（画布没有这一步）
      if (input.emitInitialProgress && input.runId) {
        window.dispatchEvent(new CustomEvent("laf-canvas-wf-progress", {
          detail: { taskId: input.runId, promptId, progress: null, node: "", templateName: input.templateName || "" },
        }));
      }
      // 元数据与对话模式同源：真实提示词 + 主模型 + LoRA（详情面板展示用）
      const meta = workflowGenMetadata(input.templateName || "", captured, input.wfDraft);
      const promptText = meta.prompt || input.templateName || "工作流生成";
      // ★ 必须构造 regeneration 快照：对话 finalize 链只从 regeneration 取模板/模型/LoRA 元数据，
      //   不传就等于把它们丢掉（详情面板会显示空模板名）。
      const regeneration = workflowRegenerationSnapshot(
        captured, input.comfyuiUrl, [], promptText, meta.templateName, meta.modelName, meta.loraNames,
      );
      const observer: WorkflowWatchObserver = {
        // 归档：对话同一条链（含判重域与「未持久化 → 保留 pending 重试」语义）
        finalize: (result, pending) => finalizeFlow(result, pending),
        // P11：空判定用**权威值** `produced`（runtime 由归档链 `finalize` 的返回值经
        // `observer.completed(result, pending, produced)` 透传，对话侧 `useChatSession.ts` 消费的就是它），
        // **不再**按 `counts` 重算 —— 两套空判定会分叉：
        //   · 有媒体但归档链没产出可见内容（`workflowMessages` 为空）⇒ 旧实现误报「N 张图已入库」；
        //   · 无媒体但有文本产出 ⇒ 旧实现误报「未拿到图片/视频/音频」。
        // `counts` 现在只用于 completed 文案里的产出数。
        completed: (result, _pending, produced) => {
          const counts = {
            images: result.images?.length || 0,
            videos: result.videos?.length || 0,
            audios: result.audios?.length || 0,
          };
          settle(canvasRunEffects(produced ? "completed" : "empty", counts));
        },
        failed: (_pending, stage, error) => {
          const text = stage === "task_not_found" ? TASK_LOST_TEXT : error;
          settle(canvasRunEffects("failed", { error: text }));
        },
        // 队列卡死：按失败处理（2026-08-29 用户需求）；「清队列 + 中断」由表驱动（interruptQueue）
        // P13：文案由 runtime 经第二参转发（不再 import STALLED_POLL_MESSAGE 自己塞回）
        stalled: (pending, error) => settle(canvasRunEffects("stalled", { error }), pending.prompt_id),
        // ComfyUI 仍在后台运转：保留「生成中」占位并告知用户（表里 keepPlaceholder=true）
        timedOut: () => settle(canvasRunEffects("timed_out")),
        // R7：5 分钟「释放忙碌」点画布/弹窗都无中间态（改前也没有），保持无副作用。
        released: () => { /* no-op */ },
      };
      // pending 生命周期与轮询交给 runtime（同一 threadId 单例，与对话恢复共享判重域）。
      getRuntime(input.threadId).start({
        promptId, comfyuiUrl: input.comfyuiUrl,
        regeneration, prompt: promptText,
        // P17：线程键与 owner 一律用归一键 `threadId`（首页 = "home"）；裸 `repoId` 仍走 API/落盘路径。
        owner: { threadId: input.threadId, repoId: input.threadId, outputDir: input.outputDir || "" },
        // R8：模板结构没有媒体种类字段，只能从抓取图节点类名推导（默认 image，只向上升级），
        // 否则视频模板会吃图片的 20 分钟硬上限。
        mediaType: deriveWorkflowMediaType(captured),
        runId: input.runId,
      }, observer);
    } catch (e) {
      // 提交/订阅阶段抛错：此时还没进 runtime，没有终态回调 → 自行收工清占位。
      settleEarly(`运转失败：${(e as Error).message}`);
    }
  })();
}
