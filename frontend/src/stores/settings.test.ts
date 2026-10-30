import { describe, expect, it } from "vitest";
import {
  activeChatModel, chatBgRenderOpacity, chatModelSource, DEFAULT_LIQUID_GLASS, exportSettings,
  importSettings, liquidGlassSceneSource, loadPersistedSettings, modelDisplayName,
  normalizeContextBudgets, normalizeLiquidGlass, normalizeTheme, OLLAMA_DEFAULT_BASE_URL,
  remoteModelSource, resolvedEmbedModel, resolveTheme, stripSecrets,
} from "./settings";
import type { Settings } from "./settings";

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

  it("keeps the scene source and per-wallpaper preset deviations across reloads", () => {
    const lg = normalizeLiquidGlass({
      wallpapers: [
        {
          id: "local-scene",
          path: "D:/wallpapers/we-project/scene.pkg",
          scenePkg: true,
          sceneSource: " local:D:/wallpapers/we-project ",
          preset: { display: "1", scale: 0.5, shu: true, junk: { a: 1 }, nope: null },
        },
        {
          id: "legacy-we",
          path: "D:/SteamLibrary/steamapps/workshop/content/431960/3346715292/preview.gif",
          scenePkg: true,
          workshopId: "3346715292",
        },
        { id: "junk", path: "D:/wall/a.mp4", scenePkg: true, workshopId: "not-a-number" },
      ],
    });
    // 本地 WE 工程没有工坊 id，但仍要能实时渲染：scenePkg 不能只认数字 workshopId
    expect(lg.wallpapers[0].scenePkg).toBe(true);
    expect(lg.wallpapers[0].sceneSource).toBe("local:D:/wallpapers/we-project");
    expect(lg.wallpapers[0].preset).toEqual({ display: "1", scale: 0.5, shu: true });
    // 旧存档只有 workshopId：按工坊口径补出来源，升级后不该失去实时渲染
    expect(liquidGlassSceneSource(lg.wallpapers[1])).toBe("workshop:3346715292");
    // 取不到包的脏标记一律丢掉，否则会去请求一个必然 404 的来源
    expect(lg.wallpapers[2].scenePkg).toBeUndefined();
    expect(lg.wallpapers[2]).not.toHaveProperty("preset");
    expect(liquidGlassSceneSource(lg.wallpapers[2])).toBe("");
    expect(liquidGlassSceneSource(null)).toBe("");
    expect(liquidGlassSceneSource({ ...lg.wallpapers[0], scenePkg: false })).toBe("");
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

describe("模型来源三分（2026-10-30）", () => {
  it("旧对话模型缺 source：按 ggufPath / Ollama 地址推断", () => {
    const settings = importSettings(JSON.stringify({
      chatModels: [
        { id: "ollama", baseUrl: "http://localhost:11434/v1", apiKey: "", modelName: "qwen3:8b" },
        { id: "local", baseUrl: "", apiKey: "", modelName: "qwen3-8b", ggufPath: "D:/m.gguf" },
        { id: "cloud", baseUrl: "https://api.openai.com/v1", apiKey: "", modelName: "gpt-4o" },
      ],
    }));

    expect(settings.chatModels.map((m) => m.source)).toEqual(["ollama", "local", "api"]);
    expect(chatModelSource({ source: "ollama", baseUrl: "https://api.test/v1" })).toBe("ollama");
    expect(chatModelSource({ baseUrl: "https://api.test/v1", ggufPath: "D:/m.gguf" })).toBe("local");
  });

  it("旧嵌入/视觉配置缺 source：11434 归 Ollama，云端地址归 API", () => {
    const settings = importSettings(JSON.stringify({
      embedModel: { mode: "remote", baseUrl: "http://localhost:11434/v1", apiKey: "", modelName: "e" },
      vlmModel: { mode: "remote", baseUrl: "https://open.bigmodel.cn/api/paas/v4", apiKey: "", modelName: "v" },
    }));

    expect(settings.embedModel.source).toBe("ollama");
    expect(settings.vlmModel.source).toBe("api");
    expect(remoteModelSource({ source: "api", baseUrl: OLLAMA_DEFAULT_BASE_URL })).toBe("api");
  });

  it("用户显式选择的来源不被地址推断覆盖", () => {
    const settings = importSettings(JSON.stringify({
      embedModel: { mode: "remote", source: "api", baseUrl: OLLAMA_DEFAULT_BASE_URL, apiKey: "", modelName: "e" },
      vlmModel: { mode: "remote", source: "ollama", baseUrl: "", apiKey: "", modelName: "v" },
    }));

    expect(settings.embedModel.source).toBe("api");
    expect(settings.vlmModel.source).toBe("ollama");
  });

  it("Ollama 对话卡缺地址/Key 时兜底本机默认，且回环端点强制直连", () => {
    const settings = importSettings(JSON.stringify({
      proxyEnabled: true,
      proxyUrl: "http://127.0.0.1:7897",
      activeChatModelId: "o",
      chatModels: [{ id: "o", source: "ollama", baseUrl: "", apiKey: "", modelName: "qwen3:8b", proxyMode: "on" }],
    }));

    const model = activeChatModel(settings);
    expect(model.baseUrl).toBe(OLLAMA_DEFAULT_BASE_URL);
    expect(model.apiKey).toBe("ollama");
    expect(model.proxyUrl).toBe("");
  });

  it("本地 GGUF 对话卡同样按本机 Ollama 直连，不受全局代理影响", () => {
    const settings = importSettings(JSON.stringify({
      proxyEnabled: true,
      proxyUrl: "http://127.0.0.1:7897",
      activeChatModelId: "l",
      chatModels: [{
        id: "l", source: "local", baseUrl: OLLAMA_DEFAULT_BASE_URL, apiKey: "ollama",
        modelName: "qwen3-8b", ggufPath: "D:/m.gguf", proxyMode: "on",
      }],
    }));

    expect(activeChatModel(settings).proxyUrl).toBe("");
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

// ---------------------------------------------------------------------------
// 2026-10-24 XSS→密钥链加固：localStorage 持久化不落密钥。
// 真实失败路径：localStorage 可被任意同源脚本/扩展读取，含 apiKey 的完整 settings
// 双写进 localStorage 与「密钥只落后端 user_state.json」合同漂移。剥离清单单一属主
// ＝ stripSecrets（persist 与 exportSettings(keepKeys=false) 共用）。
// ---------------------------------------------------------------------------

describe("localStorage 密钥剥离", () => {
  const withSecrets = {
    theme: "system",
    chatModels: [{ id: "c", apiKey: "sk-chat", baseUrl: "https://api.test/v1", modelName: "m1", proxyMode: "on" }],
    imageModels: [{ id: "i", apiKey: "sk-img", baseUrl: "", modelName: "m2", proxyMode: "on" }],
    videoModels: [{ id: "v", apiKey: "sk-vid", baseUrl: "", modelName: "m3", proxyMode: "on" }],
    embedModel: { mode: "remote", apiKey: "sk-embed", baseUrl: "", modelName: "e", proxyMode: "on" },
    vlmModel: { mode: "remote", apiKey: "sk-vlm", baseUrl: "", modelName: "vl", proxyMode: "on" },
    hfToken: "hf_x",
    civitaiToken: "civ_x",
    smitheryKey: "sm_x",
    pixivRefreshToken: "px_x",
    danbooruLogin: "dan_login",
    danbooruApiKey: "dan_key",
    workflowDir: "D:/wf",
    outputDir: "D:/out",
  } as unknown as Settings;

  it("stripSecrets 清空全部密钥/令牌字段且保留非敏感配置", () => {
    const out = stripSecrets(withSecrets);
    expect(out.chatModels[0].apiKey).toBe("");
    expect(out.imageModels[0].apiKey).toBe("");
    expect(out.videoModels[0].apiKey).toBe("");
    expect(out.embedModel.apiKey).toBe("");
    expect(out.vlmModel.apiKey).toBe("");
    expect(out.hfToken).toBe("");
    expect(out.civitaiToken).toBe("");
    expect(out.smitheryKey).toBe("");
    expect(out.pixivRefreshToken).toBe("");
    expect(out.danbooruLogin).toBe("");
    expect(out.danbooruApiKey).toBe("");
    // 非敏感字段逐字保留
    expect(out.workflowDir).toBe("D:/wf");
    expect(out.outputDir).toBe("D:/out");
    expect(out.chatModels[0].modelName).toBe("m1");
    // 原对象不被就地修改（内存里的真值照常用于编辑/回传后端）
    expect(withSecrets.hfToken).toBe("hf_x");
  });

  it("exportSettings(keepKeys=false) 与 stripSecrets 同一张清单", () => {
    expect(JSON.parse(exportSettings(withSecrets, false))).toEqual(stripSecrets(withSecrets));
    // keepKeys=true 保留真值（导出走文件，用户显式选择才带密钥）
    expect((JSON.parse(exportSettings(withSecrets, true)) as Settings).hfToken).toBe("hf_x");
  });

  it("loadPersistedSettings 清洗旧存量：含密钥的 localStorage 读入即覆写为剥离版", () => {
    // Storage 参数注入（lastSelection 同款惯例）：Node 25 的内置 localStorage 桩不可用
    class MemoryStorage implements Storage {
      private map = new Map<string, string>();
      get length() { return this.map.size; }
      clear() { this.map.clear(); }
      getItem(k: string) { return this.map.get(k) ?? null; }
      key(i: number) { return [...this.map.keys()][i] ?? null; }
      removeItem(k: string) { this.map.delete(k); }
      setItem(k: string, v: string) { this.map.set(k, String(v)); }
    }
    const storage = new MemoryStorage();
    storage.setItem("demiurge_settings", JSON.stringify(withSecrets));

    const loaded = loadPersistedSettings(storage);

    expect(loaded.hfToken).toBe("");
    expect(loaded.chatModels[0].apiKey).toBe("");
    // 盘上旧密钥被一次性清洗，之后 persist 也不会回潮
    const onDisk = JSON.parse(storage.getItem("demiurge_settings") || "{}");
    expect(onDisk.hfToken).toBe("");
    expect(onDisk.chatModels[0].apiKey).toBe("");
    expect(onDisk.workflowDir).toBe("D:/wf");
  });
});
