// 动态 import 取不到模块时的统一「识别 + 限频重载」，防止资源真缺失时无限刷新。
// 🔴 同一句 `Failed to fetch dynamically imported module` 背后有两类原因，字符串分不开：
//   ① 换包后仍开着的旧页面按旧 hash 去取已被替换的 chunk ⇒ 404；
//   ② origin 那一瞬间不可达（手机端息屏 30 s 壳层会暂停 uvicorn；升级/重启窗口同理）⇒ 请求没发出去。
// 2026-10-27 真机复测：0.30.8→0.30.13 一次**真实换包**没有出卡（根 `index.html` 已 `no-store`），
// 所以 ① 这条链在装了 2a 之后**不再被观测到**；用户那张截图归因②尚未在真机上复现成功
// （回前台窗口的时序探针没跑完）。两类都靠「重新加载」处置即可恢复，故不分支，
// 但卡片文案**不得**再断言「版本不一致」——那是把未证明的归因说成事实。
// 护栏见 `ARCHITECTURE.md` 条 73 与 `F/11` §3.11.15。

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

// 换一个查询串才是真绕过缓存：URL 缓存键含 query，旧 index.html 拿不回来。
// 单独抽出成纯函数是为了能单测（jsdom 里 location.replace 不生效，断不了行为）。
export function cacheBustedUrl(href: string, now: number): string {
  const url = new URL(href);
  // hash 要保住：手机端刷新后靠它回到当前视图（见 App.tsx 的路由恢复）
  url.searchParams.set("_r", String(now));
  return url.toString();
}

// 手动「重新加载」按钮也走这条路（而不是 `location.reload()`）：`_r` 变了才是另一个缓存键，
// 已中毒的缓存里那份旧 `index.html` 拿不回来；`reload()` 的缓存语义在各家 WebView 上不一致，
// 不能保证绕开（2026-10-27 真机只证明了「带 `_r` 的 replace 能恢复」，未证明 `reload()` 会失败）。
// 不过限频守卫——用户主动点击不该被 30s 冷却挡住。
// replace 而非 assign：坏页面不该在返回链里留一个坑位。
export function forceCacheBustedReload(): void {
  window.location.replace(cacheBustedUrl(window.location.href, Date.now()));
}

export function reloadForStaleChunk(): boolean {
  if (!shouldAutoReload()) return false;
  forceCacheBustedReload();
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
