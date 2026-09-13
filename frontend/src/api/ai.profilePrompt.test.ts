import { afterEach, describe, expect, it, vi } from "vitest";

import { genProfilePrompt } from "./ai";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("profile prompt fallback request", () => {
  it("sends the active jailbreak preset to the separate prompt request", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body || "{}"));
      expect(body).toMatchObject({
        preset_dir: "D:/presets",
        preset_name: "GrayWill",
        user_name: "我",
      });
      return new Response(JSON.stringify({ prompt: "ok", profile: "anima_tags" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await genProfilePrompt("anima_tags", {
      narrative: "scene", draft_prompt: "", wardrobe: "", locale: "",
      actors: ["冷倾雪"], rating: "nsfw",
    }, { baseUrl: "chat", apiKey: "key", modelName: "model" }, {
      presetDir: "D:/presets", presetName: "GrayWill", userName: "我",
    });

    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("aborts instead of leaving an illustration slot pending forever", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")));
      })));

    const pending = genProfilePrompt("krea2", {
      narrative: "高潮段",
      draft_prompt: "",
      wardrobe: "当前服装",
      locale: "当前地点",
      actors: ["角色"],
      rating: "sfw",
    }, { baseUrl: "chat", apiKey: "key", modelName: "model" });

    const rejected = expect(pending).rejects.toThrow("请求超时（超过 120 秒）");
    await vi.advanceTimersByTimeAsync(120_000);
    await rejected;
  });
});
