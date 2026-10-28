import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验收（2026-10-28 用户实锤：应用里 EVA 雨景比 WE 桌面原图暗一半）：
 * 播放器输出与原图逐区域比 1.00，暗化全部来自液态玻璃的 `.app-backdrop-scrim`
 * （默认 25% 平铺 + 上下渐变 + 边缘径向）。本用例锁两件事：
 *   ① 场景就绪（sceneCovered）时挂 `has-scene`，scrim 整层 `background:none`，
 *      用户滑杆值（scrim=25）仍在但不再作用于实时场景；
 *   ② 把旧 scrim 注回去（仅本用例），截图 A/B 三段横向条带的亮度比必须符合
 *      「上方浅、下方深」的渐变形状——证明撤掉的正是压暗画面的那一层；
 *   ③ 照片壁纸仍走暗化：无 `has-scene`，scrim 计算样式保留 rgba(2,6,16,0.25)。
 *
 * 跑法（隔离后端 8011 + 临时 vite 5198，均不碰用户的 8010/5173）：
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test --workers=1 \
 *     --config playwright.e2e-manual.config.ts -g "不叠暗化"
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-scene-fidelity");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "2587542891";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\2587542891\preview.jpg`;

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-media { display: none !important; }
`;

/** 旧规则原文（撤掉的那层），只在用例里注回去做前后对照。 */
const OLD_SCRIM_CSS = `
html[data-theme="liquid-glass"] .app-backdrop .app-backdrop-scrim {
  background:
    radial-gradient(120% 90% at 50% 0%, transparent 40%, rgba(2, 6, 16, 0.35) 100%),
    linear-gradient(180deg, rgba(4, 8, 18, 0.10) 0%, rgba(4, 8, 18, 0.34) 100%),
    rgba(2, 6, 16, var(--lg-scrim, 0.25)) !important;
}
`;

type Page = import("@playwright/test").Page;

const settingsWith = (wallpaper: Record<string, unknown>, scrim: number) => ({
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [wallpaper],
    activeWallpaperId: wallpaper.id,
    wallpaperFit: "cover",
    wallpaperPaused: false,
    pauseOnHidden: false,
    pauseOnBlur: false,
    pauseOnBattery: false,
    scrim,
    wallpaperBlur: 0,
    wallpaperBrightness: 100,
    wallpaperContrast: 100,
    wallpaperSaturate: 100,
  },
});

async function mockSettings(page: Page, wallpaper: Record<string, unknown>, scrim: number) {
  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { repos: null, settings: settingsWith(wallpaper, scrim) } });
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

/** 在页面里解码截图并算三段横向条带的平均 RGB（用浏览器自带的图像解码，免装 PNG 库）。 */
async function bandMeans(page: Page, buffer: Buffer) {
  return page.evaluate(async (b64) => {
    const blob = await (await fetch(`data:image/jpeg;base64,${b64}`)).blob();
    const bmp = await createImageBitmap(blob);
    const canvas = document.createElement("canvas");
    canvas.width = bmp.width;
    canvas.height = bmp.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    ctx.drawImage(bmp, 0, 0);
    const { data } = ctx.getImageData(0, 0, bmp.width, bmp.height);
    return [[0, 0.15], [0.4, 0.55], [0.85, 1]].map(([from, to]) => {
      const y0 = Math.floor(bmp.height * from);
      const y1 = Math.floor(bmp.height * to);
      let r = 0; let g = 0; let b = 0; let n = 0;
      for (let y = y0; y < y1; y++) {
        for (let x = 0; x < bmp.width; x++) {
          const i = (y * bmp.width + x) * 4;
          r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
        }
      }
      return { r: r / n, g: g / n, b: b / n };
    });
  }, buffer.toString("base64"));
}

const luma = (m: { r: number; g: number; b: number }) => 0.2126 * m.r + 0.7152 * m.g + 0.0722 * m.b;

async function injectStyle(page: Page, css: string) {
  await page.evaluate((text) => {
    const style = document.createElement("style");
    style.textContent = text;
    document.head.appendChild(style);
  }, css);
}

test.use({ viewport: { width: 1600, height: 900 } });

test("场景壁纸不叠暗化：has-scene 撤掉 scrim，对照亮度呈上浅下深", async ({ page }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await mockSettings(page, {
    id: "scene-rain-eva",
    path: SCENE_PREVIEW,
    name: "雨景验证（EVA）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  }, 25);

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 180_000 });

  // ① 类与计算样式：用户滑杆值 0.25 还在，但实时场景不吃这层。
  await expect(page.locator(".app-backdrop")).toHaveClass(/has-scene/);
  const scrimState = await page.evaluate(() => {
    const scrim = document.querySelector<HTMLElement>(".app-backdrop-scrim")!;
    const style = getComputedStyle(scrim);
    return {
      image: style.backgroundImage,
      color: style.backgroundColor,
      scrimVar: getComputedStyle(document.documentElement).getPropertyValue("--lg-scrim").trim(),
    };
  });
  expect(scrimState.image, "scrim 仍有渐变").toBe("none");
  expect(scrimState.color, "scrim 仍有平铺暗色").toBe("rgba(0, 0, 0, 0)");
  expect(scrimState.scrimVar, "用户暗化滑杆值没进来，验的就不是他的场景").toBe("0.25");

  await injectStyle(page, ISOLATE_CSS);
  await page.waitForTimeout(500);
  mkdirSync(SHOT_DIR, { recursive: true });
  const clean = await page.screenshot({ type: "jpeg", quality: 92 });
  await page.screenshot({ path: resolve(SHOT_DIR, "scene-scrim-25.png") });

  // ② 注回旧 scrim：同一帧附近再截一张，两图三段条带的亮度差必须像渐变本身。
  await injectStyle(page, OLD_SCRIM_CSS);
  const restored = await page.evaluate(() =>
    getComputedStyle(document.querySelector<HTMLElement>(".app-backdrop-scrim")!).backgroundImage);
  expect(restored, "旧 scrim 没注回去，前后对照无效").toContain("radial-gradient");
  await page.waitForTimeout(300);
  const dimmed = await page.screenshot({ type: "jpeg", quality: 92 });
  await page.screenshot({ path: resolve(SHOT_DIR, "scene-scrim-restored.png") });

  const [cleanBands, dimmedBands] = [await bandMeans(page, clean), await bandMeans(page, dimmed)];
  const ratios = cleanBands.map((band, i) => luma(band) / luma(dimmedBands[i]));
  // 预测（1600×900、scrim 0.25、旧三层叠乘）：上段 ≈1.5、中段 ≈1.8、下段 ≈2.6（暗部加性项会拉低）。
  expect(ratios[0], `上段亮度比 ${ratios[0].toFixed(2)} 不像撤掉了一层暗化`).toBeGreaterThan(1.2);
  expect(ratios[1], `中段亮度比 ${ratios[1].toFixed(2)} 不像撤掉了一层暗化`).toBeGreaterThan(1.4);
  expect(ratios[2], `下段亮度比 ${ratios[2].toFixed(2)} 不像撤掉了一层暗化`).toBeGreaterThan(2.0);
  expect(ratios[0], "渐变形状不对：上段反而比下段压得更狠").toBeLessThan(ratios[2]);

  expect(errors, errors.join("\n")).toEqual([]);
});

test("照片壁纸仍叠暗化：无 has-scene，scrim 保留用户滑杆值", async ({ page }) => {
  test.setTimeout(120_000);
  await mockSettings(page, {
    id: "photo-rain-eva",
    path: SCENE_PREVIEW,
    name: "照片验证（EVA 预览图）",
    kind: "image",
  }, 25);

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-media")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".app-backdrop")).not.toHaveClass(/has-scene/);

  const scrimState = await page.evaluate(() => {
    const style = getComputedStyle(document.querySelector<HTMLElement>(".app-backdrop-scrim")!);
    return { image: style.backgroundImage, color: style.backgroundColor };
  });
  expect(scrimState.image, "照片壁纸的渐变兜底被误删了").toContain("linear-gradient");
  expect(scrimState.color, "照片壁纸没吃到用户暗化滑杆").toBe("rgba(2, 6, 16, 0.25)");
});
