// @ts-expect-error Test runtime provides node:fs; production code does not depend on Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const chatView = readFileSync(new URL("../views/ChatView.tsx", import.meta.url), "utf8");

describe("night theme asset and ornament constraints", () => {
  it("uses sliced button frames for every state", () => {
    expect(css).not.toMatch(
      /background(?:-image)?\s*:[^;}]*\/controls\/night\/button-(?:main|secondary)-(?:default|hover|pressed|disabled)\.png/,
    );
    for (const family of ["main", "secondary"]) {
      for (const state of ["default", "hover", "pressed", "disabled"]) {
        expect(css).toContain(
          `border-image-source: url("/controls/night/button-${family}-${state}.png")`,
        );
      }
    }
  });

  it("keeps persistent selections on the pressed state", () => {
    expect(css).toMatch(
      /html\[data-theme="night"\] \.btn\.primary:active[^}]*html\[data-theme="night"\] \.btn\.primary\.is-selected\s*\{[^}]*button-main-pressed\.png/s,
    );
    expect(chatView).toContain('showComfy ? "primary is-selected" : ""');
    expect(chatView).toContain("aria-pressed={showComfy}");
  });

  it("keeps modal ornaments behind content and outside actions", () => {
    expect(css).toMatch(/html\[data-theme="night"\] \.modal-actions\s*\{[^}]*padding-right:\s*64px;/s);
    expect(css).toMatch(
      /html\[data-theme="night"\] \.modal::before,[\s\S]*?corner-flow\.png/,
    );
  });

  it("maps night backgrounds, support emblem and avatar lifecycle", () => {
    expect(css).toContain('/backgrounds/night/chat-celestial-record.webp');
    expect(css).toContain('/backgrounds/night/chat-moonless-observatory.webp');
    expect(css).toContain('/ornaments/night/empty-state-journal.png');
    for (const name of ["main", "listening", "thinking", "success"]) {
      expect(css).toContain(`/support/night/112/hostess-${name}.png`);
    }
  });

  it("keeps the portrait journal emblem inside the round support button", () => {
    expect(css).toMatch(
      /html\[data-theme="night"\] \.support-fab\s*\{[^}]*overflow:\s*hidden;/s,
    );
    expect(css).toMatch(
      /html\[data-theme="night"\] \.support-fab-emblem\s*\{[^}]*width:\s*36px;[^}]*height:\s*36px;/s,
    );
  });

  it("keeps the night layout usable on narrow screens", () => {
    expect(css).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?html\[data-theme="night"\] \.layout\s*\{[^}]*min-width:\s*0;[^}]*flex-direction:\s*column;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?html\[data-theme="night"\] \.main\s*\{[^}]*margin-right:\s*0;[^}]*padding-bottom:\s*72px;/,
    );
  });
});
