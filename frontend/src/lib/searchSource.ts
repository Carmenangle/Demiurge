// 联网搜索源（2026-09-10）：文字搜索有三个源，前端只负责「选」，解析全在后端。
// - bing-cn：cn.bing.com，国内可直连 → 不开代理也能搜到东西
// - moegirl：萌娘百科站内搜索（2026-09-11），专治 bing 对长尾新角色名的间歇性放松
// - ddg：DuckDuckGo，需代理，结果更国际化
// - auto：后端按 bing-cn → moegirl → ddg 顺序回落（默认），也就是「能用哪个用哪个」
//
// 为什么把选项表放在 lib：设置页只渲染它，别处不该再抄一份源名字符串；
// 源名写错后端会当成未注册源、静默返回空列表（最难查的一类故障）。

export type SearchProvider = "auto" | "bing-cn" | "moegirl" | "ddg";

export const SEARCH_PROVIDER_OPTIONS: { value: SearchProvider; label: string; hint: string }[] = [
  { value: "auto", label: "自动（推荐）", hint: "依次尝试 Bing 中国 → 萌娘百科 → DuckDuckGo，国内直连即可用" },
  { value: "bing-cn", label: "Bing 中国", hint: "国内可直连，不需要代理" },
  { value: "moegirl", label: "萌娘百科", hint: "站内搜索，ACG 角色/作品设定精准命中，国内可直连" },
  { value: "ddg", label: "DuckDuckGo", hint: "结果更国际化，但国内需要代理才连得上" },
];

const SEARCH_PROVIDER_VALUES: readonly string[] = SEARCH_PROVIDER_OPTIONS.map((o) => o.value);

/** 把存档里的任意值收敛成合法搜索源；非法/缺失一律回落 auto（绝不抛）。 */
export function normalizeSearchProvider(value: unknown): SearchProvider {
  const text = typeof value === "string" ? value.trim() : "";
  return SEARCH_PROVIDER_VALUES.includes(text) ? (text as SearchProvider) : "auto";
}

/** 上线值：auto 送空串（后端约定「空=自动回落链」），显式源名原样送。 */
export function searchProviderWireValue(provider: SearchProvider | undefined): string {
  const value = normalizeSearchProvider(provider);
  return value === "auto" ? "" : value;
}
