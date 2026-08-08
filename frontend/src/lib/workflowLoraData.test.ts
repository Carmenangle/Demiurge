import { describe, expect, it } from "vitest";
import type { LoraTriggerItem } from "../api/loras";
import {
  buildWorkflowLoraProposal, distillLoraSuggestedPrompt, mergeWorkflowLoraPrompt,
  workflowLoraNames,
} from "./workflowLoraData";

const record: LoraTriggerItem = {
  lora_name: "ANIMA_Niji_Sweet_Spot_v4.safetensors",
  triggers: ["NJSW33T"],
  suggested_weight: 0.8,
  suggested_prompt: "NJSW33T, masterpiece, rim light, silk texture, by putimaxi, @[sy4|diyokama], 1girl, red dress, kneeling",
  note: "",
  source: "manual",
  missing: false,
  updated_at: 1,
};

describe("LoRA author prompt distillation", () => {
  it("keeps quality, style, lighting, material and artist tags without trigger or scene content", () => {
    expect(distillLoraSuggestedPrompt(record.suggested_prompt, record.triggers)).toEqual([
      "masterpiece", "rim light", "silk texture", "by putimaxi", "@[sy4|diyokama]",
    ]);
  });

  it("rejects prose and material tags mixed with character clothing facts", () => {
    expect(distillLoraSuggestedPrompt([
      "cinematic lighting, silk texture, silk dress",
      "The scene is illuminated by dramatic rim lighting across her detailed face.",
    ].join(", "), [])).toEqual(["cinematic lighting", "silk texture"]);
  });

  it("puts triggers on line one and deduplicated quality tags at the start of line two", () => {
    expect(mergeWorkflowLoraPrompt(
      "masterpiece, NJSW33T, 1girl, blue hair",
      ["NJSW33T"],
      ["masterpiece", "rim light", "NJSW33T"],
    )).toBe("NJSW33T\nmasterpiece, rim light, 1girl, blue hair");
  });

  it("deduplicates an existing two-line CLIP prompt", () => {
    expect(mergeWorkflowLoraPrompt(
      "NJSW33T\nmasterpiece, rim light, 1girl, blue hair",
      ["NJSW33T"],
      ["masterpiece", "rim light"],
    )).toBe("NJSW33T\nmasterpiece, rim light, 1girl, blue hair");
  });
});

describe("workflow LoRA data proposal", () => {
  it("overrides active LoRA weights and only changes positive CLIP text", () => {
    const graph = {
      "1": { class_type: "LoraLoader", inputs: {
        lora_name: record.lora_name, strength_model: 1, strength_clip: 0.9,
      } },
      "2": { class_type: "CLIPTextEncode", inputs: { text: "masterpiece, 1girl", clip: ["1", 1] } },
      "3": { class_type: "CLIPTextEncode", inputs: { text: "worst quality", clip: ["1", 1] } },
      "4": { class_type: "KSampler", inputs: { positive: ["2", 0], negative: ["3", 0] } },
    };

    const proposal = buildWorkflowLoraProposal(graph, [record]);

    expect(proposal).toMatchObject({
      loraNames: [record.lora_name], weightChanges: 2, promptChanges: 1,
    });
    expect(proposal?.ops).toContainEqual({
      node_id: "1", input: "strength_model", action: "set_widget", value: 0.8,
    });
    expect(proposal?.ops).toContainEqual({
      node_id: "2", input: "text", action: "set_widget",
      value: "NJSW33T\nmasterpiece, rim light, silk texture, by putimaxi, @[sy4|diyokama], 1girl",
    });
    expect(proposal?.ops.some((op) => op.node_id === "3")).toBe(false);
  });

  it("ignores LoRA files without an exact saved record", () => {
    expect(buildWorkflowLoraProposal({
      "1": { class_type: "LoraLoader", inputs: { lora_name: "other.safetensors", strength_model: 1 } },
    }, [record])).toBeNull();
  });

  it("detects selected LoRA names before loading saved records", () => {
    expect(workflowLoraNames({
      "1": { class_type: "LoraLoader", inputs: { lora_name: record.lora_name } },
      "2": { class_type: "LoraLoaderModelOnly", inputs: { lora_name: record.lora_name } },
      "3": { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "base.safetensors" } },
    })).toEqual([record.lora_name]);
  });
});
