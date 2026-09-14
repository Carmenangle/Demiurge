// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { existsSync } from "node:fs";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { resolve } from "node:path";
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { NEWCOMER_GUIDE_SECTIONS, guideSteps, resolveGuideSection, type GuideStep } from "./newcomerGuide";
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

  it("分组章节的 steps 必须等于 groups 机械展开（防两处维护漂移）", () => {
    // 固化流程合并后，steps 由 groups 展开而来；若有人只改 groups 不改 steps，
    // 锚点序号与渲染顺序就会错位，这里直接挡住。
    for (const section of NEWCOMER_GUIDE_SECTIONS) {
      if (!section.groups?.length) continue;
      const flat = section.groups.flatMap((g) => g.steps);
      expect(flat, section.id).toEqual(section.steps);
      expect(guideSteps(section), section.id).toEqual(section.steps);
    }
  });

  it("固化流程四组齐全（01/02/03/04 自定义环节）且配图齐备", () => {
    // 历史回归：自定义环节 2026-09-11 被整体裁掉时无测试兜底；这里锁结构
    // （组标题 / 每组步数 / 自定义环节配图），正文文案改动不受影响。
    const groups = NEWCOMER_GUIDE_SECTIONS.find((s) => s.id === "curing-flows")?.groups ?? [];
    expect(groups.map((g) => g.title)).toEqual([
      "01：批量生图",
      "02：小说转合集卡",
      "03：合集卡转化（ST 卡）",
      "04：自定义环节",
    ]);
    expect(groups.map((g) => g.steps.length)).toEqual([2, 2, 1, 4]);
    expect(groups[3]?.steps.map((s) => s.image)).toEqual([
      "onboarding/curing-process-6.png",
      "onboarding/curing-process-7.png",
      "onboarding/curing-process-8.png",
      "onboarding/curing-process-9.png",
    ]);
  });

  it("旧章节 id 仍能解析到合并后的章节（旧 hash 不失效）", () => {
    for (const legacy of ["curing-process", "novel-to-collection-card",
      "card-worldbook-convert", "create-curing-process"]) {
      expect(resolveGuideSection(legacy)?.id, legacy).toBe("curing-flows");
    }
    expect(resolveGuideSection("quick-start")?.id).toBe("quick-start");
    expect(resolveGuideSection("nope")).toBeUndefined();
  });

  it("guide subnav stays in sync with sections (ids and order)", () => {
    // 新增/删除章节时必须同步 lib/viewRouting.ts 的 SECTION_SUBNAV.guide，否则左栏缺项
    expect(SECTION_SUBNAV.guide.map((item) => item.id)).toEqual(
      NEWCOMER_GUIDE_SECTIONS.map((s) => s.id),
    );
  });
});
