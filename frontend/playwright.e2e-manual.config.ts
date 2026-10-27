import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { defineConfig } from "@playwright/test";

/** manual e2e 配置（链路③ 文档插图可视化验证）。
 *
 *  与 playwright.config.ts（默认套件）的区别：
 *  - testDir 指向 e2e-manual/，**不并入** `npm run test:e2e`（默认套件跑 mock，本套跑真实服务）；
 *  - 不启 webServer：复用**已运行**的真实 vite(5173) + 真实后端(8010)；
 *    那条 vite 的 API 基址被别的实例占走时，用 `E2E_BASE_URL` 指到同源 `/api` 代理的实例；
 *  - 用系统 Edge（channel=msedge），无需下载 Chromium。
 *
 *  运行：npx playwright test --config playwright.e2e-manual.config.ts --reporter=list
 */
process.env.HTTP_PROXY = "";
process.env.HTTPS_PROXY = "";
process.env.ALL_PROXY = "";
process.env.NO_PROXY = "127.0.0.1,localhost";

// 8010 若跑的是带本机令牌的 LAN 实例，浏览器侧没有引导 cookie ⇒ 统一在 harness 上带头，
// 免得每个 manual spec 各自读一遍令牌文件。
const tokenFile = path.resolve(__dirname, "../backend/data/lan_token.txt");
const localToken = existsSync(tokenFile) ? readFileSync(tokenFile, "utf8").trim() : "";

export default defineConfig({
  testDir: "./e2e-manual",
  fullyParallel: false,
  timeout: 60_000,
  outputDir: "../_tmp/e2e-doc-illustration",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5173",
    browserName: "chromium",
    channel: process.platform === "win32" ? "msedge" : undefined,
    trace: "retain-on-failure",
    viewport: { width: 1100, height: 900 },
    extraHTTPHeaders: localToken ? { "X-Demiurge-Token": localToken } : {},
  },
});
