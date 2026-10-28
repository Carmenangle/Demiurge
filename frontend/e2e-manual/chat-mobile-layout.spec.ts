import { expect, test } from "@playwright/test";

/**
 * 手机端（390×844）对话页版式回归：设置区/输入卡不得吃掉对话正文。
 *
 * 立项原因（2026-10-26 用户真机截图「实际对话对话设置都把对话遮挡了」）：
 * 实测顶栏 75 + 功能栏 169 + 输入卡 264 + 底栏 57 ⇒ `.chat-stream` 只剩 **224px（26% 视口）**；
 * 同时「对话设置」浮层（.nci-body 420px + right:0 锚在触发钮上）左边界落到 **-208px**，
 * 被 body 的 overflow-x:hidden 静默裁掉；失败插画槽那串不可断的错误文案把气泡撑破
 * （关掉 `.media-slot-failed{overflow-wrap:anywhere}` 复现：.bot-content 256→722、
 * .bot-rich-parts 228→708、槽 510→608），正文右侧被切。
 * 断言 ③ 是**真数据**判别式：当前会话有 10 个失败槽，护栏一关即红（无需注入 fixture）。
 *
 * 跑法（需真实 vite 5173 + 后端 8010 已在运行）：
 *   npx playwright test --config playwright.e2e-manual.config.ts \
 *     --output=../_tmp/e2e-chat-mobile-layout -g chat-mobile
 */

const VIEWPORT = { width: 390, height: 844 };

async function pickFirst(page: import("@playwright/test").Page, label: string) {
  const sel = page.locator(`label.repo-picker:has-text("${label}") select`);
  if ((await sel.count()) === 0) return false;
  const count = await sel.locator("option").count();
  if (count < 2) return false; // 只有「（选仓库）」占位 ⇒ 本机没有仓库数据
  await sel.selectOption({ index: 1 });
  await page.waitForTimeout(600);
  return true;
}

test.describe("chat mobile layout @390x844", () => {
  test("对话正文拿到视口近半，浮层不越界、不撑破气泡", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize(VIEWPORT);
    await page.goto("/", { waitUntil: "networkidle" });
    test.skip(!(await pickFirst(page, "仓库")), "本机没有可选的仓库，跳过");
    await pickFirst(page, "作品");
    await page.waitForSelector(".chat-view-head", { timeout: 20_000 });

    const box = (selector: string) => page.locator(selector).first().boundingBox();

    // ① 功能栏收到两行以内（改前 149~169px）
    const head = await box(".chat-view-head");
    expect(head).not.toBeNull();
    expect(head!.height).toBeLessThanOrEqual(90);

    // ② 正文列至少拿到视口的 43%（改前 26%；改后实测 409px = 48%）
    const stream = await box(".chat-stream");
    expect(stream!.height / VIEWPORT.height).toBeGreaterThanOrEqual(0.43);

    // ③ 气泡内不得出现横向溢出（失败槽的长路径文案把轨道与气泡一起顶宽，见文件头实测）
    const overflowing = await page.evaluate(() =>
      [...document.querySelectorAll(".bot-text, .bot-content, .bot-rich-parts, .media-slot")]
        .filter((el) => el.scrollWidth > el.clientWidth + 1)
        .map((el) => `${el.className}|${el.scrollWidth}>${el.clientWidth}`),
    );
    expect(overflowing).toEqual([]);

    // ④ 「对话设置」浮层：完整落在视口内，且不盖住自己的触发钮（盖住就打不开也关不掉）
    //    触发钮在 .head-tools 里，而 2026-10-27「对话页纵向空间回收」起 head-tools 默认
    //    display:none（点「···」才展开）⇒ 不先开抽屉就是 0×0，click 直接超时。
    await page.locator(".chat-view-head .head-tools-toggle").click();
    await page.waitForSelector(".chat-view-head .head-tools.open", { timeout: 5_000 });
    await page.locator(".chat-view-head .nci-toggle").click();
    await page.waitForSelector(".nci-body", { state: "visible", timeout: 5_000 });
    const nci = await box(".nci-body");
    const toggle = await box(".chat-view-head .nci-toggle");
    expect(nci!.x).toBeGreaterThanOrEqual(0);
    expect(nci!.x + nci!.width).toBeLessThanOrEqual(VIEWPORT.width);
    expect(nci!.y).toBeGreaterThanOrEqual(toggle!.y + toggle!.height);
    await page.screenshot({ path: "../_tmp/e2e-chat-mobile-layout/nci-open.png" }).catch(() => {});
    await page.evaluate(() => document.querySelector<HTMLElement>(".chat-view-head .nci-toggle")?.click());

    // ⑤ 输入卡里的模型切换浮层同样不得越界（它是 .chat-actions 内的绝对定位子层）
    await page.evaluate(() => document.querySelectorAll<HTMLElement>(".chat-actions .model-switch-btn")[1]?.click());
    await page.waitForSelector(".model-switch-menu", { state: "visible", timeout: 5_000 });
    const menu = await box(".model-switch-menu");
    expect(menu!.x).toBeGreaterThanOrEqual(0);
    expect(menu!.x + menu!.width).toBeLessThanOrEqual(VIEWPORT.width);
  });
});
