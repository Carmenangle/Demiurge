import { describe, expect, it, vi } from "vitest";
import {
  moveComfyOutputToInput, submitGraph, submitWorkflow, uploadRemoteImageToInput,
} from "./comfyui";

describe("moveComfyOutputToInput（V1.6/P5 首尾帧顺序链）", () => {
  it("把 output 产物图 fetch 取回并上传回 input 目录，返回 input 文件名", async () => {
    const fetchMock = vi.fn(async (url: unknown) => {
      const u = String(url);
      if (u.includes("/comfyui/view?")) {
        return { ok: true, blob: async () => new Blob(["fake"], { type: "image/png" }) };
      }
      if (u.includes("/comfyui/upload")) {
        return { ok: true, json: async () => ({ name: "frame.png", raw: {} }) };
      }
      return { ok: false, status: 404 };
    });
    vi.stubGlobal("fetch", fetchMock);

    const name = await moveComfyOutputToInput(
      { filename: "out.png", subfolder: "", type: "output" },
      "http://comfy",
    );

    expect(name).toBe("frame.png");
    // 两次 fetch：先取产物图（/comfyui/view），再上传（/comfyui/upload）
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toContain("/comfyui/view?");
    expect(fetchMock.mock.calls[1][0]).toContain("/comfyui/upload");
    vi.unstubAllGlobals();
  });

  it("fetch/view 失败向上抛错（调用方降级处理）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("comfy down")));

    await expect(moveComfyOutputToInput(
      { filename: "out.png", subfolder: "", type: "output" },
      "http://comfy",
    )).rejects.toThrow("comfy down");
    vi.unstubAllGlobals();
  });
});

describe("uploadRemoteImageToInput（W3 转场参考图）", () => {
  it("把上尾帧图（URL）取回并上传回 input，返回 input 文件名", async () => {
    const fetchMock = vi.fn(async (url: unknown) => {
      const u = String(url);
      if (u.includes("/comfyui/local-view?")) {
        return { ok: true, blob: async () => new Blob(["fake"], { type: "image/png" }) };
      }
      if (u.includes("/comfyui/upload")) {
        return { ok: true, json: async () => ({ name: "prev-tail.png", raw: {} }) };
      }
      return { ok: false, status: 404 };
    });
    vi.stubGlobal("fetch", fetchMock);

    const name = await uploadRemoteImageToInput(
      "http://host/comfyui/local-view?path=%2Fout%2Fprev.png",
      "http://comfy",
    );

    expect(name).toBe("prev-tail.png");
    expect(fetchMock.mock.calls[0][0]).toContain("/comfyui/local-view?");
    expect(fetchMock.mock.calls[1][0]).toContain("/comfyui/upload");
    vi.unstubAllGlobals();
  });

  it("参考图 HTTP 失败向上抛错（调用方降级文字转场）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));

    await expect(uploadRemoteImageToInput("http://x/img.png", "http://comfy"))
      .rejects.toThrow("500");
    vi.unstubAllGlobals();
  });
});

describe("ComfyUI 提交超时合同", () => {
  // 真实 fetch 的行为：请求被 abort 时以 name=AbortError 拒绝
  const stalled = (signal?: AbortSignal) => new Promise((_r, reject) => {
    signal?.addEventListener("abort", () => {
      const err = new Error("aborted");
      err.name = "AbortError";
      reject(err);
    });
  });

  async function runUntilTimeout(call: () => Promise<unknown>): Promise<Error> {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: { signal?: AbortSignal }) =>
      stalled(init?.signal)));
    try {
      let settled = false;
      const caught = call().then(
        () => undefined,
        (e: Error) => { settled = true; return e; },
      );
      await vi.advanceTimersByTimeAsync(60_000);
      expect(settled).toBe(false); // 60s 那道墙会把「后端还在救回窗口里干活」判死
      await vi.advanceTimersByTimeAsync(30_000);
      return (await caught) as Error;
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  }

  it("插画提交超时=90s 且给 ComfyUI 专属文案（不再让用户拆需求）", async () => {
    const err = await runUntilTimeout(() => submitWorkflow("t-1", {}, "http://comfy"));

    expect(err).toBeInstanceOf(Error);
    expect(err.message).toContain("超过 90 秒");
    expect(err.message).toContain("ComfyUI 未在时限内接受本次任务");
    // 用户在多元数据插入面板里没有「生图模型」也没有引擎可换：默认文案无处可执行
    expect(err.message).not.toContain("把需求拆小");
  });

  it("画布整图提交走同一条超时与文案", async () => {
    const err = await runUntilTimeout(() => submitGraph({ nodes: [] }, "http://comfy"));

    expect(err).toBeInstanceOf(Error);
    expect(err.message).toContain("超过 90 秒");
    expect(err.message).not.toContain("把需求拆小");
  });
});
