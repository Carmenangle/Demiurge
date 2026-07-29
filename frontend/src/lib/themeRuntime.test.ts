// @ts-expect-error Test runtime provides node:fs; production code does not depend on Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const chatView = readFileSync(new URL("../views/ChatView.tsx", import.meta.url), "utf8");
const runtime = 'html:is([data-theme="bright"], [data-theme="night"], [data-theme="green"], [data-theme="eye-care"], [data-theme="gray"])';

describe("production theme runtime interface", () => {
  it("owns sliced controls once for every production theme", () => {
    expect(css).toMatch(
      new RegExp(`${runtime.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\.btn:not\\(\\.danger\\)\\s*\\{[^}]*border-image-slice:\\s*18 40 fill;[^}]*border-image-width:\\s*4px 16px;`, "s"),
    );
  });

  it("owns modal safety, composer chrome and avatar geometry once", () => {
    expect(css).toContain(`${runtime} .modal > *`);
    expect(css).toContain(`${runtime} :is(.modal::before, .modal::after)`);
    expect(css).toContain(`${runtime} .chat-input-resize-handle`);
    expect(css).toContain(`${runtime} .bot-avatar`);
  });

  it("leaves an empty conversation free of duplicate avatar and guidance", () => {
    expect(chatView).not.toContain("ChatEmptyLanding");
    expect(css).not.toContain(".chat-landing-hostess");
    expect(css).not.toContain(".chat-landing-hint");
  });

  it("renders message artwork in fixed top and bottom layers", () => {
    expect(css).toMatch(
      /html:is\(\[data-theme="bright"\], \[data-theme="night"\], \[data-theme="green"\], \[data-theme="gray"\]\) \.bubble-user::after\s*\{[^}]*height:\s*104px;[^}]*background-position:\s*center bottom;[^}]*background-size:\s*100% auto;/s,
    );
    expect(css).toMatch(
      new RegExp(`${runtime.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\.bot-content::before\\s*\\{[^}]*height:\\s*240px;[^}]*background-position:\\s*center top;[^}]*background-size:\\s*100% auto;`, "s"),
    );
    expect(css).toContain("background-image: var(--message-user-art);");
    expect(css).toContain("background-image: var(--message-assistant-art);");
  });
});
