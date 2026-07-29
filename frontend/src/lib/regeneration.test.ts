import { describe, expect, it } from "vitest";
import type { AiImageRegeneration } from "../types/chat";
import { resolveImageRegenerationModel, workflowRegenerationSnapshot } from "./regeneration";

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
});
