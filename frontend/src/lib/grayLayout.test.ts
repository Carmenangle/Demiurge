// @ts-expect-error Test runtime provides node:fs; production code does not depend on Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("gray theme asset and ornament constraints", () => {
  it("uses sliced button frames for every state", () => {
    for (const family of ["main", "secondary"]) {
      for (const state of ["default", "hover", "pressed", "disabled"]) {
        expect(css).toContain(
          `border-image-source: url("/controls/gray/button-${family}-${state}.png")`,
        );
      }
    }
  });

  it("maps gray backgrounds, composer, emblem and avatar lifecycle", () => {
    expect(css).toMatch(/html\[data-theme="gray"\] \.main\s*\{[^}]*chat-clause-workbench\.webp/s);
    expect(css).toMatch(/html\[data-theme="gray"\] \.chat-stream\s*\{[^}]*chat-contract-review-office\.webp/s);
    expect(css).toContain('/textures/gray/contract-clause-strip.webp');
    expect(css).toContain('/ornaments/gray/empty-state-contract-file.png');
    for (const name of ["main", "listening", "thinking", "success"]) {
      expect(css).toContain(`/support/gray/112/hostess-${name}.png`);
    }
    for (const name of ["top", "handle", "divider"]) {
      expect(css).toContain(`/controls/gray/composer-${name}.png`);
    }
  });

  it("keeps modal ornaments outside the action area", () => {
    expect(css).toMatch(/html\[data-theme="gray"\] \.modal-actions\s*\{[^}]*padding-right:\s*64px;/s);
    expect(css).toMatch(/html\[data-theme="gray"\] \.modal::before,[\s\S]*?corner-fold-red\.png/);
  });

  it("keeps gray usable on narrow screens", () => {
    expect(css).toMatch(/@media \(max-width: 720px\)[\s\S]*?html\[data-theme="gray"\] \.layout\s*\{[^}]*min-width:\s*0;[^}]*flex-direction:\s*column;/);
    expect(css).toMatch(/@media \(max-width: 720px\)[\s\S]*?html\[data-theme="gray"\] \.main\s*\{[^}]*margin-right:\s*0;[^}]*padding-bottom:\s*72px;/);
  });
});
