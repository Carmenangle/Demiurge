import { describe, expect, it } from "vitest";
import {
  exposeWorkflowNodeFields, replaceWorkflowNodeExposure,
} from "./workflowTemplateExposure";

describe("ComfyUI node selection exposure", () => {
  it("matches parameter-list defaults and preserves original widget names and values", () => {
    expect(exposeWorkflowNodeFields({
      id: "40",
      class_type: "LoraLoaderModelOnly",
      title: "LoRA",
      bypassed: false,
      fields: [
        { name: "model", value: null, linked: true, required: true },
        { name: "lora_name", value: "style.safetensors", linked: false, required: true },
        { name: "strength_model", value: 0.85, linked: false, required: true },
      ],
    })).toEqual([
      {
        node_id: "40", field: "lora_name", label: "lora_name", control: "text",
        semantic: "lora_name", binding: "lora_name", default: "style.safetensors",
      },
      {
        node_id: "40", field: "strength_model", label: "strength_model", control: "number",
        semantic: "strength_model", binding: "lora_weight", default: 0.85,
      },
    ]);
  });

  it("replaces saved semantic aliases with the original node field defaults", () => {
    const result = replaceWorkflowNodeExposure([
      {
        node_id: "40", field: "strength_model", label: "LoRA 权重", control: "number",
        semantic: "lora_weight", default: 1,
      },
      {
        node_id: "39", field: "text", label: "提示词", control: "textarea",
        semantic: "prompt", default: "keep",
      },
    ], {
      id: "40",
      class_type: "LoraLoaderModelOnly",
      title: "LoRA",
      bypassed: false,
      fields: [
        { name: "lora_name", value: "new.safetensors", linked: false, required: true },
        { name: "strength_model", value: 0.7, linked: false, required: true },
      ],
    });

    expect(result).toContainEqual({
      node_id: "40", field: "strength_model", label: "strength_model", control: "number",
      semantic: "strength_model", binding: "lora_weight", default: 0.7,
    });
    expect(result).not.toContainEqual(expect.objectContaining({ semantic: "lora_weight" }));
    expect(result).toContainEqual(expect.objectContaining({ node_id: "39", semantic: "prompt" }));
  });
});
