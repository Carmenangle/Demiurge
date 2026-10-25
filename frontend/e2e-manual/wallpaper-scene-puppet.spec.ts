import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 临时真机验证（puppet 拼接修复）：创意工坊 3346715292「八奈见杏菜——夜莺Night」。
 * 该场景的角色是 WE puppet（MDLV 网格 + 骨骼附件），修前表现为部件散落；
 * 这里只看真实浏览器里渲染出的画面是否是一个完整角色。
 *
 * 跑法：隔离后端(8011) + 临时 vite(5198)，串行：
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test \
 *     --config playwright.e2e-manual.config.ts e2e-manual/wallpaper-scene-puppet.spec.ts --workers=1
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-puppet");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "3346715292";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3346715292\preview.gif`;

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

const settingsWith = (wallpaper: Record<string, unknown>) => ({
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [wallpaper],
    activeWallpaperId: wallpaper.id,
    wallpaperFit: "cover",
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

test.use({ viewport: { width: 1600, height: 900 } });

test("puppet 角色按网格+骨骼拼合：整屏截图 + 网格绘制计数", async ({ page }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await mockSettings(page, {
    id: "scene-puppet",
    path: SCENE_PREVIEW,
    name: "八奈见杏菜（夜莺Night · 实时场景）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
    sceneSource: `workshop:${SCENE_ID}`,
  });

  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });

  const playerState = await page.evaluate(() => {
    const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    const win = frame?.contentWindow as (Window & { __weRenderErr?: number }) | null;
    const canvas = win?.document.getElementById("canvas") as HTMLCanvasElement | null;
    return {
      renderError: win ? win.__weRenderErr === 1 : null,
      canvasWidth: canvas?.width ?? 0,
      canvasHeight: canvas?.height ?? 0,
    };
  });
  expect(playerState.renderError).toBe(false);
  expect(playerState.canvasWidth).toBeGreaterThan(0);
  expect(playerState.canvasHeight).toBeGreaterThan(0);

  // 播放器内部：统计 drawElements（puppet 网格走这条路径）在两帧里的次数
  const meshDraws = await page.evaluate(async () => {
    const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
    const win = frame?.contentWindow as (Window & {
      WebGLRenderingContext?: typeof WebGLRenderingContext;
    }) | null;
    if (!win) return -1;
    const proto = (win as unknown as { WebGLRenderingContext: typeof WebGLRenderingContext })
      .WebGLRenderingContext?.prototype;
    if (!proto) return -1;
    let count = 0;
    const original = proto.drawElements;
    proto.drawElements = function patched(this: WebGLRenderingContext, ...args: Parameters<WebGLRenderingContext["drawElements"]>) {
      count += 1;
      return original.apply(this, args);
    };
    await new Promise((r) => win.setTimeout(r, 400));
    proto.drawElements = original;
    return count;
  });
  expect(meshDraws, "播放器没有走 drawElements ⇒ 网格层没被绘制").toBeGreaterThan(0);

  mkdirSync(SHOT_DIR, { recursive: true });
  await page.waitForTimeout(600);
  await page.screenshot({ path: resolve(SHOT_DIR, "puppet-full.png") });

  await page.evaluate((css) => {
    const style = document.createElement("style");
    style.id = "e2e-isolate";
    style.textContent = css;
    document.head.appendChild(style);
  }, ISOLATE_CSS);
  await page.waitForTimeout(400);
  await page.screenshot({ path: resolve(SHOT_DIR, "puppet-scene-only.png") });

  expect(errors, errors.join("\n")).toEqual([]);
});
