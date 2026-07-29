import { describe, expect, it } from "vitest";
import { loadGenerationSize, saveGenerationSize } from "./generationPreferences";

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe("repository generation size preferences", () => {
  it("restores aspect and resolution independently for each repository", () => {
    const storage = new MemoryStorage();
    saveGenerationSize(storage, "repo-a", "16:9", "4k", "high");
    saveGenerationSize(storage, "repo-b", "4:3", "2k", "medium");

    expect(loadGenerationSize(storage, "repo-a")).toEqual({
      aspect: "16:9", resTier: "4k", quality: "high",
      customEnabled: false, customWidth: 1280, customHeight: 1280,
    });
    expect(loadGenerationSize(storage, "repo-b")).toEqual({
      aspect: "4:3", resTier: "2k", quality: "medium",
      customEnabled: false, customWidth: 1280, customHeight: 1280,
    });
  });

  it("falls back when persisted values are invalid", () => {
    const storage = new MemoryStorage();
    storage.setItem("laf_generation_sizes", JSON.stringify({ bad: { aspect: "broken", resTier: "8k" } }));

    expect(loadGenerationSize(storage, "bad")).toEqual({
      aspect: "1:1", resTier: "1k", quality: "high",
      customEnabled: false, customWidth: 1280, customHeight: 1280,
    });
  });

  it("persists bounded custom dimensions", () => {
    const storage = new MemoryStorage();
    saveGenerationSize(storage, "repo", "1:1", "1k", "high", true, 1536, 192);
    expect(loadGenerationSize(storage, "repo")).toMatchObject({
      customEnabled: true, customWidth: 1536, customHeight: 192,
    });
  });
});
