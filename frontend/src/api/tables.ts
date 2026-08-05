import { apiGet, apiPost } from "./client";

// 一张通用数据表（背包/技能/任务/角色/选项…）。好感度/纪要由 state/narrative 各自管，不在此。
export interface DataTable {
  uid: string;
  name: string;
  columns: string[];
  rows: string[][];
  note: string;         // 这张表记什么
  order: number;
  rule: string;         // 何时增/改/删（AI 据此填表）
  colTypes: Record<string, string>;  // 列名→"文本"/"数字"
  keyCol: string;       // 身份列名（update/delete 按它定位同一条，空=按行号）
  mode?: string;        // full 全量注入 / retrieval 行进 RAG 按相关性召回（大表省 token）
}

// 填表 6 参数（数据表左下角设置面板；搭车范式下 fillEvery/minReplyLen 真正生效）
export interface TableConfig {
  contextTurns: number;  // 填表回看轮数
  fillEvery: number;     // 自动填表频率（每 N 轮，省 token 主开关）
  batchTurns: number;    // 批处理层数
  skipLatest: number;    // 跳过最新回复数
  minReplyLen: number;   // AI 回复最小长度
  maxRetry: number;      // 填表最大重试
  chronicleEvery: number; // 纪要频率（每 N 个 assistant 回合）
}

export interface TableStatusItem {
  uid: string;
  name: string;
  frequency: number;
  unrecorded: number;
  last_turn: number;
  selectable: boolean;
}

export interface TableStatus {
  total_turns: number;
  items: TableStatusItem[];
  config: TableConfig;
}

export interface ManualFillResult {
  ok: boolean;
  needs_confirmation: boolean;
  requested_start?: number;
  minimum_unrecorded?: number;
  total_turns?: number;
  processed?: number;
  calls?: number;
  applied?: number;
  chronicles?: number;
}

export interface ChatModelInput {
  baseUrl: string;
  apiKey: string;
  modelName: string;
  proxyUrl?: string;
}

// 列出某作品线全部通用表
export function listTables(outputDir: string, repoId: string) {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId });
  return apiGet<{ tables: DataTable[] }>(`/tables/?${q.toString()}`);
}

// 导入 TavernDB chatSheets 模板定义通用表 schema（replace=覆盖，否则只补新表）
export function importTableTemplate(
  outputDir: string, repoId: string, template: Record<string, unknown>, replace = false,
) {
  return apiPost<{ ok: boolean; imported: number; tables: DataTable[] }>(
    "/tables/import-template",
    { output_dir: outputDir, repo_id: repoId, template, replace },
  );
}

// 给某表新增一行（values 键=列名，缺列补空）
export function addTableRow(
  outputDir: string, repoId: string, table: string, values: Record<string, string>,
) {
  return apiPost<{ ok: boolean; tables: DataTable[] }>(
    "/tables/rows", { output_dir: outputDir, repo_id: repoId, table, values });
}

// 改某表某行单元格
export function updateTableRow(
  outputDir: string, repoId: string, table: string, row: number, values: Record<string, string>,
) {
  return apiPost<{ ok: boolean; tables: DataTable[] }>(
    "/tables/update", { output_dir: outputDir, repo_id: repoId, table, row, values });
}

// 删某表某行（0 基行号）
export function deleteTableRow(outputDir: string, repoId: string, table: string, row: number) {
  return apiPost<{ ok: boolean; tables: DataTable[] }>(
    "/tables/delete", { output_dir: outputDir, repo_id: repoId, table, row });
}

// 导出某作品线全部通用表
export function exportTables(outputDir: string, repoId: string) {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId });
  return apiGet<{ version: number; repo_id: string; tables: DataTable[] }>(
    `/tables/export?${q.toString()}`);
}

// 引导式建表：表名 + 列 + 说明/规则/列类型/身份列
export function createTable(
  outputDir: string, repoId: string, spec: {
    name: string; columns: string[]; note?: string; rule?: string;
    col_types?: Record<string, string>; key_col?: string;
  },
) {
  return apiPost<{ ok: boolean; tables: DataTable[] }>(
    "/tables/create", { output_dir: outputDir, repo_id: repoId, ...spec });
}

// 删整表
export function dropTable(outputDir: string, repoId: string, table: string) {
  return apiPost<{ ok: boolean; tables: DataTable[] }>(
    "/tables/drop", { output_dir: outputDir, repo_id: repoId, table });
}

// 改某表说明/规则/身份列/注入模式（full/retrieval）
export function setTableMeta(
  outputDir: string, repoId: string, table: string,
  meta: { note?: string; rule?: string; key_col?: string; mode?: string },
) {
  return apiPost<{ ok: boolean; tables: DataTable[] }>(
    "/tables/set-meta", { output_dir: outputDir, repo_id: repoId, table, ...meta });
}

// 读填表 6 参数（缺文件回退默认）
export function getTableConfig(outputDir: string, repoId: string) {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId });
  return apiGet<{ config: TableConfig }>(`/tables/config?${q.toString()}`);
}

// 写填表 6 参数
export function setTableConfig(
  outputDir: string, repoId: string, config: Partial<TableConfig>,
) {
  return apiPost<{ ok: boolean; config: TableConfig }>(
    "/tables/config", { output_dir: outputDir, repo_id: repoId, config });
}

export function getTableStatus(outputDir: string, repoId: string, cardName: string) {
  const q = new URLSearchParams({ output_dir: outputDir, repo_id: repoId, card_name: cardName });
  return apiGet<TableStatus>(`/tables/status?${q.toString()}`);
}

export function manualFillTables(
  outputDir: string,
  repoId: string,
  cardName: string,
  selected: string[],
  recentTurns: number,
  batchTurns: number,
  overwrite: boolean | null,
  chat: ChatModelInput,
) {
  return apiPost<ManualFillResult>("/tables/manual-fill", {
    output_dir: outputDir,
    repo_id: repoId,
    card_name: cardName,
    selected,
    recent_turns: recentTurns,
    batch_turns: batchTurns,
    overwrite,
    base_url: chat.baseUrl,
    api_key: chat.apiKey,
    model: chat.modelName,
    proxy: chat.proxyUrl || "",
  });
}
