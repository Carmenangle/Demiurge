import { describe, expect, it } from "vitest";
import {
  modelDisplayName, normalizeContextBudgets, normalizeTheme, resolveTheme,
} from "./settings";

describe("theme settings", () => {
  it("migrates the old light and dark values", () => {
    expect(normalizeTheme("light")).toBe("eye-care");
    expect(normalizeTheme("dark")).toBe("night");
  });

  it("falls back for unknown persisted values", () => {
    expect(normalizeTheme("unknown")).toBe("system");
  });

  it("maps system mode to bright or night", () => {
    expect(resolveTheme("system", false)).toBe("bright");
    expect(resolveTheme("system", true)).toBe("night");
    expect(resolveTheme("green", true)).toBe("green");
  });
});

describe("context token settings", () => {
  it("defaults to a 12000 reminder and 20000 hard limit", () => {
    expect(normalizeContextBudgets(undefined, undefined)).toEqual({
      reminder: 12_000,
      max: 20_000,
    });
  });

  it("keeps the reminder below the hard limit", () => {
    expect(normalizeContextBudgets(30_000, 20_000)).toEqual({
      reminder: 19_000,
      max: 20_000,
    });
  });
});

describe("model display names", () => {
  it("uses a custom display name without changing the API model name", () => {
    const model = { displayName: "GPT Image 2 · 4K令牌", modelName: "gpt-image-2" };
    expect(modelDisplayName(model)).toBe("GPT Image 2 · 4K令牌");
    expect(model.modelName).toBe("gpt-image-2");
  });

  it("falls back to the API model name for older settings", () => {
    expect(modelDisplayName({ modelName: "gpt-image-2" })).toBe("gpt-image-2");
  });
});
