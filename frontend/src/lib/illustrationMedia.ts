import type { MediaInsertPreset } from "../stores/settings";

export function illustrationRequestMedia(preset: MediaInsertPreset | undefined, cardNames: string[]) {
  const useCharacterCards = preset?.appearanceSource === "character_card";
  const characterLoras = useCharacterCards ? {} : preset?.characterLoras || {};
  return {
    characterBaseImages: Object.fromEntries(
      Object.entries(characterLoras)
        .filter(([, binding]) => binding.baseImage)
        .map(([name, binding]) => [name, binding.baseImage as string]),
    ),
    illustrationActorNames: [...new Set([...Object.keys(characterLoras), ...cardNames])],
    styleBaseImage: useCharacterCards ? "" : preset?.styleBaseImage || "",
  };
}

export function illustrationWorkflowMedia(
  preset: MediaInsertPreset, actors: string[], cardNames: string[],
) {
  const useCharacterCards = preset.appearanceSource === "character_card";
  const characterLoras = useCharacterCards ? {} : preset.characterLoras || {};
  let loraName = "";
  let loraWeight = 0.8;
  let baseImage = "";
  let characterLora = false;
  for (const name of [...actors, ...cardNames]) {
    const binding = characterLoras[name];
    if (!binding) continue;
    if (binding.loraName) {
      loraName = binding.loraName;
      loraWeight = binding.loraWeight ?? 0.8;
      characterLora = true;
    }
    if (binding.baseImage) baseImage = binding.baseImage;
    if (loraName || baseImage) break;
  }
  if (!loraName) {
    loraName = preset.styleLora || preset.loraName || "";
    loraWeight = preset.styleLoraWeight ?? preset.loraWeight ?? 0.8;
  }
  if (!useCharacterCards && !baseImage) baseImage = preset.styleBaseImage || "";
  return { loraName, loraWeight, baseImage, characterLora };
}
