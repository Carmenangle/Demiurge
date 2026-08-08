import { describe, expect, it } from "vitest";

import {
  applyProfileLoraTriggers, illustrationTemplateValues, normalizePromptProfile,
  PROMPT_PROFILE_OPTIONS, replacePromptQualityLine, latentSizeFor,
} from "./imagePromptProfiles";

describe("image prompt profiles", () => {
  it("提供四种可选模式，旧配置默认Krea2智能判定", () => {
    expect(PROMPT_PROFILE_OPTIONS.map((item) => item.id)).toEqual([
      "krea2", "anima_tags", "natural_language", "niji_sections",
    ]);
    expect(normalizePromptProfile(undefined)).toBe("krea2");
    expect(normalizePromptProfile("unknown")).toBe("krea2");
    expect(normalizePromptProfile("anima_tags")).toBe("anima_tags");
  });

  it("Anima把当前LoRA触发词放在质量行最前", () => {
    expect(applyProfileLoraTriggers(
      "masterpiece, best quality\n1girl, red dress", "anima_tags", ["current_trigger"],
    )).toBe("current_trigger, masterpiece, best quality\n1girl, red dress");
  });

  it("Krea、自然语言与Niji都只前置当前LoRA触发词且不重复", () => {
    expect(applyProfileLoraTriggers("中文自然语言段落。", "krea2", ["style_x"]))
      .toBe("style_x, 中文自然语言段落。");
    expect(applyProfileLoraTriggers("style_x, 中文自然语言段落。", "natural_language", ["style_x"]))
      .toBe("style_x, 中文自然语言段落。");
    expect(applyProfileLoraTriggers("主体\n风格\n补充\n--stylize 400", "niji_sections", ["char_x"]))
      .toBe("char_x, 主体\n风格\n补充\n--stylize 400");
  });

  it("LoRA没有触发词时保持提示词不变", () => {
    expect(applyProfileLoraTriggers("prompt", "krea2", [])).toBe("prompt");
  });

  it("正负提示词分别注入对应语义字段", () => {
    const values = illustrationTemplateValues([
      { node_id: "39", field: "text", semantic: "text", binding: "prompt" },
      { node_id: "22", field: "text", semantic: "text", binding: "negative_prompt" },
      { node_id: "40", field: "lora_name", semantic: "lora_name", binding: "lora_name" },
      { node_id: "40", field: "strength_model", semantic: "strength_model", binding: "lora_weight" },
      { node_id: "12", field: "width", semantic: "width", binding: "latent_width" },
      { node_id: "12", field: "height", semantic: "height", binding: "latent_height" },
    ], {
      prompt: "best quality, 8k, high resolution\n1girl, solo, red dress",
      negativePrompt: "low quality, bad anatomy",
      loraName: "selected.safetensors",
      loraWeight: 0.7,
      latentSize: { width: 1408, height: 2048 },
    });

    expect(values).toEqual({
      "39.text": "best quality, 8k, high resolution\n1girl, solo, red dress",
      "22.text": "low quality, bad anatomy",
      "40.lora_name": "selected.safetensors",
      "40.strength_model": 0.7,
      "12.width": 1408,
      "12.height": 2048,
    });
  });

  it("不按同名字段猜测自动注入用途", () => {
    expect(illustrationTemplateValues([
      { node_id: "90", field: "width", semantic: "width" },
      { node_id: "91", field: "text", semantic: "text" },
    ], {
      prompt: "prompt",
      latentSize: { width: 1024, height: 768 },
    })).toEqual({});
  });

  it("兼容旧模板保存的语义别名", () => {
    expect(illustrationTemplateValues([
      { node_id: "40", field: "strength_model", semantic: "lora_weight" },
    ], { prompt: "prompt", loraName: "style.safetensors", loraWeight: 0.65 }))
      .toEqual({ "40.strength_model": 0.65 });
  });

  it("按Agent比例和用户最长边档位换算64对齐的Latent尺寸", () => {
    expect(latentSizeFor("1:1", 1024)).toEqual({ width: 1024, height: 1024 });
    expect(latentSizeFor("2:3", 1024)).toEqual({ width: 704, height: 1024 });
    expect(latentSizeFor("3:2", 2048)).toEqual({ width: 2048, height: 1408 });
    expect(latentSizeFor("9:16", 4096)).toEqual({ width: 2304, height: 4096 });
    expect(latentSizeFor("16:9", 4096)).toEqual({ width: 4096, height: 2304 });
  });

  it("非法比例和档位分别回退2比3与1K", () => {
    expect(latentSizeFor("21:9" as never, 1234 as never)).toEqual({
      width: 704, height: 1024,
    });
  });

  it("模板未暴露负面语义时保留工作流自己的负面策略", () => {
    expect(illustrationTemplateValues([
      { node_id: "39", field: "text", semantic: "text", binding: "prompt" },
    ], { prompt: "best quality\n1girl", negativePrompt: "low quality" }))
      .toEqual({ "39.text": "best quality\n1girl" });
  });

  it("固定质量词替换质量行后仍保留剧情内容行", () => {
    const prompt = replacePromptQualityLine(
      "old quality, score_8\n1girl, solo, white hair, looking at viewer. The reflected light defines her eyes.",
      "masterpiece, best quality, score_7, score_9",
    );

    expect(prompt).toBe(
      "masterpiece, best quality, score_7, score_9\n"
      + "1girl, solo, white hair, looking at viewer. The reflected light defines her eyes.",
    );
    expect(applyProfileLoraTriggers(prompt, "anima_tags", [
      "NJSW33T", "best quality", "rim light",
    ])).toBe(
      "NJSW33T, rim light, masterpiece, best quality, score_7, score_9\n"
      + "1girl, solo, white hair, looking at viewer. The reflected light defines her eyes.",
    );
  });

  it("Anima SFW清除成人质量词并丢弃中文附加行", () => {
    expect(replacePromptQualityLine(
      "old quality\n1girl, black hair. Rim light directs attention to her eyes.\n银发少年, 孤儿院",
      "masterpiece, sensitive, explicit, best quality",
      "sfw",
    )).toBe(
      "masterpiece, best quality\n"
      + "1girl, black hair. Rim light directs attention to her eyes.",
    );
  });

  it("Anima NSFW保留成人质量词且正文仍严格为英文", () => {
    expect(replacePromptQualityLine(
      "old quality\n1girl, solo. Warm rim light isolates the subject.",
      "masterpiece, sensitive, explicit, best quality",
      "nsfw",
    )).toBe(
      "masterpiece, sensitive, explicit, best quality\n"
      + "1girl, solo. Warm rim light isolates the subject.",
    );
  });

  it("未设置固定质量词时仍执行SFW两行清洗", () => {
    expect(replacePromptQualityLine(
      "masterpiece, sensitive, explicit, best quality\n1girl, solo. Her eyes hold the focal contrast.\n中文摘要",
      "",
      "sfw",
    )).toBe(
      "masterpiece, best quality\n"
      + "1girl, solo. Her eyes hold the focal contrast.",
    );
  });
});
