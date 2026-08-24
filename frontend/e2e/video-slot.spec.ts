import { expect, test, type Page } from "@playwright/test";

// V1.3 视频槽全链路：独立文件（不带文件级 beforeEach 的 addInitScript，
// 避免 Playwright 的 LIFO 注册顺序导致图片 pending 覆盖视频 pending）。
async function selectWork(page: Page) {
  const pickers = page.locator(".repo-picker select");
  await pickers.nth(0).selectOption("library");
  await pickers.nth(1).selectOption("work");
  await expect(page.locator('[data-message-id="assistant-scene"]')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("laf_pending_gen_work", JSON.stringify([{
      prompt_id: "mock-video-prompt", createdAt: Date.now(), outputNodeIds: ["save"],
      prompt: "mock video prompt", mediaType: "video",
      target: { messageId: "assistant-scene", slotId: "slot-video", mediaType: "video", background: true },
    }]));
  });
  await page.goto("/#/story");
});

test("V1.3: video slot resolved in place inside the original message", async ({ page }) => {
  await selectWork(page);
  const message = page.locator('[data-message-id="assistant-scene"]');
  // 视频原位回填：slot-video 变为 <video>，不新增消息、正文保留
  await expect(message.locator('video[src="http://127.0.0.1:18110/mock.mp4"]')).toBeVisible();
  await expect(page.locator('[data-message-id="assistant-scene"]')).toHaveCount(1);
  await expect(message.getByText("这是图片之后的收束段落。")).toBeVisible();
});
