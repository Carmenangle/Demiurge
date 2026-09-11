import type { SyncProgress } from "../api/ai";

export function formatNodeSyncResult(progress: SyncProgress): string {
  const base = `同步完成：共 ${progress.total} 个节点包，本次处理 ${progress.synced} 个、跳过 ${progress.skipped} 个`;
  if (!progress.failed) return `${base}。`;
  const details = progress.failures.length ? `：${progress.failures.join("；")}` : "";
  return `${base}、失败 ${progress.failed} 个${details}。`;
}
