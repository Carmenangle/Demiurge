// 聊天室拍摄任务登记（2026-11-10 用户实锤：切屏后「正在拍摄中」丢失）。
//
// 媒体生成是分钟级任务，而 `busy/status` 是组件内 state：切角色、切群聊/剧情、
// 组件卸载后状态就没了。这里用模块级订阅表按「线程 + 角色」记录在跑的拍摄，
// 界面回到该线程时仍能显示「xxx 正在拍摄中…」；任务结束（成功/失败）即移除。
export interface ChatMediaTask {
  id: string;
  threadId: string;
  speaker: string;
  kind: "image" | "video";
  /** shooting=正在出图；waiting=第一次失败，等 GPU 空闲后重拍。 */
  phase?: "shooting" | "waiting";
  startedAt: number;
  /** 取消回调：中止等待/SSE；ComfyUI 任务本身不保证能中断。 */
  cancel?: () => void;
}

const tasks = new Map<string, ChatMediaTask>();
const listeners = new Set<() => void>();

// 拍摄任务超过 35 分钟仍未收尾，按僵尸任务处理：
// 最坏路径＝等 GPU 空闲 10 分钟 + 图片轮询 20 分钟 + 提交/落盘余量。
// 用户可以在「正在拍摄中」旁点「取消拍摄」立即释放，不必等僵尸清理。
export const CHAT_MEDIA_TASK_STALE_MS = 35 * 60 * 1000;

const keyOf = (threadId: string, speaker: string) => `${threadId}\u0000${speaker}`;

function notify() {
  for (const listener of listeners) listener();
}

export function startChatMediaTask(
  task: Omit<ChatMediaTask, "id" | "startedAt">,
): string {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  tasks.set(keyOf(task.threadId, task.speaker), { ...task, id, startedAt: Date.now() });
  notify();
  return id;
}

/** 第一次拍失败后切到 waiting：界面显示「等显卡空闲」，但不当成结束。 */
export function setChatMediaTaskPhase(
  threadId: string, speaker: string, phase: "shooting" | "waiting", id = "",
) {
  const key = keyOf(threadId, speaker);
  const task = tasks.get(key);
  if (!task || (id && task.id !== id) || task.phase === phase) return;
  tasks.set(key, { ...task, phase });
  notify();
}

/** 只结束自己那一份任务：旧任务的 finally 不能删掉同角色后来新起的拍摄。 */
export function finishChatMediaTask(
  threadId: string, speaker: string, id = "",
): boolean {
  const key = keyOf(threadId, speaker);
  const task = tasks.get(key);
  if (!task || (id && task.id !== id)) return false;
  tasks.delete(key);
  notify();
  return true;
}

/** 取消当前拍摄：先摘任务，再通知取消回调（避免回调收尾时再触发一次通知）。 */
export function cancelChatMediaTask(threadId: string, speaker: string): boolean {
  const key = keyOf(threadId, speaker);
  const task = tasks.get(key);
  if (!task) return false;
  tasks.delete(key);
  notify();
  try {
    task.cancel?.();
  } catch {
    // 取消失败不影响任务已移除。
  }
  return true;
}

export function subscribeChatMediaTasks(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** 已提交到 ComfyUI、但前端还没收尾的聊天图；刷新后据此续接轮询。 */
export interface ChatMediaResumeRecord {
  threadId: string;
  speaker: string;
  promptId: string;
  prompt: string;
  avatar?: boolean;
  appearance?: string;
  sourceMessageId?: string;
  createdAt: number;
}

const resumeKey = (threadId: string, speaker: string) =>
  `laf_chat_media_resume_${threadId}\u0000${speaker}`;

function resumeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export function saveChatMediaResume(record: ChatMediaResumeRecord): void {
  const store = resumeStorage();
  if (!store) return;
  try {
    store.setItem(resumeKey(record.threadId, record.speaker), JSON.stringify(record));
  } catch {
    // 存储满/隐私模式：不阻断拍摄，只是刷新后不能续接。
  }
}

export function loadChatMediaResume(
  threadId: string, speaker: string,
): ChatMediaResumeRecord | null {
  const store = resumeStorage();
  if (!store) return null;
  try {
    const raw = store.getItem(resumeKey(threadId, speaker));
    if (!raw) return null;
    const data = JSON.parse(raw) as ChatMediaResumeRecord;
    if (!data?.promptId || !data.prompt || !data.speaker) return null;
    if (Date.now() - Number(data.createdAt || 0) > CHAT_MEDIA_TASK_STALE_MS) {
      store.removeItem(resumeKey(threadId, speaker));
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

export function clearChatMediaResume(threadId: string, speaker: string): void {
  const store = resumeStorage();
  if (!store) return;
  try {
    store.removeItem(resumeKey(threadId, speaker));
  } catch {
    // 清理失败不影响聊天；记录会在过期后被 load 丢掉。
  }
}

export function chatMediaTasksForThread(threadId: string): ChatMediaTask[] {
  const now = Date.now();
  const alive: ChatMediaTask[] = [];
  for (const [key, task] of tasks) {
    if (task.threadId !== threadId) continue;
    if (now - task.startedAt > CHAT_MEDIA_TASK_STALE_MS) {
      tasks.delete(key);
      continue;
    }
    alive.push(task);
  }
  return alive;
}
