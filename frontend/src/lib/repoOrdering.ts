import type { Repo } from "../stores/repos";

export function replaceRepoCover(repos: Repo[], id: string, cover: string): Repo[] {
  return repos.map((repo) => (repo.id === id ? { ...repo, cover } : repo));
}

export function recordGeneratedRepoCover(
  repos: Repo[],
  id: string,
  cover: string,
  generatedAt: number,
): Repo[] {
  return repos.map((repo) => (
    repo.id === id ? { ...repo, cover, coverAt: generatedAt } : repo
  ));
}

export function orderReposByLatestGeneration(candidates: Repo[], allRepos: Repo[]): Repo[] {
  const children = new Map<string, Repo[]>();
  for (const repo of allRepos) {
    if (!repo.parentId) continue;
    const list = children.get(repo.parentId) || [];
    list.push(repo);
    children.set(repo.parentId, list);
  }

  const memo = new Map<string, number>();
  const latestGenerationAt = (repo: Repo, visiting = new Set<string>()): number => {
    const cached = memo.get(repo.id);
    if (cached !== undefined) return cached;
    if (visiting.has(repo.id)) return repo.coverAt || 0;

    const nextVisiting = new Set(visiting).add(repo.id);
    let latest = repo.coverAt || 0;
    for (const child of children.get(repo.id) || []) {
      latest = Math.max(latest, latestGenerationAt(child, nextVisiting));
    }
    memo.set(repo.id, latest);
    return latest;
  };

  return [...candidates].sort((a, b) => {
    const generationDelta = latestGenerationAt(b) - latestGenerationAt(a);
    if (generationDelta !== 0) return generationDelta;
    const creationDelta = (b.createdAt || 0) - (a.createdAt || 0);
    if (creationDelta !== 0) return creationDelta;
    return a.id.localeCompare(b.id);
  });
}
