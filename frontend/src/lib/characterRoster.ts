// 角色名册覆盖（2026-11-10）：合集卡的聊天成员/多元数据插入角色来自世界书条目，
// 自动识别可能漏/多；这里定义仓库级覆盖结构与纯函数，UI 只负责编辑。
import type { CharacterRosterResult } from "../api/ai/agent";

export interface CharacterRosterOverrides {
  /** auto=自动识别（默认）；cards=只用绑定角色卡；worldbook=只用世界书名册。 */
  mode?: "auto" | "cards" | "worldbook";
  /** 手动补进名册的条目 uid（自动判据漏掉的角色）。 */
  include?: string[];
  /** 从名册排除的条目 uid（误识别）。 */
  exclude?: string[];
  /** 显示顺序（uid 列表；未列出的按条目顺序排在后面）。 */
  order?: string[];
  /** 改名：uid → 展示名。 */
  rename?: Record<string, string>;
}

export const EMPTY_ROSTER_OVERRIDES: CharacterRosterOverrides = {};

/** 宽松解析仓库记录里的覆盖；非法结构一律回落默认。 */
export function parseRosterOverrides(raw: unknown): CharacterRosterOverrides {
  const data = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const mode = data.mode === "cards" || data.mode === "worldbook" ? data.mode : "auto";
  const list = (value: unknown): string[] => (
    Array.isArray(value)
      ? [...new Set(value.map((item) => String(item || "").trim()).filter(Boolean))]
      : []
  );
  const renameRaw = data.rename && typeof data.rename === "object"
    ? data.rename as Record<string, unknown> : {};
  const rename: Record<string, string> = {};
  for (const [uid, name] of Object.entries(renameRaw)) {
    const key = String(uid || "").trim();
    const value = String(name || "").trim();
    if (key && value) rename[key] = value.slice(0, 32);
  }
  return {
    mode,
    include: list(data.include),
    exclude: list(data.exclude),
    order: list(data.order),
    rename,
  };
}

/** 名册是否可视为「合集卡/世界书角色玩法」。 */
export function isWorldbookRoster(roster: CharacterRosterResult | null | undefined): boolean {
  return roster?.source === "worldbook" && (roster.members?.length || 0) > 0;
}

/** 当前应显示/可选的角色名：世界书名册优先，回退绑定角色卡。 */
export function rosterNamesOf(
  roster: CharacterRosterResult | null | undefined,
  fallback: readonly string[],
): string[] {
  if (isWorldbookRoster(roster)) return [...roster!.members];
  return [...new Set(fallback.map((name) => name.trim()).filter(Boolean))];
}

function commonPrefix(a: string, b: string): number {
  const limit = Math.min(a.length, b.length);
  let index = 0;
  while (index < limit && a[index] === b[index]) index += 1;
  return index;
}

/**
 * 角色绑定名匹配（2026-11-10）：世界书条目常用全名（腓特烈大帝 / 奥古斯特·冯·帕塞瓦尔），
 * 多元数据插入里用户可能只填短名（腓特烈 / 奥古斯特）。精确命中优先；否则按「一方包含另一方」
 * 取最长公共前缀、长度差最小者；出现同分多义则不猜（避免给错头像/底图）。
 */
export function findBindingKey(name: string, keys: readonly string[]): string {
  const target = (name || "").trim();
  if (!target) return "";
  if (keys.includes(target)) return target;
  let best = "";
  let bestScore = Number.NEGATIVE_INFINITY;
  let ambiguous = false;
  for (const raw of keys) {
    const key = (raw || "").trim();
    if (key.length < 2 || target.length < 2) continue;
    const contains = target.startsWith(key) || key.startsWith(target)
      || target.includes(key) || key.includes(target);
    if (!contains) continue;
    const score = commonPrefix(target, key) * 10 - Math.abs(target.length - key.length);
    if (score > bestScore) {
      bestScore = score;
      best = raw;
      ambiguous = false;
    } else if (score === bestScore) {
      ambiguous = true;
    }
  }
  return ambiguous ? "" : best;
}
