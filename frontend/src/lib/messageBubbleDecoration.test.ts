// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("user message decoration", () => {
  it("uses fixed decorative primitives instead of overlaying full bubble images", () => {
    const decoration = css.slice(css.indexOf("/* 消息素材中的角饰分层固定"));

    expect(decoration).not.toContain("background-image: var(--message-user-art)");
    expect(decoration).toContain("width: 48px");
    expect(decoration).toContain("width: 28px");
    expect(decoration).toContain("pointer-events: none");
  });

  it("keeps text and actions outside the fixed decoration area", () => {
    expect(css).toContain(".user-message-text {\n  padding-right: 112px;");
    expect(css).toContain(".user-message-actions {\n  padding-right: 112px;");
  });
});
