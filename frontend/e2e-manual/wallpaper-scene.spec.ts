import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真实 vite + 真实后端 + 真实浏览器：创意工坊 scene 作品的实时渲染（第三刀）。
 *
 * 验的是用户实锤的那两件事：
 * 1. 部件错位 —— 静态预览把多层拼好的图当单张图，动不起来；实时渲染器按图层网格绘制，
 *    所以这里断言「画布盖住后截图必变」，证明画布真有像素。
 * 2. 过于放大 —— `center`（原尺寸）在 3840×2160 场景上等于裁切放大，必须落到 cover；
 *    所以既看像素，也在播放器这一侧记录真正收到的 `dsh-set-fit`。
 * 设置读写全程 mock，不污染用户真实壁纸；scene.pkg 走真实后端。
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-scene");
const SCENE_ID = "2797363351";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\2797363351\preview.jpg`;
// scene.pkg 在，但包里没有可用的场景结构：必须回落静态预览，不能白屏。
const DEAD_SCENE_ID = "2489687429";
const DEAD_SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\2489687429\preview.jpg`;
// 探测过「两帧之间画布像素真的在变」的作品：用它证明时间轴在跑，而不只是画了一帧静图。
const ANIM_SCENE_ID = "2986502697";
const ANIM_SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\2986502697\preview.gif`;

// 只留壁纸层：UI 面板与会动的极光/预览图全部遮掉，截图差异就只能来自实时渲染的画布。
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

const setIsolate = (page: Page, enable: boolean) => page.evaluate(([css, on]: [string, boolean]) => {
  const existing = document.getElementById("e2e-isolate");
  if (on && !existing) {
    const style = document.createElement("style");
    style.id = "e2e-isolate";
    style.textContent = css;
    document.head.appendChild(style);
  }
  if (!on) existing?.remove();
}, [ISOLATE_CSS, enable]);

const setCanvasVisible = (page: Page, visible: boolean) => page.evaluate((flag) => {
  const frame = document.querySelector<HTMLElement>(".app-backdrop-scene");
  if (frame) frame.style.visibility = flag ? "" : "hidden";
}, visible);

/** 在播放器（同源 iframe）里记录宿主真正发进来的控制消息。 */
const armMessageSpy = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const win = frame?.contentWindow as (Window & { __e2eMsgs?: Record<string, unknown>[] }) | null;
  if (!win) throw new Error("找不到场景播放器 iframe");
  win.__e2eMsgs = [];
  win.addEventListener("message", (event) => {
    const data = event.data as Record<string, unknown> | null;
    if (data && typeof data.type === "string") win.__e2eMsgs?.push(data);
  });
});

/** 宿主 → 播放器的全部控制消息（fit / pause），顺序即行为。 */
const readMessages = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const win = frame?.contentWindow as (Window & { __e2eMsgs?: Record<string, unknown>[] }) | null;
  return (win?.__e2eMsgs || []).map((m) => (m.type === "dsh-set-fit" ? m.fit : m));
});

const clip = { x: 80, y: 80, width: 620, height: 620 };

test("scene 作品实时渲染：画布真有像素，填充方式真的传到播放器", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await mockSettings(page, {
    id: "scene-live",
    path: SCENE_PREVIEW,
    name: "[2K] Yor Forger（实时场景）",
    kind: "image",
    workshopId: SCENE_ID,
    scenePkg: true,
  });

  const started = Date.now();
  await page.goto("/");
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 60_000 });
  expect(Date.now() - started).toBeLessThan(45_000);

  // 同源 iframe ⇒ 直接读播放器状态：渲染异常守卫没触发、画布尺寸随窗口走
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

  await armMessageSpy(page);
  await setIsolate(page, true);
  await page.waitForTimeout(500);
  const withScene = await page.screenshot({ clip });
  await setCanvasVisible(page, false);
  const withoutScene = await page.screenshot({ clip });
  await setCanvasVisible(page, true);
  expect(withScene.equals(withoutScene), "盖住画布截图不变 ⇒ 实时渲染其实没画上东西").toBe(false);
  await setIsolate(page, false);

  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SHOT_DIR, "fit-cover.png") });

  // 真实产品路径：控制台「填充方式」→ 播放器消息
  await page.locator(".lg-rope").click();
  await page.getByRole("tab", { name: "效果", exact: true }).click();

  await page.getByRole("button", { name: "完整", exact: true }).click();
  await page.waitForTimeout(600);
  expect(await readMessages(page), "宿主没把 contain 送进播放器").toEqual(["contain"]);
  await page.screenshot({ path: resolve(SHOT_DIR, "fit-contain.png") });
  await setIsolate(page, true);
  const contained = await page.screenshot({ clip });
  await setIsolate(page, false);
  expect(contained.equals(withScene), "换 fit 后画面没变 ⇒ fit 没生效").toBe(false);

  // 「居中」= CSS 原尺寸，对 4K 场景就是用户说的「过于放大」，必须映射成 cover
  await page.getByRole("button", { name: "居中", exact: true }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: resolve(SHOT_DIR, "fit-center-mapped.png") });

  expect(await readMessages(page)).toEqual(["contain", "cover"]);
  expect(errors, errors.join("\n")).toEqual([]);
});

test("包里取不到场景结构时回落静态预览，不白屏", async ({ page }) => {
  const notices: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "warning" || msg.type() === "error") notices.push(msg.text());
  });

  await mockSettings(page, {
    id: "scene-dead",
    path: DEAD_SCENE_PREVIEW,
    name: "Zero Two（无可渲染结构）",
    kind: "image",
    workshopId: DEAD_SCENE_ID,
    scenePkg: true,
  });

  await page.goto("/");
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "fallback", { timeout: 60_000 });
  await expect(page.locator("img.app-backdrop-media")).toBeVisible();
  expect(notices.join("\n")).toContain("scene");

  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SHOT_DIR, "fallback-preview.png") });
});

test("实时渲染真的在动：同一机位两帧之间画面必须变化", async ({ page }) => {
  await mockSettings(page, {
    id: "scene-animated",
    path: ANIM_SCENE_PREVIEW,
    name: "实时动画场景",
    kind: "image",
    workshopId: ANIM_SCENE_ID,
    scenePkg: true,
  });

  await page.goto("/");
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 60_000 });

  // 只留画布，UI 与极光都不动 ⇒ 两张截图的差异只能来自播放器自己画的帧
  await setIsolate(page, true);
  await page.waitForTimeout(1000);
  const frameA = await page.screenshot({ clip });
  await page.waitForTimeout(2000);
  const frameB = await page.screenshot({ clip });
  await setIsolate(page, false);

  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SHOT_DIR, "animated-frame-b.png") });

  // 静态预览做不到这件事：把 scene.pkg 的首帧当图片贴上去，两帧永远相同。
  expect(frameB.equals(frameA), "两帧之间画面不变 ⇒ 时间轴没跑，只是贴了张静图").toBe(false);
});
