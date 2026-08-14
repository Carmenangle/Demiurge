import { apiGet, apiPost } from "./client";

// 一条事件纪要（AM 码大纲）。layer: 0 细 / 1 中 / 2 粗。
export interface ChronicleEntry {
  rowid: number;
  card_id: string;
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
export type EntryDraft = Omit<ChronicleEntry, "rowid" | "card_id">;

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


// ─── Narrative CI（非阻断一致性诊断）────────────────────────────────────────

export type NarrativeCISeverity = "info" | "warning" | "error";

export interface NarrativeDiagnostic {
  id: string;
  turn: number;
  code: string;
  severity: NarrativeCISeverity;
  message: string;
  evidence: string;
  source: string;
  status: string; // open | fixed | foreshadow | retcon | accepted
}

export const NARRATIVE_CI_CODES: Record<string, string> = {
  active_fact_conflict: "事实冲突",
  fact_contradiction: "否定事实",
  relationship_jump: "数值跳变",
  location_without_transition: "位置跳变",
  knowledge_overreach: "认知越权",
  temporal_paradox: "时间矛盾",
  spatial_inconsistency: "空间矛盾",
  relationship_change: "关系剧变",
  world_rule_break: "违反世界规则",
  character_belief_conflict: "认知与事实矛盾",
};

// 手动运行 Narrative CI（内容中立，只保存诊断，不修改正文）
export function runNarrativeCi(outputDir: string, repoId: string, text: string, turn: number) {
  return apiPost<{ items: NarrativeDiagnostic[] }>("/narrative/ci/check", {
    output_dir: outputDir, repo_id: repoId, text, turn,
  });
}

// 列出诊断（可按状态/代码过滤）
export function listNarrativeCi(outputDir: string, repoId: string, status = "", code = "") {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId });
  if (status) q.set("status", status);
  if (code) q.set("code", code);
  return apiGet<{ items: NarrativeDiagnostic[] }>(`/narrative/ci?${q.toString()}`);
}

// 处置诊断（fixed / foreshadow / retcon / accepted）
export function resolveNarrativeCi(outputDir: string, repoId: string, diagnosticId: string, status: string) {
  return apiPost<{ ok: boolean }>("/narrative/ci/resolve", {
    output_dir: outputDir, repo_id: repoId, diagnostic_id: diagnosticId, status,
  });
}
