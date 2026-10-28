import type { ComfyMachine, Settings } from "../stores/settings";

/** 地址簿条目 0 的固定 id：它就是 `settings.comfyuiUrl` 本身，不进 `comfyExtra`。 */
export const DEFAULT_MACHINE_ID = "default";

export type ComfyModality = "image" | "video" | "audio";

export interface ComfyMachineEntry {
  id: string;
  name: string;
  url: string;
}

const DEFAULT_MACHINE_NAME = "默认地址";

/** URL 比对用的归一：只去尾斜杠与大小写差异，**不改写真值**。 */
export function sameComfyUrl(a: string, b: string): boolean {
  return a.trim().replace(/\/+$/, "").toLowerCase() === b.trim().replace(/\/+$/, "").toLowerCase();
}

/**
 * 地址簿全集：条目 0 ＝ `settings.comfyuiUrl`，其后是 `settings.comfyExtra` 里的合法条目。
 *
 * 脏条目（缺 id / 空 url / 非字符串）一律过滤——`comfyExtra` 落在用户可编辑的 user_state 里，
 * 这里是唯一的读取入口，过滤一次胜过每个消费点各防一遍。
 */
export function listMachines(settings: Pick<Settings, "comfyuiUrl" | "comfyExtra">): ComfyMachineEntry[] {
  const head: ComfyMachineEntry = {
    id: DEFAULT_MACHINE_ID,
    name: DEFAULT_MACHINE_NAME,
    url: (settings.comfyuiUrl || "").trim(),
  };
  const extras = Array.isArray(settings.comfyExtra) ? settings.comfyExtra : [];
  const seen = new Set<string>([DEFAULT_MACHINE_ID]);
  const out: ComfyMachineEntry[] = [head];
  for (const raw of extras as ComfyMachine[]) {
    if (!raw || typeof raw !== "object") continue;
    const id = typeof raw.id === "string" ? raw.id.trim() : "";
    const url = typeof raw.url === "string" ? raw.url.trim() : "";
    if (!id || !url || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, name: (typeof raw.name === "string" ? raw.name.trim() : "") || url, url });
  }
  return out;
}

/** 地址簿里的额外机台地址（去空、按 URL 归一后去重）。 */
function extraMachineUrls(settings: Pick<Settings, "comfyExtra">): string[] {
  const urls = (Array.isArray(settings.comfyExtra) ? settings.comfyExtra : [])
    .map((m) => (m && typeof m.url === "string" ? m.url.trim() : ""))
    .filter(Boolean);
  return urls.filter((u, i) => urls.findIndex((x) => sameComfyUrl(x, u)) === i);
}

/**
 * 保存前后「额外机台」的增减集合（后端白名单对账用）。
 *
 * 默认地址由保存流程单独登记，不算在额外机台里；**仍被引用的地址也不注销**——
 * 条目 0 与某台额外机台填了同一地址时，删掉那条重复条目不能把默认机的白名单一起拆掉。
 */
export function diffComfyHosts(
  prev: Pick<Settings, "comfyExtra" | "comfyuiUrl">,
  next: Pick<Settings, "comfyExtra" | "comfyuiUrl">,
): { added: string[]; removed: string[] } {
  const head = (next.comfyuiUrl || "").trim();
  const exclusive = (u: string) => !head || !sameComfyUrl(u, head);
  const prevUrls = extraMachineUrls(prev).filter(exclusive);
  const nextUrls = extraMachineUrls(next).filter(exclusive);
  return {
    added: nextUrls.filter((u) => !prevUrls.some((p) => sameComfyUrl(p, u))),
    removed: prevUrls.filter((u) => !nextUrls.some((p) => sameComfyUrl(p, u))),
  };
}

/**
 * 某个模态该用哪台机：`comfyRoute[modality]` 指向的条目，**缺省或指向未知 id 都回退条目 0**。
 *
 * 提交/落盘必须用这里返回的**具体 URL**——名字只是标签，历史与重新生成快照记的都是 URL。
 */
export function resolveComfyUrl(
  settings: Pick<Settings, "comfyuiUrl" | "comfyExtra" | "comfyRoute">,
  modality?: ComfyModality | null,
): string {
  const machines = listMachines(settings);
  const head = machines[0]?.url || "";
  const wanted = modality ? settings.comfyRoute?.[modality] : "";
  if (!wanted) return head;
  return machines.find((m) => m.id === wanted)?.url || head;
}

/** 由 URL 反查显示名（历史/快照只存 URL）；反查不到原样返回 URL，绝不丢信息。 */
export function machineLabelOf(
  settings: Pick<Settings, "comfyuiUrl" | "comfyExtra">,
  url: string,
): string {
  const target = (url || "").trim();
  if (!target) return "";
  const hit = listMachines(settings).find((m) => sameComfyUrl(m.url, target));
  return hit && hit.id !== DEFAULT_MACHINE_ID ? hit.name : target;
}

/** 回环地址才能嵌原生画布（非回环：手机不渲染、PC 被 CSP frame-src 挡）。 */
export function isLoopbackComfyUrl(url: string): boolean {
  const raw = (url || "").trim().toLowerCase();
  if (!raw) return true;
  try {
    const host = new URL(raw.includes("://") ? raw : `http://${raw}`).hostname.replace(/^\[|\]$/g, "");
    return host === "localhost" || host === "::1" || /^127\./.test(host);
  } catch {
    return false;
  }
}
