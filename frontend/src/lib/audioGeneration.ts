import type { ExposedField } from "../api/workflows";
import {
  SEMANTIC_VOICE_TEXT, SEMANTIC_VOICE_REFERENCE, VOICE_EMOTION_PREFIX,
  type VoiceEmotionKey,
} from "../api/workflows";
import type { CharacterVoiceBinding, MediaInsertPreset } from "../stores/settings";
import { workflowFieldBinding } from "./imagePromptProfiles";

export interface AudioLineInput {
  speaker: string;
  text: string;
  emotion?: Record<string, number>;
}

export interface AudioTemplateInput {
  text: string;
  reference: string;                 // ComfyUI input 目录文件名（LoadAudio 引用）
  emotion?: Record<string, number>;
}

/** 按 exposed 语义绑定组装音频模板 values（voice_text / voice_reference / voice_emotion_<key>）。 */
export function audioTemplateValues(
  exposed: readonly ExposedField[], input: AudioTemplateInput,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of exposed) {
    const key = `${field.node_id}.${field.field}`;
    const binding = workflowFieldBinding(field);
    if (binding === SEMANTIC_VOICE_TEXT) {
      values[key] = input.text;
    } else if (binding === SEMANTIC_VOICE_REFERENCE) {
      values[key] = input.reference;
    } else if (binding.startsWith(VOICE_EMOTION_PREFIX)) {
      const emoKey = binding.slice(VOICE_EMOTION_PREFIX.length) as VoiceEmotionKey;
      const value = input.emotion?.[emoKey];
      if (typeof value === "number") values[key] = value;
    }
  }
  return values;
}

/** 按角色名查参考音轨本地路径；无绑定返回 undefined（该角色跳过配音）。 */
export function voiceReferenceFor(
  preset: MediaInsertPreset | undefined, speaker: string,
): string | undefined {
  const binding: CharacterVoiceBinding | undefined = preset?.characterVoices?.[speaker];
  return binding?.voiceRef || undefined;
}

/** 从 audio_request 的台词行过滤出「已配置音轨」的角色，返回可配音行（未配置的跳过）。 */
export function resolvableAudioLines(
  preset: MediaInsertPreset | undefined, lines: readonly AudioLineInput[],
): AudioLineInput[] {
  return lines.filter((line) => line.speaker && line.text && voiceReferenceFor(preset, line.speaker));
}

/** 台词中有台词但未配置参考音轨的角色（去重保序）——用于「已跳过配音」提示。 */
export function skippedAudioSpeakers(
  preset: MediaInsertPreset | undefined, lines: readonly AudioLineInput[],
): string[] {
  const seen = new Set<string>();
  const skipped: string[] = [];
  for (const line of lines) {
    if (!line.speaker || !line.text) continue;
    if (voiceReferenceFor(preset, line.speaker)) continue;
    if (seen.has(line.speaker)) continue;
    seen.add(line.speaker);
    skipped.push(line.speaker);
  }
  return skipped;
}
