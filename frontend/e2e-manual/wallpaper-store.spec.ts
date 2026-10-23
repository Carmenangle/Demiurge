import { expect, test } from "@playwright/test";
import {
  existsSync, mkdirSync, readdirSync, rmdirSync, rmSync, writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";

/**
 * 自定义壁纸（存储位置 / 迁移 / 选择文件 / 已上传计数）：
 * 真实 vite + 真实后端，只 mock user_state 读写，避免污染用户设置。
 */

const ROOT = resolve(process.cwd(), "..", "_tmp", "wallpaper_store_e2e");
const OLD_DIR = join(ROOT, "old");
const NEW_DIR = join(ROOT, "new");
const UPLOAD_DIR = join(ROOT, "uploads");
const OLD_FILE = join(OLD_DIR, "旧壁纸.png");
const PICK_FILE = join(UPLOAD_DIR, "星夜.png");
const BAD_FILE = join(UPLOAD_DIR, "坏格式.webm");
const SHOT = resolve(process.cwd(), "..", "_tmp", "wallpaper-lib", "store-section.png");

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

function cleanDir(dir: string) {
  try {
    for (const name of readdirSync(dir)) rmSync(join(dir, name), { force: true });
  } catch { /* 目录不存在 */ }
  try { rmdirSync(dir); } catch { /* 非空或不存在 */ }
}

test.beforeAll(() => {
  cleanDir(OLD_DIR);
  cleanDir(NEW_DIR);
  cleanDir(UPLOAD_DIR);
  mkdirSync(OLD_DIR, { recursive: true });
  mkdirSync(NEW_DIR, { recursive: true });
  mkdirSync(UPLOAD_DIR, { recursive: true });
  writeFileSync(OLD_FILE, PNG);
  writeFileSync(PICK_FILE, PNG);
  writeFileSync(BAD_FILE, Buffer.from("not a video"));
});

test.afterAll(() => {
  cleanDir(OLD_DIR);
  cleanDir(NEW_DIR);
  cleanDir(UPLOAD_DIR);
  try { rmdirSync(ROOT); } catch { /* 保留非空目录 */ }
});

test("自定义壁纸：改存储位置迁移文件、选择文件上传、格式限制", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
  page.on("pageerror", (err) => pageErrors.push(err.message));

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
  await page.addInitScript(({ oldFile, storeDir }) => {
    localStorage.setItem("demiurge_settings", JSON.stringify({
      theme: "liquid-glass",
      liquidGlass: {
        wallpaperStoreDir: storeDir,
        wallpapers: [{ id: "old-wall", path: oldFile, name: "旧壁纸", kind: "image" }],
        activeWallpaperId: "old-wall",
      },
    }));
  }, { oldFile: OLD_FILE, storeDir: OLD_DIR });

  await page.goto("/");
  await expect(page.locator(".lg-rope")).toBeVisible({ timeout: 20_000 });
  await page.locator(".lg-rope").click();
  await page.getByRole("tab", { name: "壁纸", exact: true }).click();

  const store = page.locator(".lg-section", { hasText: "自定义壁纸" });
  await expect(store).toBeVisible();
  await expect(store.locator(".lg-store__path")).toHaveText(/wallpaper_store_e2e[\\/]old$/i, { timeout: 20_000 });
  await expect(store).toContainText("已上传 1 个 · 格式仅限 JPG / PNG / MP4");
  await expect(store).toContainText("未选择文件");
  await expect(store).toContainText("已有文件会迁移到新位置");

  // 更改存储位置：旧文件迁移到新目录，列表路径同步改写
  await store.getByRole("button", { name: "更改" }).click();
  const input = store.locator(".lg-store__input");
  await expect(input).toHaveValue(OLD_DIR);
  await input.fill(NEW_DIR);
  await store.getByRole("button", { name: "保存" }).click();
  await expect(store.locator(".lg-store__path")).toHaveText(/wallpaper_store_e2e[\\/]new$/i, { timeout: 20_000 });
  await expect(store).toContainText("已上传 1 个");
  expect(existsSync(join(NEW_DIR, "旧壁纸.png"))).toBe(true);
  expect(existsSync(OLD_FILE)).toBe(false);
  await page.waitForFunction(() => {
    const img = document.querySelector<HTMLImageElement>(".lg-current .lg-thumb");
    return !!img && img.complete && img.naturalWidth > 0;
  });

  // 选择文件：上传进新存储位置、计数 +1、自动应用
  await store.locator('input[type="file"]').setInputFiles(PICK_FILE);
  await expect(store).toContainText("已上传 2 个", { timeout: 20_000 });
  await expect(store.locator(".lg-store__value")).toHaveText("星夜.png");
  await expect(page.locator(".lg-current__meta strong")).toHaveText("星夜.png");
  await expect(page.locator(".app-backdrop-media")).toBeVisible();
  await page.waitForFunction(() => {
    const img = document.querySelector<HTMLImageElement>(".app-backdrop-media");
    return !!img && img.complete && img.naturalWidth > 0;
  });
  expect(existsSync(join(NEW_DIR, "星夜.png"))).toBe(true);

  // 格式限制：后端拒绝 webm，计数不变并给出可读错误
  await store.locator('input[type="file"]').setInputFiles(BAD_FILE);
  await expect(store.locator(".lg-error")).toContainText("格式仅限 JPG / PNG / MP4", { timeout: 20_000 });
  await expect(store).toContainText("已上传 2 个");

  await page.screenshot({ path: SHOT });

  expect(pageErrors).toEqual([]);
  expect(consoleErrors.filter(
    (line) => !line.includes("favicon") && !line.includes("400 (Bad Request)"),
  )).toEqual([]);
});
