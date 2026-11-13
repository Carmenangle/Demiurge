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

// 区间整理：同一回合被多条纪要覆盖时，先出计划再确认（2026-09-13）
export interface NormalizeDuplicate {
  turn_start: number;
  turn_end: number;
  rowids: number[];
  keep: number;
  drop: number[];
}

export interface NormalizeRebase {
  rowid: number;
  old: [number, number];
  new: [number, number];
}

export interface NormalizePlan {
  ok: boolean;
  total_turns: number;
  entries: number;
  covered_turns: number;
  clean: boolean;
  conflict_turns: number[];   // 被 >=2 条纪要覆盖的回合
  duplicates: NormalizeDuplicate[];
  rebases: NormalizeRebase[]; // 相邻批次共享边界层，顺延保宽
  gaps: [number, number][];   // 没有任何纪要覆盖的回合区间
  // 2026-10-21：顺延后超出本会话层数的 rowid（用户定案「接受级联」，不改写顺延结果）。
  // UI 必须明示警告：这些区间会落到不存在的回合上，建议先删重复条目再整理。
  overrun: number[];
}

// 只读预览：不落盘，供用户确认
export function previewNormalizeChronicle(outputDir: string, repoId: string, totalTurns = 0) {
  return apiPost<NormalizePlan>("/narrative/normalize", {
    output_dir: outputDir, repo_id: repoId, total_turns: totalTurns,
  });
}

// 落盘整理：封口让位的重复条目 + 改写边界重叠区间
export function applyNormalizeChronicle(
  outputDir: string, repoId: string, sealRowids: number[], rebases: NormalizeRebase[],
) {
  return apiPost<{ ok: boolean; sealed: number; rebased: number }>("/narrative/normalize/apply", {
    output_dir: outputDir, repo_id: repoId, seal_rowids: sealRowids, rebases,
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
  // 缺陷 4 第三段：World 判失败的自主尝试，正文没写成未遂/受挫（提示级，非定罪——
  // 2026-11-06 真语料实测精确 82%／召回 100%，剩下的假阳是「合规但没用失败措辞」的写法）。
  npc_verdict_break: "判失败没写未遂",
};

// 臂参与台账：前两条判据的输入来自世界书，没书时整臂不跑（不产出诊断）；第三条来自
// 本轮 World 裁定（缺陷 4）。面板必须把「没参与」和原因显示出来，否则空列表分不清
// 「查过没问题」与「空转」。臂名/原因码与后端 `narrative_ci.py` 逐字同串，由
// `NarrativeCiPanel.arms.test.tsx` 的跨语言契约锁住（后端改名 ⇒ 前端测试红）。
export interface NarrativeCiArmStatus {
  participated: boolean;
  count: number;
  reason: string;
}

export interface NarrativeCiArmsReport {
  turn: number;
  arms: Record<string, NarrativeCiArmStatus>;
}

export const NARRATIVE_CI_ARM_ORDER = ["world_rule", "term_ratchet_stock", "agency_verdict"] as const;

export const NARRATIVE_CI_ARM_LABELS: Record<string, string> = {
  world_rule: "世界规则",
  term_ratchet_stock: "棘轮·设定存量",
  agency_verdict: "World 裁定",
};

export const NARRATIVE_CI_ARM_REASONS: Record<string, string> = {
  no_worldbook: "这个作品没有世界书，判据没有输入",
  snapshot_unreadable: "世界书快照读不出来（损坏或格式不对），判据没有输入",
  empty_worldbook: "世界书快照是空的，判据没有输入",
  no_rule_lines: "世界书里没有「禁止／必须／唯一」这类规则句",
  no_static_base: "世界书条目里没有静态设定底座",
  world_not_consulted: "本轮没有可核对的 World 裁定（未咨询，或咨询失败）",
  no_proposal: "World 本轮没有提出任何自主行动",
  no_rolled_attempt: "World 有提案但都没掷骰（被前置门槛拦下），正文未被要求体现",
};

// 手动运行 Narrative CI（内容中立，只保存诊断，不修改正文）
export function runNarrativeCi(outputDir: string, repoId: string, text: string, turn: number) {
  return apiPost<{ items: NarrativeDiagnostic[] }>("/narrative/ci/check", {
    output_dir: outputDir, repo_id: repoId, text, turn,
  });
}

// 列出诊断（可按状态/代码过滤）＋最近一轮的臂参与台账
export function listNarrativeCi(outputDir: string, repoId: string, status = "", code = "") {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId });
  if (status) q.set("status", status);
  if (code) q.set("code", code);
  return apiGet<{ items: NarrativeDiagnostic[]; arms?: NarrativeCiArmsReport | null }>(
    `/narrative/ci?${q.toString()}`,
  );
}

// 处置诊断（fixed / foreshadow / retcon / accepted）
export function resolveNarrativeCi(outputDir: string, repoId: string, diagnosticId: string, status: string) {
  return apiPost<{ ok: boolean }>("/narrative/ci/resolve", {
    output_dir: outputDir, repo_id: repoId, diagnostic_id: diagnosticId, status,
  });
}

// ── 文风（去 AI 味）配置：S0 lint + S1 生成侧注入共用词表（属主 prose_style）──

export interface ProseStyleConfig {
  enabled: boolean;
  extra: string[];      // 用户增补的 AI 味词
  removed: string[];    // 用户从内置词表移除的词（误报豁免）
  review_every: number; // S2 活人感通审采样频率（每 N 轮一次，0=关闭）
}

export function getProseStyle() {
  return apiGet<ProseStyleConfig>("/narrative/prose-style");
}

export function saveProseStyle(config: ProseStyleConfig) {
  return apiPost<ProseStyleConfig>("/narrative/prose-style", config);
}
