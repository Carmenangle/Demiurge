// @ts-expect-error Test runtime provides node:fs; production code does not depend on Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const chatView = readFileSync(new URL("../views/ChatView.tsx", import.meta.url), "utf8");

describe("eye-care ornament safety zones", () => {
  it("uses sliced bitmap frames without scaling them across button content", () => {
    expect(css).not.toMatch(
      /background(?:-image)?\s*:[^;}]*button-(?:main|secondary)-(?:default|hover|pressed|disabled)\.png/,
    );
    for (const family of ["main", "secondary"]) {
      for (const state of ["default", "hover", "pressed", "disabled"]) {
        expect(css).toContain(
          `border-image-source: url("/controls/eye-care/button-${family}-${state}.png")`,
        );
      }
    }
  });

  it("keeps modal content above ornaments and actions outside the corner", () => {
    expect(css).toMatch(
      /html\[data-theme="eye-care"\] \.modal-actions\s*\{[^}]*padding-right:\s*64px;/s,
    );
  });

  it("keeps persistent button selections on the pressed texture", () => {
    expect(css).toMatch(
      /html\[data-theme="eye-care"\] \.btn\.primary\.is-selected\s*\{[^}]*button-main-pressed\.png/s,
    );
    expect(chatView).toContain('showComfy ? "primary is-selected" : ""');
    expect(chatView).toContain("aria-pressed={showComfy}");
  });

  it("reserves a fixed rail for the support button", () => {
    expect(css).toMatch(/\.main\s*\{[^}]*margin-right:\s*80px;/s);
    expect(css).toMatch(/\.support-fab\s*\{[^}]*right:\s*24px;[^}]*width:\s*56px;/s);
  });

  it("uses the eye-care empty-state crest on the support button", () => {
    expect(css).toMatch(
      /html\[data-theme="eye-care"\] \.support-fab-emblem\s*\{[^}]*display:\s*block;[^}]*empty-state-crest\.png/s,
    );
  });

});
