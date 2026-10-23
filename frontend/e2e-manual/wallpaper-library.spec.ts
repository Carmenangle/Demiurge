import { expect, test } from "@playwright/test";
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 液态玻璃壁纸库（真实 vite + 真实后端）：
 * 存放地址扫描 → 三列卡片/缩略图 → 视频悬停动态预览 → 分页/类型过滤 →
 * 隐藏/恢复 → 点击应用（真实壁纸层）→ Esc 关闭。
 *
 * 只 mock 了 user_state 读写（避免污染用户真实设置），壁纸扫描/缩略图走真实 8010。
 */

const FIXTURE = resolve(process.cwd(), "..", "_tmp", "wallpaper_e2e");
const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-lib");

test("壁纸库：扫描/分页/动态预览/隐藏/应用", async ({ page }) => {
  test.skip(!existsSync(FIXTURE), `缺少壁纸 fixture：${FIXTURE}`);

  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
  page.on("pageerror", (err) => pageErrors.push(err.message));

  // 设置读写全部拦在浏览器内：不改用户 user_state.json
  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { repos: null, settings: null } });
    } else {
      await route.fulfill({ json: { ok: true } });
    }
  });
  await page.route("**/api/user-state/proxy-status*", (route) => route.fulfill({
    json: {
      listening: false, address: "", source: "none", system_address: "",
      manual_address: "", candidates: [],
    },
  }));
  await page.addInitScript(() => {
    localStorage.setItem("demiurge_settings", JSON.stringify({ theme: "liquid-glass" }));
  });

  await page.goto("/");
  await expect(page.locator(".lg-rope")).toBeVisible({ timeout: 20_000 });
  await page.locator(".lg-rope").click();
  await expect(page.locator(".lg-panel")).toBeVisible();

  await page.getByRole("tab", { name: "壁纸", exact: true }).click();
  await page.getByRole("button", { name: /打开壁纸库/ }).click();
  await expect(page.locator(".lg-lib")).toBeVisible();

  // 未配置本地文件夹时默认落在创意工坊来源，先切回本地文件夹
  await page.getByRole("tab", { name: "本地文件夹" }).click();
  await page.locator("#lg-lib-folder").fill(FIXTURE);
  await page.getByRole("button", { name: /扫描/ }).click();

  const cards = page.locator(".lg-lib-card");
  await expect(cards).toHaveCount(21, { timeout: 30_000 });
  await expect(page.locator(".lg-lib__summary")).toContainText("29 个可播放壁纸");

  const threeCols = await page.locator(".lg-lib__grid").evaluate(
    (el) => getComputedStyle(el).gridTemplateColumns.split(/\s+/).filter(Boolean).length,
  );
  expect(threeCols).toBe(3);

  // 缩略图是浏览器真正解码出来的（不是破图）
  const firstThumb = cards.first().locator("img.lg-lib-card__thumb");
  await expect(firstThumb).toHaveAttribute("src", /wallpapers\/thumb/);
  await page.waitForFunction(() => {
    const img = document.querySelector<HTMLImageElement>(".lg-lib-card img.lg-lib-card__thumb");
    return !!img && img.complete && img.naturalWidth > 0;
  });

  // 分页：29 个 → 21 + 8；视频排在第 2 页
  await page.locator(".lg-lib__pager").getByRole("button", { name: /下一页/ }).click();
  await expect(cards).toHaveCount(8);
  await expect(page.locator(".lg-lib__pager")).toContainText("第 2/2 页");

  // 视频卡：视频角标 + 悬停切换动态预览
  const videoCard = page.locator(".lg-lib-card").filter({ hasText: "clip1" }).first();
  await expect(videoCard.locator(".lg-lib-card__badge")).toContainText("视频");
  await videoCard.hover();
  const live = videoCard.locator("video.lg-lib-card__live");
  await expect(live).toBeVisible();
  await page.waitForFunction(() => {
    const v = document.querySelector<HTMLVideoElement>(".lg-lib-card video.lg-lib-card__live");
    return !!v && v.readyState >= 2 && !v.paused && v.currentTime > 0;
  }, null, { timeout: 20_000 });
  await page.screenshot({ path: resolve(SHOT_DIR, "library-page2.png") });
  await page.locator(".lg-lib__pager").getByRole("button", { name: /上一页/ }).click();
  await expect(cards).toHaveCount(21);
  await page.locator(".lg-lib__filters").getByRole("button", { name: "视频", exact: true }).click();
  await expect(cards).toHaveCount(3);
  await expect(page.locator(".lg-lib__pager")).toContainText("第 1/1 页");

  // 隐藏 → 已隐藏 → 恢复
  await cards.first().hover();
  await cards.first().locator(".lg-lib-card__hide").click();
  await expect(page.getByRole("tab", { name: /已隐藏 \(1\)/ })).toBeVisible();
  await expect(cards).toHaveCount(2);
  await page.getByRole("tab", { name: /已隐藏/ }).click();
  await expect(cards).toHaveCount(1);
  await cards.first().hover();
  await cards.first().locator(".lg-lib-card__hide").click();
  await expect(page.getByRole("tab", { name: /已隐藏 \(0\)/ })).toBeVisible();
  await expect(page.locator(".lg-lib__state")).toContainText("没有隐藏的壁纸");
  await page.getByRole("tab", { name: /正常列表/ }).click();
  await expect(cards).toHaveCount(3);

  // 点击卡片 = 应用：真实壁纸层出现该视频并播放
  await cards.first().click();
  await expect(cards.first()).toHaveClass(/applied/);
  await expect(cards.first().locator(".lg-lib-card__applied")).toContainText("使用中");
  await expect(page.locator(".app-backdrop-media")).toHaveCount(1);
  await page.waitForFunction(() => {
    const v = document.querySelector<HTMLVideoElement>("video.app-backdrop-media");
    return !!v && v.readyState >= 2 && !v.paused;
  }, null, { timeout: 20_000 });
  await page.screenshot({ path: resolve(SHOT_DIR, "library-applied.png") });

  // 名称搜索：防抖后自动回到第一页并只保留匹配项，清空恢复
  await page.locator("#lg-lib-search").fill("clip2");
  await expect(cards).toHaveCount(1, { timeout: 10_000 });
  await expect(page.locator(".lg-lib__summary")).toContainText("「clip2」匹配 1 个");
  await page.getByRole("button", { name: "清空搜索" }).click();
  await expect(cards).toHaveCount(3, { timeout: 10_000 });

  // Esc 先关壁纸库，再关控制台
  await page.keyboard.press("Escape");
  await expect(page.locator(".lg-lib")).toHaveCount(0);
  await expect(page.locator(".lg-panel")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".lg-panel")).toHaveCount(0);

  const relevantErrors = consoleErrors.filter(
    (text) => !text.includes("favicon") && !text.includes("Failed to load resource"),
  );
  expect(pageErrors).toEqual([]);
  expect(relevantErrors).toEqual([]);
});

test.beforeAll(() => {
  mkdirSync(SHOT_DIR, { recursive: true });
});
