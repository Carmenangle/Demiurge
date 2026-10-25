import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验证（创意工坊 3723257973 · 日期/星期/时钟/问候语/帧率的摆法）：
 * WE 里这一面的日期是三段竖排（"2 5" / "O C T" / "2 0 2 6"），星期是三个大字母竖着摞，
 * 时钟在右下角，左上角还有 "Morning" 与 "fps: 60" 两块脚本文字。应用之前把日期画成横排
 * （"25/10/2026"）、星期只剩一个小 "S"、字号被父链 scale 连乘放大又缩扁，两块脚本文字整层缺席。
 * 本用例锁五件事：
 *   ① 清单：Date 的 format 模板 + 竖排月名/补零日表/逐字年份；Day 只有 {weekday}；
 *      问候语的 8 档小时阈值表与帧率层的 label/刷新窗口/烘焙初值；
 *      字号 = pointsize × 自身与中间父组 scale、不吃最外层组件根组
 *      （25.2 / 82.155 / 56.71 / 23.025 / 22.752），父链整体 scale 只挪位置；
 *   ② 摆法：Date 在左、Day 在右且更靠上、Clock 在两者下方，问候语与帧率在日期上方；
 *   ③ 光栅：播放器 iframe 的 fillText 真的按「日号两行 + 空行 + 月名三行 + 空行 + 年份四行」
 *      逐行出字，星期三行，时钟一行；问候语命中当前时段的标签，帧率在第一个刷新窗口后变成
 *      `fps: <整数>`；字体串用的是 FontFace 家族而不是系统字栈兜底；
 *   ④ 字体：Quicksand 在 scene.pkg 里（走 scene-resource → blob），spincycle 与 Segment7 在
 *      WE 安装目录（走 /wallpapers/we-asset），三者都要真加载成功。
 *
 * 依赖：后端要带本次的 we-asset 路由。8010 是用户正在用的实例，所以跑在隔离后端(8011)
 * + 临时 vite(5198，/api 反代 8011) 上：
 *   MSYS_NO_PATHCONV=1 VITE_API_BASE=/api node frontend/node_modules/vite/bin/vite.js \
 *     --config _tmp/vite.e2e-8011.config.mjs
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test --workers=1 \
 *     --config playwright.e2e-manual.config.ts -g "竖排"
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-text-3723");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "3723257973";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3723257973\preview.gif`;
/** WE 安装目录的内置字体（星期层 spincycle、帧率层 Segment7）；日期层的 Quicksand 打在 scene.pkg 里。 */
const BUILT_IN_FONTS = ["fonts/spincycle_3d_ot.otf", "fonts/Segment7Standard.otf"];

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

type Glyph = { text: string; font: string; canvas: string };

type TextLayer = {
  name?: string;
  kind?: string;
  x?: number;
  y?: number;
  fontSize?: number;
  font?: string;
  time?: string;
  format?: string;
  months?: string[];
  days?: string[];
  dayTable?: string[];
  yearSplit?: string;
  hour24?: boolean;
  seconds?: boolean;
  labels?: string[];
  thresholds?: number[];
  label?: string;
  refreshMs?: number;
  text?: string;
};

type SceneRec = {
  draws: Glyph[];
  uploads: string[];
  manifests: { layers?: TextLayer[]; userBindings?: string[] }[];
};

type SceneWindow = Window & {
  __e2eScene?: SceneRec;
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
 * 每个文档（含播放器那个 blob iframe）建文档之前就装好监听：
 * fillText 记「这一句用什么字体栅成了多大的画布」，manifest 消息记清单本身。
 * 清单只走 postMessage（scenePlayer → iframe），主线程侧拿不到，只能在这里截。
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

/** 竖排月名的作者表（Quicksand 日期层）；与清单里的 months 独立抄一份做交叉验证。 */
const MONTHS_VERTICAL = [
  "J\nA\nN", "F\nE\nB", "M\nA\nR", "A\nP\nR", "M\nA\nY", "J\nU\nN",
  "J\nU\nL", "A\nU\nG", "S\nE\nP", "O\nC\nT", "N\nO\nV", "D\nE\nC",
];
/** 星期层的大写竖排表（spincycle）。 */
const DAYS_VERTICAL = [
  "S\nU\nN", "M\nO\nN", "T\nU\nE", "W\nE\nD", "T\nH\nU", "F\nR\nI", "S\nA\nT",
];

const pad2 = (n: number) => String(n).padStart(2, "0");

/** 与本包日期脚本同一套拼接规则，在本机时间上独立算一遍期望的逐行文本。 */
function expectedLines(now: Date) {
  const dayDigits = pad2(now.getDate()).split("").join("\n");
  const yearDigits = String(now.getFullYear()).split("").join("\n");
  return {
    // 作者模板 {dayTable}\n\n{month}\n\n{year}：组间空行会 split 成空串，一并钉住。
    date: `${dayDigits}\n\n${MONTHS_VERTICAL[now.getMonth()]}\n\n${yearDigits}`.split("\n"),
    day: DAYS_VERTICAL[now.getDay()].split("\n"),
  };
}

const CLOCK_RE = /^\d{2}:\d{2}(:\d{2})?$/;
const clockPrefixes = (now: Date) => {
  const hm24 = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
  const hm12 = `${pad2(now.getHours() % 12 || 12)}:${pad2(now.getMinutes())}`;
  return [hm24, hm12];
};

/** 与播放器同一条规则：按小时从阈值表推进，取对应标签。 */
function greetingOf(now: Date, labels: string[], thresholds: number[]) {
  let index = 0;
  while (index < thresholds.length && now.getHours() >= thresholds[index]) index += 1;
  return labels[index] ?? "";
}

/** 按「字体 + 画布尺寸」把 fillText 归回各自的位图；一次位图 = 一层的那几句。 */
function bitmapGroups(draws: Glyph[]) {
  const groups = new Map<string, { font: string; canvas: string; lines: string[] }>();
  for (const draw of draws) {
    const key = `${draw.font}|${draw.canvas}`;
    let group = groups.get(key);
    if (!group) {
      group = { font: draw.font, canvas: draw.canvas, lines: [] };
      groups.set(key, group);
    }
    group.lines.push(draw.text);
  }
  return Array.from(groups.values());
}

/** 位图缓存失效后同一句会整张重栅，故按「连续子序列」找，而不是要求整组恰好相等。 */
function containsSequence(haystack: string[], needle: string[]) {
  if (needle.length === 0 || needle.length > haystack.length) return false;
  outer: for (let start = 0; start + needle.length <= haystack.length; start++) {
    for (let i = 0; i < needle.length; i++) {
      if (haystack[start + i] !== needle[i]) continue outer;
    }
    return true;
  }
  return false;
}

/** `50px we-scene-font-2` → { size: 50, family: "we-scene-font-2" }。 */
function fontOf(css: string) {
  const match = /^(\d+(?:\.\d+)?)px\s+(.+)$/.exec(css.trim());
  return match ? { size: Number(match[1]), family: match[2] } : { size: 0, family: css };
}

test.use({ viewport: { width: 1600, height: 900 } });

test("竖排日期：清单格式/字号/坐标与 WE 一致，逐行光栅成三段竖排", async ({ page }) => {
  test.setTimeout(300_000);
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

  await installSceneHook(page);
  await mockSettings(page, {
    id: "scene-text-3723",
    path: SCENE_PREVIEW,
    name: "竖排日期验证（3723）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  });

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 180_000 });
  // 首帧是工程烘焙值，WE 生效值（这里是工程默认，全部未覆盖）随后热重建；
  // 不等它落地就取样，会拿到旧清单/旧位图。
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-preset", "applied", { timeout: 180_000 });

  // ① 清单：等带 time 档位的文字层到齐（首帧就有；重建后还会再来一份，取最后一份）。
  let manifest: SceneRec["manifests"][number] | undefined;
  await expect.poll(async () => {
    const rec = await readScene(page);
    manifest = rec.manifests[rec.manifests.length - 1];
    const timed = (manifest?.layers ?? []).filter((layer) => layer.kind === "text" && layer.time);
    return timed.filter((layer) => layer.time === "date").length + timed.filter((layer) => layer.time === "clock").length;
  }, { timeout: 60_000, message: "清单里始终没有日期/时钟文字层" }).toBeGreaterThanOrEqual(3);

  const textLayers = (manifest!.layers ?? []).filter((layer) => layer.kind === "text" && layer.time);
  const date = textLayers.find((layer) => layer.time === "date" && String(layer.format).includes("{month}"));
  const day = textLayers.find((layer) => layer.time === "date" && layer.format === "{weekday}");
  const clock = textLayers.find((layer) => layer.time === "clock");
  const greeting = textLayers.find((layer) => layer.time === "greeting");
  const fps = textLayers.find((layer) => layer.time === "fps");
  expect(date, `没发日期层：${JSON.stringify(textLayers)}`).toBeTruthy();
  expect(day, `没发星期层：${JSON.stringify(textLayers)}`).toBeTruthy();
  expect(clock, `没发时钟层：${JSON.stringify(textLayers)}`).toBeTruthy();
  expect(greeting, `没发问候语层：${JSON.stringify(textLayers)}`).toBeTruthy();
  expect(fps, `没发帧率层：${JSON.stringify(textLayers)}`).toBeTruthy();

  // 日期：三段竖排模板 + 作者表原样带到清单。
  expect(date!.format, "日期模板不是竖排三段式").toBe("{dayTable}\n\n{month}\n\n{year}");
  expect(date!.months?.[9], "十月月名没按竖排表拆成 O/C/T").toBe("O\nC\nT");
  expect(date!.dayTable?.[25], "25 号没按补零表拆成 2/5").toBe("2\n5");
  expect(date!.dayTable?.[1], "1 号该补 0 再竖排").toBe("0\n1");
  expect(date!.yearSplit, "年份没逐字换行").toBe("\n");
  // 星期：只剩 {weekday}（日/月/年归日期层各管一段，别重复拼）。
  expect(day!.format, "星期层格式不对（应为纯星期名）").toBe("{weekday}");
  expect(day!.days?.[0], "星期表不是大写竖排").toBe("S\nU\nN");
  expect(day!.days?.length, "星期表应有 7 项").toBe(7);

  // 问候语：作者的小时阈值表原样进清单（不执行脚本），英文变体是可见的那一支。
  expect(greeting!.labels, "问候语标签表不对").toEqual([
    "Before dawn", "At night", "Morning", "Morning", "Noon", "Afternoon", "Evening", "Night",
  ]);
  expect(greeting!.thresholds, "问候语阈值表不对").toEqual([2, 6, 8, 11, 13, 17, 20]);

  // 帧率：label / 刷新窗口 / 工程烘焙初值（全角冒号是作者写的，运行期会换成 label + ASCII 数字）。
  expect(fps!.label, "帧率前缀不对").toBe("fps:");
  expect(fps!.refreshMs, "帧率刷新窗口不对").toBe(500);
  expect(fps!.text, "帧率烘焙初值不对").toBe("fps：60");

  // ② 字号 = pointsize × 自身与中间父组 scale、不吃最外层组件根组：
  //    140×0.18 / 96.653×0.85 / 202.26×0.28038 / 32×0.23025×3.125 / 24×0.948。
  //    只吃自身 scale 会把问候语压成 7.37 的小糊点（参考图里它与帧率同高）；
  //    把根组 0.28 也乘进来则会让整组文字小到看不见。
  expect(date!.fontSize, "日期字号不对（父组 scale 为 1，根组不该进来）").toBeCloseTo(25.2, 2);
  expect(day!.fontSize, "星期字号没吃到父组 0.85").toBeCloseTo(82.155, 2);
  expect(clock!.fontSize, "时钟字号不对").toBeCloseTo(56.7102, 2);
  expect(greeting!.fontSize, "问候语字号没吃到父组 3.125").toBeCloseTo(23.025, 3);
  expect(fps!.fontSize, "帧率字号没吃到父组 0.948").toBeCloseTo(22.752, 3);

  // ③ 摆法：日期在左、星期在右且更靠上、时钟在两者下方；问候语与帧率在日期/星期上方
  //    （问候语在左、帧率在右）；字级 星期 > 时钟 > 日期。
  //    场景坐标是 Y 向上（播放器 ortho 0..h），所以 y 越大越靠上。
  expect(date!.x!, "日期应在星期左侧").toBeLessThan(day!.x!);
  expect(date!.x!, "日期应在时钟左侧").toBeLessThan(clock!.x!);
  expect(clock!.y!, "时钟应在日期下方").toBeLessThan(date!.y!);
  expect(date!.y!, "日期应在星期下方").toBeLessThan(day!.y!);
  expect(day!.fontSize!, "星期应比时钟大").toBeGreaterThan(clock!.fontSize!);
  expect(clock!.fontSize!, "时钟应比日期大").toBeGreaterThan(date!.fontSize!);
  expect(greeting!.y!, "问候语应在日期上方").toBeGreaterThan(date!.y!);
  expect(fps!.y!, "帧率应在星期上方").toBeGreaterThan(day!.y!);
  expect(greeting!.x!, "问候语应在帧率左侧").toBeLessThan(fps!.x!);
  expect(date!.x!).toBeCloseTo(801.86, 1);
  expect(date!.y!).toBeCloseTo(1398.0, 1);
  expect(day!.x!).toBeCloseTo(923.47, 1);
  expect(day!.y!).toBeCloseTo(1431.06, 1);
  expect(clock!.x!).toBeCloseTo(979.99, 1);
  expect(clock!.y!).toBeCloseTo(1245.55, 1);
  expect(greeting!.x!).toBeCloseTo(794.88, 1);
  expect(greeting!.y!).toBeCloseTo(1618.27, 1);
  expect(fps!.x!).toBeCloseTo(957.24, 1);
  expect(fps!.y!).toBeCloseTo(1607.39, 1);

  // ④ 光栅：等五段文本都真出过字。取样跨分钟时时钟会换句子，所以判据用「起始时刻或
  //    当前时刻」两套前缀；日期/星期当天不变；问候语同理按小时取两套；帧率必须出现
  //    运行期算出的 `label + 整数`（工程烘焙的 "fps：60" 不算数）。
  const wantedAt = new Date();
  const wanted = expectedLines(wantedAt);
  const greetingLabels = greeting!.labels ?? [];
  const greetingThresholds = greeting!.thresholds ?? [];
  const greetingTexts = new Set([
    greetingOf(wantedAt, greetingLabels, greetingThresholds),
    greetingOf(new Date(), greetingLabels, greetingThresholds),
  ].filter(Boolean));
  const fpsPattern = new RegExp(
    `^${(fps!.label ?? "fps:").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\d+$`,
  );
  let groups: ReturnType<typeof bitmapGroups> = [];
  await expect.poll(async () => {
    groups = bitmapGroups((await readScene(page)).draws);
    const now = new Date();
    const prefixes = [...clockPrefixes(wantedAt), ...clockPrefixes(now)];
    const dateOk = groups.some((group) => containsSequence(group.lines, wanted.date));
    const dayOk = groups.some((group) => containsSequence(group.lines, wanted.day));
    const clockOk = groups.some((group) => group.lines.some(
      (line) => CLOCK_RE.test(line) && prefixes.some((prefix) => line.startsWith(prefix)),
    ));
    const greetingOk = groups.some((group) => group.lines.some((line) => greetingTexts.has(line)));
    const fpsOk = groups.some((group) => group.lines.some((line) => fpsPattern.test(line)));
    return [dateOk, dayOk, clockOk, greetingOk, fpsOk].filter(Boolean).length;
  }, { timeout: 120_000, intervals: [500, 1_000, 2_000, 2_000], message: "五段文字没有全部光栅出来" })
    .toBe(5);

  const dateGroup = groups.find((group) => containsSequence(group.lines, wanted.date))!;
  const dayGroup = groups.find((group) => containsSequence(group.lines, wanted.day))!;
  const clockGroup = groups.find((group) => group.lines.some((line) => CLOCK_RE.test(line)))!;
  const greetingGroup = groups.find((group) => group.lines.some((line) => greetingTexts.has(line)))!;
  const fpsGroup = groups.find((group) => group.lines.some((line) => fpsPattern.test(line)))!;

  // 字号经 2× 超采样进画布：25.2→50px、82.155→164px、56.71→113px、23.025→46px、22.752→46px。
  // 同时这也说明绘制统一走 we-scene-font-*（FontFace 家族），没有退系统字栈。
  expect(fontOf(dateGroup.font).size, "日期位图字号不是 25.2×2").toBe(50);
  expect(fontOf(dayGroup.font).size, "星期位图字号不是 82.155×2").toBe(164);
  expect(fontOf(clockGroup.font).size, "时钟位图字号不是 56.71×2").toBe(113);
  expect(fontOf(greetingGroup.font).size, "问候语位图字号不是 23.025×2").toBe(46);
  expect(fontOf(fpsGroup.font).size, "帧率位图字号不是 22.752×2").toBe(46);
  for (const group of [dateGroup, dayGroup, clockGroup, greetingGroup, fpsGroup]) {
    expect(fontOf(group.font).family, `位图没吃到加载好的字体：${group.font}`)
      .toMatch(/^we-scene-font-/);
    // 画布高度按行数撑开：日期 11 行、星期 3 行、时钟/问候语/帧率 1 行。横排日期会是一行矮画布。
    const height = Number(group.canvas.split("x")[1]);
    expect(height, `位图高度 ${group.canvas} 撑不出竖排行数`).toBeGreaterThan(0);
  }
  expect(Number(dateGroup.canvas.split("x")[1]), "日期位图不像 11 行竖排").toBeGreaterThan(400);
  expect(Number(dayGroup.canvas.split("x")[1]), "星期位图不像 3 行竖排").toBeGreaterThan(300);
  expect(Number(clockGroup.canvas.split("x")[1]), "时钟位图不该是多行").toBeLessThan(250);
  expect(Number(greetingGroup.canvas.split("x")[1]), "问候语位图不该是多行").toBeLessThan(100);
  expect(Number(fpsGroup.canvas.split("x")[1]), "帧率位图不该是多行").toBeLessThan(100);

  // 字形位图确实进了 GPU：每个「字体+字号+句子」组合一次 texImage2D(canvas)。
  const rec = await readScene(page);
  expect(rec.uploads.length, "字形位图没上传成纹理").toBeGreaterThanOrEqual(groups.length);

  // ⑤ 字体：Quicksand 从 scene.pkg 走 blob，spincycle / Segment7 从 WE 安装目录走 we-asset 路由。
  for (const fontPath of BUILT_IN_FONTS) {
    const assets = assetResponses.filter((item) => decodeURIComponent(item.url).includes(fontPath));
    expect(assets.length, `浏览器没有向 we-asset 路由请求内置字体 ${fontPath}`).toBeGreaterThan(0);
    expect(assets[0].status, `内置字体 ${fontPath} 取回 ${assets[0].status}`).toBe(200);
    expect(assets[0].bytes, `内置字体 ${fontPath} 字节长度为 0，光栅不可能用它`).toBeGreaterThan(1000);
  }

  const faces = await readFonts(page);
  const statusOf = new Map(faces.map((face) => [face.family, face.status]));
  for (const group of [dateGroup, dayGroup, clockGroup, greetingGroup, fpsGroup]) {
    const { family } = fontOf(group.font);
    expect(statusOf.get(family), `字体 ${family} 没登记或没加载成功：${JSON.stringify(faces)}`).toBe("loaded");
  }

  await page.evaluate((css) => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }, ISOLATE_CSS);
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SHOT_DIR, "scene-text-3723.png") });
  // 日期列 + 星期 + 时钟都挤在画布左侧这一块（1600×900 下的场景坐标换算），单独裁一张便于比对。
  await page.screenshot({
    path: resolve(SHOT_DIR, "scene-text-3723-crop.png"),
    clip: { x: 250, y: 200, width: 480, height: 260 },
  });

  expect(errors, errors.join("\n")).toEqual([]);
});
