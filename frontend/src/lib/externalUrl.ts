// 外链 href 白名单：只放行 http(s)。
//
// 素材市场的 source_url 来自后端代理的第三方市场 API（pixiv/danbooru 等），直通
// <a href> 时 javascript:/data:/vbscript: 等 scheme 一旦出现在上游数据里就会被
// 浏览器执行（2026-10-24 审计改进项）。不合法时返回 "#"：链接仍在（文本可复制），
// 点击只回到页顶，不导航。
export function safeExternalHref(url: string | null | undefined): string {
  const value = (url ?? "").trim();
  return /^https?:\/\//i.test(value) ? value : "#";
}
