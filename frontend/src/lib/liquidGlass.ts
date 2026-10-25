import type { LiquidGlassSettings } from "../stores/settings";

/** 控制台「刷新壁纸」通过该事件让壁纸层重载源文件。 */
export const LIQUID_GLASS_REFRESH_EVENT = "demiurge:liquid-glass-refresh";

interface Rgb {
  r: number;
  g: number;
  b: number;
}

export function hexToRgb(hex: string): Rgb | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const raw = m[1].length === 3
    ? m[1].split("").map((c) => c + c).join("")
    : m[1];
  return {
    r: parseInt(raw.slice(0, 2), 16),
    g: parseInt(raw.slice(2, 4), 16),
    b: parseInt(raw.slice(4, 6), 16),
  };
}

export function rgba(hex: string, alpha: number): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return `rgba(255, 255, 255, ${alpha})`;
  const a = Math.max(0, Math.min(1, alpha));
  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${a.toFixed(3)})`;
}

/** 主色上的文字色：亮主色用深色字，暗主色用浅色字。 */
export function accentTextColor(hex: string): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return "#081020";
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * channel(rgb.r) + 0.7152 * channel(rgb.g) + 0.0722 * channel(rgb.b);
  return luminance > 0.42 ? "#07101f" : "#f7faff";
}

/** 玻璃透明度（0~60，越大越透）→ 底色不透明度。 */
export function glassOpacity(alpha: number): number {
  return Math.max(0.02, 0.18 - (alpha / 60) * 0.16);
}

/** 侧栏透明度（0~200，越大越透）→ 底色不透明度。 */
export function sidebarOpacity(alpha: number): number {
  return Math.max(0.01, 0.22 - (alpha / 200) * 0.21);
}

/** 内容面透明度（0~80，越大越透）→ 底色不透明度，下限 20%。 */
export function contentOpacity(alpha: number): number {
  return Math.max(0.2, 1 - alpha / 100);
}

export function borderOpacity(strength: number): number {
  return 0.04 + (Math.max(0, Math.min(100, strength)) / 100) * 0.48;
}

export function wallpaperFilter(lg: LiquidGlassSettings): string {
  return `blur(${lg.wallpaperBlur}px) brightness(${lg.wallpaperBrightness}%) `
    + `contrast(${lg.wallpaperContrast}%) saturate(${lg.wallpaperSaturate}%)`;
}

/** 模糊会软化边缘，轻微放大避免四边透出底色；翻转走合成器。 */
export function wallpaperTransform(lg: LiquidGlassSettings): string {
  const scale = 1 + lg.wallpaperBlur * 0.004;
  return `scale(${scale.toFixed(3)}) scaleX(${lg.flip ? -1 : 1})`;
}

/** 内容面空值回退：不能跟随玻璃基色（默认白），否则大块正文面白底浅字。 */
const DEFAULT_CONTENT_BASE = "#0d1524";

export function liquidGlassCssVars(lg: LiquidGlassSettings): Record<string, string> {
  const glass = glassOpacity(lg.glassAlpha);
  const sidebar = sidebarOpacity(lg.sidebarAlpha);
  const contentBase = lg.contentColor || DEFAULT_CONTENT_BASE;
  const content = contentOpacity(lg.contentAlpha);
  const border = rgba("#ffffff", borderOpacity(lg.border));
  const highlight = rgba("#ffffff", Math.min(0.8, 0.10 + borderOpacity(lg.border) * 0.7));
  return {
    "--lg-accent": lg.accent,
    "--lg-accent-text": accentTextColor(lg.accent),
    "--lg-accent-soft": rgba(lg.accent, 0.34),
    "--lg-accent-border": rgba(lg.accent, 0.52),
    "--lg-accent-glow": rgba(lg.accent, 0.26),
    "--lg-glass-color": lg.glassColor,
    "--lg-glass-top": rgba(lg.glassColor, glass * 0.95),
    "--lg-glass-bottom": rgba(lg.glassColor, glass * 0.42),
    "--lg-glass-solid": rgba(lg.glassColor, Math.min(0.92, glass * 3.2)),
    "--lg-glass-blur": `${lg.glassBlur}px`,
    "--lg-glass-border": border,
    "--lg-glass-highlight": highlight,
    "--lg-sidebar-top": rgba(lg.sidebarColor, sidebar * 0.95),
    "--lg-sidebar-bottom": rgba(lg.sidebarColor, sidebar * 0.38),
    "--lg-sidebar-border": border,
    "--lg-sidebar-blur": `${lg.sidebarBlur}px`,
    "--lg-content-top": rgba(contentBase, content * 0.95),
    "--lg-content-bottom": rgba(contentBase, content * 0.80),
    "--lg-content-border": border,
    "--lg-content-blur": `${Math.max(0, lg.glassBlur - 2)}px`,
    "--lg-scrim": String(lg.scrim / 100),
  };
}

/** 轮换的下一张；列表不足两张时保持当前。 */
export function nextLiquidGlassWallpaper(lg: LiquidGlassSettings): string {
  if (lg.wallpapers.length < 2) return lg.activeWallpaperId;
  const index = lg.wallpapers.findIndex((w) => w.id === lg.activeWallpaperId);
  return lg.wallpapers[(index + 1) % lg.wallpapers.length].id;
}
