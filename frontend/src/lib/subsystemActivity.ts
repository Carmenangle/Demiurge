// 后台活动「外部长操作」源（2026-10-19）：下载 / 节点安装更新切内核 / LoRA 同步 / 对话后置维护。
// 后端 `services/activity_feed.py` 只做只读投影，这里只负责轮询 + 补仓库显示名。
// 与 jobActivity 并列（那个管 /jobs 注册表），两者都进 SupportWidget 面板。
import { listBackgroundActivity, type BackgroundActivityItem } from "../api/activity";
import type { Repo } from "../stores/repos";
import { repoActivityLabel } from "./repoPresentation";

export interface SubsystemActivity extends BackgroundActivityItem {
  /** 关联仓库的显示名（无 repo_id 时不设置）。 */
  repoLabel?: string;
}

const POLL_MS = 1500;
const listeners = new Set<(items: SubsystemActivity[]) => void>();
let snapshot: SubsystemActivity[] = [];
let timer: ReturnType<typeof setInterval> | null = null;

function loadRepos(): Repo[] {
  try { return JSON.parse(localStorage.getItem("laf_repos") || "[]") as Repo[]; }
  catch { return []; }
}

/** 给带 repo_id 的活动补显示名（纯函数，便于单测）。 */
export function attachRepoLabels(
  items: readonly BackgroundActivityItem[],
  label: (threadId: string) => string,
): SubsystemActivity[] {
  return items.map((item) => (
    item.repo_id ? { ...item, repoLabel: label(item.repo_id) } : { ...item }
  ));
}

function publish() { listeners.forEach((listener) => listener(snapshot)); }

async function refresh() {
  try {
    const response = await listBackgroundActivity();
    snapshot = attachRepoLabels(response.items || [], (threadId) => repoActivityLabel(loadRepos(), threadId));
    publish();
  } catch { /* 后端离线/超时：保留上次快照，避免面板闪烁 */ }
}

function ensurePolling() {
  if (timer) return;
  void refresh();
  timer = setInterval(() => { void refresh(); }, POLL_MS);
}

export function subscribeSubsystemActivities(listener: (items: SubsystemActivity[]) => void) {
  listeners.add(listener);
  ensurePolling();
  listener(snapshot);
  return () => { listeners.delete(listener); };
}
