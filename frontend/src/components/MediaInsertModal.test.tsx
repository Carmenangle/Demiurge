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

    expect(html).not.toContain("Krea2（剧情高潮英文描述）");  // krea2 模版已下线（不如 Anima）
    expect(html).toContain("Anima（质量行 + 内容 tags / 英文描述）");
    expect(html).toContain("自然语言（GPT Image / Banana）");
    expect(html).toContain("Niji（主体 / 风格 / 附加 / 后缀）");
    expect(html).toContain('value="niji_sections" selected=""');
  });

  it("把旧 krea2 预设降级为 Anima", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal
        templates={[]}
        modelsDir=""
        preset={{ templateId: "", promptProfile: "krea2" }}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    expect(html).toContain('value="anima_tags" selected=""');
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
            顾凝霜: { loraName: "krea2_沈明澜.safetensors", loraWeight: 1 },
          },
          styleLora: "Krea2-Ogipote style-手绘风.safetensors",
        }}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    expect(html).toContain("krea2_沈明澜.safetensors（当前绑定，目录列表未包含）");
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

describe("MediaInsertModal prompt settings entry", () => {
  it("offers a gear entry for the LLM-facing prompts, closed by default", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", appearanceSource: "worldbook" }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain('aria-label="提示词设置"');
    expect(html).toContain("提示词设置：控制给 LLM 看的提示词");
    // 子层按需挂载：未点击齿轮时不得预先出现对话框内容
    expect(html).not.toContain("输出格式与校验");
  });
});

describe("MediaInsertModal 画面人称", () => {
  it("三档人称按钮组同排渲染，并回填手选档", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", viewpoint: "third_person" }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain("画面人称");
    expect(html).toContain("自动");
    expect(html).toContain("第一人称");
    expect(html).toContain('aria-pressed="true">第三人称');
    expect(html).toContain("主人公算一个主体");
  });

  it("旧预设未设人称时落地在自动档（零回归）", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "" }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain('aria-pressed="true">自动');
    expect(html).toContain("判不出时按第一人称处理");
  });
});

describe("MediaInsertModal 批量生图数目（第 57 刀）", () => {
  it("高潮点模式（非首尾帧）下渲染数目输入框，最小 1、缺省回填 1", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "" }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain("批量生图数目");
    expect(html).toContain('min="1"');
    expect(html).toContain('value="1"');
    // 说明文案必须点明「仅高潮点模式生效」与两条取值口径
    expect(html).toContain("仅高潮点模式生效");
    expect(html).toContain("全部出图");
  });

  it("回填已存数目（>1）", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", batchImageCount: 4 }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain('value="4"');
  });

  it("首尾帧模式（firstlast）下整个输入框不出现（零影响首尾帧链）", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", firstlast: true, batchImageCount: 4 }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).not.toContain("批量生图数目");
  });

  it("非法/0 存量一律收敛到 1（脏数据不透传）", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", batchImageCount: 0 }}
        onSave={() => {}} onClose={() => {}} />,
    );

    expect(html).toContain('value="1"');
  });
});

// ===== 2026-10-25 提示词模版：生图/视频提示词模式 =====
describe("MediaInsertModal prompt templates", () => {
  const customImage = {
    id: "a".repeat(32), builtin: false, kind: "anima_tags" as const, name: "我的Anima",
    base_id: "builtin:anima_tags", base_hash: "x", redline_lock: true, redline_lock_off_at: 0,
    system_text: "SYS", extra_instructions: "追加", quality_tags: "Q", negative_tags: "N",
    section_titles: {}, section_order: [], camera_band_overrides: {},
    beat_labels: [], default_duration: 0, negative_extra: "", meta_note: "",
  };
  const customVideo = {
    ...customImage, id: "b".repeat(32), kind: "video_climax" as const, name: "我的高潮",
    base_id: "builtin:video_climax",
  };

  it("「提示词模式」改名「生图提示词模式」，自定义模版进「我的模版」组", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", promptProfile: `custom:${"a".repeat(32)}` }}
        promptTemplateBuiltins={[]}
        promptTemplateItems={[customImage, customVideo]}
        onSave={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("生图提示词模式");
    expect(html).not.toContain(">提示词模式<");
    // 说明文案覆盖四档（2026-10-30 新增 MOSS）
    expect(html).toContain("内置四种输出结构（Anima 两行 / 自然语言 / Niji 四段 / MOSS caption）");
    expect(html).toContain("我的模版（提示词模版工作台）");
    expect(html).toContain("★ 我的Anima");
    // custom anima 模版也显示固定质量/负面词（anima 家族）
    expect(html).toContain("固定质量提示词");
  });

  it("视频分区新增「视频提示词模式」，高潮点只列 climax 模版", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", enableVideo: true, videoTemplateId: "" }}
        promptTemplateBuiltins={[]}
        promptTemplateItems={[customImage, customVideo]}
        onSave={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("视频提示词模式");
    expect(html).toContain("内置默认（六段式骨架）");
    expect(html).toContain("★ 我的高潮");
    // 生图自定义模版不混进视频选择器
    const videoBlock = html.slice(html.indexOf("视频提示词模式"));
    expect(videoBlock).not.toContain("★ 我的Anima");
  });

  it("首尾帧模式下视频选择器含段桥说明", () => {
    const html = renderToStaticMarkup(
      <MediaInsertModal templates={[]} modelsDir=""
        preset={{ templateId: "", enableVideo: true, firstlast: true }}
        promptTemplateBuiltins={[]}
        promptTemplateItems={[customVideo]}
        onSave={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("转场短桥段");
  });
});
