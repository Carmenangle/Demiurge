// 回环地址识别与「换成局域网地址」纯逻辑（2026-10-30 手机联机）。
//
// 联机时手机上的模型请求由电脑执行，地址里的 127.0.0.1 在电脑上指电脑自己；
// 模型服务跑在手机上时要填手机在当前 Wi‑Fi 的地址（后端 `/api/mobile/local-models`
// 的 lan_ip）。这里只做纯字符串改写，取不到 lan_ip 就不提供替换。

const URL_PARTS = /^(?:([a-z][a-z0-9+.-]*):\/\/)?([^/?#]*)([\s\S]*)$/i;

interface UrlParts {
  scheme: string;
  userinfo: string;
  host: string;
  port: string;
  rest: string;
}

function splitHostPort(hostport: string): [host: string, port: string] {
  const index = hostport.lastIndexOf(":");
  if (index > 0) return [hostport.slice(0, index), hostport.slice(index)];
  return [hostport, ""];
}

function parseUrl(url: string): UrlParts | null {
  const match = (url || "").trim().match(URL_PARTS);
  if (!match) return null;
  const authority = match[2];
  const at = authority.lastIndexOf("@");
  const userinfo = at >= 0 ? authority.slice(0, at + 1) : "";
  const hostport = at >= 0 ? authority.slice(at + 1) : authority;
  const v6 = hostport.match(/^(\[[^\]]+\])(:\d+)?$/);
  const [host, port] = v6 ? [v6[1], v6[2] || ""] : splitHostPort(hostport);
  return { scheme: match[1] || "", userinfo, host, port, rest: match[3] };
}

export function isLoopbackHost(host: string): boolean {
  const value = (host || "").replace(/^\[|\]$/g, "").toLowerCase();
  return value === "localhost"
    || value.endsWith(".localhost")
    || value === "::1"
    || value === "0.0.0.0"
    || /^127\./.test(value);
}

export function isLoopbackUrl(url: string): boolean {
  const parts = parseUrl(url);
  return Boolean(parts && parts.host && isLoopbackHost(parts.host));
}

/** 回环 URL → 换主机为 lanIp（保留协议/用户信息/端口/路径/查询）；非回环或没有 lanIp 原样返回。 */
export function replaceLoopbackHost(url: string, lanIp: string): string {
  const ip = (lanIp || "").trim();
  if (!ip || !isLoopbackUrl(url)) return url;
  const parts = parseUrl(url)!;
  const host = ip.includes(":") ? `[${ip}]` : ip;
  const scheme = parts.scheme ? `${parts.scheme}://` : "";
  return `${scheme}${parts.userinfo}${host}${parts.port}${parts.rest}`;
}
