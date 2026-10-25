import { describe, expect, it } from "vitest";

import {
  IMAGE_KINDS, VIDEO_KINDS,
  type PromptTemplate,
} from "../api/ai/promptTemplates";
import {
  combinedSystemChars, customIdOf, emptyForm, formFromTemplate, formToBody,
  isCustomTemplateId, legacyPresetToBody, promptTemplateInstructions,
  templateIsStale, templateKindGroup, templateSelectorOptions, videoTemplatesForMode,
  SYSTEM_CHAR_LIMIT,
} from "./promptTemplates";

const builtinImage: PromptTemplate = {
  id: "builtin:anima_tags", builtin: true, kind: "anima_tags", name: "Anima",
  base_id: "", base_hash: "", redline_lock: true, redline_lock_off_at: 0,
  content_hash: "aaaa1111",
  system_text: "SYS", extra_instructions: "", quality_tags: "Q", negative_tags: "N",
  section_titles: {}, section_order: [], camera_band_overrides: {},
  beat_labels: [], default_duration: 0, negative_extra: "", meta_note: "",
};

const customImage: PromptTemplate = {
  ...builtinImage, id: "a".repeat(32), builtin: false, name: "我的",
  base_id: "builtin:anima_tags", base_hash: "aaaa1111", content_hash: undefined,
  extra_instructions: "追加段",
};

const customVideoClimax: PromptTemplate = {
  ...builtinImage, id: "b".repeat(32), builtin: false, kind: "video_climax",
  name: "我的高潮", base_id: "builtin:video_climax", base_hash: "bbbb2222",
  section_titles: { meta: "元信息·改" }, section_order: [],
  camera_band_overrides: { "2": "镜头横移" }, beat_labels: ["起", "承", "合"],
  default_duration: 6, negative_extra: "模糊", meta_note: "备注",
};

const builtinVideoClimax: PromptTemplate = {
  ...builtinImage, id: "builtin:video_climax", kind: "video_climax",
  name: "视频·高潮点", content_hash: "bbbb2222",
};

describe("promptTemplates · id 与分组", () => {
  it("custom id 形态校验与提取", () => {
    expect(isCustomTemplateId(`custom:${"a".repeat(32)}`)).toBe(true);
    expect(isCustomTemplateId(`custom:${"a".repeat(31)}`)).toBe(false);
    expect(isCustomTemplateId(`custom:${"A".repeat(32)}`)).toBe(false);
    expect(isCustomTemplateId("builtin:anima_tags")).toBe(false);
    expect(isCustomTemplateId(undefined)).toBe(false);
    expect(customIdOf(`custom:${"a".repeat(32)}`)).toBe("a".repeat(32));
    expect(customIdOf("builtin:anima_tags")).toBe("");
  });

  it("kind 分组成生图/视频两组", () => {
    expect(templateKindGroup("anima_tags")).toBe("image");
    expect(templateKindGroup("niji_sections")).toBe("image");
    expect(templateKindGroup("video_climax")).toBe("video");
    expect(templateKindGroup("video_transition")).toBe("video");
    expect(IMAGE_KINDS).toHaveLength(3);
    expect(VIDEO_KINDS).toHaveLength(3);
  });
});

describe("promptTemplates · 选择器选项", () => {
  it("内置在前、自定义带★前缀，按组过滤", () => {
    const options = templateSelectorOptions(
      [builtinImage, builtinVideoClimax], [customImage, customVideoClimax], "image",
    );
    expect(options).toEqual([
      { id: "builtin:anima_tags", name: "Anima", builtin: true },
      { id: `custom:${"a".repeat(32)}`, name: "★ 我的", builtin: false },
    ]);
    const video = templateSelectorOptions(
      [builtinVideoClimax], [customVideoClimax], "video",
    );
    expect(video.map((o) => o.id)).toEqual(["builtin:video_climax", `custom:${"b".repeat(32)}`]);
  });

  it("视频模版按模式过滤：高潮点只看 climax，首尾帧含段桥", () => {
    const builtinFirstlast: PromptTemplate = {
      ...builtinVideoClimax, id: "builtin:video_firstlast", kind: "video_firstlast",
      name: "视频·首尾帧", content_hash: "dddd4444",
    };
    const customTransition: PromptTemplate = {
      ...customVideoClimax, id: "c".repeat(32), kind: "video_transition", name: "我的转场",
      base_id: "builtin:video_transition",
    };
    const climax = videoTemplatesForMode(
      [builtinVideoClimax, builtinFirstlast], [customVideoClimax, customTransition], "climax",
    );
    expect(climax.map((o) => o.id)).toEqual([
      "builtin:video_climax", `custom:${"b".repeat(32)}`,
    ]);
    const firstlast = videoTemplatesForMode(
      [builtinVideoClimax, builtinFirstlast], [customVideoClimax, customTransition], "firstlast",
    );
    // 首尾帧模式：内置首尾帧 + 自定义转场（段桥）；climax Kind 的不出现
    expect(firstlast.map((o) => o.id)).toEqual([
      "builtin:video_firstlast", `custom:${"c".repeat(32)}`,
    ]);
  });
});

describe("promptTemplates · 克隆基准比对", () => {
  it("基准哈希一致=不 stale；内置更新后=stale；无基准=不提示", () => {
    expect(templateIsStale(customImage, [builtinImage])).toBe(false);
    const moved = { ...builtinImage, content_hash: "cccc3333" };
    expect(templateIsStale(customImage, [moved])).toBe(true);
    const orphan = { ...customImage, base_id: "", base_hash: "" };
    expect(templateIsStale(orphan, [builtinImage])).toBe(false);
    expect(templateIsStale(builtinImage, [builtinImage])).toBe(false);
  });
});

describe("promptTemplates · 表单模型", () => {
  it("formFromTemplate 原样回填；formToBody 按组路由字段", () => {
    const imageForm = formFromTemplate(customImage);
    expect(imageForm.system_text).toBe("SYS");
    expect(imageForm.extra_instructions).toBe("追加段");
    expect(formToBody(imageForm)).toMatchObject({
      kind: "anima_tags", name: "我的", system_text: "SYS",
      extra_instructions: "追加段", quality_tags: "Q", negative_tags: "N",
      redline_lock: true,
    });
    expect(formToBody(imageForm)).not.toHaveProperty("section_titles");

    const videoForm = formFromTemplate(customVideoClimax);
    expect(videoForm.default_duration).toBe(6);
    expect(videoForm.camera_band_overrides).toEqual({ "2": "镜头横移" });
    const body = formToBody(videoForm);
    expect(body).toMatchObject({
      kind: "video_climax", default_duration: 6, negative_extra: "模糊", meta_note: "备注",
    });
    expect(body).not.toHaveProperty("system_text");
  });

  it("emptyForm 克隆时带种子、名字清空", () => {
    const seeded = emptyForm("anima_tags", builtinImage);
    expect(seeded.system_text).toBe("SYS");
    expect(seeded.name).toBe("");
    const blank = emptyForm("video_climax");
    expect(blank.system_text).toBe("");
    expect(blank.section_titles).toEqual({});
  });

  it("combinedSystemChars 合计两段（后端上限 12000 同口径）", () => {
    const form = { ...formFromTemplate(customImage), system_text: "x".repeat(100) };
    expect(combinedSystemChars(form)).toBe(100 + "追加段".length);
    expect(SYSTEM_CHAR_LIMIT).toBe(12_000);
  });
});

describe("promptTemplates · 旧存档迁移与 styleTemplate", () => {
  it("旧存档内容原样进追加段，system 用内置全文", () => {
    const body = legacyPresetToBody(
      { id: "old-1", name: "旧风格", content: "画风：厚涂；结构：三段" }, "SYS",
    );
    expect(body).toMatchObject({
      kind: "anima_tags", name: "旧风格", system_text: "SYS",
      extra_instructions: "画风：厚涂；结构：三段",
    });
  });

  it("选中自定义模版才透传追加段；内置/未选=空串", () => {
    expect(promptTemplateInstructions(`custom:${"a".repeat(32)}`, [customImage])).toBe("追加段");
    expect(promptTemplateInstructions("builtin:anima_tags", [customImage])).toBe("");
    expect(promptTemplateInstructions("", [customImage])).toBe("");
    // 模版被删（列表里没有）⇒ 空串，不幻觉
    expect(promptTemplateInstructions(`custom:${"z".repeat(32)}`, [customImage])).toBe("");
  });
});
