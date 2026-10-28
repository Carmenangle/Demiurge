import { expect, test, type Page } from "@playwright/test";

/** 手机端「联机检测」弹窗真浏览器验证（2026-10-27 追加需求 1）。
 *
 *  用户口径：「电脑不是有 Comfyui 检测的弹窗吗，那手机端就是检测有没有和电脑联机的弹窗……
 *  前提是设置了电脑和 comfyui 的地址并且开始连接后才会跳出弹窗，然后如果连接着就不会出现，
 *  中断了就会跳出」。
 *
 *  本用例只验**前端行为**（轮询 / 边沿触发 / 390 视口里真的渲染出抽屉 / 点「知道了」后本轮不再
 *  打扰）：`/api/` 全部自足打桩，状态由用例自己翻转——真实双后端链路的五态由
 *  `_tmp/online-watch-e2e.sh` 负责（那边打的是真 HTTP，不在此处重复）。
 *  跑法：vite 需带 VITE_MOBILE_PROFILE=1，否则 MOBILE_PROFILE=false、组件按设计直接空转。
 */
test.use({ viewport: { width: 390, height: 844 } });

const SHOTS = "../_tmp/mobile-online-watch";
const PC_BASE = "http://10.0.0.9:8010";
const PC_COMFY = "http://127.0.0.1:8188";

type Stub = "off" | "green" | "pc_down" | "comfy_down" | "no_comfy_url";

const STUB_BODIES: Record<Stub, Record<string, unknown>> = {
  off: {
    active: false, base_url: "", reachable: false, latency_ms: null,
    comfyui_url: "", comfyui_running: false, detail: "",
  },
  green: {
    active: true, base_url: PC_BASE, reachable: true, latency_ms: 128,
    comfyui_url: PC_COMFY, comfyui_running: true, detail: "",
  },
  pc_down: {
    active: true, base_url: PC_BASE, reachable: false, latency_ms: null,
    comfyui_url: "", comfyui_running: false,
    detail: "连不上电脑：确认同一 Wi‑Fi、地址/端口正确、PC 已用 start-lan 脚本启动，并放行防火墙",
  },
  comfy_down: {
    active: true, base_url: PC_BASE, reachable: true, latency_ms: 210,
    comfyui_url: PC_COMFY, comfyui_running: false,
    detail: `地址 ${PC_COMFY} 上没有响应中的 ComfyUI`,
  },
  no_comfy_url: {
    active: true, base_url: PC_BASE, reachable: true, latency_ms: 190,
    comfyui_url: "", comfyui_running: false, detail: "电脑端没有填写 ComfyUI 地址",
  },
};

let stubState: Stub = "off";
let onlineCalls = 0;

/** 只认 pathname 前缀：通配 **\/api\/** 会把 vite 的 /src/api/client.ts 也拦掉（页面白屏）。 */
async function stubApi(page: Page) {
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/api/mobile/online-status") {
      onlineCalls += 1;
      return json(STUB_BODIES[stubState]);
    }
    if (p === "/api/user-state") {
      return json({
        repos: [],
        settings: { outputDir: "D:/x", chatModels: [], imageModels: [], userPersonas: [], liquidGlass: {} },
        selection: null,
      });
    }
    if (p === "/api/skills" || p === "/api/mcp") return json([]);
    return json({ ok: true });
  });
}

const watch = (page: Page) => page.locator(".modal-mask .modal");

/** 真等满 ms：`toHaveCount(0)` 立即满足，直接 await 等于没等（踩过：静默判据会退化成空断言）。 */
async function expectSilent(page: Page, ms: number) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    await expect(watch(page).filter({ hasText: "联机中断" })).toHaveCount(0, { timeout: 1_000 });
    await expect(watch(page).filter({ hasText: "ComfyUI 没开着" })).toHaveCount(0, { timeout: 1_000 });
    await page.waitForTimeout(1_000);
  }
}

test.beforeEach(() => {
  stubState = "off";
  onlineCalls = 0;
});

test("未开联机（active=false）：永不弹窗，但探针仍在本机应答", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await expect(page.locator(".app-main")).toBeVisible();
  await expectSilent(page, 12_000);
  expect(onlineCalls, "轮询在跑（否则「永不弹」是假绿）").toBeGreaterThan(1);
  await page.screenshot({ path: `${SHOTS}/01-off-linked-silent.png`, fullPage: false });
});

test("连接正常：不弹窗；断一次弹一次，点「知道了」后本轮不再打扰；换断点再弹；恢复自动静默", async ({ page }) => {
  test.setTimeout(180_000);
  await stubApi(page);
  stubState = "green";
  await page.goto("/");
  await expect(page.locator(".app-main")).toBeVisible();

  // ① 连着就不出现（覆盖两个以上的 5 s 轮询周期）
  await expectSilent(page, 12_000);
  await page.screenshot({ path: `${SHOTS}/02-green-no-modal.png`, fullPage: false });

  // ② 电脑在线、ComfyUI 没按地址开着 ⇒ 弹，且文案点名电脑存的那个地址
  stubState = "comfy_down";
  await expect(watch(page).filter({ hasText: "电脑在线，但 ComfyUI 没开着" })).toBeVisible({ timeout: 20_000 });
  await expect(watch(page)).toContainText(PC_COMFY);
  const comfyBox = await watch(page).boundingBox();
  expect(comfyBox, "弹窗在 390 视口内").toBeTruthy();
  expect(comfyBox!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: `${SHOTS}/03-comfy-down-modal.png`, fullPage: false });

  // ③ 边沿触发：同一个断点被「知道了」压掉后，只要没换断点就不得再弹（不是每 5 s 敲一次）
  await page.getByRole("button", { name: "知道了" }).click();
  await expect(watch(page).filter({ hasText: "ComfyUI 没开着" })).toHaveCount(0);
  const callsAtDismiss = onlineCalls;
  await expectSilent(page, 13_000);
  expect(onlineCalls, "静默期间仍在轮询").toBeGreaterThan(callsAtDismiss);

  // ④ 断点换了（电脑整体不可达）⇒ 新episode 再弹，文案换成「中断」并带电脑地址
  stubState = "pc_down";
  await expect(watch(page).filter({ hasText: "与电脑的联机中断了" })).toBeVisible({ timeout: 20_000 });
  await expect(watch(page)).toContainText(PC_BASE);
  await page.screenshot({ path: `${SHOTS}/04-pc-down-modal.png`, fullPage: false });

  // ⑤ 恢复 ⇒ 自动静默，不需要用户操作
  stubState = "green";
  await expect(watch(page).filter({ hasText: "与电脑的联机中断了" })).toHaveCount(0, { timeout: 20_000 });
  await page.screenshot({ path: `${SHOTS}/05-recovered-silent.png`, fullPage: false });

  // 抽屉形态不得把页面撑出横向滚动
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "整页横向溢出(px)").toBeLessThanOrEqual(0);
});

test("电脑没填 ComfyUI 地址：如实报「没有填写」，不编造原因", async ({ page }) => {
  await stubApi(page);
  stubState = "no_comfy_url";
  await page.goto("/");
  await expect(watch(page).filter({ hasText: "电脑在线，但 ComfyUI 没开着" })).toBeVisible({ timeout: 20_000 });
  await expect(watch(page)).toContainText("电脑端没有填写 ComfyUI 地址");
  await page.screenshot({ path: `${SHOTS}/06-no-comfy-url-modal.png`, fullPage: false });
});
