/**
 * 每张场景壁纸的 WE 预设快照（工程属性表 + 用户在 Wallpaper Engine 里的生效值）。
 *
 * 为什么要缓存在这里而不是各自 fetch：壁纸层要拿它当**初值**（「部署该壁纸在 WE 上的
 * 预设」），选项面板要拿它当**控件表**（标签/范围/选项）。两边读的是同一份事实，
 * 属主只有一个；重复请求同一大 JSON 没有意义。
 */
import {
  fetchWallpaperSceneProperties,
  type ScenePropertyItem,
  type ScenePropertyValue,
  type ScenePropertyValues,
} from "../../api/userState";

export interface ScenePresetSnapshot {
  /** 后端已按 order 排好、并剔除广告/WE UI 色的属性表。 */
  items: ScenePropertyItem[];
  /** WE 生效值（工程默认值 + 用户在 Wallpaper Engine 里的覆盖）——预设初值。 */
  base: ScenePropertyValues;
}

const EMPTY: ScenePresetSnapshot = { items: [], base: {} };
const cache = new Map<string, Promise<ScenePresetSnapshot>>();

function toSnapshot(page: Awaited<ReturnType<typeof fetchWallpaperSceneProperties>>): ScenePresetSnapshot {
  const base: ScenePropertyValues = {};
  for (const item of page.properties) base[item.key] = item.value;
  return { items: page.properties, base };
}

/**
 * 读取（并缓存）某个场景来源的预设快照。
 *
 * 失败不缓存：project.json 缺失/后端离线时下一次仍会重试，而不是永久停在空快照。
 */
export function loadScenePresets(
  source: string,
  options: { force?: boolean } = {},
): Promise<ScenePresetSnapshot> {
  if (!source) return Promise.resolve(EMPTY);
  const cached = cache.get(source);
  if (cached && !options.force) return cached;
  const next = fetchWallpaperSceneProperties(source)
    .then(toSnapshot)
    .catch((error: unknown) => {
      cache.delete(source);
      throw error instanceof Error ? error : new Error(String(error));
    });
  cache.set(source, next);
  return next;
}

/** 用户在应用内改过的键覆盖 WE 生效值；其余键保持 Wallpaper Engine 里的样子。 */
export function scenePresetValues(
  base: ScenePropertyValues,
  preset: ScenePropertyValues | undefined,
): ScenePropertyValues {
  return { ...base, ...(preset ?? {}) };
}

// ---- 面板控件与属性表之间的纯换算（组件只做渲染，判定都留在这里，便于单测）----

/**
 * WE 的颜色属性存 `"r g b"`，但**两种刻度**都真实存在：绑到 shader uniform 的 tint 走
 * 0~1 浮点（渲染器按 parseVec3 原样吃），粒子色带 / 预览图色标走 0~255。
 * 判据取「任一分量 > 1 即为 0~255」——0~1 的写法永远不超过 1；`1 1 1` 在两种刻度下
 * 都读作白色，不会误判。回转时按参照值的刻度写回，免得把 0~1 的 tint 存成 255 制。
 */
type SceneColorScale = "unit" | "byte";

interface SceneColor {
  scale: SceneColorScale;
  /** 归一到 0~1 的分量，控件与比较都只认这个。 */
  parts: number[];
}

function parseSceneColor(raw: unknown): SceneColor | null {
  if (Array.isArray(raw)) {
    const numbers = raw.slice(0, 3).map(Number);
    if (numbers.length === 3 && numbers.every((n) => Number.isFinite(n))) {
      return { scale: numbers.some((n) => n > 1) ? "byte" : "unit", parts: numbers.map((n) => clamp01(n > 1 ? n / 255 : n)) };
    }
    return null;
  }
  const text = String(raw ?? "").trim();
  if (!text) return null;
  if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(text)) {
    const hex = text.slice(1);
    const pairs = hex.length === 3
      ? hex.split("").map((c) => c + c)
      : [hex.slice(0, 2), hex.slice(2, 4), hex.slice(4, 6)];
    return { scale: "unit", parts: pairs.map((pair) => parseInt(pair, 16) / 255) };
  }
  const numbers = text.split(/[\s,]+/).map(Number).filter((n) => Number.isFinite(n));
  if (numbers.length < 3) return null;
  const parts = numbers.slice(0, 3);
  const scale: SceneColorScale = parts.some((n) => n > 1) ? "byte" : "unit";
  return { scale, parts: parts.map((n) => clamp01(scale === "byte" ? n / 255 : n)) };
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function toByteString(parts: number[]): string {
  return parts.map((p) => Math.round(clamp01(p) * 255)).join(" ");
}

/** WE 颜色 → 控件用的 hex；认不出来的写法给白色占位，别显示成黑块让人以为预设坏了。 */
export function sceneColorToHex(raw: unknown): string {
  const color = parseSceneColor(raw);
  if (!color) return "#ffffff";
  return `#${color.parts.map((p) => Math.round(p * 255).toString(16).padStart(2, "0")).join("")}`;
}

/** hex → WE 的分量串；`reference` 提供原写法，按它的刻度回写。 */
export function hexToSceneColor(hex: string, reference?: unknown): string {
  const color = parseSceneColor(hex);
  if (!color) return hex;
  const scale: SceneColorScale = parseSceneColor(reference)?.scale ?? color.scale;
  if (scale === "byte") return toByteString(color.parts);
  return color.parts.map((p) => Number(p.toFixed(6))).join(" ");
}

/** 滑杆按 step 的位数收敛，否则拖一格会存下 0.30000000000000004 这种噪声值。 */
export function roundSceneNumber(item: ScenePropertyItem, value: number): number {
  if (!Number.isFinite(value)) return value;
  const digits = Math.min(4, Math.max(0, Math.round(Number(item.precision ?? 3))));
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

/** 与 WE 生效值等价就算「没改过」——类型感知的比较，避免把 "1" 与 1 存成偏离。 */
export function sameScenePropertyValue(
  item: ScenePropertyItem, actual: ScenePropertyValue, base: ScenePropertyValue,
): boolean {
  if (item.type === "color") return sceneColorToHex(actual) === sceneColorToHex(base);
  if (item.type === "slider") return Number(actual) === Number(base);
  if (item.type === "bool") return Boolean(actual) === Boolean(base);
  return String(actual) === String(base);
}

/**
 * 记下「相对 WE 生效值的偏离量」，而不是把整张属性表抄进设置。
 *
 * 只存差集，用户在 Wallpaper Engine 里再改预设时应用这边才会跟着走；改回原样即删除该键，
 * 否则一张全量快照会把 WE 侧的后续调整永久钉死。
 */
export function scenePresetDeviation(
  deviations: ScenePropertyValues,
  item: ScenePropertyItem,
  next: ScenePropertyValue,
  base: ScenePropertyValues,
): ScenePropertyValues {
  const merged: ScenePropertyValues = { ...deviations };
  const original = base[item.key];
  if (original !== undefined && sameScenePropertyValue(item, next, original)) delete merged[item.key];
  else merged[item.key] = next;
  return merged;
}

const FALSEISH = new Set(["", "0", "false", "off", "no"]);

function conditionTruthy(value: ScenePropertyValue | undefined): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === "boolean") return value;
  const num = Number(value);
  if (value !== "" && Number.isFinite(num)) return num !== 0;
  return !FALSEISH.has(String(value).trim().toLowerCase());
}

function literalCompare(left: ScenePropertyValue | undefined, rawRight: string): boolean {
  const right = rawRight.trim().replace(/^['"]|['"]$/g, "");
  const num = Number(left);
  const rightNum = Number(right);
  if (Number.isFinite(num) && Number.isFinite(rightNum)) return num === rightNum;
  return String(left ?? "").toLowerCase() === right.toLowerCase();
}

/**
 * WE 用 `condition` 决定一条预设是否在面板里出现（子选项挂在开关下面）。
 * 只实现能安全解析的写法：`key.value`、`key.value == 字面量`、`key.value != 字面量`。
 * 解析不了（`&&` / `||` / 括号混合）**照常显示**——多摆一个能用的控件，远好过把
 * 用户想要的开关悄悄藏起来。
 */
export function scenePropertyVisible(
  item: Pick<ScenePropertyItem, "condition">,
  values: ScenePropertyValues,
): boolean {
  const expr = (item.condition || "").trim();
  if (!expr) return true;
  const compare = /^([\w.-]+)\.value\s*(==|!=)\s*(.+)$/.exec(expr);
  if (compare) {
    const equal = literalCompare(values[compare[1]], compare[3]);
    return compare[2] === "==" ? equal : !equal;
  }
  const ref = /^([\w.-]+)\.value$/.exec(expr);
  if (ref) return conditionTruthy(values[ref[1]]);
  return true;
}

/**
 * 面板真正要摆的控件：先按渲染器登记的有效键（`userBindings`）收窄，再套 WE 的条件显示。
 * bindings 为 null（清单还没解出来）时不给控件——那时连改了会不会生效都不知道。
 */
export function filterScenePresetItems(
  items: ScenePropertyItem[],
  bindings: string[] | null,
  values: ScenePropertyValues,
): ScenePropertyItem[] {
  if (!bindings) return [];
  const effective = new Set(bindings);
  return items.filter((item) => effective.has(item.key) && scenePropertyVisible(item, values));
}
