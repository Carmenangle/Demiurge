export interface MaskPoint {
  x: number;
  y: number;
}

export interface MaskBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export function hexToRgb(hex: string): [number, number, number] {
  const value = hex.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(value)) return [0, 0, 0];
  return [
    Number.parseInt(value.slice(0, 2), 16),
    Number.parseInt(value.slice(2, 4), 16),
    Number.parseInt(value.slice(4, 6), 16),
  ];
}

export function mergeMaskBounds(a: MaskBounds | null, b: MaskBounds | null): MaskBounds | null {
  if (!a) return b;
  if (!b) return a;
  return {
    left: Math.min(a.left, b.left),
    top: Math.min(a.top, b.top),
    right: Math.max(a.right, b.right),
    bottom: Math.max(a.bottom, b.bottom),
  };
}

export function paintMaskCircle(
  mask: Uint8Array,
  width: number,
  height: number,
  point: MaskPoint,
  radius: number,
  value: 0 | 255,
  selection?: Uint8Array | null,
): MaskBounds | null {
  const r = Math.max(0.5, radius);
  const left = clamp(Math.floor(point.x - r), 0, width - 1);
  const top = clamp(Math.floor(point.y - r), 0, height - 1);
  const right = clamp(Math.ceil(point.x + r), 0, width - 1);
  const bottom = clamp(Math.ceil(point.y + r), 0, height - 1);
  const r2 = r * r;
  let changed = false;
  for (let y = top; y <= bottom; y += 1) {
    for (let x = left; x <= right; x += 1) {
      const dx = x + 0.5 - point.x;
      const dy = y + 0.5 - point.y;
      if (dx * dx + dy * dy > r2) continue;
      const index = y * width + x;
      if (selection && selection[index] === 0) continue;
      if (mask[index] !== value) {
        mask[index] = value;
        changed = true;
      }
    }
  }
  return changed ? { left, top, right: right + 1, bottom: bottom + 1 } : null;
}

export function paintMaskLine(
  mask: Uint8Array,
  width: number,
  height: number,
  from: MaskPoint,
  to: MaskPoint,
  radius: number,
  value: 0 | 255,
  selection?: Uint8Array | null,
): MaskBounds | null {
  const distance = Math.hypot(to.x - from.x, to.y - from.y);
  const steps = Math.max(1, Math.ceil(distance / Math.max(1, radius * 0.45)));
  let bounds: MaskBounds | null = null;
  for (let step = 0; step <= steps; step += 1) {
    const ratio = step / steps;
    bounds = mergeMaskBounds(bounds, paintMaskCircle(
      mask,
      width,
      height,
      { x: from.x + (to.x - from.x) * ratio, y: from.y + (to.y - from.y) * ratio },
      radius,
      value,
      selection,
    ));
  }
  return bounds;
}

export function fillMask(
  mask: Uint8Array,
  value: 0 | 255,
  selection?: Uint8Array | null,
): void {
  if (!selection) {
    mask.fill(value);
    return;
  }
  for (let index = 0; index < mask.length; index += 1) {
    if (selection[index]) mask[index] = value;
  }
}

export function editMaskRgba(mask: Uint8Array): Uint8ClampedArray {
  const pixels = new Uint8ClampedArray(mask.length * 4);
  for (let index = 0; index < mask.length; index += 1) {
    const offset = index * 4;
    pixels[offset] = 255;
    pixels[offset + 1] = 255;
    pixels[offset + 2] = 255;
    // OpenAI-compatible edits: transparent pixels are replaced, opaque pixels are preserved.
    pixels[offset + 3] = mask[index] ? 0 : 255;
  }
  return pixels;
}

export function polygonSelection(width: number, height: number, points: MaskPoint[]): Uint8Array {
  const selection = new Uint8Array(width * height);
  if (points.length < 3) return selection;
  const minX = clamp(Math.floor(Math.min(...points.map((point) => point.x))), 0, width - 1);
  const maxX = clamp(Math.ceil(Math.max(...points.map((point) => point.x))), 0, width - 1);
  const minY = clamp(Math.floor(Math.min(...points.map((point) => point.y))), 0, height - 1);
  const maxY = clamp(Math.ceil(Math.max(...points.map((point) => point.y))), 0, height - 1);
  for (let y = minY; y <= maxY; y += 1) {
    for (let x = minX; x <= maxX; x += 1) {
      const px = x + 0.5;
      const py = y + 0.5;
      let inside = false;
      for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
        const a = points[i];
        const b = points[j];
        if ((a.y > py) !== (b.y > py)
          && px < ((b.x - a.x) * (py - a.y)) / (b.y - a.y) + a.x) {
          inside = !inside;
        }
      }
      if (inside) selection[y * width + x] = 1;
    }
  }
  return selection;
}

export function magicWandSelection(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  startX: number,
  startY: number,
  tolerance = 32,
): Uint8Array {
  const selection = new Uint8Array(width * height);
  if (width <= 0 || height <= 0 || pixels.length < width * height * 4) return selection;
  const x0 = clamp(Math.floor(startX), 0, width - 1);
  const y0 = clamp(Math.floor(startY), 0, height - 1);
  const start = y0 * width + x0;
  const source = start * 4;
  const target = [pixels[source], pixels[source + 1], pixels[source + 2], pixels[source + 3]];
  const threshold = tolerance * tolerance * 3;
  const visited = new Uint8Array(width * height);
  const queue = new Int32Array(width * height);
  let head = 0;
  let tail = 0;
  queue[tail++] = start;
  visited[start] = 1;
  while (head < tail) {
    const index = queue[head++];
    const offset = index * 4;
    const dr = pixels[offset] - target[0];
    const dg = pixels[offset + 1] - target[1];
    const db = pixels[offset + 2] - target[2];
    const da = pixels[offset + 3] - target[3];
    if (dr * dr + dg * dg + db * db + da * da > threshold) continue;
    selection[index] = 1;
    const x = index % width;
    const y = Math.floor(index / width);
    const neighbors = [
      x > 0 ? index - 1 : -1,
      x + 1 < width ? index + 1 : -1,
      y > 0 ? index - width : -1,
      y + 1 < height ? index + width : -1,
    ];
    for (const next of neighbors) {
      if (next >= 0 && !visited[next]) {
        visited[next] = 1;
        queue[tail++] = next;
      }
    }
  }
  return selection;
}
