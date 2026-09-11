import {
  cancelWorkflowBuildTask, enqueueWorkflowBuildTask, listWorkflowBuildTasks,
  type BuildResult, type BuildTurn, type WorkflowBuildTask,
} from "../api/ai";

export type WorkflowBuildMode = "direct" | "module" | "workflow" | "plan";
export type WorkflowBuildActivity = {
  id: string; sessionId: string; mode: WorkflowBuildMode; need: string;
  status: WorkflowBuildTask["status"]; result?: BuildResult | { plan: string };
  error?: string; createdAt: number; updatedAt: number;
};
type BuildArgs = {
  need: string; mode: WorkflowBuildMode; sessionId: string;
  chat: { baseUrl: string; apiKey: string; modelName: string };
  embed: { baseUrl: string; apiKey: string; modelName: string; mode?: "remote" | "local"; modelDir?: string; rerankerDir?: string };
  comfyUrl: string; workflowDir: string; currentGraph: Record<string, unknown>;
  history: BuildTurn[]; direct: boolean; incremental: boolean;
};

const listeners = new Set<(items: WorkflowBuildActivity[]) => void>();
const cache = new Map<string, WorkflowBuildActivity>();
const cancelledPending = new Set<string>();
let timer: ReturnType<typeof setInterval> | null = null;

function fromTask(task: WorkflowBuildTask): WorkflowBuildActivity {
  return { id: task.id, sessionId: task.session_id, mode: task.mode, need: task.need,
    status: task.status, result: task.result, error: task.error,
    createdAt: task.created_at, updatedAt: task.updated_at };
}
function publish() { listeners.forEach((listener) => listener([...cache.values()].sort((a, b) => b.createdAt - a.createdAt))); }
async function refresh() {
  try {
    const response = await listWorkflowBuildTasks();
    const serverIds = new Set(response.tasks.map((task) => task.id));
    for (const id of cache.keys()) {
      if (!id.startsWith("pending-") && !serverIds.has(id)) cache.delete(id);
    }
    for (const task of response.tasks) cache.set(task.id, fromTask(task));
    publish();
  } catch { /* 后端未启动时保持已有快照 */ }
}
function ensurePolling() {
  if (timer) return;
  void refresh();
  timer = setInterval(() => { void refresh(); }, 1000);
}

export function enqueueWorkflowBuild(args: BuildArgs): WorkflowBuildActivity {
  const optimistic: WorkflowBuildActivity = {
    id: `pending-${crypto.randomUUID()}`, sessionId: args.sessionId, mode: args.mode,
    need: args.need, status: "queued", error: "", createdAt: Date.now(), updatedAt: Date.now(),
  };
  cache.set(optimistic.id, optimistic); publish(); ensurePolling();
  void enqueueWorkflowBuildTask({
    sessionId: args.sessionId, mode: args.mode, need: args.need, chat: args.chat,
    embed: args.embed, comfyUrl: args.comfyUrl, workflowDir: args.workflowDir,
    currentGraph: args.currentGraph, history: args.history,
  }).then((task) => {
    cache.delete(optimistic.id);
    if (cancelledPending.delete(optimistic.id)) {
      void cancelWorkflowBuildTask(task.id).then((cancelled) => { cache.set(cancelled.id, fromTask(cancelled)); publish(); });
    } else {
      cache.set(task.id, fromTask(task)); publish();
    }
  }).catch((error) => {
    cache.set(optimistic.id, { ...optimistic, status: "error", error: (error as Error).message }); publish();
  });
  return optimistic;
}
export function cancelWorkflowBuild(id: string) {
  const item = cache.get(id);
  if (!item) return;
  cache.set(id, { ...item, status: "cancelled", error: "已停止" }); publish();
  if (id.startsWith("pending-")) cancelledPending.add(id);
  else void cancelWorkflowBuildTask(id).then((task) => { cache.set(task.id, fromTask(task)); publish(); }).catch(() => {});
}
export function listWorkflowBuildActivities() { ensurePolling(); return [...cache.values()]; }
export function subscribeWorkflowBuildActivities(listener: (items: WorkflowBuildActivity[]) => void) {
  listeners.add(listener); ensurePolling(); listener([...cache.values()]);
  return () => { listeners.delete(listener); };
}
