import { describe, expect, it } from "vitest";
import { illustrationRequestMedia, illustrationWorkflowMedia } from "./illustrationMedia";

const legacyBindings = {
  templateId: "tpl",
  appearanceSource: "character_card" as const,
  characterLoras: {
    露娜: { loraName: "role.safetensors", loraWeight: 1.1, baseImage: "role.png" },
  },
  styleLora: "style.safetensors",
  styleLoraWeight: 0.7,
  styleBaseImage: "fallback.png",
};

describe("character-card illustration media", () => {
  it("removes role and fallback images from request metadata", () => {
    expect(illustrationRequestMedia(legacyBindings, ["露娜"])).toEqual({
      characterBaseImages: {}, illustrationActorNames: ["露娜"], styleBaseImage: "",
    });
  });

  it("ignores role LoRA and images but keeps the optional global style LoRA", () => {
    expect(illustrationWorkflowMedia(legacyBindings, ["露娜"], ["露娜"])).toEqual({
      loras: [{ name: "style.safetensors", weight: 0.7, character: false }],
      loraName: "style.safetensors", loraWeight: 0.7, baseImage: "", characterLora: false,
    });
  });
});

describe("illustration LoRA modes", () => {
  const preset = {
    templateId: "tpl",
    characterLoras: {
      甲: { loraName: "a.safetensors", loraWeight: 0.9, baseImage: "a.png" },
      乙: { loraName: "b.safetensors", loraWeight: 1.1, baseImage: "b.png" },
      丙: { baseImage: "c.png" },
    },
    styleLora: "style.safetensors",
    styleLoraWeight: 0.7,
    styleBaseImage: "fallback.png",
  };

  it("世界书模式只上报角色 LoRA 绑定名，不把作品卡名当成角色", () => {
    expect(illustrationRequestMedia(
      { ...preset, appearanceSource: "worldbook" }, ["白给谷"],
    )).toMatchObject({ illustrationActorNames: ["甲", "乙", "丙"] });
  });

  it("单 LoRA 只匹配真实在场角色，不把全部绑定卡当作在场角色", () => {
    expect(illustrationWorkflowMedia({ ...preset, loraMode: "single" }, ["乙"], ["甲", "乙"]))
      .toMatchObject({
        loras: [{ name: "b.safetensors", weight: 1.1, character: true }],
        baseImage: "b.png",
      });
  });

  it("单 LoRA 在该角色没有 LoRA 时回退风格 LoRA", () => {
    expect(illustrationWorkflowMedia({ ...preset, loraMode: "single" }, ["丙"], ["甲", "丙"]))
      .toMatchObject({
        loras: [{ name: "style.safetensors", weight: 0.7, character: false }],
        baseImage: "c.png",
      });
  });

  it("多 LoRA 固定叠加默认风格与全部在场角色 LoRA", () => {
    expect(illustrationWorkflowMedia({ ...preset, loraMode: "multi" }, ["甲", "乙"], ["甲", "乙"]))
      .toMatchObject({
        loras: [
          { name: "style.safetensors", weight: 0.7, character: false },
          { name: "a.safetensors", weight: 0.9, character: true },
          { name: "b.safetensors", weight: 1.1, character: true },
        ],
      });
  });

  it("多 LoRA 的全部在场角色均无角色 LoRA 时才回退风格", () => {
    expect(illustrationWorkflowMedia({ ...preset, loraMode: "multi" }, ["丁"], ["甲", "乙"]))
      .toMatchObject({
        loras: [{ name: "style.safetensors", weight: 0.7, character: false }],
      });
  });

  it("冷倾雪与虞妙玥同场加载默认风格、柳世熙与蔡秀晶", () => {
    const result = illustrationWorkflowMedia({
      templateId: "tpl",
      loraMode: "multi",
      appearanceSource: "worldbook",
      characterLoras: {
        冷倾雪: { loraName: "krea2_柳世熙.safetensors", loraWeight: 1 },
        虞妙玥: { loraName: "krea2_蔡秀晶.safetensors", loraWeight: 1 },
      },
      styleLora: "Krea2-Ogipote style-手绘风.safetensors",
      styleLoraWeight: 1,
    }, ["冷倾雪", "虞妙玥"], ["白给谷"]);

    expect(result.loras).toEqual([
      { name: "Krea2-Ogipote style-手绘风.safetensors", weight: 1, character: false },
      { name: "krea2_柳世熙.safetensors", weight: 1, character: true },
      { name: "krea2_蔡秀晶.safetensors", weight: 1, character: true },
    ]);
  });

  it("冷倾雪单 LoRA 命中柳世熙且不加载 Ogipote", () => {
    const result = illustrationWorkflowMedia({
      templateId: "tpl",
      loraMode: "single",
      appearanceSource: "worldbook",
      characterLoras: {
        冷倾雪: { loraName: "krea2_柳世熙.safetensors", loraWeight: 1 },
      },
      styleLora: "Krea2-Ogipote style-手绘风.safetensors",
      styleLoraWeight: 1,
    }, ["冷倾雪"], ["白给谷"]);

    expect(result.loras).toEqual([
      { name: "krea2_柳世熙.safetensors", weight: 1, character: true },
    ]);
  });

  it("无 LoRA 模式只保留角色底图", () => {
    expect(illustrationWorkflowMedia({ ...preset, loraMode: "none" }, ["甲"], ["甲"]))
      .toMatchObject({ loras: [], loraName: "", baseImage: "a.png" });
  });
});
