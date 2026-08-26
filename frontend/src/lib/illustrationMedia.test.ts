import { describe, expect, it } from "vitest";
import {
  illustrationLoraConfigurationError, illustrationRequestMedia, illustrationWorkflowMedia,
  resolveIllustrationActors, resolveVideoMode,
} from "./illustrationMedia";

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

  it("单模式双人高潮串联两份角色 LoRA 且不加载兜底风格", () => {
    expect(illustrationWorkflowMedia(
      { ...preset, loraMode: "single" }, ["乙", "甲"], ["甲", "乙"],
    )).toMatchObject({
      loras: [
        { name: "b.safetensors", weight: 1.1, character: true },
        { name: "a.safetensors", weight: 0.9, character: true },
      ],
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

  it.each(["冷倾雪", "虞妙玥", "任意角色"])(
    "事件角色丢失时从场景主体恢复任意角色绑定：%s",
    (actor) => {
      expect(resolveIllustrationActors(
        [], [{ name: actor, description: "adult woman" }], [actor, "另一角色"],
      )).toEqual([actor]);
    },
  );

  it.each([
    ["冷倾雪", "krea2_柳世熙.safetensors"],
    ["虞妙玥", "krea2_蔡秀晶.safetensors"],
    ["任意角色", "future-character.safetensors"],
  ])("从场景主体恢复后单 LoRA 精确选择绑定：%s", (actor, loraName) => {
    const config = {
      templateId: "tpl", loraMode: "single" as const, appearanceSource: "worldbook" as const,
      characterLoras: { [actor]: { loraName, loraWeight: 1 } },
    };
    const resolved = resolveIllustrationActors([], [{ name: actor }], [actor]);
    expect(illustrationWorkflowMedia(config, resolved, ["作品名"]).loras).toEqual([
      { name: loraName, weight: 1, character: true },
    ]);
  });

  it("世界书单 LoRA 无角色命中且无兜底时拒绝偷偷使用模板残留 LoRA", () => {
    const config = { ...preset, loraMode: "single" as const, styleLora: "" };
    const media = illustrationWorkflowMedia(config, ["丁"], ["白给谷"]);
    expect(illustrationLoraConfigurationError(config, media)).toContain("未命中角色 LoRA");
  });

  it("世界书多 LoRA 缺少默认风格时明确拒绝提交", () => {
    const config = { ...preset, loraMode: "multi" as const, styleLora: "" };
    const media = illustrationWorkflowMedia(config, ["甲"], ["白给谷"]);
    expect(illustrationLoraConfigurationError(config, media)).toContain("默认风格 LoRA");
  });
});

describe("resolveVideoMode（V1.5/B1 视频模式决策）", () => {
  it("事件 videoMode 优先于 preset", () => {
    expect(resolveVideoMode({ videoMode: "climax" }, "firstlast")).toBe("firstlast");
  });

  it("无事件字段时回退 preset.videoMode", () => {
    expect(resolveVideoMode({ videoMode: "firstlast" }, undefined)).toBe("firstlast");
  });

  it("两者都缺省 → climax（旧预设兼容）", () => {
    expect(resolveVideoMode(undefined, undefined)).toBe("climax");
    expect(resolveVideoMode({}, undefined)).toBe("climax");
  });

  it("非法事件值忽略，走 preset 回退", () => {
    expect(resolveVideoMode({ videoMode: "climax" }, "bogus")).toBe("climax");
  });
});
