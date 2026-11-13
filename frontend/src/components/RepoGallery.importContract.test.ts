// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// 2026-11-08 资产库导入：图片/视频可拖入（或从工具栏选择），落盘后把内嵌元信息
// （ComfyUI PNG / A1111 / EXIF / 视频容器标签）提取到提示词与生成参数。
// 按 JSX 区间锁关键接线，不做全文 grep——防止「写了但没接上」也绿。
const src = readFileSync("src/components/RepoGallery.tsx", "utf-8").replace(/\r\n/g, "\n");

describe("资产库导入（拖拽 + 选择）", () => {
  it("根容器接收拖放：dragover 必须 preventDefault，drop 走 startImport", () => {
    const at = src.indexOf("onDragEnter={");
    expect(at, "找不到根容器的拖放接线").toBeGreaterThanOrEqual(0);
    const end = src.indexOf("<input ref={importInputRef}", at);
    expect(end).toBeGreaterThan(at);
    const block = src.slice(at, end);
    expect(block).toContain("onDragOver");
    expect(block).toContain("e.preventDefault();   // 必须");
    expect(block).toContain("onDrop");
    expect(block).toContain("startImport(Array.from(e.dataTransfer.files || []))");
  });

  it("工具栏有导入入口，文件选择器多选且只收图片/视频", () => {
    expect(src).toContain('type="file" multiple accept="image/*,video/*"');
    expect(src).toContain("importInputRef.current?.click()");
    expect(src).toContain('{importing ? "导入中…" : "导入"}');
  });

  it("多仓库先选目标作品，单仓库直达", () => {
    expect(src).toContain("if (repoIds.length === 1) { void runImport(repoIds[0], media); return; }");
    expect(src).toContain("导入到哪个作品？");
  });

  it("详情面板展示提取出的生成参数与负面提示词，来源标本地导入", () => {
    expect(src).toContain("const genMeta = active?.sourceMeta?.generation;");
    expect(src).toContain('label="采样器"');
    expect(src).toContain('label="采样步数"');
    expect(src).toContain("负面提示词（从文件元信息提取）");
    expect(src).toContain('active.source === "import" ? "本地导入"');
    expect(src).toContain("active.sourceMeta?.originalName");
  });
});
