// @ts-expect-error Test runtime provides node:fs; production code does not depend on Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const chatView = readFileSync(new URL("../views/ChatView.tsx", import.meta.url), "utf8");
const supportWidget = readFileSync(new URL("../components/SupportWidget.tsx", import.meta.url), "utf8");

describe("bright theme asset and ornament constraints", () => {
  it("uses sliced button edge overlays for every state", () => {
    for (const family of ["main", "secondary"]) {
      for (const state of ["default", "hover", "pressed", "disabled"]) {
        expect(css).toContain(
          `border-image-source: url("/controls/bright/button-${family}-${state}.png")`,
        );
      }
    }
  });

  it("keeps persistent selections on the pressed state", () => {
    expect(css).toMatch(
      /html\[data-theme="bright"\] \.btn\.primary:active[^}]*html\[data-theme="bright"\] \.btn\.primary\.is-selected\s*\{[^}]*button-main-pressed\.png/s,
    );
    expect(chatView).toContain('showComfy ? "primary is-selected" : ""');
    expect(chatView).toContain("aria-pressed={showComfy}");
  });

  it("keeps modal ornaments below content and outside actions", () => {
    expect(css).toMatch(/html\[data-theme="bright"\] \.modal-actions\s*\{[^}]*padding-right:\s*68px;/s);
    expect(css).toMatch(
      /html\[data-theme="bright"\] \.modal::before,[\s\S]*?cut-corner-porcelain\.png/,
    );
  });

  it("maps all assistant lifecycle avatar assets", () => {
    for (const name of ["main", "listening", "thinking", "success"]) {
      expect(css).toContain(`/support/bright/112/hostess-${name}.png`);
    }
  });

  it("uses the bright calibrator on the support button", () => {
    expect(supportWidget).toContain('className="support-fab-headset"');
    expect(supportWidget).toContain('className="support-fab-emblem"');
    expect(css).toMatch(
      /html\[data-theme="bright"\] \.support-fab-emblem\s*\{[^}]*display:\s*block;[^}]*empty-state-calibrator\.png/s,
    );
  });

  it("removes the desktop minimum width and keeps a mobile support rail", () => {
    expect(css).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?html\[data-theme="bright"\] \.layout\s*\{[^}]*min-width:\s*0;[^}]*flex-direction:\s*column;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?html\[data-theme="bright"\] \.main\s*\{[^}]*margin-right:\s*0;[^}]*padding-bottom:\s*72px;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?html\[data-theme="bright"\] \.chat-layout\s*\{[^}]*flex-direction:\s*column;/,
    );
  });
});
