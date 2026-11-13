import { afterEach, describe, expect, it, vi } from "vitest";
import { importAssets } from "./ai";

// 资产库拖拽导入的 wire 契约：multipart 必带 repo_id/output_dir/全部 files，
// 后端按文件返回 {items:[{item,embedded,embedError}], errors:[{name,error}]}。
describe("importAssets wire", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("multipart 携带仓库根与全部文件，返回 items+errors", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      items: [{ item: { id: "gen-1", prompt: "1girl" }, embedded: true }],
      errors: [{ name: "b.txt", error: "不支持的文件类型：.txt" }],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const files = [
      new File(["a"], "a.png", { type: "image/png" }),
      new File(["b"], "b.mp4", { type: "video/mp4" }),
    ];
    const result = await importAssets("repo-1", "D:/works", files);

    expect(result.items[0].item.prompt).toBe("1girl");
    expect(result.items[0].embedded).toBe(true);
    expect(result.errors[0].name).toBe("b.txt");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toMatch(/\/api\/rag\/assets\/import$/);
    const form = init.body as FormData;
    expect(form.get("repo_id")).toBe("repo-1");
    expect(form.get("output_dir")).toBe("D:/works");
    expect(form.getAll("files").map((f) => (f as File).name)).toEqual(["a.png", "b.mp4"]);
  });

  it("非 2xx 抛出后端 detail（未配置仓库文件夹等）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "尚未配置「仓库文件夹」" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    )));
    await expect(importAssets("repo-1", "", [new File(["a"], "a.png")]))
      .rejects.toThrow("仓库文件夹");
  });
});
