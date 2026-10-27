import { test, expect, type Page } from "@playwright/test";

/** 手机端「返回链」真浏览器验证（2026-10-27 用户报障：返回是直接退出而不是返回上一页）。
 *
 *  跑的是 **APK 里那份** 移动包（`VITE_MOBILE_PROFILE=1` 构建，由隔离后端静态挂载），
 *  不是 vite dev —— 这样「点导航写 hash」与「历史事件回改状态」两端都在真实产物里。
 *  Android 返回键在 WebView 里等价于 `goBack()`，故用 `page.goBack()` 复现同一动作。
 *
 *  跑法（空 `LAF_DATA_DIR`，绝不碰用户实例 8010）：
 *    LAF_FRONTEND_DIST=<repo>/mobile/spike/frontend-mobile-dist LAF_DATA_DIR=<tmp> \
 *    .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18097
 *    npx playwright test --config playwright.e2e-manual.config.ts \
 *      --reporter=list e2e-manual/mobile-return-chain.spec.ts
 */
process.env.HTTP_PROXY = "";
process.env.HTTPS_PROXY = "";
process.env.ALL_PROXY = "";
process.env.NO_PROXY = "127.0.0.1,localhost";

const ORIGIN = "http://127.0.0.1:18097";

test.use({ viewport: { width: 390, height: 844 } });

const hash = (page: Page) => page.evaluate(() => window.location.hash);
/** 只点可见者：窄屏下同一文案可能有隐藏副本（桌面侧栏），strict mode 会自己撞墙。 */
const tap = (page: Page, text: string) =>
  page.getByText(text, { exact: true }).locator("visible=true").first().click();
const visibleCount = (page: Page, text: string) =>
  page.getByText(text, { exact: true }).locator("visible=true").count();

test.beforeEach(async ({ page }) => {
  await page.goto(`${ORIGIN}/`);
  await page.waitForLoadState("networkidle");
});

test("主导航进得去也退得回：创作平台 → 资产管理 → 角色卡 → 逐格退回", async ({ page }) => {
  await expect(hash(page)).resolves.toBe("");
  await expect(page.locator(".home-launcher-cards")).toBeVisible();

  await tap(page, "资产管理");
  await expect(hash(page)).resolves.toBe("#/assets/works");
  await expect(page.getByText("生成内容", { exact: true }).locator("visible=true").first()).toBeVisible();

  await tap(page, "角色卡");
  await expect(hash(page)).resolves.toBe("#/assets/character-cards");

  await page.goBack();
  await expect(hash(page)).resolves.toBe("#/assets/works");

  await page.goBack();
  await expect(hash(page)).resolves.toBe("");
  // 退回的是首页工作台本体，不是黑屏、也不是仍停在资产区
  await expect(page.locator(".home-launcher-cards")).toBeVisible();
  await expect(visibleCount(page, "生成内容")).resolves.toBe(0);
});

test("设置是链上的一格：打开 → 换小节 → 逐格退回并关闭", async ({ page }) => {
  await page.locator(".nav-settings").locator("visible=true").first().click();
  await expect(hash(page)).resolves.toBe("#/settings");
  await expect(page.locator(".settings-page")).toBeVisible();

  await tap(page, "路径与代理");
  await expect(hash(page)).resolves.toBe("#/settings/paths");

  await page.goBack();
  await expect(hash(page)).resolves.toBe("#/settings");
  await expect(page.locator(".settings-page")).toBeVisible();

  await page.goBack();
  await expect(hash(page)).resolves.toBe("");
  await expect(page.locator(".home-launcher-cards")).toBeVisible();
  await expect(page.locator(".settings-page")).toHaveCount(0);
});

test("两级下钻各占一格：作品列表 → 某仓库 → 返回先退仓库再退列表", async ({ page }) => {
  // 空库没有真仓库可点，用同文档 hash 跳转复现「点进去」写的历史位（与点击等价）
  await tap(page, "资产管理");
  await expect(hash(page)).resolves.toBe("#/assets/works");
  await page.goto(`${ORIGIN}/#/assets/works/repo/demo-repo`);
  await expect(hash(page)).resolves.toBe("#/assets/works/repo/demo-repo");

  await page.goBack();
  await expect(hash(page)).resolves.toBe("#/assets/works");

  await page.goBack();
  await expect(hash(page)).resolves.toBe("");
  await expect(page.locator(".home-launcher-cards")).toBeVisible();
});

test("深链直达 + 刷新停在当前视图（重载不回首页，hash 不被 _r 挤掉）", async ({ page }) => {
  await page.goto(`${ORIGIN}/#/assets/generations`);
  await page.waitForLoadState("networkidle");
  await expect(hash(page)).resolves.toBe("#/assets/generations");

  // 带 _r 的 cache-busted 重载（staleChunk 走的那条路）也必须按 hash 落回原视图
  await page.goto(`${ORIGIN}/index.html?_r=1#/assets/generations`);
  await page.waitForLoadState("networkidle");
  await expect(hash(page)).resolves.toBe("#/assets/generations");
});
