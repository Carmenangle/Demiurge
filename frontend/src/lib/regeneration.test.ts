import { describe, expect, it } from "vitest";
import type { AiImageRegeneration } from "../types/chat";
import {
  legacyGenerationPrompt, resolveImageRegenerationModel, templateRegenerationSnapshot,
  workflowRegenerationSnapshot,
  workflowModelName, workflowLoraNameList, workflowPositivePrompt, workflowGenMetadata,
} from "./regeneration";

describe("regeneration snapshots", () => {
  it("clones each workflow graph so later edits cannot change a result", () => {
    const graph = { "1": { class_type: "KSampler", inputs: { seed: 11 } } };
    const snapshot = workflowRegenerationSnapshot(graph, "http://127.0.0.1:8188", ["9"]);

    graph["1"].inputs.seed = 99;

    expect((snapshot.graph as typeof graph)["1"].inputs.seed).toBe(11);
    expect(snapshot.outputNodeIds).toEqual(["9"]);
  });

  it("resolves credentials only from the model bound to that result", () => {
    const snapshot: AiImageRegeneration = {
      kind: "ai-image",
      prompt: "original prompt",
      images: ["reference-a.png"],
      size: "1536x1024",
      quality: "high",
      model: { baseUrl: "https://original.example/v1", modelName: "image-original" },
    };
    const models = [
      { id: "current", baseUrl: "https://current.example/v1", apiKey: "wrong", modelName: "image-current" },
      { id: "original", baseUrl: "https://original.example/v1", apiKey: "right", modelName: "image-original" },
    ];

    expect(resolveImageRegenerationModel(snapshot, models)?.apiKey).toBe("right");
    expect(resolveImageRegenerationModel(snapshot, models.slice(0, 1))).toBeUndefined();
  });

  it("保存自动插画模板和值供原槽重新生成", () => {
    const values = { "39.text": "old prompt", "40.strength_model": 0.8 };
    const snapshot = templateRegenerationSnapshot(
      "tpl", values, "http://127.0.0.1:8188", ["45"], "old prompt",
    );
    values["39.text"] = "changed";

    expect(snapshot).toEqual({
      kind: "template",
      templateId: "tpl",
      values: { "39.text": "old prompt", "40.strength_model": 0.8 },
      comfyuiUrl: "http://127.0.0.1:8188",
      outputNodeIds: ["45"],
      prompt: "old prompt",
    });
  });

  it("角色 LoRA 生图时透传主角名，非角色 LoRA 不携带", () => {
    const args = (actor = "") => templateRegenerationSnapshot(
      "tpl", {}, "http://127.0.0.1:8188", ["45"], "prompt", [], "single", actor,
    );
    expect(args("虞妙玥").characterLoraActor).toBe("虞妙玥");
    expect(args().characterLoraActor).toBeUndefined();
  });

  it("旧自动插画按图片地址取资产库原提示词", () => {
    expect(legacyGenerationPrompt("local://b", [
      { image_url: "local://a", prompt: "prompt a" },
      { image_url: "local://b", prompt: "  prompt b  " },
    ])).toBe("prompt b");
    expect(legacyGenerationPrompt("local://missing", [])).toBe("");
  });
});

describe("workflow metadata extraction", () => {
  const basicGraph = {
    "1": { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "dreamshaper_8.safetensors" } },
    "2": { class_type: "CLIPTextEncode", inputs: { text: "masterpiece, 1girl" } },
    "3": { class_type: "CLIPTextEncode", inputs: { text: "worst quality" } },
    "4": { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0], model: ["1", 0] } },
    "5": { class_type: "VAEDecode", inputs: { samples: ["4", 0], vae: ["1", 0] } },
    "6": { class_type: "SaveImage", inputs: { images: ["5", 0] } },
    "7": { class_type: "LoraLoaderModelOnly", inputs: { lora_name: "add_detail.safetensors", strength_model: 0.8, model: ["1", 0] } },
  };

  it("extracts main model name from checkpoint loader", () => {
    expect(workflowModelName(basicGraph)).toBe("dreamshaper_8.safetensors");
  });

  it("extracts model name from UNETLoader", () => {
    const g = { "1": { class_type: "UNETLoader", inputs: { unet_name: "flux_dev.safetensors" } } };
    expect(workflowModelName(g)).toBe("flux_dev.safetensors");
  });

  it("returns empty for graph without loader", () => {
    expect(workflowModelName({ "1": { class_type: "KSampler" } })).toBe("");
  });

  it("extracts LoRA names from all lora loaders", () => {
    expect(workflowLoraNameList(basicGraph)).toEqual(["add_detail.safetensors"]);
  });

  it("extracts positive prompt from sampler positive chain", () => {
    expect(workflowPositivePrompt(basicGraph)).toBe("masterpiece, 1girl");
  });

  it("one-shot metadata extraction", () => {
    const meta = workflowGenMetadata("Krea2-高清", basicGraph);
    expect(meta.templateName).toBe("Krea2-高清");
    expect(meta.modelName).toBe("dreamshaper_8.safetensors");
    expect(meta.loraNames).toEqual(["add_detail.safetensors"]);
    expect(meta.prompt).toBe("masterpiece, 1girl");
  });

  // 2026-08-23：画布工具卡 captured 未回流（空/旧值）时回退 wfDraft（UI 格式）提取
  const uiDraft = {
    nodes: [
      { id: 14, type: "UNETLoader", widgets_values: ["Krea2-MuseByStable_v15Turbo_fp8.safetensors", "default"] },
      { id: 19, type: "LoraLoaderModelOnly", widgets_values: ["krea2_QRQ_韩漫风.safetensors", 0.8] },
      { id: 1, type: "CLIPTextEncode", title: "负面条件", widgets_values: ["mosaic, censored"] },
      { id: 18, type: "CLIPTextEncode", widgets_values: ["QRQ, masterpiece, very aesthetic, 1girl"] },
    ],
  };

  it("API 格式为空时回退 UI 格式（wfDraft）提取模型/LoRA/提示词", () => {
    const meta = workflowGenMetadata("Krea2-高清", {}, uiDraft);
    expect(meta.modelName).toBe("Krea2-MuseByStable_v15Turbo_fp8.safetensors");
    expect(meta.loraNames).toEqual(["krea2_QRQ_韩漫风.safetensors"]);
    // 优先非负面标题的 CLIPTextEncode
    expect(meta.prompt).toBe("QRQ, masterpiece, very aesthetic, 1girl");
  });

  it("API 格式优先于 UI 格式（不回退覆盖）", () => {
    const meta = workflowGenMetadata("t", basicGraph, uiDraft);
    expect(meta.modelName).toBe("dreamshaper_8.safetensors");
    expect(meta.prompt).toBe("masterpiece, 1girl");
  });
});
