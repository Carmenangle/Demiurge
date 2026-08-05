import { describe, expect, it } from "vitest";
import {
  activeChatModel, importSettings, modelDisplayName, normalizeContextBudgets, normalizeTheme,
  resolvedEmbedModel, resolveTheme,
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

describe("agent stream output setting", () => {
  it("defaults legacy settings to non-streaming and preserves an enabled choice", () => {
    expect(importSettings("{}").streamOutput).toBe(false);
    expect(importSettings(JSON.stringify({ streamOutput: true })).streamOutput).toBe(true);
  });
});

describe("illustration prompt profile setting", () => {
  it("preserves the selected per-repository prompt profile", () => {
    const settings = importSettings(JSON.stringify({
      mediaInsert: { work: { templateId: "tpl", promptProfile: "niji_sections" } },
    }));
    expect(settings.mediaInsert?.work.promptProfile).toBe("niji_sections");
  });

  it("preserves the selected latent longest edge", () => {
    const settings = importSettings(JSON.stringify({
      mediaInsert: { work: { templateId: "tpl", latentLongEdge: 4096 } },
    }));
    expect(settings.mediaInsert?.work.latentLongEdge).toBe(4096);
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

describe("model proxy migration", () => {
  it("defaults every legacy model to proxy on", () => {
    const settings = importSettings(JSON.stringify({
      chatModels: [{ id: "c", baseUrl: "c", apiKey: "", modelName: "c" }],
      imageModels: [{ id: "i", baseUrl: "i", apiKey: "", modelName: "i" }],
      videoModels: [{ id: "v", baseUrl: "v", apiKey: "", modelName: "v" }],
      embedModel: { mode: "remote", baseUrl: "e", apiKey: "", modelName: "e" },
    }));

    expect(settings.chatModels[0].proxyMode).toBe("on");
    expect(settings.imageModels[0].proxyMode).toBe("on");
    expect(settings.videoModels[0].proxyMode).toBe("on");
    expect(settings.embedModel.proxyMode).toBe("on");
  });

  it("resolves the active chat and embedding proxies used during app startup", () => {
    const settings = importSettings(JSON.stringify({
      proxyEnabled: false,
      proxyUrl: "http://127.0.0.1:7897",
      activeChatModelId: "c",
      chatModels: [{ id: "c", baseUrl: "c", apiKey: "", modelName: "c", proxyMode: "on" }],
      embedModel: {
        mode: "remote", baseUrl: "e", apiKey: "", modelName: "e", proxyMode: "inherit",
      },
    }));

    expect(activeChatModel(settings).proxyUrl).toBe("http://127.0.0.1:7897");
    expect(resolvedEmbedModel(settings).proxyUrl).toBe("");
  });

  it("keeps a loopback embedding endpoint direct even when its proxy mode is on", () => {
    const settings = importSettings(JSON.stringify({
      proxyEnabled: true,
      proxyUrl: "http://127.0.0.1:7897",
      embedModel: {
        mode: "remote",
        baseUrl: "http://localhost:11434/v1",
        apiKey: "",
        modelName: "qwen3-embedding:latest",
        proxyMode: "on",
      },
    }));

    expect(resolvedEmbedModel(settings).proxyUrl).toBe("");
  });
});
