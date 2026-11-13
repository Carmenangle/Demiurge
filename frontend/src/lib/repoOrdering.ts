import type { Repo } from "../stores/repos";

// 封面地址是不是视频：cover 可能是直链（.../v.mp4）、ComfyUI view 地址
// （...filename=v.mp4&...）或 local-view 查询串（...?path=D%3A%5Cv.mp4）。
// 先解码再扫全串，避免只看 path 段漏掉 query 里的真实文件名。
const VIDEO_COVER_RE = /\.(mp4|webm|mov|mkv|m4v|avi)(?:$|[?#&])/i;

export function isVideoCover(url?: string): boolean {
  if (!url) return false;
  let decoded = url;
  try { decoded = decodeURIComponent(url); } catch { /* 非法转义按原串判断 */ }
  return VIDEO_COVER_RE.test(decoded);
}

/** 最新一条「真实生成」媒体（跳过联网/本地导入资产与音频）；类型跟着最新一条走。 */
export function latestGeneratedCover(
  items: readonly {
    source?: string;
    mediaType?: "image" | "video" | "audio";
    image_url?: string;
    created_at?: number;
  }[],
): { url: string; at: number } | null {
  let latest: { url: string; at: number } | null = null;
  for (const item of items) {
    if (item.source || item.mediaType === "audio" || !item.image_url) continue;
    const at = Number(item.created_at || 0);
    if (at > (latest?.at || 0)) latest = { url: item.image_url, at };
  }
  return latest;
}

/** 封面是否落后于最新生成媒体；最新是图就封面图、最新是视频就动态视频。 */
export function coverNeedsRepair(
  cover: string | undefined,
  coverAt: number | undefined,
  latest: { url: string; at: number } | null,
): boolean {
  return !!latest && latest.at > (coverAt || 0) && cover !== latest.url;
}

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
