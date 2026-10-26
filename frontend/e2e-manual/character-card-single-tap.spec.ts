import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

/**
 * 角色卡封面「单击即开」：真实 vite(5173) + 真实后端(8010)，只读链路。
 *
 * 缺陷来源：封面曾绑 onDoubleClick，手机上「单点一次/慢慢点两次」都没反应，要快速点两次才进。
 * 本用例只发**一次** click()，断言预览弹窗出现——Playwright 的单次点击与触屏单击同语义
 * （浏览器不会把它合成 dblclick）。全程不写入：只走列表与卡详情读取，看完即关。
 */

const SHOT = resolve(process.cwd(), "..", "_tmp", "mobile-sync-test", "tap-01-single-click-preview.png");
const SHOT_CLOSED = resolve(process.cwd(), "..", "_tmp", "mobile-sync-test", "tap-02-after-close.png");

test("单击角色卡封面即打开预览弹窗（不需要双击）", async ({ page }) => {
  await page.goto("/");
  // 走真实导航：资产管理 → 角色卡（hash 直达会被首屏视图状态覆盖，不如点导航可靠）
  await page.getByRole("button", { name: "资产管理" }).click();
  await page.getByRole("button", { name: "角色卡" }).click();
  const cover = page.locator('.repo-cover[title="点击预览角色卡"]').first();
  await expect(cover).toBeVisible({ timeout: 30_000 });

  await cover.click();

  const modal = page.locator(".modal-mask .modal");
  await expect(modal).toBeVisible({ timeout: 10_000 });
  const title = (await modal.locator("h3").first().textContent())?.trim() || "";
  expect(title.length).toBeGreaterThan(0);
  await page.screenshot({ path: SHOT });

  await modal.locator('button:has-text("关闭")').click();
  await expect(page.locator(".modal-mask")).toHaveCount(0, { timeout: 10_000 });
  await page.screenshot({ path: SHOT_CLOSED });
});
