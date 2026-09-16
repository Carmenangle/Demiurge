// @vitest-environment jsdom
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { existsSync, readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  apiGet, WORKS_ROOT_UNCONFIGURED_EVENT, WORKS_ROOT_UNCONFIGURED_MARK,
} from "./client";

// 本仓未装 @types/node（应用代码不碰 Node API），但本用例要按 cwd 找后端源码 ⇒ 就地声明最小签名。
declare const process: { cwd(): string };

/**
 * 作品根校验「未配置」的跨语言契约（2026-09-15 架构审计 P15）。
 *
 * 用户定案：仓库文件夹没配时，作品根校验由「放行」改为**拒绝**，前端弹窗引导去设置页。
 * 前后端是靠**一条固定文案**串起来的（`detail.includes(MARK)`），文案分叉就会静默不弹窗
 * （没有类型系统能拦住跨语言的字符串）⇒ 这里直接读后端源码把两边钉在一起。
 */
function readBackend(rel: string): string {
  // jsdom 环境下 `import.meta.url` 不是 file: URL（`new URL(rel, import.meta.url)` 会抛
  // 「The URL must be of scheme file」），故按 vitest 的 cwd 往上试两个位置（frontend/ 或仓库根）。
  const candidates = [
    `${process.cwd()}/../${rel}`,
    `${process.cwd()}/${rel}`,
  ];
  const hit = candidates.find((p) => existsSync(p));
  if (!hit) throw new Error(`找不到后端源码 ${rel}（试过：${candidates.join(" / ")}）`);
  return readFileSync(hit, "utf-8");
}

const backend = readBackend("backend/app/services/repo_meta.py");

const NOT_CONFIGURED_DETAIL =
  "尚未配置「仓库文件夹」，请先在「设置 → 路径与代理」里填写后再试。";
const MISMATCH_DETAIL = "output_dir 必须是当前配置的仓库文件夹根路径";

function stubFetch(detail: string) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail }), { status: 400, headers: { "Content-Type": "application/json" } },
  )));
}

function listen(): { seen: string[]; stop: () => void } {
  const seen: string[] = [];
  const handler = (e: Event) =>
    seen.push((e as CustomEvent<{ message?: string }>).detail?.message || "");
  window.addEventListener(WORKS_ROOT_UNCONFIGURED_EVENT, handler);
  return { seen, stop: () => window.removeEventListener(WORKS_ROOT_UNCONFIGURED_EVENT, handler) };
}

afterEach(() => vi.unstubAllGlobals());

describe("作品根校验「未配置」文案：前端识别串 ⊆ 后端固定文案", () => {
  it("识别串嵌在后端 WORKS_ROOT_NOT_CONFIGURED_MSG 里", () => {
    const m = backend.match(/WORKS_ROOT_NOT_CONFIGURED_MSG\s*=\s*"([^"]*)"/);
    expect(m, "backend/app/services/repo_meta.py 里找不到 WORKS_ROOT_NOT_CONFIGURED_MSG").not.toBeNull();
    expect(m![1]).toContain(WORKS_ROOT_UNCONFIGURED_MARK);
  });

  it("「不匹配」那条文案**不**含识别串（否则选错目录也会误弹「去配置」）", () => {
    const m = backend.match(/^WORKS_ROOT_MSG\s*=\s*"([^"]*)"/m);
    expect(m).not.toBeNull();
    expect(m![1]).not.toContain(WORKS_ROOT_UNCONFIGURED_MARK);
    expect(MISMATCH_DETAIL).not.toContain(WORKS_ROOT_UNCONFIGURED_MARK);
  });
});

describe("api 层：未配置仓库文件夹 → 广播引导事件", () => {
  it("400 + 未配置文案：照旧抛错，同时广播一次（带后端原文）", async () => {
    stubFetch(NOT_CONFIGURED_DETAIL);
    const { seen, stop } = listen();
    await expect(apiGet("/artifacts/list?output_dir=X")).rejects.toThrow("尚未配置「仓库文件夹」");
    stop();
    expect(seen).toEqual([NOT_CONFIGURED_DETAIL]);
  });

  it("别的 400（路径不匹配）不广播：不误弹窗", async () => {
    stubFetch(MISMATCH_DETAIL);
    const { seen, stop } = listen();
    await expect(apiGet("/artifacts/list?output_dir=X")).rejects.toThrow("必须是当前配置的仓库文件夹根路径");
    stop();
    expect(seen).toEqual([]);
  });

  it("500 或非 JSON 错误体也不广播", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    const { seen, stop } = listen();
    await expect(apiGet("/artifacts/list?output_dir=X")).rejects.toThrow();
    stop();
    expect(seen).toEqual([]);
  });
});
