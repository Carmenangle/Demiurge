import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验证（创意工坊 2587542891 · EVA 绫波丽 4K 雨景）：
 * WE 里这一面是满屏动态雨丝 + 右下角一个数字时钟；应用此前两处缺失：
 *   ① 12 份 `spritetrail` 雨系统被渲染器白名单丢掉（只剩 7 份 sprite 雨雾，看着像雪景）；
 *   ② 时钟被旧 Clock 惯用法吸收（只画 `HH:MM`、日期段丢失），且字号漏了 1080p 基准系数（10 场景单位）。
 * 本用例锁四件事：
 *   ① 清单：19 份粒子系统全在，其中 12 份 `spritetrail` 带 trailLength 0.005 / trailMax 100|6，
 *      贴图走 we-asset 的 `particle/drop.tex`；
 *   ② 时钟层：`time:'format'`、format 取 scene.json 保存的 scriptproperties 快照
 *      `yyyyMMdhh:mm`（WE 画的就是这份）、字号 10×(2571/1080)×(4/3)×1.25≈39.68、
 *      框宽 390 / 内边距 32；
 *   ③ 光栅：真画出 13 字 `YYYYMMDDHH:MM`（保存格式无斜杠/空格），字号经 2× 超采样进画布（39.68 → 79px），
 *      字体是 WE 安装目录的 CursedTimerUlil-Aznm；
 *   ④ 模拟：`__weParticles` 有 19 套系统、总活粒子 > 100、quads 在涨——雨丝真的在发射。
 *
 * 依赖隔离后端 8011（带 we-asset 路由）+ 临时 vite 5198，命令同 wallpaper-scene-text-layout：
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test --workers=1 \
 *     --config playwright.e2e-manual.config.ts -g "雨丝"
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-rain-eva");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "2587542891";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\2587542891\preview.jpg`;

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

type Glyph = { text: string; font: string; canvas: string };

type ParticleSystem = {
  name?: string;
  renderer?: string;
  trailLength?: number;
  trailMax?: number;
  texUrl?: string;
  maxCount?: number;
  rate?: number;
  velMin?: number[];
};

type TextLayer = {
  name?: string;
  kind?: string;
  x?: number;
  y?: number;
  fontSize?: number;
  boxWidth?: number;
  padding?: number;
  font?: string;
  time?: string;
  format?: string;
  language?: string;
  week?: string;
  twelve?: boolean;
};

type SceneRec = {
  draws: Glyph[];
  uploads: string[];
  manifests: { layers?: TextLayer[]; particles3d?: ParticleSystem[]; userBindings?: string[] }[];
};

type ParticlesProbe = { systems: number; live: Record<string, number>; quads: number; steps: number };

type SceneWindow = Window & {
  __e2eScene?: SceneRec;
  __weParticles?: ParticlesProbe;
  CanvasRenderingContext2D?: typeof CanvasRenderingContext2D;
  WebGLRenderingContext?: typeof WebGLRenderingContext;
};

const settingsWith = (wallpaper: Record<string, unknown>) => ({
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [wallpaper],
    activeWallpaperId: wallpaper.id,
    wallpaperFit: "cover",
    wallpaperPaused: false,
    pauseOnHidden: false,
    pauseOnBlur: false,
    pauseOnBattery: false,
    scrim: 0,
    wallpaperBlur: 0,
    wallpaperBrightness: 100,
    wallpaperContrast: 100,
    wallpaperSaturate: 100,
  },
});

async function mockSettings(page: Page, wallpaper: Record<string, unknown>) {
  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { repos: null, settings: settingsWith(wallpaper) } });
      return;
    }
    await route.fulfill({ json: { ok: true } });
  });
  await page.route("**/api/user-state/proxy-status*", (route) => route.fulfill({
    json: {
      listening: false, address: "", source: "none", system_address: "",
      manual_address: "", candidates: [],
    },
  }));
}

/**
 * 每个文档（含播放器 iframe）建文档前装监听：fillText 记栅格化、清单两条通道都截。
 *
 * 清单进播放器有两条路：首帧是播放器自己 `fetch` 宿主给的 blob（srcdoc 里的 MANIFEST_URL）；
 * 只有预设热重建才由宿主 post `dsh-set-manifest`。EVA 这首帧就是 WE 生效值、没有热重建，
 * 只截消息会一条都拿不到。
 */
async function installSceneHook(page: Page) {
  await page.addInitScript(() => {
    const win = window as unknown as SceneWindow;
    const rec: SceneRec = { draws: [], uploads: [], manifests: [] };
    win.__e2eScene = rec;
    window.addEventListener("message", (event) => {
      const data = event.data as { type?: unknown; manifest?: SceneRec["manifests"][number] } | null;
      if (!data || data.type !== "dsh-set-manifest" || !data.manifest) return;
      rec.manifests.push(data.manifest);
    });
    const nativeFetch = window.fetch.bind(window);
    window.fetch = function patchedFetch(input: RequestInfo | URL, init?: RequestInit) {
      const result = nativeFetch(input, init);
      const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      // 清单是唯一的 JSON blob；贴图/字体 blob 走 img.src / FontFace，不经过 fetch。
      if (href.startsWith("blob:")) {
        result
          .then((response) => (response.headers.get("content-type") ?? "").includes("json")
            ? response.clone().json()
            : null)
          .then((data: { manifest?: SceneRec["manifests"][number] } | null) => {
            if (data?.manifest) rec.manifests.push(data.manifest);
          })
          .catch(() => {});
      }
      return result;
    };
    const c2d = win.CanvasRenderingContext2D?.prototype;
    if (!c2d) return;
    const fill = c2d.fillText;
    c2d.fillText = function patchedFillText(
      this: CanvasRenderingContext2D,
      text: string,
      x: number,
      y: number,
    ) {
      rec.draws.push({ text, font: this.font, canvas: `${this.canvas.width}x${this.canvas.height}` });
      return fill.call(this, text, x, y);
    };
    const gl = win.WebGLRenderingContext?.prototype;
    if (!gl) return;
    const upload = gl.texImage2D as unknown as (this: WebGLRenderingContext, ...a: unknown[]) => void;
    gl.texImage2D = function patchedTexImage2D(this: WebGLRenderingContext, ...args: unknown[]) {
      const source = args[5] as { tagName?: string; width?: number; height?: number } | undefined;
      if (source?.tagName === "CANVAS") rec.uploads.push(`${source.width}x${source.height}`);
      return upload.apply(this, args as never);
    } as typeof gl.texImage2D;
  });
}

const readScene = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const rec = (frame?.contentWindow as SceneWindow | null)?.__e2eScene;
  if (!rec) throw new Error("监听器未装到播放器 iframe 上");
  return { draws: rec.draws.slice(), uploads: rec.uploads.slice(), manifests: rec.manifests.slice() };
});

const readParticles = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const win = frame?.contentWindow as SceneWindow | null;
  return win?.__weParticles ?? null;
});

/** `63px we-scene-font-3` → { size: 63, family: "we-scene-font-3" }。 */
function fontOf(css: string) {
  const match = /^(\d+(?:\.\d+)?)px\s+(.+)$/.exec(css.trim());
  return match ? { size: Number(match[1]), family: match[2] } : { size: 0, family: css };
}

test.use({ viewport: { width: 1600, height: 900 } });

test("雨丝与格式串时钟：12 份 spritetrail 真发射，时钟画出 13 字日期+分钟", async ({ page }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  // 清单里的资源在进播放器前已全量换成 blob（字体也是），所以「字节来自 WE 安装目录」
  // 只能靠这条路由的响应来证。
  const assetResponses: { url: string; status: number; bytes: number }[] = [];
  page.on("response", async (response) => {
    if (!response.url().includes("/wallpapers/we-asset")) return;
    let bytes = -1;
    try {
      bytes = (await response.body()).length;
    } catch {
      bytes = -1;
    }
    assetResponses.push({ url: response.url(), status: response.status(), bytes });
  });

  await installSceneHook(page);
  await mockSettings(page, {
    id: "scene-rain-eva",
    path: SCENE_PREVIEW,
    name: "雨景验证（EVA）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  });

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 180_000 });
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-preset", "applied", { timeout: 180_000 });

  // ① 清单：等 19 套粒子系统 + 文本层到齐（重建后再来一份，取最后一份）。
  let manifest: SceneRec["manifests"][number] | undefined;
  await expect.poll(async () => {
    const rec = await readScene(page);
    manifest = rec.manifests[rec.manifests.length - 1];
    return (manifest?.particles3d ?? []).length;
  }, { timeout: 60_000, message: "清单里始终没有 19 套粒子系统" }).toBe(19);

  const systems = manifest!.particles3d ?? [];
  const trails = systems.filter((sys) => sys.renderer === "spritetrail");
  const sprites = systems.filter((sys) => sys.renderer === "sprite");
  expect(trails.length, "spritetrail 雨系统数量不对").toBe(12);
  expect(sprites.length, "sprite 雨雾系统数量不对").toBe(7);

  // 主雨：rainperspective 三份，3000 px/s × 0.005 = 15 倍尺寸拉丝、maxlength 100 封顶。
  const heavy = trails.filter((sys) => sys.name === "Rain perspective");
  expect(heavy.length, "主雨系统数量不对").toBe(3);
  for (const sys of heavy) {
    expect(sys.trailLength, "主雨 trailLength 不是 WE 原厂 0.005").toBeCloseTo(0.005, 6);
    expect(sys.trailMax, "主雨 maxlength 不是 100").toBe(100);
    expect(sys.velMin?.[1], "主雨没有向下的 3000 px/s 速度").toBe(-3000);
    expect(sys.texUrl, "雨丝贴图没有换成 blob").toMatch(/^blob:/);
  }
  // 玻璃雨：只写了 maxlength 6，length 缺省仍取 0.005。
  const glass = trails.filter((sys) => sys.name !== "Rain perspective");
  expect(glass.length).toBe(9);
  for (const sys of glass) {
    expect(sys.trailLength, "缺省 length 不是 0.005").toBeCloseTo(0.005, 6);
    expect(sys.trailMax, "玻璃雨 maxlength 不是 6").toBe(6);
  }

  // 内置资产的真身：雨丝贴图与时钟字体都得从 WE 安装目录（we-asset 路由）取回字节。
  for (const [path, what] of [
    ["materials/particle/drop.tex", "雨丝贴图"],
    ["fonts/CursedTimerUlil-Aznm.ttf", "时钟字体"],
  ] as const) {
    const hits = assetResponses.filter((item) => decodeURIComponent(item.url).includes(path));
    expect(hits.length, `浏览器没有向 we-asset 路由请求${what} ${path}`).toBeGreaterThan(0);
    expect(hits[0].status, `${what} ${path} 取回 ${hits[0].status}`).toBe(200);
    expect(hits[0].bytes, `${what} ${path} 字节长度为 0`).toBeGreaterThan(1000);
  }

  // ② 时钟层：格式串族用 scene.json 保存的 scriptproperties（WE 渲染的就是这份快照），
  //    字号 = 10 × (2571/1080) × (4/3) × 1.25 ≈ 39.68（1080p 点基准 + WE 的 120 DPI 档）。
  const textLayers = (manifest!.layers ?? []).filter((layer) => layer.kind === "text");
  const clock = textLayers.find((layer) => layer.time === "format");
  expect(clock, `没发格式串时钟层：${JSON.stringify(textLayers)}`).toBeTruthy();
  expect(clock!.format, "format 没取 scene.json 保存的档位").toBe("yyyyMMdhh:mm");
  expect(clock!.fontSize, "字号没含 1080p 基准系数").toBeCloseTo(10 * (2571 / 1080) * (4 / 3) * 1.25, 4);
  expect(clock!.font, "时钟字体没有换成 blob").toMatch(/^blob:/);

  // ③ 光栅：13 字 `YYYYMMDDHH:MM`（保存格式没有斜杠/空格），字号经 2× 超采样进画布（39.68 → 79px）。
  const CLOCK_RE = /^\d{10}:\d{2}$/;
  let clockDraw: Glyph | undefined;
  await expect.poll(async () => {
    const rec = await readScene(page);
    clockDraw = rec.draws.find((draw) => CLOCK_RE.test(draw.text));
    return clockDraw ? 1 : 0;
  }, { timeout: 120_000, intervals: [500, 1_000, 2_000], message: "时钟层始终没有光栅成 13 字日期" })
    .toBe(1);
  const emPx = Math.round((clock!.fontSize ?? 0) * 2);
  expect(emPx, "时钟 2× 超采样字号不对").toBe(79);
  expect(fontOf(clockDraw!.font).size, "时钟位图字号不是 39.68×2").toBe(emPx);
  expect(fontOf(clockDraw!.font).family).toMatch(/^we-scene-font-/);
  // 13 字 × 0.625 em × 79 ≈ 642：宽度落在这一带说明没有把 19 字（含 :ss）画全。
  const clockCanvasWidth = Number(clockDraw!.canvas.split("x")[0]);
  expect(clockCanvasWidth, `时钟位图宽 ${clockDraw!.canvas} 不像 13 字`).toBeGreaterThan(560);
  expect(clockCanvasWidth, `时钟位图宽 ${clockDraw!.canvas} 不像 13 字`).toBeLessThan(700);

  // ④ 模拟：19 套系统在跑；雨丝是十几份、每份上限数百 ⇒ 活粒子总数必须远超 sprite 单独时。
  let particle: ParticlesProbe | null = null;
  await expect.poll(async () => {
    particle = await readParticles(page);
    if (!particle) return 0;
    return particle.steps > 0 && particle.quads > 0
      ? Object.values(particle.live).reduce((sum, n) => sum + n, 0)
      : 0;
  }, { timeout: 60_000, intervals: [500, 1_000, 2_000], message: "粒子模拟没有推进" })
    .toBeGreaterThan(100);
  expect(particle!.systems, "播放器侧粒子系统数量不对").toBe(19);
  expect(particle!.live["Rain perspective"] ?? 0, "主雨没有活粒子").toBeGreaterThan(0);

  await page.evaluate((css) => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }, ISOLATE_CSS);
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SHOT_DIR, "scene-rain-eva.png") });
  // 时钟：场景坐标 (2212.78, 769.09)，1600×900 下按 cover 0.35 换算 ≈ (700, 631) 居中。
  await page.screenshot({
    path: resolve(SHOT_DIR, "scene-rain-eva-clock.png"),
    clip: { x: 600, y: 590, width: 220, height: 80 },
  });
  // 上半屏无人物区域：雨丝形态（细长、随速度倾斜）在这里最容易看清。
  await page.screenshot({
    path: resolve(SHOT_DIR, "scene-rain-eva-rain.png"),
    clip: { x: 80, y: 80, width: 640, height: 420 },
  });

  expect(errors, errors.join("\n")).toEqual([]);
});
