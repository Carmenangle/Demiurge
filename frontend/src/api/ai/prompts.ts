// 提示词与文本生成（原 api/ai.ts 提示词段，2026-10-24 按资源域拆分）：
// 用设置里的对话模型产文本——出图提示词 / 反推描述 / 翻译 / 能力描述 / Profile 编译 / 关键词切分。
import { apiGet, apiPost } from "../client";
import type { IllustrationSceneSpec } from "../chatStreamProtocol";
import { chatBody, type Chat } from "./wire";

export interface GenPromptResult {
  prompt: string;
  negative_prompt?: string;
  strategy?: "direct" | "repaired" | "fallback";
  validation_errors?: string[];
}

// 根据场景描述生成出图提示词（用设置里的对话模型）
export function genPrompt(
  scene: string,
  chat: Chat,
) {
  return apiPost<GenPromptResult>("/ai/prompt", {
    scene,
    ...chatBody(chat),
  });
}

// 反推：看图生成提示词（/r）。需视觉模型，复用对话模型配置。
export function describeImage(
  images: string[],
  chat: Chat,
  hint = "",
) {
  return apiPost<GenPromptResult>("/ai/describe-image", {
    images,
    hint,
    ...chatBody(chat),
  });
}

// 翻译（可选润色），用于模型介绍的多语言翻译
export function translateText(
  text: string,
  targetLang: string,
  chat: Chat,
  polish = false,
) {
  return apiPost<{ text: string }>("/ai/translate", {
    text,
    target_lang: targetLang,
    polish,
    ...chatBody(chat),
  });
}

export interface DescribeResult {
  description: string;
}

// 根据工作流节点结构 AI 生成一句能力描述（模板描述弹窗）
export function describeWorkflow(
  name: string,
  nodes: { id: string; type: string; title: string }[],
  chat: Chat,
) {
  return apiPost<DescribeResult>("/ai/describe-workflow", {
    name,
    nodes,
    ...chatBody(chat),
  });
}

// 基于已输入的能力描述文本润色，使其更便于 AI 理解
export function polishDescription(
  text: string,
  chat: Chat,
) {
  return apiPost<DescribeResult>("/ai/polish-description", {
    text,
    ...chatBody(chat),
  });
}

export function genProfilePrompt(
  profile: string,
  scene: IllustrationSceneSpec,
  chat: Chat,
  preset: { presetDir?: string; presetName?: string; userName?: string } = {},
) {
  return apiPost<GenPromptResult & { profile: string }>("/ai/prompt/profile", {
    profile,
    scene,
    preset_dir: preset.presetDir || "",
    preset_name: preset.presetName || "",
    user_name: preset.userName || "",
    ...chatBody(chat),
  }, 120_000);
}

export interface FramePromptItem {
  prompt: string;
  negative_prompt: string;
  strategy: string;
  validation_errors: string[];
  field_ledger: Record<string, unknown>;
}

// 首尾帧提示词：与高潮点同构——后端先做时点提取（首/尾各自英文结构化 action/visual_facts），
// 再逐帧走同一 Profile 编译器，一次调用出两帧成品。
export function genFramePrompts(
  profile: string,
  scene: IllustrationSceneSpec,
  frames: { first?: string; last?: string },
  chat: Chat,
  preset: { presetDir?: string; presetName?: string; userName?: string } = {},
) {
  return apiPost<{ frames: Partial<Record<"first" | "last", FramePromptItem>>; profile: string }>(
    "/ai/prompt/profile/frames",
    {
      profile,
      scene,
      frames: { first: frames.first || "", last: frames.last || "" },
      preset_dir: preset.presetDir || "",
      preset_name: preset.presetName || "",
      user_name: preset.userName || "",
      ...chatBody(chat),
    },
    240_000,
  );
}

export function getProfilePromptDefaults(profile: string, rating = "nsfw") {
  return apiGet<{ quality_prompt: string; negative_prompt: string }>(
    `/ai/prompt/profile/defaults?profile=${encodeURIComponent(profile)}&rating=${encodeURIComponent(rating)}`,
  );
}

// 把提示词轻量切分成关键词标签（纯文本，非反推，省 token）
export function extractKeywords(
  text: string,
  chat: { baseUrl: string; apiKey: string; modelName: string },
) {
  return apiPost<{ tags: string[] }>("/ai/extract-keywords", {
    text, ...chatBody(chat),
  });
}
