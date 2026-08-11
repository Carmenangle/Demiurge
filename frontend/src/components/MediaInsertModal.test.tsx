import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MediaInsertModal, suggestedWeightForLora } from "./MediaInsertModal";

describe("MediaInsertModal prompt profiles", () => {
  it("uses the same full-width select layout for the appearance source", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", appearanceSource: "character_card" }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain("角色外貌来源");
    expect(html).toContain("条目模式");
    expect(html).toContain("角色卡模式");
    expect(html).toContain('value="character_card" selected=""');
    expect(html).not.toContain("media-source-switch");
    expect(html).not.toContain("按角色配置（LoRA + 底图）");
    expect(html).not.toContain("风格底图（可选）");
    expect(html).toContain("全局风格 LoRA（可选）");
  });

  it("keeps role LoRA and fallback image controls in worldbook mode", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", appearanceSource: "worldbook" }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain("按角色配置（LoRA + 底图）");
    expect(html).toContain("风格底图（可选）");
  });

  it("shows all prompt modes and restores the selected mode", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal
        templates={[]}
        modelsDir=""
        preset={{ templateId: "", promptProfile: "niji_sections" }}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    expect(html).toContain("Krea2（剧情高潮英文描述）");
    expect(html).toContain("Anima（质量行 + 内容 tags / 英文描述）");
    expect(html).toContain("自然语言（GPT Image / Banana）");
    expect(html).toContain("Niji（主体 / 风格 / 附加 / 后缀）");
    expect(html).toContain('value="niji_sections" selected=""');
  });

  it("shows saved fixed quality and negative prompts for Anima", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal
        templates={[]}
        modelsDir=""
        preset={{
          templateId: "", promptProfile: "anima_tags",
          qualityPrompt: "masterpiece, best quality",
          negativePrompt: "worst quality, low quality",
        }}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    expect(html).toContain("固定质量提示词");
    expect(html).toContain("固定负面提示词");
    expect(html).toContain("masterpiece, best quality");
    expect(html).toContain("worst quality, low quality");
    expect(html).not.toContain("二次采样总步数");
    expect(html).not.toContain("二采起点偏移");
  });

  it("shows and restores the latent longest-edge tier", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal
        templates={[]}
        modelsDir=""
        preset={{ templateId: "", latentLongEdge: 4096 }}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    expect(html).toContain("Latent 最长边");
    expect(html).toContain("1K（1024）");
    expect(html).toContain("2K（2048）");
    expect(html).toContain("4K（4096）");
    expect(html).toContain('value="4096" selected=""');
  });
});

describe("MediaInsertModal LoRA suggested weights", () => {
  it("uses the exact selected LoRA suggested weight and defaults unknown models", () => {
    const loras = [{
      lora_name: "character.safetensors", has_triggers: true,
      trigger_status: "configured" as const, suggested_weight: 1.15,
    }];
    expect(suggestedWeightForLora(loras, "character.safetensors")).toBe(1.15);
    expect(suggestedWeightForLora(loras, "unknown.safetensors")).toBe(0.8);
  });

  it("keeps saved LoRA bindings visible before the disk list finishes loading", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal
        templates={[]}
        modelsDir="D:/ComfyUI/models"
        preset={{
          templateId: "", loraMode: "single", appearanceSource: "worldbook",
          characterLoras: {
            冷倾雪: { loraName: "krea2_柳世熙.safetensors", loraWeight: 1 },
          },
          styleLora: "Krea2-Ogipote style-手绘风.safetensors",
        }}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    expect(html).toContain("krea2_柳世熙.safetensors（当前绑定，目录列表未包含）");
    expect(html).toContain("Krea2-Ogipote style-手绘风.safetensors（当前绑定，目录列表未包含）");
  });
});

describe("MediaInsertModal LoRA modes", () => {
  it("renders the three modes on one segmented row and restores multi mode", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", loraMode: "multi" }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain('class="lora-mode-switch"');
    expect(html).toContain("无 LoRA");
    expect(html).toContain("单 LoRA");
    expect(html).toContain('aria-pressed="true">多 LoRA');
    expect(html).toContain("固定加载默认风格 LoRA，并叠加全部在场角色 LoRA");
    expect(html).toContain("兜底风格 LoRA");
  });

  it("none mode keeps character base-image rows but hides every LoRA selector", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{
          templateId: "", loraMode: "none",
          characterLoras: { 露娜: { baseImage: "role.png" } },
        }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain('aria-pressed="true">无 LoRA');
    expect(html).toContain('value="露娜"');
    expect(html).toContain("更换底图");
    expect(html).not.toContain("无角色 LoRA（用风格 LoRA + 底图）");
    expect(html).not.toContain("兜底风格 LoRA");
  });
});
