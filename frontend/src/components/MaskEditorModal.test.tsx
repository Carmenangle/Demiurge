import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MaskEditorModal } from "./MaskEditorModal";

describe("MaskEditorModal", () => {
  it("exposes the requested mask tools and completion actions", () => {
    const html = renderToStaticMarkup(
      <MaskEditorModal imageUrl="result.png" onCancel={() => {}} onComplete={() => {}} />,
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-label="画笔"');
    expect(html).toContain('aria-label="橡皮擦"');
    expect(html).toContain('aria-label="套索工具"');
    expect(html).toContain('aria-label="魔棒工具"');
    expect(html).toContain('aria-label="油桶"');
    expect(html).toContain('type="color"');
    expect(html).toContain('type="range"');
    expect(html).toContain("绘制完毕");
    expect(html).toContain("取消");
  });
});
