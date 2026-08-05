import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("dompurify", () => ({ default: { sanitize: (html: string) => html } }));

import { AssistantMessage } from "./ChatMessages";

describe("assistant inline media slot", () => {
  it("renders the reserved slot between the anchored text parts", () => {
    const html = renderToStaticMarkup(
      <AssistantMessage
        msg={{
          id: "assistant-1",
          role: "assistant",
          text: "高潮段后续正文",
          parts: [
            { type: "text", text: "高潮段" },
            { type: "media-slot", slotId: "slot-1", status: "pending" },
            { type: "text", text: "后续正文" },
          ],
        }}
        onSendImage={() => {}}
      />,
    );

    expect(html.indexOf("高潮段")).toBeLessThan(html.indexOf("media-slot"));
    expect(html.indexOf("media-slot")).toBeLessThan(html.indexOf("后续正文"));
    expect(html).toContain("插画生成中");
  });
});
