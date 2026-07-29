// 仓库对话后台活动：把「正在跑的仓库对话」与「后端排队消息」合并成一份可订阅列表，
// 供后台活动面板(SupportWidget)显示。正在跑的运行态由后端 agent_runner 托管，这里只读它的
// running-threads；排队消息读后端持久化队列。两者都跨页面/刷新存活。
import {
  fetchRunningChatThreads, listChatQueueTasks, cancelChatQueueTask,
  type ChatQueueTask,
} from "../api/ai";
import type { Repo } from "../stores/repos";

export type ChatActivityKind = "running" | "queued";
export interface ChatBackgroundActivity {
  kind: ChatActivityKind;
  threadId: string;
  label: string;       // 仓库显示名（找不到则用 threadId）
  need: string;        // 队列项的消息预览；运行态为空
  taskId?: string;     // 队列项 id（运行态无）
}

const listeners = new Set<(items: ChatBackgroundActivity[]) => void>();
let snapshot: ChatBackgroundActivity[] = [];
let timer: ReturnType<typeof setInterval> | null = null;

function loadRepos(): Repo[] {
  try { return JSON.parse(localStorage.getItem("laf_repos") || "[]") as Repo[]; }
  catch { return []; }
}

function repoLabel(threadId: string): string {
  if (threadId === "home") return "首页";
  const repo = loadRepos().find((item) => item.id === threadId);
  return repo?.name || threadId;
}

function publish() { listeners.forEach((listener) => listener(snapshot)); }

// 合并「正在跑的 thread」与「排队消息」为活动列表（纯函数，便于单测）：
// 首页(home)是临时草稿区不纳入；running 运行态由 running-threads 覆盖，队列里的 running 项去重不重复列。
export function mergeChatActivities(
  runningThreads: string[],
  queueTasks: ChatQueueTask[],
  label: (threadId: string) => string,
): ChatBackgroundActivity[] {
  const items: ChatBackgroundActivity[] = [];
  for (const threadId of runningThreads) {
    if (threadId === "home") continue;
    items.push({ kind: "running", threadId, label: label(threadId), need: "" });
  }
  for (const task of queueTasks) {
    if (task.status !== "queued") continue;  // running 已由 running-threads 覆盖
    if (task.thread_id === "home") continue;
    items.push({
      kind: "queued", threadId: task.thread_id, label: label(task.thread_id),
      need: task.need, taskId: task.id,
    });
  }
  return items;
}

async function refresh() {
  try {
    const [running, queue] = await Promise.all([
      fetchRunningChatThreads(),
      listChatQueueTasks(),
    ]);
    snapshot = mergeChatActivities(running.threads, queue.tasks, repoLabel);
    publish();
  } catch { /* 后端未启动时保持已有快照 */ }
}

function ensurePolling() {
  if (timer) return;
  void refresh();
  timer = setInterval(() => { void refresh(); }, 1500);
}

export function subscribeChatBackgroundActivities(
  listener: (items: ChatBackgroundActivity[]) => void,
) {
  listeners.add(listener);
  ensurePolling();
  listener(snapshot);
  return () => { listeners.delete(listener); };
}

// 队列项被取消/编辑后主动刷新，不必等下一次轮询。
export function refreshChatBackgroundActivities() { void refresh(); }

// 列出某仓库尚在排队的消息（队列条持久化显示用）。
export async function listQueuedForThread(threadId: string): Promise<ChatQueueTask[]> {
  try {
    const { tasks } = await listChatQueueTasks(threadId);
    return tasks.filter((task) => task.status === "queued");
  } catch { return []; }
}

export async function cancelQueuedTask(taskId: string): Promise<void> {
  try { await cancelChatQueueTask(taskId); } catch { /* 后端未起忽略 */ }
  void refresh();
}
