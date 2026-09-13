import { describe, expect, it } from "vitest";

import {
  applyProfileLoraTriggers, ensureAnimaIllustrationStyle, illustrationTemplateValues, normalizePromptProfile,
  PROMPT_PROFILE_OPTIONS, replacePromptQualityLine, latentSizeFor,
} from "./imagePromptProfiles";

describe("image prompt profiles", () => {
  it("提供三种可选模式，旧 krea2 配置与未知值回退 Anima（krea2 已下线）", () => {
    expect(PROMPT_PROFILE_OPTIONS.map((item) => item.id)).toEqual([
      "anima_tags", "natural_language", "niji_sections",
    ]);
    expect(normalizePromptProfile(undefined)).toBe("anima_tags");  // krea2 下线后回退 Anima
    expect(normalizePromptProfile("unknown")).toBe("anima_tags");
    expect(normalizePromptProfile("krea2")).toBe("anima_tags");  // 旧 krea2 预设降级
    expect(normalizePromptProfile("anima_tags")).toBe("anima_tags");
  });

  it("Anima把当前LoRA触发词放在质量行最前", () => {
    expect(applyProfileLoraTriggers(
      "masterpiece, best quality\n1girl, red dress", "anima_tags", ["current_trigger"],
    )).toBe("current_trigger, masterpiece, best quality\n1girl, red dress");
  });

  it("Anima使用风格LoRA时机械锁定手绘二次元媒介", () => {
    expect(ensureAnimaIllustrationStyle(
      "masterpiece, anime coloring, adult woman, walking,\nAn adult woman walks through the street.",
      true,
    )).toBe(
      "masterpiece, anime coloring, adult woman, walking, anime illustration, "
      + "hand-drawn anime style, 2d cel shading, non-photorealistic,\n"
      + "An adult woman walks through the street.",
    );
    expect(ensureAnimaIllustrationStyle("quality\ncontent", false)).toBe("quality\ncontent");
  });

  it("Anima、自然语言与Niji都只前置当前LoRA触发词且不重复", () => {
    expect(applyProfileLoraTriggers("中文自然语言段落。", "anima_tags", ["style_x"]))
      .toBe("style_x, 中文自然语言段落。");
    expect(applyProfileLoraTriggers("style_x, 中文自然语言段落。", "natural_language", ["style_x"]))
      .toBe("style_x, 中文自然语言段落。");
    expect(applyProfileLoraTriggers("主体\n风格\n补充\n--stylize 400", "niji_sections", ["char_x"]))
      .toBe("char_x, 主体\n风格\n补充\n--stylize 400");
  });

  it("LoRA没有触发词时保持提示词不变", () => {
    expect(applyProfileLoraTriggers("prompt", "anima_tags", [])).toBe("prompt");
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

  it("固定质量词只替换质量tags并保留同一行的内容tags与第二行文段", () => {
    const prompt = replacePromptQualityLine(
      "old quality, score_8, 1girl, solo, white hair, looking at viewer,\n"
      + "An adult woman with white hair looks directly at the viewer.",
      "masterpiece, best quality, score_7, score_9",
    );

    expect(prompt).toBe(
      "masterpiece, best quality, score_7, score_9, 1girl, solo, white hair, looking at viewer,\n"
      + "An adult woman with white hair looks directly at the viewer.",
    );
    expect(applyProfileLoraTriggers(prompt, "anima_tags", [
      "NJSW33T", "best quality", "rim light",
    ])).toBe(
      "NJSW33T, rim light, masterpiece, best quality, score_7, score_9, 1girl, solo, white hair, looking at viewer,\n"
      + "An adult woman with white hair looks directly at the viewer.",
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

// ===== V1.2 视频最小事实：时长/镜头 binding =====
describe("illustrationTemplateValues · 视频字段", () => {
  it("video_duration / video_camera binding 注入预设值（V1.2 不从模型输出读）", () => {
    const exposed = [
      { node_id: "9", field: "duration", semantic: "duration", binding: "video_duration" },
      { node_id: "12", field: "camera", semantic: "camera", binding: "video_camera" },
    ];
    const values = illustrationTemplateValues(exposed, {
      prompt: "p", videoDuration: 5, videoCamera: "pan",
    });
    expect(values["9.duration"]).toBe(5);
    expect(values["12.camera"]).toBe("pan");
  });
  it("视频字段未配置 → 不进 values（模板原值生效，旧预设兼容）", () => {
    const exposed = [{ node_id: "9", field: "duration", semantic: "duration", binding: "video_duration" }];
    expect(illustrationTemplateValues(exposed, { prompt: "p" })).toEqual({});
  });
  it("B3 双帧图 binding：first_frame_image / last_frame_image 注入上传后的 ComfyUI 文件名", () => {
    const exposed = [
      { node_id: "3", field: "first", semantic: "first", binding: "first_frame_image" },
      { node_id: "7", field: "last", semantic: "last", binding: "last_frame_image" },
    ];
    const values = illustrationTemplateValues(exposed, {
      prompt: "p",
      firstFrameImage: "first.png",
      lastFrameImage: "last.png",
    });
    expect(values["3.first"]).toBe("first.png");
    expect(values["7.last"]).toBe("last.png");
  });
  it("B3 尾帧图缺省 → 只注入首帧（降级为首帧单图，不产生悬空引用）", () => {
    const exposed = [
      { node_id: "3", field: "first", semantic: "first", binding: "first_frame_image" },
      { node_id: "7", field: "last", semantic: "last", binding: "last_frame_image" },
    ];
    const values = illustrationTemplateValues(exposed, { prompt: "p", firstFrameImage: "first.png" });
    expect(values["3.first"]).toBe("first.png");
    expect("7.last" in values).toBe(false);
  });
});
