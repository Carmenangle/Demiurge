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

export interface StateCardGrouping {
  cards: StateCard[];
  /** 无角色归属、因此**不进角色状态表**的字段路径（仍留在 state.json 与【当前状态】注入里）。 */
  unowned: string[];
}

/**
 * 把 `state.数值`/`state.叙事` 按**角色归属**分卡 —— 角色状态表只放「属于某个角色」的字段。
 *
 * 无归属裸键（旧拼接残留/关系类字段，如「师徒名分」）的归属规则（2026-09-23 用户定案）：
 * ① 恰好一个显式属主 ⇒ 继承该属主（单角色/单主角存档的旧格式兼容，与后端
 *    `character_state._owner_hints` 同源）；
 * ② 否则**不入表**：`card_name`（合集/世界书作品名）与「全局状态」都不是角色，给它们造一张卡
 *    就是把非角色字段塞进角色表（2026-09-23 用户实锤：「这是角色状态表，不是放这种东西的」）。
 *    裸键不删，只不进本表（路径见 `unowned`，UI 另起一行说明）。
 * ⛔ 禁止回退 `state.card_name`：后端 `character_state._canonical_leaf`（2026-09-01
 *    「合集卡名不是角色名」）与 `agent_agency` 的世界书 `card_name` 剔除同源判例。
 */
export function groupStateCardsDetailed(state: CharacterStateDto): StateCardGrouping {
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
  // 唯一显式属主 ⇒ 无归属裸键继承它（旧格式兼容）；否则裸键没有可归属的角色 ⇒ 不进表。
  const soleOwner = resolvedOwners.size === 1 ? [...resolvedOwners][0] : "";
  const cards = new Map<string, StateCardField[]>();
  const unowned: string[] = [];
  raw.forEach(({ parsed, field }) => {
    const name = parsed?.owner
      ? (parsed.explicit ? parsed.owner : resolveLegacyOwner(parsed.owner))
      : soleOwner;
    if (!name) { unowned.push(field.path); return; }
    const qualifier = parsed && !parsed.explicit && name !== parsed.owner
      ? parsed.owner.slice(name.length) : "";
    const normalized = qualifier && parsed?.label === "角色状态"
      ? { ...field, label: `${qualifier}状态` } : field;
    cards.set(name, [...(cards.get(name) || []), normalized]);
  });
  return {
    cards: [...cards.entries()].map(([name, fields]) => ({
      name,
      fields: fields.sort((a, b) => Number(b.label === "好感度") - Number(a.label === "好感度")),
    })),
    unowned,
  };
}

/** 只要角色卡（未归属字段路径见 `groupStateCardsDetailed`）。 */
export function groupStateCards(state: CharacterStateDto): StateCard[] {
  return groupStateCardsDetailed(state).cards;
}
