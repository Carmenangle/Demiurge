import { describe, expect, it } from "vitest";
import {
  editMaskRgba,
  fillMask,
  hexToRgb,
  magicWandSelection,
  paintMaskLine,
  polygonSelection,
} from "./maskEditor";

describe("mask editor algorithms", () => {
  it("constrains brush and eraser strokes to the active selection", () => {
    const mask = new Uint8Array(25);
    const selection = new Uint8Array(25);
    selection[12] = 1;

    paintMaskLine(mask, 5, 5, { x: 0, y: 2.5 }, { x: 5, y: 2.5 }, 2, 255, selection);
    expect(Array.from(mask).filter(Boolean)).toHaveLength(1);
    expect(mask[12]).toBe(255);

    paintMaskLine(mask, 5, 5, { x: 2.5, y: 2.5 }, { x: 2.5, y: 2.5 }, 2, 0, selection);
    expect(mask[12]).toBe(0);
  });

  it("fills the full image without a selection and only the range with one", () => {
    const mask = new Uint8Array(9);
    fillMask(mask, 255);
    expect(Array.from(mask)).toEqual(new Array(9).fill(255));

    const selection = new Uint8Array(9);
    selection[4] = 1;
    fillMask(mask, 0, selection);
    expect(mask[4]).toBe(0);
    expect(mask[0]).toBe(255);
  });

  it("rasterizes lasso polygons and connected magic-wand regions", () => {
    const lasso = polygonSelection(4, 4, [
      { x: 1, y: 1 }, { x: 3, y: 1 }, { x: 3, y: 3 }, { x: 1, y: 3 },
    ]);
    expect(Array.from(lasso).filter(Boolean)).toHaveLength(4);

    const pixels = new Uint8ClampedArray([
      10, 10, 10, 255, 10, 10, 10, 255, 240, 240, 240, 255,
      10, 10, 10, 255, 12, 12, 12, 255, 240, 240, 240, 255,
    ]);
    const wand = magicWandSelection(pixels, 3, 2, 0, 0, 8);
    expect(Array.from(wand)).toEqual([1, 1, 0, 1, 1, 0]);
  });

  it("parses the selected mask color", () => {
    expect(hexToRgb("#12a0ff")).toEqual([18, 160, 255]);
  });

  it("exports painted pixels as transparent editable mask regions", () => {
    expect(Array.from(editMaskRgba(new Uint8Array([0, 255])))).toEqual([
      255, 255, 255, 255,
      255, 255, 255, 0,
    ]);
  });
});
