import type { MediaInsertPreset } from "../stores/settings";

export interface IllustrationLora {
  name: string;
  weight: number;
  character: boolean;
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
      if (mode === "single") break;
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
