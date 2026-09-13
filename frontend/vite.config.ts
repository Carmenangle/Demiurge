import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
  test: {
    // e2e / e2e-manual 都是 Playwright 用例（真实浏览器驱动），不能被 vitest 收集
    exclude: ["e2e/**", "e2e-manual/**", "node_modules/**"],
  },
});
