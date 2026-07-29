import { describe, expect, it } from "vitest";
import { filterModelNames } from "./modelSearch";

describe("model list search", () => {
  const models = ["gpt-4o", "GPT-4.1-mini", "claude-3-7-sonnet", "text-embedding-3-large"];

  it("filters model names without case sensitivity", () => {
    expect(filterModelNames(models, "gpt")).toEqual(["gpt-4o", "GPT-4.1-mini"]);
  });

  it("matches every space-separated term", () => {
    expect(filterModelNames(models, "4.1 MINI")).toEqual(["GPT-4.1-mini"]);
  });

  it("returns the full list for an empty query", () => {
    expect(filterModelNames(models, "   ")).toBe(models);
  });
});
