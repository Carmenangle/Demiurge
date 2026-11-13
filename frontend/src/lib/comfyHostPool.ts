import { probeComfyHosts, type ComfyHostLoad } from "../api/comfyui";
import type { Settings } from "../stores/settings";
import { resolveComfyUrls, type ComfyModality } from "./comfyMachines";

interface CachedLoad {
  at: number;
  load: ComfyHostLoad;
}

const TTL_MS = 10_000;
const cache = new Map<string, CachedLoad>();
const inFlight = new Set<string>();
const rr = new Map<string, number>();

function poolKey(urls: readonly string[]): string {
  return [...urls].map((url) => url.trim().toLowerCase()).sort().join("|");
}

/** 已缓存的机台负载；过期/未探测返回空。 */
export function cachedHostLoads(urls: readonly string[]): ComfyHostLoad[] {
  const now = Date.now();
  const out: ComfyHostLoad[] = [];
  for (const url of urls) {
    const hit = cache.get(url.trim());
    if (hit && now - hit.at < TTL_MS) out.push(hit.load);
  }
  return out;
}

/** 后台批量探测；10s TTL、同池去重、失败不抛（调用方回退第一台）。 */
export function refreshHostPool(urls: readonly string[]): Promise<void> | void {
  const unique = [...new Set(urls.map((url) => url.trim()).filter(Boolean))];
  if (!unique.length) return;
  const key = poolKey(unique);
  if (inFlight.has(key)) return;
  const fresh = unique.every((url) => {
    const hit = cache.get(url);
    return hit && Date.now() - hit.at < TTL_MS;
  });
  if (fresh) return;
  inFlight.add(key);
  return probeComfyHosts(unique).then((result) => {
    const at = Date.now();
    for (const load of result.items || []) {
      if (load && typeof load.url === "string") cache.set(load.url.trim(), { at, load });
    }
  }).catch(() => undefined).finally(() => {
    inFlight.delete(key);
  });
}

/**
 * 从池里选一台：只在 online 中选 `score` 最低；同分按配置序轮转。
 * 全部 offline/尚未探测 ⇒ 第一台（提交失败可见，探测不会阻断生成）。
 */
export function pickComfyHost(urls: readonly string[]): string {
  const unique = [...new Set(urls.map((url) => url.trim()).filter(Boolean))];
  if (!unique.length) return "";
  refreshHostPool(unique);
  const loads = cachedHostLoads(unique);
  const online = loads.filter((load) => load.online);
  if (!online.length) return unique[0];
  const key = poolKey(unique);
  const minScore = Math.min(...online.map((load) => load.score));
  const candidates = online.filter((load) => load.score === minScore);
  const cursor = (rr.get(key) || 0) % candidates.length;
  rr.set(key, cursor + 1);
  return candidates[cursor]?.url || unique[0];
}

/** 某模态的池里选一台（图片/视频/音频任务分发入口）。 */
export function pickComfyHostFor(
  settings: Pick<Settings, "comfyuiUrl" | "comfyExtra" | "comfyRoute">,
  modality: ComfyModality,
): string {
  return pickComfyHost(resolveComfyUrls(settings, modality));
}
