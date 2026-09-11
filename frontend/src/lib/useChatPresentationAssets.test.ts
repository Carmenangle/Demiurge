import { describe, expect, it } from "vitest";
import { loadDisplayRegexSources, mergeDisplayRegexSources } from "./useChatPresentationAssets";
import type { RegexScript } from "./regexEngine";

const script = (id: string, markdownOnly = true, disabled = false) => ({
  id,
  scriptName: id,
  findRegex: `/${id}/g`,
  replaceString: "",
  placement: [2],
  markdownOnly,
  promptOnly: false,
  disabled,
} as RegexScript);

describe("mergeDisplayRegexSources", () => {
  it("按全局、预设、卡内顺序合并显示正则", () => {
    expect(mergeDisplayRegexSources(
      [script("card")],
      [script("preset"), script("prompt-only", false), script("disabled", true, true)],
      [script("global")],
    ).map((item) => item.id)).toEqual(["global", "preset", "card"]);
  });

  it("当前预设正则会进入展示链", async () => {
    const calls: string[] = [];
    const result = await loadDisplayRegexSources(
      ["card-a"], "cards", "presets", "graywill", {
        global: async () => { calls.push("global"); return [script("global")]; },
        preset: async (base, name) => {
          calls.push(`preset:${base}:${name}`);
          return [script("preset")];
        },
        card: async (base, name) => {
          calls.push(`card:${base}:${name}`);
          return [script("card")];
        },
      },
    );
    expect(calls).toContain("preset:presets:graywill");
    expect(result.map((item) => item.id)).toEqual(["global", "preset", "card"]);
  });
});
