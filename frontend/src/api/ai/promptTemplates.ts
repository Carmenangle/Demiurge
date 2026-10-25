// 提示词模版（2026-10-25 工作台）：内置默认模版只读 + 用户自定义模版 CRUD。
// 后端真源 app/services/prompt_template_store.py；本文件只做 wire 序列化。
import { apiDelete, apiGet, apiPost, apiPut } from "../client";

/** 协议族六值：生图三档 + 视频三形态（krea2 已下线，不进列表）。 */
export type PromptTemplateKind =
  | "anima_tags" | "natural_language" | "niji_sections"
  | "video_climax" | "video_firstlast" | "video_transition";

export const IMAGE_KINDS: readonly PromptTemplateKind[] = [
  "anima_tags", "natural_language", "niji_sections",
];
export const VIDEO_KINDS: readonly PromptTemplateKind[] = [
  "video_climax", "video_firstlast", "video_transition",
];

export const KIND_LABELS: Record<PromptTemplateKind, string> = {
  anima_tags: "Anima（质量行 + 内容 tags / 英文描述）",
  natural_language: "自然语言（GPT Image / Banana）",
  niji_sections: "Niji（主体 / 风格 / 附加 / 后缀）",
  video_climax: "视频·高潮点动作代入",
  video_firstlast: "视频·首尾帧剧情影片",
  video_transition: "视频·转场短桥段",
};

/** 区块键（视频六段；顺序/标题可被模版覆盖，语义固定）。 */
export const SECTION_KEYS = ["meta", "binding", "subject", "segments", "audio", "negative"] as const;
export type SectionKey = (typeof SECTION_KEYS)[number];

export const SECTION_KEY_LABELS: Record<SectionKey, string> = {
  meta: "元信息",
  binding: "参考绑定",
  subject: "主体/场景",
  segments: "时间分镜 / 转场分镜",
  audio: "音频",
  negative: "负面约束",
};

export interface PromptTemplate {
  id: string;                 // 自定义=32位hex；内置=builtin:<kind>
  builtin: boolean;
  kind: PromptTemplateKind;
  name: string;
  base_id: string;
  base_hash: string;
  redline_lock: boolean;
  redline_lock_off_at: number;
  content_hash?: string;      // 仅内置：内容哈希（克隆基准/「有更新」比对）
  // 生图字段
  system_text: string;
  extra_instructions: string;
  quality_tags: string;
  negative_tags: string;
  // 视频字段
  section_titles: Record<string, string>;
  section_order: string[];
  camera_band_overrides: Record<string, string>;
  beat_labels: string[];
  default_duration: number;
  negative_extra: string;
  meta_note: string;
  created_at?: number;
  updated_at?: number;
}

export interface PromptTemplateList {
  items: PromptTemplate[];    // 我的模版（新→旧）
  builtins: PromptTemplate[]; // 内置默认（只读全文）
}

export interface PromptTemplateBody {
  kind: PromptTemplateKind;
  name: string;
  system_text?: string;
  extra_instructions?: string;
  quality_tags?: string;
  negative_tags?: string;
  redline_lock?: boolean;
  section_titles?: Record<string, string>;
  section_order?: string[];
  camera_band_overrides?: Record<string, string>;
  beat_labels?: string[];
  default_duration?: number;
  negative_extra?: string;
  meta_note?: string;
  base_id?: string;           // 克隆基准（builtin:<kind> / custom:<hex>）
}

export function listPromptTemplates(kind: PromptTemplateKind | "" = ""): Promise<PromptTemplateList> {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return apiGet<PromptTemplateList>(`/ai/prompt/templates${query}`);
}

export function getPromptTemplate(templateId: string): Promise<PromptTemplate> {
  return apiGet<PromptTemplate>(`/ai/prompt/templates/${encodeURIComponent(templateId)}`);
}

export function createPromptTemplate(body: PromptTemplateBody): Promise<PromptTemplate> {
  return apiPost<PromptTemplate>("/ai/prompt/templates", body);
}

export function updatePromptTemplate(templateId: string, body: PromptTemplateBody): Promise<PromptTemplate> {
  return apiPut<PromptTemplate>(`/ai/prompt/templates/${encodeURIComponent(templateId)}`, body);
}

export function deletePromptTemplate(templateId: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`/ai/prompt/templates/${encodeURIComponent(templateId)}`);
}
