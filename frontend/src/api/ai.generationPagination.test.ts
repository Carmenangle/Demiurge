import { afterEach, describe, expect, it, vi } from "vitest";
import { listGenerations, type Embed } from "./ai";

// P0-1：/rag/generations 的 keyset 分页 wire 契约。
// 后端新增 {items, has_more}；前端 limit/beforeTs 必须透传为 limit/before_ts，
// 缺省时逐字等于改造前的全量请求（嵌入画廊等既有调用方无感）。
const embed: Embed = { baseUrl: "http://x", apiKey: "k", modelName: "m" };

describe("listGenerations keyset pagination wire", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch(): Array<[string, string]> {
    const calls: Array<[string, string]> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { body?: string }) => {
        calls.push([url, init?.body || ""]);
        return new Response(
          JSON.stringify({ items: [], has_more: false }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    return calls;
  }

  it("limit/beforeTs 透传为 limit/before_ts", async () => {
    const calls = stubFetch();
    await listGenerations("repo-1", embed, { limit: 32, beforeTs: 1750000000000 });
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toContain("/rag/generations");
    const body = JSON.parse(calls[0][1]);
    expect(body.repo_id).toBe("repo-1");
    expect(body.limit).toBe(32);
    expect(body.before_ts).toBe(1750000000000);
  });

  it("缺省参数等于改造前的全量请求（limit/before_ts 均为 0）", async () => {
    const calls = stubFetch();
    await listGenerations("repo-1", embed);
    const body = JSON.parse(calls[0][1]);
    expect(body.limit).toBe(0);
    expect(body.before_ts).toBe(0);
  });

  it("has_more 透出为调用方可判定（布尔）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ items: [], has_more: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const r = await listGenerations("repo-1", embed, { limit: 32 });
    expect(r.has_more).toBe(true);
  });
});
