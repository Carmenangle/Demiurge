import { useEffect, useState } from "react";
import { avatarUrl, characterMedia, characterRegex, expressionUrl } from "../api/characters";
import { listRegex } from "../api/regex";
import type { CharacterPortrait } from "./characterPortrait";
import type { RegexScript } from "./regexEngine";

export function useChatPresentationAssets(
  cardNames: string[], characterDir: string, outputDir: string, repoId: string,
) {
  const [displayRegex, setDisplayRegex] = useState<RegexScript[]>([]);
  const [characterPortraits, setCharacterPortraits] = useState<Record<string, CharacterPortrait>>({});
  const cardKey = cardNames.join("\u0000");

  useEffect(() => {
    const onlyDisplay = (items: RegexScript[]) => items.filter((item) => item.markdownOnly && !item.disabled);
    const global = listRegex().then((result) => result.items || []).catch(() => [] as RegexScript[]);
    const cards = characterDir
      ? Promise.all(cardNames.map((name) => characterRegex(characterDir, name)
        .then((result) => (result.items || []) as unknown as RegexScript[]).catch(() => [])))
        .then((groups) => groups.flat())
      : Promise.resolve([] as RegexScript[]);
    let alive = true;
    Promise.all([cards, global]).then(([cardItems, globalItems]) => {
      if (alive) setDisplayRegex(onlyDisplay([...cardItems, ...globalItems]));
    });
    return () => { alive = false; };
  }, [cardKey, characterDir]); // eslint-disable-line react-hooks/exhaustive-deps

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
