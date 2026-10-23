// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  SCENE_RESOURCE_PREFIX,
  collectSceneResourcePaths,
  readScenePackage,
  rewriteSceneResourceUrls,
} from "./decodeScenePackage";
import { deflateSync, inflateSync } from "./shims/nodeZlib";
import { asBuffer } from "./shims/nodeBuffer";
import { decodePngToRgba, encodePng } from "./vendor/we-scene-manifest";
import type { WeSceneManifest } from "./vendor/we-scene-manifest";

const encoder = new TextEncoder();

/**
 * 造一个最小 PKGV 包：条目全部按未压缩存放。
 * （LZ4 block chain 的解码由上游 vendored 代码负责，这里不重复实现它的编码端；
 * 真包的 LZ4 路径在真机 e2e 里覆盖。）
 */
function buildPkg(entries: Array<{ path: string; bytes: Uint8Array }>): Uint8Array {
  const magic = encoder.encode("PKGV0001");
  const pathBytes = entries.map((entry) => encoder.encode(entry.path));
  let indexSize = 4 + magic.length + 4;
  entries.forEach((entry, i) => { indexSize += 4 + pathBytes[i].length + 8; void entry; });
  const dataSize = entries.reduce((total, entry) => total + entry.bytes.length, 0);
  const out = new Uint8Array(indexSize + dataSize);
  const view = new DataView(out.buffer);
  let pos = 0;
  let cursor = indexSize;
  const offsets = entries.map((entry) => {
    const at = cursor;
    cursor += entry.bytes.length;
    return at;
  });
  const i32 = (value: number) => { view.setInt32(pos, value, true); pos += 4; };
  const u32 = (value: number) => { view.setUint32(pos, value, true); pos += 4; };
  const raw = (bytes: Uint8Array) => { out.set(bytes, pos); pos += bytes.length; };

  i32(magic.length);
  raw(magic);
  i32(entries.length);
  entries.forEach((entry, i) => {
    i32(pathBytes[i].length);
    raw(pathBytes[i]);
    u32(offsets[i] - indexSize);
    u32(entry.bytes.length);
  });
  for (const entry of entries) raw(entry.bytes);
  return out;
}

const jsonEntry = (path: string, value: unknown) => ({ path, bytes: encoder.encode(JSON.stringify(value)) });

/** 「1 个 image 图层 + 1 张纹理」的可渲染场景包；纹理放裸 PNG，走解析失败原样回吐那条分支。 */
function fixturePackage(): { bytes: Uint8Array; png: Uint8Array } {
  const rgba = new Uint8Array(4 * 4 * 4);
  for (let i = 0; i < rgba.length; i += 4) {
    rgba[i] = 10; rgba[i + 1] = 20; rgba[i + 2] = 30; rgba[i + 3] = 255;
  }
  const png = new Uint8Array(encodePng(4, 4, rgba));
  const bytes = buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json", title: "Fixture" }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 1920, height: 1080 }, clearcolor: "0.1 0.2 0.3" },
      objects: [{ name: "bg", image: "bg.image.json", origin: "960 540 0", size: "1920 1080 0" }],
    }),
    jsonEntry("bg.image.json", { material: "bg.material.json", width: 1920, height: 1080 }),
    jsonEntry("bg.material.json", { passes: [{ shader: "genericimage2", textures: ["bg"] }] }),
    { path: "materials/bg.tex", bytes: png },
  ]);
  return { bytes, png };
}

describe("node:zlib 垫片", () => {
  it("deflate → inflate 往返保持字节一致（zlib 封装，不是裸 deflate）", () => {
    const raw = new Uint8Array(5000);
    for (let i = 0; i < raw.length; i += 1) raw[i] = i % 251;
    const packed = deflateSync(raw, { level: 9 });
    expect(packed[0]).toBe(0x78); // PNG IDAT 用的就是 zlib 封装
    const back = inflateSync(packed, { maxOutputLength: raw.length + 64 });
    expect(Array.from(back)).toEqual(Array.from(raw));
  });

  it("maxOutputLength 超限必须抛错，而不是静默截断", () => {
    const packed = deflateSync(new Uint8Array(4096).fill(7), { level: 1 });
    expect(() => inflateSync(packed, { maxOutputLength: 100 })).toThrow(/maxOutputLength/);
    expect(inflateSync(packed, { maxOutputLength: 4096 }).length).toBe(4096);
  });

  it("asBuffer 零拷贝包住字节", () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const wrapped = asBuffer(bytes);
    expect(wrapped.length).toBe(4);
    expect(wrapped.buffer).toBe(bytes.buffer);
  });

  it("encodePng 的产物能被上游 PNG 解码读回（Buffer / zlib 互操作成立）", () => {
    const rgba = new Uint8Array([
      255, 0, 0, 255, 0, 255, 0, 128,
      0, 0, 255, 255, 255, 255, 255, 0,
    ]);
    const png = new Uint8Array(encodePng(2, 2, rgba));
    expect(Array.from(png.slice(0, 8))).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const decoded = decodePngToRgba(png);
    expect([decoded.width, decoded.height]).toEqual([2, 2]);
    expect(Array.from(decoded.rgba)).toEqual(Array.from(rgba));
  });
});

describe("readScenePackage", () => {
  it("从合成 PKGV 包解出清单与纹理字节", () => {
    const { bytes, png } = fixturePackage();
    const bundle = readScenePackage(bytes);
    expect([bundle.manifest.width, bundle.manifest.height]).toEqual([1920, 1080]);
    expect(bundle.manifest.layers).toHaveLength(1);
    expect(bundle.textureCount).toBe(1);
    const resource = bundle.resources["materials/bg.tex"];
    expect(Array.from(resource)).toEqual(Array.from(png));
    // 必须是独立分配：共享包体 ArrayBuffer 的视图没法 transfer
    expect(resource.buffer.byteLength).toBe(png.length);
  });

  it("清单里的纹理引用带可预测前缀，便于换成 blob", () => {
    const bundle = readScenePackage(fixturePackage().bytes);
    const layer = bundle.manifest.layers[0] as { texUrl: string };
    expect(layer.texUrl).toBe(`${SCENE_RESOURCE_PREFIX}materials/bg.tex`);
    expect(collectSceneResourcePaths(bundle.manifest)).toEqual(["materials/bg.tex"]);
    expect(bundle.manifest.clearColor).toEqual([0.1, 0.2, 0.3]);
  });

  it("没有可用场景结构 / 不是 PKGV 包都要抛可读错误", () => {
    const empty = buildPkg([
      jsonEntry("project.json", { type: "scene", file: "scene.json" }),
      jsonEntry("scene.json", { general: {}, objects: [] }),
    ]);
    expect(() => readScenePackage(empty)).toThrow(/scene\.pkg/);
    expect(() => readScenePackage(new Uint8Array([1, 2, 3, 4]))).toThrow(/pkg/);
  });
});

describe("rewriteSceneResourceUrls", () => {
  const manifest = {
    width: 100,
    height: 100,
    meteorTex: `${SCENE_RESOURCE_PREFIX}fx/meteor.tex`,
    layers: [
      { name: "a", texUrl: `${SCENE_RESOURCE_PREFIX}materials/bg.tex`, uvCrop: [0, 0, 1, 1] },
      { name: "b", texUrls: [`${SCENE_RESOURCE_PREFIX}x.tex`] },
    ],
    models: [{ posB64: "AAAA", texUrl: `${SCENE_RESOURCE_PREFIX}m.tex` }],
  } as unknown as WeSceneManifest;

  it("只替换前缀命中的字符串，数组 / 嵌套对象 / base64 原样保留", () => {
    const rewritten = rewriteSceneResourceUrls(manifest, (path) => `blob:/generated/${path}`);
    const value = rewritten as unknown as typeof manifest;
    expect(value.meteorTex).toBe("blob:/generated/fx/meteor.tex");
    expect((value.layers[0] as { texUrl: string }).texUrl).toBe("blob:/generated/materials/bg.tex");
    expect((value.layers[0] as { uvCrop: number[] }).uvCrop).toEqual([0, 0, 1, 1]);
    expect((value.layers[1] as { texUrls: string[] }).texUrls).toEqual(["blob:/generated/x.tex"]);
    expect((value.models as { posB64: string }[])[0].posB64).toBe("AAAA");
  });

  it("换不出 URL 的纹理保留原引用（播放器加载失败即跳过该图层，不影响其余图层）", () => {
    const rewritten = rewriteSceneResourceUrls(manifest, () => null);
    expect((rewritten as unknown as typeof manifest).meteorTex)
      .toBe(`${SCENE_RESOURCE_PREFIX}fx/meteor.tex`);
  });

  it("不改入参：返回新结构，原清单仍可复用", () => {
    rewriteSceneResourceUrls(manifest, () => "blob:/x");
    expect((manifest as unknown as typeof manifest).meteorTex)
      .toBe(`${SCENE_RESOURCE_PREFIX}fx/meteor.tex`);
  });
});

describe("vendored 源码约束", () => {
  it("清单模块里不得残留 node: 说明符（垫片必须齐）", () => {
    const source = readFileSync(new URL("./vendor/we-scene-manifest.js", import.meta.url), "utf8");
    expect(source).not.toMatch(/from ['"]node:/);
    expect(source).toMatch(/from ['"]\.\.\/shims\/nodeZlib['"]/);
  });

  it("播放器 HTML 本体不得被改写（本地只加了文件头注释）", () => {
    const source = readFileSync(new URL("./vendor/we-scene-player-html.js", import.meta.url), "utf8")
      // Windows 签出可能是 CRLF，头部比对与换行数无关。
      .replace(/\r\n/g, "\n");
    expect(source.startsWith("/**\n * @license MIT")).toBe(true);
    expect(source).toContain("export const WE_SCENE_PLAYER_HTML = `<!DOCTYPE html>");
    // scenePlayer 的垫片注入靠「只有 1 个 <script>」才能安全地函数式 replace，多一个就注错位置。
    expect(source.match(/<script>/g)?.length).toBe(1);
  });
});
