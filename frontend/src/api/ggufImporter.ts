// GGUF 模型导入 API 封装
import { apiGet, apiPost } from "./client";

// ── 类型 ──────────────────────────────────────────────────────────────

export interface GgufMeta {
  path: string;
  filename: string;
  size_bytes: number;
  size_gb: number;
  size_label: string;
  architecture: string;
  kind: string;           // model | mmproj
  parameters_b: number;
  context_length: number;
  quant: string;
  is_vision: boolean;
  has_vision_encoder: boolean;
  is_embedding: boolean;
  name: string;
  notes: string[];
}

export interface GgufFit {
  level: "ok" | "partial_offload" | "low" | "cpu_only";
  device: {
    available_mib: number;
    total_mib: number;
    name: string;
    probe_source: string;
    probe_error?: string;
  };
  model_mib: number;
  context_mib: number;
  total_needed_mib: number;
  suggestions: string[];
}

export interface GgufScanResult {
  directory: string;
  count: number;
  files: GgufMeta[];
  models: GgufMeta[];
  mmproj: GgufMeta[];
  error?: string;
}

export interface GgufParseResult {
  meta: GgufMeta;
  fit: GgufFit;
  suggested_name: string;
}

export interface GgufImportResult {
  ok: boolean;
  model_name: string;
  message: string;
  meta: GgufMeta | null;
  elapsed_sec: number;
  fit: GgufFit | null;
  register?: { ok: boolean; message: string; provider_id?: string };
}

export interface GgufStatus {
  running: boolean;
  models: string[];
  count: number;
}

// ── API ───────────────────────────────────────────────────────────────

export async function ggufStatus(timeoutMs = 8000): Promise<GgufStatus> {
  return apiGet<GgufStatus>(`/gguf/status?t=${Date.now()}`);
}

export async function ggufScan(directory: string, timeoutMs = 20000): Promise<GgufScanResult> {
  return apiPost<GgufScanResult>("/gguf/scan", { directory }, timeoutMs);
}

export async function ggufParse(ggufPath: string, timeoutMs = 15000): Promise<GgufParseResult> {
  return apiPost<GgufParseResult>("/gguf/parse", { gguf_path: ggufPath }, timeoutMs);
}

export async function ggufImport(
  params: {
    ggufPath: string;
    modelName?: string;
    mmprojPath?: string;
    quantize?: string;
    registerProvider?: boolean;
  },
  timeoutMs = 900_000,   // 大模型导入可能耗时数分钟
): Promise<GgufImportResult> {
  return apiPost<GgufImportResult>("/gguf/import", {
    gguf_path: params.ggufPath,
    model_name: params.modelName || "",
    mmproj_path: params.mmprojPath || "",
    quantize: params.quantize || "",
    register_provider: params.registerProvider ?? true,
  }, timeoutMs);
}
