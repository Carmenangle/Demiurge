// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  SCENE_RESOURCE_PREFIX,
  collectSceneResourcePaths,
  readScenePackage,
  rewriteSceneResourceUrls,
  type SceneDecodeCache,
} from "./decodeScenePackage";
import { deflateSync, inflateSync } from "./shims/nodeZlib";
import { asBuffer } from "./shims/nodeBuffer";
import { decodePngToRgba, decodeTex, encodePng, parseMdlRig } from "./vendor/we-scene-manifest";
import type { SceneUserProperties, WeSceneManifest } from "./vendor/we-scene-manifest";

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

/** 造一个最小 TEXV0005 容器（TEXB0001 = 未压缩 mipmap），只填解码要读的字段。 */
function buildRawTex(
  format: number,
  width: number,
  height: number,
  pixels: Uint8Array,
  sheet?: { frameWidth: number; frameHeight: number; count: number },
): Uint8Array {
  const chunks: number[] = [];
  const push = (bytes: ArrayLike<number> | string) => {
    if (typeof bytes === "string") chunks.push(...encoder.encode(bytes), 0);
    else for (let i = 0; i < bytes.length; i++) chunks.push(bytes[i]);
  };
  const i32 = (value: number) => {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setInt32(0, value, true);
    push(b);
  };
  const f32 = (value: number) => {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setFloat32(0, value, true);
    push(b);
  };
  push("TEXV0005");
  push("TEXI0001");
  i32(format);
  // flags：只有置了 GIF 位（4）后面才会跟一段 TEXS 帧表
  i32(sheet ? 4 : 0);
  i32(width);
  i32(height);
  i32(width); // 有效图像尺寸与纹理一致 ⇒ cropToImageRect 不裁
  i32(height);
  i32(0); // unknown
  push("TEXB0001");
  i32(1); // imageCount
  i32(1); // mipmapCount
  i32(width);
  i32(height);
  i32(pixels.length);
  push(pixels);
  if (sheet) {
    // TEXS0001 每帧：imageId、frametime(秒)、x/y/width/height（后两格是宽Y/高X，读法忽略）
    push("TEXS0001");
    i32(sheet.count);
    const cols = Math.max(1, Math.floor(width / sheet.frameWidth));
    for (let i = 0; i < sheet.count; i++) {
      i32(0);
      f32(0.015625);
      i32((i % cols) * sheet.frameWidth);
      i32(Math.floor(i / cols) * sheet.frameHeight);
      i32(sheet.frameWidth);
      i32(sheet.frameHeight);
      i32(sheet.frameWidth);
      i32(sheet.frameHeight);
    }
  }
  return Uint8Array.from(chunks);
}

describe("TEX 通道语义（R8 / RG88）", () => {  it("单通道与双通道贴图把「值」解成透明度，不是解成不透明灰 / 红绿斑", () => {
    // WE 采样 albedo 前先做通道换算：R8 = 白底 + 通道值作 alpha，RG88 = R 作灰度颜色 + G 作 alpha。
    // 直解成 (v,v,v,255) / (R,G,0,255) 会让官方雾与光束贴图从「半透明软团」变成整块不透明的色斑，
    // 加色混合下整屏刷花（真机 3346715292 的雾就是这个现象）。
    const r8 = decodeTex(buildRawTex(9, 2, 1, new Uint8Array([0, 200])));
    expect(Array.from(r8.rgba)).toEqual([255, 255, 255, 0, 255, 255, 255, 200]);

    const rg = decodeTex(buildRawTex(8, 2, 1, new Uint8Array([40, 10, 60, 20])));
    expect(Array.from(rg.rgba)).toEqual([40, 40, 40, 10, 60, 60, 60, 20]);
  });

  it("带 TEXS 帧表的序列页把帧表原样交回（内置雾贴图靠它才知道格宽）", () => {
    // 官方雾贴图是一张 4×4 的 2×2 序列页，`.tex` 里没有文件名可解析，只有 TEXS 表说得出「一格多大」。
    const sheet = buildRawTex(0, 4, 4, new Uint8Array(4 * 4 * 4), { frameWidth: 2, frameHeight: 2, count: 4 });
    const frames = decodeTex(sheet).frames;
    expect(frames?.length).toBe(4);
    expect(frames?.[0]).toMatchObject({ width: 2, height: 2, frametime: 0.015625 });
    expect(frames?.[1]).toMatchObject({ x: 2, y: 0, width: 2, height: 2 });
    expect(decodeTex(buildRawTex(8, 2, 1, new Uint8Array(4))).frames).toBeUndefined();
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

  it("保留 WE 的视差深度（vec2 字符串，缺省 [0, 0]）", () => {
    const rgba = new Uint8Array(4 * 4 * 4);
    const png = new Uint8Array(encodePng(4, 4, rgba));
    const bytes = buildPkg([
      jsonEntry("project.json", { type: "scene", file: "scene.json", title: "Fixture" }),
      jsonEntry("scene.json", {
        general: { orthogonalprojection: { width: 1920, height: 1080 } },
        objects: [
          { name: "fg", image: "bg.image.json", origin: "960 540 0", size: "1920 1080 0", parallaxDepth: "0.79 -0.64" },
          { name: "bg", image: "bg.image.json", origin: "960 540 0", size: "1920 1080 0" },
        ],
      }),
      jsonEntry("bg.image.json", { material: "bg.material.json", width: 1920, height: 1080 }),
      jsonEntry("bg.material.json", { passes: [{ shader: "genericimage2", textures: ["bg"] }] }),
      { path: "materials/bg.tex", bytes: png },
    ]);
    const layers = readScenePackage(bytes).manifest.layers as Array<{ name: string; depth: number[] }>;
    expect(layers.map((layer) => [layer.name, layer.depth])).toEqual([
      ["fg", [0.79, -0.64]],
      ["bg", [0, 0]],
    ]);
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

/**
 * 视频纹理：WE 把 MP4 直接当 mip0 载荷（TEXB0004 video 标记）。
 * 真机 3723257973 的人像主图就是这样一张 67MB 的 `.tex`——按静态贴图解必失败。
 */
const fakeMp4 = () => new Uint8Array([
  0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 0x6d, 0x70, 0x34, 0x32,
  0, 0, 0, 0, 0x6d, 0x70, 0x34, 0x31, 0x69, 0x73, 0x6f, 0x6d,
]);

function videoPackage(): Uint8Array {
  return buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json", title: "Video" }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 3840, height: 2160 } },
      objects: [{ name: "art", image: "art.image.json", origin: "1920 1080 0", size: "3840 2160 0" }],
    }),
    jsonEntry("art.image.json", { material: "art.material.json", width: 3840, height: 2160 }),
    jsonEntry("art.material.json", { passes: [{ shader: "genericimage4", textures: ["art"] }] }),
    { path: "materials/art.tex", bytes: buildRawTex(0, 3840, 2160, fakeMp4()) },
  ]);
}

describe("视频纹理（MP4-backed .tex）", () => {
  it("资源通道交回裸 MP4 而不是整个 TEX 容器，并给图层打 videoTex", () => {
    const bundle = readScenePackage(videoPackage());
    // 旧行为是把整段 TEXV0005 原样回吐、blob 标 image/png ⇒ <img> 必失败 ⇒ 图层被静默跳过。
    expect(Array.from(bundle.resources["materials/art.tex"])).toEqual(Array.from(fakeMp4()));
    const layer = bundle.manifest.layers[0] as Record<string, unknown>;
    expect(layer.videoTex).toBe(true);
  });

  it("热重建（字节来自缓存、本次不回传）仍保留 videoTex 标注", () => {
    const bytes = videoPackage();
    const cache: SceneDecodeCache = { textures: new Map() };
    const first = readScenePackage(bytes, undefined, cache);
    const rebuilt = readScenePackage(bytes, undefined, {
      textures: cache.textures,
      knownPaths: new Set(first.paths),
    });
    expect(rebuilt.resources).toEqual({});
    expect((rebuilt.manifest.layers[0] as Record<string, unknown>).videoTex).toBe(true);
  });

  it("普通 PNG 贴图不会被误标", () => {
    const layer = readScenePackage(fixturePackage().bytes).manifest.layers[0] as Record<string, unknown>;
    expect(layer.videoTex).toBeUndefined();
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

  it("播放器保留 puppet 网格绘制分支（清单产出 mesh 层后必须真的走 drawElements）", () => {
    const source = readFileSync(new URL("./vendor/we-scene-player-html.js", import.meta.url), "utf8");
    expect(source).toContain("function drawBasicMeshLayer(");
    expect(source).toContain("gl.drawElements(gl.TRIANGLES");
    // 网格顶点是 puppet 本地单位，必须按对象 scale 变换，不能当成像素矩形。
    expect(source).toContain("layer.meshScale");
    // 蒙皮与附件跟随同样要有落点：清单给了蒙皮数组却没人消费的话，人物会退回 bind pose 静止。
    expect(source).toContain("function skinGpuMesh(");
    expect(source).toContain("function followDelta(");
  });

  it("水flow 分支不得用 REPEAT 取内容图（WebGL1 的 NPOT 贴图会整张失效）", () => {
    const source = readFileSync(new URL("./vendor/we-scene-player-html.js", import.meta.url), "utf8");
    const branch = source.slice(source.indexOf("if (layer.flow"), source.indexOf("// Standard Layer"));
    expect(branch, "flow 分支没找到").toContain("layer.flow.enabled !== false");
    // 本包天空 4200x2500 不是 2 的幂：REPEAT 让纹理 incomplete ⇒ 取样恒 (0,0,0,1) ⇒ 天空画成黑块。
    expect(branch).toContain("loadTexture(layer.texUrl, false, !!layer.videoTex)");
    expect(branch).not.toMatch(/loadTexture\(layer\.texUrl, true\)/);
    // 环绕改在着色器里做（fract），位移越界才折回对边而不是被 CLAMP 拉成边条。
    expect(source).toMatch(/texture2D\(u_tex, *fract\(/);
    // 像素→UV 的唯一换算点不能被删：strength 是 WE 的像素档。
    expect(branch).toContain("/ Math.max(1, flowContent.width)");
  });

  it("视频纹理由渲染循环每帧重采：rVFC 只是加速通道，不能当唯一通道", () => {
    const source = readFileSync(new URL("./vendor/we-scene-player-html.js", import.meta.url), "utf8");
    const branch = source.slice(
      source.indexOf("if (video) {"),
      source.indexOf("const img = new Image();"),
    );
    expect(branch, "视频分支没找到").toContain("requestVideoFrameCallback");
    // 3840×2160 是 NPOT：默认 REPEAT 会让纹理 incomplete，采样恒黑。
    expect(branch).toContain("CLAMP_TO_EDGE");
    expect(source).toContain("loadTexture(layer.texUrl, false, !!layer.videoTex)");
    expect(source).toContain("rec.video.pause()");
    // 换预设后不在 live 集合里的视频要被摘下并停播，否则 4K 解码永不停止。
    expect(source).toContain("url.endsWith('|video')");

    // 真机（WebView2 / Edge 155，2026-10-25）实锤：1px + opacity:0 挂在 body 外的 <video>
    // 只是纹理源，合成器不呈现它 ⇒ rVFC 连续 12 秒一次都不回调，而 currentTime 照常 1:1 推进、
    // totalVideoFrames 每秒 +30。只挂 rVFC 时美术层永久停在最后一次 loadeddata 的那张图上
    // ⇒ 用户看到「艺术字还在走，壁纸不动了」。可靠通道只有渲染循环自己按 currentTime 重传。
    expect(branch).toContain("videoTextures.add(record)");
    expect(source).toContain("function pumpVideoTextures(now) {");
    // uploadVideo 必须有调用方（缺陷形态正是「定义了却没人调」的整条死代码）。
    expect(source).toContain("record.uploadVideo();");
    expect(source).toContain("el.currentTime === record.videoTime");
    // 调用点必须在 renderFrame 内部：挂在别处（事件里、初始化里）等于没挂。
    const renderStart = source.indexOf("function renderFrame(now) {");
    const pumpCall = source.indexOf("pumpVideoTextures(now);", renderStart);
    expect(renderStart, "renderFrame 没找到").toBeGreaterThan(-1);
    expect(pumpCall, "渲染循环没有重新采样视频纹理").toBeGreaterThan(renderStart);
    // 回收时摘出视频集合，否则泵会永久遍历已 disposed 的记录。
    expect(
      source.slice(source.indexOf("function pruneTextures("), source.indexOf("function pumpVideoTextures")),
    ).toContain("videoTextures.delete(record)");
    // 播放被拒要能自愈：创建时那次 play() 的 rejection 是被吞掉的，不重试就没有第二次机会。
    expect(source).toContain("const mayRetry = now - lastVideoPlayRetry > 1000;");
  });

  it("播放器脚本能被解析（模板字符串里的 JS 不能有语法错误）", async () => {
    const { WE_SCENE_PLAYER_HTML } = await import("./vendor/we-scene-player-html");
    const match = WE_SCENE_PLAYER_HTML.match(/<script>([\s\S]*)<\/script>/);
    expect(match).toBeTruthy();
    expect(() => new Function(match![1])).not.toThrow();
  });
});

/* ------------------------------------------------------------------ */
/* puppet 网格（MDLV + MDLS + MDAT）：拼图式图集必须按网格绘制，附件按骨骼落位 */
/* ------------------------------------------------------------------ */

const u32 = (value: number) => {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setUint32(0, value, true);
  return bytes;
};
const i32 = (value: number) => {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setInt32(0, value, true);
  return bytes;
};
const u16 = (value: number) => {
  const bytes = new Uint8Array(2);
  new DataView(bytes.buffer).setUint16(0, value, true);
  return bytes;
};
const cstr = (text: string) => {
  const raw = encoder.encode(text);
  const out = new Uint8Array(raw.length + 1);
  out.set(raw);
  return out;
};
const concat = (...inputs: Array<Uint8Array | Uint8Array[]>) => {
  const parts = inputs.flat() as Uint8Array[];
  const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
};
/** 列主序 4x4：单位旋转 + 平移 (tx, ty)。 */
const translatedMatrix = (tx: number, ty: number) => {
  const matrix = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, tx, ty, 0, 1]);
  return new Uint8Array(matrix.buffer);
};
const identityBone = () => concat([
  new Uint8Array([0]), // flag
  i32(0), // f1
  i32(-1), // parent
  u32(64), // matrix bytes
  translatedMatrix(10, 20),
  cstr("{}"),
]);

/** MDLV0015（pos+uv，无 AABB）：1 个三角形 + 1 根骨头 + 1 个名为“头”的挂点。 */
function puppetMdl(): Uint8Array {
  const vertices = new Float32Array([
    0, 0, 0, 0, 1,
    100, 0, 0, 1, 1,
    0, 100, 0, 0, 0,
  ]);
  const mesh = concat([
    cstr("materials/puppet-mat.json"),
    u32(0), // flagA
    u32(0x8), // meshFlag: position + uv
    u32(vertices.byteLength),
    new Uint8Array(vertices.buffer),
    u32(6),
    new Uint8Array(new Uint16Array([0, 1, 2]).buffer),
  ]);
  const meshSection = concat([cstr("MDLV0015"), u32(0x8), u32(1), u32(1), mesh]);
  const bone = identityBone();
  const mdlsHeader = cstr("MDLS0003");
  const mdatOffset = meshSection.length + mdlsHeader.length + 4 + 4 + bone.length;
  const mdls = concat([mdlsHeader, u32(mdatOffset), u32(1), bone]);
  const attachment = concat([u16(0), cstr("头"), translatedMatrix(5, 6)]);
  const mdat = concat([cstr("MDAT0001"), u32(0), u16(1), attachment]);
  return concat(meshSection, mdls, mdat);
}

/** 影子/夜模式结构：隐藏组里的子对象不得渲染；附件与对象 {value} origin 必须生效。 */
function puppetFixturePackage(): Uint8Array {
  const rgba = new Uint8Array(4 * 4 * 4).fill(255);
  const png = new Uint8Array(encodePng(4, 4, rgba));
  return buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json" }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 1920, height: 1080 }, clearcolor: "0 0 0" },
      objects: [
        { id: 1, name: "hidden-group", origin: { value: "500 300 0" }, visible: { value: false } },
        { id: 2, name: "hidden-child", parent: 1, image: "models/plain.json", origin: "0 0 0", size: "60 60 0" },
        { id: 3, name: "puppet", parent: 6, image: "models/puppet.json", origin: "100 200 0", size: "200 200 0" },
        { id: 4, name: "hair", parent: 3, image: "models/hair.json", attachment: "头", origin: "1 2 0", size: "50 50 0" },
        { id: 5, name: "bg", image: "models/bg.json", origin: { value: "100 200 0" }, size: "20 20 0" },
        { id: 6, name: "group", origin: { value: "500 300 0" }, scale: { value: "1 1 1" } },
      ],
    }),
    jsonEntry("models/puppet.json", { autosize: true, material: "materials/body.json", puppet: "models/puppet_puppet.mdl" }),
    jsonEntry("models/hair.json", { autosize: true, material: "materials/hair.json", cropoffset: "-150 522.5" }),
    jsonEntry("models/plain.json", { autosize: true, material: "materials/plain.json" }),
    jsonEntry("models/bg.json", { autosize: true, material: "materials/bg.json" }),
    jsonEntry("materials/body.json", { passes: [{ shader: "genericimage4", textures: ["body"] }] }),
    jsonEntry("materials/puppet-mat.json", { passes: [{ shader: "genericimage4", textures: ["mesh"] }] }),
    jsonEntry("materials/hair.json", { passes: [{ shader: "genericimage2", textures: ["hair"] }] }),
    jsonEntry("materials/plain.json", { passes: [{ shader: "genericimage2", textures: ["plain"] }] }),
    jsonEntry("materials/bg.json", { passes: [{ shader: "genericimage2", textures: ["bg"] }] }),
    { path: "models/puppet_puppet.mdl", bytes: puppetMdl() },
    { path: "materials/body.tex", bytes: png },
    { path: "materials/mesh.tex", bytes: png },
    { path: "materials/hair.tex", bytes: png },
    { path: "materials/plain.tex", bytes: png },
    { path: "materials/bg.tex", bytes: png },
  ]);
}

const f32 = (value: number) => {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setFloat32(0, value, true);
  return bytes;
};
const boneRecord = (parent: number, tx: number, ty: number) => concat([
  new Uint8Array([0]),
  u32(0),
  i32(parent),
  u32(64),
  translatedMatrix(tx, ty),
  cstr("{}"),
]);
/** MDLA：每根骨头 (frames+1) 个关键帧（9 个 f32），后跟 8 字节脚注，整段再跟 27 字节尾。 */
function mdlaClip(
  id: number,
  name: string,
  type: string,
  fps: number,
  frames: number,
  tracks: number[][],
): Uint8Array {
  const trackBytes = (frames + 1) * 36;
  const parts: Uint8Array[] = [
    u32(id), u32(0), cstr(name), cstr(type),
    f32(fps), u32(frames), u32(0), u32(tracks.length), u32(0), u32(trackBytes),
  ];
  for (const boneKeys of tracks) {
    const bytes = new Uint8Array(boneKeys.length * 4);
    const view = new DataView(bytes.buffer);
    boneKeys.forEach((value, i) => view.setFloat32(i * 4, value, true));
    parts.push(bytes);
    parts.push(new Uint8Array(8));
  }
  parts.push(new Uint8Array(27));
  return concat(parts);
}
const trsKey = (tx: number, ty: number, sx = 1, sy = 1) => [tx, ty, 0, 0, 0, 0, sx, sy, 1];
const trsKeys = (tx: number, ty: number, sx = 1, sy = 1) => [
  ...trsKey(tx, ty, sx, sy), ...trsKey(tx, ty, sx, sy), ...trsKey(tx, ty, sx, sy),
];

/**
 * 3 根骨骼 + 蒙皮权重 + MDLA 的 puppet：
 * - 骨骼 0 平移变化（基础/加性 clip 都动）
 * - 骨骼 1 静态且等于 bind（不得进 payload）
 * - 骨骼 2 静态但覆盖 bind（进 payload，加性里贡献零 delta）
 */
function animatedPuppetMdl(): Uint8Array {
  const stride = 52; // pos 12 + blend 16 + weights 16 + uv 8
  const verts = [
    { pos: [0, 0, 0], uv: [0, 1] },
    { pos: [100, 0, 0], uv: [1, 1] },
    { pos: [0, 100, 0], uv: [0, 0] },
  ];
  const vertexBytes = new Uint8Array(verts.length * stride);
  const view = new DataView(vertexBytes.buffer);
  verts.forEach((vert, i) => {
    let at = i * stride;
    for (const value of vert.pos) { view.setFloat32(at, value, true); at += 4; }
    for (let k = 0; k < 4; k++) { view.setUint32(at, 0, true); at += 4; }
    view.setFloat32(at, 1, true); at += 16;
    for (const value of vert.uv) { view.setFloat32(at, value, true); at += 4; }
  });
  const mesh = concat([
    cstr("materials/puppet-mat.json"),
    u32(0),
    u32(0x8 | 0x00800000 | 0x01000000),
    u32(vertexBytes.length),
    vertexBytes,
    u32(6),
    new Uint8Array(new Uint16Array([0, 1, 2]).buffer),
  ]);
  const meshSection = concat([cstr("MDLV0015"), u32(0x01800008), u32(1), u32(1), mesh]);
  const bones = [boneRecord(-1, 10, 20), boneRecord(0, 5, 0), boneRecord(1, 0, 0)];
  const boneBytes = concat(bones);
  const mdatOffset = meshSection.length + cstr("MDLS0003").length + 8 + boneBytes.length;
  const mdls = concat([cstr("MDLS0003"), u32(mdatOffset), u32(3), boneBytes]);
  const mdat = concat([
    cstr("MDAT0001"), u32(0), u16(1),
    u16(2), cstr("头"), translatedMatrix(2, 3),
  ]);
  const base = mdlaClip(10, "待机", "loop", 30, 2, [
    [...trsKey(10, 20), ...trsKey(20, 20), ...trsKey(10, 20)],
    trsKeys(5, 0),
    trsKeys(0, 7),
  ]);
  const additive = mdlaClip(11, "呼吸", "loop", 30, 2, [
    [...trsKey(10, 20), ...trsKey(15, 20), ...trsKey(10, 20)],
    trsKeys(5, 0),
    trsKeys(0, 7),
  ]);
  const click = mdlaClip(12, "双击", "single", 30, 2, [
    [...trsKey(10, 20), ...trsKey(30, 20), ...trsKey(10, 20)],
    trsKeys(5, 0),
    trsKeys(0, 7),
  ]);
  const mdla = concat([cstr("MDLA0001"), u32(0), u32(3), base, additive, click]);
  return concat(meshSection, mdls, mdat, mdla);
}

function animatedPuppetPackage(general: Record<string, unknown> = {}): Uint8Array {
  const rgba = new Uint8Array(4 * 4 * 4).fill(255);
  const png = new Uint8Array(encodePng(4, 4, rgba));
  return buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json" }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 1920, height: 1080 }, clearcolor: "0 0 0", ...general },
      objects: [
        {
          id: 1,
          name: "puppet",
          image: "models/puppet.json",
          origin: "100 200 0",
          size: "200 200 0",
          animationlayers: [
            { additive: false, animation: 10, blend: 1.0, rate: { value: 1 }, visible: { value: true, scriptproperties: { percentage: 0.5 } } },
            { additive: true, animation: 11, blend: 0.5, rate: 1.5, visible: true },
            { additive: true, animation: 11, visible: { value: false } },
            { additive: false, animation: 12, visible: true },
          ],
        },
        { id: 2, name: "hair", parent: 1, attachment: "头", image: "models/hair.json", origin: "1 2 0", size: "20 20 0" },
      ],
    }),
    jsonEntry("models/puppet.json", { autosize: true, material: "materials/body.json", puppet: "models/puppet_puppet.mdl" }),
    jsonEntry("models/hair.json", { autosize: true, material: "materials/hair.json" }),
    jsonEntry("materials/body.json", { passes: [{ shader: "genericimage4", textures: ["body"] }] }),
    jsonEntry("materials/puppet-mat.json", { passes: [{ shader: "genericimage4", textures: ["mesh"] }] }),
    jsonEntry("materials/hair.json", { passes: [{ shader: "genericimage2", textures: ["hair"] }] }),
    { path: "models/puppet_puppet.mdl", bytes: animatedPuppetMdl() },
    { path: "materials/body.tex", bytes: png },
    { path: "materials/mesh.tex", bytes: png },
    { path: "materials/hair.tex", bytes: png },
  ]);
}

const b64F32 = (value: string) => {
  const raw = Uint8Array.from(atob(value), (ch) => ch.charCodeAt(0));
  return new Float32Array(raw.buffer);
};

describe("puppet 网格与骨骼附件", () => {
  it("MDLS 骨骼世界矩阵 × MDAT 挂点矩阵落在模型空间", () => {
    const rig = parseMdlRig(puppetMdl());
    expect(rig.boneCount).toBe(1);
    expect(rig.attachments).toHaveLength(1);
    const attachment = rig.attachments[0] as { name: string; boneIndex: number; socket: Float32Array };
    expect([attachment.name, attachment.boneIndex]).toEqual(["头", 0]);
    expect([attachment.socket[12], attachment.socket[13]]).toEqual([15, 26]);
  });

  it("puppet 对象按 MDLV 网格出层（不再铺整张图集），纹理取网格材质", () => {
    const bundle = readScenePackage(puppetFixturePackage());
    const layers = bundle.manifest.layers as Array<Record<string, unknown>>;
    const puppet = layers.find((layer) => layer.name === "puppet") as Record<string, any>;
    expect(puppet, "puppet 对象没有生成网格图层").toBeTruthy();
    expect(puppet.texUrl).toBe(`${SCENE_RESOURCE_PREFIX}materials/mesh.tex`);
    expect(puppet.mesh.iCount).toBe(3);
    expect(puppet.meshScale).toEqual([1, 1]);
    // 组 origin/scale 用 {value} 存放：不认对象形式会让整组偏回画布中心。
    // 对象原点就是落点，网格顶点按原始 MDLV 模型空间直接叠加（与 WE 一致，
    // autosize 不改变原点语义；用户看到的偏移来自 WE 自定义属性 x9/y9）。
    expect([puppet.x, puppet.y]).toEqual([600, 500]);
    const positions = b64F32(puppet.mesh.posB64);
    expect(Array.from(positions.slice(0, 6))).toEqual([0, 0, 0, 100, 0, 0]);
    expect(Array.from(b64F32(puppet.mesh.uvB64).slice(0, 4))).toEqual([0, 1, 1, 1]);
    expect(bundle.resources["materials/mesh.tex"]).toBeTruthy();
  });

  it("attachment 子对象按骨骼挂点落位，而不是父对象原点", () => {
    const layers = readScenePackage(puppetFixturePackage()).manifest.layers as Array<Record<string, any>>;
    const hair = layers.find((layer) => layer.name === "hair") as Record<string, any>;
    expect(hair).toBeTruthy();
    // 骨骼世界平移 (10,20) + 挂点平移 (5,6) + 对象 origin (1,2)，再加宿主原点
    // (100,200) 与组原点 (500,300)。
    expect([hair.x, hair.y]).toEqual([616, 528]);
  });

  it("隐藏祖先的整棵子树都不渲染（昼夜双模式不再叠两份）", () => {
    const layers = readScenePackage(puppetFixturePackage()).manifest.layers as Array<Record<string, any>>;
    expect(layers.some((layer) => layer.name === "hidden-child")).toBe(false);
    const bg = layers.find((layer) => layer.name === "bg") as Record<string, any>;
    // 根对象 origin 同样是 {value: "..."}
    expect([bg.x, bg.y]).toEqual([100, 200]);
  });
});

describe("用户预设（WE project.json properties）", () => {
  const solidPng = () => {
    const rgba = new Uint8Array(4 * 4 * 4).fill(255);
    return new Uint8Array(encodePng(4, 4, rgba));
  };
  const presetPackage = (
    objects: Array<Record<string, unknown>>,
    properties: Record<string, unknown>,
    general: Record<string, unknown> = {},
  ) => buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json", general: { properties } }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 1920, height: 1080 }, ...general },
      objects,
    }),
    jsonEntry("img.json", { material: "materials/art.json", width: 100, height: 100 }),
    jsonEntry("materials/art.json", {
      passes: [{ shader: "genericimage2", textures: ["art"], usershadervalues: { tintcolor: "tint" } }],
    }),
    { path: "materials/art.tex", bytes: solidPng() },
  ]);
  type Layer = Record<string, any>;

  it("条件组合框按当前值切换昼夜子树，baked value 不得压过条件", () => {
    const bytes = presetPackage([
      { id: 1, name: "day", image: "img.json", origin: "100 100 0", visible: { user: { name: "display", condition: "0" }, value: true } },
      { id: 2, name: "night", image: "img.json", origin: "200 200 0", visible: { user: { name: "display", condition: "1" }, value: true } },
      { id: 3, name: "night-group", visible: { user: { name: "display", condition: "1" } } },
      { id: 4, name: "night-child", parent: 3, image: "img.json", origin: "300 300 0" },
    ], {
      display: { type: "combo", value: "0", options: [{ label: "白天", value: "0" }, { label: "夜晚", value: "1" }] },
    });
    const day = readScenePackage(bytes);
    expect((day.manifest.layers as Layer[]).map((layer) => layer.name)).toEqual(["day"]);
    expect(day.manifest.userBindings).toEqual(["display"]);

    const night = readScenePackage(bytes, { display: "1" });
    expect((night.manifest.layers as Layer[]).map((layer) => layer.name)).toEqual(["night", "night-child"]);
  });

  it("画布百分比脚本按 canvasSize 重算，用户值优先于 baked 快照", () => {
    const origin = {
      script: "value.x = scriptProperties.x * engine.canvasSize.x;\nvalue.y = scriptProperties.y * engine.canvasSize.y;",
      scriptproperties: { x: { user: "x9", value: 0.5 }, y: { user: "y9", value: 0.5 } },
      value: "1919.69226 1045.94714 0.00000",
    };
    const bytes = presetPackage([
      { id: 1, name: "char", image: "img.json", origin, size: "100 100 0" },
    ], {
      x9: { type: "slider", value: 0.5, min: 0, max: 1 },
      y9: { type: "slider", value: 0.5, min: 0, max: 1 },
    });
    const plain = readScenePackage(bytes).manifest.layers as Layer[];
    expect([plain[0].x, plain[0].y]).toEqual([960, 540]);

    const moved = readScenePackage(bytes, { x9: 0.25, y9: 0.75 }).manifest;
    const layer = moved.layers[0] as Layer;
    expect([layer.x, layer.y]).toEqual([480, 810]);
    expect(moved.userBindings).toEqual(["x9", "y9"]);
  });

  it("标量滑杆绑定到向量字段时按分量广播；颜色与透明度同样吃用户值", () => {
    const bytes = presetPackage([
      {
        id: 1,
        name: "char",
        image: "img.json",
        origin: "960 540 0",
        size: "100 100 0",
        scale: { user: "newproperty2", value: "1 1 1" },
        alpha: { user: "newproperty52", value: 1 },
      },
    ], {
      newproperty2: { type: "slider", value: 1, min: 0, max: 2 },
      newproperty52: { type: "slider", value: 1, min: 0, max: 1 },
      tintcolor: { type: "color", value: "1 1 1" },
    });
    const plain = readScenePackage(bytes).manifest;
    const plainLayer = plain.layers[0] as Layer;
    expect([plainLayer.w, plainLayer.h, plainLayer.alpha]).toEqual([100, 100, 1]);
    expect(plain.userBindings).toEqual(["newproperty2", "newproperty52", "tintcolor"]);

    const custom = readScenePackage(bytes, {
      newproperty2: 0.5,
      newproperty52: 0.3,
      tintcolor: "0 0.5 1",
    }).manifest;
    const layer = custom.layers[0] as Layer;
    expect([layer.w, layer.h]).toEqual([50, 50]);
    expect(layer.alpha).toBeCloseTo(0.3);
    expect(layer.userColors.tint).toEqual([0, 0.5, 1]);
  });

  it("动画速率/可见/进度都接受绑定；隐藏态也把开关报给 UI", () => {
    const png = solidPng();
    const bytes = buildPkg([
      jsonEntry("project.json", {
        type: "scene",
        file: "scene.json",
        general: {
          properties: {
            newproperty4: { type: "slider", value: 1, min: 0, max: 4 },
            newproperty5: { type: "slider", value: 0.5, min: 0, max: 1 },
            hrbigbbreath: { type: "bool", value: true },
          },
        },
      }),
      jsonEntry("scene.json", {
        general: { orthogonalprojection: { width: 1920, height: 1080 } },
        objects: [{
          id: 1,
          name: "puppet",
          image: "models/puppet.json",
          origin: "100 200 0",
          size: "200 200 0",
          animationlayers: [{
            additive: false,
            animation: 10,
            rate: { user: "newproperty4", value: 1 },
            visible: {
              user: "hrbigbbreath",
              value: true,
              scriptproperties: { percentage: { user: "newproperty5", value: 0.5 } },
            },
          }],
        }],
      }),
      jsonEntry("models/puppet.json", { autosize: true, material: "materials/body.json", puppet: "models/puppet_puppet.mdl" }),
      jsonEntry("materials/body.json", { passes: [{ shader: "genericimage4", textures: ["body"] }] }),
      jsonEntry("materials/puppet-mat.json", { passes: [{ shader: "genericimage4", textures: ["mesh"] }] }),
      { path: "models/puppet_puppet.mdl", bytes: animatedPuppetMdl() },
      { path: "materials/body.tex", bytes: png },
      { path: "materials/mesh.tex", bytes: png },
    ]);
    const plain = readScenePackage(bytes).manifest as Record<string, any>;
    const plainLayer = (plain.layers as Layer[]).find((layer) => layer.name === "puppet")!;
    expect(plainLayer.animation).toEqual([{ id: 10, additive: false, offset: 0.5, rate: 1, blend: 1 }]);
    expect(plain.userBindings).toEqual(["hrbigbbreath", "newproperty4", "newproperty5"]);

    const custom = readScenePackage(bytes, {
      newproperty4: 2, newproperty5: 0.25, hrbigbbreath: false,
    }).manifest as Record<string, any>;
    const layer = (custom.layers as Layer[]).find((entry) => entry.name === "puppet")!;
    expect(layer.animation).toBeUndefined();
    expect(custom.userBindings).toEqual(["hrbigbbreath", "newproperty4", "newproperty5"]);

    const sped = readScenePackage(bytes, { newproperty4: 2, newproperty5: 0.25 }).manifest as Record<string, any>;
    expect((sped.layers as Layer[]).find((entry) => entry.name === "puppet")!.animation)
      .toEqual([{ id: 10, additive: false, offset: 0.25, rate: 2, blend: 1 }]);
  });

  it("隐藏对象的可见性开关进面板；能过可见性但缺纹理被跳过的对象不进", () => {
    const png = solidPng();
    const bytes = buildPkg([
      jsonEntry("project.json", {
        type: "scene",
        file: "scene.json",
        general: {
          properties: {
            toggle: { type: "bool", value: false },
            ghost: { type: "bool", value: true },
          },
        },
      }),
      jsonEntry("scene.json", {
        general: { orthogonalprojection: { width: 1920, height: 1080 } },
        objects: [
          { id: 1, name: "hidden-char", image: "img.json", origin: "100 100 0", visible: { user: "toggle", value: false } },
          { id: 2, name: "ghost", image: "ghost.json", origin: "200 200 0", visible: { user: "ghost", value: true } },
          { id: 3, name: "bg", image: "img.json", origin: "960 540 0", size: "100 100 0" },
        ],
      }),
      jsonEntry("img.json", { material: "materials/art.json", width: 100, height: 100 }),
      jsonEntry("ghost.json", { width: 100, height: 100 }),
      jsonEntry("materials/art.json", { passes: [{ shader: "genericimage2", textures: ["art"] }] }),
      { path: "materials/art.tex", bytes: png },
    ]);
    const manifest = readScenePackage(bytes).manifest;
    expect((manifest.layers as Layer[]).map((layer) => layer.name)).toEqual(["bg"]);
    expect(manifest.userBindings).toEqual(["toggle"]);
  });

  it("未被渲染的对象/视差关闭时的绑定不得出现在 userBindings", () => {
    const bytes = presetPackage([
      { id: 1, name: "text", alpha: { user: "newproperty52", value: 1 } }, // 没有 image ⇒ 不出层
      { id: 2, name: "bg", image: "img.json", origin: "960 540 0", size: "100 100 0", parallaxDepth: { user: "newproperty10", value: "0.03 0.03" } },
    ], {
      newproperty52: { type: "slider", value: 1, min: 0, max: 1 },
      newproperty10: { type: "slider", value: 0.03, min: 0, max: 1 },
    });
    const on = readScenePackage(bytes, { newproperty10: 0.05, newproperty52: 0.2 }).manifest;
    expect(on.userBindings).toEqual(["newproperty10"]);
    expect((on.layers[0] as Layer).depth).toEqual([0.05, 0.05]);

    const noParallax = readScenePackage(
      presetPackage([
        { id: 2, name: "bg", image: "img.json", origin: "960 540 0", size: "100 100 0", parallaxDepth: { user: "newproperty10", value: "0.03 0.03" } },
      ], {
        newproperty10: { type: "slider", value: 0.03, min: 0, max: 1 },
      }, { cameraparallax: false }),
      { newproperty10: 0.05 },
    ).manifest;
    expect(noParallax.cameraParallax).toBe(false);
    expect(noParallax.userBindings).toBeUndefined();
  });
});

describe("puppet 动画、蒙皮与相机视差", () => {
  it("cameraparallax=false 必须写进清单，缺省才开启鼠标视差", () => {
    const off = readScenePackage(animatedPuppetPackage({ cameraparallax: false })).manifest;
    expect(off.cameraParallax).toBe(false);
    const on = readScenePackage(animatedPuppetPackage()).manifest;
    expect(on.cameraParallax).toBe(true);
  });

  it("只导出 loop clip，且只保留真正动 / 覆盖 bind 的骨骼轨迹", () => {
    const manifest = readScenePackage(animatedPuppetPackage()).manifest as Record<string, any>;
    const payload = manifest.puppets["models/puppet_puppet.mdl"];
    expect(payload.boneCount).toBe(3);
    // 单击动作（single）不得自动播放，所以整个 clip 都不进清单。
    expect(Object.keys(payload.clips).sort()).toEqual(["10", "11"]);
    const bonesOf = (id: string) => payload.clips[id].tracks.map((track: { bone: number }) => track.bone);
    expect(bonesOf("10")).toEqual([0, 2]); // 骨骼 1 静态且等于 bind → 丢
    expect(bonesOf("11")).toEqual([0, 2]); // 骨骼 2 覆盖 bind 但加性 delta 为零 → 保留
    const inverseBind = b64F32(payload.inverseBindB64);
    expect(inverseBind[12]).toBeCloseTo(-10);
    expect(inverseBind[13]).toBeCloseTo(-20);
  });

  it("mesh 层带蒙皮数组；动画层按 visible / clip 类型过滤", () => {
    const layers = readScenePackage(animatedPuppetPackage()).manifest.layers as Array<Record<string, any>>;
    const puppet = layers.find((layer) => layer.name === "puppet") as Record<string, any>;
    expect(puppet.puppetId).toBe("models/puppet_puppet.mdl");
    expect(puppet.mesh.skinIndicesB64).toBeTruthy();
    expect(Array.from(b64F32(puppet.mesh.skinWeightsB64).slice(0, 4))).toEqual([1, 0, 0, 0]);
    expect(puppet.animation).toEqual([
      { id: 10, additive: false, offset: 0.5, rate: 1, blend: 1 },
      { id: 11, additive: true, offset: 0, rate: 1.5, blend: 0.5 },
    ]);
  });

  it("附件子层携带绕 bind socket 旋转的跟随数据", () => {
    const layers = readScenePackage(animatedPuppetPackage()).manifest.layers as Array<Record<string, any>>;
    const hair = layers.find((layer) => layer.name === "hair") as Record<string, any>;
    // 宿主原点 (100,200) + socket (17,23) + 附件自身 origin (1,2)
    expect([hair.x, hair.y]).toEqual([118, 225]);
    expect(hair.follow).toMatchObject({
      puppetId: "models/puppet_puppet.mdl",
      bone: 2,
      bindSocket: [17, 23],
      screenOrigin: [100, 200],
    });
    // 宿主 scale=1 / angle=0 ⇒ 单位映射（-0 与 0 等价，不锁符号）
    expect(hair.follow.screenMap[0]).toBe(1);
    expect(hair.follow.screenMap[1]).toBe(0);
    expect(hair.follow.screenMap[2]).toBeCloseTo(0);
    expect(hair.follow.screenMap[3]).toBe(1);
    expect(hair.follow.bindAngle).toBeCloseTo(0);
    expect(hair.follow.animation).toEqual([
      { id: 10, additive: false, offset: 0.5, rate: 1, blend: 1 },
      { id: 11, additive: true, offset: 0, rate: 1.5, blend: 0.5 },
    ]);
  });
});

describe("文字图层（WE TextObject）", () => {
  const textPkg = (
    objects: Array<Record<string, unknown>>,
    properties: Record<string, unknown> = {},
    extraEntries: Array<{ path: string; bytes: Uint8Array }> = [],
  ) => buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json", general: { properties } }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 3840, height: 2160 } },
      objects,
    }),
    ...extraEntries,
  ]);
  const fontEntry = { path: "fonts/2.ttf", bytes: encoder.encode("FONT") };
  type Layer = Record<string, any>;
  const layersOf = (bytes: Uint8Array, props?: Record<string, string | number | boolean>) =>
    (readScenePackage(bytes, props).manifest.layers as Layer[]).filter((l) => l.kind === "text");

  const clockScript = [
    "export var scriptProperties = createScriptProperties()",
    "  .addCheckbox({ name: 'use24hFormat', value: true })",
    "  .addCheckbox({ name: 'showSeconds', value: false })",
    "  .addText({ name: 'delimiter', value: ':' })",
    "  .finish();",
    "export function update(value) {",
    "  let time = new Date();",
    "  var hours = time.getHours();",
    "  if (!scriptProperties.use24hFormat) { hours %= 12; }",
    "  value = hours + scriptProperties.delimiter + (\"00\" + time.getMinutes()).slice(-2);",
    "  if (scriptProperties.showSeconds) { value += scriptProperties.delimiter + (\"00\" + time.getSeconds()).slice(-2); }",
    "  return value;",
    "}",
  ].join("\n");

  // WE 脚本里的 '\nSat' 是两个字符（反斜杠 + n），不是真换行；拼接避免源码转义歧义。
  const esc = String.fromCharCode(92);
  const nl = "'" + esc + "n'";
  // 脚本族 A（真机 3346715292）：分隔符取属性、newLine 按横/竖排选 '\n' 或 ''，
  // 月名表带空格，showDay=false 只拼日号 + 月 + 年。
  const dateScriptA = [
    "export var scriptProperties = createScriptProperties()",
    "  .addCombo({ name: 'monthFormat', options: [{value:'1'},{value:'2'}] })",
    "  .addCombo({ name: 'dayFormat', options: [{value:'1'},{value:'2'}] })",
    "  .addCheckbox({ name: 'showDay', value: false })",
    "  .addCheckbox({ name: 'alignVertical', value: false })",
    "  .addCheckbox({ name: 'useDelimiter', value: false })",
    "  .addText({ name: 'addDelimiter', value: '/' })",
    "  .finish();",
    "var delimiterValue;",
    "var newLine;",
    "export function update(value){",
    "  let date = new Date();",
    "  if (scriptProperties.useDelimiter == true){ delimiterValue = scriptProperties.addDelimiter }",
    "  if (scriptProperties.useDelimiter == false){ delimiterValue = [' '] }",
    "  if (scriptProperties.alignVertical == true){ newLine = [" + nl + "] }",
    "  if (scriptProperties.alignVertical == false){ newLine = [''] }",
    "  if (scriptProperties.monthFormat == 1){ months = ['1','2','3','4','5','6','7','8','9','10','11','12'] }",
    "  if (scriptProperties.monthFormat == 2){ months = [' JAN ',' FEB ',' MAR ',' APR ',' MAY ',' JUN ',' JUL ',' AUG ',' SEP ',' OCT ',' NOV ',' DEC '] }",
    "  if (scriptProperties.dayFormat == 1){ day = ['Sun','Mon','Tue','Wed','Thu','Fri','" + esc + "nSat'] }",
    "  if (scriptProperties.dayFormat == 2){ day = ['S U N D A Y','M O N D A Y','T U E S D A Y','W E D N E S D A Y','T H U R S D A Y','F R I D A Y','S A T U R D A Y'] }",
    "  if (scriptProperties.showDay == true ){ return day[date.getDay()] }",
    "  if (scriptProperties.showDay == false ){ return date.getDate() + delimiterValue + months[date.getMonth()] + delimiterValue + date.getFullYear(); }",
    "}",
  ].join("\n");

  // 脚本族 B（真机 3723257973 的 Date）：竖排时 delimiterValue = ['\n\n']、newLine = ['\n']，
  // 月名/日号按字符拼接成竖排表、年份逐字拆开，showDay=false 时省略星期。
  const dttTable = ["''"].concat(
    Array.from({ length: 31 }, (_, i) => {
      const day = String(i + 1).padStart(2, "0");
      return "'" + day[0] + "' + newLine + '" + day[1] + "'";
    }),
  ).join(",");
  const dateScriptB = [
    "export var scriptProperties = createScriptProperties()",
    "  .addCombo({ name: 'monthFormat', options: [{value:'1'},{value:'2'}] })",
    "  .addCombo({ name: 'dayFormat', options: [{value:'1'}] })",
    "  .addCheckbox({ name: 'showDay', value: false })",
    "  .addCheckbox({ name: 'alignVertical', value: true })",
    "  .addCheckbox({ name: 'useDelimiter', value: false })",
    "  .addText({ name: 'addDelimiter', value: '/' })",
    "  .finish();",
    "var delimiterValue;",
    "var newLine;",
    "export function update(value){",
    "  if (scriptProperties.useDelimiter == true){ delimiterValue = scriptProperties.addDelimiter }",
    "  if (scriptProperties.useDelimiter == false && scriptProperties.alignVertical == true){ delimiterValue = ['" + esc + "n" + esc + "n'] }",
    "  if (scriptProperties.useDelimiter == false && scriptProperties.alignVertical == false){ delimiterValue = [' '] }",
    "  if (scriptProperties.alignVertical == true){ newLine = [" + nl + "] }",
    "  if (scriptProperties.alignVertical == false){ newLine = [''] }",
    "  if (scriptProperties.monthFormat == 1){ months = ['0'+newLine+'1','0'+newLine+'2'] }",
    "  if (scriptProperties.monthFormat == 2){ months = ['J'+newLine+'A'+newLine+'N','F'+newLine+'E'+newLine+'B','M'+newLine+'A'+newLine+'R','A'+newLine+'P'+newLine+'R','M'+newLine+'A'+newLine+'Y','J'+newLine+'U'+newLine+'N','J'+newLine+'U'+newLine+'L','A'+newLine+'U'+newLine+'G','S'+newLine+'E'+newLine+'P','O'+newLine+'C'+newLine+'T','N'+newLine+'O'+newLine+'V','D'+newLine+'E'+newLine+'C'] }",
    "  if (scriptProperties.dayFormat == 1){ day = ['S'+newLine+'u'+newLine+'n','M'+newLine+'o'+newLine+'n','T'+newLine+'u'+newLine+'e','W'+newLine+'e'+newLine+'d','T'+newLine+'h'+newLine+'u','F'+newLine+'r'+newLine+'i','S'+newLine+'a'+newLine+'t'] }",
    "  if (scriptProperties.alignVertical == true || scriptProperties.alignVertical == false){ dtt = [" + dttTable + "] }",
    "  let date = new Date(); {",
    "    var yearold = date.getFullYear() + ''",
    "    if (scriptProperties.alignVertical == true || scriptProperties.alignVertical == false){",
    "      var yearnew = yearold.split('');",
    "      year = yearnew[0] + newLine + yearnew[1] + newLine + yearnew[2] + newLine + yearnew[3];",
    "    }",
    "    if (scriptProperties.showDay == true){",
    "      return day[date.getDay()] + delimiterValue + dtt[date.getDate()] + delimiterValue + months[date.getMonth()] + delimiterValue + year;",
    "    }",
    "    if (scriptProperties.showDay == false){",
    "      return dtt[date.getDate()] + delimiterValue + months[date.getMonth()] + delimiterValue + year;",
    "    }",
    "  }",
    "}",
  ].join("\n");

  // 脚本族 C（真机 3723257973 的 D a y）：showDay=true 只回星期表（竖排 SUN）。
  const dayScriptC = [
    "export var scriptProperties = createScriptProperties()",
    "  .addCombo({ name: 'monthFormat', options: [{value:'1'}] })",
    "  .addCombo({ name: 'dayFormat', options: [{value:'1'}] })",
    "  .addCheckbox({ name: 'showDay', value: true })",
    "  .addCheckbox({ name: 'alignVertical', value: true })",
    "  .addCheckbox({ name: 'useDelimiter', value: true })",
    "  .addText({ name: 'addDelimiter', value: '/' })",
    "  .finish();",
    "var delimiterValue;",
    "var nl;",
    "export function update(value){",
    "  if (scriptProperties.useDelimiter == true){ delimiterValue = scriptProperties.addDelimiter }",
    "  if (scriptProperties.useDelimiter == false){ delimiterValue = [' '] }",
    "  if (scriptProperties.alignVertical == true){ nl = [" + nl + "] }",
    "  if (scriptProperties.alignVertical == false){ nl = [' '] }",
    "  if (scriptProperties.monthFormat == 1){ months = ['1','2','3','4','5','6','7','8','9','10','11','12'] }",
    "  if (scriptProperties.dayFormat == 1){ day = ['S'+nl+'U'+nl+'N','M'+nl+'O'+nl+'N','T'+nl+'U'+nl+'E','W'+nl+'E'+nl+'D','T'+nl+'H'+nl+'U','F'+nl+'R'+nl+'I','S'+nl+'A'+nl+'T'] }",
    "  let date = new Date(); {",
    "    if (scriptProperties.showDay == true){",
    "      return day[date.getDay()]",
    "    }",
    "    if (scriptProperties.showDay == false){",
    "      return date.getDate() + delimiterValue + months[date.getMonth()] + delimiterValue + date.getFullYear();",
    "    }",
    "  }",
    "}",
  ].join("\n");

  it("静态文字出图层；工程自带字体走包内资源，包外字体交给内置资产通道", () => {
    const bytes = textPkg([
      { id: 1, name: "day7", font: "fonts/2.ttf", pointsize: 34.8, origin: "100 200 0", color: "0.1 0.2 0.3", text: { user: "newproperty55", value: "夜莺" } },
      { id: 2, name: "clock", font: "fonts/Blackout 2 AM.ttf", pointsize: 100, origin: "300 400 0", text: "12:34" },
    ], { newproperty55: { type: "textinput", value: "夜莺" } }, [fontEntry]);
    const [own, builtIn] = layersOf(bytes);
    expect(own).toMatchObject({ kind: "text", x: 100, y: 200, text: "夜莺", fontSize: 34.8, color: [0.1, 0.2, 0.3] });
    expect(own.font).toBe(SCENE_RESOURCE_PREFIX + "fonts/2.ttf");
    expect(builtIn.font).toBe(SCENE_RESOURCE_PREFIX + "we-asset/fonts/Blackout 2 AM.ttf");
    // textinput 的内容键不是可渲染事实，只登记真正影响画面的键。
    expect(readScenePackage(bytes).manifest.userBindings).toEqual(["newproperty55"]);
    // 内置字体不来自包体：解码只回传包里真有的那份，we-asset 交给主线程补取。
    const bundle = readScenePackage(bytes);
    expect(bundle.paths).toContain("we-asset/fonts/Blackout 2 AM.ttf");
    expect(Object.keys(bundle.resources)).toEqual(["fonts/2.ttf"]);
  });

  it("字号吃自身与中间父组 scale，最外层组件根组只挪位置（真机 3723257973）", () => {
    const bytes = textPkg([
      { id: 1, name: "组件", scale: "0.28 0.28 1", origin: "100 200 0" },
      { id: 2, name: "组", parent: 1, scale: "2 2 1", origin: "10 20 0" },
      { id: 3, name: "t", parent: 2, font: "fonts/2.ttf", pointsize: 10, scale: "0.5 0.5 1", origin: "10 20 0", text: "A" },
      { id: 4, name: "m", parent: 2, font: "fonts/2.ttf", pointsize: 10, scale: "-1 -1 1", origin: "0 0 0", text: "B" },
    ], {}, [fontEntry]);
    const [t, mirrored] = layersOf(bytes);
    // 真机参考图三处锚点：问候语 32×0.23025×3.125≈23 与帧率 24×0.948≈22.8 字高相当；
    // 竖排 SUN 与日期字高比≈3.2 ≈ 96.653×0.85 / (140×0.18)。根组 0.28 只缩放位置。
    expect(t.fontSize).toBeCloseTo(10);
    expect(mirrored.fontSize).toBeCloseTo(20);
    // 位置仍折叠整条父链：100 + 10×0.28 + 10×0.56 = 108.4，200 + 20×0.28 + 20×0.56 = 216.8。
    expect(t.x).toBeCloseTo(108.4);
    expect(t.y).toBeCloseTo(216.8);
  });

  it("官方 Clock 脚本抠出档位；Date 脚本族 A 取属性分隔符与月名表", () => {
    const bytes = textPkg([
      {
        id: 1, name: "Clock", font: "fonts/2.ttf", pointsize: 40, origin: "0 0 0",
        text: {
          script: clockScript,
          scriptproperties: {
            delimiter: ":",
            showSeconds: { user: "newproperty8", value: false },
            use24hFormat: { user: "_24", value: true },
          },
          value: "12:34",
        },
      },
      {
        id: 2, name: "Date", font: "fonts/2.ttf", pointsize: 40, origin: "0 0 0",
        text: {
          script: dateScriptA,
          scriptproperties: { monthFormat: "2", dayFormat: "2", showDay: false, alignVertical: false, useDelimiter: true, addDelimiter: "" },
          value: "<Date>",
        },
      },
      {
        id: 3, name: "D a y", font: "fonts/2.ttf", pointsize: 40, origin: "0 0 0",
        text: {
          script: dateScriptA,
          scriptproperties: { monthFormat: "2", dayFormat: "2", showDay: true, alignVertical: false, useDelimiter: true, addDelimiter: "" },
          value: "<Day>",
        },
      },
    ], {}, [fontEntry]);
    const [clock, date, day] = layersOf(bytes);
    expect(clock).toMatchObject({ time: "clock", hour24: true, seconds: false, delimiter: ":" });
    expect(clock.text).toBeUndefined();
    expect(date).toMatchObject({
      time: "date",
      format: "{day}{month}{year}",
      months: [" JAN ", " FEB ", " MAR ", " APR ", " MAY ", " JUN ", " JUL ", " AUG ", " SEP ", " OCT ", " NOV ", " DEC "],
    });
    // 只显示星期的那一支：默认档位的 dayFormat=2 表是空格分隔的整词，不再混进日期。
    expect(day).toMatchObject({
      time: "date",
      format: "{weekday}",
      days: ["S U N D A Y", "M O N D A Y", "T U E S D A Y", "W E D N E S D A Y", "T H U R S D A Y", "F R I D A Y", "S A T U R D A Y"],
    });
    // 脚本里烘焙的档位值（use24hFormat/showSeconds）带用户覆盖键时，键必须进面板。
    expect(readScenePackage(bytes).manifest.userBindings).toEqual(["_24", "newproperty8"]);
  });

  it("Date 脚本族 B：竖排分隔符/拼接表/日号表/年份拆分全部按档位求值", () => {
    const vertical = textPkg([
      {
        id: 1, name: "Date", font: "fonts/2.ttf", pointsize: 140, origin: "0 0 0",
        text: {
          script: dateScriptB,
          scriptproperties: { monthFormat: "2", dayFormat: "1", showDay: false, alignVertical: true, useDelimiter: false, addDelimiter: "/" },
          value: "<Date>",
        },
      },
    ], {}, [fontEntry]);
    const [date] = layersOf(vertical);
    expect(date.format).toBe("{dayTable}\n\n{month}\n\n{year}");
    expect(date.months[9]).toBe("O\nC\nT");
    expect(date.days).toBeUndefined();
    expect(date.dayTable[25]).toBe("2\n5");
    expect(date.dayTable[1]).toBe("0\n1");
    expect(date.yearSplit).toBe("\n");

    // 同一脚本换横排档位：分隔符变空格、表项不再换行、年份仍逐字但拼接符为空。
    const horizontal = textPkg([
      {
        id: 1, name: "Date", font: "fonts/2.ttf", pointsize: 140, origin: "0 0 0",
        text: {
          script: dateScriptB,
          scriptproperties: { monthFormat: "2", dayFormat: "1", showDay: true, alignVertical: false, useDelimiter: false, addDelimiter: "/" },
          value: "<Date>",
        },
      },
    ], {}, [fontEntry]);
    const [flat] = layersOf(horizontal);
    expect(flat.format).toBe("{weekday} {dayTable} {month} {year}");
    expect(flat.months[9]).toBe("OCT");
    expect(flat.days[0]).toBe("Sun");
    expect(flat.dayTable[25]).toBe("25");
    expect(flat.yearSplit).toBe("");
  });

  it("Date 脚本族 C：showDay=true 只回星期表；false 才拼日期", () => {
    const dayOn = textPkg([
      {
        id: 1, name: "D a y", font: "fonts/2.ttf", pointsize: 96.653, origin: "0 0 0",
        text: {
          script: dayScriptC,
          scriptproperties: { monthFormat: "1", dayFormat: "1", showDay: true, alignVertical: true, useDelimiter: true, addDelimiter: "/" },
          value: "<Day>",
        },
      },
    ], {}, [fontEntry]);
    const [day] = layersOf(dayOn);
    expect(day).toMatchObject({ time: "date", format: "{weekday}" });
    expect(day.days[0]).toBe("S\nU\nN");
    expect(day.months).toBeUndefined();
    expect(day.dayTable).toBeUndefined();

    const dayOff = textPkg([
      {
        id: 1, name: "D a y", font: "fonts/2.ttf", pointsize: 96.653, origin: "0 0 0",
        text: {
          script: dayScriptC,
          scriptproperties: { monthFormat: "1", dayFormat: "1", showDay: false, alignVertical: true, useDelimiter: true, addDelimiter: "/" },
          value: "<Day>",
        },
      },
    ], {}, [fontEntry]);
    const [flat] = layersOf(dayOff);
    expect(flat.format).toBe("{day}/{month}/{year}");
    expect(flat.months[9]).toBe("10");
    expect(flat.days).toBeUndefined();
  });

  it("日期脚本里认不出的 return 项整条跳过，不把半懂的表发出去", () => {
    const bytes = textPkg([
      { id: 1, name: "ok", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", text: "正常" },
      {
        id: 2, name: "season", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0",
        text: {
          script: [
            "export var scriptProperties = createScriptProperties()",
            "  .addCheckbox({ name: 'showDay', value: true })",
            "  .finish();",
            "var day;",
            "export function update(value){",
            "  let date = new Date();",
            "  if (scriptProperties.showDay == true){ return season[date.getMonth()] }",
            "  if (scriptProperties.showDay == false){ return date.getDate() + '/' + date.getMonth() + '/' + date.getFullYear(); }",
            "}",
          ].join("\n"),
          scriptproperties: { showDay: true },
          value: "<season>",
        },
      },
    ], {}, [fontEntry]);
    // 同一个包里另一层照常出；半懂的日期脚本连层带绑定一起缺席。
    expect(layersOf(bytes).map((l) => l.name)).toEqual(["ok"]);
  });

  it("认不出的脚本整条跳过且不登记任何键；隐藏文字只交出可见性键", () => {
    const bytes = textPkg([
      { id: 1, name: "mystery", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", alpha: { user: "deadbeef", value: 1 }, text: { script: "export function update(v){ return weather(); }", scriptproperties: {}, value: "x" } },
      { id: 2, name: "night7", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", alpha: { user: "newproperty52", value: 1 }, visible: { user: { name: "display", condition: "1" }, value: true }, text: { user: "newproperty55", value: "夜里" } },
      { id: 3, name: "day7", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", visible: { user: { name: "display", condition: "0" }, value: true }, text: "白天" },
    ], { display: { type: "combo", value: "0" } }, [fontEntry]);
    const bundle = readScenePackage(bytes);
    expect((bundle.manifest.layers as Layer[]).map((l) => l.name)).toEqual(["day7"]);
    // 没有渲染的 mystery 不贡献 deadbeef；隐藏的 night7 只交出让它缺席的那个开关。
    expect(bundle.manifest.userBindings).toEqual(["display"]);

    const night = readScenePackage(bytes, { display: "1" });
    expect((night.manifest.layers as Layer[]).map((l) => l.name)).toEqual(["night7"]);
    expect(night.manifest.userBindings).toEqual(["display", "newproperty52", "newproperty55"]);
  });

  // 真机 3723257973 的「早中晚英文」：八段文案写在对象表里，小时阈值是一串三元式，
  // switch 把标签名映射到表项。清单只抠表，不执行 JS。
  const greetingScript = (table = "shichenY") => [
    "let shichenC = {1:'凌晨',2:'夜间',3:'早晨',4:'上午',5:'中午',6:'下午',7:'傍晚',8:'晚上'};",
    "let shichenY = {1:'Before dawn',2:'At night',3:'Morning',4:'Morning',5:'Noon',6:'Afternoon',7:'Evening',8:'Night'};",
    "let kong",
    "export function update(value) {",
    "  let hour = new Date().getHours();",
    "  let timeTag = hour< 2 ?'lingchen':hour< 6 ?'yejian':hour< 8 ?'zaochen':hour< 11 ?'shangwu':hour < 13 ?'zhongwu':hour< 17 ?'xiawu':hour< 20 ?'bangwan':'wanshang';",
    "  switch (timeTag) {",
    "    case 'lingchen' : kong = " + table + "[1] ;break;",
    "    case 'yejian' : kong = " + table + "[2] ;break;",
    "    case 'zaochen' : kong = " + table + "[3] ;break;",
    "    case 'shangwu' : kong = " + table + "[4] ;break;",
    "    case 'zhongwu' : kong = " + table + "[5] ;break;",
    "    case 'xiawu' : kong = " + table + "[6] ;break;",
    "    case 'bangwan' : kong = " + table + "[7] ;break;",
    "    case 'wanshang' : kong = " + table + "[8] ;break;",
    "  }",
    "  value = kong",
    "  return value",
    "}",
  ].join("\n");

  // 真机 3723257973 的「帧率显示」：相邻帧间隔求瞬时帧率，按 refreshInterval 求平均后拼 label。
  const fpsScript = [
    "export var scriptProperties = createScriptProperties()",
    "  .addCheckbox({ name: 'active', value: true })",
    "  .addSlider({ name: 'refreshInterval', value: 500, min: 0, max: 1000 })",
    "  .addText({ name: 'label', value: 'fps:' })",
    "  .finish();",
    "var currentFrame, oldFrame = 0, fps, totFps = 0, oldTime = 0, n = 0;",
    "export function update(value) {",
    "  if (scriptProperties.active){",
    "    let time = new Date();",
    "    if (oldFrame == 0){ oldFrame = time.getTime(); oldTime = oldFrame; }",
    "    currentFrame = time.getTime();",
    "    if (currentFrame > oldFrame){",
    "      fps = 1000 / (currentFrame - oldFrame);",
    "      oldFrame = currentFrame;",
    "      totFps += fps;",
    "      n++;",
    "      if (currentFrame - oldTime >= parseInt(scriptProperties.refreshInterval)){",
    "        totFps /= n;",
    "        value = scriptProperties.label + ' ' + Math.round(totFps);",
    "        oldTime = currentFrame;",
    "        n = 0;",
    "        totFps = 0;",
    "      }",
    "    }",
    "    return value;",
    "  }",
    "}",
  ].join("\n");

  it("问候语脚本：小时阈值与标签表静态求值，隐藏的中文变体只交出可见性键", () => {
    const bytes = textPkg([
      { id: 10, name: "组件", scale: "0.28 0.28 1", origin: "100 200 0" },
      { id: 11, name: "", parent: 10, scale: "3.125 3.125 1", origin: "10 20 0" },
      {
        id: 1, name: "早中晚英文", parent: 11, font: "fonts/2.ttf", pointsize: 32, scale: "0.23025 0.23025 1",
        origin: "0 0 0", visible: { user: "newproperty59", value: true },
        text: { script: greetingScript(), value: "Before dawn" },
      },
      {
        id: 2, name: "早中晚", parent: 11, font: "fonts/2.ttf", pointsize: 32, scale: "0.23025 0.23025 1",
        origin: "0 0 0", visible: { user: "newproperty54", value: false },
        text: { script: greetingScript("shichenC"), value: "凌晨" },
      },
    ], {}, [fontEntry]);
    const bundle = readScenePackage(bytes);
    const layers = (bundle.manifest.layers as Layer[]).filter((l) => l.kind === "text");
    expect(layers).toHaveLength(1);
    expect(layers[0]).toMatchObject({
      name: "早中晚英文",
      time: "greeting",
      labels: ["Before dawn", "At night", "Morning", "Morning", "Noon", "Afternoon", "Evening", "Night"],
      thresholds: [2, 6, 8, 11, 13, 17, 20],
      // 字号吃中间父组的 3.125（参考图里与帧率同高），不吃最外层组件的 0.28。
      fontSize: 32 * 0.23025 * 3.125,
    });
    // 隐藏的中文变体只贡献「它为什么看不见」那个键；标签/脚本属性都不是可渲染事实。
    expect(bundle.manifest.userBindings).toEqual(["newproperty54", "newproperty59"]);
  });

  it("帧率脚本：label/refreshInterval/烘焙初值进清单；active=false 整条缺席", () => {
    const on = textPkg([
      {
        id: 1, name: "帧率显示", font: "fonts/2.ttf", pointsize: 24, origin: "0 0 0",
        visible: { user: "newproperty46", value: true },
        text: { script: fpsScript, scriptproperties: { active: true, label: "fps:", refreshInterval: 250 }, value: "fps：60" },
      },
    ], {}, [fontEntry]);
    const [fps] = layersOf(on);
    expect(fps).toMatchObject({ time: "fps", label: "fps:", refreshMs: 250, text: "fps：60" });
    expect(readScenePackage(on).manifest.userBindings).toEqual(["newproperty46"]);

    const off = textPkg([
      { id: 1, name: "ok", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", text: "正常" },
      {
        id: 2, name: "帧率显示", font: "fonts/2.ttf", pointsize: 24, origin: "0 0 0",
        text: { script: fpsScript, scriptproperties: { active: false, label: "fps:", refreshInterval: 250 }, value: "fps：60" },
      },
    ], {}, [fontEntry]);
    // active=false 时 WE 的 update 返回 undefined，文本不刷新；不发图层也不登记开关。
    expect(layersOf(off).map((l) => l.name)).toEqual(["ok"]);
  });

  it("问候语/帧率脚本缺一环就整条跳过，不发半懂的表", () => {
    const brokenGreeting = greetingScript().replace("    case 'wanshang' : kong = shichenY[8] ;break;\n", "");
    const brokenFps = fpsScript.replace("scriptProperties.label", "scriptProperties.caption");
    const bytes = textPkg([
      { id: 1, name: "ok", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", text: "正常" },
      { id: 2, name: "greet", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", text: { script: brokenGreeting, value: "x" } },
      { id: 3, name: "fps", font: "fonts/2.ttf", pointsize: 10, origin: "0 0 0", text: { script: brokenFps, scriptproperties: { active: true, label: "fps:", refreshInterval: 500 }, value: "fps：60" } },
    ], {}, [fontEntry]);
    expect(layersOf(bytes).map((l) => l.name)).toEqual(["ok"]);
  });
});

describe("WE 粒子系统（2D 通道）", () => {
  // 逐字段抄真机 3346715292 的三份预设：鸟（包内序列帧）、官方雾（包外内置贴图）、官方尾迹（rope）。
  const birdPreset = {
    maxcount: 30,
    sequencemultiplier: 30,
    material: "birds.material.json",
    emitter: [{ name: "sphererandom", rate: 100, distancemin: 0, distancemax: 64 }],
    initializer: [
      { name: "lifetimerandom", min: 25, max: 25 },
      { name: "sizerandom", min: 50, max: 60 },
      { name: "colorrandom", min: "255 255 255" },
      { name: "velocityrandom", min: "20 100 0", max: "50 5 0" },
    ],
    operator: [
      { name: "movement", gravity: "30 0 0", drag: 0.1 },
      { name: "controlpointattract", controlpoint: 1, scale: 128, threshold: 2048 },
      { name: "controlpointattract", controlpoint: 2, scale: 256, threshold: 1024 },
    ],
    renderer: [{ name: "sprite" }],
  };
  const fogPreset = {
    maxcount: 20,
    material: "fog.material.json",
    emitter: [{ name: "sphererandom", rate: 1.5, distancemin: 0, distancemax: 512, directions: "1 0.2 0" }],
    initializer: [
      { name: "lifetimerandom", min: 3, max: 5 },
      { name: "sizerandom", min: 1000, max: 2200 },
      { name: "velocityrandom", min: "25 0 0", max: "80 0 0" },
      { name: "colorrandom", min: "255 255 255" },
      { name: "alpharandom", min: 0.15, max: 0.2 },
    ],
    operator: [{ name: "movement" }, { name: "alphafade" }],
    renderer: [{ name: "sprite" }],
  };
  const ropePreset = {
    maxcount: 128,
    material: "trail.material.json",
    emitter: [{ name: "sphererandom", rate: 32, distancemin: 0, distancemax: 0 }],
    initializer: [{ name: "lifetimerandom", min: 1, max: 1 }],
    operator: [{ name: "movement", drag: 0 }, { name: "alphafade", fadeintime: 0.1, fadeouttime: 0.1 }],
    renderer: [{ name: "rope", length: 3 }],
  };
  const particlePkg = (
    objects: Array<Record<string, unknown>>,
    properties: Record<string, unknown> = {},
  ) => buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json", general: { properties } }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 1920, height: 1080 } },
      objects: [
        { name: "bg", image: "bg.image.json", origin: "960 540 0", size: "1920 1080 0" },
        ...objects,
      ],
    }),
    jsonEntry("bg.image.json", { material: "bg.material.json", width: 1920, height: 1080 }),
    jsonEntry("bg.material.json", { passes: [{ shader: "genericimage2", textures: ["bg"] }] }),
    jsonEntry("birds.json", birdPreset),
    jsonEntry("birds.material.json", {
      passes: [{ blending: "translucent", shader: "genericparticle", textures: ["bird/birds_128x120x16"] }],
    }),
    jsonEntry("fog.json", fogPreset),
    jsonEntry("fog.material.json", {
      passes: [{ blending: "additive", shader: "genericparticle", textures: ["particle/fog/fog2"] }],
    }),
    jsonEntry("trail.json", ropePreset),
    jsonEntry("trail.material.json", {
      passes: [{ blending: "additive", shader: "genericparticle", textures: ["particle/meteor"] }],
    }),
    { path: "materials/bg.tex", bytes: new Uint8Array(encodePng(4, 4, new Uint8Array(4 * 4 * 4))) },
    { path: "bird/birds_128x120x16.tex", bytes: new Uint8Array(encodePng(4, 4, new Uint8Array(4 * 4 * 4))) },
  ]);
  type System = Record<string, any>;
  const systemsOf = (bytes: Uint8Array, props?: Record<string, string | number | boolean>) =>
    ((readScenePackage(bytes, props).manifest as { particles3d?: System[] }).particles3d ?? []);

  it("贴图精灵出齐 2D 语义：混合模式、序列帧、吸引点、镜像、逐实例微调都取自真机字段", () => {
    const [sys] = systemsOf(particlePkg([
      {
        id: 2, name: "day2", particle: "birds.json", origin: "1654 -204 0", scale: "-1 1 1",
        instanceoverride: { id: 5124, size: 1.77, controlpoint1: "5075 116 0", controlpoint2: "2408 253 0" },
      },
    ]));
    expect(sys).toMatchObject({
      name: "day2",
      blend: "translucent",
      // scale.x = −1 是作者做的镜像：鸟群贴图朝左，翻过来才与 +X 的飞行方向一致。
      flipX: true,
      flipY: false,
      sizeScale: [1, 1],
      maxCount: 30,
      rate: 100,
      lifeMin: 25,
      lifeMax: 25,
      sizeMin: 50 * 1.77,
      sizeMax: 60 * 1.77,
      velMin: [20, 100, 0],
      velMax: [50, 5, 0],
      gravity: [30, 0],
      drag: 0.1,
      frames: { w: 128, h: 120, count: 16 },
      attractions: [
        { x: 5075, y: 116, scale: 128, threshold: 2048 },
        { x: 2408, y: 253, scale: 256, threshold: 1024 },
      ],
    });
    expect(sys.texUrl).toBe(SCENE_RESOURCE_PREFIX + "bird/birds_128x120x16.tex");
    // 速度/重力按预设原值（场景像素/秒），不随对象 scale 放大：雾的 10 倍缩放会把缓漂变成洪流。
    expect(sys.velMax[0]).toBe(50);
  });

  it("instanceoverride.count：<1 当比例、≥1 当绝对上限；没写 count 绝不塌成 1 只", () => {
    const bytes = particlePkg([
      { id: 2, name: "a", particle: "birds.json", instanceoverride: { count: 0.8 } },
      { id: 3, name: "b", particle: "birds.json", instanceoverride: { count: 5 } },
      { id: 4, name: "c", particle: "birds.json", instanceoverride: { count: 999 } },
      { id: 5, name: "d", particle: "birds.json" },
    ]);
    expect(systemsOf(bytes).map((s) => s.maxCount)).toEqual([24, 5, 30, 30]);
  });

  it("包外内置贴图登记 builtInAssetKeys，取不到时主线程据此撤掉这一路的键", () => {
    const bundle = readScenePackage(particlePkg([
      {
        id: 6, name: "雾 1", particle: "fog.json", origin: "1526 172 0", scale: "-10.07223 3.37065 1",
        visible: { user: "hrbigbfog", value: true },
        instanceoverride: { alpha: 0.86, colorn: "0.7451 0.78431 1" },
      },
    ], { hrbigbfog: { type: "checkbox", value: true } }));
    const [sys] = (bundle.manifest as { particles3d: System[] }).particles3d;
    expect(sys.texUrl).toBe(SCENE_RESOURCE_PREFIX + "we-asset/materials/particle/fog/fog2.tex");
    expect(sys.blend).toBe("additive");
    expect(sys.sizeScale).toEqual([10.07223, 3.37065]);
    expect(sys.flipX).toBe(true);
    expect(sys.alphaMin).toBeCloseTo(0.15 * 0.86);
    expect(sys.colorMin).toEqual([0.7451, 0.78431, 1]);
    expect(bundle.manifest.builtInAssetKeys).toEqual({
      "we-asset/materials/particle/fog/fog2.tex": ["hrbigbfog"],
    });
    // 解码只回传包里真有的纹理；内置贴图交给主线程的 WE 资产通道。
    expect(bundle.paths).toContain("we-asset/materials/particle/fog/fog2.tex");
    expect(Object.keys(bundle.resources)).not.toContain("we-asset/materials/particle/fog/fog2.tex");
  });

  it("隐藏的精灵系统不发射，但把「此刻为什么看不见」那把键交出去", () => {
    const bytes = particlePkg([
      { id: 6, name: "雾 1", particle: "fog.json", visible: { user: "hrbigbfog", value: false } },
    ]);
    expect(systemsOf(bytes)).toEqual([]);
    expect(readScenePackage(bytes).manifest.userBindings).toEqual(["hrbigbfog"]);
    expect(systemsOf(bytes, { hrbigbfog: true }).map((s) => s.name)).toEqual(["雾 1"]);
  });

  it("没实现的渲染器（rope / ropetrail）整条不出现，连可见性键都不交", () => {
    const bytes = particlePkg([
      {
        id: 7, name: "Trails 2", particle: "trail.json", origin: "1920 1080 0",
        visible: { user: "shu", value: false },
        instanceoverride: {
          colorn: { user: "newproperty16", value: "0 1 0.53725" },
          size: { user: "newproperty17", value: 1 },
        },
      },
    ], { shu: { type: "checkbox", value: false }, newproperty16: { type: "colorpicker", value: "0 1 0.53725" } });
    expect(systemsOf(bytes)).toEqual([]);
    expect(systemsOf(bytes, { shu: true })).toEqual([]);
    // 红线：渲染不消费它，面板就不摆它——开关打开也不交键。
    expect(readScenePackage(bytes).manifest.userBindings ?? []).toEqual([]);
    expect(readScenePackage(bytes, { shu: true }).manifest.userBindings ?? []).toEqual([]);
  });
});

describe("对象效果：云层流动（effects/waterflow）", () => {
  // 逐字抄真机 3346715292 的 day1 挂载：可见性绑 newproperty9、速度绑 newproperty10，
  // 参数写在 scene.json 的 effects 条目里（effect.json 只是模板，没有这些）。
  const properties = {
    flowon: { type: "checkbox", value: true },
    flowspeed: { type: "slider", value: 0.03, min: 0.01, max: 2 },
  };
  const waterflow = (over: Record<string, unknown> = {}) => ({
    file: "effects/waterflow/effect.json",
    visible: { user: "flowon", value: true },
    passes: [{
      constantshadervalues: { phasescale: 2, speed: { user: "flowspeed", value: 0.03 }, strength: 5 },
      // 槽位即 WE 的 g_Texture0/1/2：0=对象自身的图，1=流场遮罩，2=相位图。
      textures: [null, "masks/flow_mask", "util/white"],
    }],
    ...over,
  });
  const white = () => new Uint8Array(4 * 4 * 4).fill(255);
  const flowPkg = (
    effect: Record<string, unknown> = {},
    objExtra: Record<string, unknown> = {},
    withMask = true,
  ) => buildPkg([
    jsonEntry("project.json", { type: "scene", file: "scene.json", general: { properties } }),
    jsonEntry("scene.json", {
      general: { orthogonalprojection: { width: 1920, height: 1080 } },
      objects: [
        {
          name: "sky", image: "sky.image.json", origin: "960 540 0", size: "1920 1080 0",
          effects: [waterflow(effect)], ...objExtra,
        },
      ],
    }),
    jsonEntry("sky.image.json", { material: "sky.material.json", width: 1920, height: 1080 }),
    jsonEntry("sky.material.json", { passes: [{ shader: "genericimage4", textures: ["sky"] }] }),
    { path: "materials/sky.tex", bytes: new Uint8Array(encodePng(4, 4, white())) },
    ...(withMask ? [{ path: "materials/masks/flow_mask.tex", bytes: new Uint8Array(encodePng(4, 4, white())) }] : []),
  ]);
  const skyLayer = (bytes: Uint8Array, values?: SceneUserProperties) =>
    ((readScenePackage(bytes, values).manifest as { layers: Record<string, any>[] }).layers[0]);

  it("遮罩取到才发 flow，开关与速度两把键随描述符一起进面板", () => {
    const bytes = flowPkg();
    expect(skyLayer(bytes).flow).toEqual({
      maskUrl: SCENE_RESOURCE_PREFIX + "materials/masks/flow_mask.tex",
      // 本包相位槽是 WE 内置 util/white，磁盘上没有文件 ⇒ 空串，播放器用 1×1 白兜底。
      phaseUrl: "",
      speed: 0.03,
      strength: 5,
      phasescale: 2,
      enabled: true,
    });
    expect(readScenePackage(bytes).manifest.userBindings).toEqual(["flowon", "flowspeed"]);
  });

  it("遮罩不在包里：图层照常出，但不发 flow，也不提交任何键", () => {
    const bytes = flowPkg({}, {}, false);
    expect(skyLayer(bytes).flow).toBeUndefined();
    expect(readScenePackage(bytes).manifest.userBindings ?? []).toEqual([]);
  });

  it("用户关掉流动：enabled=false，两把键留在面板——它们正是这一层此刻不动的原因", () => {
    const bytes = flowPkg();
    expect(skyLayer(bytes, { flowon: false }).flow).toMatchObject({ enabled: false });
    expect(readScenePackage(bytes, { flowon: false }).manifest.userBindings).toEqual(["flowon", "flowspeed"]);
  });

  it("速度绑用户值：滑杆改的是采样位移的节奏", () => {
    expect(skyLayer(flowPkg(), { flowspeed: 0.5 }).flow).toMatchObject({ speed: 0.5 });
  });

  it("作者写死关掉的效果（visible 是字面量 false）：既不画也不交键", () => {
    const bytes = flowPkg({ visible: false });
    expect(skyLayer(bytes).flow).toBeUndefined();
    expect(readScenePackage(bytes).manifest.userBindings ?? []).toEqual([]);
  });

  it("相位图真在包里：phaseUrl 跟着发，播放器不必退化成全场同相", () => {
    const bytes = buildPkg([
      jsonEntry("project.json", { type: "scene", file: "scene.json", general: { properties } }),
      jsonEntry("scene.json", {
        general: { orthogonalprojection: { width: 1920, height: 1080 } },
        objects: [
          {
            name: "sky", image: "sky.image.json", origin: "960 540 0", size: "1920 1080 0",
            effects: [waterflow()],
          },
        ],
      }),
      jsonEntry("sky.image.json", { material: "sky.material.json", width: 1920, height: 1080 }),
      jsonEntry("sky.material.json", { passes: [{ shader: "genericimage4", textures: ["sky"] }] }),
      { path: "materials/sky.tex", bytes: new Uint8Array(encodePng(4, 4, new Uint8Array(4 * 4 * 4).fill(255))) },
      { path: "materials/masks/flow_mask.tex", bytes: new Uint8Array(encodePng(4, 4, new Uint8Array(4 * 4 * 4).fill(255))) },
      { path: "materials/util/white.tex", bytes: new Uint8Array(encodePng(4, 4, new Uint8Array(4 * 4 * 4).fill(255))) },
    ]);
    expect(skyLayer(bytes).flow).toMatchObject({
      phaseUrl: SCENE_RESOURCE_PREFIX + "materials/util/white.tex",
    });
  });

  it("半透明图层也接管：专用流场程序带 u_alpha，透明度不再是闸门", () => {
    // 早先复用上游 progFlow（无 alpha uniform）时这里必须判死，否则半透明层的透明度会被丢掉。
    // fsWaterflow 与 fsBasic 逐个对齐了 u_tint/u_bright/u_power/u_alpha，这一条前提已经消失。
    const bytes = flowPkg({}, { alpha: "0.5" });
    expect(skyLayer(bytes).alpha).toBeCloseTo(0.5);
    expect(skyLayer(bytes).flow).toMatchObject({ enabled: true, strength: 5 });
    expect(readScenePackage(bytes).manifest.userBindings).toEqual(["flowon", "flowspeed"]);
  });
});
