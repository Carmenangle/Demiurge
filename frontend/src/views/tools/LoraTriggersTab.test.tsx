import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { LoraTriggerItem } from "../../api/loras";
import { LoraDataModal } from "./LoraTriggersTab";

describe("LoRA data editor", () => {
  it("shows the author prompt distillation control below the editable prompt", () => {
    const item: LoraTriggerItem = {
      lora_name: "style.safetensors",
      triggers: ["style_trigger"],
      suggested_weight: 0.8,
      suggested_prompt: "masterpiece, rim light, by artist",
      note: "",
      source: "manual",
      missing: false,
      updated_at: 1,
    };

    const html = renderToStaticMarkup(
      <LoraDataModal item={item} onConfirm={() => {}} onCancel={() => {}} />,
    );

    expect(html).toContain("作者建议提示词");
    expect(html).toContain("提炼质量/风格/光影/材质/作者标签");
    expect(html).toContain("masterpiece, rim light, by artist");
  });
});
