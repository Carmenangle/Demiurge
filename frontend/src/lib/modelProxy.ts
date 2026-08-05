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
