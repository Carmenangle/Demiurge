// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { existsSync } from "node:fs";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { resolve } from "node:path";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { NEWCOMER_GUIDE_SECTIONS, type GuideStep } from "./newcomerGuide";
import { SECTION_SUBNAV } from "./viewRouting";
import { splitGuideLinks, type GuideLink } from "./guideLinks";

// 仓库根：frontend/src/lib/*.test.ts → 上三级（src → frontend → 仓库根）
const REPO_ROOT = resolve(fileURLToPath(new URL("../../..", import.meta.url)));

function allSteps(): { sectionId: string; step: GuideStep }[] {
  return NEWCOMER_GUIDE_SECTIONS.flatMap((s) => s.steps.map((step) => ({ sectionId: s.id, step })));
}

function linksOf(text: string): GuideLink[] {
  return splitGuideLinks(text).map((seg) => seg.link).filter((l): l is GuideLink => !!l);
}

// 新人引导内容合同：id 唯一、步骤必填、插图路径要么为空要么指向 public（onboarding/ 或绝对/外链）。
// 防回归：新人加内容时漏字段/重复 id/错误路径在这里直接失败。
// 另有导航同步合同：SECTION_SUBNAV.guide（左栏章节子项）与这里的章节保持同序同 id。

describe("newcomer guide content", () => {
  it("has sections", () => {
    expect(NEWCOMER_GUIDE_SECTIONS.length).toBeGreaterThan(0);
  });

  it("section ids are unique", () => {
    const ids = NEWCOMER_GUIDE_SECTIONS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every step has title and text", () => {
    for (const section of NEWCOMER_GUIDE_SECTIONS) {
      for (const step of section.steps) {
        expect(step.title.trim(), `${section.id} step title`).not.toBe("");
        expect(step.text.trim(), `${section.id}/${step.title} text`).not.toBe("");
      }
    }
  });

  it("image paths are empty or public-relative onboarding assets", () => {
    for (const section of NEWCOMER_GUIDE_SECTIONS) {
      for (const step of section.steps) {
        const image = step.image ?? "";
        const valid =
          image === "" ||
          image.startsWith("http") ||
          image.startsWith("/") ||
          image.startsWith("onboarding/");
        expect(valid, `${section.id}/${step.title} image: ${image}`).toBe(true);
      }
    }
  });

  it("doc: links must exist as real markdown under docs/ (no dead doc links)", () => {
    // 死链防回归：正文里写了 doc: 链接但文档被改名/移走，这里直接失败。
    for (const { sectionId, step } of allSteps()) {
      for (const link of linksOf(step.text)) {
        if (link.kind !== "doc") continue;
        const where = `${sectionId}/${step.title} → ${link.target}`;
        expect(link.target.startsWith("docs/"), where).toBe(true);
        expect(existsSync(resolve(REPO_ROOT, link.target)), where).toBe(true);
      }
    }
  });

  it("guide: links must point at an existing section and step", () => {
    // 章节改名/删步骤后，跨章链接会静默失效（点了没反应），这里挡住。
    const byId = new Map(NEWCOMER_GUIDE_SECTIONS.map((s) => [s.id, s]));
    for (const { sectionId, step } of allSteps()) {
      for (const link of linksOf(step.text)) {
        if (link.kind !== "guide") continue;
        const where = `${sectionId}/${step.title} → ${link.target}`;
        const target = byId.get(link.target);
        expect(target, where).toBeDefined();
        if (link.stepNumber) {
          expect(link.stepNumber, where).toBeLessThanOrEqual(target?.steps.length ?? 0);
        }
      }
    }
  });

  it("guide subnav stays in sync with sections (ids and order)", () => {
    // 新增/删除章节时必须同步 lib/viewRouting.ts 的 SECTION_SUBNAV.guide，否则左栏缺项
    expect(SECTION_SUBNAV.guide.map((item) => item.id)).toEqual(
      NEWCOMER_GUIDE_SECTIONS.map((s) => s.id),
    );
  });
});
