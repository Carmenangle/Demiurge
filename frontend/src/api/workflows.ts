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

// 多元数据插入内部绑定：界面与提交仍使用原工作流字段名，剧情高潮异步出图按隐藏 binding 注入值。
// prompt=后端提取的提示词；lora_name=角色 LoRA 文件名；lora_weight=LoRA 权重。
export const SEMANTIC_PROMPT = "prompt";
export const SEMANTIC_NEGATIVE_PROMPT = "negative_prompt";
export const SEMANTIC_LORA_NAME = "lora_name";
export const SEMANTIC_LORA_WEIGHT = "lora_weight";
export const SEMANTIC_BASE_IMAGE = "base_image";
export const SEMANTIC_LATENT_WIDTH = "latent_width";
export const SEMANTIC_LATENT_HEIGHT = "latent_height";
// 音频（IndexTTS 系）：voice_text=该角色台词（写 TTS 节点 text）；voice_reference=参考音轨（音色，写 LoadAudio）；
// voice_emotion_<key>=8 维情感向量（happy/angry/sad/fear/hate/low/surprise/neutral，0~1 混合权重）。
export const SEMANTIC_VOICE_TEXT = "voice_text";
export const SEMANTIC_VOICE_REFERENCE = "voice_reference";
export const VOICE_EMOTION_KEYS = ["happy", "angry", "sad", "fear", "hate", "low", "surprise", "neutral"] as const;
export type VoiceEmotionKey = (typeof VOICE_EMOTION_KEYS)[number];
export const VOICE_EMOTION_PREFIX = "voice_emotion_";
export const voiceEmotionSemantic = (key: VoiceEmotionKey) => `${VOICE_EMOTION_PREFIX}${key}` as const;
export const MEDIA_INSERT_SEMANTICS = [
  { value: SEMANTIC_PROMPT, label: "提示词（必选，写入 booru 提示词）" },
  { value: SEMANTIC_NEGATIVE_PROMPT, label: "负面提示词（独立写入负向条件）" },
  { value: SEMANTIC_LORA_NAME, label: "角色 LoRA 文件名" },
  { value: SEMANTIC_LORA_WEIGHT, label: "角色 LoRA 权重" },
  { value: SEMANTIC_BASE_IMAGE, label: "角色底图（图生图输入图节点）" },
  { value: SEMANTIC_LATENT_WIDTH, label: "Latent 宽度（按 Agent 画幅比例换算）" },
  { value: SEMANTIC_LATENT_HEIGHT, label: "Latent 高度（按 Agent 画幅比例换算）" },
  { value: SEMANTIC_VOICE_TEXT, label: "角色台词（写 TTS 节点 text）" },
  { value: SEMANTIC_VOICE_REFERENCE, label: "参考音轨（音色，写 LoadAudio）" },
  ...VOICE_EMOTION_KEYS.map((key) => ({
    value: voiceEmotionSemantic(key),
    label: `情感向量·${key}（写 EmotionVector 节点）`,
  })),
];

// 控件类型
export type ControlType = "text" | "textarea" | "number" | "select" | "image" | "seed" | "boolean";

export interface ExposedField {
  node_id: string;
  field: string;
  label: string;
  control: ControlType;
  semantic: string;
  binding?: string; // 内部自动用途；界面字段名/semantic 始终保持原工作流定义
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
