import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真机验证（创意工坊 3346715292 · Tier D 粒子）：WE 粒子系统走新加的 2D 正交通道，
 * 在应用里真的**生成并绘制**出来。
 *
 * 这份作品实测有 3 条 sprite 系统：鸟群 `day2`（包内序列帧图集）、官方雾 `雾 1`、
 * 官方光束 `光束 2 (旧)`（后两者的贴图不在 scene.pkg 里，而在 Wallpaper Engine 安装目录）。
 * 同作品的鼠标尾迹 `Trails 2` 是 rope 渲染器、`night13` 是 ropetrail，本实现不模拟 ⇒
 * 它们既不出现，也不交开关（16 册红线 16）。
 *
 * 证据分三层，逐层可分辨（只看像素分不出「没发射」「贴图没加载上」「模拟没推进」）：
 * ① HTTP：`/wallpapers/we-asset` 真把 fog2.tex / light_shafts_6.tex 取回来；
 * ② 播放器现场 `window.__weParticles`：系统条数 + 每条存活数 + 本帧绘制的 quad 数；
 * ③ `steps`：模拟推进过的帧数，暂停态必须冻住。
 *
 * 依赖：带 we-asset 路由的后端。8010 是用户正在用的实例，所以跑隔离后端(8011)
 * + 临时 vite(5198，/api 反代 8011)：
 *   MSYS_NO_PATHCONV=1 VITE_API_BASE=/api node frontend/node_modules/vite/bin/vite.js \
 *     --config _tmp/vite.e2e-8011.config.mjs
 *   E2E_APP_URL=http://127.0.0.1:5198/ npx playwright test --workers=1 \
 *     --config playwright.e2e-manual.config.ts -g "粒子"
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-particle");
const APP_URL = process.env.E2E_APP_URL || "/";
const SCENE_ID = "3346715292";
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3346715292\preview.gif`;
const BUILT_IN_TEXES = ["materials/particle/fog/fog2.tex", "materials/particle/light/light_shafts_6.tex"];

const ISOLATE_CSS = `
.app > :not(.app-backdrop) { visibility: hidden !important; }
.app-backdrop-aurora, .app-backdrop-scrim, .app-backdrop-media { display: none !important; }
`;

type Page = import("@playwright/test").Page;

type ParticleStats = { systems: number; live: Record<string, number>; quads: number; steps: number };

type PlayerWindow = Window & { __weParticles?: ParticleStats };

const settingsWith = (wallpaper: Record<string, unknown>, paused: boolean) => ({
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [wallpaper],
    activeWallpaperId: wallpaper.id,
    wallpaperFit: "cover",
    wallpaperPaused: paused,
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

/** 播放器 iframe 里的粒子现场；没挂上播放器时直接抛可读错误。 */
const readParticles = (page: Page) => page.evaluate(() => {
  const frame = document.querySelector<HTMLIFrameElement>(".app-backdrop-scene");
  const stats = (frame?.contentWindow as PlayerWindow | null)?.__weParticles;
  if (!stats) throw new Error("播放器还没写出 __weParticles");
  return stats;
});

/**
 * 轮询到「每条系统都有存活精灵」：预热不足、贴图未到、清单未重建都会让某条留空，
 * 一次性断言会把「慢」误判成「坏」。
 */
async function waitForParticles(page: Page) {
  let stats: ParticleStats | null = null;
  for (let attempt = 0; attempt < 30; attempt++) {
    await page.waitForTimeout(400);
    try {
      stats = await readParticles(page);
    } catch {
      stats = null;
    }
    if (stats && stats.systems > 0 && stats.quads > 0
      && Object.values(stats.live).every((count) => count > 0)) break;
  }
  return stats;
}

const sceneWallpaper = (id: string, name: string) => ({
  id,
  path: SCENE_PREVIEW,
  name,
  kind: "image",
  workshopId: SCENE_ID,
  scenePkg: true,
  sceneSource: `workshop:${SCENE_ID}`,
});

test.use({ viewport: { width: 1600, height: 900 } });

test("2D 粒子：雾 / 光束 / 鸟群都生成并绘制，包外贴图经内置资产通道取回", async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  const assetResponses: { path: string; status: number; bytes: number }[] = [];
  page.on("response", async (response) => {
    const url = response.url();
    if (!url.includes("/wallpapers/we-asset")) return;
    let bytes = -1;
    try {
      bytes = (await response.body()).length;
    } catch {
      bytes = -1;
    }
    const query = new URL(url).searchParams.get("path") ?? "";
    assetResponses.push({ path: decodeURIComponent(query), status: response.status(), bytes });
  });

  await mockSettings(page, sceneWallpaper("scene-particle", "八奈见杏菜（夜莺Night · 粒子验证）"));
  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });

  const stats = await waitForParticles(page);
  expect(stats, "轮询 12 秒后仍没有「每条系统都有存活精灵」的现场").not.toBeNull();
  const live = Object.entries(stats!.live);

  // ① 三条 sprite 系统全在场（鸟群 + 雾 + 光束），且每条都真的生出了精灵——
  //    某条贴图没加载上时播放器整条跳过，live 里就不会有它。
  expect(stats!.systems, `粒子系统条数不对：${JSON.stringify(live)}`).toBeGreaterThanOrEqual(3);
  expect(live.length, `存活的系统条数不对：${JSON.stringify(live)}`).toBeGreaterThanOrEqual(3);
  for (const [name, count] of live) {
    expect(count, `系统「${name}」一只精灵都没生成`).toBeGreaterThan(0);
  }
  // ② 本帧真的提交了 quad（数量与存活数一致 ⇒ 绘制分支走到了每个精灵）。
  const alive = live.reduce((total, [, count]) => total + count, 0);
  expect(stats!.quads, `绘制了 ${stats!.quads} 个 quad，存活 ${alive} 只`).toBeGreaterThanOrEqual(alive);
  // ③ 模拟在推进（暂停态的对照在下一条用例）。
  const first = await readParticles(page);
  await page.waitForTimeout(2_000);
  const later = await readParticles(page);
  expect(later.steps, `2 秒内模拟没有推进（steps ${first.steps} → ${later.steps}）`)
    .toBeGreaterThan(first.steps);

  // ④ 包外内置贴图：主线程经 we-asset 路由取到字节，才可能出现在 ② 的绘制里。
  for (const tex of BUILT_IN_TEXES) {
    const hit = assetResponses.find((item) => item.path === tex);
    expect(hit, `浏览器没有向 we-asset 请求 ${tex}：${JSON.stringify(assetResponses)}`).toBeTruthy();
    expect(hit!.status, `${tex} 取回 ${hit!.status}`).toBe(200);
    expect(hit!.bytes, `${tex} 字节长度为 0`).toBeGreaterThan(1000);
  }

  mkdirSync(SHOT_DIR, { recursive: true });
  await page.evaluate((css) => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }, ISOLATE_CSS);
  await page.screenshot({ path: resolve(SHOT_DIR, "scene-particle.png") });

  expect(errors, errors.join("\n")).toEqual([]);
});

/**
 * 暂停 = 粒子定格。
 *
 * 签名与文字那条同源但取的是另一处现场：垫片的 settle 判据是「连续两帧 draw call 相同」，
 * 而粒子每帧都在画（`quads > 0`），所以「仍在画但 steps 不再增长」正是
 * `if (!isPaused) updateParticles2d(...)` 生效的可观测证明——只截一张图分辨不出冻住与否。
 */
test("暂停时粒子定格：每帧仍在绘制，但模拟帧数不再推进", async ({ page }) => {
  test.setTimeout(240_000);
  await mockSettings(page, sceneWallpaper("scene-particle-paused", "八奈见杏菜（夜莺Night · 粒子暂停）"), true);
  await page.goto(APP_URL);
  await expect(page.locator(".app-backdrop-scene-host"))
    .toHaveAttribute("data-scene-status", "ready", { timeout: 120_000 });

  const stats = await waitForParticles(page);
  expect(stats, "暂停态 settle 后仍没有粒子现场").not.toBeNull();
  expect(stats!.quads, "暂停态没有绘制任何粒子精灵").toBeGreaterThan(0);

  const frozen = await readParticles(page);
  await page.waitForTimeout(4_000);
  const after = await readParticles(page);
  expect(after.steps, `4 秒内模拟仍在推进（steps ${frozen.steps} → ${after.steps}）`)
    .toBe(frozen.steps);
  expect(after.quads, `冻住后本帧不再画精灵：${JSON.stringify(after)}`).toBeGreaterThan(0);
});
