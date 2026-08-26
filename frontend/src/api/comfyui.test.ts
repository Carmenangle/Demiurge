import { describe, expect, it, vi } from "vitest";
import { moveComfyOutputToInput } from "./comfyui";

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