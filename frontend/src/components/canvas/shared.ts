// canvas/shared.ts — 画布跨组件共享状态（不依赖 ReactFlow，避免破坏 lazy loading）
export const globalPendingToolCreates: Array<{
  id: string; templateId: string; templateName: string; estimatedNodeCount: number;
}> = [];

/**
 * 画布（CanvasStageFlow）是否挂载。
 * - 挂载中：laf-canvas-workflow-tool 事件由画布自身监听消费，ChatView 兜底监听不再写入
 *   globalPendingToolCreates，避免同一次选择被双消费（切走再切回出现重复工具卡）。
 * - 未挂载：ChatView 兜底写入 globalPendingToolCreates，画布挂载时消费。
 * StrictMode 下 mount→cleanup→mount 会短暂翻转，同步序列内无事件派发，安全。
 */
export const canvasBridge = { canvasMounted: false };
