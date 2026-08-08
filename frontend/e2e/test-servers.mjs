import { spawn } from "node:child_process";
import "./mock-api.mjs";

const vite = spawn(process.execPath, ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "15173"], {
  cwd: new URL("..", import.meta.url),
  env: { ...process.env, VITE_API_BASE: "http://127.0.0.1:18110/api" },
  stdio: "inherit",
});
const stop = () => { if (!vite.killed) vite.kill(); };
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
vite.on("exit", (code) => { process.exitCode = code ?? 0; });
