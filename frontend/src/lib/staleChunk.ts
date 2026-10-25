// 前端重新打包后，仍开着的旧页面会按旧 hash 去取已被替换的 chunk，拿到 404：
// "Failed to fetch dynamically imported module: .../NodeManagerView-<oldhash>.js"。
// 这不是代码缺陷，重新加载拿新 index.html 即可恢复。这里只负责「识别 + 限频重载」，
// 防止资源真的缺失时无限刷新。

const GUARD_KEY = "demiurge:stale-chunk-reload-at";
export const STALE_CHUNK_RELOAD_GUARD_MS = 30_000;

const STALE_CHUNK_PATTERNS: RegExp[] = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /loading chunk \S+ failed/i,
  /chunkloaderror/i,
];

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function isStaleChunkError(error: unknown): boolean {
  const message =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";
  if (!message) return false;
  return STALE_CHUNK_PATTERNS.some((re) => re.test(message));
}

export function shouldAutoReload(
  now: number = Date.now(),
  storage: KeyValueStorage | null = browserSessionStorage(),
): boolean {
  if (!storage) return false;
  let last = 0;
  try {
    last = Number(storage.getItem(GUARD_KEY) || 0);
  } catch {
    return false;
  }
  if (Number.isFinite(last) && now - last < STALE_CHUNK_RELOAD_GUARD_MS) return false;
  try {
    storage.setItem(GUARD_KEY, String(now));
  } catch {
    return false;
  }
  return true;
}

export function reloadForStaleChunk(): boolean {
  if (!shouldAutoReload()) return false;
  window.location.reload();
  return true;
}

function browserSessionStorage(): KeyValueStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}
