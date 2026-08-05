import { apiGet, apiPost, apiUrl } from "./client";
import type { RegexScript } from "../lib/regexEngine";

export interface PresetSummary {
  name: string;
  file: string;
  prompts: number;
  enabled: number;
}

export interface PresetConflict {
  reason: "exists";
  name: string;
}

// ST 预设片段（只取展示/编辑需要的字段）
// injection_position: 0=相对（按 prompt_order 顺序）/1=聊天内@深度；injection_depth 为深度值。
// injection_trigger: 生成类型筛选（normal/continue/impersonate/swipe/regenerate/quiet），空=全部。
export interface PresetPrompt {
  identifier: string;
  name?: string;
  role?: string;
  content?: string;
  marker?: boolean;
  injection_position?: number;
  injection_depth?: number;
  injection_trigger?: string[];
}
export interface PresetOrderEntry {
  identifier: string;
  enabled: boolean;
}
// 思维链（条件推理链）：按真状态 scene/affinity/turn 命中则注入，驱动剧情推理质量。
// position: tail(默认,落历史后·遵守最严) / head(随 system 头·框定框架)。
export interface ChainWhen {
  scene?: string;        // dialogue/action/emotion/conflict/nsfw/climax，空=不限
  affinity_lt?: number;  // 好感度 < 阈值
  affinity_gt?: number;  // 好感度 > 阈值
  turn_mod?: [number, number]; // [n,r] → turn%n==r 周期触发
}
export interface ThinkingChain {
  name?: string;
  content?: string;
  position?: "tail" | "head";
  when?: ChainWhen;
}
export interface PresetData {
  prompts: PresetPrompt[];
  prompt_order: { character_id?: number; order: PresetOrderEntry[] }[];
  thinking_chains?: ThinkingChain[];
  [k: string]: unknown;
}

export function listPresets(base: string) {
  return apiGet<{ items: PresetSummary[] }>(`/preset/?base=${encodeURIComponent(base)}`);
}

export async function importPreset(
  file: File, base: string, overwrite = false, name = "",
): Promise<PresetSummary & { ok: boolean }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("base", base);
  fd.append("overwrite", String(overwrite));
  if (name) fd.append("name", name);
  const resp = await fetch(apiUrl("/preset/import"), { method: "POST", body: fd });
  if (resp.status === 409) {
    const body = await resp.json().catch(() => ({}));
    const conflict = (body?.detail ?? body) as PresetConflict;
    throw Object.assign(new Error(`已存在同名预设「${conflict.name}」`), { conflict });
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(typeof body?.detail === "string" ? body.detail : `导入失败: ${resp.status}`);
  }
  return resp.json();
}

export function presetDetail(base: string, name: string) {
  return apiGet<{ name: string; preset: PresetData }>(
    `/preset/detail?base=${encodeURIComponent(base)}&name=${encodeURIComponent(name)}`,
  );
}

export function savePreset(base: string, name: string, preset: PresetData) {
  return apiPost<{ ok: boolean }>("/preset/save", { base, name, preset });
}

export function deletePreset(base: string, name: string) {
  return apiPost<{ ok: boolean }>("/preset/delete", { base, name });
}

// 预设级正则（存预设 JSON 的 regexScripts 键，仅该预设激活时生效）
export function getPresetRegex(base: string, name: string) {
  return apiGet<{ items: RegexScript[] }>(
    `/preset/regex?base=${encodeURIComponent(base)}&name=${encodeURIComponent(name)}`,
  );
}

export function savePresetRegex(base: string, name: string, scripts: RegexScript[]) {
  return apiPost<{ ok: boolean; items: RegexScript[] }>("/preset/regex", { base, name, scripts });
}
