import { apiGet, apiPost } from "./client";

/** 后端统一后台任务（2026-10-18）：视觉索引/数据表补充/缩略图等长时操作的进度。 */
export interface JobInfo {
  id: string;
  kind: string;
  label: string;
  total: number;
  done: number;
  status: "running" | "done" | "failed" | "cancelled" | string;
  error: string;
  /** 收尾摘要（如「成功 240 / 失败 5」）；running 时为空。 */
  detail?: string;
  started_at: number;
  updated_at: number;
}

// 轮询类接口必须带超时：后端挂起时否则 await 永不返回，轮询链会静默断掉（面板停在旧快照）。
const POLL_TIMEOUT_MS = 8000;

export function listJobs() {
  return apiGet<{ jobs: JobInfo[] }>("/jobs", POLL_TIMEOUT_MS);
}

export function cancelJob(id: string) {
  return apiPost<{ ok: boolean }>(`/jobs/${encodeURIComponent(id)}/cancel`, {});
}

/**
 * 客户端驱动的 job 上报（2026-10-19）：跑在浏览器里的长操作（单图/批量反推、视觉描述）
 * 登记进同一个注册表，「后台活动」面板才能显示它的进度与成败。
 * 不登记 ⇒ 面板对它完全瞎眼（用户实锤：点了反推，右侧「后台活动」什么都没有）。
 * kind 必须在后端 CLIENT_KINDS 白名单里。
 */
export function createJob(kind: string, label: string, total: number) {
  return apiPost<{ ok: boolean; job_id: string }>("/jobs", { kind, label, total });
}

export function tickJob(id: string, n = 1) {
  return apiPost<{ ok: boolean }>(`/jobs/${encodeURIComponent(id)}/tick`, { n });
}

export function finishJob(id: string, status: "done" | "failed" | "cancelled", opts: { error?: string; detail?: string } = {}) {
  return apiPost<{ ok: boolean }>(`/jobs/${encodeURIComponent(id)}/finish`, {
    status, error: opts.error ?? "", detail: opts.detail ?? "",
  });
}
