// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { openSSE } from "./sse";

/** 跑一次 openSSE，拿到 onDone 的 err（undefined=正常/主动停止）。 */
function runOnce(): Promise<string | undefined> {
  return new Promise((resolve) => {
    openSSE("/api/ai/multi-agent", {}, () => undefined, resolve);
  });
}

function okStream(errAfterFirstChunk: boolean): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"type":"delta","text":"前半"}\n\n'));
      if (errAfterFirstChunk) controller.error(new Error("terminated"));
      else controller.close();
    },
  });
}

describe("openSSE 断流文案（2026-10-23 运行时稳健性短板 ③）", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("流读到一半终止 ⇒ 可读的断流文案（而非浏览器原生 terminated）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, body: okStream(true) })));
    const err = await runOnce();
    expect(err).toContain("连接中断");
    expect(err).toContain("后台活动面板");
    expect(err).not.toBe("terminated");
  });

  it("fetch 被拒（后端不在/网络断）⇒ 同一断流文案", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await runOnce();
    expect(err).toContain("连接中断");
  });

  it("AbortError 不上文案（用户主动停止，已有半成品照常保留）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      const e = new Error("aborted");
      e.name = "AbortError";
      throw e;
    }));
    expect(await runOnce()).toBeUndefined();
  });

  it("HTTP 错误仍走后端 detail（不套断流文案）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false,
      status: 409,
      body: null,
      json: async () => ({ detail: "已有活动运行" }),
    })));
    expect(await runOnce()).toBe("已有活动运行");
  });
});
