import { describe, expect, it } from "vitest";
import type { AiImageRegeneration } from "../types/chat";
import {
  legacyGenerationPrompt, resolveImageRegenerationModel, templateRegenerationSnapshot,
  workflowRegenerationSnapshot,
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

  it("旧自动插画按图片地址取资产库原提示词", () => {
    expect(legacyGenerationPrompt("local://b", [
      { image_url: "local://a", prompt: "prompt a" },
      { image_url: "local://b", prompt: "  prompt b  " },
    ])).toBe("prompt b");
    expect(legacyGenerationPrompt("local://missing", [])).toBe("");
  });
});
