import { describe, expect, it } from "vitest";
import {
  accentTextColor, contentOpacity, glassOpacity, liquidGlassCssVars, nextLiquidGlassWallpaper,
  rgba, sidebarOpacity, wallpaperFilter, wallpaperTransform,
} from "./liquidGlass";
import { DEFAULT_LIQUID_GLASS, type LiquidGlassSettings } from "../stores/settings";

const lg = (patch: Partial<LiquidGlassSettings> = {}): LiquidGlassSettings => ({
  ...DEFAULT_LIQUID_GLASS,
  ...patch,
});

describe("liquid glass variable mapping", () => {
  it("parses hex colors and emits rgba", () => {
    expect(rgba("#fff", 0.5)).toBe("rgba(255, 255, 255, 0.500)");
    expect(rgba("#0d1524", 0.25)).toBe("rgba(13, 21, 36, 0.250)");
  });

  it("picks readable accent text", () => {
    expect(accentTextColor("#ffffff")).toBe("#07101f");
    expect(accentTextColor("#0d1524")).toBe("#f7faff");
  });

  it("maps the three transparency sliders so higher means more transparent", () => {
    expect(glassOpacity(0)).toBeGreaterThan(glassOpacity(60));
    expect(sidebarOpacity(0)).toBeGreaterThan(sidebarOpacity(200));
    expect(contentOpacity(0)).toBe(1);
    expect(contentOpacity(80)).toBeCloseTo(0.2);
  });

  it("emits the CSS variables consumed by the theme", () => {
    const vars = liquidGlassCssVars(lg({ accent: "#67DCE7", glassAlpha: 30, border: 50 }));
    expect(vars["--lg-accent"]).toBe("#67DCE7");
    expect(vars["--lg-glass-top"]).toContain("rgba(");
    expect(vars["--lg-glass-blur"]).toBe("24px");
    expect(vars["--lg-scrim"]).toBe("0.25");
    expect(vars["--lg-content-top"]).toBe("rgba(13, 21, 36, 0.665)");
  });

  it("falls back to the dark content base when contentColor is empty", () => {
    // 「跟随玻璃色」曾让内容面吃到白色玻璃基色 → 设置页白底浅字。
    const vars = liquidGlassCssVars(lg({ contentColor: "", glassColor: "#ffffff", contentAlpha: 30 }));
    expect(vars["--lg-content-top"]).toBe("rgba(13, 21, 36, 0.665)");
    expect(vars["--lg-content-bottom"]).toBe("rgba(13, 21, 36, 0.560)");
  });

  it("builds wallpaper filters and the flip transform", () => {
    expect(wallpaperFilter(lg({ wallpaperBlur: 8, wallpaperBrightness: 110, wallpaperContrast: 90, wallpaperSaturate: 120 })))
      .toBe("blur(8px) brightness(110%) contrast(90%) saturate(120%)");
    expect(wallpaperTransform(lg({ wallpaperBlur: 10, flip: true }))).toBe("scale(1.040) scaleX(-1)");
  });

  it("advances rotation in list order and wraps", () => {
    const base = lg({
      wallpapers: [
        { id: "a", path: "a.png", name: "a", kind: "image" },
        { id: "b", path: "b.png", name: "b", kind: "image" },
      ],
      activeWallpaperId: "b",
    });
    expect(nextLiquidGlassWallpaper(base)).toBe("a");
    expect(nextLiquidGlassWallpaper({ ...base, activeWallpaperId: "a" })).toBe("b");
    expect(nextLiquidGlassWallpaper({ ...base, wallpapers: base.wallpapers.slice(0, 1) })).toBe("b");
  });
});
