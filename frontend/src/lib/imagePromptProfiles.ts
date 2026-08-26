import type { VideoMode } from "./illustrationMedia";

export type PromptProfileId = "krea2" | "anima_tags" | "natural_language" | "niji_sections";

export const PROMPT_PROFILE_OPTIONS: readonly { id: PromptProfileId; label: string }[] = [
  { id: "krea2", label: "Krea2（剧情高潮英文描述）" },
  { id: "anima_tags", label: "Anima（质量行 + 内容 tags / 英文描述）" },
  { id: "natural_language", label: "自然语言（GPT Image / Banana）" },
  { id: "niji_sections", label: "Niji（主体 / 风格 / 附加 / 后缀）" },
];

const IDS = new Set(PROMPT_PROFILE_OPTIONS.map((item) => item.id));

export function normalizePromptProfile(value: unknown): PromptProfileId {
  return typeof value === "string" && IDS.has(value as PromptProfileId)
    ? value as PromptProfileId
    : "krea2";
}

export function prependLoraTriggers(prompt: string, triggers: readonly string[]): string {
  const newline = prompt.indexOf("\n");
  const words = triggers
    .map((word) => word.trim())
    .filter((word, index, all) => word && all.findIndex(
      (candidate) => candidate.toLowerCase() === word.toLowerCase(),
    ) === index)
    .filter((word) => !prompt.toLowerCase().includes(word.toLowerCase()));
  if (!words.length) return prompt;
  if (newline < 0) return `${words.join(", ")}, ${prompt}`;
  return `${words.join(", ")}, ${prompt.slice(0, newline)}${prompt.slice(newline)}`;
}

export function applyProfileLoraTriggers(
  prompt: string,
  _profile: PromptProfileId,
  triggers: readonly string[],
): string {
  return prependLoraTriggers(prompt, triggers);
}

const ANIMA_ILLUSTRATION_STYLE = [
  "anime illustration", "hand-drawn anime style", "2d cel shading", "non-photorealistic",
] as const;

export function ensureAnimaIllustrationStyle(prompt: string, enabled: boolean): string {
  if (!enabled) return prompt;
  const newline = prompt.indexOf("\n");
  if (newline < 0) return prompt;
  const tags = prompt.slice(0, newline).trim();
  const prose = prompt.slice(newline + 1).trim();
  const lower = tags.toLowerCase();
  const missing = ANIMA_ILLUSTRATION_STYLE.filter((tag) => !lower.includes(tag));
  const trailingComma = tags.endsWith(",");
  const merged = [tags.replace(/,\s*$/, ""), ...missing].filter(Boolean).join(", ");
  return `${merged}${trailingComma ? "," : ""}\n${prose}`;
}

function splitQualityTags(value: string): string[] {
  const tags: string[] = [];
  let current = "";
  let depth = 0;
  for (const char of value.replace(/\r\n?/g, "\n")) {
    if (char === "(") depth += 1;
    else if (char === ")" && depth > 0) depth -= 1;
    if ((char === "," || char === ";" || char === "\n") && depth === 0) {
      if (current.trim()) tags.push(current.trim());
      current = "";
    } else current += char;
  }
  if (current.trim()) tags.push(current.trim());
  return tags.filter((tag, index, all) => all.findIndex(
    (candidate) => candidate.toLowerCase() === tag.toLowerCase(),
  ) === index);
}

export function replacePromptQualityLine(
  prompt: string,
  fixedQuality: string,
  rating: "sfw" | "nsfw" = "sfw",
): string {
  const lines = prompt.replace(/\r\n?/g, "\n").split("\n");
  const originalTags = splitQualityTags(lines[0] || "");
  const configuredQuality = splitQualityTags(fixedQuality.trim());
  const qualityLike = (tag: string) => {
    const value = tag.trim().toLowerCase();
    return /^(?:score_[1-9]|old quality)$/.test(value) || [
      "masterpiece", "best quality", "sensitive", "explicit", "very aesthetic",
      "ultra detailed", "fair skin", "high contrast", "amazing quality", "newest",
      "absurdres", "8k", "high resolution", "refined details", "good anatomy",
      "good shading", "sharp focus", "anime coloring",
    ].includes(value);
  };
  const qualitySource = configuredQuality.length ? configuredQuality : originalTags;
  const quality = qualitySource
    .filter((tag) => rating === "nsfw" || !/^(?:sensitive|explicit)$/i.test(tag.trim()))
  const contentTags = configuredQuality.length
    ? originalTags.filter((tag) => !qualityLike(tag))
    : [];
  const tags = [...quality, ...contentTags].filter((tag, index, all) => all.findIndex(
    (candidate) => candidate.toLowerCase() === tag.toLowerCase(),
  ) === index).join(", ");
  const prose = lines.slice(1)
    .map((line) => line.trim())
    .filter((line) => line && /^[\x20-\x7E\t]+$/.test(line))
    .join(" ");
  const trailingComma = (lines[0] || "").trimEnd().endsWith(",");
  const tagLine = tags ? `${tags}${trailingComma ? "," : ""}` : "";
  if (tagLine && prose) return `${tagLine}\n${prose}`;
  return tagLine || prose;
}

interface SemanticField {
  node_id: string;
  field: string;
  semantic: string;
  binding?: string;
}

export function workflowFieldBinding(field: SemanticField): string {
  if (field.binding) return field.binding;
  if (field.semantic && field.semantic !== field.field) return field.semantic; // 旧模板兼容
  return "";
}

interface IllustrationValues {
  prompt: string;
  negativePrompt?: string;
  loraName?: string;
  loraWeight?: number;
  baseImage?: string;
  latentSize?: { width: number; height: number };
  /** V1.2 视频最小事实：时长（秒）与镜头运动（static/pan/zoom），来自用户预设，不从模型输出读 */
  videoDuration?: number;
  videoCamera?: "static" | "pan" | "zoom";
  /** V1.5/B1 视频模式 + 首尾帧描述：来自 preset.videoMode 与 illustrate_request 事件可选字段 */
  videoMode?: VideoMode;
  firstFrameDesc?: string;
  lastFrameDesc?: string;
  prevTailDesc?: string;
  lastFrameUrl?: string;
  /** V1.5/B3 双帧图：首帧图（已上传 ComfyUI）与尾帧图（事件 lastFrameUrl 上传后） */
  firstFrameImage?: string;
  lastFrameImage?: string;
  /** V1.5 默认开放：后端编译的 climax 视频提示词（无视频模板/模型也生成） */
  videoPrompt?: string;
}

export type IllustrationAspectRatio = "1:1" | "2:3" | "3:2" | "3:4" | "4:3" | "9:16" | "16:9";
export type LatentLongEdge = 1024 | 2048 | 4096;

const BASE_LATENT_SIZES: Record<IllustrationAspectRatio, { width: number; height: number }> = {
  "1:1": { width: 1024, height: 1024 },
  "2:3": { width: 704, height: 1024 },
  "3:2": { width: 1024, height: 704 },
  "3:4": { width: 768, height: 1024 },
  "4:3": { width: 1024, height: 768 },
  "9:16": { width: 576, height: 1024 },
  "16:9": { width: 1024, height: 576 },
};

export function latentSizeFor(
  aspectRatio: IllustrationAspectRatio,
  longestEdge: LatentLongEdge,
): { width: number; height: number } {
  const base = BASE_LATENT_SIZES[aspectRatio] || BASE_LATENT_SIZES["2:3"];
  const edge = longestEdge === 2048 || longestEdge === 4096 ? longestEdge : 1024;
  const scale = edge / 1024;
  return { width: base.width * scale, height: base.height * scale };
}

export function illustrationTemplateValues(
  exposed: readonly SemanticField[], input: IllustrationValues,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of exposed) {
    const key = `${field.node_id}.${field.field}`;
    const binding = workflowFieldBinding(field);
    if (binding === "prompt") values[key] = input.prompt;
    else if (binding === "negative_prompt" && input.negativePrompt) {
      values[key] = input.negativePrompt;
    } else if (binding === "lora_name" && input.loraName) {
      values[key] = input.loraName;
    } else if (binding === "lora_weight" && input.loraName) {
      values[key] = input.loraWeight ?? 0.8;
    } else if (binding === "base_image" && input.baseImage) {
      values[key] = input.baseImage;
    } else if (binding === "video_duration" && input.videoDuration) {
      values[key] = input.videoDuration;
    } else if (binding === "video_camera" && input.videoCamera) {
      values[key] = input.videoCamera;
    } else if (binding === "video_mode" && input.videoMode) {
      values[key] = input.videoMode;
    } else if (binding === "first_frame_desc" && input.firstFrameDesc) {
      values[key] = input.firstFrameDesc;
    } else if (binding === "last_frame_desc" && input.lastFrameDesc) {
      values[key] = input.lastFrameDesc;
    } else if (binding === "prev_tail_desc" && input.prevTailDesc) {
      values[key] = input.prevTailDesc;
    } else if (binding === "last_frame_url" && input.lastFrameUrl) {
      values[key] = input.lastFrameUrl;
    } else if (binding === "first_frame_image" && input.firstFrameImage) {
      values[key] = input.firstFrameImage;
    } else if (binding === "last_frame_image" && input.lastFrameImage) {
      values[key] = input.lastFrameImage;
    } else if (binding === "video_prompt" && input.videoPrompt) {
      values[key] = input.videoPrompt;
    } else if (binding === "latent_width" && input.latentSize) {
      values[key] = input.latentSize.width;
    } else if (binding === "latent_height" && input.latentSize) {
      values[key] = input.latentSize.height;
    }
  }
  return values;
}
