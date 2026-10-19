// 后端统一后台任务：轮询 GET /jobs，把视觉索引/数据表补充/缩略图等**长时操作**的进度
// （X/N）暴露给「后台活动」面板(SupportWidget)展示进度条（2026-10-18）。
// 与 comfyBackgroundActivity 并列：那个管 ComfyUI 出图（纯前端 localStorage），这个管后端 Job。
import { listJobs, type JobInfo } from "../api/jobs";

export type JobActivity = JobInfo;

const POLL_MS = 1500;
const listeners = new Set<(items: JobActivity[]) => void>();
let snapshot: JobActivity[] = [];
let timer: ReturnType<typeof setInterval> | null = null;

async function refresh() {
  try {
    const r = await listJobs();
    snapshot = (r.jobs || []).slice();
  } catch {
    return; // 后端离线/重启：保留上次快照，不清空（避免面板闪烁）
  }
  listeners.forEach((fn) => fn(snapshot));
}

function ensurePolling() {
  if (timer) return;
  void refresh();
  timer = setInterval(() => { void refresh(); }, POLL_MS);
}

export function subscribeJobActivities(listener: (items: JobActivity[]) => void) {
  listeners.add(listener);
  ensurePolling();
  listener(snapshot);
  return () => { listeners.delete(listener); };
}

/** 任务类型 → 面板标题。 */
export const JOB_KIND_LABEL: Record<string, string> = {
  visual_index: "视觉索引",
  thumbnails: "补缩略图",
  sync_index: "仓库同步",
  tag_infer: "反推打标",
  vlm_describe: "视觉描述",
};
