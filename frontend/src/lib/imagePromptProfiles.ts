export type PromptProfileId = "krea2" | "anima_tags" | "natural_language" | "niji_sections";

export const PROMPT_PROFILE_OPTIONS: readonly { id: PromptProfileId; label: string }[] = [
  { id: "krea2", label: "Krea2（自动判断 SFW / NSFW）" },
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
  const qualitySource = fixedQuality.trim() || lines[0] || "";
  const quality = splitQualityTags(qualitySource)
    .filter((tag) => rating === "nsfw" || !/^(?:sensitive|explicit)$/i.test(tag.trim()))
    .join(", ");
  const contentLines = lines.length > 1 ? lines.slice(1) : lines;
  const content = contentLines
    .map((line) => line.trim())
    .filter((line) => line && /^[\x20-\x7E\t]+$/.test(line))
    .join(" ");
  if (quality && content) return `${quality}\n${content}`;
  return quality || content;
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
    } else if (binding === "latent_width" && input.latentSize) {
      values[key] = input.latentSize.width;
    } else if (binding === "latent_height" && input.latentSize) {
      values[key] = input.latentSize.height;
    }
  }
  return values;
}
