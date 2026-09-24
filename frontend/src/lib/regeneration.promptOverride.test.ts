import { describe, expect, it } from "vitest";
import type { RegenerationSnapshot } from "../types/chat";
import { overrideGraphPrompt, overrideRegenerationPrompt } from "./regeneration";

/**
 * 「重新生图（含提示词）」的核心逻辑。
 *
 * ⚠ 三种快照的**提示词真源不同**——只改 `prompt` 字段在 template / workflow 下
 * **不影响出图**（用户会看到「改了提示词但图没变」）。这里逐 kind 钉住真源被改写。
 */

const template = (values: Record<string, unknown>): RegenerationSnapshot => ({
  kind: "template",
  templateId: "tpl-1",
  values,
  comfyuiUrl: "http://127.0.0.1:8188",
  outputNodeIds: ["9"],
  prompt: "旧提示词",
});

const aiImage: RegenerationSnapshot = {
  kind: "ai-image",
  prompt: "旧提示词",
  images: [],
  size: "1024x1024",
  quality: "auto",
  model: { baseUrl: "http://x", modelName: "m" },
};

const apiGraph = {
  "3": { class_type: "KSampler", inputs: { positive: ["6", 0], negative: ["7", 0] } },
  "6": { class_type: "CLIPTextEncode", inputs: { text: "旧正向", clip: ["1", 1] } },
  "7": { class_type: "CLIPTextEncode", inputs: { text: "旧负面", clip: ["1", 1] } },
};
const workflow: RegenerationSnapshot = {
  kind: "workflow",
  graph: apiGraph,
  comfyuiUrl: "http://127.0.0.1:8188",
  outputNodeIds: ["9"],
  prompt: "旧提示词",
};

describe("overrideRegenerationPrompt", () => {
  it("ai-image：prompt 字段即真源，改字段", () => {
    const next = overrideRegenerationPrompt(aiImage, "新提示词");
    expect(next.kind === "ai-image" && next.prompt).toBe("新提示词");
  });

  it("template：必须同时改写 values 里绑到 prompt 的键（只改字段不出图）", () => {
    const next = overrideRegenerationPrompt(
      template({ "6.text": "旧提示词", "4.ckpt_name": "model.safetensors", "7.text": "负面" }),
      "新提示词",
      [
        { node_id: "6", field: "text", semantic: "prompt" },
        { node_id: "4", field: "ckpt_name", semantic: "ckpt" },
        { node_id: "7", field: "text", semantic: "negative_prompt" },
      ],
    );
    expect(next.kind === "template" && next.prompt).toBe("新提示词");
    expect(next.kind === "template" && next.values).toEqual({
      "6.text": "新提示词",
      "4.ckpt_name": "model.safetensors",
      "7.text": "负面",       // 负面提示词不得被覆盖
    });
  });

  it("template：模板未暴露 prompt 键时保留原 values（出图退化，但记录新提示词）", () => {
    const next = overrideRegenerationPrompt(template({ "4.ckpt_name": "m" }), "新提示词", []);
    expect(next.kind === "template" && next.prompt).toBe("新提示词");
    expect(next.kind === "template" && next.values).toEqual({ "4.ckpt_name": "m" });
  });

  it("workflow：提示词烧在图里，改图里正向节点、不动负面", () => {
    const next = overrideRegenerationPrompt(workflow, "新正向");
    expect(next.kind === "workflow" && next.prompt).toBe("新正向");
    const graph = (next.kind === "workflow" ? next.graph : {}) as typeof apiGraph;
    expect(graph["6"].inputs.text).toBe("新正向");
    expect(graph["7"].inputs.text).toBe("旧负面");
    // 原图不得被就地改动（快照克隆契约）
    expect(apiGraph["6"].inputs.text).toBe("旧正向");
  });

  it("空/空白提示词视为「不改」，原样返回", () => {
    expect(overrideRegenerationPrompt(aiImage, "   ")).toBe(aiImage);
    expect(overrideRegenerationPrompt(workflow, "")).toBe(workflow);
  });
});

describe("overrideGraphPrompt", () => {
  it("无采样器时退回第一个文本编码器", () => {
    const g = { "6": { class_type: "CLIPTextEncode", inputs: { text: "旧" } } };
    expect(((overrideGraphPrompt(g, "新") as typeof g)["6"].inputs.text)).toBe("新");
  });

  it("UI 格式（wfDraft）：改正向编码器第一个 widget", () => {
    const ui = { nodes: [
      { type: "CLIPTextEncode", title: "负面提示词", widgets_values: ["负面"] },
      { type: "CLIPTextEncode", title: "正向提示词", widgets_values: ["旧正向"] },
    ] };
    const next = overrideGraphPrompt(ui, "新正向") as typeof ui;
    expect(next.nodes[1].widgets_values[0]).toBe("新正向");
    expect(next.nodes[0].widgets_values[0]).toBe("负面");
  });

  it("非对象图原样返回", () => {
    expect(overrideGraphPrompt(null, "新")).toBeNull();
  });
});
