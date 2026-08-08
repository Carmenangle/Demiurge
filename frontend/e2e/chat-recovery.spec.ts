import { expect, test, type Page } from "@playwright/test";

async function selectWork(page: Page) {
  const pickers = page.locator(".repo-picker select");
  await pickers.nth(0).selectOption("library");
  await pickers.nth(1).selectOption("work");
  await expect(page.locator('[data-message-id="assistant-scene"]')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("laf_pending_gen_work", JSON.stringify([{
      prompt_id: "mock-prompt", createdAt: Date.now(), outputNodeIds: ["save"],
      prompt: "mock artistic prompt",
      target: { messageId: "assistant-scene", slotId: "slot-1", mediaType: "image", background: true },
    }]));
  });
  await page.goto("/#/story");
});

test("recovers the conversation snapshot after a browser refresh", async ({ page }) => {
  await selectWork(page);
  await expect(page.getByText("高潮段落之后应当原位显示插画。")).toBeVisible();
  await page.reload();
  await selectWork(page);
  await expect(page.getByText("这是图片之后的收束段落。")).toBeVisible();
});

test("keeps background generation visible across refresh", async ({ page }) => {
  await page.getByTitle("后台活动（拖动可移动，长按隐藏）").click();
  await expect(page.getByText("出图中")).toBeVisible();
  await page.reload();
  await page.getByTitle("后台活动（拖动可移动，长按隐藏）").click();
  await expect(page.getByText("恢复测试作品")).toBeVisible();
});

test("replaces the mocked ComfyUI slot inside the original message", async ({ page }) => {
  await selectWork(page);
  const message = page.locator('[data-message-id="assistant-scene"]');
  await expect(message.locator('img[src="http://127.0.0.1:18110/mock.png"]')).toBeVisible();
  await expect(page.locator('[data-message-id="assistant-scene"]')).toHaveCount(1);
  await expect(message.getByText("这是图片之后的收束段落。")).toBeVisible();
});
