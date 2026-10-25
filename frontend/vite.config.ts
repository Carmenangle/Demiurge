import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 后端给的取图地址是根相对（`/api/comfyui/...`，属主 backend/app/services/view_urls.py），
// 浏览器按当前 origin 去请求。生产态 dist 由后端挂载，同源天然成立；开发态页面在 5173，
// 少了这条转发图片就 404。默认端口对齐 src/api/client.ts 里 API_BASE 的缺省值；后端不在
// 这个端口时（桌面壳协商到别的端口、LAN 隔离实例）用 VITE_DEV_API_ORIGIN 覆盖。
const devApiOrigin = process.env.VITE_DEV_API_ORIGIN || "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: devApiOrigin, changeOrigin: true },
    },
  },
  test: {
    // e2e / e2e-manual 都是 Playwright 用例（真实浏览器驱动），不能被 vitest 收集
    exclude: ["e2e/**", "e2e-manual/**", "node_modules/**"],
  },
});
