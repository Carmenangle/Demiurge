function normalized(path: string): string {
  return path.replace(/\//g, "\\").replace(/\\+$/, "").toLocaleLowerCase();
}

export function relocateLocalViewUrl(url: string | undefined, oldDir: string, newDir: string) {
  if (!url || !url.includes("local-view")) return url;
  try {
    const parsed = new URL(url);
    const path = parsed.searchParams.get("path");
    if (!path) return url;
    const oldRoot = normalized(oldDir);
    const current = normalized(path);
    if (current !== oldRoot && !current.startsWith(`${oldRoot}\\`)) return url;
    const relative = path.slice(oldDir.replace(/[\\/]+$/, "").length).replace(/^[\\/]+/, "");
    const root = newDir.replace(/[\\/]+$/, "");
    parsed.searchParams.set("path", relative ? `${root}\\${relative}` : root);
    return parsed.toString();
  } catch {
    return url;
  }
}
