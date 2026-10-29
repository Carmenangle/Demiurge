// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// 2026-10-26 手机端「生成内容」空态：整条工具栏（含「同步体检」）挂在 items.length > 0 上，
// 于是「图已在磁盘、索引里没有」这台设备上最常见的坏状态，页面上一个自救入口都没有。
// 2026-10-29 同一个门控的第二例：切到本来就没有资产的「视频／音频」分类，工具栏连同分类
// 按钮一起消失，用户被锁在空态里回不去 ⇒ 门控拆掉，工具栏常驻。
// 这里按 JSX 区间锁，不做全文 grep——按钮写在工具栏里也算 grep 命中，等于没锁。

const src = readFileSync("src/components/RepoGallery.tsx", "utf-8").replace(/\r\n/g, "\n");

function emptyStateBlock(): string {
  const start = src.indexOf("items.length === 0 ? (");
  expect(start, "找不到空态分支（items.length === 0）").toBeGreaterThanOrEqual(0);
  const end = src.indexOf(") : filtered.length === 0 ? (", start);
  expect(end, "空态分支结构变了（后面的 filtered 分支没找到）").toBeGreaterThan(start);
  return src.slice(start, end);
}

describe("资产库空态必须可自救", () => {
  it("空态分支里有「同步体检」入口，且只在 enhanced（资产库总页）出现", () => {
    const block = emptyStateBlock();
    expect(block).toContain("enhanced &&");
    expect(block).toContain("setShowSync(true)");
    expect(block).toContain("同步体检");
  });

  it("空态文案跟着媒体分类走——视频/音频分类下不能说「还没有生成记录」", () => {
    expect(emptyStateBlock()).toContain("MEDIA_KIND_LABEL[mediaFilter]");
  });

  it("工具栏只按 enhanced 门控，不再挂 items.length（否则空分类把分类按钮自己吃掉）", () => {
    const at = src.indexOf('<div className="asset-toolbar-sticky">');
    expect(at, "找不到资产库工具栏").toBeGreaterThanOrEqual(0);
    const guard = src.slice(src.lastIndexOf("{enhanced", at), at);
    expect(guard.trim()).toBe("{enhanced && (");
    expect(src).not.toContain("items.length > 0 && (");
  });

  it("媒体分类分段控件在工具栏里（常驻才切得回来）", () => {
    const at = src.indexOf('<div className="asset-toolbar-sticky">');
    const seg = src.indexOf('aria-label="媒体类型"');
    expect(seg).toBeGreaterThan(at);
    expect(seg).toBeLessThan(src.indexOf("第二行 · 筛选与维护"));
  });
});
