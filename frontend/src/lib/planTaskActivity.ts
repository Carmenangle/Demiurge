import { apiGet, apiPost } from "../api/client";

// Autopilot 计划任务（P2–P4）：后台活动面板的轮询源 + 审批/取消动作。
// 后端真源是 /api/plans（plan_tasks），此处只做轮询订阅与动作转发。

export interface PlanTaskStep {
  seq: number;
  step_id: string;
  operation: string;
  status: "pending" | "running" | "done" | "failed" | "blocked" | "skipped";
  attempts: number;
  last_error: string;
  outputs?: Record<string, unknown>;
}

export interface PlanTask {
  id: string;
  repo_id: string;
  output_dir: string;
  intent: string;
  status: "queued" | "running" | "awaiting_approval" | "blocked" | "done" | "partial" | "error" | "cancelled";
  error: string;
  created_at: number;
  updated_at: number;
  progress?: string;        // 后端算好的活动进度（含图片级进度），如 "2/4 步 · 图 3/14"
  images_total?: number;
  images_done?: number;
  merged_count?: number;     // 同意图终态任务聚合数（1=未聚合）
  steps: PlanTaskStep[];
}

export interface PlanTaskActivity {
  id: string;
  repo_id: string;   // 计划所属会话（后台活动点击跳转用）
  intent: string;
  status: PlanTask["status"];
  progress: string;
  needsApproval: boolean;
}

const listeners = new Set<(items: PlanTaskActivity[]) => void>();
let cache: PlanTaskActivity[] = [];
let timer: ReturnType<typeof setInterval> | null = null;

const STATUS_LABEL: Record<PlanTask["status"], string> = {
  queued: "计划排队中",
  running: "计划执行中",
  awaiting_approval: "计划待审批",
  blocked: "计划受阻",
  done: "计划已完成",
  partial: "计划部分完成",
  error: "计划失败",
  cancelled: "计划已取消",
};

function fromTask(task: PlanTask): PlanTaskActivity {
  const done = task.steps.filter((s) => s.status === "done" || s.status === "skipped").length;
  const merged = task.merged_count && task.merged_count > 1 ? `（共 ${task.merged_count} 次）` : "";
  return {
    id: task.id,
    repo_id: task.repo_id,
    intent: `${task.intent}${merged}`,
    status: task.status,
    progress: task.progress || `${done}/${task.steps.length} 步`,
    needsApproval: task.status === "awaiting_approval",
  };
}

function publish() {
  listeners.forEach((listener) => listener([...cache]));
}

async function poll() {
  try {
    const tasks = await apiGet<PlanTask[]>("/plans?limit=10");
    cache = tasks.map(fromTask);
    publish();
  } catch { /* 后端离线：保留上次快照 */ }
}

export function subscribePlanTaskActivities(listener: (items: PlanTaskActivity[]) => void): () => void {
  listeners.add(listener);
  if (!timer) {
    timer = setInterval(poll, 5000);
    void poll();
  } else {
    listener([...cache]);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export function getPlanTask(taskId: string) {
  return apiGet<PlanTask>(`/plans/${taskId}`);
}

export function approvePlanTask(taskId: string) {
  return apiPost<{ lease_id: string; ttl_seconds: number }>(`/plans/${taskId}/approve`, {});
}

export function cancelPlanTask(taskId: string) {
  return apiPost<{ ok: boolean }>(`/plans/${taskId}/cancel`, {});
}
