import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadAttachment } from "./ai";

// uploadAttachment 的后端响应是 snake_case {file_id,...}，前端必须映射为 camelCase。
// apiUpload 原样透传 JSON 不做映射——若不在这里显式映射，meta.fileId === undefined，
// RichInput 占位卡替换后渲染 `fileId.startsWith` 抛 TypeError → React 崩溃黑屏（2026-09-01 实锤）。
describe("uploadAttachment wire mapping", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("映射后端 snake_case 为前端 camelCase（file_id → fileId）", async () => {
    const backend = {
      ok: true,
      file_id: "a".repeat(32),
      name: "计划.md",
      mime: "text/markdown",
      size: 12,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(backend), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const meta = await uploadAttachment("repo-1", new File(["你好"], "计划.md", { type: "text/markdown" }));
    expect(meta.fileId).toBe("a".repeat(32));
    expect(meta.name).toBe("计划.md");
    expect(meta.mime).toBe("text/markdown");
    expect(meta.size).toBe(12);
    // 不透传后端 ok 字段；返回结构即 FileAttachmentMeta（fileId 必为 32 位 hex 字符串）
    expect(meta).not.toHaveProperty("ok");
    expect(meta.fileId.length).toBe(32);
  });

  it("非 2xx 响应抛出可读错误（透出后端 detail）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "空文件不能作为附件" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(uploadAttachment("repo-1", new File([], "empty.txt"))).rejects.toThrow("空文件不能作为附件");
  });
});
