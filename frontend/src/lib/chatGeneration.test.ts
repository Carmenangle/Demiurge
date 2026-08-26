import { describe, it, expect } from "vitest";
import {
  needsImageInput, hasImageProvided, pickBestText, slimSnapshot, promptHistory,
  prependLoraTriggers, prepareConversationRegeneration, resolveGenerationPrompt,
  acceptSlimmedMessages, canCommitSnapshot,
  promptAdditionsForSelectedLora, triggersForSelectedLora,
  resolveLoraPromptMetadata,
  resolveVideoBaseImage,
  resolveVideoBaseImageRef,
  resolvePrevTailDesc,
  userMessageRichContent,
} from "./chatGeneration";
import {
  generationResultAction, notFoundPollAction, pendingResumeAction, pollSchedule,
  durableFinalizeSucceeded, registerPending, shouldFinalize, unregisterPending,
} from "./workflowGenerationRuntime";
import { serializeInspirationSend, type InspirationAttachment } from "./inspirationInsert";
import type { Template } from "../api/workflows";
import type { ChatMessage } from "../types/chat";

const tpl = (over: Partial<Template>): Template => ({
  id: "t1", name: "x", source_path: "", exposed: [],
  node_order: [], input_node_ids: [], output_node_ids: [],
  created_at: 0, updated_at: 0, ...over,
});

describe("needsImageInput", () => {
  it("有 image_node_id → 需要图", () => {
    expect(needsImageInput(tpl({ image_node_id: "5" }))).toBe(true);
  });
  it("exposed 里有 image 控件 → 需要图", () => {
    expect(needsImageInput(tpl({ exposed: [{ node_id: "5", field: "image", label: "", control: "image", semantic: "", default: null }] }))).toBe(true);
  });
  it("都没有 → 不需要", () => {
    expect(needsImageInput(tpl({}))).toBe(false);
  });
});

describe("hasImageProvided", () => {
  const t = tpl({ image_node_id: "5" });
  it("无图像输入口 → 放行", () => {
    expect(hasImageProvided({}, tpl({}))).toBe(true);
  });
  it("litegraph 结构：节点已填 widgets_values → true", () => {
    expect(hasImageProvided({ nodes: [{ id: 5, widgets_values: ["photo.png"] }] }, t)).toBe(true);
  });
  it("litegraph 结构：目标节点为空 → false", () => {
    expect(hasImageProvided({ nodes: [{ id: 5, widgets_values: [""] }] }, t)).toBe(false);
  });
  it("litegraph 结构：目标节点缺失 → false", () => {
    expect(hasImageProvided({ nodes: [{ id: 9, widgets_values: ["x"] }] }, t)).toBe(false);
  });
  it("API 结构：inputs 有非空值 → true", () => {
    expect(hasImageProvided({ "5": { inputs: { image: "photo.png" } } }, t)).toBe(true);
  });
  it("API 结构：inputs 全空 → false", () => {
    expect(hasImageProvided({ "5": { inputs: { image: "" } } }, t)).toBe(false);
  });
  it("两种结构都不匹配（如 null）→ 拿不准放行", () => {
    expect(hasImageProvided(null, t)).toBe(true);
  });
});

describe("pickBestText", () => {
  it("空/undefined → 空串", () => {
    expect(pickBestText(undefined)).toBe("");
    expect(pickBestText([])).toBe("");
  });
  it("过滤纯符号噪声段", () => {
    expect(pickBestText(["!@#$%^&*()", "有效的中文提示词"])).toBe("有效的中文提示词");
  });
  it("多段有效 → 取最长", () => {
    expect(pickBestText(["短", "更长的一段文本"])).toBe("更长的一段文本");
  });
  it("首尾空白被 trim", () => {
    expect(pickBestText(["  hello world  "])).toBe("hello world");
  });
});

describe("promptHistory", () => {
  it("只上传当前可见且有文本的对话消息", () => {
    const visible: ChatMessage[] = [
      { id: "u1", role: "user", text: "保留的用户消息" },
      { id: "empty", role: "assistant", text: "", image: "generated.png" },
      { id: "a1", role: "assistant", text: "", parts: [
        { type: "image", url: "generated.png" },
        { type: "media-slot", slotId: "slot-1", status: "ready" },
        { type: "text", text: "保留的助手消息" },
      ] },
    ];
    expect(promptHistory(visible)).toEqual([
      { role: "user", content: "保留的用户消息" },
      { role: "assistant", content: "保留的助手消息" },
    ]);
  });
  it("状态/Toast 与非剧情路由消息不进上下文", () => {
    const visible: ChatMessage[] = [
      { id: "u1", role: "user", text: "用户请求" },
      { id: "toast", role: "assistant", text: "已提交到 ComfyUI 生成…", system: true },
      { id: "gen", role: "assistant", text: "已生成图片", route: "generate" },
      { id: "story", role: "assistant", text: "剧情正文", route: "roleplay" },
      { id: "legacy", role: "assistant", text: "旧正文（无 route）" },
    ];
    expect(promptHistory(visible)).toEqual([
      { role: "user", content: "用户请求" },
      { role: "assistant", content: "剧情正文" },
      { role: "assistant", content: "旧正文（无 route）" },
    ]);
  });
  it("工作流媒体气泡（图/视频/音频提示词）不进上下文", () => {
    const visible: ChatMessage[] = [
      { id: "img", role: "assistant", text: "1girl, portrait", image: "/local-view?path=a.png" },
      { id: "vid", role: "assistant", text: "girl dancing", video: "/local-view?path=b.mp4" },
      { id: "aud", role: "assistant", text: "voiceover script", audio: "/local-view?path=c.wav" },
      { id: "story", role: "assistant", text: "剧情正文" },
    ];
    expect(promptHistory(visible)).toEqual([
      { role: "assistant", content: "剧情正文" },
    ]);
  });
});

describe("acceptSlimmedMessages", () => {
  it("异步瘦身完成前出现新消息时不得用旧数组覆盖", () => {
    const original: ChatMessage[] = [{ id: "a", role: "assistant", text: "完成" }];
    const current: ChatMessage[] = [
      ...original, { id: "u", role: "user", text: "下一条" },
    ];
    const slimmed: ChatMessage[] = [{ id: "a", role: "assistant", text: "完成" }];

    expect(acceptSlimmedMessages(current, original, slimmed)).toBe(current);
    expect(acceptSlimmedMessages(original, original, slimmed)).toBe(slimmed);
  });
});

describe("canCommitSnapshot", () => {
  it("异步瘦身期间追加用户消息后旧任务不得写本地或后端快照", () => {
    const original: ChatMessage[] = [{ id: "a", role: "assistant", text: "完成" }];
    const current: ChatMessage[] = [
      ...original, { id: "u", role: "user", text: "不能丢失的新消息" },
    ];

    expect(canCommitSnapshot(current, original, "repo-1", "repo-1")).toBe(false);
    expect(canCommitSnapshot(original, original, "repo-2", "repo-1")).toBe(false);
    expect(canCommitSnapshot(original, original, "repo-1", "repo-1")).toBe(true);
  });
});

describe("prepareConversationRegeneration", () => {
  it("保留所选用户消息、删除后续回复并完整恢复图文输入", () => {
    const messages: ChatMessage[] = [
      { id: "a1", role: "assistant", text: "前情" },
      {
        id: "u1", role: "user", text: "重跑本轮",
        parts: [
          { type: "image", url: "image.png" },
          { type: "masked-image", url: "preview.png", image: "base.png", mask: "mask.png" },
          { type: "text", text: "重跑本轮" },
        ],
      },
      { id: "a2", role: "assistant", text: "旧回复" },
      { id: "u2", role: "user", text: "后续消息" },
    ];

    const replay = prepareConversationRegeneration(messages, "u1");

    expect(replay?.history).toEqual([messages[0]]);
    expect(replay?.retained).toEqual(messages.slice(0, 2));
    expect(replay?.content).toEqual({
      text: "重跑本轮",
      images: ["image.png"],
      parts: messages[1].parts,
      maskedImage: { image: "base.png", mask: "mask.png", preview: "preview.png" },
    });
  });

  it("拒绝助手消息和不存在的消息", () => {
    const messages: ChatMessage[] = [{ id: "a1", role: "assistant", text: "回复" }];

    expect(prepareConversationRegeneration(messages, "a1")).toBeNull();
    expect(prepareConversationRegeneration(messages, "missing")).toBeNull();
  });
});

describe("prependLoraTriggers", () => {
  it("只把触发词插到质量行最前，内容行保持不变", () => {
    const prompt = "masterpiece, best quality, score_9\n1girl, blue hair, looking at viewer";
    expect(prependLoraTriggers(prompt, ["moby_d1ck"])).toBe(
      "moby_d1ck, masterpiece, best quality, score_9\n1girl, blue hair, looking at viewer",
    );
  });

  it("已有触发词时不重复插入", () => {
    const prompt = "moby_d1ck, masterpiece\n1girl, blue hair";
    expect(prependLoraTriggers(prompt, ["moby_d1ck"])).toBe(prompt);
  });

  it("触发词已在任意提示词段落出现时不重复前置", () => {
    const prompt = "masterpiece, best quality\nmoby_d1ck, 1girl, blue hair";
    expect(prependLoraTriggers(prompt, ["moby_d1ck"])).toBe(prompt);
  });
});

describe("LoRA 触发词精确绑定", () => {
  const items = [
    { lora_name: "old.safetensors", triggers: ["old_trigger"], missing: false },
    { lora_name: "selected.safetensors", triggers: ["selected_trigger"], missing: false },
    { lora_name: "empty.safetensors", triggers: [], missing: false },
  ];

  it("只返回本次最终选择 LoRA 的触发词", () => {
    expect(triggersForSelectedLora(items, "selected.safetensors")).toEqual(["selected_trigger"]);
  });

  it("只合并当前LoRA的触发词与作者建议提示词", () => {
    const data = [
      { ...items[0], suggested_prompt: "old quality" },
      { ...items[1], suggested_prompt: "masterpiece, best quality" },
    ];
    expect(promptAdditionsForSelectedLora(data, "selected.safetensors")).toEqual([
      "selected_trigger", "masterpiece", "best quality",
    ]);
    expect(promptAdditionsForSelectedLora(data, "missing.safetensors")).toEqual([]);
  });

  it("区分无需触发词的已记录LoRA与完全缺失的元数据", () => {
    expect(resolveLoraPromptMetadata(items, "empty.safetensors")).toEqual({
      found: true, additions: [],
    });
    expect(resolveLoraPromptMetadata(items, "missing.safetensors")).toEqual({
      found: false, additions: [],
    });
  });

  it("无需触发词的通用LoRA仍可注入中性质量建议", () => {
    const data = [{
      lora_name: "anime-character.safetensors", triggers: [], missing: false,
      suggested_prompt: "高质量, 清晰细节",
    }];

    expect(promptAdditionsForSelectedLora(data, "anime-character.safetensors")).toEqual([
      "高质量", "清晰细节",
    ]);
  });

  it("LoRA建议排除真人与二次元媒介锁定词", () => {
    const data = [{
      lora_name: "character.safetensors", triggers: [], missing: false,
      suggested_prompt: "high quality, sharp focus, photorealistic, realistic skin, anime style, donghua style",
    }];

    expect(promptAdditionsForSelectedLora(data, "character.safetensors")).toEqual([
      "high quality", "sharp focus",
    ]);
  });

  it("作者双段提示词只提取质量内容且已有触发词不重复", () => {
    const data = [{
      lora_name: "selected.safetensors", triggers: ["NJSW33T"], missing: false,
      suggested_prompt: [
        "NJSW33T, blurry background, depth of field, rim light, chiaroscuro, (anime coloring, flat color, sketch), (artist:pigeon666:0.67), by putimaxi",
        "NJSW33T, close-up, best quality, masterpiece, amazing quality, newest, very aesthetic, absurdres, 8k, good anatomy, ultra detailed, high resolution, (semi-realistic), sharp focus, ((donghua style))",
      ].join("\n\n"),
    }];

    const additions = promptAdditionsForSelectedLora(data, "selected.safetensors");

    expect(additions.filter((item) => item.toLowerCase() === "njsw33t")).toHaveLength(1);
    expect(additions).toContain("blurry background");
    expect(additions).toContain("(artist:pigeon666:0.67)");
    expect(additions).toContain("masterpiece");
    expect(additions).not.toContain("((donghua style))");
    expect(additions).not.toContain("close-up");
  });

  it("完整作者提示词排除人物服装动作，只保留质量与画风", () => {
    const data = [{
      lora_name: "selected.safetensors", triggers: ["style_trigger"], missing: false,
      suggested_prompt: "masterpiece, cinematic lighting, anime style, 1girl, red dress, kneeling, holding sword, forest",
    }];

    expect(promptAdditionsForSelectedLora(data, "selected.safetensors")).toEqual([
      "style_trigger", "masterpiece", "cinematic lighting",
    ]);
  });

  it("LoRA建议中的分级人物外貌与动作不得混入质量行", () => {
    const data = [{
      lora_name: "selected.safetensors", triggers: ["NJSW33T"], missing: false,
      suggested_prompt: [
        "NJSW33T, masterpiece, anime coloring, sensitive, explicit, fair skin",
        "1girl, red dress, kissing, kneeling, intimate scene",
      ].join("\n"),
    }];

    expect(promptAdditionsForSelectedLora(data, "selected.safetensors")).toEqual([
      "NJSW33T", "masterpiece",
    ]);
  });

  it("未记录、无触发词或文件已缺失时不回退旧记录", () => {
    expect(triggersForSelectedLora(items, "unknown.safetensors")).toEqual([]);
    expect(triggersForSelectedLora(items, "empty.safetensors")).toEqual([]);
    expect(triggersForSelectedLora([
      { lora_name: "selected.safetensors", triggers: ["stale"], missing: true },
      ...items.slice(0, 1),
    ], "selected.safetensors")).toEqual([]);
  });

  it("Krea成稿后才注入实际LoRA触发词和质量建议并全局去重", () => {
    const data = [
      {
        lora_name: "style.safetensors", triggers: ["style_token"], missing: false,
        suggested_prompt: "sharp focus, high quality",
      },
      {
        lora_name: "character.safetensors", triggers: ["character_token"], missing: false,
        suggested_prompt: "high quality, refined details",
      },
      {
        lora_name: "unused.safetensors", triggers: ["unused_token"], missing: false,
        suggested_prompt: "masterpiece",
      },
    ];
    let prompt = "A coherent English Krea2 scene with sharp focus.";
    for (const name of ["style.safetensors", "character.safetensors"]) {
      prompt = prependLoraTriggers(prompt, promptAdditionsForSelectedLora(data, name));
    }

    expect(prompt).toContain("style_token");
    expect(prompt).toContain("character_token");
    expect(prompt).toContain("high quality");
    expect(prompt).toContain("refined details");
    expect(prompt).not.toContain("unused_token");
    expect(prompt.toLowerCase().match(/sharp focus/g)).toHaveLength(1);
    expect(prompt.toLowerCase().match(/high quality/g)).toHaveLength(1);
  });
});

describe("生成资产提示词", () => {
  it("自动插画优先使用提交时持久化的提示词", () => {
    expect(resolveGenerationPrompt(
      "masterpiece\n1girl, climax action", undefined, "workflow text output",
    )).toBe("masterpiece\n1girl, climax action");
  });

  it("普通工作流兼容重生成快照与文本输出", () => {
    const regeneration = { kind: "workflow" as const, prompt: "saved prompt" };
    expect(resolveGenerationPrompt("", regeneration, "result text")).toBe("saved prompt");
    expect(resolveGenerationPrompt("", {
      kind: "template" as const, prompt: "inline prompt",
    }, "result text")).toBe("inline prompt");
    expect(resolveGenerationPrompt("", undefined, "result text")).toBe("result text");
  });
});

describe("slimSnapshot", () => {
  const persist = async (src: string) => (src.startsWith("data:") ? "local://x" : src);
  it("用户 parts 里的 data:URI 图被落盘转小地址", async () => {
    const msgs: ChatMessage[] = [{
      id: "1", role: "user", text: "",
      parts: [{ type: "image", url: "data:image/png;base64,AAAA" }, { type: "text", text: "hi" }],
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].parts?.[0]).toMatchObject({ type: "image", url: "local://x" });
    expect(out[0].parts?.[1]).toMatchObject({ type: "text", text: "hi" });
  });
  it("已执行的 portsPlan.images 被清空", async () => {
    const msgs: ChatMessage[] = [{
      id: "1", role: "assistant", text: "",
      portsPlan: { status: "applied", images: ["data:image/png;base64,AAAA"], ops: [] } as any,
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].portsPlan?.images).toEqual([]);
  });
  it("待执行的 portsPlan.images 被落盘保留", async () => {
    const msgs: ChatMessage[] = [{
      id: "1", role: "assistant", text: "",
      portsPlan: { status: "pending", images: ["data:image/png;base64,AAAA"], ops: [] } as any,
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].portsPlan?.images).toEqual(["local://x"]);
  });
  it("工作流 UI 草稿与执行图保持不变", async () => {
    const draftGraph = { nodes: [{ id: 51, properties: { selection_data: "new" } }], links: [[1]] };
    const capturedGraph = { "51": { class_type: "DanbooruGalleryNode", inputs: { bypass_prompts: "new" } } };
    const msgs: ChatMessage[] = [{
      id: "w", role: "assistant", text: "",
      workflow: { templateId: "t", templateName: "x", draftGraph, capturedGraph, done: true },
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].workflow?.draftGraph).toEqual(draftGraph);
    expect(out[0].workflow?.capturedGraph).toEqual(capturedGraph);
  });
  it("无图消息原样返回", async () => {
    const msgs: ChatMessage[] = [{ id: "1", role: "assistant", text: "纯文本" }];
    const out = await slimSnapshot(msgs, persist);
    expect(out).toEqual(msgs);
  });
  it("重生成快照中的参考图被落盘保留", async () => {
    const msgs: ChatMessage[] = [{
      id: "g", role: "assistant", text: "", image: "result.png",
      regeneration: {
        kind: "ai-image", prompt: "p", images: ["data:image/png;base64,AAAA"],
        size: "1024x1024", quality: "high",
        model: { baseUrl: "https://example.test/v1", modelName: "image" },
      },
    }];
    const out = await slimSnapshot(msgs, persist);
    expect(out[0].regeneration?.kind).toBe("ai-image");
    expect((out[0].regeneration as any).images).toEqual(["local://x"]);
  });
  it("蒙版附件与重生成参数中的原图和mask一起落盘", async () => {
    const data = "data:image/png;base64,AAAA";
    const msgs: ChatMessage[] = [{
      id: "masked", role: "user", text: "修改",
      parts: [{ type: "masked-image", url: data, image: data, mask: data }],
      regeneration: {
        kind: "ai-image", prompt: "修改", images: [data], imageMask: { image: data, mask: data },
        size: "1024x1024", quality: "high",
        model: { baseUrl: "https://example.test/v1", modelName: "image" },
      },
    }];

    const out = await slimSnapshot(msgs, persist);

    expect(out[0].parts?.[0]).toMatchObject({
      type: "masked-image", url: "local://x", image: "local://x", mask: "local://x",
    });
    expect((out[0].regeneration as any).imageMask).toEqual({
      image: "local://x", mask: "local://x",
    });
  });
});

describe("pending generation", () => {
  it("注册时去重后追加，且不修改输入", () => {
    const input = [
      { prompt_id: "p1", createdAt: 1 },
      { prompt_id: "p2", createdAt: 2 },
    ];
    expect(registerPending(input, "p1", 3)).toEqual([
      { prompt_id: "p2", createdAt: 2 },
      { prompt_id: "p1", createdAt: 3 },
    ]);
    expect(input).toEqual([
      { prompt_id: "p1", createdAt: 1 },
      { prompt_id: "p2", createdAt: 2 },
    ]);
  });
  it("工作流 pending 绑定自己的完整重生成快照", () => {
    const regeneration = {
      kind: "workflow" as const,
      graph: { "1": { class_type: "KSampler", inputs: { seed: 7 } } },
      comfyuiUrl: "http://127.0.0.1:8188",
      outputNodeIds: ["9"],
      prompt: "",
    };
    const out = registerPending([], "prompt-a", 1, ["9"], regeneration);
    expect(out[0].regeneration).toEqual(regeneration);
  });

  it("带主输出过滤时记录 outputNodeIds，空数组则省略该键", () => {
    expect(registerPending([], "p1", 1, ["47"])).toEqual([
      { prompt_id: "p1", createdAt: 1, outputNodeIds: ["47"] },
    ]);
    expect(registerPending([], "p1", 1, [])).toEqual([
      { prompt_id: "p1", createdAt: 1 },
    ]);
  });

  it("自动插画 pending 持久化目标消息与slot", () => {
    const regeneration = {
      kind: "template" as const,
      templateId: "tpl",
      values: { "39.text": "masterpiece\n1girl" },
      comfyuiUrl: "http://127.0.0.1:8188",
      outputNodeIds: ["45"],
      prompt: "masterpiece\n1girl",
    };
    expect(registerPending([], "p1", 1, ["45"], regeneration, {
      messageId: "bot-1", slotId: "slot-1", background: true,
    }, "masterpiece\n1girl, climax action")).toEqual([{
      prompt_id: "p1", createdAt: 1,
      outputNodeIds: ["45"],
      regeneration,
      target: { messageId: "bot-1", slotId: "slot-1", background: true },
      prompt: "masterpiece\n1girl, climax action",
    }]);
  });

  it("删除只移除指定任务并保持顺序", () => {
    const input = [
      { prompt_id: "p1", createdAt: 1 },
      { prompt_id: "p2", createdAt: 2 },
    ];
    expect(unregisterPending(input, "p1")).toEqual([{ prompt_id: "p2", createdAt: 2 }]);
    expect(unregisterPending(input, "missing")).toEqual(input);
  });

  it("恢复判定保持已处理和 30 分钟边界", () => {
    const item = { prompt_id: "p1", createdAt: 1000 };
    expect(pendingResumeAction(item, new Set(["p1"]), 1000)).toBe("skip");
    expect(pendingResumeAction(item, new Set(), 1000 + 30 * 60 * 1000)).toBe("inspect");
    expect(pendingResumeAction(item, new Set(), 1001 + 30 * 60 * 1000)).toBe("expire");
  });

  it.each([
    [149, false, 2000],
    [150, true, 15000],
    [151, false, 15000],
    [209, false, 15000],
    [210, false, null],
  ])("第 %i 次轮询维持原调度", (tries, releaseBusy, delayMs) => {
    expect(pollSchedule(tries)).toEqual({ releaseBusy, delayMs });
  });

  it("ComfyUI execution_error 是失败终态，不能继续轮询", () => {
    expect(generationResultAction("failed")).toBe("fail");
    expect(generationResultAction("completed")).toBe("complete");
    expect(generationResultAction("running")).toBe("poll");
  });

  it("完成瞬间短暂查不到 history 时继续轮询，连续五次才判丢失", () => {
    expect(notFoundPollAction(1)).toBe("retry");
    expect(notFoundPollAction(4)).toBe("retry");
    expect(notFoundPollAction(5)).toBe("fail");
  });
});

describe("shouldFinalize", () => {
  const pend = (...ids: string[]) => ids.map((prompt_id) => ({ prompt_id }));

  it("无 promptId → 直接放行（老路径兼容）", () => {
    expect(shouldFinalize(undefined, [], new Set())).toBe(true);
  });
  it("promptId 在 pending 且未收尾 → 放行", () => {
    expect(shouldFinalize("p1", pend("p1"), new Set())).toBe(true);
  });
  it("展示层误清 pending 时仍允许实时守望归档", () => {
    expect(shouldFinalize("p1", pend("p2"), new Set())).toBe(true);
  });
  it("promptId 在内存已收尾集合（并发窗口）→ 拦", () => {
    expect(shouldFinalize("p1", pend("p1"), new Set(["p1"]))).toBe(false);
  });
  it("pending 为空不代表用户取消", () => {
    expect(shouldFinalize("p1", [], new Set())).toBe(true);
  });
  it("并发收尾集合仍优先拦截", () => {
    expect(shouldFinalize("p1", pend("p2"), new Set(["p1"]))).toBe(false);
  });
  it("用户显式取消的任务不得归档", () => {
    expect(shouldFinalize("p1", pend("p1"), new Set(), new Set(["p1"]))).toBe(false);
  });
});

describe("durableFinalizeSucceeded", () => {
  it("原图或快照未持久化时要保留 pending 重试", () => {
    expect(durableFinalizeSucceeded({
      durable: true, images: [{ persisted: false, snapshotted: true }],
    })).toBe(false);
    expect(durableFinalizeSucceeded({
      durable: true, images: [{ persisted: true, snapshotted: false }],
    })).toBe(false);
    expect(durableFinalizeSucceeded({
      durable: true, images: [{ persisted: true, snapshotted: true }],
    })).toBe(true);
  });
});

// ===== V1.1 视频首帧底图来源解析 =====
describe("resolveVideoBaseImage", () => {
  const tplWithImage = { id: "v1", image_node_id: "9", exposed: [] } as any;
  const tplNoImage = { id: "v2", exposed: [] } as any;
  const msg = (id: string, parts: any[] = []) => ({ id, parts });
  const readyImg = (slotId: string, url: string) => ({ type: "image", slotId, status: "ready", url });
  const pendingSlot = (slotId: string) => ({ type: "media-slot", slotId, status: "pending" });

  it("模板无图像口 → 纯文生视频（返回 undefined，不拦截）", () => {
    expect(resolveVideoBaseImage({
      tpl: tplNoImage, messageId: "m1", slotId: "s1", messages: [],
    })).toBeUndefined();
  });

  it("本回合同槽已完成插画优先", () => {
    const messages = [
      msg("m1", [pendingSlot("s1"), readyImg("s1", "local://same-slot")]),
      msg("m0", [readyImg("s0", "local://older")]),
    ];
    expect(resolveVideoBaseImage({
      tpl: tplWithImage, messageId: "m1", slotId: "s1", messages,
    })).toBe("local://same-slot");
  });

  it("同槽 pending 不等待（时序红线：不阻塞）→ 回退最近一次已完成插画", () => {
    const messages = [
      msg("m1", [pendingSlot("s1")]),
      msg("m0", [readyImg("s0", "local://recent")]),
    ];
    expect(resolveVideoBaseImage({
      tpl: tplWithImage, messageId: "m1", slotId: "s1", messages,
    })).toBe("local://recent");
  });

  it("无已完成插画 → 用户手动指定底图", () => {
    const messages = [msg("m1", [pendingSlot("s1")])];
    expect(resolveVideoBaseImage({
      tpl: tplWithImage, messageId: "m1", slotId: "s1", messages,
      manualBaseImage: "local://manual",
    })).toBe("local://manual");
  });

  it("全无 → undefined（视频分支据此拦截 image_required）", () => {
    expect(resolveVideoBaseImage({
      tpl: tplWithImage, messageId: "m1", slotId: "s1", messages: [msg("m1")],
    })).toBeUndefined();
  });
});

describe("resolveVideoBaseImageRef（M2.1 底图来源槽引用）", () => {
  const tplWithImage = { id: "v1", image_node_id: "9", exposed: [] } as any;
  const msg = (id: string, parts: any[] = []) => ({ id, parts });

  it("本槽来源：返回 url + 当前消息/槽引用", () => {
    const r = resolveVideoBaseImageRef({
      tpl: tplWithImage, messageId: "m1", slotId: "s1",
      messages: [msg("m1", [{ type: "image", slotId: "s1", status: "ready", url: "local://img" }])],
    });
    expect(r).toEqual({ url: "local://img", sourceMessageId: "m1", sourceSlotId: "s1" });
  });

  it("历史槽来源：返回 url + 来源消息/槽引用（不误报为本槽）", () => {
    const r = resolveVideoBaseImageRef({
      tpl: tplWithImage, messageId: "m2", slotId: "s9",
      messages: [
        msg("m1", [{ type: "image", slotId: "s3", status: "ready", url: "local://older" }]),
        msg("m2", []),
      ],
    });
    expect(r).toEqual({ url: "local://older", sourceMessageId: "m1", sourceSlotId: "s3" });
  });

  it("手动底图：无槽引用（derived_from 不记录）", () => {
    const r = resolveVideoBaseImageRef({
      tpl: tplWithImage, messageId: "m1", slotId: "s1", messages: [],
      manualBaseImage: "local://manual",
    });
    expect(r).toEqual({ url: "local://manual" });
  });
});

describe("resolvePrevTailDesc（V1.5/B2 尾帧链式反查）", () => {
  const msg = (id: string, parts: any[] = []) => ({ id, parts });
  const video = (slotId: string, url: string, lastFrameDesc?: string) => ({
    type: "video", slotId, status: "ready" as const, url,
    ...(lastFrameDesc ? { lastFrameDesc } : {}),
  });
  const image = (slotId: string, url: string) =>
    ({ type: "image", slotId, status: "ready" as const, url });

  it("取最近一条已完成视频槽的尾帧描述", () => {
    expect(resolvePrevTailDesc([
      msg("m1", [video("s1", "local://v1", "上楼层尾帧：收伞进门")]),
      msg("m2", []),
    ])).toEqual({
      lastFrameDesc: "上楼层尾帧：收伞进门",
      messageId: "m1", slotId: "s1",
    });
  });

  it("最近已完成视频槽无尾帧描述 → undefined（不跳过取更早楼层，避免跨楼层过时尾帧）", () => {
    expect(resolvePrevTailDesc([
      msg("m0", [video("s0", "local://v0", "更早楼层尾帧")]),
      msg("m1", [video("s1", "local://v1")]),
    ])).toBeUndefined();
  });

  it("图片槽不参与反查，只认视频槽", () => {
    expect(resolvePrevTailDesc([
      msg("m1", [
        image("s1", "local://img"),
        video("s2", "local://v2", "尾帧描述"),
      ]),
    ])).toEqual({ lastFrameDesc: "尾帧描述", messageId: "m1", slotId: "s2" });
  });

  it("无任何已完成视频槽 → undefined", () => {
    expect(resolvePrevTailDesc([
      msg("m1", [{ type: "media-slot", slotId: "s1", status: "pending" }]),
      msg("m2", []),
    ])).toBeUndefined();
    expect(resolvePrevTailDesc([])).toBeUndefined();
  });
});

describe("userMessageRichContent（灵感卡编辑回填还原）", () => {
  const card: InspirationAttachment = {
    id: "c1", title: "女仆装", content: "主流款式…",
    imageUrl: "https://x/cover.png", sourceUrl: "https://x/cover.png",
  };

  it("有灵感卡附件时：还原卡片 + 拆回纯用户文本/图（不重复）", () => {
    const s = serializeInspirationSend([card], "你好", ["https://x/u1.png"]);
    const msg: ChatMessage = {
      id: "m1", role: "user", text: s.text,
      parts: [
        ...s.images.map((url) => ({ type: "image" as const, url })),
        { type: "text" as const, text: s.text },
      ],
      inspirationAttachments: [card],
    };
    const c = userMessageRichContent(msg);
    expect(c.text).toBe("你好");
    expect(c.images).toEqual(["https://x/u1.png"]);
    expect(c.inspirationAttachments).toEqual([card]);
    // parts 里不得残留灵感卡封面图或卡片文本
    expect(c.parts).not.toContainEqual({ type: "image", url: "https://x/cover.png" });
    expect(JSON.stringify(c.parts)).not.toContain("【灵感参考");
  });

  it("无灵感卡附件时：文本/图片原样返回，不生成附件", () => {
    const msg: ChatMessage = {
      id: "m2", role: "user", text: "你好",
      parts: [{ type: "text", text: "你好" }, { type: "image", url: "https://x/a.png" }],
    };
    const c = userMessageRichContent(msg);
    expect(c.text).toBe("你好");
    expect(c.images).toEqual(["https://x/a.png"]);
    expect(c.inspirationAttachments).toBeUndefined();
  });
});
