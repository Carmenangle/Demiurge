// @ts-expect-error Test runtime provides node:fs; production code does not depend on Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const chatView = readFileSync(new URL("../views/ChatView.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("unread message jump button layout", () => {
  it("belongs to the resizable input bar instead of the message list", () => {
    const inputBar = chatView.indexOf('<div className="chat-input-bar">');
    const jumpButton = chatView.indexOf('className="chat-new-message-btn"');
    const resizeHandle = chatView.indexOf('className="chat-input-resize-handle"');

    expect(chatView.match(/className="chat-new-message-btn"/g)).toHaveLength(1);
    expect(jumpButton).toBeGreaterThan(inputBar);
    expect(jumpButton).toBeLessThan(resizeHandle);
  });

  it("anchors above the input bar so resizing moves it", () => {
    expect(css).toMatch(/\.chat-input-bar\s*\{[^}]*position:\s*relative;/s);
    expect(css).toMatch(
      /\.chat-new-message-btn\s*\{[^}]*position:\s*absolute;[^}]*right:\s*0;[^}]*bottom:\s*calc\(100% \+ 8px\);/s,
    );
    expect(css).not.toMatch(/\.chat-new-message-btn\s*\{[^}]*top:\s*12px;/s);
  });
});
