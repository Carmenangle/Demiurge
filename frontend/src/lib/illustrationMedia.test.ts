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
      loraName: "style.safetensors", loraWeight: 0.7, baseImage: "", characterLora: false,
    });
  });
});
