// userState 上传链合同（2026-10-24）：upload-bg / upload-voice 是「API_BASE 单一属主」红线
// 的回归锁——曾经硬编码 http://127.0.0.1:8010，壳的 DEMIURGE_PORT 端口协商场景下直接失效。
// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({
  apiGet: vi.fn(),
  apiGetBuffer: vi.fn(),
  apiPost: vi.fn(),
  apiUpload: vi.fn(),
  apiUrl: vi.fn((p: string) => p),
}));

import { uploadChatBg, uploadVoice } from "./userState";
import { apiUpload } from "./client";

const source = readFileSync(new URL("./userState.ts", import.meta.url), "utf-8");

beforeEach(() => {
  vi.mocked(apiUpload).mockReset();
  vi.mocked(apiUpload).mockResolvedValue({ ok: true, path: "C:/saved/bg.png" });
});

describe("上传链走 API_BASE 单一属主", () => {
  it("uploadChatBg 经 apiUpload 打 /user-state/upload-bg，文件进 FormData", async () => {
    const r = await uploadChatBg(new File(["x"], "a.png", { type: "image/png" }));
    expect(apiUpload).toHaveBeenCalledTimes(1);
    const [path, fd] = vi.mocked(apiUpload).mock.calls[0];
    expect(path).toBe("/user-state/upload-bg");
    expect(fd.get("file")).toBeTruthy();
    expect(fd.get("repo_id")).toBeNull();
    expect(r).toEqual({ ok: true, path: "C:/saved/bg.png" });
  });

  it("uploadVoice 经 apiUpload 打 /user-state/upload-voice，repo_id/output_dir 进表单", async () => {
    await uploadVoice(new File(["x"], "v.wav"), "r1", "D:/out");
    const [path, fd] = vi.mocked(apiUpload).mock.calls[0];
    expect(path).toBe("/user-state/upload-voice");
    expect(fd.get("repo_id")).toBe("r1");
    expect(fd.get("output_dir")).toBe("D:/out");
  });
});

describe("源码形态锁", () => {
  it("userState.ts 不得出现硬编码 127.0.0.1:8010（API_BASE 是唯一属主）", () => {
    expect(source).not.toContain("127.0.0.1:8010");
  });

  it("两个上传函数必须走 apiUpload（multipart 语义与错误透出统一在 client）", () => {
    expect(source).toContain('apiUpload("/user-state/upload-bg"');
    expect(source).toContain('apiUpload("/user-state/upload-voice"');
  });
});
