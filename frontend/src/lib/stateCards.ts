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
// 主角/第一人称代词不是剧情角色，不入角色状态表（2026-09-01 用户定案，与后端
// character_state._PROTAGONIST_OWNERS 同源；存量脏数据在此显示层兜底过滤）。
const PROTAGONIST_OWNERS = new Set(["主角", "我", "我们", "你", "您", "玩家", "宿主", "用户", "user", "player"]);

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
    // 主角字段（含「我·所在」等显式归属与「我状态」等旧拼接）整行丢弃
    if (parsed?.owner && PROTAGONIST_OWNERS.has(parsed.owner)) return;
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
