import { apiGet, apiPost, apiPut, apiDelete } from "./client";

export interface ScannedWorkflow {
  name: string;
  path: string;
  rel: string;
}

export interface ParsedField {
  name: string;
  value: string | number | boolean | null;
  linked: boolean;
  required: boolean;
}

export interface ParsedNode {
  id: string;
  class_type: string;
  title: string;
  bypassed: boolean;
  fields: ParsedField[];
}

export function scanWorkflows(dir: string) {
  return apiGet<{ items: ScannedWorkflow[] }>(`/workflows/scan?dir=${encodeURIComponent(dir)}`);
}

export function parseWorkflowByPath(path: string) {
  return apiPost<{ nodes: ParsedNode[]; node_count: number }>("/workflows/parse", { path });
}

export function parseWorkflowJson(workflow: unknown) {
  return apiPost<{ nodes: ParsedNode[]; node_count: number }>("/workflows/parse", { workflow });
}

// 按路径取原始工作流 JSON（模板编辑页 ComfyUI 画布预览用）
export function rawWorkflowByPath(path: string) {
  return apiGet<{ workflow: unknown }>(`/workflows/raw?path=${encodeURIComponent(path)}`);
}

// 控件类型
export type ControlType = "text" | "textarea" | "number" | "select" | "image" | "seed" | "boolean";

export interface ExposedField {
  node_id: string;
  field: string;
  label: string;
  control: ControlType;
  semantic: string;
  default: string | number | boolean | null;
}

export interface Template {
  id: string;
  name: string;
  source_path: string;
  exposed: ExposedField[];
  node_order: string[];
  description?: string;
  prompt_node_id?: string;
  image_node_id?: string;
  input_node_ids: string[];
  output_node_ids: string[];
  primary_output_node_id?: string;  // 主输出节点（多输出时优先取用）
  created_at: number;
  updated_at: number;
}

export interface TemplatePayload {
  name: string;
  source_path: string;
  exposed: ExposedField[];
  node_order?: string[];
  description?: string;
  prompt_node_id?: string;
  image_node_id?: string;
  input_node_ids?: string[];
  output_node_ids?: string[];
  primary_output_node_id?: string;
}

export function listTemplates() {
  return apiGet<{ items: Template[] }>("/workflows/templates");
}

export interface TemplateRaw {
  workflow: unknown;
  exposed_ids: string[];
  description?: string;
  prompt_node_id?: string;
  image_node_id?: string;
  input_node_ids?: string[];
  output_node_ids?: string[];
  primary_output_node_id?: string;
}

// 取模板原始工作流 + 暴露节点 id（供锁定画布载入）
export function getTemplateRaw(id: string) {
  return apiGet<TemplateRaw>(`/workflows/templates/${id}/raw`);
}

export function createTemplate(payload: TemplatePayload) {
  return apiPost<Template>("/workflows/templates", payload);
}

export function updateTemplate(id: string, payload: TemplatePayload) {
  return apiPut<Template>(`/workflows/templates/${id}`, payload);
}

export function deleteTemplate(id: string) {
  return apiDelete<{ ok: boolean }>(`/workflows/templates/${id}`);
}
