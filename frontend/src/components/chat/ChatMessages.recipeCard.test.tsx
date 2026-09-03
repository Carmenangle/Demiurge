import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("dompurify", () => ({ default: { sanitize: (html: string) => html } }));
vi.mock("../../lib/planTaskActivity", () => ({
  approvePlanTask: vi.fn(),
  cancelPlanTask: vi.fn(),
  getPlanTask: vi.fn(),
  keepRecipe: vi.fn(),
  deleteRecipe: vi.fn(),
  listRecipes: vi.fn(async () => ({})),
}));

import { AssistantMessage } from "./ChatMessages";

describe("assistant recipe solidify card", () => {
  it("renders keep/discard buttons and strips the marker from body", () => {
    const html = renderToStaticMarkup(
      <AssistantMessage
        msg={{
          id: "assistant-recipe-1",
          role: "assistant",
          text: "已生成套装文档\n\n[[recipe:abc123def|套装文档流程]]",
        }}
        onSendImage={() => {}}
      />,
    );

    expect(html).toContain("已生成套装文档");
    expect(html).toContain("把这次流程固化为预设《套装文档流程》？");
    expect(html).toContain("保留");
    expect(html).toContain("不保留");
    expect(html).not.toContain("[[recipe:");
  });

  it("does not render the card when no recipe marker exists", () => {
    const html = renderToStaticMarkup(
      <AssistantMessage
        msg={{ id: "assistant-recipe-2", role: "assistant", text: "普通回复" }}
        onSendImage={() => {}}
      />,
    );

    expect(html).not.toContain("固化为预设");
    expect(html).not.toContain("不保留");
  });
});
