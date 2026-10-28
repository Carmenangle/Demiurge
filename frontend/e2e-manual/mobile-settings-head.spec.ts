import { expect, test, type Page } from "@playwright/test";

/** 手机「设置面板头行」真浏览器验证（2026-10-27 问题9）。
 *
 *  用户真机截图：技能扩展标题被挤成竖排，Smithery 市场 / 新建 / 保存 三个按钮逐字折行。
 *  这里自足夹具（全部 /api/ 打桩，不依赖后端），视口 390×844 复现手机竖屏。
 */
test.use({ viewport: { width: 390, height: 844 } });

const SHOTS = "../_tmp/mobile-settings-head";

/** 只认 pathname 前缀：通配 **\/api\/** 会把 vite 的 /src/api/client.ts 也拦掉（页面白屏）。 */
async function stubApi(page: Page) {
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/api/user-state") {
      return json({
        repos: [], settings: { outputDir: "D:/x", chatModels: [], imageModels: [], userPersonas: [], liquidGlass: {} },
        selection: null,
      });
    }
    if (p === "/api/skills" || p === "/api/mcp") return json([]);
    return json({ ok: true });
  });
}

async function openSettings(page: Page) {
  await page.goto("/");
  await expect(async () => {
    if (!(await page.locator(".settings-page").isVisible().catch(() => false))) {
      await page.locator(".nav-settings").click({ timeout: 5_000 }).catch(() => {});
    }
    await expect(page.locator(".settings-page")).toBeVisible({ timeout: 2_500 });
  }).toPass({ timeout: 30_000 });
}

/** 头部行度量：标题/按钮的文字各占几行（Range 逐文本节点取矩形，折行 = 多个 top）。 */
async function measureHead(page: Page) {
  return page.evaluate(() => {
    const lineTops = (el: Element) => {
      const tops = new Set<number>();
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        if (!walker.currentNode.textContent?.trim()) continue;
        const r = document.createRange();
        r.selectNodeContents(walker.currentNode);
        for (const rect of r.getClientRects()) tops.add(Math.round(rect.top));
      }
      return tops.size;
    };
    const box = (el: Element) => {
      const r = el.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), right: Math.round(r.right), lines: lineTops(el) };
    };
    const head = document.querySelector(".settings-section .settings-actions-head");
    if (!head) return null;
    const body = document.querySelector(".app-body") as HTMLElement;
    return {
      title: box(head.querySelector("h4, label") as Element),
      buttons: Array.from(head.querySelectorAll("button")).map((b) => ({
        text: b.textContent?.trim() ?? "", ...box(b),
      })),
      bodySW: body.scrollWidth,
      bodyCW: body.clientWidth,
    };
  });
}

async function checkHead(page: Page, section: string) {
  await page.locator(".settings-nav-item", { hasText: section }).click();
  await expect(page.locator(".settings-section .settings-actions-head").first()).toBeVisible({ timeout: 10_000 });
  const m = await measureHead(page);
  expect(m, `${section} 头行`).toBeTruthy();
  expect(m!.title.lines, `${section} 标题应单行`).toBe(1);
  expect(m!.title.w, `${section} 标题宽度`).toBeGreaterThan(40);
  expect(m!.buttons.length, `${section} 按钮数`).toBeGreaterThan(0);
  for (const b of m!.buttons) {
    expect(b.lines, `${section}「${b.text}」文案不应折行`).toBe(1);
    expect(b.h, `${section}「${b.text}」高度`).toBeLessThanOrEqual(48);
    expect(b.right, `${section}「${b.text}」不得越过屏幕`).toBeLessThanOrEqual(390);
  }
  // 头行不把整页撑宽（与问题8 同一口径）
  expect(m!.bodySW, `${section} 整页不得横滚`).toBe(m!.bodyCW);
}

test("问题9：技能扩展头行——标题单行、三个按钮各单行且都在屏内", async ({ page }) => {
  await stubApi(page);
  await openSettings(page);
  await checkHead(page, "技能扩展");
  await page.screenshot({ path: `${SHOTS}/skills-head.png` });
});

test("问题9：MCP 服务器头行同口径（同一类头行的另一处）", async ({ page }) => {
  await stubApi(page);
  await openSettings(page);
  await checkHead(page, "MCP 服务器");
  await page.screenshot({ path: `${SHOTS}/mcp-head.png` });
});
