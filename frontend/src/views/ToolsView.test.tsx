import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ToolsView } from "./ToolsView";

describe("ToolsView", () => {
  it("does not duplicate the standalone LoRA data page", () => {
    const html = renderToStaticMarkup(<ToolsView repoId="test-work" />);
    expect(html).toContain("多功能工具");
    expect(html).not.toContain("LoRA 数据保存");
  });
});
