export type ProxyMode = "on" | "off" | "inherit";

export function normalizeProxyMode(value: unknown): ProxyMode {
  return value === "off" || value === "inherit" || value === "on" ? value : "on";
}

export function resolveModelProxy(
  mode: unknown,
  globalUrl: string,
  globalEnabled: boolean,
): string {
  const selected = normalizeProxyMode(mode);
  if (selected === "off") return "";
  if (selected === "inherit" && !globalEnabled) return "";
  return (globalUrl || "").trim();
}

export function isLoopbackEndpoint(baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl.includes("://") ? baseUrl : `http://${baseUrl}`);
    return url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  } catch {
    return false;
  }
}

// 全局代理的候选地址（2026-09-10「继承全局」升级）：系统代理优先，其次设置里手填的兜底地址。
// 只挑地址、不含「是否启用」判断——模型级三级代理要把 enabled 交给 resolveModelProxy 判：
// 选「使用代理」(on) 是用户显式强制，探测判定不可用也照走；选「继承全局」(inherit) 才受它约束。
export function globalProxyAddress(s: { proxyUrl?: string; systemProxyUrl?: string }): string {
  return (s.systemProxyUrl || s.proxyUrl || "").trim();
}

// 生效的全局代理地址：供**没有**模型级 proxyMode 的链路（联网搜索、模型下载、画布取图）直接用；
// 探测判定不可用（proxyEnabled=false）时返回空串=直连。唯一取用口，别再直接读 settings.proxyUrl。
export function effectiveGlobalProxyUrl(s: {
  proxyEnabled: boolean;
  proxyUrl?: string;
  systemProxyUrl?: string;
}): string {
  return s.proxyEnabled ? globalProxyAddress(s) : "";
}

export function resolveEndpointProxy(
  baseUrl: string,
  mode: unknown,
  globalUrl: string,
  globalEnabled: boolean,
): string {
  return isLoopbackEndpoint(baseUrl)
    ? ""
    : resolveModelProxy(mode, globalUrl, globalEnabled);
}
