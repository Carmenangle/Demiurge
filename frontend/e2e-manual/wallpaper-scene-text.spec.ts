import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验证（创意工坊 3346715292 · 文字图层 Tier C）：
 * 1) 工程里 5 个 TextObject 全部出字：2 条静态文字（包内字体 2.ttf）+ 时钟 + 日期 + 星期；
 * 2) 时钟/日期按**本机真实时间**本地拼接（不执行 WE 脚本），暂停时不推进；
 * 3) WE 安装目录的内置字体经 `/wallpapers/we-asset` 取到字节 → blob → FontFace 真加载成功，
 *    光栅用的是内置字形而不是 sans-serif 兜底。
 *
 * 取字证的不是像素（截图看不出字形来源），而是播放器 iframe 内的三处现场：
 * fillText 的句子与字体串、document.fonts 里内置字体的状态、texImage2D 的画布上传。
 *
 * 监听必须在首帧之前挂上：文字位图按「字体+字号+句子」缓存，句子没变就不再 fillText，
 * ready 之后再装只会抓到「这一秒变了」的那几条，静态/日期/星期永远抓不到。
 *
 * 依赖：后端要带本次新增的 we-asset 路由。8010 是用户正在用的实例、且没有这条路由，
 * 所以跑在隔离后端(8011) + 临时 vite(5198，/api 反代 8011) 上：
 *   MSYS_NO_PATHCONV=1 VITE_API_BASE=/api node frontend/node_modules/vite/bin/vite.js \
 *     --config _tmp/vite.e2e-8011.config.mjs
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test --workers=1 \
 *     --config playwright.e2e-manual.config.ts -g "文字图层|暂停时文字"
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-text");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "3346715292";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3346715292\preview.gif`;
const BUILT_IN_FONT = "fonts/spincycle_3d_ot.otf";

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

type Glyph = { text: string; font: string; canvas: string };

type SceneWindow = Window & {
  __e2eGlyphs?: { draws: Glyph[]; uploads: string[] };
  CanvasRenderingContext2D?: typeof CanvasRenderingContext2D;
  WebGLRenderingContext?: typeof WebGLRenderingContext;
};

const settingsWith = (wallpaper: Record<string, unknown>, paused: boolean) => ({
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [wallpaper],
    activeWallpaperId: wallpaper.id,
    wallpaperFit: "cover",
    wallpaperPaused: paused,
    // 失焦/切标签页/电池供电这三个自动暂停一律关掉：不关的话浏览器窗口一失焦
    // 壁纸就定格，「播放态每秒重栅」这条证据就变成随机的。
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

async function mockSettings(page: Page, wallpaper: Record<string, unknown>, paused = false) {
  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { repos: null, settings: settingsWith(wallpaper, paused) } });
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
 * 每个文档（含播放器那个 blob iframe）建文档之前就装好监听：
 * fillText 记「这一句用什么字体栅成了多大的画布」，texImage2D 记「哪张画布进了 GPU」。
 */
async function installGlyphHook(page: Page) {
  await page.addInitScript(() => {
    const win = window as unknown as SceneWindow;
    const rec = { draws: [] as Glyph[], uploads: [] as string[] };
    win.__e2eGlyphs = rec;
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

/** 读播放器 iframe 里累计的现场；`reset=true` 时清空，用于取「这段时间内新发生的」。 */
const readGlyphs = (page: Page, reset: boolean) => page.evaluate((clear) => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const rec = (frame?.contentWindow as SceneWindow | null)?.__e2eGlyphs;
  if (!rec) throw new Error("监听器未装到播放器 iframe 上");
  const out = { draws: rec.draws.slice(), uploads: rec.uploads.slice() };
  if (clear) {
    rec.draws.length = 0;
    rec.uploads.length = 0;
  }
  return out;
}, reset);

/** iframe 里已登记的 FontFace：family + 加载状态，用来证明内置字体真就位。 */
const readFonts = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const win = frame?.contentWindow as SceneWindow | null;
  const faces: { family: string; status: string }[] = [];
  win?.document.fonts.forEach((face) => {
    faces.push({ family: face.family, status: face.status });
  });
  return faces;
});

const pad2 = (n: number) => String(n).padStart(2, "0");

/** 与播放器同一套拼接规则，在本机时间上独立算一遍期望值（不共用被测代码）。 */
function expectedStrings(now: Date) {
  const months = [" JAN ", " FEB ", " MAR ", " APR ", " MAY ", " JUN ",
    " JUL ", " AUG ", " SEP ", " OCT ", " NOV ", " DEC "];
  const days = ["S U N D A Y", "M O N D A Y", "T U E S D A Y", "W E D N E S D A Y",
    "T H U R S D A Y", "F R I D A Y", "S A T U R D A Y"];
  return {
    // 时钟档位来自用户在 WE 里的覆盖（24 小时 / 显示秒），首轮可能还是工程烘焙的
    // 「12 小时不显秒」，等预设热重建才换格式 ⇒ 只要求时分对得上，两种制式都接受。
    hm24: `${pad2(now.getHours())}:${pad2(now.getMinutes())}`,
    hm12: `${pad2(now.getHours() % 12 || 12)}:${pad2(now.getMinutes())}`,
    date: `${now.getDate()}${months[now.getMonth()]}${now.getFullYear()}`,
    // 这张包的星期脚本只输出星期名（日期另一条图层各管一段），别再拼日/月/年。
    day: days[now.getDay()],
  };
}

const CLOCK_RE = /^\d{2}:\d{2}(:\d{2})?$/;
const clockLines = (draws: Glyph[]) =>
  Array.from(new Set(draws.map((draw) => draw.text))).filter((text) => CLOCK_RE.test(text));

/**
 * 轮询直到 5 条文字都现形（首帧一轮 + 字体就位后的整张重建可能分批）。
 *
 * 只读不清：静态/日期/星期在首帧与字体就位那两轮就栅完了，之后句子不再变化就不再 fillText，
 * 一进循环就清空等于把它们丢掉（实测只剩每秒都在变的时钟）。
 */
async function waitForGlyphs(page: Page, want: ReturnType<typeof expectedStrings>) {
  let draws: Glyph[] = [];
  for (let attempt = 0; attempt < 25; attempt++) {
    await page.waitForTimeout(400);
    draws = (await readGlyphs(page, false)).draws;
    const texts = new Set(draws.map((draw) => draw.text));
    const clocks = Array.from(texts).filter((text) => CLOCK_RE.test(text));
    // 轮询会跨分钟：起点与当前时刻的两种制式（12/24）都算命中。
    const now = expectedStrings(new Date());
    const prefixes = [want.hm24, want.hm12, now.hm24, now.hm12];
    const minuteOk = clocks.some((text) => prefixes.some((hm) => text.startsWith(hm)));
    if (minuteOk && texts.has(want.date) && texts.has(want.day)) break;
  }
  return draws;
}

test.use({ viewport: { width: 1600, height: 900 } });

test("文字图层：5 条都出字，时钟按本机时间走，内置字体真加载", async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

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

  await installGlyphHook(page);
  await mockSettings(page, {
    id: "scene-text",
    path: SCENE_PREVIEW,
    name: "八奈见杏菜（夜莺Night · 文字验证）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  });

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });

  mkdirSync(SHOT_DIR, { recursive: true });
  const wanted = expectedStrings(new Date());
  // 预设热重建会把时钟从工程烘焙的「12 小时不显秒」换成用户存的「24 小时显秒」，
  // 期望值按重建后的本机时间重算，所以取样窗口要贴着当前时刻。
  const draws = await waitForGlyphs(page, wanted);
  const texts = new Set(draws.map((draw) => draw.text));
  const clocks = clockLines(draws);

  // ① 时钟：时分必须与本机一致（12/24 制式都放过，档位本身由预设决定；采样跨分钟时两个分钟都收）。
  const minuteNow = expectedStrings(new Date());
  const prefixes = [wanted.hm24, wanted.hm12, minuteNow.hm24, minuteNow.hm12];
  expect(clocks.length, `没有光栅出时钟文本：${JSON.stringify([...texts])}`).toBeGreaterThan(0);
  for (const line of clocks) {
    expect(
      prefixes.some((hm) => line.startsWith(hm)),
      `时钟与本机时间不符：${line}（期望 ${prefixes.join(" / ")} 开头）`,
    ).toBe(true);
  }

  // ② 日期与星期：整句逐字对（月名表 / 星期名表都从工程脚本里抠出来）。
  expect(texts, `日期文本不对：${JSON.stringify([...texts])}`).toContain(wanted.date);
  expect(texts, `星期文本不对：${JSON.stringify([...texts])}`).toContain(wanted.day);

  // ③ 两条静态文字（作者名 / 自定义文字）走同一条光栅通道。
  const statics = Array.from(texts).filter((text) => text
    && !CLOCK_RE.test(text) && text !== wanted.date && text !== wanted.day);
  expect(statics.length, `静态文字图层没出字：${JSON.stringify([...texts])}`).toBeGreaterThanOrEqual(2);

  // ④ 每秒推进：播放态下再采一段，必须出现两个不同的新句子（档位显不显秒都算推进）。
  //     用 poll 而不是「固定睡 3.2 秒再数一次」：这台机器同时跑多条重解码链路时 rAF 会被拖慢，
  //     固定窗口把「推进得慢」误判成「不推进」（2026-10-24 实跑撞到：窗口里只采到 1 个值）。
  await readGlyphs(page, true);
  await expect
    .poll(
      async () => new Set(clockLines((await readGlyphs(page, false)).draws)).size,
      {
        timeout: 15_000,
        intervals: [500, 1_000, 1_000, 2_000, 2_000],
        message: () => "采样窗口内时钟没有新的文本 ⇒ 未跟真实时间推进",
      },
    )
    .toBeGreaterThan(1);

  // ⑤ 字形位图确实进了 GPU：每个「字体+字号+句子」组合一次 texImage2D(canvas)。
  const glyphs = await readGlyphs(page, false);
  const bitmaps = new Set(glyphs.draws.map((draw) => `${draw.font}|${draw.text}`));
  expect(
    glyphs.uploads.length,
    `字形位图没上传成纹理（${bitmaps.size} 张位图 / 实测 ${glyphs.uploads.length} 次画布上传）`,
  ).toBeGreaterThanOrEqual(bitmaps.size);

  // ⑥ 内置字体：主线程经 we-asset 路由取到字节 → blob → FontFace 加载完成。
  const assets = assetResponses.filter((item) => decodeURIComponent(item.url).includes(BUILT_IN_FONT));
  expect(assets.length, "浏览器没有向 we-asset 路由请求内置字体").toBeGreaterThan(0);
  expect(assets[0].status, `内置字体取回 ${assets[0].status}`).toBe(200);
  expect(assets[0].bytes, "内置字体字节长度为 0，光栅不可能用它").toBeGreaterThan(1000);

  const faces = await readFonts(page);
  const sceneFaces = faces.filter((face) => face.family.startsWith("we-scene-font-"));
  expect(sceneFaces.length, `播放器没登记内置字体：${JSON.stringify(faces)}`).toBeGreaterThan(0);
  expect(
    sceneFaces.some((face) => face.status === "loaded"),
    `内置字体没加载成功：${JSON.stringify(sceneFaces)}`,
  ).toBe(true);
  expect(
    [...new Set(glyphs.draws.map((draw) => draw.font))].some((font) => /we-scene-font-\d+/.test(font)),
    `光栅仍用系统字栈兜底：${JSON.stringify([...new Set(glyphs.draws.map((draw) => draw.font))])}`,
  ).toBe(true);

  await page.evaluate((css) => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }, ISOLATE_CSS);
  await page.screenshot({ path: resolve(SHOT_DIR, "scene-text.png") });

  expect(errors, errors.join("\n")).toEqual([]);
});

/**
 * 暂停＝定格在文字上的表现。
 *
 * 句子没变时 `textBitmapOf` 命中缓存、不再 fillText，所以「暂停期间不出现新句子」正是 wallClock
 * 没推进的可观测签名。单独看它可能是空转，所以同一页先用同一套监听证明「首帧确实栅过时钟」，
 * 再证明之后 6 秒不再出现任何**没栅过**的句子。
 * ⚠ 判据不是「零光栅」：内置字体异步就位会作废位图缓存，定格期补帧会把同一句重栅一遍
 * （像素不变），这属正常；重栅次数另设「每句至多一次」的上限防空转风暴。
 */
test("暂停时文字定格：首帧有时钟，之后 6 秒零重栅", async ({ page }) => {
  test.setTimeout(240_000);
  await installGlyphHook(page);
  await mockSettings(page, {
    id: "scene-text-paused",
    path: SCENE_PREVIEW,
    name: "八奈见杏菜（夜莺Night · 暂停定格）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  }, true);

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });
  // 必须先等 WE 生效值落地：首帧刻意用工程烘焙值（这张包的时钟是 12 小时、不显秒），热重建后才
  // 换成 WE 的 24 小时带秒。不等就开测，晚到的那一次换格式会被记成「出现新句子」⇒ 假红
  // （真机踩过：settle 抓到 10:12 PM，6 秒后热重建栅出 22:12:49，看着像时钟在暂停里走了）。
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-preset", "applied", { timeout: 120_000 });

  // 定格态仍会补帧到内容成型（字体就位 + 预设热重建），先等这段 settle 过完再开始计时。
  const settled: Glyph[] = [];
  for (let attempt = 0; attempt < 20; attempt++) {
    await page.waitForTimeout(500);
    const glyphs = await readGlyphs(page, true);
    settled.push(...glyphs.draws);
    if (attempt >= 5 && glyphs.draws.length === 0) break;
  }
  expect(
    clockLines(settled).length,
    `暂停态的 settle 期没栅过任何时钟文本：${JSON.stringify(settled.map((draw) => draw.text))}`,
  ).toBeGreaterThan(0);

  const quiet = await readGlyphs(page, true);
  await page.waitForTimeout(6_000);
  const after = await readGlyphs(page, false);
  // 判据是「不出现**新句子**」而不是「一次都不重栅」：内置字体异步就位时会作废位图缓存
  // ⇒ 定格期补帧一跑就可能把**同一句**重栅一遍，像素完全相同、不违反定格；
  // 而只要时钟走过一秒句子必然变化 ⇒ 这条照样红。上限取「每个已知句子最多重栅一次」，
  // 防的是另一种坏画面：定格期陷入重栅风暴（每帧都重建位图 = 补帧条件判错）。
  const seen = new Set(settled.map((draw) => draw.text));
  const freshInQuiet = quiet.draws.filter((draw) => !seen.has(draw.text));
  const fresh = after.draws.filter((draw) => !seen.has(draw.text));
  expect(
    freshInQuiet.length,
    `settle 之后仍出现新句子光栅：${JSON.stringify(freshInQuiet.map((d) => d.text))}`,
  ).toBe(0);
  expect(fresh.length, `暂停期间时钟推进了新句子：${JSON.stringify(fresh.map((d) => d.text))}`).toBe(0);
  expect(
    after.draws.length,
    `6 秒静默期里重栅了 ${after.draws.length} 次（已知句子 ${seen.size} 句 ⇒ 上限 ${seen.size} 次）：`
    + `${JSON.stringify(after.draws.map((d) => d.text))}`,
  ).toBeLessThanOrEqual(seen.size);
});
