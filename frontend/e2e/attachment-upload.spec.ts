import { expect, test, type Page } from "@playwright/test";

// 附件上传链路 e2e 回归（2026-09-01 黑屏事故后固化）：
// 真实浏览器 + mock 后端（snake_case 响应）验证「粘贴/选择文件 → 占位卡 → 上传成功 →
// 替换渲染为附件卡」全链路无渲染崩溃（pageerror = React 渲染期错误，会整树卸载黑屏）。
// mock-api.mjs 的 /api/attachments/upload 返回 {ok, file_id, name, mime, size}（snake_case），
// 前端 uploadAttachment 负责 camelCase 映射——若映射回归，占位卡替换后渲染 `String(undefined).startsWith`
// 抛 TypeError → pageerror，本测试即红。

async function selectWork(page: Page) {
  const pickers = page.locator(".repo-picker select");
  await pickers.nth(0).selectOption("library");
  await pickers.nth(1).selectOption("work");
  await expect(page.locator('[data-message-id="assistant-scene"]')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/#/story");
});

test("文件选择器注入文档 → 附件卡替换渲染，无渲染期错误", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.stack || err)));
  const uploadResponses: string[] = [];
  page.on("response", (r) => {
    if (r.url().includes("/api/attachments/upload")) uploadResponses.push(`${r.request().method()} ${r.url()} → ${r.status()}`);
  });

  await selectWork(page);

  const richInput = page.locator('input[type=file][accept*=".md"]');
  await expect(richInput).toHaveCount(1);
  await richInput.setInputFiles({
    name: "回归测试.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 附件上传 e2e 回归测试\n\n".repeat(20), "utf-8"),
  });

  // 附件卡最终应稳定显示文件名 + 大小（上传成功替换占位卡）
  const card = page.locator(".rich-filebar-item", { hasText: "回归测试.md" });
  await expect(card).toBeVisible();
  await expect(card.getByText(/B$/)).toBeVisible();

  // 上传请求确实发出且成功（mock 返回 200）
  expect(uploadResponses).toHaveLength(1);
  expect(uploadResponses[0]).toContain("→ 200");

  // 无渲染期错误（pageerror = React 渲染崩溃/黑屏）
  expect(pageErrors).toEqual([]);
});

test("Ctrl+V 粘贴文档（ClipboardEvent 含 File）→ 附件卡出现，无渲染期错误", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.stack || err)));
  const uploadResponses: string[] = [];
  page.on("response", (r) => {
    if (r.url().includes("/api/attachments/upload")) uploadResponses.push(`${r.request().method()} ${r.url()} → ${r.status()}`);
  });

  await selectWork(page);

  // 与用户 Ctrl+V 同构：构造原生 paste 事件（DataTransfer 携带 File）派发给 textarea
  const dispatched = await page.evaluate(async () => {
    const ta = document.querySelector("textarea.rich-input");
    if (!ta) return false;
    const dt = new DataTransfer();
    dt.items.add(new File(["# 粘贴回归测试\n".repeat(20)], "粘贴回归.md", { type: "text/markdown" }));
    ta.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    return true;
  });
  expect(dispatched).toBe(true);

  const card = page.locator(".rich-filebar-item", { hasText: "粘贴回归.md" });
  await expect(card).toBeVisible();
  expect(uploadResponses).toHaveLength(1);
  expect(uploadResponses[0]).toContain("→ 200");
  expect(pageErrors).toEqual([]);
});

// D1 历史回放回归（2026-09-01 用户报告「发送出去却看不到对应文档名」后固化）：
// 上传附件 → 输入文本 → Enter 发送 → 断言用户消息气泡内出现 .file-attach-chip（含文件名+大小）。
// 根因：RichInput.doSubmit 早期只把 attachments 放顶层 content.attachments 字段，parts 不含 file，
// userMsg.parts 缺 file → ChatMessages 渲染 else 分支（仅 msg.text）→ 附件元信息丢失。
// 修复：RichInput.buildSubmitParts 把 attachments 转成 type="file" 的 part 一并写入 parts。
test("上传 .md 附件 + 文本 → 发送后用户消息体渲染 file-attach-chip（修复 D1 历史回放）", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.stack || err)));

  await selectWork(page);

  // 注入 .md 附件（与用户路径一致：文件选择器）
  const richInput = page.locator('input[type=file][accept*=".md"]');
  await richInput.setInputFiles({
    name: "形象提示词-唐柚.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 形象提示词\n\n唐柚穿搭套系定义：\n\n".repeat(20), "utf-8"),
  });

  // 等待上传完成 + 占位卡替换为真实 meta（fileId 真源，非 pending-）
  const filebarCard = page.locator(".rich-filebar-item", { hasText: "形象提示词-唐柚.md" });
  await expect(filebarCard).toBeVisible();
  await expect(filebarCard).not.toContainText("上传中");

  // 输入文本（指令）+ Enter 发送
  await page.locator("textarea.rich-input").fill("读取文档,生成唐柚的第一套穿搭参考图");
  await page.keyboard.press("Enter");

  // 关键断言：用户消息气泡内出现 .file-attach-chip（FileAttachmentChip 渲染的根 selector），
  // 含原文件名与人类可读大小。**bug 修复前这里 .file-attach-chip 不存在（被吞）**。
  const userBubble = page.locator(".msg-user").last();
  await expect(userBubble).toBeVisible();
  const fileChip = userBubble.locator(".file-attach-chip");
  await expect(fileChip).toBeVisible();
  await expect(fileChip).toContainText("形象提示词-唐柚.md");
  await expect(fileChip.locator(".file-attach-chip-size")).toBeVisible();
  await expect(fileChip.locator(".file-attach-chip-size")).not.toContainText("0 B"); // 真实文件大小已上传

  // 无渲染期错误（pageerror = React 渲染崩溃）
  expect(pageErrors).toEqual([]);
});

// richPaste 混合内容回归（2026-09-01 用户截图场景：外部 AI 复制含 @image 引用 + 截图粘贴 Demiurge）：
// 原实现「text 优先」导致图片被丢弃（richPaste.test.ts L14-20 用例已被翻转）。
// 修复后：剪贴板同时含文本 + 图片时，**两者都保留**——图片进图片栏，文本插入输入框。
test("粘贴混合内容（文本 + 图片）→ 图片进图片栏 + 文本进 textarea", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(String(err?.stack || err)));

  await selectWork(page);

  // 与用户操作同构：构造 ClipboardEvent 同时含 text/plain + image/png（DataTransfer.items 加 File）。
  // 模拟从外部 AI 复制「文本（含 @image 引用）+ 截图」后的粘贴行为。
  const dispatched = await page.evaluate(async () => {
    const ta = document.querySelector("textarea.rich-input");
    if (!ta) return false;
    const dt = new DataTransfer();
    dt.setData("text/plain", "读取文档 @image#1:\"{...}.png\" 调用 krea2 文生图");
    dt.items.add(new File(["# 截图内容\n".repeat(30)], "screenshot.png", { type: "image/png" }));
    ta.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    return true;
  });
  expect(dispatched).toBe(true);

  // 文本进 textarea（关键断言：原 bug 这里 textarea 为空，文本被丢弃）
  const ta = page.locator("textarea.rich-input");
  await expect(ta).toHaveValue(/读取文档 @image#1/);

  // 图片进图片栏（关键断言：原 bug 这里 .rich-imgbar-item 不存在）
  await expect(page.locator(".rich-imgbar-item").first()).toBeVisible();

  // 无渲染期错误（pageerror = React 渲染崩溃）
  expect(pageErrors).toEqual([]);
});
