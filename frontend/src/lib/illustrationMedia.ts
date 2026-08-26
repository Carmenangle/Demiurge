import type { MediaInsertPreset } from "../stores/settings";

export interface IllustrationLora {
  name: string;
  weight: number;
  character: boolean;
}

export function resolveIllustrationActors(
  eventActors: readonly string[],
  subjects: readonly { name?: string; description?: string; weight?: number }[] | undefined,
  knownNames: readonly string[],
): string[] {
  const known = new Set(knownNames.map((name) => name.trim()).filter(Boolean));
  return [...new Set([
    ...eventActors,
    ...(subjects || []).map((subject) => subject.name || ""),
  ].map((name) => name.trim()).filter((name) => name && known.has(name)))];
}

export function illustrationLoraConfigurationError(
  preset: MediaInsertPreset,
  media: { loras: IllustrationLora[]; characterLora: boolean },
): string {
  if (preset.appearanceSource === "character_card" || preset.loraMode === "none") return "";
  const mode = preset.loraMode || "single";
  const styleName = preset.styleLora || preset.loraName || "";
  if (mode === "multi" && !styleName) return "多 LoRA 模式尚未配置默认风格 LoRA";
  if (mode === "single" && !media.characterLora && media.loras.length === 0) {
    return "当前场景未命中角色 LoRA，且尚未配置兜底风格 LoRA";
  }
  return "";
}

export function illustrationRequestMedia(preset: MediaInsertPreset | undefined, cardNames: string[]) {
  const useCharacterCards = preset?.appearanceSource === "character_card";
  const characterLoras = useCharacterCards ? {} : preset?.characterLoras || {};
  return {
    characterBaseImages: Object.fromEntries(
      Object.entries(characterLoras)
        .filter(([, binding]) => binding.baseImage)
        .map(([name, binding]) => [name, binding.baseImage as string]),
    ),
    illustrationActorNames: [...new Set(
      useCharacterCards ? cardNames : Object.keys(characterLoras),
    )],
    styleBaseImage: useCharacterCards ? "" : preset?.styleBaseImage || "",
  };
}

export function illustrationWorkflowMedia(
  preset: MediaInsertPreset, actors: string[], cardNames: string[],
) {
  const useCharacterCards = preset.appearanceSource === "character_card";
  const characterLoras = useCharacterCards ? {} : preset.characterLoras || {};
  const mode = preset.loraMode || "single";
  const candidates = actors.length ? [...new Set(actors)] : cardNames.slice(0, 1);
  let baseImage = "";
  for (const name of candidates) {
    const binding = characterLoras[name];
    if (!binding) continue;
    if (binding.baseImage) { baseImage = binding.baseImage; break; }
  }
  const styleName = preset.styleLora || preset.loraName || "";
  const styleWeight = preset.styleLoraWeight ?? preset.loraWeight ?? 0.8;
  const loras: IllustrationLora[] = [];
  const addLora = (item: IllustrationLora) => {
    if (item.name && !loras.some((existing) => existing.name === item.name)) loras.push(item);
  };
  if (mode !== "none") {
    if (mode === "multi" && styleName) {
      addLora({ name: styleName, weight: styleWeight, character: false });
    }
    for (const name of candidates) {
      const binding = characterLoras[name];
      if (!binding?.loraName) continue;
      addLora({
        name: binding.loraName,
        weight: binding.loraWeight ?? 0.8,
        character: true,
      });
    }
    if (mode === "single" && loras.length === 0 && styleName) {
      addLora({ name: styleName, weight: styleWeight, character: false });
    }
  }
  if (!useCharacterCards && !baseImage) baseImage = preset.styleBaseImage || "";
  const primary = loras[0];
  return {
    loras,
    loraName: primary?.name || "",
    loraWeight: primary?.weight ?? 0.8,
    baseImage,
    characterLora: loras.some((lora) => lora.character),
  };
}

export type VideoMode = "climax" | "firstlast";

/** V1.5/B1 视频模式决策：事件 videoMode 优先，其次 preset.videoMode，缺省 climax（旧预设兼容）。 */
export function resolveVideoMode(
  preset: { videoMode?: VideoMode } | undefined,
  eventVideoMode?: string,
): VideoMode {
  if (eventVideoMode === "climax" || eventVideoMode === "firstlast") return eventVideoMode;
  if (preset?.videoMode === "climax" || preset?.videoMode === "firstlast") {
    return preset.videoMode;
  }
  return "climax";
}
