// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 2026-11-07：GBC 面板只交付官方布局 JSON（用户定案：MOSS JSON / 草稿 / 归一化
// Layout 与手动「生成官方布局提示词」按钮都不要，打标完自动走生成流程）。
const gallery = readFileSync(new URL("../components/RepoGallery.tsx", import.meta.url), "utf8");
const ragApi = readFileSync(new URL("../api/ai/rag.ts", import.meta.url), "utf8");

describe("GBC → 官方布局 JSON", () => {
  it("打标成功后自动生成官方布局，面板只留复制入口", () => {
    expect(gallery).toContain("const layout = await genMossLayout(g.id);");
    expect(gallery).toContain("generateMossLayout(id, vlmChat)");
    expect(gallery).toContain('label="复制官方布局 JSON"');
    expect(gallery).toContain("官方布局生成中…");
    // 三个旧出口与手动生成按钮不再交付
    expect(gallery).not.toContain("复制 MOSS JSON");
    expect(gallery).not.toContain("复制 MOSS 草稿");
    expect(gallery).not.toContain("复制 Layout（构图）");
    expect(gallery).not.toContain("生成官方布局提示词");
  });

  it("老资产打开详情时补生成（有实体但还没有官方布局）", () => {
    expect(gallery).toContain("saved.moss_layout?.layout");
    expect(gallery).toContain("void genMossLayout(id);");
  });

  it("官方布局的 style / 角色卡 / 逐项描述仍可预览", () => {
    expect(gallery).toContain("<strong>Style</strong>");
    expect(gallery).toContain("<strong>角色卡</strong>");
    expect(gallery).toContain("<strong>逐项描述</strong>");
  });
});

describe("GBC 契约", () => {
  it("API 层只保留官方布局，不再导出归一化 Layout / MOSS 交付形", () => {
    expect(ragApi).toContain("export function generateMossLayout");
    expect(ragApi).toContain("export interface MossLayoutParts");
    expect(ragApi).toContain("moss_layout?: Partial<MossLayoutPayload>;");
    expect(ragApi).toContain('"/rag/moss-layout"');
    expect(ragApi).not.toContain("export type GbcLayout");
    expect(ragApi).not.toContain("moss_draft");
  });
});
