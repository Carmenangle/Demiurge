// @ts-expect-error Test runtime provides node:fs; production code does not depend on Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("green theme asset and ornament constraints", () => {
  it("uses sliced button frames for every state", () => {
    for (const family of ["main", "secondary"]) {
      for (const state of ["default", "hover", "pressed", "disabled"]) {
        expect(css).toContain(
          `border-image-source: url("/controls/green/button-${family}-${state}.png")`,
        );
      }
    }
  });

  it("maps green backgrounds, composer ornaments, emblem and avatar lifecycle", () => {
    expect(css).toMatch(
      /html\[data-theme="green"\] \.main\s*\{[^}]*chat-tide-vein\.webp/s,
    );
    expect(css).toMatch(
      /html\[data-theme="green"\] \.chat-stream\s*\{[^}]*chat-institute-atrium\.webp/s,
    );
    expect(css).toContain('/textures/green/tide-vein-strip.webp');
    expect(css).toContain('/ornaments/green/empty-state-symbiosis.png');
    for (const name of ["main", "listening", "thinking", "success"]) {
      expect(css).toContain(`/support/green/112/hostess-${name}.png`);
    }
    for (const name of ["top", "handle", "divider"]) {
      expect(css).toContain(`/controls/green/composer-${name}.png`);
    }
  });

  it("keeps modal ornaments behind content and outside actions", () => {
    expect(css).toMatch(/html\[data-theme="green"\] \.modal-actions\s*\{[^}]*padding-right:\s*64px;/s);
    expect(css).toMatch(
      /html\[data-theme="green"\] \.modal::before,[\s\S]*?calyx-corner-cut\.png/,
    );
  });

  it("keeps green layout usable on narrow screens", () => {
    expect(css).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?html\[data-theme="green"\] \.layout\s*\{[^}]*min-width:\s*0;[^}]*flex-direction:\s*column;/,
    );
    expect(css).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?html\[data-theme="green"\] \.main\s*\{[^}]*margin-right:\s*0;[^}]*padding-bottom:\s*72px;/,
    );
  });
});
