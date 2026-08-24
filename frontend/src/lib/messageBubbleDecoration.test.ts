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
    // padding 已主题化（bright/night/green/gray 统一 112px，eye-care 用 76/58px）；
    // 工作副本为 CRLF，先归一化换行再断言，避免平台差异误报。
    const normalized = css.replace(/\r\n/g, "\n");
    expect(normalized).toContain('.user-message-text {\n  padding-right: 112px;');
    expect(normalized).toContain('.user-message-actions {\n  padding-right: 112px;');
  });
});
