// 仓库对话后台活动：把「正在跑的仓库对话」与「后端排队消息」合并成一份可订阅列表，
// 供后台活动面板(SupportWidget)显示。正在跑的运行态由后端 agent_runner 托管，这里只读它的
// running-threads；排队消息读后端持久化队列。两者都跨页面/刷新存活。
import {
  fetchRunningChatThreads, listChatQueueTasks, cancelChatQueueTask,
  type ChatQueueTask,
} from "../api/ai";
import type { Repo } from "../stores/repos";
import { addUnread } from "./chatRoomUnread";
import { repoActivityLabel } from "./repoPresentation";

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
// 当前正在看的聊天线程；后台运行结束时若不属于它，就记一条未读红点。
let activeThreadId = "";
// 上一轮轮询看到的 running 线程，用于识别「刚刚跑完」。
let previousRunning = new Set<string>();
// 需要连续两轮确认「确实不在跑」才补红点：防止一次运行刚结束、下一次运行还没被
// running-threads 收录时，把「切换页面」误判成「回复完成」。
const pendingSince = new Map<string, number>();
// 本轮前端发起后，必须先在 running-threads 里真的看到它跑过，才允许判「完成」。
// 否则「刚发出、后端还没准入」会被误判成「已经跑完」。
const seenRunningSinceStart = new Set<string>();

/** 聊天室把当前可见线程登记到这里；切走/卸载时传空串。 */
export function setActiveChatThread(threadId: string) {
  activeThreadId = (threadId || "").trim();
}

/** 前端刚发起一次生成（发送/恢复）——给 running-threads 一个收录窗口，别立刻判完成。 */
export function markChatRunStarted(threadId: string) {
  const id = (threadId || "").trim();
  if (!id) return;
  // 新一次运行开始：清掉旧的「已观测」与待确认，等 running-threads 真的收录后再认完成。
  seenRunningSinceStart.delete(id);
  pendingSince.delete(id);
}

/** 只要 ChatView 还在，就保持后台活动轮询（跨剧情/私聊模式，切走也能收到完成红点）。 */
export function ensureChatActivityWatch() {
  ensurePolling();
}

/** 纯函数：上一轮在跑、这一轮已结束、且不是当前可见线程 → 需要补未读。 */
export function finishedBackgroundThreads(
  previous: Iterable<string>,
  current: Iterable<string>,
  active: string,
): string[] {
  const currentSet = new Set(current);
  const activeId = (active || "").trim();
  return [...new Set(previous)].filter(
    (threadId) => threadId && threadId !== "home"
      && threadId !== activeId && !currentSet.has(threadId),
  );
}

function loadRepos(): Repo[] {
  try { return JSON.parse(localStorage.getItem("laf_repos") || "[]") as Repo[]; }
  catch { return []; }
}

function repoLabel(threadId: string): string {
  return repoActivityLabel(loadRepos(), threadId);
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
    const finished = finishedBackgroundThreads(
      previousRunning, running.threads,
      typeof document !== "undefined" && document.hidden ? "" : activeThreadId,
    );
    const currentRunning = new Set(running.threads);
    // 真正出现在 running-threads 里：记「观测到在跑」，并撤销待确认。
    for (const threadId of currentRunning) {
      seenRunningSinceStart.add(threadId);
      pendingSince.delete(threadId);
    }
    for (const threadId of finished) {
      // 只给聊天室线程补红点；剧情 roleplay 的 thread=repo_id 不在私聊/群聊未读口径里。
      if (!threadId.includes("__chat") && !threadId.includes("__group")) continue;
      if (!pendingSince.has(threadId)) pendingSince.set(threadId, Date.now());
    }
    // 至少隔一轮仍不在 running，并且本轮确实观测到它跑过，才算真的跑完。
    for (const [threadId, since] of [...pendingSince]) {
      if (currentRunning.has(threadId)) {
        pendingSince.delete(threadId);
        continue;
      }
      // 用户已经切回该线程：不需要红点（快照会自己显示结果）。
      if (threadId === activeThreadId
        && !(typeof document !== "undefined" && document.hidden)) {
        pendingSince.delete(threadId);
        continue;
      }
      if (!seenRunningSinceStart.has(threadId)) continue;
      if (Date.now() - since < 1500) continue;
      addUnread(threadId, "", 1);
      pendingSince.delete(threadId);
      seenRunningSinceStart.delete(threadId);
    }
    // 已经结束且不再待确认的线程清掉「已观测」：下一轮新运行必须重新被收录。
    // 待确认的线程保留，等下一轮确认「仍不在跑」后再补红点。
    for (const threadId of previousRunning) {
      if (!currentRunning.has(threadId) && !pendingSince.has(threadId)) {
        seenRunningSinceStart.delete(threadId);
      }
    }
    previousRunning = currentRunning;
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
