import { describe, expect, it } from "vitest";
import {
  activeChatModel, chatBgRenderOpacity, DEFAULT_LIQUID_GLASS, importSettings, modelDisplayName,
  normalizeContextBudgets, normalizeLiquidGlass, normalizeTheme, resolvedEmbedModel, resolveTheme,
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

  it("accepts the liquid glass theme independently of the system preference", () => {
    expect(normalizeTheme("liquid-glass")).toBe("liquid-glass");
    expect(resolveTheme("liquid-glass", false)).toBe("liquid-glass");
    expect(resolveTheme("liquid-glass", true)).toBe("liquid-glass");
  });

  it("boosts wallpaper opacity only for the liquid glass theme", () => {
    expect(chatBgRenderOpacity("eye-care", 0.15)).toBe(0.15);
    expect(chatBgRenderOpacity("liquid-glass", 0.15)).toBeCloseTo(0.6175);
    expect(chatBgRenderOpacity("liquid-glass", 1)).toBe(1);
  });
});

describe("liquid glass standalone theme settings", () => {
  it("fills the full control-console defaults for missing config", () => {
    const lg = normalizeLiquidGlass(undefined);
    expect(lg.accent).toBe(DEFAULT_LIQUID_GLASS.accent);
    expect(lg.glassColor).toBe("#ffffff");
    expect(lg.glassWindow).toBe(true);
    expect(lg.sidebarGlass).toBe(true);
    expect(lg.wallpapers).toEqual([]);
    expect(lg.rotationInterval).toBe(30);
    expect(lg.ropeShown).toBe(true);
    expect(lg.ropeScale).toBe(1);
  });

  it("clamps every numeric range and rejects invalid colors/fits", () => {
    const lg = normalizeLiquidGlass({
      accent: "javascript:alert(1)",
      glassColor: "red",
      glassAlpha: 999,
      glassBlur: -3,
      sidebarAlpha: -20,
      contentAlpha: 500,
      contentColor: "not-a-color",
      wallpaperFit: "weird",
      wallpaperBrightness: 999,
      playbackRate: 9,
      rotationInterval: 1,
      ropeScale: 9,
    });
    expect(lg.accent).toBe(DEFAULT_LIQUID_GLASS.accent);
    expect(lg.glassColor).toBe(DEFAULT_LIQUID_GLASS.glassColor);
    expect(lg.glassAlpha).toBe(60);
    expect(lg.glassBlur).toBe(0);
    expect(lg.sidebarAlpha).toBe(0);
    expect(lg.contentAlpha).toBe(80);
    expect(lg.contentColor).toBe(DEFAULT_LIQUID_GLASS.contentColor);
    expect(lg.wallpaperFit).toBe("cover");
    expect(lg.wallpaperBrightness).toBe(160);
    expect(lg.playbackRate).toBe(2);
    expect(lg.rotationInterval).toBe(10);
    expect(lg.ropeScale).toBe(2.5);
  });

  it("keeps an explicit empty content color as follow-glass", () => {
    expect(normalizeLiquidGlass({ contentColor: "" }).contentColor).toBe("");
  });

  it("dedupes wallpapers and repairs the active id", () => {
    const lg = normalizeLiquidGlass({
      wallpapers: [
        { path: "D:/wall/a.mp4" },
        { path: "D:/wall/a.mp4" },
        { path: "" },
        { id: "second", path: "D:/wall/b.PNG" },
      ],
      activeWallpaperId: "missing",
    });
    expect(lg.wallpapers).toHaveLength(2);
    expect(lg.wallpapers[0].kind).toBe("video");
    expect(lg.wallpapers[0].name).toBe("a.mp4");
    expect(lg.wallpapers[1].kind).toBe("image");
    expect(lg.activeWallpaperId).toBe(lg.wallpapers[0].id);
  });

  it("keeps workshop preview covers and low-res marks across reloads", () => {
    const lg = normalizeLiquidGlass({
      wallpapers: [{
        id: "we",
        path: "D:/SteamLibrary/steamapps/workshop/content/431960/1/clip.mp4",
        name: "作品",
        preview: "  D:/SteamLibrary/steamapps/workshop/content/431960/1/preview.gif  ",
        lowRes: true,
      }],
    });
    expect(lg.wallpapers[0].preview).toBe(
      "D:/SteamLibrary/steamapps/workshop/content/431960/1/preview.gif",
    );
    expect(lg.wallpapers[0].lowRes).toBe(true);
    expect(normalizeLiquidGlass({ wallpapers: [{ path: "D:/a.png" }] }).wallpapers[0])
      .not.toHaveProperty("preview");
  });

  it("keeps an explicit closed wallpaper closed across reloads", () => {
    const lg = normalizeLiquidGlass({
      wallpapers: [{ id: "kept", path: "D:/wall/a.png" }],
      activeWallpaperId: "",
    });
    expect(lg.wallpapers).toHaveLength(1);
    expect(lg.activeWallpaperId).toBe("");
  });

  it("trims the wallpaper folder and dedupes hidden paths", () => {
    const lg = normalizeLiquidGlass({
      wallpaperDir: '  "D:/wall/engine"  ',
      wallpaperHidden: [" D:/wall/a.mp4 ", "", 3, "D:/wall/a.mp4", "D:/wall/b.png"],
      wallpaperStoreDir: '  "~/Demiurge/wallpapers"  ',
    });
    expect(lg.wallpaperDir).toBe("D:/wall/engine");
    expect(lg.wallpaperHidden).toEqual(["D:/wall/a.mp4", "D:/wall/b.png"]);
    expect(lg.wallpaperStoreDir).toBe("~/Demiurge/wallpapers");
    expect(normalizeLiquidGlass(undefined).wallpaperDir).toBe("");
    expect(normalizeLiquidGlass(undefined).wallpaperHidden).toEqual([]);
    expect(normalizeLiquidGlass(undefined).wallpaperStoreDir).toBe("");
  });

  it("migrates legacy settings that predate the standalone theme", () => {
    const settings = importSettings(JSON.stringify({ theme: "liquid-glass" }));
    expect(settings.theme).toBe("liquid-glass");
    expect(settings.liquidGlass.glassWindow).toBe(true);
    expect(settings.liquidGlass.ropeForm).toBe("icon");
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

describe("上下文 token 化（2026-09-11）", () => {
  it("条数设置废弃：contextCount/旧 historyPerRole 键被忽略，不进 settings", () => {
    const settings = importSettings(JSON.stringify({ contextCount: 4, historyPerRole: 9 }));
    expect((settings as unknown as Record<string, unknown>).contextCount).toBeUndefined();
  });

  it("压缩触发百分比：缺失回退 80，非法钳到 0~100", () => {
    expect(importSettings("{}").compactTriggerPct).toBe(80);
    expect(importSettings(JSON.stringify({ compactTriggerPct: 60 })).compactTriggerPct).toBe(60);
    expect(importSettings(JSON.stringify({ compactTriggerPct: 250 })).compactTriggerPct).toBe(100);
    expect(importSettings(JSON.stringify({ compactTriggerPct: -5 })).compactTriggerPct).toBe(0);
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
  it("历史模型（无 proxyMode）迁移为「继承全局」，跟随探测结果", () => {
    // 2026-10-20 用户定案（方案 C）**取代**了原先「历史模型一律 proxy on」的决定：
    // 「on」语义是「用户显式强制，探测判定不可用也照走」——把从没设过的模型当
    // 「强制走代理」，会让探测结果失效，并造成「对话能通、填表 Connection error」的割裂。
    const settings = importSettings(JSON.stringify({
      chatModels: [{ id: "c", baseUrl: "c", apiKey: "", modelName: "c" }],
      imageModels: [{ id: "i", baseUrl: "i", apiKey: "", modelName: "i" }],
      videoModels: [{ id: "v", baseUrl: "v", apiKey: "", modelName: "v" }],
      embedModel: { mode: "remote", baseUrl: "e", apiKey: "", modelName: "e" },
    }));

    expect(settings.chatModels[0].proxyMode).toBe("inherit");
    expect(settings.imageModels[0].proxyMode).toBe("inherit");
    expect(settings.videoModels[0].proxyMode).toBe("inherit");
    expect(settings.embedModel.proxyMode).toBe("inherit");
  });

  it("用户显式选过的 proxyMode 不被迁移覆盖", () => {
    const settings = importSettings(JSON.stringify({
      chatModels: [{ id: "c", baseUrl: "c", apiKey: "", modelName: "c", proxyMode: "on" }],
      imageModels: [{ id: "i", baseUrl: "i", apiKey: "", modelName: "i", proxyMode: "off" }],
    }));

    expect(settings.chatModels[0].proxyMode).toBe("on");
    expect(settings.imageModels[0].proxyMode).toBe("off");
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

describe("provider profile migration", () => {
  it("旧 Claude 模型名不得被猜成 Claude wire", () => {
    const settings = importSettings(JSON.stringify({
      chatModels: [{
        id: "c", baseUrl: "https://proxy.example/v1", apiKey: "",
        modelName: "claude-opus-4-6",
      }],
    }));

    expect(settings.chatModels[0].providerProfile).toBe("openai_compatible");
  });

  it("清理旧版按模型名自动写入的 Claude profile", () => {
    const settings = importSettings(JSON.stringify({
      chatModels: [{
        id: "c", baseUrl: "https://proxy.example/v1", apiKey: "",
        modelName: "claude-opus-4-6", providerProfile: "claude_compatible",
      }],
    }));

    expect(settings.chatModels[0].providerProfile).toBe("openai_compatible");
  });

  it("新语义版本保留用户明确选择的 Claude wire", () => {
    const settings = importSettings(JSON.stringify({
      providerProfileSemanticsVersion: 2,
      chatModels: [{
        id: "c", baseUrl: "https://proxy.example/v1", apiKey: "",
        modelName: "claude-opus-4-6", providerProfile: "claude_compatible",
      }],
    }));

    expect(settings.chatModels[0].providerProfile).toBe("claude_compatible");
  });
});
