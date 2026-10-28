import { test, expect, type Page } from "@playwright/test";

/** 弹窗纳入返回链的真浏览器验证（2026-10-28，承接 #95）。
 *
 *  协议：弹窗打开时在当前视图 hash 后追加 `/@<id>`（lib/overlayRoute.ts），
 *  壳层返回键（= goBack）先弹掉这一段 → 弹窗关闭、底下的页面原样不动。
 *  跑的是移动包产物（`VITE_MOBILE_PROFILE=1`），服务由隔离后端静态挂载：
 *    LAF_FRONTEND_DIST=<repo>/mobile/spike/frontend-mobile-dist LAF_DATA_DIR=<tmp> \
 *    .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18097
 *    npx playwright test --config playwright.e2e-manual.config.ts \
 *      --reporter=list e2e-manual/mobile-modal-backchain.spec.ts
 */
process.env.HTTP_PROXY = "";
process.env.HTTPS_PROXY = "";
process.env.ALL_PROXY = "";
process.env.NO_PROXY = "127.0.0.1,localhost";

const ORIGIN = "http://127.0.0.1:18097";

test.use({ viewport: { width: 390, height: 844 } });

const hash = (page: Page) => page.evaluate(() => window.location.hash);
const tap = (page: Page, text: string) =>
  page.getByText(text, { exact: true }).locator("visible=true").first().click();
/** 等 hash 变成目标值：history.back() 是异步的，直接断言会撞上「还没退完」。 */
const waitHash = (page: Page, expected: string) =>
  page.waitForFunction((want) => window.location.hash === want, expected);

test.beforeEach(async ({ page }) => {
  await page.goto(`${ORIGIN}/`);
  await page.waitForLoadState("networkidle");
});

test("首页预设弹窗：开一格、退一格，弹窗消失而首页原样", async ({ page }) => {
  await expect(hash(page)).resolves.toBe("");

  await tap(page, "预设");
  await expect(page.locator(".modal.preset-modal")).toBeVisible();
  await expect(hash(page)).resolves.toBe("#/@preset");

  await page.goBack();
  await expect(page.locator(".modal.preset-modal")).toHaveCount(0);
  await expect(hash(page)).resolves.toBe("");
  await expect(page.locator(".home-launcher-cards")).toBeVisible();
});

test("叠在资产页上的弹窗：退一格只关弹窗，作品页不动", async ({ page }) => {
  await tap(page, "资产管理");
  await expect(hash(page)).resolves.toBe("#/assets/works");

  await tap(page, "新建仓库");
  const modal = page.locator(".modal").filter({ hasText: "新建仓库" });
  await expect(modal).toBeVisible();
  await expect(hash(page)).resolves.toBe("#/assets/works/@repo-create");

  await page.goBack();
  await expect(modal).toHaveCount(0);
  await expect(hash(page)).resolves.toBe("#/assets/works");
  await expect(page.getByText("生成内容", { exact: true }).locator("visible=true").first()).toBeVisible();
});

test("程序内取消不留幽灵历史格：关掉后再退一格回到首页", async ({ page }) => {
  await tap(page, "资产管理");
  await expect(hash(page)).resolves.toBe("#/assets/works");

  await tap(page, "新建仓库");
  await expect(hash(page)).resolves.toBe("#/assets/works/@repo-create");

  await tap(page, "取消");
  // 关弹窗要自己把那一条历史退掉（不是只改 state 留下 #/.../@repo-create）
  await waitHash(page, "#/assets/works");
  await expect(page.locator(".modal")).toHaveCount(0);

  await page.goBack();
  await expect(hash(page)).resolves.toBe("");
  await expect(page.locator(".home-launcher-cards")).toBeVisible();
});

test("刷新（带弹窗段的 hash 直达）：不复活弹窗，也不留一格空退", async ({ page }) => {
  // 用 index.html + 带弹窗段 hash 触发**整页装载**（同文档只有 hash 变化时不会重新开屏；
  // 这里要验的正是 Desktops 上「弹窗开着按 F5」那条路）。
  await page.goto(`${ORIGIN}/index.html#/@preset`);
  await page.waitForLoadState("networkidle");
  // 弹窗状态不可从 hash 恢复：开屏剥掉弹窗段，落在首页本体
  await expect(page.locator(".modal.preset-modal")).toHaveCount(0);
  await expect(hash(page)).resolves.toBe("");
  await expect(page.locator(".home-launcher-cards")).toBeVisible();
});
