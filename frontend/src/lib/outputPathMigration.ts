function normalized(path: string): string {
  return path.replace(/\//g, "\\").replace(/\\+$/, "").toLocaleLowerCase();
}

// wire 形态是根相对（`/api/comfyui/local-view?path=…`，属主后端 view_urls）；`new URL` 无 base
// 解析它会抛并静默走 catch 分支 ⇒ 迁移后封面/产物地址悄悄不改写。占位 origin 只借解析与
// 回填能力，出口按原形态剥掉，不把相对地址升格成绝对。
const PLACEHOLDER_ORIGIN = "http://demiurge.local";

function isRootRelative(url: string): boolean {
  return url.startsWith("/") && !url.startsWith("//");
}

export function relocateLocalViewUrl(url: string | undefined, oldDir: string, newDir: string) {
  if (!url || !url.includes("local-view")) return url;
  try {
    const relative = isRootRelative(url);
    const parsed = new URL(relative ? `${PLACEHOLDER_ORIGIN}${url}` : url);
    const path = parsed.searchParams.get("path");
    if (!path) return url;
    const oldRoot = normalized(oldDir);
    const current = normalized(path);
    if (current !== oldRoot && !current.startsWith(`${oldRoot}\\`)) return url;
    const relativePath = path.slice(oldDir.replace(/[\\/]+$/, "").length).replace(/^[\\/]+/, "");
    const root = newDir.replace(/[\\/]+$/, "");
    parsed.searchParams.set("path", relativePath ? `${root}\\${relativePath}` : root);
    const moved = parsed.toString();
    return relative ? moved.slice(PLACEHOLDER_ORIGIN.length) : moved;
  } catch {
    return url;
  }
}
