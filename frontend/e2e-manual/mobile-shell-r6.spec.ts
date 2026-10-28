import { expect, test, type Page } from "@playwright/test";

/** 手机壳层第三轮（2026-10-27 用户四项反馈）真浏览器验证。
 *
 *  真实 vite(5173, VITE_MOBILE_PROFILE=1) + 真实后端（隔离端口），只读：
 *  所有写路径（POST/PUT/PATCH/DELETE）在 route 层拦下，绝不改用户数据。
 *  视口 390×844 = 手机竖屏。
 */
test.use({ viewport: { width: 390, height: 844 } });

const SHOTS = "../_tmp/mobile-shell-r6";

// 设置页里的是 SkillsBrowser（技能市场），打 /api/skills/smithery/search，返回 skills
const SMITHERY_STUB = {
  ok: true,
  skills: [
    {
      namespace: "demo", slug: "one", displayName: "示例技能一",
      description: "用于验证手机版式的示例卡片一", verified: true,
      totalActivations: 1200, servers: ["exa"],
    },
    {
      namespace: "demo", slug: "two", displayName: "示例技能二",
      description: "用于验证手机版式的示例卡片二", verified: false,
      totalActivations: 30, servers: [],
    },
  ],
  pagination: { totalCount: 2 },
  error: "",
};

async function guardWrites(page: Page) {
  await page.route("**/api/**", async (route) => {
    const method = route.request().method();
    if (method === "GET" || method === "HEAD" || method === "OPTIONS") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }),
    });
  });
}

async function openSettings(page: Page) {
  await page.goto("/");
  // 启动期 user-state 回填/并发会话的 HMR 可能把这次点击冲掉：以「设置页真的出现」为准重试
  await expect(async () => {
    if (!(await page.locator(".settings-page").isVisible().catch(() => false))) {
      await page.locator(".nav-settings").click({ timeout: 5_000 }).catch(() => {});
    }
    await expect(page.locator(".settings-page")).toBeVisible({ timeout: 2_500 });
  }).toPass({ timeout: 30_000 });
}

test("问题4+3：设置页去掉顶部「设置」标题条、栏目压成 5×2，电脑地址不预填 IP", async ({ page }) => {
  await guardWrites(page);
  await openSettings(page);

  // 顶栏那条「设置」整条隐藏（底栏设置按钮已表明位置）
  await expect(page.locator("header.app-topbar.topbar-settings")).toBeHidden();

  // 10 个栏目 5 列 × 2 行，全部在首屏内，且不再占掉上半屏
  const items = page.locator(".settings-nav-item");
  await expect(items).toHaveCount(10);
  const nav = await page.locator(".settings-nav").boundingBox();
  expect(nav).toBeTruthy();
  expect(nav!.height, "栏目条高度").toBeLessThan(140);
  const boxes = await items.evaluateAll((els) =>
    els.map((el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, b: r.bottom }; }));
  expect(new Set(boxes.map((b) => Math.round(b.y))).size, "只应有两行").toBe(2);
  for (const b of boxes) {
    expect(b.w).toBeGreaterThan(50);
    expect(b.b).toBeLessThan(200);
    expect(b.x).toBeGreaterThanOrEqual(0);
  }
  await page.screenshot({ path: `${SHOTS}/r6-01-settings-compact.png` });

  // 问题3：联机同库的电脑地址不得带默认 IP
  await page.locator(".settings-nav-item", { hasText: "离线资源同步" }).click();
  await expect(page.getByText("联机同库", { exact: true })).toBeVisible({ timeout: 15_000 });
  const urlInput = page.locator('input[inputmode="url"]');
  await expect(urlInput).toBeVisible();
  await expect(urlInput).toHaveAttribute("placeholder", "http://电脑IP:8010");
  expect(await page.locator(".settings-page").innerHTML()).not.toContain("192.168");
  await page.screenshot({ path: `${SHOTS}/r6-02-sync-no-ip.png` });
});

test("问题2：Smithery 市场在手机上是上下两段（筛选横滑 chips + 全宽结果）", async ({ page }) => {
  await guardWrites(page);
  await page.route("**/api/skills/smithery/search**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SMITHERY_STUB) }));
  await openSettings(page);

  await page.locator(".settings-nav-item", { hasText: "技能扩展" }).click();
  await page.getByRole("button", { name: /Smithery 市场/ }).click();
  const modal = page.locator(".smithery-modal");
  await expect(modal).toBeVisible({ timeout: 15_000 });

  const filters = await page.locator(".smithery-filters").boundingBox();
  const results = await page.locator(".smithery-results").boundingBox();
  expect(filters, "筛选区").toBeTruthy();
  expect(results, "结果区").toBeTruthy();
  // 上下堆叠：筛选在上、结果紧接着在下面，两段都吃满宽度（不是电脑那套左右两栏）
  expect(filters!.y).toBeLessThan(results!.y);
  expect(results!.y - (filters!.y + filters!.height)).toBeLessThan(3);
  expect(filters!.width).toBeGreaterThan(330);
  expect(Math.abs(filters!.width - results!.width)).toBeLessThan(6);

  // 结果卡真的渲染出来（不是卡在「搜索中…」）
  await expect(page.locator(".smithery-card")).toHaveCount(2);
  await expect(page.locator(".smithery-card").first()).toBeVisible();
  expect(await page.locator(".smithery-filters").evaluate((el) => el.scrollWidth > el.clientWidth)).toBeTruthy();
  await page.screenshot({ path: `${SHOTS}/r6-03-smithery-mobile.png` });
});

test("问题1：对话页「…」点开就能看到隐藏的工具（反证：去掉定位就落到屏幕外）", async ({ page }) => {
  await guardWrites(page);
  await page.goto("/");

  // 进创作页；用户状态里若已有上次选中作品会直接进对话
  const head = page.locator(".chat-view-head");
  await expect(async () => {
    if (!(await head.isVisible().catch(() => false))) {
      const selects = page.locator(".app-topbar select");
      if (await selects.count() >= 2) {
        await selects.nth(0).selectOption({ index: 1 }).catch(() => {});
        await page.waitForTimeout(400);
        await selects.nth(1).selectOption({ index: 1 }).catch(() => {});
      } else {
        await page.locator(".nav-sections .nav-item", { hasText: "创作平台" })
          .click({ timeout: 5_000 }).catch(() => {});
      }
    }
    await expect(head).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 40_000 });

  const toggle = page.locator(".chat-view-head .head-tools-toggle");
  await expect(toggle).toBeVisible();
  const panel = page.locator(".chat-view-head .head-tools.open");

  // 反证（红）：把头部的定位拿掉 = 修复前的状态，浮层就摆到视口下缘之外
  await page.evaluate(() => {
    const el = document.querySelector(".chat-view-head") as HTMLElement | null;
    if (el) el.style.position = "static";
  });
  await toggle.click();
  await expect(panel).toBeAttached();
  const broken = await panel.boundingBox();
  expect(broken, "修复前浮层框").toBeTruthy();
  expect(broken!.y, "修复前应落到屏幕外").toBeGreaterThanOrEqual(844);
  await toggle.click();
  await expect(panel).toBeHidden();

  // 修复后（绿）：浮层贴着功能栏下沿，完整落在视口里
  await page.evaluate(() => {
    const el = document.querySelector(".chat-view-head") as HTMLElement | null;
    if (el) el.style.position = "";
  });
  await toggle.click();
  await expect(panel).toBeVisible();
  const fixed = await panel.boundingBox();
  expect(fixed, "修复后浮层框").toBeTruthy();
  expect(fixed!.y).toBeGreaterThan(0);
  expect(fixed!.height).toBeGreaterThan(40);
  expect(fixed!.y + fixed!.height).toBeLessThanOrEqual(845);
  // 隐藏的工具确实在里面（导出会话是最靠右的几个之一）
  await expect(panel.locator('button[title^="导出会话"]')).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/r6-04-head-tools-open.png` });
});

test("问题8：生成内容页只有工具条自己横滑，图区和整页都不跟着拖", async ({ page }) => {
  // 自足夹具：不碰真实后端（用户状态 + 32/页 × 11 页，复现真机「第 3 / 11 页」的吸底分页条）
  const PNG = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFklEQVQI12P8z8Dwn4EIwESMokGoEAAAqAcD/4y1mBQAAAAASUVORK5CYII=",
    "base64");
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/api/user-state") {
      return json({
        repos: [{ id: "r1", name: "探针仓库", createdAt: 1761400000000, lastUsedAt: 1761500000000 }],
        settings: { outputDir: "D:/x", chatModels: [], imageModels: [], userPersonas: [], liquidGlass: {} },
        selection: null,
      });
    }
    if (p === "/api/rag/generations") {
      const items = Array.from({ length: 32 }, (_, i) => ({
        id: `gen-${i}`, repo_id: "r1", prompt: `probe ${i}`,
        image_url: `/api/comfyui/local-view?path=${encodeURIComponent(`D:/x/${i}.png`)}`,
        tags: ["a"], created_at: 1761500000000 - i * 1000, mediaType: "image",
      }));
      return json({ items, has_more: true, total: 352 });
    }
    if (p === "/api/comfyui/local-view") {
      return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    }
    return json({ ok: true });
  });

  await page.goto("/#/assets/generations");
  await expect(page.locator(".page-toolbar.asset-toolbar")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".pager-dock")).toBeVisible();

  // 反证基线：修复前同一页面 `.app-body` scrollWidth 432 / clientWidth 390（横滑 42px）
  const metrics = await page.evaluate(() => {
    const body = document.querySelector(".app-body") as HTMLElement;
    const toolbar = document.querySelector(".page-toolbar.asset-toolbar") as HTMLElement;
    const dock = document.querySelector(".pager-dock") as HTMLElement;
    const tRect = toolbar.getBoundingClientRect();
    return {
      bodySW: body.scrollWidth, bodyCW: body.clientWidth,
      toolbarSW: toolbar.scrollWidth, toolbarCW: toolbar.clientWidth,
      toolbarRight: Math.round(tRect.right),
      dockSW: dock.scrollWidth, dockCW: dock.clientWidth,
    };
  });
  expect(metrics.bodySW, "整页不得有横向滚动").toBe(metrics.bodyCW);
  // 工具条自己仍然可横滑（用户要的「横滑看多个功能」），且不越过视口
  expect(metrics.toolbarSW).toBeGreaterThan(metrics.toolbarCW);
  expect(metrics.toolbarRight).toBeLessThanOrEqual(390);
  // 分页条在 390px 下整条放得下（手机上收掉了页码输入框）
  expect(metrics.dockSW, "分页条 390px 下应正好放下").toBe(metrics.dockCW);

  // 强行把整页横推到 200：必须回弹到 0（页面不可横滚）
  const scrollLeft = await page.evaluate(() => {
    const body = document.querySelector(".app-body") as HTMLElement;
    body.scrollLeft = 200;
    return body.scrollLeft;
  });
  expect(scrollLeft, "整页不可横滚").toBe(0);
  await page.screenshot({ path: `${SHOTS}/r6-05-assets-no-page-scroll.png` });
});
