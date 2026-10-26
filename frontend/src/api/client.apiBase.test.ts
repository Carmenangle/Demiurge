// @vitest-environment jsdom
// API_BASE 契约（L2 联机同库 · 2026-10-26）：生产默认**同源相对 "/api"**（origin 不烧进
// 产物，与 view_urls 根相对同一条教义——LAN 浏览器/手机联机访问 PC 的前提）；dev 默认
// 绝对回环；VITE_API_BASE 永远最优先。源码锁（读 client.ts 断言字面量）防静默回退。
import { describe, expect, it, vi } from "vitest";
// @ts-expect-error 应用代码不装 Node 类型（仓库惯例）；Vitest 跑在 Node 里，测试可用。
import { existsSync, readFileSync } from "node:fs";
import { API_BASE } from "./client";

// jsdom 环境下 import.meta.url 不是 file: URL（client.contract.test.ts 同坑），
// 按 vitest 的 cwd 往上试两个位置找 client.ts 源码。
declare const process: { cwd(): string };

function readClientSource(): string {
  const candidates = [`${process.cwd()}/src/api/client.ts`, `${process.cwd()}/frontend/src/api/client.ts`];
  const hit = candidates.find((path) => existsSync(path));
  if (!hit) throw new Error(`找不到 client.ts 源码（试过：${candidates.join(" / ")}）`);
  return readFileSync(hit, "utf-8");
}

const source = readClientSource();

describe("API_BASE 默认形态（L2 同源教义）", () => {
  it("生产默认是同源相对 /api（源码锁：不允许静默改回绝对回环）", () => {
    expect(source).toContain('? "http://127.0.0.1:8010/api" : "/api"');
    // 旧形态（无条件绝对回环默认）不得回归
    expect(source).not.toMatch(/VITE_API_BASE \|\| "http:\/\//);
  });

  it("dev 默认（vitest 即 DEV）是绝对回环，与历史行为一致", () => {
    expect(API_BASE).toBe("http://127.0.0.1:8010/api");
  });

  it("VITE_API_BASE 覆盖永远最优先", async () => {
    vi.stubEnv("VITE_API_BASE", "http://192.168.1.9:8010/api/");
    vi.resetModules();
    const mod = await import("./client");
    expect(mod.API_BASE).toBe("http://192.168.1.9:8010/api");
    vi.unstubAllEnvs();
    vi.resetModules();
  });
});
