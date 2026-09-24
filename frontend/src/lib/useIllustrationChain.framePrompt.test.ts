// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * 源码锁（对齐 `canvasProjection.test.ts` 的写法）：**首尾帧槽位必须与提交 ComfyUI 的
 * 提示词同源**。
 *
 * 背景：2026-08-29 验收过一次「资产库显示帧描述原文」，根因是入库链传了
 * `firstFrameDesc`/`lastFrameDesc` 原文而非编译后的 `frameXxxPrompt`——当时**只补了视频
 * 分支，独立图片模式分支漏了**，2026-09-22 再次复现（尾帧入库提示词是中文剧情原文）。
 *
 * ⚠ 源码必须在 **it 回调内**读（jsdom 下模块顶层 `import.meta.url` 不是 file: 协议）。
 */
const read = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8").replace(/\r\n/g, "\n");

/** 去注释（锁只钉代码；注释里提到旧实参名会算「还在」）。 */
const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "");
const codeLines = (s: string) => stripComments(s).split("\n")
  .map((l) => l.replace(/(?<!:)\/\/.*$/, "").trim())
  .filter((l) => l.length > 0);

const SLOT_CALLS = ["appendImageSlot(", "pollResult(", "reportFrame("];

describe("首尾帧槽位提示词同源锁", () => {
  it("槽位/上报调用行不得出现 lastFrameDesc（原文）", () => {
    const offenders = codeLines(read("./useIllustrationChain.ts")).filter(
      (l) => l.includes("lastFrameDesc") && SLOT_CALLS.some((c) => l.includes(c)),
    );
    expect(offenders).toEqual([]);
  });

  it("尾帧槽三处必须用编译后的 frameLastPrompt，且 media 同源", () => {
    const src = codeLines(read("./useIllustrationChain.ts")).join("\n");
    // 显示：appendImageSlot(...) 用编译后提示词
    expect(src).toContain("appendImageSlot(current, messageId, lastSlotId, frameLastPrompt)");
    // pollResult 的提示词实参同源
    expect(src).toContain("frameTarget(lastSlotId), frameLastPrompt");
    // 上报：提示词 + 逐帧 media 都同源（少传 media 会让 LoRA 上报回退到请求级）
    expect(src).toContain("frameLastPrompt, frameLastValueKeys, frameLastMedia");
  });

  it("首帧槽同样用编译后的 frameFirstPrompt", () => {
    const src = codeLines(read("./useIllustrationChain.ts")).join("\n");
    expect(src).toContain("appendImageSlot(current, messageId, `${slotId}:first`, frameFirstPrompt");
    expect(src).toContain("frameFirstPrompt, frameFirstValueKeys, frameFirstMedia");
  });
});
