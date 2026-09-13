import { describe, expect, it } from "vitest";
import {
  illustrationLoraConfigurationError, illustrationRequestMedia, illustrationWorkflowMedia,
  resolveIllustrationActors, resolveVideoMode, resolveVideoTemplateChoice,
  planFirstlastFrameTasks, firstlastFrameValues, firstlastSlotLayout, transitionVideoValues,
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

describe("resolveVideoTemplateChoice（V1.5/B3 R4 触发闸门）", () => {
  const preset = (over: Record<string, unknown> = {}) => ({
    templateId: "img", videoTemplateId: "vid", smartVideo: true, ...over,
  });

  it("firstlast：配置了视频模板即触发，不看 motion/smartVideo（楼层触发）", () => {
    expect(resolveVideoTemplateChoice(preset(), "firstlast", 0)).toBe(true);
    expect(resolveVideoTemplateChoice(preset({ smartVideo: false }), "firstlast", 0)).toBe(true);
  });

  it("climax：维持 smartVideo && motion>=2", () => {
    expect(resolveVideoTemplateChoice(preset(), "climax", 1)).toBe(false);
    expect(resolveVideoTemplateChoice(preset(), "climax", 2)).toBe(true);
    expect(resolveVideoTemplateChoice(preset({ smartVideo: false }), "climax", 5)).toBe(false);
  });

  it("没配视频模板 → 永不触发视频（firstlast 也不例外）", () => {
    expect(resolveVideoTemplateChoice(preset({ videoTemplateId: undefined }), "firstlast", 5)).toBe(false);
    expect(resolveVideoTemplateChoice(preset({ videoTemplateId: undefined }), "climax", 5)).toBe(false);
  });
});

describe("transitionVideoValues（W3 转场视频 2 任务排队）", () => {
  const exposed = [
    { node_id: "3", field: "first", semantic: "first", binding: "first_frame_image" },
    { node_id: "7", field: "last", semantic: "last", binding: "last_frame_image" },
    { node_id: "9", field: "duration", semantic: "duration", binding: "video_duration" },
    { node_id: "10", field: "mode", semantic: "mode", binding: "video_mode" },
  ];
  const media = { negativePrompt: "bad", loraName: "style.safetensors", loraWeight: 0.7 };
  const latentSize = { width: 1024, height: 576 };

  it("图片1=上尾帧、图片2=当前首帧（转场起终点），videoMode 走 firstlast", () => {
    const values = transitionVideoValues(exposed, "转场提示词", media, latentSize, {
      prevTailDesc: "上一楼层尾帧", firstFrameDesc: "当前首帧",
      prevTailImage: "prev-tail.png", firstFrameImage: "curr-first.png",
    });
    expect(values["3.first"]).toBe("prev-tail.png");
    expect(values["7.last"]).toBe("curr-first.png");
    expect(values["10.mode"]).toBe("firstlast");
  });

  it("坑G：转场时长缺省 → 不注入 video_duration（交视频模型默认，不兑底正片时长）", () => {
    const values = transitionVideoValues(exposed, "转场", media, latentSize, {
      prevTailImage: "prev.png", firstFrameImage: "first.png",
    });
    expect("9.duration" in values).toBe(false);
  });

  it("坑G：transitionDurationHint 有值才写秒数", () => {
    const values = transitionVideoValues(exposed, "转场", media, latentSize, {
      transitionDurationHint: 3, prevTailImage: "prev.png", firstFrameImage: "first.png",
    });
    expect(values["9.duration"]).toBe(3);
  });
});

describe("planFirstlastFrameTasks（V1.6/P5 首尾帧顺序链·图来源计划）", () => {
  it("reuse + 有上尾帧图 → 首帧复用（零生图），尾帧用 lastFrameDesc 生成", () => {
    const plan = planFirstlastFrameTasks({
      transition: "reuse",
      prevTailUrl: "local://tail.png",
      firstFrameDesc: "面馆暖光",
      lastFrameDesc: "她抿了口汤",
    });
    expect(plan.tasks).toEqual([
      { frame: "first", kind: "reuse", imageUrl: "local://tail.png" },
      { frame: "last", kind: "generate", desc: "她抿了口汤" },
    ]);
    expect(plan.canGenerateVideo).toBe(true);
  });

  it("regenerate → 首帧用 firstFrameDesc 生成，尾帧生成（两帧都生图）", () => {
    const plan = planFirstlastFrameTasks({
      transition: "regenerate",
      prevTailUrl: "local://tail.png",
      firstFrameDesc: "雨夜面馆门口",
      lastFrameDesc: "灯笼摇晃",
    });
    expect(plan.tasks).toEqual([
      { frame: "first", kind: "generate", desc: "雨夜面馆门口" },
      { frame: "last", kind: "generate", desc: "灯笼摇晃" },
    ]);
    expect(plan.canGenerateVideo).toBe(true);
  });

  it("ambiguous → 视为独立生成（与 regenerate 同路径）", () => {
    const plan = planFirstlastFrameTasks({
      transition: "ambiguous",
      firstFrameDesc: "清晨车站",
      lastFrameDesc: "列车远去",
    });
    expect(plan.tasks[0]).toEqual({ frame: "first", kind: "generate", desc: "清晨车站" });
    expect(plan.canGenerateVideo).toBe(true);
  });

  it("事件 lastFrameUrl 有值 → 尾帧直接复用现成图（不浪费生图）", () => {
    const plan = planFirstlastFrameTasks({
      transition: "regenerate",
      firstFrameDesc: "船头",
      lastFrameUrl: "local://event-last.png",
    });
    expect(plan.tasks).toEqual([
      { frame: "first", kind: "generate", desc: "船头" },
      { frame: "last", kind: "existing", imageUrl: "local://event-last.png" },
    ]);
  });

  it("首帧无来源（无 reuse 图且无描述）→ canGenerateVideo=false（视频不可成片）", () => {
    expect(planFirstlastFrameTasks({ transition: "regenerate", lastFrameDesc: "只有尾帧" }).canGenerateVideo).toBe(false);
    expect(planFirstlastFrameTasks({ transition: "reuse" }).canGenerateVideo).toBe(false);
  });

  it("尾帧无来源（无 lastFrameUrl 且无描述）→ 尾帧缺图但首帧在仍可成片（降级首帧单图）", () => {
    const plan = planFirstlastFrameTasks({ transition: "regenerate", firstFrameDesc: "首帧" });
    expect(plan.tasks).toEqual([{ frame: "first", kind: "generate", desc: "首帧" }]);
    expect(plan.canGenerateVideo).toBe(true);
  });

  it("空白描述不产生 generate 任务（避免提交空 prompt 生图）", () => {
    const plan = planFirstlastFrameTasks({ transition: "regenerate", firstFrameDesc: "  " });
    expect(plan.tasks).toEqual([]);
    expect(plan.canGenerateVideo).toBe(false);
  });
});

describe("firstlastSlotLayout（V1.6/P5+ 独立图片模式槽位布局）", () => {
  it("regenerate 双帧：主槽=首帧新图，尾帧新图进 :last 副槽", () => {
    const plan = planFirstlastFrameTasks({ transition: "regenerate", firstFrameDesc: "首帧", lastFrameDesc: "尾帧" });
    expect(firstlastSlotLayout(plan)).toEqual({ main: "first_prompt", lastSlot: true });
  });

  it("regenerate 仅首帧描述：主槽=首帧新图，无副槽", () => {
    const plan = planFirstlastFrameTasks({ transition: "regenerate", firstFrameDesc: "首帧" });
    expect(firstlastSlotLayout(plan)).toEqual({ main: "first_prompt", lastSlot: false });
  });

  it("reuse + 尾帧新图：主槽=尾帧新图（本楼层唯一新画面）", () => {
    const plan = planFirstlastFrameTasks({ transition: "reuse", prevTailUrl: "local://prev.png", lastFrameDesc: "尾帧" });
    expect(firstlastSlotLayout(plan)).toEqual({ main: "last_prompt", lastSlot: false });
  });

  it("reuse + 事件尾帧现成图：主槽直接显示事件图", () => {
    const plan = planFirstlastFrameTasks({ transition: "reuse", prevTailUrl: "local://prev.png", lastFrameUrl: "local://last.png" });
    expect(firstlastSlotLayout(plan)).toEqual({ main: "last_frame_url", lastSlot: false });
  });

  it("reuse 无尾帧素材：主槽显示上尾帧图（画面延续）", () => {
    const plan = planFirstlastFrameTasks({ transition: "reuse", prevTailUrl: "local://prev.png" });
    expect(firstlastSlotLayout(plan)).toEqual({ main: "prev_tail_url", lastSlot: false });
  });

  it("无首帧仅尾帧可生成（图片模式放宽）：主槽=尾帧新图", () => {
    const plan = planFirstlastFrameTasks({ lastFrameDesc: "尾帧" });
    expect(firstlastSlotLayout(plan)).toEqual({ main: "last_prompt", lastSlot: false });
  });

  it("无任何帧素材：null（调用方拦截）", () => {
    expect(firstlastSlotLayout(planFirstlastFrameTasks({}))).toBeNull();
  });
});

describe("firstlastFrameValues（V1.6/P5 首尾帧生图模板 values）", () => {
  const exposed = [
    { node_id: "3", field: "text", semantic: "prompt", binding: "prompt" },
    { node_id: "4", field: "name", semantic: "lora", binding: "lora_name" },
    { node_id: "5", field: "weight", semantic: "lora_weight", binding: "lora_weight" },
    { node_id: "6", field: "image", semantic: "base_image", binding: "base_image" },
    { node_id: "7", field: "neg", semantic: "negative", binding: "negative_prompt" },
    { node_id: "8", field: "w", semantic: "latent_width", binding: "latent_width" },
    { node_id: "9", field: "h", semantic: "latent_height", binding: "latent_height" },
  ];

  it("prompt=帧画面描述，LoRA/底图/负面/latent 复用插画模板媒体", () => {
    expect(firstlastFrameValues(
      exposed, "雨夜面馆门口，灯笼摇晃",
      { negativePrompt: "nsfw", loraName: "role.safetensors", loraWeight: 1.1, baseImage: "role.png" },
      { width: 704, height: 1024 },
    )).toEqual({
      "3.text": "雨夜面馆门口，灯笼摇晃",
      "4.name": "role.safetensors",
      "5.weight": 1.1,
      "6.image": "role.png",
      "7.neg": "nsfw",
      "8.w": 704,
      "9.h": 1024,
    });
  });

  it("空媒体值不注入对应 binding（不覆盖模板原值）", () => {
    expect(firstlastFrameValues(exposed, "船头", {}, { width: 704, height: 1024 })).toEqual({
      "3.text": "船头",
      "8.w": 704,
      "9.h": 1024,
    });
  });
});
