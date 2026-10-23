import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

/**
 * Wallpaper Engine 创意工坊：后端自动发现 Steam 库（431960），壁纸库默认展示；
 * 应用视频/场景预览壁纸后，用「GET 回填最后一次 POST 的 settings」模拟重启，
 * 验证当前壁纸仍能从后端存档恢复。全程 mock user_state 读写，不污染真实设置。
 */

const SHOT = resolve(process.cwd(), "..", "_tmp", "wallpaper-lib", "workshop.png");

type SavedSettings = {
  liquidGlass?: {
    activeWallpaperId?: string;
    wallpapers?: { id: string; path: string }[];
  };
} | null;

function activePathFromLocalStorage(): string {
  const settings = JSON.parse(localStorage.getItem("demiurge_settings") || "{}");
  const lg = settings.liquidGlass || {};
  const active = (lg.wallpapers || []).find(
    (wallpaper: { id: string }) => wallpaper.id === lg.activeWallpaperId,
  );
  return active?.path || "";
}

test("创意工坊壁纸：自动识别 + 应用 + 重启后仍显示", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
  page.on("pageerror", (err) => pageErrors.push(err.message));

  let savedSettings: SavedSettings = null;
  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { repos: null, settings: savedSettings } });
      return;
    }
    const body = route.request().postDataJSON() as { settings?: SavedSettings } | null;
    savedSettings = body?.settings ?? null;
    await route.fulfill({ json: { ok: true } });
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
  await page.getByRole("tab", { name: "壁纸", exact: true }).click();

  const librarySection = page.locator(".lg-section", { hasText: "壁纸库" });
  await expect(librarySection).toContainText(/Wallpaper Engine：已识别 \d+ 个壁纸/, { timeout: 60_000 });
  await librarySection.getByRole("button", { name: "打开壁纸库" }).click();

  const dialog = page.getByRole("dialog", { name: "液态玻璃壁纸库" });
  await expect(dialog).toBeVisible();
  // 本地未配置文件夹时默认展示创意工坊
  await expect(dialog.getByRole("tab", { name: /Wallpaper Engine/ }))
    .toHaveAttribute("aria-selected", "true");
  await expect(dialog).toContainText("已自动识别 Steam 库中的创意工坊壁纸");
  await expect(dialog.locator(".lg-lib-card").first()).toBeVisible({ timeout: 60_000 });
  await page.waitForFunction(() => {
    const img = document.querySelector<HTMLImageElement>(".lg-lib-card__thumb");
    return !!img && img.complete && img.naturalWidth > 0;
  }, { timeout: 60_000 });

  // 视频作品：应用真正的视频文件
  await dialog.getByRole("button", { name: "视频", exact: true }).click();
  await expect(dialog.locator(".lg-lib-card").first()).toBeVisible({ timeout: 60_000 });
  await dialog.locator(".lg-lib-card").first().click();
  await expect(dialog).toContainText("使用中");

  const media = page.locator(".app-backdrop-media");
  await expect(media).toBeVisible();
  expect(await media.evaluate((el) => el.tagName.toLowerCase())).toBe("video");
  await page.waitForFunction(() => {
    const video = document.querySelector<HTMLVideoElement>("video.app-backdrop-media");
    return !!video && video.readyState >= 2;
  }, { timeout: 60_000 });
  const appliedVideo = await page.evaluate(activePathFromLocalStorage);
  expect(appliedVideo).toMatch(/431960.*\.mp4$/i);

  // 场景作品带 scene.pkg ⇒ 卡片标「实时渲染」，不再谎称「静态预览」（用户实锤的「没效果/发糊」正靠这条链路解决）
  await dialog.getByRole("button", { name: "图片", exact: true }).click();
  await expect(dialog.locator(".lg-lib-card", { hasText: "实时渲染" }).first())
    .toBeVisible({ timeout: 60_000 });

  // 网页作品浏览器渲染不了 ⇒ 仍标「静态预览」，按工坊 id 搜索锁定一张确定存在的条目（防抖后必须真的过滤）
  await dialog.getByLabel("搜索壁纸名称").fill("3683176053");
  await expect(dialog).toContainText("「3683176053」匹配 1 个", { timeout: 60_000 });
  const previewCard = dialog.locator(".lg-lib-card").first();
  await expect(previewCard).toContainText("静态预览");
  await expect(previewCard).toBeVisible();
  await previewCard.click();
  await expect(media).toBeVisible();
  expect(await media.evaluate((el) => el.tagName.toLowerCase())).toBe("img");
  await page.waitForFunction(() => {
    const img = document.querySelector<HTMLImageElement>("img.app-backdrop-media");
    return !!img && img.complete && img.naturalWidth > 0;
  }, { timeout: 60_000 });
  const appliedPreview = await page.evaluate(activePathFromLocalStorage);
  expect(appliedPreview).toMatch(/431960.*(preview|\.(png|jpe?g|gif|webp))$/i);

  await page.screenshot({ path: SHOT });

  // 等「场景预览」这最后一次设置真正回写后端（只看 activeWallpaperId 非空会读到上一张的旧存档）
  await expect.poll(() => {
    const lg = savedSettings?.liquidGlass;
    return lg?.wallpapers?.find((item) => item.id === lg.activeWallpaperId)?.path || "";
  }, { timeout: 20_000 }).toBe(appliedPreview);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await page.reload();

  await expect(page.locator(".app-backdrop-media")).toBeVisible({ timeout: 30_000 });
  await page.waitForFunction(() => {
    const el = document.querySelector<HTMLImageElement | HTMLVideoElement>(".app-backdrop-media");
    if (!el) return false;
    return el.tagName === "IMG"
      ? (el as HTMLImageElement).complete && (el as HTMLImageElement).naturalWidth > 0
      : (el as HTMLVideoElement).readyState >= 2;
  }, { timeout: 60_000 });
  expect(await page.evaluate(activePathFromLocalStorage)).toBe(appliedPreview);
  expect(savedSettings?.liquidGlass?.wallpapers?.some((item) => item.path === appliedPreview)).toBe(true);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter((line) => !line.includes("favicon"))).toEqual([]);
});
