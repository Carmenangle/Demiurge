// ComfyUI 工作流后台活动：扫描各仓库的 laf_pending_gen_* localStorage，
// 把进行中的出图任务暴露给后台活动面板(SupportWidget)。
// pending 项由 useChatSession 在完成/失败时删除，还在的即为进行中。
import type { Repo } from "../stores/repos";

export interface ComfyBackgroundActivity {
  promptId: string;
  threadId: string;
  label: string;  // 仓库显示名
}

const STALE_MS = 10 * 60 * 1000;  // 10 分钟以上视为过期残留（绝大多数生成在此内完成）

const listeners = new Set<(items: ComfyBackgroundActivity[]) => void>();
let snapshot: ComfyBackgroundActivity[] = [];
let timer: ReturnType<typeof setInterval> | null = null;

function loadRepos(): Repo[] {
  try { return JSON.parse(localStorage.getItem("laf_repos") || "[]") as Repo[]; }
  catch { return []; }
}

function repoLabel(threadId: string): string {
  const repo = loadRepos().find((r) => r.id === threadId);
  return repo?.name || threadId;
}

function publish() { listeners.forEach((fn) => fn(snapshot)); }

// 扫描所有仓库进行中的出图任务（纯函数，便于单测；storage 默认为 globalThis.localStorage）
// 同时清理过期条目，避免重启后残留项持续显示。
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
    // 有过期条目：写回清理后的列表（或直接删 key）
    if (fresh.length !== pending.length) {
      try {
        if (fresh.length === 0) storage.removeItem(key);
        else storage.setItem(key, JSON.stringify(fresh));
      } catch { /* ignore */ }
    }
    for (const item of fresh) {
      items.push({ promptId: item.prompt_id, threadId, label: label(threadId) });
    }
  }
  return items;
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
