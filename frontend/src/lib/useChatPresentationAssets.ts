import { useEffect, useState } from "react";
import { avatarUrl, characterMedia, characterRegex, expressionUrl } from "../api/characters";
import { getPresetRegex } from "../api/preset";
import { listRegex } from "../api/regex";
import type { CharacterPortrait } from "./characterPortrait";
import type { RegexScript } from "./regexEngine";

export function mergeDisplayRegexSources(
  cardItems: RegexScript[], presetItems: RegexScript[], globalItems: RegexScript[],
) {
  return [...globalItems, ...presetItems, ...cardItems]
    .filter((item) => item.markdownOnly && !item.disabled);
}

type RegexSourceLoaders = {
  global: () => Promise<RegexScript[]>;
  preset: (base: string, name: string) => Promise<RegexScript[]>;
  card: (base: string, name: string) => Promise<RegexScript[]>;
};

const regexSourceLoaders: RegexSourceLoaders = {
  global: () => listRegex().then((result) => result.items || []),
  preset: (base, name) => getPresetRegex(base, name)
    .then((result) => (result.items || []) as RegexScript[]),
  card: (base, name) => characterRegex(base, name)
    .then((result) => (result.items || []) as unknown as RegexScript[]),
};

export async function loadDisplayRegexSources(
  cardNames: string[], characterDir: string, presetDir: string, presetName: string,
  loaders: RegexSourceLoaders = regexSourceLoaders,
) {
  const global = loaders.global().catch(() => [] as RegexScript[]);
  const preset = presetDir && presetName
    ? loaders.preset(presetDir, presetName).catch(() => [] as RegexScript[])
    : Promise.resolve([] as RegexScript[]);
  const cards = characterDir
    ? Promise.all(cardNames.map((name) => loaders.card(characterDir, name).catch(() => [])))
      .then((groups) => groups.flat())
    : Promise.resolve([] as RegexScript[]);
  const [cardItems, presetItems, globalItems] = await Promise.all([cards, preset, global]);
  return mergeDisplayRegexSources(cardItems, presetItems, globalItems);
}

export function useChatPresentationAssets(
  cardNames: string[], characterDir: string, outputDir: string, repoId: string,
  presetDir: string, presetName: string,
) {
  const [displayRegex, setDisplayRegex] = useState<RegexScript[]>([]);
  const [characterPortraits, setCharacterPortraits] = useState<Record<string, CharacterPortrait>>({});
  const cardKey = cardNames.join("\u0000");

  useEffect(() => {
    let alive = true;
    loadDisplayRegexSources(cardNames, characterDir, presetDir, presetName)
      .then((items) => { if (alive) setDisplayRegex(items); })
      .catch(() => { if (alive) setDisplayRegex([]); });
    return () => { alive = false; };
  }, [cardKey, characterDir, presetDir, presetName]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let alive = true;
    if (!characterDir || !cardNames.length) { setCharacterPortraits({}); return; }
    Promise.all(cardNames.map(async (name) => {
      const media = await characterMedia(characterDir, name, outputDir, repoId);
      return [name, {
        name,
        avatar: media.has_avatar ? avatarUrl(media.base || characterDir, media.folder) : undefined,
        expressions: Object.fromEntries(media.expressions.map((item) => [
          item.name, expressionUrl(media.base || characterDir, media.folder, item.file),
        ])),
      } satisfies CharacterPortrait] as const;
    })).then((items) => { if (alive) setCharacterPortraits(Object.fromEntries(items)); })
      .catch(() => { if (alive) setCharacterPortraits({}); });
    return () => { alive = false; };
  }, [cardKey, characterDir, outputDir, repoId]); // eslint-disable-line react-hooks/exhaustive-deps

  return { displayRegex, characterPortraits };
}
