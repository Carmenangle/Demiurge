import { apiGet, apiPost, apiUrl } from "./client";

export interface WorldbookSummary {
  name: string;
  file: string;
  entries: number;
}

export interface WorldbookConflict {
  reason: "exists";
  name: string;
}

export function listWorldbooks(base: string) {
  return apiGet<{ items: WorldbookSummary[] }>(
    `/worldbook/?base=${encodeURIComponent(base)}`,
  );
}

// 导入独立世界书。同名且 overwrite=false → 抛 WorldbookConflict（前端弹覆盖确认）
export async function importWorldbook(
  file: File, base: string, overwrite = false, name = "",
): Promise<WorldbookSummary & { ok: boolean }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("base", base);
  fd.append("overwrite", String(overwrite));
  if (name) fd.append("name", name);
  const resp = await fetch(apiUrl("/worldbook/import"), { method: "POST", body: fd });
  if (resp.status === 409) {
    const body = await resp.json().catch(() => ({}));
    const conflict = (body?.detail ?? body) as WorldbookConflict;
    throw Object.assign(new Error(`已存在同名世界书「${conflict.name}」`), { conflict });
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(typeof body?.detail === "string" ? body.detail : `导入失败: ${resp.status}`);
  }
  return resp.json();
}

export function worldbookDetail(base: string, name: string) {
  return apiGet<{ name: string; book: { entries?: unknown } }>(
    `/worldbook/detail?base=${encodeURIComponent(base)}&name=${encodeURIComponent(name)}`,
  );
}

export function deleteWorldbook(base: string, name: string) {
  return apiPost<{ ok: boolean }>("/worldbook/delete", { base, name });
}

// ⑤ 条目级增删改。定位二选一：独立书 {base,name}；卡内嵌 {characterDir,cardName}
export interface WBEntryFields {
  content: string;
  comment: string;
  keys: string[];
  constant: boolean;
  enabled: boolean;
}
export interface WBEntryItem extends WBEntryFields { index: number }
export type WBLocation =
  | { base: string; name: string }
  | { character_dir: string; card_name: string };

export function listWorldbookEntries(loc: WBLocation) {
  return apiPost<{ entries: WBEntryItem[] }>("/worldbook/entries", loc);
}
export function addWorldbookEntry(loc: WBLocation, entry: WBEntryFields) {
  return apiPost<{ ok: boolean; index: number }>("/worldbook/entry/add", { ...loc, entry });
}
export function updateWorldbookEntry(loc: WBLocation, index: number, entry: WBEntryFields) {
  return apiPost<{ ok: boolean }>("/worldbook/entry/update", { ...loc, index, entry });
}
export function deleteWorldbookEntry(loc: WBLocation, index: number) {
  return apiPost<{ ok: boolean }>("/worldbook/entry/delete", { ...loc, index });
}

// ── 仓库快照世界书 CRUD（画布模式编辑：读写 <repo>/worldbook.json）──

export interface RepoWorldbookLoc {
  output_dir: string;
  repo_id: string;
}

export function repoWorldbookEntries(loc: RepoWorldbookLoc, seedFrom?: { base: string; name: string }) {
  return apiPost<{ entries: WBEntryItem[]; not_found?: boolean }>(
    "/narrative/repo-worldbook/entries", seedFrom ? { ...loc, seed_from: seedFrom } : loc,
  );
}
export function repoWorldbookEntryAdd(loc: RepoWorldbookLoc, entry: WBEntryFields) {
  return apiPost<{ ok: boolean; index: number }>(
    "/narrative/repo-worldbook/entry/add", { ...loc, entry },
  );
}
export function repoWorldbookEntryUpdate(loc: RepoWorldbookLoc, index: number, entry: WBEntryFields) {
  return apiPost<{ ok: boolean }>(
    "/narrative/repo-worldbook/entry/update", { ...loc, index, entry },
  );
}
export function repoWorldbookEntryDelete(loc: RepoWorldbookLoc, index: number) {
  return apiPost<{ ok: boolean }>(
    "/narrative/repo-worldbook/entry/delete", { ...loc, index },
  );
}
