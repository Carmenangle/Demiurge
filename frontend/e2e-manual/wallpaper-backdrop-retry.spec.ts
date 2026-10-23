import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

/**
 * 真实 vite + 真实后端：
 * 1. 当前壁纸是创意工坊长视频时，首次 local-view 故意 500 —— 背景层必须自动重试并真正播放；
 * 2. 低清预览（scene 192×192）在壁纸列表里标「低清预览」。
 * 设置读写全程 mock，不污染用户真实壁纸。
 */

const SHOT_DIR = resolve(process.cwd(), "..", "_tmp", "wallpaper-lib");
const SHIZURU = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\2849228609\公主连结-静流SHIZURU.mp4`;
const SCENE_PREVIEW = String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\3483546335\preview.gif`;

const SETTINGS = {
  theme: "liquid-glass",
  liquidGlass: {
    wallpapers: [
      {
        id: "shizuru",
        path: SHIZURU,
        name: "[R18]静流Shizuru-公主连结",
        kind: "video",
        preview: String.raw`D:\SteamLibrary\steamapps\workshop\content\431960\2849228609\preview.gif`,
      },
      {
        id: "scene",
        path: SCENE_PREVIEW,
        name: "UOMI（低清预览）",
        kind: "image",
        lowRes: true,
      },
    ],
    activeWallpaperId: "shizuru",
  },
};

test("视频壁纸首次加载失败会自动重试并播放；低清预览有标注", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));

  await page.route("**/api/user-state", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { repos: null, settings: SETTINGS } });
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

  // 第一次视频请求失败，后续放行到真实后端 —— 验证失败一次不会永久回落极光
  let videoRequests = 0;
  await page.route("**/api/comfyui/local-view*", async (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") || "";
    if (path.toLowerCase().endsWith(".mp4")) {
      videoRequests += 1;
      if (videoRequests === 1) {
        await route.fulfill({ status: 500, body: "forced failure" });
        return;
      }
    }
    await route.continue();
  });

  await page.goto("/");
  await expect(page.locator(".app-backdrop-media")).toBeVisible({ timeout: 20_000 });
  await page.waitForFunction(() => {
    const video = document.querySelector<HTMLVideoElement>("video.app-backdrop-media");
    return !!video && video.readyState >= 2 && !video.paused && video.currentTime > 0;
  }, null, { timeout: 45_000 });
  expect(videoRequests).toBeGreaterThanOrEqual(2);

  // 打开控制台壁纸页：低清预览有明确标注，封面不是黑块
  await page.locator(".lg-rope").click();
  await page.getByRole("tab", { name: "壁纸", exact: true }).click();
  const lowResRow = page.locator(".lg-wallpaper").filter({ hasText: "UOMI" }).first();
  await expect(lowResRow).toContainText("低清预览", { timeout: 20_000 });
  await page.waitForFunction(() => {
    const img = document.querySelector<HTMLImageElement>(".lg-wallpaper img.lg-thumb");
    return !!img && img.complete && img.naturalWidth > 0;
  }, null, { timeout: 20_000 });

  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SHOT_DIR, "backdrop-retry.png") });
  expect(pageErrors).toEqual([]);
});
