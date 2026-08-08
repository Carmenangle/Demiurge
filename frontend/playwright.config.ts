import { defineConfig } from "@playwright/test";

process.env.HTTP_PROXY = "";
process.env.HTTPS_PROXY = "";
process.env.ALL_PROXY = "";
process.env.NO_PROXY = "127.0.0.1,localhost";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:15173",
    browserName: "chromium",
    channel: process.platform === "win32" ? "msedge" : undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node e2e/test-servers.mjs",
    url: "http://127.0.0.1:15173",
    reuseExistingServer: false,
  },
});
