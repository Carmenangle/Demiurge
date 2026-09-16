// canvasWorkflowRun.ts — 画布工作流运转的纯逻辑（不碰 React / DOM / 网络，故可单测）
//
// 2026-09-15 架构审计 C4-A 第 3 步：画布原先自持「提交 → 轮询 → 入库 → 派发完成」整条链
// （`CanvasStageFlow.runWorkflowTask` 里的 `pollWorkflowResult` + `finally` 兜底），与对话侧
// `WorkflowGenerationRuntime.start` 构成**两个属主**。搬到 runtime 之后，画布只剩一件事：
// **把终态翻成事件序列**。本文件就是这件事，也是画布唯一还需要单测的部分
// （`CanvasStageFlow.tsx` / `WorkflowToolModal.tsx` 都没有组件测试 —— 审计 R5）。
//
// 契约要点（与对话侧对齐，2026-09-15 用户要求「对话与画布本质功能一致」）：
// · **没有「入库失败」这一格**。归档失败会被 runtime 吞掉并**保留 pending 继续重试**
//   （画布与对话共用同一条 finalize 链）。改前画布那条 `finally` 会立刻 `untrackCanvasWorkflow`
//   删掉 pending 并 toast「稍后会自动入库」——后端根本没有后台收集器，那句是假的（审计 R2）。
// · **只有 timed_out 保留占位**：ComfyUI 仍在后台运转，擅自删占位会让用户以为任务没了。

/** 画布运转的终态，与 `WorkflowWatchObserver` 的回调一一对应。 */
export type CanvasRunTerminal = "completed" | "empty" | "failed" | "stalled" | "timed_out";

/** 画布对「一个终态」要做的全部动作。收成数据而非散落在 5 个回调里，便于逐格断言。 */
export interface CanvasRunEffects {
  /** 保留「生成中」占位（不派发 done、不标记结束） */
  keepPlaceholder: boolean;
  /** 派发 `laf-canvas-wf-done`：清画布占位 + 通知 `ChatView` 清进度列表（跨组件契约） */
  dispatchDone: boolean;
  /** 停顿早停：清 ComfyUI 队列 + 中断该 prompt（2026-08-29 用户需求） */
  interruptQueue: boolean;
  /** 回读对话快照：产出已入库，画布据此投影出生成内容节点 */
  reloadFromSnapshot: boolean;
  toast: { text: string; kind: "info" | "error" | "success" };
}

export interface CanvasRunDetail {
  images?: number;
  videos?: number;
  audios?: number;
  /** 失败/停顿原因（failed / stalled 用） */
  error?: string;
}

/** 终态表的**执行口**：表里每个字段都有同名方法，且**全部必填**。
 *
 *  2026-09-15 架构审计 P19：`keepPlaceholder` / `interruptQueue` 此前**没有任何生产消费方**
 *  （全仓 grep 只命中本文件定义 + `.test.ts` 断言），真实中断写在两个组件的 `stalled` 回调里
 *  直接调 `interruptComfy` **绕过表** ⇒ 表在这两点是「文档」不是「合同」。
 *  现补执行口：调用方只提供 sink，**表说做什么就做什么**；方法名与表字段一一对应、且为必填，
 *  故将来新增终态或新增字段时，漏接线会**编译不过**（结构性保证，不靠自觉）。
 */
export interface CanvasRunSink {
  /** 表要求保留「生成中」占位（不派发 done、不清画布占位） */
  keepPlaceholder(): void;
  /** 表要求派发 `laf-canvas-wf-done`：清画布占位 + 通知 `ChatView` 清进度列表 */
  dispatchDone(): void;
  /** 表要求停顿早停：清 ComfyUI 队列 + 中断该 prompt（2026-08-29 用户需求） */
  interruptQueue(): void;
  /** 表要求回读对话快照：产出已入库，画布据此投影出生成内容节点 */
  reloadFromSnapshot(): void;
  /** 提示文案（各调用方自己的 toast / onNotify 形态） */
  toast(text: string, kind: CanvasRunEffects["toast"]["kind"]): void;
}

/** 按终态表执行副作用：**表的唯一消费入口**。
 *
 *  顺序对齐改造前两个调用方的观察行为：中断最先（fire-and-forget）→ 提示 → 回读快照 →
 *  保留占位 / 派发 done。不变量：`keepPlaceholder` 与 `dispatchDone` 永不同时为真
 *  （`timed_out` 只保留占位、其余终态必派发——漏派发会让画布占位与 `ChatView` 进度列表永久残留）。
 */
export function runCanvasEffects(effects: CanvasRunEffects, sink: CanvasRunSink): void {
  if (effects.interruptQueue) sink.interruptQueue();
  sink.toast(effects.toast.text, effects.toast.kind);
  if (effects.reloadFromSnapshot) sink.reloadFromSnapshot();
  if (effects.keepPlaceholder) sink.keepPlaceholder();
  if (effects.dispatchDone) sink.dispatchDone();
}

const NO_MEDIA_TEXT =
  "运转完成但未拿到图片/视频/音频（可能输出节点未正确配置），请到 ComfyUI 控制台查看。";
const TIMEOUT_TEXT =
  "运转仍在进行中（已超过预期时长）。画布会保留「生成中」节点，请稍后在 ComfyUI 控制台查看或重新运转。";

export function canvasRunEffects(
  terminal: CanvasRunTerminal, detail: CanvasRunDetail = {},
): CanvasRunEffects {
  switch (terminal) {
    case "completed": {
      const images = detail.images || 0;
      const counts = `${images} 张图`
        + (detail.videos ? `、${detail.videos} 个视频` : "")
        + (detail.audios ? `、${detail.audios} 个音频` : "");
      return {
        keepPlaceholder: false, dispatchDone: true, interruptQueue: false, reloadFromSnapshot: true,
        toast: { text: `运转完成，${counts}已入库。`, kind: "info" },
      };
    }
    case "empty":
      return {
        keepPlaceholder: false, dispatchDone: true, interruptQueue: false, reloadFromSnapshot: false,
        toast: { text: NO_MEDIA_TEXT, kind: "error" },
      };
    case "failed":
      return {
        keepPlaceholder: false, dispatchDone: true, interruptQueue: false, reloadFromSnapshot: false,
        toast: { text: `运转失败：${detail.error || "未知错误"}`, kind: "error" },
      };
    case "stalled":
      return {
        keepPlaceholder: false, dispatchDone: true, interruptQueue: true, reloadFromSnapshot: false,
        toast: { text: `运转失败：${detail.error || "长时间未开始加载节点，已停止"}`, kind: "error" },
      };
    case "timed_out":
      return {
        keepPlaceholder: true, dispatchDone: false, interruptQueue: false, reloadFromSnapshot: false,
        toast: { text: TIMEOUT_TEXT, kind: "info" },
      };
  }
}

// ── 超时合同：媒体种类推导（审计 R8）─────────────────────────────────────────
//
// 画布 / 弹窗 / 对话 `/w` 三处都硬编码 `mediaType="image"`，于是**视频模板也吃图片的 20 分钟硬上限**
// （`pollSchedule`：图片 150×2s + 60×15s = 20min；视频 450×2s + 180×15s = 60min）。
// 模板结构里没有媒体种类字段（`api/workflows.ts:87-101` 只有 `output_node_ids`），
// 所以只能从抓取图的节点类名推导。
//
// **判错方向是安全的**：把图片任务误判成视频，只是「等得更久」（20min → 60min，用户看到 still_running）；
// 反过来（视频判成图片）才是今天这个缺陷。故本函数只做「向上升级」，默认 image。

/** 明确的视频合成/保存节点（不含 SaveAnimatedWEBP/GIF 这类动图，避免把动图任务误升级）。 */
const VIDEO_OUTPUT_NODES = /^(VHS_VideoCombine|SaveVideo|CreateVideo|SaveWebM|SaveWEBM)$/;

/** 明确的音频保存/合成节点。 */
const AUDIO_OUTPUT_NODES = /^(SaveAudio|SaveAudioMP3|SaveAudioOpus|SaveAudioFLAC|VHS_AudioCombine)$/;

export function deriveWorkflowMediaType(graph: unknown): "image" | "video" | "audio" {
  const classes = workflowNodeClassTypes(graph);
  if (classes.some((name) => VIDEO_OUTPUT_NODES.test(name))) return "video";
  if (classes.some((name) => AUDIO_OUTPUT_NODES.test(name))) return "audio";
  return "image";
}

/** 抓取图为 ComfyUI API 格式 `{ <id>: { class_type, inputs } }`；畸形输入一律当空图。 */
function workflowNodeClassTypes(graph: unknown): string[] {
  if (!graph || typeof graph !== "object") return [];
  const names: string[] = [];
  for (const node of Object.values(graph as Record<string, unknown>)) {
    const classType = (node as { class_type?: unknown } | null | undefined)?.class_type;
    if (typeof classType === "string") names.push(classType);
  }
  return names;
}

// ── 占位/进度条上的「运转任务名」 ────────────────────────────────────────────

/** 画布占位节点与对话框进度条上显示的运转任务名。
 *
 *  2026-09-15（C4-A Step 3/4）：pending 的 `prompt` 字段自画布/弹窗收口到 runtime 起，存的是**真实提示词**
 *  （归档要用，见 `useChatSession.resolveGenerationPrompt`）——改前那三处是拿它当「模板名」用的
 *  （`trackCanvasWorkflow(..., templateName, ...)`）。所以恢复/展示路径必须改从 `regeneration` 快照取模板名
 *  （画布与弹窗运转都会落 `kind:"workflow"` 快照），回退链保留 `prompt` 以兼容收口前的旧 pending。
 *
 *  这是**不可见路径**（只在「切走仓库再切回、且任务仍在跑」时渲染一次），故抽成纯函数上锁。
 */
export function pendingRunLabel(pending: {
  prompt?: string;
  regeneration?: { kind: string; templateName?: string };
}): string {
  const fromRegeneration = pending.regeneration?.kind === "workflow"
    ? pending.regeneration.templateName : "";
  return fromRegeneration || pending.prompt || "工作流";
}
