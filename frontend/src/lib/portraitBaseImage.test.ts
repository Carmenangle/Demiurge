// 第 92 刀 · 角色底图一键定妆（前端提交与回收链）
//
// 用户规格原话：「角色底图在没有的情况下，但是有绑定多元数据插入的生图工作流模版时，
// 可以利用该工作流一键生成，生成时会参考角色条目外貌，使用绑定的角色lora和风格lora去生成」。
//
// 本文件锁三件事：
// 1. 可用性判据只有一处（`portraitGateReason`）——面板按它禁用按钮，链按它拒绝开工；
//    判据写两遍就会「面板放行、链里死」或反之（本仓人称口径的同族教训）。
// 2. LoRA／底图／values 的口径**逐字复用**插画链的两个真源（`illustrationWorkflowMedia`
//    与 `illustrationTemplateValues`），定妆只把该角色一人当 actors 传进去。
// 3. 提交→轮询→落盘任一环失败都不写回底图（半途成功会把坏图钉成该角色的长期锚点）。
// 4. 生效 LoRA 的触发词＋建议提示词由程序机械前置（吃 `applyLoraTriggerWords` 真源）：
//    Profile 侧明令模型不许写触发词，不前置就等于挂了 LoRA 却不生效。
import { describe, expect, it } from "vitest";
import {
  PORTRAIT_BASE_IMAGE_SUBDIR, generatePortraitBaseImage,
  portraitAppearanceOriginText, portraitCameraViolation, portraitGateReason,
} from "./portraitBaseImage";
import type { SemanticField } from "./imagePromptProfiles";

const exposed: SemanticField[] = [
  { node_id: "3", field: "text", semantic: "prompt", binding: "prompt" },
  { node_id: "4", field: "neg", semantic: "negative", binding: "negative_prompt" },
  { node_id: "5", field: "name", semantic: "lora", binding: "lora_name" },
  { node_id: "6", field: "weight", semantic: "lora_weight", binding: "lora_weight" },
  { node_id: "7", field: "image", semantic: "base_image", binding: "base_image" },
  { node_id: "8", field: "w", semantic: "latent_width", binding: "latent_width" },
  { node_id: "9", field: "h", semantic: "latent_height", binding: "latent_height" },
];
const template = { id: "tpl-1", exposed };
const preset = {
  templateId: "tpl-1",
  loraMode: "multi" as const,
  appearanceSource: "worldbook" as const,
  negativePrompt: "lowres, bad anatomy",
  characterLoras: { 甲: { loraName: "role_a.safetensors", loraWeight: 0.9 } },
  styleLora: "style.safetensors",
  styleLoraWeight: 0.6,
};
const chat = { baseUrl: "http://x/v1", apiKey: "k", modelName: "m" };
const runtime = { comfyUrl: "http://127.0.0.1:8188", chat };

// LoRA 触发词表夹具（真实 wire 形状的子集）：风格那条带作者建议提示词，角色那条只有触发词。
const LORA_ITEMS = [
  { lora_name: "style.safetensors", triggers: ["style_x"], missing: false, suggested_prompt: "masterpiece" },
  { lora_name: "role_a.safetensors", triggers: ["char_a"], missing: false, suggested_prompt: "" },
];
// multi 栈＝[风格, 角色]，逐条前置 ⇒ 后处理的角色触发词落在最前面。
const TRIGGERED_PROMPT = "char_a, style_x, masterpiece, best quality\n1girl, solo";

// 判据夹具：与面板传给人家的字段一一对应（名字／模板／草稿预设／链路配置）。
const gate = (patch: Record<string, unknown> = {}) => ({
  name: "甲", template, preset, ...runtime, ...patch,
});

const args = (patch: Record<string, unknown> = {}) => ({
  name: "甲",
  repoId: "repo-1",
  outputDir: "D:/works",
  comfyUrl: "http://127.0.0.1:8188",
  chat,
  preset,
  template,
  templateName: "测试生图模板",
  promptProfile: "anima_tags",
  ...patch,
});

/** 零网络依赖：六段 I/O 全部注入，调用顺序与入参即本链的合同。 */
function harness(opts: {
  prompt?: string;
  appearanceOrigin?: string;
  pose?: string;
  loraItems?: { lora_name: string; triggers: string[]; missing: boolean; suggested_prompt: string }[];
  poll?: (promptId: string, url: string, mediaType: string) => Promise<unknown>;
  submit?: (templateId: string, values: Record<string, unknown>) => Promise<{ ok: boolean; prompt_id?: string }>;
  save?: (a: Record<string, unknown>) => Promise<{ ok: boolean; path: string }>;
  index?: (input: Record<string, unknown>) => Promise<void>;
} = {}) {
  const calls: { step: string; args: unknown[] }[] = [];
  const deps = {
    genPrompt: (async (a: unknown) => {
      calls.push({ step: "genPrompt", args: [a] });
      return {
        prompt: opts.prompt === undefined ? "best quality\n1girl, solo" : opts.prompt,
        appearance_origin: opts.appearanceOrigin ?? "entry",
        pose: opts.pose ?? "双手背在身后、手指自然交叠",
      };
    }) as never,
    listLoras: (async () => {
      calls.push({ step: "listLoras", args: [] });
      return { items: opts.loraItems ?? LORA_ITEMS };
    }) as never,
    submit: (async (templateId: string, values: Record<string, unknown>, url: string, prompt: string,
      loras: unknown, loraMode: string) => {
      calls.push({ step: "submit", args: [templateId, values, url, prompt, loras, loraMode] });
      if (opts.submit) return opts.submit(templateId, values);
      return { ok: true, prompt_id: "pid-1" };
    }) as never,
    poll: (async (promptId: string, url: string, mediaType: string) => {
      calls.push({ step: "poll", args: [promptId, url, mediaType] });
      if (opts.poll) return opts.poll(promptId, url, mediaType);
      return { kind: "complete", result: { images: [{ filename: "a_00001_.png", subfolder: "", type: "output" }] } };
    }) as never,
    save: (async (a: Record<string, unknown>) => {
      calls.push({ step: "save", args: [a] });
      if (opts.save) return opts.save(a);
      return { ok: true, path: "D:/works/repo-1/portraits/00001.png" };
    }) as never,
    index: (async (input: Record<string, unknown>) => {
      calls.push({ step: "index", args: [input] });
      if (opts.index) return opts.index(input);
    }) as never,
  };
  const byStep = (name: string) => calls.find((c) => c.step === name)?.args ?? [];
  return { deps, calls, byStep };
}

describe("一键定妆可用性判据", () => {
  it("角色名／生图模板／提示词槽／链路配置齐备 ⇒ 可定妆", () => {
    expect(portraitGateReason(gate())).toBe("");
  });

  it("缺角色名／缺模板／模板没有提示词槽各自给出中文原因", () => {
    expect(portraitGateReason(gate({ name: "   " }))).toContain("角色名");
    expect(portraitGateReason(gate({ template: undefined }))).toContain("模板");
    expect(portraitGateReason(gate({
      template: { id: "tpl-1", exposed: exposed.filter((f) => f.binding !== "prompt") },
    }))).toContain("提示词");
  });

  it("模板未选择（id 为空）同样判不可用，不把空模板 id 提交出去", () => {
    expect(portraitGateReason(gate({ template: { id: "", exposed } }))).toContain("模板");
  });

  it("没配 ComfyUI 地址／没选对话模型 ⇒ 判不可用，不发一次注定失败的提交", () => {
    expect(portraitGateReason(gate({ comfyUrl: "" }))).toContain("ComfyUI");
    expect(portraitGateReason(gate({ chat: { baseUrl: "", apiKey: "", modelName: "" } })))
      .toContain("模型");
  });

  it("外貌来源账本五档都有中文回显（面板据此报「外貌来自哪」）", () => {
    for (const origin of ["entry", "anchor", "table", "merged", "none"]) {
      expect(portraitAppearanceOriginText(origin)).not.toBe("");
    }
    expect(portraitAppearanceOriginText("none")).toContain("未取到");
  });
});

describe("定妆机位合同（第 95 刀）", () => {
  it("背身机位措辞一律判违约（中英文都要拦）", () => {
    for (const bad of [
      "1girl, solo, three-quarter back view",
      "1girl, solo, from behind",
      "1girl, solo, rear view",
      "1girl, solo, back view",
      "1girl, solo, head turned back over shoulder",
      "1girl, solo, looking back over her shoulder",
      "1girl, solo, 背对镜头",
      "1girl, solo, 回望镜头",
    ]) {
      expect(portraitCameraViolation(bad)).not.toBe("");
    }
  });

  it("正面看向镜头的成稿不误判（含背景、侧身等合法措辞）", () => {
    expect(portraitCameraViolation(
      "1girl, solo, front view, looking at viewer, full body, simple background",
    )).toBe("");
    expect(portraitCameraViolation(
      "她正面朝向镜头，双眼看向镜头，背景是纯色影棚。",
    )).toBe("");
  });
});

describe("一键定妆成功链", () => {
  it("后端入参带作品根与角色名，底图默认走 sfw 立绘口径", async () => {
    const { deps, calls } = harness();
    await generatePortraitBaseImage(args(), deps);
    expect(calls[0].args[0]).toMatchObject({
      name: "甲", repoId: "repo-1", outputDir: "D:/works",
      profile: "anima_tags", rating: "sfw", aspectRatio: "9:16",
      appearanceSource: "worldbook",
      chat,
    });
  });

  it("values 复用插画链口径：触发词前置后的提示词／负面／角色＋风格 LoRA／底图／latent 尺寸", async () => {
    const { deps, byStep } = harness();
    await generatePortraitBaseImage(args(), deps);
    const [templateId, values, url, prompt, loras, loraMode] = byStep("submit") as [
      string, Record<string, unknown>, string, string, { name: string; weight: number }[], string,
    ];
    expect(templateId).toBe("tpl-1");
    expect(url).toBe("http://127.0.0.1:8188");
    expect(prompt).toBe(TRIGGERED_PROMPT);
    expect(loras).toEqual([
      { name: "style.safetensors", weight: 0.6 },
      { name: "role_a.safetensors", weight: 0.9 },
    ]);
    expect(loraMode).toBe("multi");
    expect(values).toEqual({
      "3.text": TRIGGERED_PROMPT,
      "4.neg": "lowres, bad anatomy",
      "5.name": "style.safetensors",
      "6.weight": 0.6,
      "8.w": 576,
      "9.h": 1024,
    });
  });

  it("触发词＋建议提示词逐条前置到质量行最前（吃 applyLoraTriggerWords 真源，不由模型写）", async () => {
    const { deps, calls, byStep } = harness();
    const out = await generatePortraitBaseImage(args(), deps);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt", "listLoras", "submit", "poll", "save", "index"]);
    expect(byStep("submit")[3]).toBe(TRIGGERED_PROMPT);
    expect(out.prompt).toBe(TRIGGERED_PROMPT);
  });

  it("生效 LoRA 缺精确元数据记录 ⇒ 抛错且不提交 ComfyUI（不静默出一张不像本人的底图）", async () => {
    const { deps, calls } = harness({
      loraItems: [LORA_ITEMS[0]],  // 只有风格那条，角色那条没登记
    });
    await expect(generatePortraitBaseImage(args(), deps))
      .rejects.toThrow(/实际加载的 LoRA 没有精确元数据记录：role_a\.safetensors/);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt", "listLoras"]);
  });

  it("loraMode=none（空栈）⇒ 不查触发词表，一次多余请求都不发", async () => {
    const { deps, calls, byStep } = harness();
    await generatePortraitBaseImage(args({ preset: { ...preset, loraMode: "none" as const } }), deps);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt", "submit", "poll", "save", "index"]);
    expect(byStep("submit")[3]).toBe("best quality\n1girl, solo");
    expect(byStep("submit")[4]).toEqual([]);
  });

  it("无角色底图时按预设回退风格底图（与自动插画同一真源，不自建第二套）", async () => {
    const { deps, byStep } = harness();
    await generatePortraitBaseImage(args({
      preset: { ...preset, styleBaseImage: "D:/works/repo-1/reference/style.png" },
    }), deps);
    const values = byStep("submit")[1] as Record<string, unknown>;
    expect(values["7.image"]).toBe("D:/works/repo-1/reference/style.png");
  });

  it("最长边预设生效到 latent 尺寸（9:16 @ 2048 ⇒ 1152×2048）", async () => {
    const { deps, byStep } = harness();
    await generatePortraitBaseImage(args({ preset: { ...preset, latentLongEdge: 2048 } }), deps);
    const values = byStep("submit")[1] as Record<string, unknown>;
    expect(values["8.w"]).toBe(1152);
    expect(values["9.h"]).toBe(2048);
  });

  it("轮询按图片口径等待，产物落 portraits 子夹并回传路径、来源与本次动作", async () => {
    const { deps, byStep } = harness();
    const out = await generatePortraitBaseImage(args(), deps);
    expect(byStep("poll")).toEqual(["pid-1", "http://127.0.0.1:8188", "image"]);
    expect(byStep("save")[0]).toEqual({
      img: { filename: "a_00001_.png", subfolder: "", type: "output" },
      repoId: "repo-1",
      outputDir: "D:/works",
      url: "http://127.0.0.1:8188",
      subdir: PORTRAIT_BASE_IMAGE_SUBDIR,
    });
    expect(out).toEqual({
      path: "D:/works/repo-1/portraits/00001.png",
      prompt: TRIGGERED_PROMPT,
      appearanceOrigin: "entry",
      pose: "双手背在身后、手指自然交叠",
      indexed: true,
    });
  });

  it("产物按 local-view 口径回传资产库入参（模板名与 LoRA 栈随图入库）", async () => {
    const { deps, byStep } = harness();
    await generatePortraitBaseImage(args(), deps);
    expect(byStep("index")[0]).toEqual({
      repoId: "repo-1",
      prompt: TRIGGERED_PROMPT,
      imagePath: "D:/works/repo-1/portraits/00001.png",
      templateName: "测试生图模板",
      loraNames: "style.safetensors,role_a.safetensors",
    });
  });

  it("资产库入库失败 ⇒ 底图仍算生成成功（indexed=false），不因入库拖垮绑定", async () => {
    const { deps, calls } = harness({ index: async () => { throw new Error("embedding 不可用"); } });
    const out = await generatePortraitBaseImage(args(), deps);
    expect(out.path).toBe("D:/works/repo-1/portraits/00001.png");
    expect(out.indexed).toBe(false);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt", "listLoras", "submit", "poll", "save", "index"]);
  });
});

describe("一键定妆失败面", () => {
  it("判据不过 ⇒ 一次网络请求都不发", async () => {
    const { deps, calls } = harness();
    await expect(generatePortraitBaseImage(args({ name: "" }), deps)).rejects.toThrow(/角色名/);
    expect(calls).toEqual([]);
  });

  it("后端没编排出提示词 ⇒ 不提交 ComfyUI", async () => {
    const { deps, calls } = harness({ prompt: "   " });
    await expect(generatePortraitBaseImage(args(), deps)).rejects.toThrow(/提示词/);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt"]);
  });

  it("成稿出现背身机位 ⇒ 一次提交都不发，更不落盘（底图不能被钉成背身）", async () => {
    const { deps, calls } = harness({
      prompt: "best quality\n1girl, solo, three-quarter back view",
    });
    await expect(generatePortraitBaseImage(args(), deps)).rejects.toThrow(/背身机位/);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt"]);
  });

  it("ComfyUI 没返回任务 id ⇒ 不轮询、不落盘", async () => {
    const { deps, calls } = harness({ submit: async () => ({ ok: true }) });
    await expect(generatePortraitBaseImage(args(), deps)).rejects.toThrow(/任务 ID/);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt", "listLoras", "submit"]);
  });

  it("提交抛错原样上抛（后端 422 的 LoRA 槽原因不能被吞成通用失败）", async () => {
    const deps = {
      genPrompt: (async () => ({ prompt: "x", appearance_origin: "entry", pose: "" })) as never,
      listLoras: (async () => ({ items: LORA_ITEMS })) as never,
      submit: (async () => { throw new Error("模板没有已标注且受支持的 LoRA 加载器"); }) as never,
      poll: (async () => ({ kind: "complete", result: { images: [] } })) as never,
      save: (async () => ({ ok: true, path: "" })) as never,
      index: (async () => {}) as never,
    };
    await expect(generatePortraitBaseImage(args(), deps)).rejects.toThrow(/LoRA 加载器/);
  });

  it("轮询失败／停顿上抛原因，硬超时给出可行动文案，三种都不落盘", async () => {
    const cases: [Record<string, unknown>, RegExp][] = [
      [{ kind: "failed", error: "节点执行失败" }, /节点执行失败/],
      [{ kind: "stalled", error: "长时间未开始加载节点（ComfyUI 队列未运转），已停止" }, /队列未运转/],
      [{ kind: "still_running" }, /仍在/],
    ];
    for (const [outcome, matcher] of cases) {
      const { deps, calls } = harness({ poll: async () => outcome });
      await expect(generatePortraitBaseImage(args(), deps)).rejects.toThrow(matcher);
      expect(calls.map((c) => c.step)).toEqual(["genPrompt", "listLoras", "submit", "poll"]);
    }
  });

  it("任务完成但没有产物图 ⇒ 不落盘", async () => {
    const { deps, calls } = harness({ poll: async () => ({ kind: "complete", result: { images: [] } }) });
    await expect(generatePortraitBaseImage(args(), deps)).rejects.toThrow(/没有产出图片/);
    expect(calls.map((c) => c.step)).toEqual(["genPrompt", "listLoras", "submit", "poll"]);
  });

  it("落盘没返回路径 ⇒ 判失败（不把空路径写回底图）", async () => {
    const { deps } = harness({ save: async () => ({ ok: false, path: "" }) });
    await expect(generatePortraitBaseImage(args(), deps)).rejects.toThrow(/保存/);
  });

  it("判据不过时连后端都不问：无模板与无提示词槽两条都短路", async () => {
    const { deps, calls } = harness();
    await expect(generatePortraitBaseImage(args({ template: { id: "", exposed } }), deps))
      .rejects.toThrow(/模板/);
    await expect(generatePortraitBaseImage(
      args({ template: { id: "tpl-1", exposed: [] } }), deps,
    )).rejects.toThrow(/提示词/);
    expect(calls).toEqual([]);
  });
});
