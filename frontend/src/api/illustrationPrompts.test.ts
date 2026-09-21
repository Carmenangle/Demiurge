import { afterEach, describe, expect, it, vi } from "vitest";

import { getIllustrationPromptSettings, saveIllustrationPromptSettings } from "./illustrationPrompts";

afterEach(() => {
  vi.unstubAllGlobals();
});

const PAYLOAD = {
  climax_anchor: { rubric: "判据" },
  default_rubric: "缺省判据",
  output_contract: "程序怎么读",
};

describe("illustration prompt settings api", () => {
  it("reads the settings from the dedicated prompt settings endpoint", async () => {
    const fetchMock = vi.fn(async (_url: string) => new Response(
      JSON.stringify(PAYLOAD), { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const data = await getIllustrationPromptSettings();

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/ai/prompt/settings");
    expect(data.output_contract).toBe("程序怎么读");
  });

  it("posts only the climax anchor section", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(
      JSON.stringify(PAYLOAD), { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await saveIllustrationPromptSettings({ rubric: "我的判据" });

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body || "{}"));
    expect(body).toEqual({ climax_anchor: { rubric: "我的判据" } });
  });
});
