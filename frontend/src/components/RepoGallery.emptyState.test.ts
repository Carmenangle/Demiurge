// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// 2026-10-26 手机端「生成内容」空态：整条工具栏（含「同步体检」）挂在 items.length > 0 上，
// 于是「图已在磁盘、索引里没有」这台设备上最常见的坏状态，页面上一个自救入口都没有。
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

  it("工具栏仍按 items.length > 0 门控——这正是空态必须自带入口的原因", () => {
    expect(src).toContain("{enhanced && items.length > 0 && (");
  });
});
