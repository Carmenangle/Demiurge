import { describe, expect, it } from "vitest";
import type { Settings } from "../stores/settings";
import {
  changedModelSettings,
  omitModelSettings,
  overlayModelSettings,
  pickModelSettings,
} from "./mobileModelSettings";

const PC_CHAT = { id: "pc-1", modelName: "pc-model", baseUrl: "https://pc.example/v1", apiKey: "pc-key" };
const PHONE_CHAT = { id: "ph-1", modelName: "phone-model", baseUrl: "https://phone.example/v1", apiKey: "ph-key" };
const PC_EMBED = { mode: "remote" as const, baseUrl: "https://pc.example/v1", apiKey: "pc-embed", modelName: "pc-embed" };
const PHONE_EMBED = { mode: "remote" as const, baseUrl: "https://phone.example/v1", apiKey: "ph-embed", modelName: "ph-embed" };

function settings(over: Record<string, unknown> = {}): Settings {
  return {
    theme: "night",
    chatModels: [PC_CHAT],
    activeChatModelId: "pc-1",
    imageModels: [],
    videoModels: [],
    embedModel: PC_EMBED,
    vlmModel: { mode: "remote", baseUrl: "https://pc.example/v1", apiKey: "pc-vlm", modelName: "pc-vlm" },
    outputDir: "D:/works",
    ...over,
  } as unknown as Settings;
}

describe("联机模型设置的所有权纯逻辑", () => {
  it("pick 只取模型字段", () => {
    const picked = pickModelSettings(settings());
    expect(Object.keys(picked).sort()).toEqual([
      "activeChatModelId", "chatModels", "embedModel", "imageModels",
      "videoModels", "vlmModel",
    ]);
  });

  it("omit 摘掉模型字段，其余设置原样保留", () => {
    const out = omitModelSettings(settings());
    for (const key of ["chatModels", "activeChatModelId", "embedModel", "vlmModel", "imageModels", "videoModels"]) {
      expect(key in out).toBe(false);
    }
    expect(out.theme).toBe("night");
    expect((out as unknown as Record<string, unknown>).outputDir).toBe("D:/works");
  });

  it("手机没配过的模型字段沿用电脑那份", () => {
    const merged = overlayModelSettings(settings(), { activeChatModelId: "ph-1" });
    expect(merged.activeChatModelId).toBe("ph-1");
    expect(merged.chatModels[0].modelName).toBe("pc-model"); // 手机没存列表 ⇒ 跟电脑
    expect(merged.embedModel.modelName).toBe("pc-embed");
  });

  it("手机存过的模型字段覆盖电脑快照", () => {
    const merged = overlayModelSettings(settings(), {
      chatModels: [PHONE_CHAT],
      activeChatModelId: "ph-1",
      embedModel: PHONE_EMBED,
    });
    expect(merged.chatModels[0].modelName).toBe("phone-model");
    expect(merged.activeChatModelId).toBe("ph-1");
    expect(merged.embedModel.modelName).toBe("ph-embed");
    expect(merged.vlmModel.modelName).toBe("pc-vlm"); // 没存过 ⇒ 电脑
  });

  it("没有本机模型时原样返回电脑快照", () => {
    const pc = settings();
    expect(overlayModelSettings(pc, null)).toBe(pc);
  });

  it("changed 只报相对基线真变过的字段", () => {
    const baseline = pickModelSettings(settings());
    expect(changedModelSettings(settings(), baseline)).toEqual({});
    expect(changedModelSettings(settings({ theme: "bright" }), baseline)).toEqual({});
    expect(changedModelSettings(settings({ chatModels: [PHONE_CHAT] }), baseline))
      .toEqual({ chatModels: [PHONE_CHAT] });
    expect(changedModelSettings(settings({ activeChatModelId: "ph-1" }), baseline))
      .toEqual({ activeChatModelId: "ph-1" });
  });

  it("没有基线时不报改动（首次联机只跟随电脑，不落本机）", () => {
    expect(changedModelSettings(settings(), null)).toEqual({});
  });
});
