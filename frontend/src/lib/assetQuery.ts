/**
 * 资产搜索框语法解析（P1-3）：`tag:xxx` 走结构化（asset_tags 精确 AND），
 * 其余词走自由文本（FTS5 + LIKE 兜底）。分隔符与既有输入习惯一致（逗号/空格）。
 *
 * 例：
 *   "tag:1girl tag:solo 森林"    → { tags: ["1girl","solo"], text: "森林" }
 *   'tag:"BanG Dream"'          → { tags: ["BanG Dream"], text: "" }（多词标签用引号）
 *   "1girl，solo"               → { tags: [], text: "1girl solo" }（旧口径：自由文本）
 * 去重保序（同一标签重复写只算一次）。
 */
export type AssetQuery = { tags: string[]; text: string };

export function parseAssetQuery(raw: string): AssetQuery {
  const tags: string[] = [];
  const seen = new Set<string>();
  const push = (value: string) => {
    const tag = value.trim();
    if (tag && !seen.has(tag.toLowerCase())) {
      seen.add(tag.toLowerCase());
      tags.push(tag);
    }
  };
  // 先摘出 tag:"多词标签"（danbooru/pixiv 的多词标签带空格，裸 token 会被空格拆开）
  let rest = (raw || "").replace(/tag:"([^"]+)"/gi, (_m, t: string) => { push(t); return " "; });
  const words: string[] = [];
  for (const token of rest.split(/[,，;；\s]+/)) {
    const value = token.trim();
    if (!value) continue;
    const m = /^tag:(.+)$/i.exec(value);
    if (m) push(m[1]);
    else words.push(value);
  }
  rest = words.join(" ");
  return { tags, text: rest };
}

/** 去掉某个已选标签（chips 的 ×）：只删对应 `tag:xxx` 词项，其余原样保留。 */
export function removeTagFromQuery(raw: string, tag: string): string {
  const parts = (raw || "").split(/([,，;；\s]+)/);   // 保留分隔符原位，少动用户输入
  const out: string[] = [];
  for (const part of parts) {
    const m = /^tag:(?:"([^"]+)"|(.+))$/i.exec(part.trim());
    const value = (m?.[1] ?? m?.[2] ?? "").trim();
    if (value && value.toLowerCase() === tag.trim().toLowerCase()) continue;
    out.push(part);
  }
  return out.join("")
    .replace(/^[,，;；\s]+/, "")
    .replace(/[,，;；\s]+$/, "")
    .trim();
}
