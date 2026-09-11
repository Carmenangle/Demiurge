// ComfyUI 工作流后台活动：扫描各仓库的 laf_pending_gen_* localStorage，
// 把进行中的出图任务暴露给后台活动面板(SupportWidget)。
// pending 项由 useChatSession 在完成/失败时删除，还在的即为进行中。
import type { Repo } from "../stores/repos";
import { repoActivityLabel } from "./repoPresentation";

export interface ComfyBackgroundActivity {
  promptId: string;
  threadId: string;
  label: string;  // 仓库显示名
}

const STALE_MS = 30 * 60 * 1000;  // 只影响面板展示；任务生命周期归 WorkflowGenerationRuntime 所有

const listeners = new Set<(items: ComfyBackgroundActivity[]) => void>();
let snapshot: ComfyBackgroundActivity[] = [];
let timer: ReturnType<typeof setInterval> | null = null;

function loadRepos(): Repo[] {
  try { return JSON.parse(localStorage.getItem("laf_repos") || "[]") as Repo[]; }
  catch { return []; }
}

function repoLabel(threadId: string): string {
  return repoActivityLabel(loadRepos(), threadId);
}

function publish() { listeners.forEach((fn) => fn(snapshot)); }

// 扫描所有仓库进行中的出图任务（纯函数，便于单测；storage 默认为 globalThis.localStorage）
// 展示层必须只读：不得删除 pending，否则慢工作流完成时会跳过 finalize。
export function scanComfyActivities(
  now: number,
  label: (threadId: string) => string,
  storage: Storage = localStorage,
): ComfyBackgroundActivity[] {
  const items: ComfyBackgroundActivity[] = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (!key?.startsWith("laf_pending_gen_")) continue;
    const threadId = key.slice("laf_pending_gen_".length);
    if (!threadId || threadId === "home") continue;
    let pending: { prompt_id: string; createdAt: number }[] = [];
    try { pending = JSON.parse(storage.getItem(key) || "[]"); } catch { continue; }
    const fresh = pending.filter((item) => now - item.createdAt <= STALE_MS);
    for (const item of fresh) {
      items.push({ promptId: item.prompt_id, threadId, label: label(threadId) });
    }
  }
  return items;
}

export function hasPendingComfyGeneration(
  threadIds: readonly string[], storage: Pick<Storage, "getItem"> = localStorage,
): boolean {
  return threadIds.some((threadId) => {
    try {
      const items = JSON.parse(storage.getItem(`laf_pending_gen_${threadId}`) || "[]");
      return Array.isArray(items) && items.length > 0;
    } catch {
      return false;
    }
  });
}

function refresh() {
  snapshot = scanComfyActivities(Date.now(), repoLabel);
  publish();
}

function ensurePolling() {
  if (timer) return;
  refresh();
  timer = setInterval(refresh, 1500);
}

export function subscribeComfyBackgroundActivities(
  listener: (items: ComfyBackgroundActivity[]) => void,
) {
  listeners.add(listener);
  ensurePolling();
  listener(snapshot);
  return () => { listeners.delete(listener); };
}
