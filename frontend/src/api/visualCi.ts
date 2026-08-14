// Visual CI 诊断 API 封装：对自动插画做非阻断验收诊断。
// 后端路由前缀 /api/visual-ci（backend/app/routers/visual_ci.py）。
import { apiPost } from "./client";

// ── 后端数据结构（与 backend/app/services/visual_ci.py 对齐）──

export interface VisualCiFieldLedger {
  name: string;
  required: boolean;
  covered: boolean;
  evidence: string;
  vlm_ok: boolean | null; // null = 未检
  score: number; // 0-1
}

export interface VisualCiMechanicalLedger {
  checkpoint: string;
  loras: { name: string; weight: number }[];
  seed: number | null;
  width: number;
  height: number;
  sampler: string;
  steps: number;
  cfg: number;
  prompt_chars: number;
}

export interface VisualCiVlmAssessment {
  model: string;
  dimensions: Record<string, boolean>; // 维度名 → 是否通过
  overall_ok: boolean | null;
  summary: string;
  raw_response: string;
}

export interface VisualCiDiagnostic {
  id: string;
  generation_id: string;
  turn_id: string;
  status: "pending" | "ok" | "warn" | "fail" | "retry" | "error";
  verdict: "green" | "amber" | "red" | "unknown";
  mechanical: VisualCiMechanicalLedger;
  vlm: VisualCiVlmAssessment;
  similarity: number; // 0-1，与参考图的相似度
  field_ledger: VisualCiFieldLedger[];
  retry_count: number;
  retry_of: string;
  evidence: Record<string, unknown>;
  created_at: string;
}

export interface VisualCiChatConfig {
  base_url: string;
  api_key: string;
  model: string;
  proxy: string;
}

export interface RunVisualCiParams {
  generationId: string;
  repoId: string;
  outputDir: string;
  turnId?: string;
  generationRecord?: Record<string, unknown>;
  sceneSpec?: Record<string, unknown>;
  referenceImageUrl?: string;
  chat?: Partial<VisualCiChatConfig>;
}

/** 执行一次 Visual CI 诊断（非阻断，失败返回 error 状态而非抛异常）。 */
export async function runVisualCiDiagnostic(
  params: RunVisualCiParams,
): Promise<VisualCiDiagnostic> {
  return apiPost<VisualCiDiagnostic>("/visual-ci/run", {
    generation_id: params.generationId,
    repo_id: params.repoId,
    output_dir: params.outputDir,
    turn_id: params.turnId || "",
    generation_record: params.generationRecord || {},
    scene_spec: params.sceneSpec || {},
    reference_image_url: params.referenceImageUrl || "",
    chat: params.chat || {},
  });
}

/** 按 generation_id 加载最新诊断；无记录时抛 404。 */
export async function loadVisualCiDiagnostic(
  generationId: string,
  repoId: string,
  outputDir: string,
): Promise<VisualCiDiagnostic> {
  return apiPost<VisualCiDiagnostic>("/visual-ci/load", {
    generation_id: generationId,
    repo_id: repoId,
    output_dir: outputDir,
  });
}

export interface ListVisualCiParams {
  repoId: string;
  outputDir: string;
  status?: VisualCiDiagnostic["status"];
  limit?: number;
}

export async function listVisualCiDiagnostics(
  params: ListVisualCiParams,
): Promise<VisualCiDiagnostic[]> {
  return apiPost<VisualCiDiagnostic[]>("/visual-ci/list", {
    repo_id: params.repoId,
    output_dir: params.outputDir,
    status: params.status || null,
    limit: params.limit || 50,
  });
}

/** 申请受限重试（默认最多 1 次，可配 1-3）。 */
export async function requestVisualCiRetry(
  generationId: string,
  repoId: string,
  outputDir: string,
  maxRetries = 1,
): Promise<VisualCiDiagnostic> {
  return apiPost<VisualCiDiagnostic>("/visual-ci/request-retry", {
    generation_id: generationId,
    repo_id: repoId,
    output_dir: outputDir,
    max_retries: maxRetries,
  });
}

// ── 前端展示辅助 ──

/** verdict → 中文标签 */
export function visualCiVerdictLabel(verdict: VisualCiDiagnostic["verdict"]): string {
  switch (verdict) {
    case "green": return "验收通过";
    case "amber": return "有疑点";
    case "red": return "未通过";
    default: return "未诊断";
  }
}

/** verdict → CSS class 后缀 */
export function visualCiVerdictClass(verdict: VisualCiDiagnostic["verdict"]): string {
  switch (verdict) {
    case "green": return "vc-green";
    case "amber": return "vc-amber";
    case "red": return "vc-red";
    default: return "vc-unknown";
  }
}

/** 诊断状态 → 短标签 */
export function visualCiStatusLabel(status: VisualCiDiagnostic["status"]): string {
  switch (status) {
    case "ok": return "通过";
    case "warn": return "警告";
    case "fail": return "失败";
    case "retry": return "已重试";
    case "error": return "诊断出错";
    default: return "诊断中";
  }
}
