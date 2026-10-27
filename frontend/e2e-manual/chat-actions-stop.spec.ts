import { expect, test } from "@playwright/test";

/**
 * 手机端（390×844）输入卡工具条「生成中」态版式回归。
 *
 * 立项原因（2026-10-27 23:35 用户真机截图）：Agent 生成中 `.chat-actions` 会同时出现
 * 发送与「停止」（`.btn.danger`），这一态在静止态下不存在 ⇒ 静止态的版式测试全绿也测不到。
 * 停止键没有移动端紧凑样式（`.btn` 默认 padding 8px 16px、文字可断行），被 flex 压窄后
 * 「停止」断成两行 ⇒ 按钮变高变大、整行溢出输入卡。
 *
 * 做法：真实页面 + 真实 CSS，只把「生成中」才会出现的那颗按钮按 ChatView 的形态注入
 * （clone 发送键改类与文案，保留行内 svg 样式），量完即摘——纯版式判别，不碰数据链路。
 * ⚠ 不选仓库：直接沿用 user_state 里已持久化的选中项（选仓库会改写用户当前的作品选择）。
 *
 * 跑法（需真实后端 8010 已在运行；本机令牌由 playwright.e2e-manual.config.ts 统一带头。
 * 默认打 5173，那条 vite 的 API 基址被别的实例占走时改指同源代理实例：
 * `E2E_BASE_URL=http://127.0.0.1:5273`）：
 *   npx playwright test --config playwright.e2e-manual.config.ts \
 *     --output=../_tmp/e2e-chat-actions-stop -g chat-actions-stop
 */

const VIEWPORT = { width: 390, height: 844 };

/** 注入「生成中」态的停止键；返回一个自清函数。 */
async function injectStopButton(page: import("@playwright/test").Page) {
  await page.evaluate(() => {
    const actions = document.querySelector<HTMLElement>(".chat-actions");
    const send = actions?.querySelector<HTMLElement>(".chat-send-btn");
    if (!actions || !send) throw new Error("未找到 .chat-actions / .chat-send-btn");
    const stop = send.cloneNode(true) as HTMLElement;
    stop.className = "btn danger chat-stop-btn";
    stop.setAttribute("title", "仅停止当前生成，不发送");
    stop.lastChild!.textContent = "停止";
    stop.setAttribute("data-probe", "stop");
    actions.appendChild(stop);
  });
  return () =>
    page.evaluate(() => {
      document.querySelector("[data-probe='stop']")?.remove();
    });
}

async function injectExtraSwitcher(page: import("@playwright/test").Page) {
  await page.evaluate(() => {
    const actions = document.querySelector<HTMLElement>(".chat-actions");
    const first = actions?.querySelector<HTMLElement>(".model-switch");
    if (!actions || !first) throw new Error("未找到 .model-switch");
    const extra = first.cloneNode(true) as HTMLElement;
    extra.setAttribute("data-probe", "extra-switch");
    actions.insertBefore(extra, actions.firstChild);
  });
  return () =>
    page.evaluate(() => {
      document.querySelector("[data-probe='extra-switch']")?.remove();
    });
}

async function measure(page: import("@playwright/test").Page) {
  return page.evaluate(() => {
    const actions = document.querySelector<HTMLElement>(".chat-actions")!;
    const card = actions.closest<HTMLElement>(".chat-input-bar") ?? actions.parentElement!;
    const cardBox = card.getBoundingClientRect();
    const kids = [...actions.children].map((el) => {
      const b = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        cls: el.className.toString().slice(0, 40),
        text: (el.textContent || "").trim().slice(0, 8),
        x: Math.round(b.x),
        right: Math.round(b.right),
        w: Math.round(b.width),
        h: Math.round(b.height),
        lines: Math.round(b.height / parseFloat(cs.lineHeight || "18")),
      };
    });
    return {
      card: { x: Math.round(cardBox.x), right: Math.round(cardBox.right), w: Math.round(cardBox.width) },
      actions: {
        w: Math.round(actions.getBoundingClientRect().width),
        clientW: actions.clientWidth,
        scrollW: actions.scrollWidth,
        h: Math.round(actions.getBoundingClientRect().height),
      },
      kids,
    };
  });
}

test.describe("chat-actions generating state @390x844", () => {
  test("生成中多一颗停止键后，工具条仍是一行且不溢出输入卡", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize(VIEWPORT);
    await page.goto("/", { waitUntil: "networkidle" });
    // 沿用持久化选中项；没有对话页（未选仓库）就跳过，不去改用户的选择
    const hasChat = await page.locator(".chat-actions .chat-send-btn").count();
    test.skip(hasChat === 0, "当前没有选中作品（对话页未挂载），跳过");
    await page.waitForSelector(".chat-actions .chat-send-btn", { timeout: 20_000 });

    const assertFits = (m: Awaited<ReturnType<typeof measure>>, tag: string) => {
      console.log(tag, JSON.stringify(m));
      // ① 工具条不得横向溢出自身（越界的本质是 flex 把最左子项顶出卡外，见 ③）
      expect(m.actions.scrollW, tag).toBeLessThanOrEqual(m.actions.clientW + 1);
      // ② 纵向最多两行（换行兜底可以，不能顶成三四行吃掉输入卡）
      expect(m.actions.h, tag).toBeLessThanOrEqual(76);
      for (const k of m.kids) {
        // ③ 每个子元素完整落在输入卡内（右侧 48px 是右下浮标通道，由 padding-right 让出）
        expect(k.x, `${tag} ${k.cls}|${k.text} 左边越界`).toBeGreaterThanOrEqual(m.card.x);
        expect(k.right, `${tag} ${k.cls}|${k.text} 右边越界`).toBeLessThanOrEqual(m.card.right);
        // ④ 按钮文字不得断行（停止键从 33px 长到 81px 的直接原因）
        expect(k.h, `${tag} ${k.cls}|${k.text} 变高到 ${k.h}px`).toBeLessThanOrEqual(40);
      }
    };

    const clean = await injectStopButton(page);
    assertFits(await measure(page), "生成中（5 切换器）");

    // 最坏宽度档：配了智能体 ⇒ 6 个切换器（本机没配，克隆一个顶上）
    const cleanExtra = await injectExtraSwitcher(page);
    await page.screenshot({ path: "../_tmp/e2e-chat-actions-stop/generating-worst.png" }).catch(() => {});
    assertFits(await measure(page), "生成中（6 切换器）");
    await cleanExtra();
    await clean();
  });
});
