import type { Repo } from "../stores/repos";

export function recentWorks(repos: readonly Repo[], limit = 5): Repo[] {
  return repos
    .filter((repo) => Boolean(repo.parentId) && (repo.lastUsedAt || 0) > 0)
    .sort((left, right) => (right.lastUsedAt || 0) - (left.lastUsedAt || 0))
    .slice(0, Math.max(0, limit));
}

export function repoLastUsedAt(repo: Repo, children: readonly Repo[]): number {
  return Math.max(repo.lastUsedAt || 0, ...children.map((child) => child.lastUsedAt || 0));
}

export function repoActivityLabel(repos: readonly Repo[], threadId: string): string {
  if (threadId === "home") return "首页";
  const repo = repos.find((item) => item.id === threadId);
  if (!repo) return threadId;
  const parent = repo.parentId ? repos.find((item) => item.id === repo.parentId) : undefined;
  return parent ? `${parent.name} · ${repo.name}` : repo.name;
}

export function formatLastUsed(timestamp: number): string {
  if (!timestamp) return "尚未使用";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(timestamp));
}
