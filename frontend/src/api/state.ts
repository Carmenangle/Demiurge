import { apiGet, apiPatch, apiPost } from "./client";

// 数值字段（好感度等）：value + 边界 + provenance
export interface NumericField {
  value: number;
  min: number;
  max: number;
  turn: number;
  evidence: string;
  source: string;   // auto=剧情推导 / user=人工设定
}
// 叙事字段（态度/所在等）：字符串值 + provenance
export interface NarrativeField {
  value: string;
  turn: number;
  evidence: string;
  source: string;
}
export interface Snapshot { text: string; turn: number }
export interface HistoryEntry {
  turn: number; field: string; op: string;
  from: unknown; to: unknown; evidence: string; source: string;
}
export interface CharacterStateDto {
  card_name: string;
  repo_id: string;
  数值: Record<string, NumericField>;
  叙事: Record<string, NarrativeField>;
  快照: Snapshot;
  历史: HistoryEntry[];
}

export function getState(outputDir: string, repoId: string, cardName = "") {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId, card_name: cardName });
  return apiGet<CharacterStateDto>(`/state/?${q.toString()}`);
}

// 人工修正：把字段设为精确值（数值直接设，非累加），标 source=user
export function patchState(
  outputDir: string, repoId: string, cardName: string,
  edits: { field: string; value: number | string }[],
) {
  return apiPatch<CharacterStateDto & { updated: number }>("/state/", {
    output_dir: outputDir, repo_id: repoId, card_name: cardName, edits,
  });
}

// 回滚：撤销最近 n 条变更，字段还原到审计历史 from 值
export function rollbackState(outputDir: string, repoId: string, cardName: string, n = 1) {
  return apiPost<CharacterStateDto & { undone: number }>("/state/rollback", {
    output_dir: outputDir, repo_id: repoId, card_name: cardName, n,
  });
}

// ⑤ 删除字段（新增字段复用 patchState，缺失键会被创建）+ 导入导出
export function deleteStateField(outputDir: string, repoId: string, cardName: string, field: string) {
  return apiPost<CharacterStateDto & { ok: boolean }>("/state/delete-field", {
    output_dir: outputDir, repo_id: repoId, card_name: cardName, field,
  });
}

export function exportState(outputDir: string, repoId: string, cardName = "") {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId, card_name: cardName });
  return apiGet<{ version: number } & CharacterStateDto>(`/state/export?${q.toString()}`);
}

export function importState(
  outputDir: string, repoId: string, cardName: string, state: Partial<CharacterStateDto>,
) {
  return apiPost<CharacterStateDto & { ok: boolean }>("/state/import", {
    output_dir: outputDir, repo_id: repoId, card_name: cardName, state,
  });
}
