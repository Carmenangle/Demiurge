import type { CharacterStateDto, NarrativeField, NumericField } from "../api/state";

export interface StateCardField {
  kind: "number" | "text";
  label: string;
  path: string;
  value: number | string;
  min?: number;
  max?: number;
  evidence: string;
  source: string;
}

export interface StateCard {
  name: string;
  fields: StateCardField[];
}

const SUFFIXES = ["好感度", "角色状态", "当前状态", "状态", "态度", "心情", "所在地点", "所在"];
const LEGACY_STATE_QUALIFIERS = new Set(["身体", "精神", "生理", "心理", "外观", "伤势", "衣着"]);

function splitLeaf(leaf: string): { owner: string; label: string; explicit: boolean } | null {
  const separated = leaf.match(/^(.+?)[·/：:](.+)$/);
  if (separated) return { owner: separated[1].trim(), label: separated[2].trim(), explicit: true };
  for (const suffix of SUFFIXES) {
    if (leaf.endsWith(suffix) && leaf.length > suffix.length) {
      return {
        owner: leaf.slice(0, -suffix.length).trim(),
        label: suffix === "状态" ? "角色状态" : suffix,
        explicit: false,
      };
    }
  }
  return null;
}

export function groupStateCards(state: CharacterStateDto): StateCard[] {
  const raw: { leaf: string; field: StateCardField; parsed: ReturnType<typeof splitLeaf> }[] = [];
  const add = (leaf: string, field: StateCardField) => {
    const parsed = splitLeaf(leaf);
    raw.push({ leaf, field: { ...field, label: parsed?.label || field.label }, parsed });
  };

  Object.entries(state.数值).forEach(([leaf, f]: [string, NumericField]) => add(leaf, {
    kind: "number", label: leaf, path: `数值/${leaf}`, value: f.value,
    min: f.min, max: f.max, evidence: f.evidence, source: f.source,
  }));
  Object.entries(state.叙事).forEach(([leaf, f]: [string, NarrativeField]) => add(leaf, {
    kind: "text", label: leaf, path: `叙事/${leaf}`, value: f.value,
    evidence: f.evidence, source: f.source,
  }));

  const candidates = raw.flatMap(({ parsed }) => parsed?.owner ? [parsed.owner] : []);
  const resolveLegacyOwner = (owner: string) => {
    const root = candidates
      .filter((candidate) => candidate !== owner && owner.startsWith(candidate))
      .filter((candidate) => LEGACY_STATE_QUALIFIERS.has(owner.slice(candidate.length)))
      .sort((a, b) => b.length - a.length)[0];
    return root || owner;
  };
  const resolvedOwners = new Set(raw.flatMap(({ parsed }) => parsed?.owner
    ? [parsed.explicit ? parsed.owner : resolveLegacyOwner(parsed.owner)] : []));
  const fallback = resolvedOwners.size === 1 ? [...resolvedOwners][0] : (state.card_name || "全局状态");
  const cards = new Map<string, StateCardField[]>();
  raw.forEach(({ parsed, field }) => {
    const name = parsed?.owner
      ? (parsed.explicit ? parsed.owner : resolveLegacyOwner(parsed.owner))
      : fallback;
    const qualifier = parsed && !parsed.explicit && name !== parsed.owner
      ? parsed.owner.slice(name.length) : "";
    const normalized = qualifier && parsed?.label === "角色状态"
      ? { ...field, label: `${qualifier}状态` } : field;
    cards.set(name, [...(cards.get(name) || []), normalized]);
  });
  return [...cards.entries()].map(([name, fields]) => ({
    name,
    fields: fields.sort((a, b) => Number(b.label === "好感度") - Number(a.label === "好感度")),
  }));
}
