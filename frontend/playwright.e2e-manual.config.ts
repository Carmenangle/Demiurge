import { defineConfig } from "@playwright/test";

/** manual e2e 配置（链路③ 文档插图可视化验证）。
 *
 *  与 playwright.config.ts（默认套件）的区别：
 *  - testDir 指向 e2e-manual/，**不并入** `npm run test:e2e`（默认套件跑 mock，本套跑真实服务）；
 *  - 不启 webServer：复用**已运行**的真实 vite(5173) + 真实后端(8010)；
 *  - 用系统 Edge（channel=msedge），无需下载 Chromium。
 *
 *  运行：npx playwright test --config playwright.e2e-manual.config.ts --reporter=list
 */
process.env.HTTP_PROXY = "";
process.env.HTTPS_PROXY = "";
process.env.ALL_PROXY = "";
process.env.NO_PROXY = "127.0.0.1,localhost";

export default defineConfig({
  testDir: "./e2e-manual",
  fullyParallel: false,
  timeout: 60_000,
  outputDir: "../_tmp/e2e-doc-illustration",
  use: {
    baseURL: "http://127.0.0.1:5173",
    browserName: "chromium",
    channel: process.platform === "win32" ? "msedge" : undefined,
    trace: "retain-on-failure",
    viewport: { width: 1100, height: 900 },
  },
});
