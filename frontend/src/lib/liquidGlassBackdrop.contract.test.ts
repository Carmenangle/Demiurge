// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 工作副本可能是 CRLF，先归一化换行，避免平台差异误报。
const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const backdrop = readFileSync(
  new URL("../components/LiquidGlassBackdrop.tsx", import.meta.url),
  "utf8",
).replace(/\r\n/g, "\n");

function blockOf(haystack: string, selector: string, last = false): string {
  // 选择器也出现在文件前部的「三合一」定位组里，基础规则要认最后一次独立声明。
  const at = last
    ? haystack.lastIndexOf(`${selector} {`)
    : haystack.indexOf(`${selector} {`);
  expect(at, `找不到规则 ${selector} {`).toBeGreaterThanOrEqual(0);
  const open = haystack.indexOf("{", at);
  let depth = 0;
  for (let i = open; i < haystack.length; i++) {
    if (haystack[i] === "{") depth++;
    else if (haystack[i] === "}" && --depth === 0) return haystack.slice(open + 1, i);
  }
  throw new Error(`规则 ${selector} 的花括号没配对`);
}

describe("液态玻璃壁纸层 · 实时场景不叠暗化（2026-10-28 用户实锤）", () => {
  it("照片/视频壁纸的暗化兜底仍在：基础 scrim 规则带 --lg-scrim 与上下渐变", () => {
    const base = blockOf(css, 'html[data-theme="liquid-glass"] .app-backdrop-scrim', true);
    expect(base).toContain("var(--lg-scrim, 0.25)");
    expect(base).toContain("linear-gradient(180deg");
    expect(base).toContain("radial-gradient(120% 90% at 50% 0%");
  });

  it("场景就绪时 scrim 整层清空：背景压到原图 0.5–0.7 倍的那三层全部撤掉", () => {
    const scene = blockOf(
      css,
      'html[data-theme="liquid-glass"] .app-backdrop.has-scene .app-backdrop-scrim',
    );
    const decl = scene.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(decl).toMatch(/background:\s*none\s*;/);
    expect(decl).not.toContain("--lg-scrim");
    expect(decl).not.toContain("gradient");
  });

  it("has-scene 只在「有场景来源且播放器就绪」时挂上（覆盖层没就绪时预览仍要可读）", () => {
    // 组件里 sceneCovered 的定义与 className 分支是这条契约的两半，缺一即失效。
    expect(backdrop).toMatch(
      /const sceneCovered = Boolean\(sceneSource\) && sceneStatus === "ready";/,
    );
    expect(backdrop).toMatch(/\$\{sceneCovered \? " has-scene" : ""\}/);
  });
});
