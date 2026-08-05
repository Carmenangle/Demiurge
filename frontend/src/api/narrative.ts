import { apiGet, apiPost } from "./client";

// 一条事件纪要（AM 码大纲）。layer: 0 细 / 1 中 / 2 粗。
export interface ChronicleEntry {
  rowid: number;
  text: string;
  overview: string;
  dialogue: string;
  characters: string[];
  turn_start: number;
  turn_end: number;
  layer: number;
  keywords: string[];
}

// 列出某作品线最近 k 条纪要（时间倒序）
export function listChronicle(outputDir: string, repoId: string, k = 50) {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId, k: String(k) });
  return apiGet<{ items: ChronicleEntry[] }>(`/narrative/?${q.toString()}`);
}

// 按 trigram 相关性检索纪要
export function searchChronicle(outputDir: string, repoId: string, query: string, k = 8) {
  return apiPost<{ items: ChronicleEntry[] }>("/narrative/search", {
    output_dir: outputDir, repo_id: repoId, query, k,
  });
}

// RAG 重建：从已存正文清空并重建 FTS5 索引
export function rebuildChronicle(outputDir: string, repoId: string) {
  return apiPost<{ ok: boolean; rebuilt: number }>("/narrative/rebuild", {
    output_dir: outputDir, repo_id: repoId,
  });
}

// ⑤ 人工增删改 + 导入导出（可视化 CRUD）
export type EntryDraft = Omit<ChronicleEntry, "rowid">;

export function addChronicle(outputDir: string, repoId: string, entry: EntryDraft) {
  return apiPost<{ ok: boolean; rowid: number }>("/narrative/add", {
    output_dir: outputDir, repo_id: repoId, entry,
  });
}

export function updateChronicle(outputDir: string, repoId: string, rowid: number, entry: EntryDraft) {
  return apiPost<{ ok: boolean }>("/narrative/update", {
    output_dir: outputDir, repo_id: repoId, rowid, entry,
  });
}

export function deleteChronicle(outputDir: string, repoId: string, rowids: number[]) {
  return apiPost<{ ok: boolean; deleted: number }>("/narrative/delete", {
    output_dir: outputDir, repo_id: repoId, rowids,
  });
}

export function exportChronicle(outputDir: string, repoId: string) {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId });
  return apiGet<{ version: number; repo_id: string; items: ChronicleEntry[] }>(
    `/narrative/export?${q.toString()}`);
}

export function importChronicle(outputDir: string, repoId: string, items: EntryDraft[], replace = false) {
  return apiPost<{ ok: boolean; imported: number }>("/narrative/import", {
    output_dir: outputDir, repo_id: repoId, items, replace,
  });
}
